from __future__ import annotations

import logging
import time
from typing import Any

import jsonschema
from anthropic import AsyncAnthropic

from toolforge.agent.selector import SelectionContext, select_server
from toolforge.config import Settings
from toolforge.mcp_pool.pool import MCPClientPool
from toolforge.models.catalog import ToolDescriptor
from toolforge.models.chat import ChatRequest, ChatResponse
from toolforge.models.trace import TraceRecord
from toolforge.prompts import load_system_prompt, load_tools_intro
from toolforge.traces.writer import TraceWriter, compute_cost, hash_arguments

logger = logging.getLogger(__name__)

MODEL = "claude-sonnet-4-6"
MAX_TURNS = 10


class Orchestrator:
    def __init__(
        self,
        pool: MCPClientPool,
        writer: TraceWriter,
        settings: Settings,
    ) -> None:
        self._pool = pool
        self._writer = writer
        self._settings = settings
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._system_text = load_system_prompt() + "\n\n" + load_tools_intro()

    async def run(
        self,
        request: ChatRequest,
        catalog: list[ToolDescriptor],
    ) -> ChatResponse:
        session_id = request.session_id
        step = 0
        total_cost = 0.0

        system_blocks = self._build_system()
        anthropic_tools = self._build_tools(catalog)
        messages: list[dict] = list(request.messages) + [
            {"role": "user", "content": request.message}
        ]
        sel_ctx = SelectionContext(prompt=request.message)

        for _ in range(MAX_TURNS):
            response = await self._client.messages.create(
                model=MODEL,
                max_tokens=4096,
                system=system_blocks,
                tools=anthropic_tools if anthropic_tools else [],
                messages=messages,
            )

            usage = response.usage
            tokens_in = usage.input_tokens
            tokens_out = usage.output_tokens
            cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
            cache_write = getattr(usage, "cache_creation_input_tokens", 0) or 0
            turn_cost = compute_cost(MODEL, tokens_in, tokens_out, cache_read, cache_write)
            total_cost += turn_cost

            if response.stop_reason in ("end_turn", "max_tokens"):
                text = next(
                    (b.text for b in response.content if b.type == "text"), ""
                )
                return ChatResponse(
                    session_id=session_id,
                    response=text,
                    steps=step,
                    cost_usd=round(total_cost, 8),
                )

            if response.stop_reason != "tool_use":
                logger.warning("Unexpected stop_reason: %s", response.stop_reason)
                text = next(
                    (b.text for b in response.content if b.type == "text"),
                    f"[unexpected stop: {response.stop_reason}]",
                )
                return ChatResponse(
                    session_id=session_id,
                    response=text,
                    steps=step,
                    cost_usd=round(total_cost, 8),
                )

            # Serialize assistant message back for context
            assistant_content = []
            for b in response.content:
                if b.type == "text":
                    assistant_content.append({"type": "text", "text": b.text})
                elif b.type == "tool_use":
                    assistant_content.append(
                        {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
                    )
            messages.append({"role": "assistant", "content": assistant_content})

            tool_use_blocks = [b for b in response.content if b.type == "tool_use"]
            cost_per_tool = turn_cost / max(len(tool_use_blocks), 1)
            tool_results = []

            for block in tool_use_blocks:
                step += 1
                tool_name = block.name
                tool_args = dict(block.input)

                candidates = catalog_candidates(catalog, tool_name)
                if not candidates:
                    tool_results.append(
                        _error_result(block.id, f"Unknown tool: {tool_name}")
                    )
                    continue

                try:
                    selected, rule = select_server(tool_name, candidates, sel_ctx)
                except Exception as exc:
                    tool_results.append(_error_result(block.id, str(exc)))
                    continue

                server_id = selected.server_id
                validation_err = _validate_args(selected, tool_args)

                t0 = time.monotonic()
                if validation_err:
                    latency = (time.monotonic() - t0) * 1000
                    self._writer.write(
                        TraceRecord(
                            session_id=session_id,
                            step=step,
                            server=server_id,
                            tool=tool_name,
                            arguments_hash=hash_arguments(tool_args),
                            latency_ms=latency,
                            success=False,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            cost_usd=cost_per_tool,
                            selection_rule=rule,
                            error=validation_err,
                            arguments=tool_args if self._settings.trace_verbose else None,
                        )
                    )
                    tool_results.append(
                        _error_result(block.id, f"Validation error: {validation_err}")
                    )
                    continue

                try:
                    mcp_result = await self._pool.call_tool(server_id, tool_name, tool_args)
                    latency = (time.monotonic() - t0) * 1000
                    is_error = bool(getattr(mcp_result, "isError", False))
                    content_text = _extract_content(mcp_result)

                    self._writer.write(
                        TraceRecord(
                            session_id=session_id,
                            step=step,
                            server=server_id,
                            tool=tool_name,
                            arguments_hash=hash_arguments(tool_args),
                            latency_ms=latency,
                            success=not is_error,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            cost_usd=cost_per_tool,
                            selection_rule=rule,
                            error=content_text if is_error else None,
                            arguments=tool_args if self._settings.trace_verbose else None,
                        )
                    )
                    if is_error:
                        tool_results.append(_error_result(block.id, content_text))
                    else:
                        sel_ctx.session_used_servers.append(server_id)
                        tool_results.append(
                            {"type": "tool_result", "tool_use_id": block.id, "content": content_text}
                        )
                except Exception as exc:
                    latency = (time.monotonic() - t0) * 1000
                    self._writer.write(
                        TraceRecord(
                            session_id=session_id,
                            step=step,
                            server=server_id,
                            tool=tool_name,
                            arguments_hash=hash_arguments(tool_args),
                            latency_ms=latency,
                            success=False,
                            tokens_in=tokens_in,
                            tokens_out=tokens_out,
                            cost_usd=cost_per_tool,
                            selection_rule=rule,
                            error=str(exc),
                            arguments=tool_args if self._settings.trace_verbose else None,
                        )
                    )
                    tool_results.append(_error_result(block.id, str(exc)))

            messages.append({"role": "user", "content": tool_results})

        return ChatResponse(
            session_id=session_id,
            response="[max turns reached]",
            steps=step,
            cost_usd=round(total_cost, 8),
        )

    def _build_system(self) -> list[dict]:
        return [{"type": "text", "text": self._system_text, "cache_control": {"type": "ephemeral"}}]

    def _build_tools(self, catalog: list[ToolDescriptor]) -> list[dict]:
        if not catalog:
            return []
        tools = []
        for i, tool in enumerate(catalog):
            entry: dict[str, Any] = {
                "name": tool.name,
                "description": tool.description,
                "input_schema": tool.input_schema,
            }
            if i == len(catalog) - 1:
                entry["cache_control"] = {"type": "ephemeral"}
            tools.append(entry)
        return tools


def catalog_candidates(catalog: list[ToolDescriptor], tool_name: str) -> list[ToolDescriptor]:
    return [t for t in catalog if t.name == tool_name]


def _validate_args(tool: ToolDescriptor, args: dict) -> str | None:
    try:
        jsonschema.validate(args, tool.input_schema)
        return None
    except jsonschema.ValidationError as exc:
        return exc.message
    except jsonschema.SchemaError as exc:
        return f"Schema error: {exc.message}"


def _extract_content(result: Any) -> str:
    if hasattr(result, "content"):
        parts = []
        for block in result.content:
            if hasattr(block, "text"):
                parts.append(block.text)
            else:
                parts.append(str(block))
        return "\n".join(parts)
    return str(result)


def _error_result(tool_use_id: str, message: str) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": message,
        "is_error": True,
    }
