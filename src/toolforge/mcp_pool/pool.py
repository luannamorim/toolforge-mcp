from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from toolforge.models.catalog import MCPServerConfig, ToolDescriptor

logger = logging.getLogger(__name__)


class MCPClientPool:
    """Manages one persistent stdio MCP connection per configured server."""

    def __init__(self, configs: list[MCPServerConfig]) -> None:
        self._configs = {c.id: c for c in configs}
        self._sessions: dict[str, ClientSession] = {}
        self._stacks: dict[str, AsyncExitStack] = {}
        self._connected: dict[str, bool] = {c.id: False for c in configs}

    @property
    def connection_status(self) -> dict[str, bool]:
        return dict(self._connected)

    @property
    def connected_servers(self) -> list[str]:
        return [sid for sid, ok in self._connected.items() if ok]

    async def connect_all(self) -> None:
        for server_id, config in self._configs.items():
            await self._connect(server_id, config)

    async def _connect(self, server_id: str, config: MCPServerConfig) -> None:
        try:
            stack = AsyncExitStack()
            params = StdioServerParameters(
                command=config.command,
                args=config.args,
                env=config.env if config.env else None,
            )
            read, write = await stack.enter_async_context(stdio_client(params))
            session = ClientSession(read, write)
            await stack.enter_async_context(session)
            await session.initialize()
            self._sessions[server_id] = session
            self._stacks[server_id] = stack
            self._connected[server_id] = True
            logger.info("Connected to MCP server %s", server_id)
        except Exception:
            self._connected[server_id] = False
            logger.exception("Failed to connect to MCP server %s", server_id)

    async def disconnect_all(self) -> None:
        for server_id, stack in list(self._stacks.items()):
            try:
                await stack.aclose()
            except Exception:
                logger.exception("Error disconnecting from %s", server_id)
        self._sessions.clear()
        self._stacks.clear()

    async def list_tools(self, server_id: str) -> list[ToolDescriptor]:
        session = self._sessions[server_id]
        result = await session.list_tools()
        return [
            ToolDescriptor(
                name=t.name,
                description=t.description or "",
                input_schema=t.inputSchema if t.inputSchema else {"type": "object", "properties": {}},
                server_id=server_id,
            )
            for t in result.tools
        ]

    async def call_tool(self, server_id: str, tool_name: str, arguments: dict) -> Any:
        session = self._sessions[server_id]
        return await session.call_tool(tool_name, arguments)
