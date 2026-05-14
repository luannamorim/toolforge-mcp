"""Integration tests for the off-domain input guardrail in /chat and /chat/stream."""

from __future__ import annotations

import pytest


@pytest.mark.integration
def test_off_domain_chat_rejected_with_400(client):
    resp = client.post("/chat", json={"message": "write me a poem"})
    assert resp.status_code == 400
    assert "off-domain" in resp.json()["detail"]


@pytest.mark.integration
def test_off_domain_stream_rejected_with_400(client):
    resp = client.post("/chat/stream", json={"message": "tell me a joke"})
    assert resp.status_code == 400
    assert "off-domain" in resp.json()["detail"]


@pytest.mark.integration
def test_operational_prompt_not_rejected(client):
    """A clearly operational prompt must not be blocked by the classifier."""
    from unittest.mock import AsyncMock, patch

    from tests.conftest import make_end_turn_response

    orch = client.app.state.orchestrator
    with patch.object(
        orch._client.messages,
        "create",
        new=AsyncMock(return_value=make_end_turn_response("Done")),
    ):
        resp = client.post(
            "/chat",
            json={"message": "read the file /tmp/hello.txt"},
        )
    assert resp.status_code == 200
