"""503 degraded-mode gate: /chat and /chat/stream reject when an MCP server is down."""

from unittest.mock import AsyncMock, patch

import pytest
from starlette.testclient import TestClient

from tests.conftest import make_end_turn_response


@pytest.mark.unit
def test_chat_503_when_server_down(client_degraded):
    resp = client_degraded.post("/chat", json={"message": "hello"})
    assert resp.status_code == 503
    assert "filesystem" in resp.json()["detail"]


@pytest.mark.unit
def test_chat_stream_503_when_server_down(client_degraded):
    resp = client_degraded.post("/chat/stream", json={"message": "hello"})
    assert resp.status_code == 503
    assert "filesystem" in resp.json()["detail"]


@pytest.mark.unit
def test_chat_dry_run_bypasses_degraded_guard(test_app_degraded):
    orch = test_app_degraded.state.orchestrator
    mock_create = AsyncMock(return_value=make_end_turn_response("plan"))
    with patch.object(orch._client.messages, "create", new=mock_create):
        resp = TestClient(test_app_degraded).post("/chat", json={"message": "hello", "dry_run": True})
    assert resp.status_code != 503
