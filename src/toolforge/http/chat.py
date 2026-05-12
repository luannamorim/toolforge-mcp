from fastapi import APIRouter, Request

from toolforge.mcp_pool.catalog_builder import build_catalog
from toolforge.models.chat import ChatRequest, ChatResponse

router = APIRouter()


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    catalog = await build_catalog(
        request.app.state.pool,
        request.app.state.cache,
        request.app.state.embedder,
    )
    return await request.app.state.orchestrator.run(body, catalog)
