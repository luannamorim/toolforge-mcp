import pytest


@pytest.mark.unit
def test_tools_returns_catalog(client):
    resp = client.get("/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    tool = data["tools"][0]
    assert tool["name"] == "read_file"
    assert tool["server_id"] == "filesystem"


@pytest.mark.unit
def test_tools_shape(client):
    data = client.get("/tools").json()
    assert set(data.keys()) == {"count", "tools"}
    assert data["count"] == len(data["tools"])


@pytest.mark.unit
def test_tools_excludes_embedding(client):
    data = client.get("/tools").json()
    for tool in data["tools"]:
        assert set(tool.keys()) == {"name", "description", "input_schema", "server_id"}
        assert "description_embedding" not in tool


@pytest.mark.unit
def test_tools_empty_when_degraded(client_degraded):
    resp = client_degraded.get("/tools")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 0
    assert data["tools"] == []
