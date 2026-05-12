"""Live integration test: connect MCPClientPool to both filesystem and github servers.

Skipped automatically when GITHUB_PERSONAL_ACCESS_TOKEN is not set.
Run with:  uv run pytest -m live
"""

from __future__ import annotations

import os

import pytest

from toolforge.mcp_pool.pool import MCPClientPool
from toolforge.models.catalog import MCPServerConfig


@pytest.fixture
def github_pat() -> str:
    pat = os.environ.get("GITHUB_PERSONAL_ACCESS_TOKEN", "")
    if not pat:
        pytest.skip("GITHUB_PERSONAL_ACCESS_TOKEN not set")
    return pat


@pytest.fixture
def two_server_configs(github_pat: str) -> list[MCPServerConfig]:
    return [
        MCPServerConfig(
            id="filesystem",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-filesystem", "/tmp/toolforge-demo"],
        ),
        MCPServerConfig(
            id="github",
            command="npx",
            args=["-y", "@modelcontextprotocol/server-github"],
            env={"GITHUB_PERSONAL_ACCESS_TOKEN": "${GITHUB_PERSONAL_ACCESS_TOKEN}"},
        ),
    ]


@pytest.mark.live
@pytest.mark.asyncio
async def test_pool_connects_both_servers(two_server_configs):
    pool = MCPClientPool(two_server_configs)
    try:
        await pool.connect_all()
        status = pool.connection_status
        assert status.get("filesystem") is True, "filesystem server failed to connect"
        assert status.get("github") is True, "github server failed to connect"
        assert set(pool.connected_servers) == {"filesystem", "github"}
    finally:
        await pool.disconnect_all()


@pytest.mark.live
@pytest.mark.asyncio
async def test_github_lists_tools(two_server_configs):
    pool = MCPClientPool(two_server_configs)
    try:
        await pool.connect_all()
        assert pool.connection_status.get("github"), "github server not connected"
        tools = await pool.list_tools("github")
        assert len(tools) > 0, "github server returned no tools"
        for tool in tools:
            assert tool.name, "tool has empty name"
            assert tool.server_id == "github"
    finally:
        await pool.disconnect_all()
