import asyncio
import json
from collections.abc import AsyncGenerator

from fastapi import APIRouter, Request
from starlette.responses import StreamingResponse

from toolforge.mcp_pool.catalog_builder import build_catalog
from toolforge.models.chat import ChatRequest, ChatResponse

router = APIRouter()


def _format_sse(event: str, data: dict) -> bytes:
    payload = json.dumps(data, separators=(",", ":"))
    return f"event: {event}\ndata: {payload}\n\n".encode()


@router.post("/chat", response_model=ChatResponse)
async def chat(body: ChatRequest, request: Request) -> ChatResponse:
    catalog = await build_catalog(
        request.app.state.pool,
        request.app.state.cache,
        request.app.state.embedder,
    )
    return await request.app.state.orchestrator.run(body, catalog)


@router.post("/chat/stream")
async def chat_stream(body: ChatRequest, request: Request) -> StreamingResponse:
    catalog = await build_catalog(
        request.app.state.pool,
        request.app.state.cache,
        request.app.state.embedder,
    )
    queue: asyncio.Queue[dict | None] = asyncio.Queue()

    async def event_sink(event: dict) -> None:
        await queue.put(event)

    async def runner() -> None:
        try:
            response = await request.app.state.orchestrator.run(
                body, catalog, event_sink=event_sink
            )
            await queue.put({"event": "final.response", "data": response.model_dump(exclude_none=True)})
        except Exception as exc:
            await queue.put({"event": "error", "data": {"message": str(exc)}})
        finally:
            await queue.put(None)

    async def stream() -> AsyncGenerator[bytes, None]:
        yield _format_sse("session.start", {"session_id": body.session_id, "dry_run": body.dry_run})
        task = asyncio.create_task(runner())
        try:
            while (event := await queue.get()) is not None:
                yield _format_sse(event["event"], event["data"])
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    return StreamingResponse(stream(), media_type="text/event-stream")
