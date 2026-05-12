"""Stub MCP stdio server for eval rule-coverage testing.

Runs as a stdio subprocess: python -m evals.stub_server --id <local|cloud>

Two server identities share the same tool NAMES but with deliberately
different schemas so the selector's 5-rule heuristic can be exercised:

  search  — local: {path: string},  cloud: {url: string}
             Different required keys → only one validates any given LLM arg
             set (tests rule 2: argument-type).

  lookup  — local + cloud: identical {query: string} schema, different
             descriptions.  Both validate → rule 2 falls through.
             HashingEmbedder scores are within margin → rule 4 falls through.
             Falls to rule 5 (priority-order) which picks whichever server
             appears first in mcp.servers.eval.json.

Tool handlers return a trivial synthetic string; dry_run=True means they are
never invoked by the eval harness, but a real handler is required for a valid
MCP protocol implementation.
"""

from __future__ import annotations

import argparse
import asyncio

import mcp.server.stdio
import mcp.types as types
from mcp.server import NotificationOptions, Server
from mcp.server.models import InitializationOptions

# ---------------------------------------------------------------------------
# Per-identity tool definitions
# ---------------------------------------------------------------------------

_TOOLS: dict[str, list[types.Tool]] = {
    "local": [
        types.Tool(
            name="search",
            description="Search the local filesystem index by absolute path.",
            inputSchema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            },
        ),
        types.Tool(
            name="lookup",
            description="Look up a term in the local index.",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    ],
    "cloud": [
        types.Tool(
            name="search",
            description="Search a remote cloud index by URL.",
            inputSchema={
                "type": "object",
                "properties": {"url": {"type": "string"}},
                "required": ["url"],
            },
        ),
        types.Tool(
            name="lookup",
            description="Look up a term in the cloud index.",
            inputSchema={
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        ),
    ],
}


def build_server(server_id: str) -> Server:
    server = Server(f"stub-{server_id}")
    tool_list = _TOOLS[server_id]

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return tool_list

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict | None
    ) -> list[types.TextContent]:
        return [types.TextContent(type="text", text=f"[stub-{server_id}] {name} called")]

    return server


async def _serve(server_id: str) -> None:
    server = build_server(server_id)
    async with mcp.server.stdio.stdio_server() as (read_stream, write_stream):
        await server.run(
            read_stream,
            write_stream,
            InitializationOptions(
                server_name=server.name,
                server_version="0.1.0",
                capabilities=server.get_capabilities(
                    notification_options=NotificationOptions(),
                    experimental_capabilities={},
                ),
            ),
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="ToolForge stub MCP server")
    parser.add_argument("--id", required=True, choices=list(_TOOLS), dest="server_id")
    args = parser.parse_args()
    asyncio.run(_serve(args.server_id))
