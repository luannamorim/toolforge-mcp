"""Orchestrator integration tests — mocked Anthropic + mocked MCP pool."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.conftest import (
    make_end_turn_response,
    make_multi_tool_use_response,
    make_tool_use_response,
)
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
    # Single-candidate path: alternatives list is empty → omitted from JSONL
    assert "alternatives" not in record
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


_READ_FILE_SCHEMA = {
    "type": "object",
    "properties": {"path": {"type": "string"}},
    "required": ["path"],
}
_READ_FILE_TOOL = ToolDescriptor(
    name="read_file", description="", input_schema=_READ_FILE_SCHEMA, server_id="filesystem"
)


@pytest.mark.integration
async def test_corrective_retry_first_failure_includes_schema(
    fake_mcp_pool, settings, trace_writer
):
    """First validation failure returns a corrective message with schema and corrective_retry=True."""
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    from tests.conftest import _Message

    @dataclass
    class _BadBlock:
        type: str = "tool_use"
        id: str = "toolu_c1"
        name: str = "read_file"
        input: dict = dc_field(default_factory=lambda: {"wrong_key": 99})

    bad_response = _Message(stop_reason="tool_use", content=[_BadBlock()])
    end_response = make_end_turn_response("done")

    mock_create = AsyncMock(side_effect=[bad_response, end_response])
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(orch._client.messages, "create", new=mock_create):
        await orch.run(ChatRequest(message="read something"), [_READ_FILE_TOOL])

    # The tool_result sent back to the LLM should contain the schema hint
    second_call_messages = mock_create.call_args_list[1].kwargs["messages"]
    last_user_msg = second_call_messages[-1]
    assert last_user_msg["role"] == "user"
    tool_result_content = last_user_msg["content"][0]
    assert tool_result_content["is_error"] is True
    assert "Expected schema" in tool_result_content["content"]
    assert "Please retry" in tool_result_content["content"]

    # Trace must have corrective_retry=True
    record = json.loads(settings.trace_sink.read_text().splitlines()[0])
    assert record["corrective_retry"] is True
    assert record["success"] is False


@pytest.mark.integration
async def test_corrective_retry_second_failure_surfaces_plain_error(
    fake_mcp_pool, settings, trace_writer
):
    """Second validation failure for the same tool emits a plain error (corrective_retry=False)."""
    from dataclasses import dataclass
    from dataclasses import field as dc_field

    from tests.conftest import _Message

    @dataclass
    class _BadBlock:
        type: str = "tool_use"
        id: str = "toolu_c2"
        name: str = "read_file"
        input: dict = dc_field(default_factory=lambda: {"wrong_key": 99})

    bad_response = _Message(stop_reason="tool_use", content=[_BadBlock()])
    end_response = make_end_turn_response("giving up")

    mock_create = AsyncMock(side_effect=[bad_response, bad_response, end_response])
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(orch._client.messages, "create", new=mock_create):
        await orch.run(ChatRequest(message="read something"), [_READ_FILE_TOOL])

    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 2
    first = json.loads(lines[0])
    second = json.loads(lines[1])
    assert first["corrective_retry"] is True   # first failure: corrective hint
    assert second["corrective_retry"] is False  # second failure: plain error

    # Second tool_result must NOT contain "Expected schema"
    third_call_messages = mock_create.call_args_list[2].kwargs["messages"]
    last_user_msg = third_call_messages[-1]
    second_tool_result = last_user_msg["content"][0]
    assert "Expected schema" not in second_tool_result["content"]


@pytest.mark.integration
async def test_orchestrator_session_id_echoed(fake_mcp_pool, settings, trace_writer, fake_catalog):
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    req = ChatRequest(message="Hi", session_id="my-session-42")

    with patch.object(
        orch._client.messages, "create", new=AsyncMock(return_value=make_end_turn_response())
    ):
        resp = await orch.run(req, fake_catalog)

    assert resp.session_id == "my-session-42"


@pytest.mark.integration
async def test_orchestrator_heuristic_records_alternatives(fake_mcp_pool, settings, trace_writer):
    """Two-candidate catalog: rule 2 picks filesystem; trace records github as alternative."""
    import json

    from tests.conftest import FS_READ_TOOL, GH_READ_TOOL, _Message, _ToolUseBlock

    two_server_catalog = [FS_READ_TOOL, GH_READ_TOOL]
    # Args {"path": "/tmp/x"} validate only filesystem schema (github requires owner+repo)
    tool_block = _ToolUseBlock(name="read_file", input={"path": "/tmp/x"})
    tool_response = _Message(stop_reason="tool_use", content=[tool_block])

    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[tool_response, make_end_turn_response()]),
    ):
        resp = await orch.run(
            ChatRequest(message="read a file"),
            two_server_catalog,
        )

    assert resp.steps == 1
    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["server"] == "filesystem"
    assert record["selection_rule"] == "argument-type"
    assert record["alternatives"] == ["github"]


# ---------------------------------------------------------------------------
# Dry-run mode
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_chat_request_dry_run_defaults_false():
    req = ChatRequest(message="hi")
    assert req.dry_run is False


@pytest.mark.unit
def test_trace_record_dry_run_default_false():
    record = TraceRecord(
        session_id="s1", step=1, server="filesystem", tool="read_file",
        arguments_hash="abc", latency_ms=1.0, success=True,
        tokens_in=10, tokens_out=5, cost_usd=0.0001,
        selection_rule="single-candidate",
    )
    data = record.model_dump()
    assert data["dry_run"] is False

    record2 = record.model_copy(update={"dry_run": True, "executed": False})
    data2 = record2.model_dump()
    assert data2["dry_run"] is True
    assert data2["executed"] is False


@pytest.mark.integration
async def test_orchestrator_dry_run_skips_pool_call(fake_mcp_pool, settings, trace_writer, fake_catalog):
    """dry_run=True: MCP never called; trace has executed=False, dry_run=True, success=True."""
    import json

    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
    ):
        resp = await orch.run(
            ChatRequest(message="read a file", dry_run=True),
            fake_catalog,
        )

    fake_mcp_pool.call_tool.assert_not_awaited()
    assert resp.dry_run is True
    assert resp.steps == 1

    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["executed"] is False
    assert record["dry_run"] is True
    assert record["success"] is True


@pytest.mark.integration
async def test_orchestrator_dry_run_response_echoes_flag(fake_mcp_pool, settings, trace_writer):
    """ChatResponse.dry_run echoes the request flag."""
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(return_value=make_end_turn_response()),
    ):
        resp = await orch.run(ChatRequest(message="hi", dry_run=True), [])

    assert resp.dry_run is True


@pytest.mark.integration
async def test_orchestrator_dry_run_validation_failure_records_trace(
    fake_mcp_pool, settings, trace_writer
):
    """Validation failure in dry-run: trace has success=False, executed=False, dry_run=True."""
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
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[bad_response, make_end_turn_response()]),
    ):
        await orch.run(
            ChatRequest(message="read something", dry_run=True),
            [ToolDescriptor(
                name="read_file", description="",
                input_schema={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
                server_id="filesystem",
            )],
        )

    fake_mcp_pool.call_tool.assert_not_awaited()
    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["success"] is False
    assert record["executed"] is False
    assert record["dry_run"] is True
    assert record["error"] is not None


@pytest.mark.integration
async def test_orchestrator_dry_run_multi_step(fake_mcp_pool, settings, trace_writer, fake_catalog):
    """Two consecutive tool-use turns in dry_run: pool never called, two trace records."""
    import json

    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[
            make_tool_use_response(),
            make_tool_use_response(),
            make_end_turn_response(),
        ]),
    ):
        resp = await orch.run(
            ChatRequest(message="read a file twice", dry_run=True),
            fake_catalog,
        )

    fake_mcp_pool.call_tool.assert_not_awaited()
    assert resp.steps == 2
    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 2
    steps = [json.loads(line)["step"] for line in lines]
    assert steps == [1, 2]
    for line in lines:
        record = json.loads(line)
        assert record["dry_run"] is True
        assert record["executed"] is False


# ---------------------------------------------------------------------------
# Retry policy (SPEC FR7)
# ---------------------------------------------------------------------------


class TestRetryPolicy:
    """Four canonical retry paths — asyncio.sleep monkeypatched to a no-op."""

    @pytest.mark.integration
    async def test_transient_then_success(
        self, fake_mcp_pool, settings, trace_writer, fake_catalog, monkeypatch
    ):
        """Two transient failures then success → retries=2, attempt=3, success=True."""
        import json

        monkeypatch.setattr("toolforge.agent.orchestrator.asyncio.sleep", AsyncMock())

        content_block = fake_mcp_pool.call_tool.return_value
        fake_mcp_pool.call_tool.side_effect = [
            TimeoutError("timed out"),
            ConnectionError("reset"),
            content_block,
        ]

        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
        ):
            resp = await orch.run(ChatRequest(message="read a file"), fake_catalog)

        assert resp.steps == 1
        lines = settings.trace_sink.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["success"] is True
        assert record["retries"] == 2
        assert record["attempt"] == 3
        assert record["retry_reason"] == "ConnectionError"  # last transient exc before success

    @pytest.mark.integration
    async def test_transient_exhausted(
        self, fake_mcp_pool, settings, trace_writer, fake_catalog, monkeypatch
    ):
        """Three transient failures → retries=2, attempt=3, success=False."""
        import json

        monkeypatch.setattr("toolforge.agent.orchestrator.asyncio.sleep", AsyncMock())
        fake_mcp_pool.call_tool.side_effect = [
            ConnectionError("refused"),
            ConnectionError("refused"),
            ConnectionError("refused"),
        ]

        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
        ):
            await orch.run(ChatRequest(message="read a file"), fake_catalog)

        lines = settings.trace_sink.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["success"] is False
        assert record["retries"] == 2
        assert record["attempt"] == 3
        assert "refused" in record["error"]

    @pytest.mark.integration
    async def test_terminal_no_retry(
        self, fake_mcp_pool, settings, trace_writer, fake_catalog, monkeypatch
    ):
        """Non-transient exception → pool called once, retries=0, attempt=1."""
        import json

        monkeypatch.setattr("toolforge.agent.orchestrator.asyncio.sleep", AsyncMock())
        fake_mcp_pool.call_tool.side_effect = ValueError("bad schema")

        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
        ):
            await orch.run(ChatRequest(message="read a file"), fake_catalog)

        assert fake_mcp_pool.call_tool.call_count == 1
        lines = settings.trace_sink.read_text().splitlines()
        assert len(lines) == 1
        record = json.loads(lines[0])
        assert record["success"] is False
        assert record["retries"] == 0
        assert record["attempt"] == 1

    @pytest.mark.integration
    async def test_dry_run_skips_retry(
        self, fake_mcp_pool, settings, trace_writer, fake_catalog, monkeypatch
    ):
        """dry_run=True — pool never called regardless of side_effect."""
        monkeypatch.setattr("toolforge.agent.orchestrator.asyncio.sleep", AsyncMock())
        fake_mcp_pool.call_tool.side_effect = ConnectionError("should never fire")

        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
        ):
            resp = await orch.run(
                ChatRequest(message="read a file", dry_run=True), fake_catalog
            )

        fake_mcp_pool.call_tool.assert_not_awaited()
        assert resp.dry_run is True


@pytest.mark.integration
async def test_orchestrator_dry_run_no_session_pollution(fake_mcp_pool, settings, trace_writer):
    """Dry-run does not add to session_used_servers, so rule 3 cannot fire on step 2."""
    import json

    from tests.conftest import FS_READ_TOOL, GH_READ_TOOL, _Message, _ToolUseBlock

    # Two candidates, no explicit mention, open schemas → both validate → rule 2 ambiguous.
    # Empty session → rule 3 falls through. No embeddings → rule 4 falls through.
    # Rule 5 fires on both steps; session is NOT polluted between them.
    open_fs = FS_READ_TOOL.model_copy(update={"input_schema": {"type": "object"}})
    open_gh = GH_READ_TOOL.model_copy(update={"input_schema": {"type": "object"}})
    two_catalog = [open_fs, open_gh]

    tool_block = _ToolUseBlock(name="read_file", input={})
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[
            _Message(stop_reason="tool_use", content=[tool_block]),
            _Message(stop_reason="tool_use", content=[tool_block]),
            make_end_turn_response(),
        ]),
    ):
        resp = await orch.run(
            ChatRequest(message="read twice", dry_run=True),
            two_catalog,
        )

    assert resp.steps == 2
    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 2
    for line in lines:
        record = json.loads(line)
        # Rule 3 (session-recency) must NOT have fired — dry-run doesn't accumulate history
        assert record["selection_rule"] != "session-recency"
        assert record["selection_rule"] == "priority-order"


# ---------------------------------------------------------------------------
# Parallel tool execution (SPEC FR5)
# ---------------------------------------------------------------------------


class TestParallelExecution:
    """Tests for asyncio.gather-based parallel dispatch of tool_use blocks."""

    @pytest.mark.unit
    def test_make_multi_tool_use_response_helper(self):
        resp = make_multi_tool_use_response([
            ("id_a", "read_file", {"path": "/a"}),
            ("id_b", "read_file", {"path": "/b"}),
        ])
        assert resp.stop_reason == "tool_use"
        assert len(resp.content) == 2
        assert resp.content[0].id == "id_a"
        assert resp.content[0].input == {"path": "/a"}
        assert resp.content[1].id == "id_b"
        assert resp.content[1].input == {"path": "/b"}

    @pytest.mark.integration
    async def test_two_blocks_both_succeed(
        self, fake_mcp_pool, settings, trace_writer, fake_catalog
    ):
        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[
                make_multi_tool_use_response([
                    ("id_a", "read_file", {"path": "/a"}),
                    ("id_b", "read_file", {"path": "/b"}),
                ]),
                make_end_turn_response(),
            ]),
        ):
            resp = await orch.run(ChatRequest(message="read two files"), fake_catalog)

        assert resp.steps == 2
        lines = settings.trace_sink.read_text().splitlines()
        assert len(lines) == 2
        records = sorted([json.loads(ln) for ln in lines], key=lambda r: r["step"])
        assert records[0]["step"] == 1
        assert records[1]["step"] == 2
        assert all(r["success"] is True for r in records)

        # Both tool_result entries must be in the messages sent to the LLM.
        # Verify by checking that both tool_use_ids were ack'd.
        assert fake_mcp_pool.call_tool.await_count == 2

    @pytest.mark.integration
    async def test_two_blocks_one_fails(
        self, fake_mcp_pool, settings, trace_writer, fake_catalog
    ):
        """One terminal failure, one success — both trace records emitted."""
        async def dispatch_side_effect(server_id, tool_name, tool_args):
            if tool_args.get("path") == "/bad":
                raise ValueError("access denied")
            result = MagicMock()
            result.isError = False
            result.content = [MagicMock(text="ok")]
            return result

        fake_mcp_pool.call_tool.side_effect = dispatch_side_effect

        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[
                make_multi_tool_use_response([
                    ("id_bad", "read_file", {"path": "/bad"}),
                    ("id_ok", "read_file", {"path": "/ok"}),
                ]),
                make_end_turn_response(),
            ]),
        ):
            await orch.run(ChatRequest(message="read two files"), fake_catalog)

        lines = settings.trace_sink.read_text().splitlines()
        assert len(lines) == 2
        records = {json.loads(ln)["step"]: json.loads(ln) for ln in lines}
        fail_rec = records[1]
        ok_rec = records[2]
        assert fail_rec["success"] is False
        assert "access denied" in fail_rec["error"]
        assert ok_rec["success"] is True

    @pytest.mark.integration
    async def test_recency_visibility_across_turns(
        self, fake_mcp_pool, settings, trace_writer
    ):
        """Turn-1 parallel blocks don't see each other's server via rule-3;
        turn-2 sees both because the deferred appends are visible by then."""
        from tests.conftest import FS_READ_TOOL, GH_READ_TOOL

        # Open schemas so both servers pass validation for any input
        open_fs = FS_READ_TOOL.model_copy(update={"input_schema": {"type": "object"}})
        open_gh = GH_READ_TOOL.model_copy(update={"input_schema": {"type": "object"}})
        two_catalog = [open_fs, open_gh]

        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[
                make_multi_tool_use_response([
                    ("id_1", "read_file", {}),
                    ("id_2", "read_file", {}),
                ]),
                make_tool_use_response(),          # turn 2: single block
                make_end_turn_response(),
            ]),
        ):
            await orch.run(ChatRequest(message="read files"), two_catalog)

        lines = settings.trace_sink.read_text().splitlines()
        records = sorted([json.loads(ln) for ln in lines], key=lambda r: r["step"])

        # Turn 1 blocks (steps 1, 2): no recency visible within the parallel turn
        turn1 = records[:2]
        for r in turn1:
            assert r["selection_rule"] != "session-recency", (
                f"step {r['step']}: within-turn recency should not fire"
            )
        # Turn 2 block (step 3): should see the two servers appended after turn 1
        turn2 = records[2]
        assert turn2["selection_rule"] == "session-recency", (
            "turn 2 should fire rule-3 because turn-1 appends are now visible"
        )

    @pytest.mark.integration
    async def test_parallel_independent_retries(
        self, fake_mcp_pool, settings, trace_writer, fake_catalog, monkeypatch
    ):
        """Each block retries independently — A retries twice, B succeeds first try."""
        monkeypatch.setattr("toolforge.agent.orchestrator.asyncio.sleep", AsyncMock())

        success_result = MagicMock()
        success_result.isError = False
        success_result.content = [MagicMock(text="done")]

        call_counts = {"a": 0, "b": 0}

        async def per_block_side_effect(server_id, tool_name, tool_args):
            if tool_args.get("path") == "/a":
                call_counts["a"] += 1
                if call_counts["a"] <= 2:
                    raise ConnectionError("transient")
                return success_result
            else:
                call_counts["b"] += 1
                return success_result

        fake_mcp_pool.call_tool.side_effect = per_block_side_effect

        orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
        with patch.object(
            orch._client.messages, "create",
            new=AsyncMock(side_effect=[
                make_multi_tool_use_response([
                    ("id_a", "read_file", {"path": "/a"}),
                    ("id_b", "read_file", {"path": "/b"}),
                ]),
                make_end_turn_response(),
            ]),
        ):
            await orch.run(ChatRequest(message="read two files"), fake_catalog)

        lines = settings.trace_sink.read_text().splitlines()
        assert len(lines) == 2
        records = {json.loads(ln)["step"]: json.loads(ln) for ln in lines}
        rec_a = records[1]
        rec_b = records[2]
        assert rec_a["success"] is True
        assert rec_a["retries"] == 2
        assert rec_a["attempt"] == 3
        assert rec_b["success"] is True
        assert rec_b["retries"] == 0
        assert rec_b["attempt"] == 1


# ---------------------------------------------------------------------------
# event_sink parameter
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_run_without_event_sink_unchanged(fake_mcp_pool, settings, trace_writer, fake_catalog):
    """Omitting event_sink leaves existing /chat behaviour exactly as before."""
    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
    ):
        resp = await orch.run(ChatRequest(message="read a file"), fake_catalog)

    assert resp.steps == 1
    assert resp.cost_usd >= 0
    lines = settings.trace_sink.read_text().splitlines()
    assert len(lines) == 1


@pytest.mark.integration
async def test_run_with_event_sink_emits_per_trace(fake_mcp_pool, settings, trace_writer, fake_catalog):
    """event_sink receives one tool.result event per TraceRecord written."""
    emitted: list[dict] = []

    async def sink(event: dict) -> None:
        emitted.append(event)

    orch = Orchestrator(fake_mcp_pool, trace_writer, settings)
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
    ):
        await orch.run(ChatRequest(message="read a file"), fake_catalog, event_sink=sink)

    lines = settings.trace_sink.read_text().splitlines()
    assert len(emitted) == len(lines), "one SSE event per trace record"
    assert all(e["event"] == "tool.result" for e in emitted)
    trace_data = json.loads(lines[0])
    sse_data = emitted[0]["data"]
    assert sse_data["step"] == trace_data["step"]
    assert sse_data["tool"] == trace_data["tool"]
    assert sse_data["server"] == trace_data["server"]
    assert sse_data["selection_rule"] == trace_data["selection_rule"]
    assert sse_data["success"] == trace_data["success"]
