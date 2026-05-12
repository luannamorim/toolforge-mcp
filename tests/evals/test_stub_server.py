"""Unit tests for evals.stub_server — no subprocess, no MCP protocol."""

from __future__ import annotations

import pytest

from evals.stub_server import _TOOLS, build_server


@pytest.mark.unit
def test_both_identities_expose_search_and_lookup():
    for server_id in ("local", "cloud"):
        tool_names = {t.name for t in _TOOLS[server_id]}
        assert "search" in tool_names, f"{server_id} missing 'search'"
        assert "lookup" in tool_names, f"{server_id} missing 'lookup'"


@pytest.mark.unit
def test_search_schemas_differ_between_local_and_cloud():
    local_search = next(t for t in _TOOLS["local"] if t.name == "search")
    cloud_search = next(t for t in _TOOLS["cloud"] if t.name == "search")
    local_required = set(local_search.inputSchema.get("required", []))
    cloud_required = set(cloud_search.inputSchema.get("required", []))
    assert local_required != cloud_required, "search schemas must differ so argument-type rule can fire"
    assert "path" in local_required
    assert "url" in cloud_required


@pytest.mark.unit
def test_lookup_schemas_identical_between_local_and_cloud():
    local_lookup = next(t for t in _TOOLS["local"] if t.name == "lookup")
    cloud_lookup = next(t for t in _TOOLS["cloud"] if t.name == "lookup")
    assert local_lookup.inputSchema == cloud_lookup.inputSchema, (
        "lookup schemas must be identical so rule 2 falls through to priority-order"
    )


@pytest.mark.unit
def test_build_server_returns_server_with_correct_name():
    server = build_server("local")
    assert "local" in server.name
