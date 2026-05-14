"""Catalog cache backends — both conform to CatalogCache so callers are backend-agnostic."""

from __future__ import annotations

import time
from typing import Protocol

import redis.asyncio as aioredis

from toolforge.models.catalog import ToolCatalog

DEFAULT_TTL = 300  # 5 minutes, matching SPEC § FR2


def _resolve_ttl(ttl: int | None, default: int) -> int:
    return ttl if ttl is not None else default


class CatalogCache(Protocol):
    async def get(self, server_id: str) -> ToolCatalog | None: ...
    async def set(self, server_id: str, catalog: ToolCatalog, ttl: int | None = None) -> None: ...
    async def invalidate(self, server_id: str) -> None: ...
    async def invalidate_all(self) -> None: ...
    async def close(self) -> None: ...
    async def ping(self) -> bool: ...


class InMemoryCatalogCache:
    def __init__(self, default_ttl: int = DEFAULT_TTL) -> None:
        self._store: dict[str, tuple[ToolCatalog, float]] = {}
        self._default_ttl = default_ttl

    async def get(self, server_id: str) -> ToolCatalog | None:
        entry = self._store.get(server_id)
        if entry is None:
            return None
        catalog, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[server_id]
            return None
        return catalog

    async def set(self, server_id: str, catalog: ToolCatalog, ttl: int | None = None) -> None:
        effective_ttl = _resolve_ttl(ttl, self._default_ttl)
        self._store[server_id] = (catalog, time.monotonic() + effective_ttl)

    async def invalidate(self, server_id: str) -> None:
        self._store.pop(server_id, None)

    async def invalidate_all(self) -> None:
        self._store.clear()

    async def close(self) -> None:
        pass

    async def ping(self) -> bool:
        return True


class RedisCatalogCache:
    """Redis-backed catalog cache.

    Key format: <key_prefix>:<server_id>  (e.g. mcp:catalog:github)
    TTL is enforced server-side via SETEX so the catalog is evicted even if the
    process crashes before it can call invalidate.
    """

    def __init__(
        self,
        url: str,
        *,
        default_ttl: int = DEFAULT_TTL,
        key_prefix: str = "mcp:catalog",
        max_connections: int = 10,
    ) -> None:
        self._client: aioredis.Redis = aioredis.Redis.from_url(
            url, decode_responses=True, max_connections=max_connections
        )
        self._default_ttl = default_ttl
        self._key_prefix = key_prefix

    def _key(self, server_id: str) -> str:
        return f"{self._key_prefix}:{server_id}"

    async def get(self, server_id: str) -> ToolCatalog | None:
        raw = await self._client.get(self._key(server_id))
        if raw is None:
            return None
        return ToolCatalog.model_validate_json(raw)

    async def set(self, server_id: str, catalog: ToolCatalog, ttl: int | None = None) -> None:
        effective_ttl = _resolve_ttl(ttl, self._default_ttl)
        await self._client.setex(self._key(server_id), effective_ttl, catalog.model_dump_json())

    async def invalidate(self, server_id: str) -> None:
        await self._client.delete(self._key(server_id))

    async def invalidate_all(self) -> None:
        async for key in self._client.scan_iter(f"{self._key_prefix}:*", count=100):
            await self._client.delete(key)

    async def close(self) -> None:
        await self._client.aclose()

    async def ping(self) -> bool:
        try:
            await self._client.ping()
            return True
        except Exception:
            return False
