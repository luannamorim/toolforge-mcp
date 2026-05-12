from fastapi import APIRouter, Request

from toolforge.models.catalog import ToolCatalog, ToolDescriptor
from toolforge.models.chat import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    pool = request.app.state.pool
    cache = request.app.state.cache
    orchestrator = request.app.state.orchestrator
    embedder = request.app.state.embedder

    catalog: list[ToolDescriptor] = []
    for server_id in pool.connected_servers:
        # Composite key scopes the cache to the embedder's dimension space so
        # swapping HashingEmbedder → Voyage/BGE (OQ#4) never mixes dimensions.
        cache_key = f"{server_id}:{embedder.embedder_id}"
        cached = cache.get(cache_key)
        if cached is None:
            tools = await pool.list_tools(server_id)
            # Embed tool descriptions at catalog-load time; stored in cache so
            # per-request cosine scoring skips re-embedding (SPEC § rule 4).
            tools = [
                t.model_copy(update={"description_embedding": embedder.embed(t.description)})
                for t in tools
            ]
            cache.set(cache_key, ToolCatalog(tools=tools))
            catalog.extend(tools)
        else:
            catalog.extend(cached.tools)

    return await orchestrator.run(body, catalog)
