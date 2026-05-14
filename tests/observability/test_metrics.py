"""Tests for OpenTelemetry metric emission from the orchestrator."""

from __future__ import annotations

from dataclasses import dataclass, field
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import InMemoryMetricReader

import toolforge.observability.metrics as metrics_mod
from tests.conftest import _Message, make_end_turn_response
from toolforge.agent.orchestrator import Orchestrator
from toolforge.models.catalog import ToolDescriptor
from toolforge.models.chat import ChatRequest


def _points_for(reader: InMemoryMetricReader, name: str) -> list:
    data = reader.get_metrics_data()
    if data is None:
        return []
    for rm in data.resource_metrics:
        for sm in rm.scope_metrics:
            for m in sm.metrics:
                if m.name == name:
                    return list(m.data.data_points)
    return []


@dataclass
class _BadBlock:
    type: str = "tool_use"
    id: str = "toolu_err"
    name: str = "read_file"
    input: dict = field(default_factory=lambda: {"wrong_key": 99})


@dataclass
class _GoodBlock:
    type: str = "tool_use"
    id: str = "toolu_ok"
    name: str = "read_file"
    input: dict = field(default_factory=lambda: {"path": "/tmp/hello.txt"})


_CATALOG = [
    ToolDescriptor(
        name="read_file",
        description="Read a file from the filesystem",
        input_schema={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
        server_id="filesystem",
    )
]


@pytest.fixture(autouse=True)
def fresh_meter(monkeypatch):
    """Patch module-level instruments with fresh ones bound to an InMemoryMetricReader.

    Avoids touching the global MeterProvider (a one-way door) so tests can run
    in any order without the "Overriding of current MeterProvider is not allowed"
    guard firing.
    """
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    # Get the meter directly from the local provider — not via get_meter() which
    # goes through the global singleton and would bind to the wrong reader.
    meter = provider.get_meter("toolforge")
    monkeypatch.setattr(metrics_mod, "_task_latency",
        meter.create_histogram("toolforge.task.latency_ms", unit="ms"))
    monkeypatch.setattr(metrics_mod, "_task_cost",
        meter.create_histogram("toolforge.task.cost_usd", unit="USD"))
    monkeypatch.setattr(metrics_mod, "_tool_errors",
        meter.create_counter("toolforge.tool.errors_total"))
    monkeypatch.setattr(metrics_mod, "_selection_rule",
        meter.create_counter("toolforge.selection.heuristic_rule_fired"))
    yield reader


@pytest.fixture
def fake_pool():
    pool = MagicMock()
    content_block = MagicMock()
    content_block.text = "Hello!"
    tool_result = MagicMock()
    tool_result.isError = False
    tool_result.content = [content_block]
    pool.call_tool = AsyncMock(return_value=tool_result)
    return pool


@pytest.fixture
def settings(tmp_path):
    from toolforge.config import Settings
    return Settings(anthropic_api_key="test", trace_sink=tmp_path / "traces.jsonl")


@pytest.fixture
def trace_writer(settings):
    from toolforge.traces.writer import TraceWriter
    return TraceWriter(settings.trace_sink, verbose=False)


@pytest.mark.unit
async def test_task_histograms_emit_on_end_turn(fresh_meter, fake_pool, settings, trace_writer):
    """A completed run emits one data point each for latency and cost histograms."""
    orch = Orchestrator(fake_pool, trace_writer, settings)
    mock_create = AsyncMock(return_value=make_end_turn_response("Done"))

    with patch.object(orch._client.messages, "create", new=mock_create):
        await orch.run(ChatRequest(message="list files"), _CATALOG)

    latency_pts = _points_for(fresh_meter, "toolforge.task.latency_ms")
    assert len(latency_pts) == 1
    assert latency_pts[0].sum >= 0
    cost_pts = _points_for(fresh_meter, "toolforge.task.cost_usd")
    assert len(cost_pts) == 1


@pytest.mark.unit
async def test_task_histogram_halted_attribute(fresh_meter, fake_pool, settings, trace_writer):
    """A cost-ceiling halt emits latency/cost with halted=True and halt_reason=cost_ceiling."""
    settings = settings.__class__(
        anthropic_api_key="test",
        trace_sink=settings.trace_sink,
        cost_ceiling_usd=0.0,
    )
    orch = Orchestrator(fake_pool, trace_writer, settings)
    mock_create = AsyncMock(return_value=make_end_turn_response("Done"))

    with patch.object(orch._client.messages, "create", new=mock_create):
        await orch.run(ChatRequest(message="list files"), _CATALOG)

    pts = _points_for(fresh_meter, "toolforge.task.latency_ms")
    assert len(pts) == 1
    attrs = dict(pts[0].attributes)
    assert attrs.get("halted") is True
    assert attrs.get("halt_reason") == "cost_ceiling"


@pytest.mark.unit
async def test_selection_rule_counter_emits(fresh_meter, fake_pool, settings, trace_writer):
    """After a tool dispatch, heuristic_rule_fired counter has ≥1 data point."""
    orch = Orchestrator(fake_pool, trace_writer, settings)
    tool_msg = _Message(stop_reason="tool_use", content=[_GoodBlock()])
    end_msg = make_end_turn_response("Done")
    mock_create = AsyncMock(side_effect=[tool_msg, end_msg])

    with patch.object(orch._client.messages, "create", new=mock_create):
        await orch.run(ChatRequest(message="read file"), _CATALOG)

    pts = _points_for(fresh_meter, "toolforge.selection.heuristic_rule_fired")
    assert len(pts) >= 1
    attrs = dict(pts[0].attributes)
    assert attrs.get("rule") == "single-candidate"
    assert attrs.get("server") == "filesystem"


@pytest.mark.unit
async def test_tool_error_counter_validation(fresh_meter, fake_pool, settings, trace_writer):
    """A validation failure increments the error counter with reason=validation."""
    orch = Orchestrator(fake_pool, trace_writer, settings)
    bad_msg = _Message(stop_reason="tool_use", content=[_BadBlock()])
    end_msg = make_end_turn_response("Done")
    mock_create = AsyncMock(side_effect=[bad_msg, end_msg])

    with patch.object(orch._client.messages, "create", new=mock_create):
        await orch.run(ChatRequest(message="read file"), _CATALOG)

    pts = _points_for(fresh_meter, "toolforge.tool.errors_total")
    assert len(pts) >= 1
    reasons = {dict(p.attributes).get("reason") for p in pts}
    assert "validation" in reasons


@pytest.mark.unit
async def test_tool_error_counter_tool_error(fresh_meter, settings, trace_writer):
    """A tool returning isError=True increments the error counter with reason=tool_error."""
    pool = MagicMock()
    content_block = MagicMock()
    content_block.text = "file not found"
    tool_result = MagicMock()
    tool_result.isError = True
    tool_result.content = [content_block]
    pool.call_tool = AsyncMock(return_value=tool_result)

    orch = Orchestrator(pool, trace_writer, settings)
    tool_msg = _Message(stop_reason="tool_use", content=[_GoodBlock()])
    end_msg = make_end_turn_response("Done")
    mock_create = AsyncMock(side_effect=[tool_msg, end_msg])

    with patch.object(orch._client.messages, "create", new=mock_create):
        await orch.run(ChatRequest(message="read file"), _CATALOG)

    pts = _points_for(fresh_meter, "toolforge.tool.errors_total")
    assert len(pts) >= 1
    reasons = {dict(p.attributes).get("reason") for p in pts}
    assert "tool_error" in reasons
