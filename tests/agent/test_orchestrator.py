"""Orchestrator integration tests — mocked Anthropic + mocked MCP pool."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_end_turn_response, make_tool_use_response
from toolforge.agent.orchestrator import Orchestrator, _validate_args
from toolforge.models.catalog import ToolDescriptor
from toolforge.models.chat import ChatRequest
from toolforge.models.trace import SCHEMA_VERSION, TraceRecord
from toolforge.traces.writer import TraceWriter, compute_cost, hash_arguments

# ---------------------------------------------------------------------------
# Unit tests — pure functions
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_hash_arguments_deterministic():
    args = {"path": "/tmp/hello.txt", "encoding": "utf-8"}
    assert hash_arguments(args) == hash_arguments(args)


@pytest.mark.unit
def test_hash_arguments_order_independent():
    a = {"b": 2, "a": 1}
    b = {"a": 1, "b": 2}
    assert hash_arguments(a) == hash_arguments(b)


@pytest.mark.unit
def test_compute_cost_basic():
    cost = compute_cost("claude-sonnet-4-6", input_tokens=1000, output_tokens=500)
    # 1000 * 3.0/1M + 500 * 15.0/1M = 0.003 + 0.0075 = 0.0105
    assert abs(cost - 0.0105) < 1e-6


@pytest.mark.unit
def test_compute_cost_with_cache_read():
    cost = compute_cost(
        "claude-sonnet-4-6",
        input_tokens=1000,
        output_tokens=0,
        cache_read_tokens=800,
    )
    # 200 regular * 3.0/1M + 800 cache_read * 0.30/1M = 0.0006 + 0.00024 = 0.00084
    assert abs(cost - 0.00084) < 1e-7


@pytest.mark.unit
def test_trace_record_schema_version():
    record = TraceRecord(
        session_id="s1",
        step=1,
        server="filesystem",
        tool="read_file",
        arguments_hash="abc",
        latency_ms=42.0,
        success=True,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        selection_rule="single-candidate",
    )
    assert record.schema_version == SCHEMA_VERSION


@pytest.mark.unit
def test_trace_writer_writes_jsonl(tmp_path: Path):
    import json

    writer = TraceWriter(tmp_path / "traces.jsonl", verbose=False)
    record = TraceRecord(
        session_id="s1",
        step=1,
        server="filesystem",
        tool="read_file",
        arguments_hash="abc",
        latency_ms=10.5,
        success=True,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        selection_rule="single-candidate",
    )
    writer.write(record)
    lines = (tmp_path / "traces.jsonl").read_text().splitlines()
    assert len(lines) == 1
    data = json.loads(lines[0])
    assert data["schema_version"] == SCHEMA_VERSION
    assert data["session_id"] == "s1"
    assert data["tool"] == "read_file"
    assert "arguments" not in data  # not verbose


@pytest.mark.unit
def test_trace_writer_verbose_includes_arguments(tmp_path: Path):
    import json

    writer = TraceWriter(tmp_path / "traces.jsonl", verbose=True)
    record = TraceRecord(
        session_id="s1",
        step=1,
        server="filesystem",
        tool="read_file",
        arguments_hash="abc",
        latency_ms=5.0,
        success=True,
        tokens_in=100,
        tokens_out=50,
        cost_usd=0.001,
        selection_rule="single-candidate",
        arguments={"path": "/tmp/hello.txt"},
    )
    writer.write(record)
    data = json.loads((tmp_path / "traces.jsonl").read_text())
    assert data["arguments"] == {"path": "/tmp/hello.txt"}


@pytest.mark.unit
def test_validate_args_valid():
    tool = ToolDescriptor(
        name="read_file",
        description="",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        server_id="filesystem",
    )
    assert _validate_args(tool, {"path": "/tmp/hello.txt"}) is None


@pytest.mark.unit
def test_validate_args_missing_required():
    tool = ToolDescriptor(
        name="read_file",
        description="",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        server_id="filesystem",
    )
    error = _validate_args(tool, {})
    assert error is not None
    assert "path" in error


@pytest.mark.unit
def test_validate_args_wrong_type():
    tool = ToolDescriptor(
        name="read_file",
        description="",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        server_id="filesystem",
    )
    error = _validate_args(tool, {"path": 42})
    assert error is not None


# ---------------------------------------------------------------------------
# Integration tests — mocked Anthropic + mocked MCP pool
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_orchestrator_happy_path(fake_mcp_pool, settings, trace_writer, fake_catalog, tmp_path):
    """Tool-use turn followed by end_turn produces a response and one trace record."""
    import json

    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)

    side_effects = [make_tool_use_response(), make_end_turn_response()]
    with patch.object(
        orch._client.messages, "create", new=AsyncMock(side_effect=side_effects)
    ):
        resp = await orch.run(
            ChatRequest(message="What is in /tmp/hello.txt?"),
            fake_catalog,
        )

    assert resp.response == "The file contains: Hello from the file!"
    assert resp.steps == 1
    assert resp.cost_usd >= 0

    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["schema_version"] == SCHEMA_VERSION
    assert record["tool"] == "read_file"
    assert record["server"] == "filesystem"
    assert record["success"] is True
    assert record["selection_rule"] == "single-candidate"
    assert record["executed"] is True
    for field in ("timestamp", "session_id", "step", "arguments_hash",
                  "latency_ms", "tokens_in", "tokens_out", "cost_usd"):
        assert field in record, f"missing field: {field}"


@pytest.mark.integration
async def test_orchestrator_no_tools_end_turn(fake_mcp_pool, settings, trace_writer):
    """If the model responds without tool_use, no trace is emitted."""
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)

    with patch.object(
        orch._client.messages, "create", new=AsyncMock(return_value=make_end_turn_response("Done!"))
    ):
        resp = await orch.run(ChatRequest(message="Hello"), [])

    assert resp.response == "Done!"
    assert resp.steps == 0
    assert not settings.trace_sink.exists() or settings.trace_sink.read_text().strip() == ""


@pytest.mark.integration
async def test_orchestrator_validation_failure(fake_mcp_pool, settings, trace_writer):
    """A tool call with invalid args emits a failure trace and no MCP call is made."""
    import json
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    @dataclass
    class _BadToolUseBlock:
        type: str = "tool_use"
        id: str = "toolu_bad"
        name: str = "read_file"
        input: dict = dc_field(default_factory=lambda: {"wrong_key": 42})

    from tests.conftest import _Message

    bad_response = _Message(stop_reason="tool_use", content=[_BadToolUseBlock()])
    end_response = make_end_turn_response("Could not read file.")

    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create", new=AsyncMock(side_effect=[bad_response, end_response])
    ):
        await orch.run(
            ChatRequest(message="Read something"),
            [
                ToolDescriptor(
                    name="read_file",
                    description="",
                    input_schema={
                        "type": "object",
                        "properties": {"path": {"type": "string"}},
                        "required": ["path"],
                    },
                    server_id="filesystem",
                )
            ],
        )

    fake_mcp_pool.call_tool.assert_not_awaited()

    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["success"] is False
    assert record["error"] is not None


@pytest.mark.integration
async def test_orchestrator_session_id_echoed(fake_mcp_pool, settings, trace_writer, fake_catalog):
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    req = ChatRequest(message="Hi", session_id="my-session-42")

    with patch.object(
        orch._client.messages, "create", new=AsyncMock(return_value=make_end_turn_response())
    ):
        resp = await orch.run(req, fake_catalog)

    assert resp.session_id == "my-session-42"
