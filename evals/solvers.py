"""In-process ToolForge solvers for Inspect AI eval harness.

Two solver factories are provided:

  toolforge_solver() — uses the production server config (mcp.servers.json:
                        filesystem + github).  Measures server-routing
                        accuracy and single-candidate rule coverage.

  stub_solver()      — uses the eval-only stub config (mcp.servers.eval.json:
                        local + cloud).  Measures rules 1, 2, and 5 of the
                        selection heuristic where tool-name overlap exists.

Each factory bootstraps a real MCPClientPool + Orchestrator the first time it
is called, then reuses that pool for all subsequent samples in the same eval
run.  The pools are keyed by mcp_servers_config path so both can coexist in
one process.

Dry-run mode (dry_run=True on ChatRequest) is used throughout: the LLM plans
tool calls and the selector fires, emitting trace records, but no MCP server
is actually invoked.  This keeps eval cost ~$0.001/sample.
"""

from __future__ import annotations

import asyncio
import atexit
import os
import tempfile
from collections import defaultdict
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
# Module-level lazy pool cache, keyed by mcp_servers_config path string.
# Avoids spawning server processes more than once per eval run.
# ---------------------------------------------------------------------------

_init_locks: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
_shared_pools: dict[str, MCPClientPool] = {}
_shared_catalogs: dict[str, list[ToolDescriptor]] = {}


def _cleanup_pools() -> None:
    if not _shared_pools:
        return

    async def _disconnect_all() -> None:
        await asyncio.gather(
            *[pool.disconnect_all() for pool in _shared_pools.values()],
            return_exceptions=True,
        )

    try:
        asyncio.run(_disconnect_all())
    except Exception:
        pass


atexit.register(_cleanup_pools)


async def _get_pool_and_catalog(settings: Settings) -> tuple[MCPClientPool, list[ToolDescriptor]]:
    config_key = str(settings.mcp_servers_config)
    async with _init_locks[config_key]:
        if config_key in _shared_pools:
            return _shared_pools[config_key], _shared_catalogs[config_key]

        embedder = HashingEmbedder()
        pool = MCPClientPool(settings.mcp_servers)
        await pool.connect_all()

        catalog = await build_catalog(pool, InMemoryCatalogCache(), embedder)

        _shared_pools[config_key] = pool
        _shared_catalogs[config_key] = catalog
        return pool, catalog


def _make_solver(settings: Settings) -> Solver:
    """Return a Solver bound to the given settings (and its server config)."""
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


@solver
def toolforge_solver() -> Solver:
    """Inspect AI Solver using the production server config (filesystem + github).

    Measures server-routing accuracy.  All tool calls emit selection_rule=
    "single-candidate" because filesystem and github share no tool names.

    CLI usage:
        uv run inspect eval evals/selection_accuracy.py \\
            --model anthropic/claude-sonnet-4-6
    """
    return _make_solver(Settings())


@solver
def stub_solver() -> Solver:
    """Inspect AI Solver using the eval-only stub config (local + cloud).

    Measures heuristic rules 1 (explicit-mention), 2 (argument-type), and 5
    (priority-order).  Rules 3 (session-recency) and 4 (cosine-similarity)
    remain blocked — see evals/selection_heuristic.py docstring.

    CLI usage:
        uv run inspect eval evals/selection_heuristic.py \\
            --model anthropic/claude-sonnet-4-6
    """
    return _make_solver(Settings(mcp_servers_config=Path("mcp.servers.eval.json")))
