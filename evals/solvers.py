"""In-process ToolForge solver for Inspect AI eval harness.

The solver bootstraps a real MCPClientPool + Orchestrator (identical to
toolforge.app.create_app wiring) the first time it is called, then reuses
that pool for all subsequent samples in the same eval run.  Each sample gets
its own TraceWriter pointed at a temp file so the scorer can parse
per-sample records by session_id.

Dry-run mode (dry_run=True on ChatRequest) is used throughout: the LLM
plans tool calls and the selector fires, emitting trace records, but no
MCP server is actually invoked.  This keeps eval cost ~$0.001/sample and
removes dependency on live GitHub/filesystem state for selection scoring.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import tempfile
from pathlib import Path

from inspect_ai.solver import Generate, Solver, TaskState, solver

from toolforge.agent.embedder import HashingEmbedder
from toolforge.agent.orchestrator import Orchestrator
from toolforge.config import Settings
from toolforge.mcp_pool.catalog_builder import build_catalog
from toolforge.mcp_pool.catalog_cache import InMemoryCatalogCache
from toolforge.mcp_pool.pool import MCPClientPool
from toolforge.models.catalog import ToolDescriptor
from toolforge.models.chat import ChatRequest
from toolforge.traces.writer import TraceWriter

# ---------------------------------------------------------------------------
# Module-level lazy pool shared across all samples in one eval run.
# Avoids spawning N server processes for N samples.
# ---------------------------------------------------------------------------

_init_lock = asyncio.Lock()
_shared_pool: MCPClientPool | None = None
_shared_catalog: list[ToolDescriptor] | None = None


def _cleanup_pool() -> None:
    """Disconnect the shared pool at interpreter exit to avoid subprocess leaks."""
    if _shared_pool is not None:
        try:
            asyncio.run(_shared_pool.disconnect_all())
        except Exception:
            pass


atexit.register(_cleanup_pool)


async def _get_pool_and_catalog(settings: Settings) -> tuple[MCPClientPool, list[ToolDescriptor]]:
    global _shared_pool, _shared_catalog
    async with _init_lock:
        if _shared_pool is not None:
            return _shared_pool, _shared_catalog  # type: ignore[return-value]

        embedder = HashingEmbedder()
        pool = MCPClientPool(settings.mcp_servers)
        await pool.connect_all()

        catalog = await build_catalog(pool, InMemoryCatalogCache(), embedder)

        _shared_pool = pool
        _shared_catalog = catalog
        return pool, catalog


@solver
def toolforge_solver() -> Solver:
    """Inspect AI Solver that runs a prompt through the ToolForge Orchestrator.

    Each sample gets a unique trace sink (tmp file) and session_id so the
    scorer can read back only that sample's tool calls.  Results are stored
    in state.metadata["trace_sink"] and state.metadata["session_id"].

    CLI usage:
        uv run inspect eval evals/selection_accuracy.py \\
            --model anthropic/claude-sonnet-4-6
    """

    settings = Settings()

    async def solve(state: TaskState, generate: Generate) -> TaskState:
        pool, catalog = await _get_pool_and_catalog(settings)

        sample_id = str(state.sample_id)
        fd, path = tempfile.mkstemp(suffix=f"-{sample_id}.jsonl", prefix="tf-eval-")
        os.close(fd)
        sink = Path(path)

        writer = TraceWriter(sink, verbose=False)
        orchestrator = Orchestrator(pool, writer, settings, embedder=HashingEmbedder())

        request = ChatRequest(
            message=state.input_text,
            session_id=sample_id,
            dry_run=True,
        )
        response = await orchestrator.run(request, catalog)

        state.metadata = state.metadata or {}
        state.metadata["trace_sink"] = str(sink)
        state.metadata["session_id"] = sample_id
        state.metadata["response"] = response.response
        state.metadata["steps"] = response.steps
        state.metadata["cost_usd"] = response.cost_usd
        state.metadata.setdefault("expected_calls", [])
        return state

    return solve
