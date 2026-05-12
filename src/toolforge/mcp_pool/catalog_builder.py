"""Shared catalog-building helper used by both the HTTP layer and the eval harness."""

from __future__ import annotations

from toolforge.agent.embedder import Embedder
from toolforge.mcp_pool.catalog_cache import InMemoryCatalogCache
from toolforge.mcp_pool.pool import MCPClientPool
from toolforge.models.catalog import ToolCatalog, ToolDescriptor


async def build_catalog(
    pool: MCPClientPool,
    cache: InMemoryCatalogCache,
    embedder: Embedder,
) -> list[ToolDescriptor]:
    """List tools from all connected servers, embed descriptions, and cache per embedder.

    Composite cache key (<server_id>:<embedder_id>) prevents dimension-space
    collisions when swapping embedders (OQ#4).
    """
    catalog: list[ToolDescriptor] = []
    for server_id in pool.connected_servers:
        cache_key = f"{server_id}:{embedder.embedder_id}"
        cached = cache.get(cache_key)
        if cached is None:
            tools = await pool.list_tools(server_id)
            tools = [
                t.model_copy(update={"description_embedding": embedder.embed(t.description)})
                for t in tools
            ]
            cache.set(cache_key, ToolCatalog(tools=tools))
            catalog.extend(tools)
        else:
            catalog.extend(cached.tools)
    return catalog
