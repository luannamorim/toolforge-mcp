"""Integration tests for PayloadSizeMiddleware."""

from __future__ import annotations

import json

import pytest
from fastapi import FastAPI
from starlette.responses import JSONResponse
from starlette.testclient import TestClient

from toolforge.guardrails.payload import PayloadSizeMiddleware

_MAX = 32 * 1024  # 32KB


def _make_app(max_bytes: int = _MAX) -> FastAPI:
    app = FastAPI()
    app.add_middleware(PayloadSizeMiddleware, max_bytes=max_bytes)

    @app.post("/chat")
    async def chat():
        return JSONResponse({"ok": True})

    @app.post("/chat/stream")
    async def chat_stream():
        return JSONResponse({"ok": True})

    @app.get("/health")
    async def health():
        return JSONResponse({"ok": True})

    return app


@pytest.fixture
def mw_client() -> TestClient:
    return TestClient(_make_app(), raise_server_exceptions=False)


@pytest.mark.integration
def test_payload_within_limit_passes(mw_client: TestClient):
    body = json.dumps({"message": "a" * (31 * 1024)}).encode()
    resp = mw_client.post(
        "/chat",
        content=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    assert resp.status_code == 200


@pytest.mark.integration
def test_payload_over_limit_rejected(mw_client: TestClient):
    body = json.dumps({"message": "a" * (33 * 1024)}).encode()
    resp = mw_client.post(
        "/chat",
        content=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    assert resp.status_code == 400
    assert "payload too large" in resp.json()["detail"]


@pytest.mark.integration
def test_chat_stream_over_limit_rejected(mw_client: TestClient):
    body = json.dumps({"message": "a" * (33 * 1024)}).encode()
    resp = mw_client.post(
        "/chat/stream",
        content=body,
        headers={"Content-Type": "application/json", "Content-Length": str(len(body))},
    )
    assert resp.status_code == 400


@pytest.mark.integration
def test_health_endpoint_unaffected(mw_client: TestClient):
    """GET /health has no body — middleware must not block it."""
    resp = mw_client.get("/health")
    assert resp.status_code == 200
