from fastapi import APIRouter, Request
from pydantic import BaseModel

from toolforge.mcp_pool.catalog_builder import build_catalog
from toolforge.models.catalog import ToolSummary

router = APIRouter()


class ToolsResponse(BaseModel):
    count: int
    tools: list[ToolSummary]


@router.get("/tools", response_model=ToolsResponse)
async def list_tools(request: Request) -> ToolsResponse:
    catalog = await build_catalog(
        request.app.state.pool,
        request.app.state.cache,
        request.app.state.embedder,
    )
    summaries = [
        ToolSummary(
            name=t.name,
            description=t.description,
            input_schema=t.input_schema,
            server_id=t.server_id,
        )
        for t in catalog
    ]
    return ToolsResponse(count=len(summaries), tools=summaries)
