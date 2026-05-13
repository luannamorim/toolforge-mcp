from __future__ import annotations

import asyncio
import logging
import random
import time
from collections.abc import Awaitable, Callable
from typing import Any

import jsonschema
from anthropic import AsyncAnthropic

from toolforge.agent.embedder import Embedder, HashingEmbedder
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
        embedder: Embedder | None = None,
    ) -> None:
        self._pool = pool
        self._writer = writer
        self._settings = settings
        self._embedder: Embedder = embedder if embedder is not None else HashingEmbedder()
        self._priority_order = [s.id for s in settings.mcp_servers]
        self._client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        self._system_text = load_system_prompt() + "\n\n" + load_tools_intro()

    @property
    def embedder(self) -> Embedder:
        return self._embedder

    async def run(
        self,
        request: ChatRequest,
        catalog: list[ToolDescriptor],
        event_sink: Callable[[dict], Awaitable[None]] | None = None,
    ) -> ChatResponse:
        session_id = request.session_id
        step = 0
        total_cost = 0.0

        system_blocks = self._build_system()
        anthropic_tools = self._build_tools(catalog)
        messages: list[dict] = list(request.messages) + [
            {"role": "user", "content": request.message}
        ]
        # prompt scoped to current message only; OQ#1 covers multi-turn tracking
        sel_ctx = SelectionContext(
            prompt=request.message,
            priority_order=self._priority_order,
            prompt_embedding=self._embedder.embed(request.message),
        )

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
                    dry_run=request.dry_run,
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
                    dry_run=request.dry_run,
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
            # Evenly split the turn's cost across tool blocks; per-tool values
            # undercount when blocks share a prefill hit, but sum correctly.
            cost_per_tool = turn_cost / max(len(tool_use_blocks), 1)
            tool_results = []

            # Pre-assign a unique step to each block before parallel dispatch
            # so trace records carry deterministic step values regardless of
            # completion order (the scorer sorts by step, not write order).
            block_steps = range(step + 1, step + 1 + len(tool_use_blocks))
            step += len(tool_use_blocks)

            block_outputs = await asyncio.gather(*[
                self._dispatch_one_block(
                    block, s, session_id, sel_ctx, catalog,
                    tokens_in, tokens_out, cost_per_tool, request.dry_run,
                    event_sink,
                )
                for block, s in zip(tool_use_blocks, block_steps)
            ])

            # Append results and deferred recency updates in input order.
            # Within-turn visibility of rule-3 is intentionally absent here
            # (all selections ran before any block completed); the appends
            # become visible starting from the next turn — SPEC FR5 / failure
            # mode #5 accept this "parallel-anyway" trade-off.
            for tool_result, used_server in block_outputs:
                tool_results.append(tool_result)
                if used_server is not None:
                    sel_ctx.session_used_servers.append(used_server)

            messages.append({"role": "user", "content": tool_results})

        return ChatResponse(
            session_id=session_id,
            response="[max turns reached]",
            steps=step,
            cost_usd=round(total_cost, 8),
            dry_run=request.dry_run,
        )

    async def _dispatch_one_block(
        self,
        block: Any,
        step: int,
        session_id: str,
        sel_ctx: SelectionContext,
        catalog: list[ToolDescriptor],
        tokens_in: int,
        tokens_out: int,
        cost_per_tool: float,
        dry_run: bool,
        event_sink: Callable[[dict], Awaitable[None]] | None = None,
    ) -> tuple[dict, str | None]:
        """Validate, select, and execute one tool_use block.

        Returns (tool_result_dict, used_server_id) where used_server_id is
        None on any failure or dry-run path — only set on live success.
        """
        tool_name = block.name
        tool_args = dict(block.input)
        args_hash = hash_arguments(tool_args)

        candidates = catalog_candidates(catalog, tool_name)
        if not candidates:
            return _error_result(block.id, f"Unknown tool: {tool_name}"), None

        selected, rule, alternatives = select_server(
            tool_name, candidates, sel_ctx, tool_input=tool_args
        )
        server_id = selected.server_id
        validation_err = _validate_args(selected, tool_args)

        t0 = time.monotonic()
        if validation_err:
            latency = (time.monotonic() - t0) * 1000
            await self._emit(
                TraceRecord(
                    session_id=session_id,
                    step=step,
                    server=server_id,
                    tool=tool_name,
                    arguments_hash=args_hash,
                    latency_ms=latency,
                    success=False,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_per_tool,
                    selection_rule=rule,
                    executed=False,
                    dry_run=dry_run,
                    alternatives=alternatives or None,
                    error=validation_err,
                    arguments=tool_args if self._settings.trace_verbose else None,
                ),
                event_sink,
            )
            return _error_result(block.id, f"Validation error: {validation_err}"), None

        if dry_run:
            latency = (time.monotonic() - t0) * 1000
            await self._emit(
                TraceRecord(
                    session_id=session_id,
                    step=step,
                    server=server_id,
                    tool=tool_name,
                    arguments_hash=args_hash,
                    latency_ms=latency,
                    success=True,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_per_tool,
                    selection_rule=rule,
                    executed=False,
                    dry_run=True,
                    alternatives=alternatives or None,
                    arguments=tool_args if self._settings.trace_verbose else None,
                ),
                event_sink,
            )
            synthetic = (
                f"[DRY RUN: {tool_name} on {server_id}"
                " would be invoked with the given arguments]"
            )
            # Dry-run does NOT update session history — rule 3 must reflect
            # only real successful executions so the plan mirrors actual runs.
            return {"type": "tool_result", "tool_use_id": block.id, "content": synthetic}, None

        max_attempts = max(1, self._settings.retry_max_attempts)
        base_delay = self._settings.retry_base_delay_ms / 1000.0
        factor = self._settings.retry_backoff_factor
        use_jitter = self._settings.retry_jitter

        mcp_result = None
        last_exc: Exception | None = None
        retry_reason: str | None = None

        for attempt_num in range(1, max_attempts + 1):
            try:
                mcp_result = await self._pool.call_tool(server_id, tool_name, tool_args)
                last_exc = None
                break
            except Exception as exc:
                if _is_transient(exc) and attempt_num < max_attempts:
                    retry_reason = type(exc).__name__
                    last_exc = exc
                    delay = base_delay * (factor ** (attempt_num - 1))
                    if use_jitter:
                        delay *= random.uniform(0.5, 1.0)
                    await asyncio.sleep(delay)
                else:
                    last_exc = exc
                    break

        latency = (time.monotonic() - t0) * 1000
        retries = attempt_num - 1

        if last_exc is not None:
            await self._emit(
                TraceRecord(
                    session_id=session_id,
                    step=step,
                    server=server_id,
                    tool=tool_name,
                    arguments_hash=args_hash,
                    latency_ms=latency,
                    success=False,
                    tokens_in=tokens_in,
                    tokens_out=tokens_out,
                    cost_usd=cost_per_tool,
                    selection_rule=rule,
                    attempt=attempt_num,
                    retries=retries,
                    retry_reason=retry_reason,
                    alternatives=alternatives or None,
                    error=str(last_exc),
                    arguments=tool_args if self._settings.trace_verbose else None,
                ),
                event_sink,
            )
            return _error_result(block.id, str(last_exc)), None

        is_error = bool(getattr(mcp_result, "isError", False))
        content_text = _extract_content(mcp_result)

        await self._emit(
            TraceRecord(
                session_id=session_id,
                step=step,
                server=server_id,
                tool=tool_name,
                arguments_hash=args_hash,
                latency_ms=latency,
                success=not is_error,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                cost_usd=cost_per_tool,
                selection_rule=rule,
                attempt=attempt_num,
                retries=retries,
                retry_reason=retry_reason,
                alternatives=alternatives or None,
                error=content_text if is_error else None,
                arguments=tool_args if self._settings.trace_verbose else None,
            ),
            event_sink,
        )
        if is_error:
            return _error_result(block.id, content_text), None
        return {"type": "tool_result", "tool_use_id": block.id, "content": content_text}, server_id

    async def _emit(
        self,
        record: TraceRecord,
        event_sink: Callable[[dict], Awaitable[None]] | None,
    ) -> None:
        data = self._writer.write(record)
        if event_sink is not None:
            await event_sink({"event": "tool.result", "data": data})

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


def _is_transient(exc: BaseException) -> bool:
    """SPEC FR7: network/timeout/transport errors are retryable; everything else is terminal."""
    return isinstance(exc, (asyncio.TimeoutError, TimeoutError, ConnectionError, BrokenPipeError, OSError))


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
