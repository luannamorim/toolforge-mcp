from unittest.mock import AsyncMock

import pytest
from starlette.testclient import TestClient


@pytest.mark.unit
def test_health_ok_shape(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["servers"], list)
    ids = [s["id"] for s in data["servers"]]
    assert "filesystem" in ids
    assert data["cache"]["connected"] is True


@pytest.mark.unit
def test_health_ok_all_connected(client):
    data = client.get("/health").json()
    for server in data["servers"]:
        assert server["connected"] is True


@pytest.mark.unit
def test_health_degraded_status(client_degraded):
    resp = client_degraded.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "degraded"


@pytest.mark.unit
def test_health_degraded_server_false(client_degraded):
    data = client_degraded.get("/health").json()
    fs = next(s for s in data["servers"] if s["id"] == "filesystem")
    assert fs["connected"] is False


@pytest.mark.unit
def test_health_cache_unreachable(test_app):
    test_app.state.cache.ping = AsyncMock(return_value=False)
    data = TestClient(test_app).get("/health").json()
    assert data["status"] == "degraded"
    assert data["cache"]["connected"] is False
    for server in data["servers"]:
        assert server["connected"] is True
