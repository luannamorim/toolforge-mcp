"""Rate-limit (429) handling: /chat returns 429 with Retry-After; /chat/stream emits error event."""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import httpx
import pytest
from anthropic import RateLimitError


def _make_rate_limit_error(retry_after: str = "30") -> RateLimitError:
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    resp = httpx.Response(status_code=429, headers={"retry-after": retry_after}, request=req)
    return RateLimitError("rate limited", response=resp, body=None)


def _parse_sse(raw: bytes) -> list[tuple[str, dict]]:
    events: list[tuple[str, dict]] = []
    event_name: str | None = None
    for line in raw.decode().splitlines():
        if line.startswith("event: "):
            event_name = line[7:]
        elif line.startswith("data: ") and event_name is not None:
            events.append((event_name, json.loads(line[6:])))
            event_name = None
    return events


@pytest.mark.unit
def test_chat_returns_429_on_rate_limit(test_app, client):
    orch = test_app.state.orchestrator
    with patch.object(orch._client.messages, "create", new=AsyncMock(side_effect=_make_rate_limit_error("30"))):
        resp = client.post("/chat", json={"message": "hello"})
    assert resp.status_code == 429
    assert resp.headers.get("Retry-After") == "30"
    assert "detail" in resp.json()


@pytest.mark.unit
def test_chat_stream_emits_error_event_on_rate_limit(test_app, client):
    orch = test_app.state.orchestrator
    with patch.object(orch._client.messages, "create", new=AsyncMock(side_effect=_make_rate_limit_error("45"))):
        resp = client.post("/chat/stream", json={"message": "hello"})
    events = _parse_sse(resp.content)
    error_events = [(name, data) for name, data in events if name == "error"]
    assert len(error_events) == 1
    data = error_events[0][1]
    assert data.get("message") == "rate_limited"
    assert data.get("retry_after") == "45"
