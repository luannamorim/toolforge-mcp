"""Unit tests for RedisCatalogCache using fakeredis (no real Redis daemon needed)."""

import fakeredis.aioredis
import pytest

from toolforge.mcp_pool.catalog_cache import DEFAULT_TTL, InMemoryCatalogCache, RedisCatalogCache
from toolforge.models.catalog import ToolCatalog, ToolDescriptor


@pytest.fixture()
def sample_catalog() -> ToolCatalog:
    return ToolCatalog(
        tools=[
            ToolDescriptor(
                name="read_file",
                description="Read a file from disk",
                input_schema={"type": "object", "properties": {"path": {"type": "string"}}},
                server_id="filesystem",
            )
        ]
    )


@pytest.fixture()
async def fake_redis_cache() -> RedisCatalogCache:
    fake = fakeredis.aioredis.FakeRedis(decode_responses=True)
    cache = RedisCatalogCache.__new__(RedisCatalogCache)
    cache._client = fake
    cache._default_ttl = DEFAULT_TTL
    cache._key_prefix = "mcp:catalog"
    return cache


@pytest.mark.unit
async def test_set_and_get_round_trip(fake_redis_cache, sample_catalog):
    await fake_redis_cache.set("filesystem:hashing", sample_catalog)
    result = await fake_redis_cache.get("filesystem:hashing")
    assert result is not None
    assert len(result.tools) == 1
    assert result.tools[0].name == "read_file"


@pytest.mark.unit
async def test_key_format_matches_spec(fake_redis_cache, sample_catalog):
    """Key must be mcp:catalog:<server_id> per SPEC § FR2 / Architecture."""
    await fake_redis_cache.set("github:voyage-3-lite", sample_catalog)
    raw = await fake_redis_cache._client.get("mcp:catalog:github:voyage-3-lite")
    assert raw is not None, "Key must be mcp:catalog:github:voyage-3-lite"


@pytest.mark.unit
async def test_ttl_is_set(fake_redis_cache, sample_catalog):
    await fake_redis_cache.set("filesystem:hashing", sample_catalog)
    ttl = await fake_redis_cache._client.ttl("mcp:catalog:filesystem:hashing")
    # Allow ±1s for clock granularity between SETEX and TTL check.
    assert DEFAULT_TTL - 1 <= ttl <= DEFAULT_TTL


@pytest.mark.unit
async def test_custom_ttl(fake_redis_cache, sample_catalog):
    await fake_redis_cache.set("filesystem:hashing", sample_catalog, ttl=120)
    ttl = await fake_redis_cache._client.ttl("mcp:catalog:filesystem:hashing")
    assert 119 <= ttl <= 120


@pytest.mark.unit
async def test_get_returns_none_on_miss(fake_redis_cache):
    result = await fake_redis_cache.get("nonexistent:server")
    assert result is None


@pytest.mark.unit
async def test_invalidate_removes_key(fake_redis_cache, sample_catalog):
    await fake_redis_cache.set("filesystem:hashing", sample_catalog)
    await fake_redis_cache.invalidate("filesystem:hashing")
    result = await fake_redis_cache.get("filesystem:hashing")
    assert result is None


@pytest.mark.unit
async def test_invalidate_all_removes_all_keys(fake_redis_cache, sample_catalog):
    await fake_redis_cache.set("filesystem:hashing", sample_catalog)
    await fake_redis_cache.set("github:hashing", sample_catalog)
    await fake_redis_cache.invalidate_all()
    assert await fake_redis_cache.get("filesystem:hashing") is None
    assert await fake_redis_cache.get("github:hashing") is None


@pytest.mark.unit
async def test_get_returns_none_after_expiry(fake_redis_cache, sample_catalog):
    await fake_redis_cache.set("filesystem:hashing", sample_catalog)
    await fake_redis_cache._client.expire("mcp:catalog:filesystem:hashing", 0)
    result = await fake_redis_cache.get("filesystem:hashing")
    assert result is None


@pytest.mark.unit
async def test_redis_ping_returns_true(fake_redis_cache):
    assert await fake_redis_cache.ping() is True


@pytest.mark.unit
async def test_inmemory_ping_returns_true():
    cache = InMemoryCatalogCache()
    assert await cache.ping() is True
