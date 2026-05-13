"""Unit tests for cost ceiling enforcement (SPEC.md §Cost envelope)."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import _Usage, make_end_turn_response
from toolforge.agent.embedder import HashingEmbedder
from toolforge.agent.orchestrator import Orchestrator
from toolforge.config import Settings
from toolforge.models.catalog import ToolDescriptor
from toolforge.models.chat import ChatRequest
from toolforge.traces.writer import TraceWriter

_READ_TOOL = ToolDescriptor(
    name="read_file",
    description="Read a file",
    input_schema={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    server_id="filesystem",
)


def _make_orchestrator(tmp_path: Path, ceiling: float) -> Orchestrator:
    settings = Settings(
        anthropic_api_key="test-key",
        trace_sink=tmp_path / "traces.jsonl",
        cost_ceiling_usd=ceiling,
    )
    pool = MagicMock()
    content_block = MagicMock()
    content_block.text = "file contents"
    tool_result = MagicMock()
    tool_result.isError = False
    tool_result.content = [content_block]
    pool.call_tool = AsyncMock(return_value=tool_result)
    writer = TraceWriter(settings.trace_sink, verbose=False)
    return Orchestrator(pool, writer, settings, embedder=HashingEmbedder())


def _make_expensive_response(tokens_in: int = 100_000, tokens_out: int = 50_000):
    """Create an end_turn response whose token counts will exceed the ceiling."""
    from tests.conftest import _Message, _TextBlock
    return _Message(
        stop_reason="end_turn",
        content=[_TextBlock(text="Partial result from model")],
        usage=_Usage(input_tokens=tokens_in, output_tokens=tokens_out),
    )


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cost_ceiling_halts_loop(tmp_path: Path):
    """A single turn whose cost exceeds the ceiling returns halted=True."""
    orch = _make_orchestrator(tmp_path, ceiling=0.00001)
    req = ChatRequest(message="do something expensive", session_id="s1")

    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(return_value=_make_expensive_response()),
    ):
        resp = await orch.run(req, [_READ_TOOL])

    assert resp.halted is True
    assert resp.halt_reason == "cost_ceiling"
    assert "[TRUNCATED" in resp.response
    assert "$0.00" in resp.response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cost_ceiling_response_contains_partial_text(tmp_path: Path):
    """Text produced by the model before halt is included in the response."""
    orch = _make_orchestrator(tmp_path, ceiling=0.00001)
    req = ChatRequest(message="do something", session_id="s2")

    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(return_value=_make_expensive_response()),
    ):
        resp = await orch.run(req, [_READ_TOOL])

    assert "Partial result from model" in resp.response


@pytest.mark.unit
@pytest.mark.asyncio
async def test_no_halt_below_ceiling(tmp_path: Path):
    """A cheap request finishes normally without halted flag."""
    orch = _make_orchestrator(tmp_path, ceiling=10.0)  # very high ceiling
    req = ChatRequest(message="hello", session_id="s3")

    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(return_value=make_end_turn_response("Done!")),
    ):
        resp = await orch.run(req, [_READ_TOOL])

    assert resp.halted is False
    assert resp.halt_reason is None
    assert resp.response == "Done!"


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cost_ceiling_emits_halt_event_to_sink(tmp_path: Path):
    """When event_sink is provided, a halt event is emitted before run() returns."""
    orch = _make_orchestrator(tmp_path, ceiling=0.00001)
    req = ChatRequest(message="expensive", session_id="s4")
    emitted: list[dict] = []

    async def sink(event: dict) -> None:
        emitted.append(event)

    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(return_value=_make_expensive_response()),
    ):
        await orch.run(req, [_READ_TOOL], event_sink=sink)

    halt_events = [e for e in emitted if e.get("event") == "halt"]
    assert len(halt_events) == 1
    assert halt_events[0]["data"]["reason"] == "cost_ceiling"
    assert halt_events[0]["data"]["cost_usd"] > 0


@pytest.mark.unit
@pytest.mark.asyncio
async def test_cost_compared_unrounded(tmp_path: Path):
    """Ceiling comparison uses unrounded total_cost, not the 8dp cost_usd."""
    # Ceiling is exactly at a round boundary — unrounded total should still trigger halt
    orch = _make_orchestrator(tmp_path, ceiling=0.10)
    req = ChatRequest(message="test", session_id="s5")

    # tokens that produce cost slightly above $0.10
    resp_msg = _make_expensive_response(tokens_in=6_000, tokens_out=7_000)
    # Cost: 6000*3/1M + 7000*15/1M = 0.018 + 0.105 = 0.123 > 0.10

    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(return_value=resp_msg),
    ):
        resp = await orch.run(req, [_READ_TOOL])

    assert resp.halted is True
