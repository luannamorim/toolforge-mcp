import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI

from toolforge.agent.embedder import Embedder, HashingEmbedder, VoyageEmbedder
from toolforge.agent.orchestrator import Orchestrator
from toolforge.config import Settings
from toolforge.guardrails.payload import PayloadSizeMiddleware
from toolforge.http import chat, health, tools
from toolforge.mcp_pool.catalog_cache import InMemoryCatalogCache
from toolforge.mcp_pool.pool import MCPClientPool
from toolforge.traces.writer import TraceWriter

logger = logging.getLogger(__name__)


def _build_embedder(settings: Settings) -> Embedder:
    if settings.embedder_backend == "voyage":
        if settings.voyage_api_key:
            return VoyageEmbedder(api_key=settings.voyage_api_key)
        logger.warning(
            "embedder_backend=voyage but VOYAGE_API_KEY is empty — "
            "falling back to HashingEmbedder (rule 4 will be effectively disabled)"
        )
    return HashingEmbedder()


def create_app() -> FastAPI:
    settings = Settings()

    @asynccontextmanager
    async def _lifespan(app: FastAPI):
        embedder = _build_embedder(settings)
        pool = MCPClientPool(settings.mcp_servers)
        cache = InMemoryCatalogCache()
        writer = TraceWriter(settings.trace_sink, verbose=settings.trace_verbose)
        orchestrator = Orchestrator(pool, writer, settings, embedder=embedder)

        await pool.connect_all()

        app.state.settings = settings
        app.state.embedder = embedder
        app.state.pool = pool
        app.state.cache = cache
        app.state.writer = writer
        app.state.orchestrator = orchestrator

        yield

        await pool.disconnect_all()
        if hasattr(embedder, "close"):
            embedder.close()

    app = FastAPI(title="ToolForge", version="0.1.0", lifespan=_lifespan)
    # Must be registered before any middleware that reads the request body.
    app.add_middleware(PayloadSizeMiddleware, max_bytes=settings.max_request_bytes)
    app.include_router(health.router)
    app.include_router(chat.router)
    app.include_router(tools.router)
    return app
