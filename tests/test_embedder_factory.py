"""Unit tests for _build_embedder factory in app.py."""

from __future__ import annotations

import logging

import pytest

from toolforge.agent.embedder import HashingEmbedder, VoyageEmbedder
from toolforge.app import _build_embedder
from toolforge.config import Settings


@pytest.mark.unit
def test_voyage_backend_with_key_returns_voyage_embedder():
    settings = Settings(
        embedder_backend="voyage",
        voyage_api_key="vk-test",
        mcp_servers_config="mcp.servers.json",
    )
    embedder = _build_embedder(settings)
    assert isinstance(embedder, VoyageEmbedder)
    assert embedder.embedder_id == "voyage-3-lite"
    embedder.close()


@pytest.mark.unit
def test_voyage_backend_without_key_falls_back_to_hashing(caplog):
    settings = Settings(
        embedder_backend="voyage",
        voyage_api_key="",
        mcp_servers_config="mcp.servers.json",
    )
    with caplog.at_level(logging.WARNING, logger="toolforge.app"):
        embedder = _build_embedder(settings)

    assert isinstance(embedder, HashingEmbedder)
    assert embedder.embedder_id == "hashing-v1"
    assert any("VOYAGE_API_KEY" in r.message for r in caplog.records)


@pytest.mark.unit
def test_hashing_backend_ignores_voyage_key():
    settings = Settings(
        embedder_backend="hashing",
        voyage_api_key="vk-test",
        mcp_servers_config="mcp.servers.json",
    )
    embedder = _build_embedder(settings)
    assert isinstance(embedder, HashingEmbedder)
    assert embedder.embedder_id == "hashing-v1"
