import pytest


@pytest.mark.unit
def test_health_ok_shape(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "ok"
    assert isinstance(data["servers"], list)
    ids = [s["id"] for s in data["servers"]]
    assert "filesystem" in ids


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
