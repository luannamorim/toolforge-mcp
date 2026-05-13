"""Shared fixtures for the ToolForge test suite."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from starlette.testclient import TestClient

from toolforge.agent.embedder import HashingEmbedder
from toolforge.agent.orchestrator import Orchestrator
from toolforge.config import Settings
from toolforge.mcp_pool.catalog_cache import InMemoryCatalogCache
from toolforge.models.catalog import ToolDescriptor
from toolforge.traces.writer import TraceWriter

# ---------------------------------------------------------------------------
# Catalog fixtures — single server (Phase 1 path)
# ---------------------------------------------------------------------------

READ_FILE_TOOL = ToolDescriptor(
    name="read_file",
    description="Read a file from the filesystem",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    server_id="filesystem",
)

# ---------------------------------------------------------------------------
# Synthetic multi-server catalog (Phase 2 — selection heuristic tests)
# Three overlapping servers: filesystem, github (same tool name), slack (unique).
# ---------------------------------------------------------------------------

FS_READ_TOOL = ToolDescriptor(
    name="read_file",
    description="Read a file from the local filesystem",
    input_schema={
        "type": "object",
        "properties": {"path": {"type": "string"}},
        "required": ["path"],
    },
    server_id="filesystem",
)

GH_READ_TOOL = ToolDescriptor(
    name="read_file",
    description="Read a file from a GitHub repository",
    input_schema={
        "type": "object",
        "properties": {
            "owner": {"type": "string"},
            "repo": {"type": "string"},
            "path": {"type": "string"},
        },
        "required": ["owner", "repo", "path"],
    },
    server_id="github",
)

SLACK_SEND_TOOL = ToolDescriptor(
    name="send_message",
    description="Send a message to a Slack channel",
    input_schema={
        "type": "object",
        "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
        "required": ["channel", "text"],
    },
    server_id="slack",
)

SYNTHETIC_PRIORITY = ["filesystem", "github", "slack"]


@pytest.fixture
def fake_catalog() -> list[ToolDescriptor]:
    return [READ_FILE_TOOL]


@pytest.fixture
def synthetic_catalog() -> list[ToolDescriptor]:
    """Three-server catalog with deliberately overlapping tool names."""
    return [FS_READ_TOOL, GH_READ_TOOL, SLACK_SEND_TOOL]


@pytest.fixture
def embedder() -> HashingEmbedder:
    return HashingEmbedder()


@pytest.fixture
def fake_mcp_pool():
    pool = MagicMock()
    pool.connection_status = {"filesystem": True}
    pool.connected_servers = ["filesystem"]

    content_block = MagicMock()
    content_block.text = "Hello from the file!"

    tool_result = MagicMock()
    tool_result.isError = False
    tool_result.content = [content_block]

    pool.call_tool = AsyncMock(return_value=tool_result)
    pool.list_tools = AsyncMock(return_value=[READ_FILE_TOOL])
    return pool


@pytest.fixture
def fake_mcp_pool_degraded():
    pool = MagicMock()
    pool.connection_status = {"filesystem": False}
    pool.connected_servers = []
    return pool


# ---------------------------------------------------------------------------
# Anthropic response stubs
# ---------------------------------------------------------------------------


@dataclass
class _Usage:
    input_tokens: int = 100
    output_tokens: int = 50
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0


@dataclass
class _TextBlock:
    type: str = "text"
    text: str = "The file contains: Hello from the file!"


@dataclass
class _ToolUseBlock:
    type: str = "tool_use"
    id: str = "toolu_01"
    name: str = "read_file"
    input: dict = field(default_factory=lambda: {"path": "/tmp/hello.txt"})


@dataclass
class _Message:
    stop_reason: str
    content: list
    usage: _Usage = field(default_factory=_Usage)


def make_tool_use_response() -> _Message:
    return _Message(
        stop_reason="tool_use",
        content=[_ToolUseBlock()],
    )


def make_multi_tool_use_response(blocks: list[tuple[str, str, dict]]) -> _Message:
    """Return a single-turn response containing multiple tool_use blocks.

    blocks: list of (tool_use_id, tool_name, input_args).
    """
    return _Message(
        stop_reason="tool_use",
        content=[_ToolUseBlock(id=tid, name=name, input=inp) for tid, name, inp in blocks],
    )


def make_end_turn_response(text: str = "The file contains: Hello from the file!") -> _Message:
    return _Message(
        stop_reason="end_turn",
        content=[_TextBlock(text=text)],
    )


# ---------------------------------------------------------------------------
# Settings / writer fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        anthropic_api_key="test-key",
        trace_sink=tmp_path / "traces.jsonl",
        trace_verbose=False,
    )


@pytest.fixture
def trace_writer(settings: Settings) -> TraceWriter:
    return TraceWriter(settings.trace_sink, verbose=settings.trace_verbose)


# ---------------------------------------------------------------------------
# FastAPI test app (bypasses lifespan — sets state directly)
# ---------------------------------------------------------------------------


@pytest.fixture
def test_app(fake_mcp_pool, settings, trace_writer, fake_catalog, embedder):
    from toolforge.http import chat as chat_mod
    from toolforge.http import health as health_mod
    from toolforge.http import tools as tools_mod

    app = FastAPI()
    app.include_router(health_mod.router)
    app.include_router(chat_mod.router)
    app.include_router(tools_mod.router)

    orchestrator = Orchestrator(fake_mcp_pool, trace_writer, settings, embedder=embedder)

    app.state.pool = fake_mcp_pool
    app.state.cache = InMemoryCatalogCache()
    app.state.writer = trace_writer
    app.state.orchestrator = orchestrator
    app.state.embedder = embedder
    app.state.settings = settings
    return app


@pytest.fixture
def test_app_degraded(fake_mcp_pool_degraded, settings, trace_writer, embedder):
    from toolforge.http import chat as chat_mod
    from toolforge.http import health as health_mod
    from toolforge.http import tools as tools_mod

    app = FastAPI()
    app.include_router(health_mod.router)
    app.include_router(chat_mod.router)
    app.include_router(tools_mod.router)

    orchestrator = Orchestrator(fake_mcp_pool_degraded, trace_writer, settings, embedder=embedder)

    app.state.pool = fake_mcp_pool_degraded
    app.state.cache = InMemoryCatalogCache()
    app.state.writer = trace_writer
    app.state.orchestrator = orchestrator
    app.state.embedder = embedder
    app.state.settings = settings
    return app


@pytest.fixture
def client(test_app) -> TestClient:
    return TestClient(test_app)


@pytest.fixture
def client_degraded(test_app_degraded) -> TestClient:
    return TestClient(test_app_degraded)
