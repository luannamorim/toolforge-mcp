"""In-memory catalog cache with the same get/set/invalidate interface as a Redis-backed cache.

Phase 1: in-process dict. Phase 2: swap implementation for Redis without touching callers.
"""

from __future__ import annotations

import time

from toolforge.models.catalog import ToolCatalog

DEFAULT_TTL = 300  # 5 minutes, matching SPEC § FR2


class InMemoryCatalogCache:
    def __init__(self, default_ttl: int = DEFAULT_TTL) -> None:
        self._store: dict[str, tuple[ToolCatalog, float]] = {}
        self._default_ttl = default_ttl

    def get(self, server_id: str) -> ToolCatalog | None:
        entry = self._store.get(server_id)
        if entry is None:
            return None
        catalog, expires_at = entry
        if time.monotonic() > expires_at:
            del self._store[server_id]
            return None
        return catalog

    def set(self, server_id: str, catalog: ToolCatalog, ttl: int | None = None) -> None:
        effective_ttl = ttl if ttl is not None else self._default_ttl
        self._store[server_id] = (catalog, time.monotonic() + effective_ttl)

    def invalidate(self, server_id: str) -> None:
        self._store.pop(server_id, None)

    def invalidate_all(self) -> None:
        self._store.clear()
