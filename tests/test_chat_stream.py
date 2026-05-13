"""Integration tests for POST /chat/stream (SSE)."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_end_turn_response, make_tool_use_response


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
    """Parse SSE bytes into [(event_name, data_dict), ...]."""
    events: list[tuple[str, dict]] = []
    event_name: str | None = None
    for line in raw.decode().splitlines():
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: ") and event_name is not None:
            events.append((event_name, json.loads(line[6:])))
            event_name = None
    return events


@pytest.mark.integration
def test_session_start_emitted_first(test_app, client):
    """First event is session.start with the request's session_id echoed."""
    orch = test_app.state.orchestrator
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(return_value=make_end_turn_response("Done!")),
    ):
        resp = client.post(
            "/chat/stream",
            json={"message": "hello", "session_id": "test-session-42"},
        )

    assert resp.status_code == 200
    events = _parse_sse(resp.content)
    assert events[0][0] == "session.start"
    assert events[0][1]["session_id"] == "test-session-42"
    assert events[0][1]["dry_run"] is False


@pytest.mark.integration
def test_tool_result_per_tool_call(test_app, client):
    """One tool call → exactly one tool.result event with the trace fields."""
    orch = test_app.state.orchestrator
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
    ):
        resp = client.post("/chat/stream", json={"message": "read a file"})

    events = _parse_sse(resp.content)
    tool_events = [(name, data) for name, data in events if name == "tool.result"]
    assert len(tool_events) == 1
    _, data = tool_events[0]
    assert data["tool"] == "read_file"
    assert data["server"] == "filesystem"
    assert data["selection_rule"] == "single-candidate"
    assert data["success"] is True
    assert "step" in data
    assert "latency_ms" in data


@pytest.mark.integration
def test_final_response_terminates_stream(test_app, client):
    """final.response is the last event; its payload matches ChatResponse shape."""
    orch = test_app.state.orchestrator
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response("Done!")]),
    ):
        resp = client.post("/chat/stream", json={"message": "read a file"})

    events = _parse_sse(resp.content)
    assert events[-1][0] == "final.response"
    data = events[-1][1]
    for field in ("session_id", "response", "steps", "cost_usd", "dry_run"):
        assert field in data, f"missing field: {field}"
    assert data["response"] == "Done!"
    assert data["steps"] == 1


@pytest.mark.integration
def test_dry_run_emits_executed_false(test_app, client):
    """dry_run=True → tool.result.data.executed is False and final.response.dry_run is True."""
    orch = test_app.state.orchestrator
    with patch.object(
        orch._client.messages, "create",
        new=AsyncMock(side_effect=[make_tool_use_response(), make_end_turn_response()]),
    ):
        resp = client.post(
            "/chat/stream",
            json={"message": "read a file", "dry_run": True},
        )

    events = _parse_sse(resp.content)
    tool_events = [(name, data) for name, data in events if name == "tool.result"]
    assert len(tool_events) == 1
    assert tool_events[0][1]["executed"] is False
    assert tool_events[0][1]["dry_run"] is True

    final = next((data for name, data in events if name == "final.response"), None)
    assert final is not None
    assert final["dry_run"] is True


@pytest.mark.integration
def test_error_event_on_unhandled_exception(test_app, client):
    """If the orchestrator raises, the stream ends with a single error event."""
    with patch.object(
        test_app.state.orchestrator, "run",
        new=AsyncMock(side_effect=RuntimeError("unexpected boom")),
    ):
        resp = client.post("/chat/stream", json={"message": "trigger error"})

    events = _parse_sse(resp.content)
    error_events = [(name, data) for name, data in events if name == "error"]
    assert len(error_events) == 1
    assert "boom" in error_events[0][1]["message"]
    assert not any(name == "final.response" for name, _ in events)
