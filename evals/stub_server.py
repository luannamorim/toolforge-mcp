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
import json
import os

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


_FAIL_PER_CALL = int(os.environ.get("STUB_FAIL_PER_CALL", "0"))
_FAIL_TYPES_ENV = os.environ.get("STUB_FAIL_TYPES", "ConnectionError")
_NAME_TO_EXC: dict[str, type[Exception]] = {
    "ConnectionError": ConnectionError,
    "TimeoutError": TimeoutError,
    "asyncio.TimeoutError": asyncio.TimeoutError,
    "OSError": OSError,
    "BrokenPipeError": BrokenPipeError,
}
_FAIL_TYPES: list[type[Exception]] = []
for _n in _FAIL_TYPES_ENV.split(","):
    _n = _n.strip()
    if _n in _NAME_TO_EXC:
        _FAIL_TYPES.append(_NAME_TO_EXC[_n])
    elif _n:
        import sys
        print(f"stub_server: unrecognised STUB_FAIL_TYPES entry {_n!r} — ignored", file=sys.stderr)


def build_server(server_id: str) -> Server:
    server = Server(f"stub-{server_id}")
    tool_list = _TOOLS[server_id]

    # Per-call failure injection: each distinct (name, arguments) gets its own
    # budget of _FAIL_PER_CALL failures and a deterministic exception class
    # assigned by insertion order (0th call type → ConnectionError, 1st →
    # TimeoutError, etc. cycling through _FAIL_TYPES).
    _args_state: dict[str, dict[str, int]] = {}

    @server.list_tools()
    async def handle_list_tools() -> list[types.Tool]:
        return tool_list

    @server.call_tool()
    async def handle_call_tool(
        name: str, arguments: dict | None
    ) -> list[types.TextContent]:
        if _FAIL_PER_CALL > 0 and _FAIL_TYPES:
            key = f"{name}:{json.dumps(arguments or {}, sort_keys=True)}"
            if key not in _args_state:
                # len() before insert gives the stable insertion-order index for this call
                _args_state[key] = {"remaining": _FAIL_PER_CALL, "type_idx": len(_args_state)}
            state = _args_state[key]
            if state["remaining"] > 0:
                state["remaining"] -= 1
                exc_cls = _FAIL_TYPES[state["type_idx"] % len(_FAIL_TYPES)]
                raise exc_cls("simulated transient failure")
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
