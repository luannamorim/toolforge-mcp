from fastapi import APIRouter, Request

from toolforge.models.catalog import ToolCatalog, ToolDescriptor
from toolforge.models.chat import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    pool = request.app.state.pool
    cache = request.app.state.cache
    orchestrator = request.app.state.orchestrator

    catalog: list[ToolDescriptor] = []
    for server_id in pool.connected_servers:
        cached = cache.get(server_id)
        if cached is None:
            tools = await pool.list_tools(server_id)
            cache.set(server_id, ToolCatalog(tools=tools))
            catalog.extend(tools)
        else:
            catalog.extend(cached.tools)

    return await orchestrator.run(body, catalog)
