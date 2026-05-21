# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project status

This repository is **feature-complete for v1**. The source tree lives under
`src/toolforge/`. Treat `SPEC.md` as the source of truth for behavior. Verify
that something does not already exist in `src/toolforge/` before scaffolding it.

## Read `SPEC.md` before non-trivial work

`SPEC.md` locks the following commitments that are easy to drift away from once code starts landing. Preserve them unless the user explicitly relaxes them:

- **Model:** `claude-sonnet-4-6` for the main orchestration loop, with **Anthropic prompt caching mandatory** on the system prompt + merged tool catalog. Haiku 4.5 is the documented fallback; Opus 4.7 is explicitly excluded because it breaks the cost envelope.
- **Cost envelope:** p95 < $0.05 per 3–5 step task; hard ceiling $0.10 (request rejected past ceiling). Per-task cost lives in every trace record.
- **Latency:** p95 < 8s end-to-end for the canonical task.
- **Tool selection is deterministic, not LLM-judged.** When ≥2 MCP servers expose overlapping tools, selection follows a 5-rule ordering: (1) explicit user mention → (2) argument-type match → (3) recent successful session use → (4) cosine similarity of tool descriptions to prompt → (5) `mcp.servers.json` priority order. The rule that fired is logged in every trace.
- **Observability:** one JSON Lines record per tool call, schema versioned, default sink `logs/traces.jsonl`. Fields include `timestamp`, `session_id`, `step`, `server`, `tool`, `arguments_hash`, `latency_ms`, `success`, `tokens_in`, `tokens_out`, `cost_usd`. Raw arguments are hashed by default; full payloads only when `TRACE_VERBOSE=1`.
- **Dry-run mode** (`dry_run: true` on `/chat`) is a first-class feature, not an afterthought — it must produce the full execution plan without contacting any MCP server, and trace records still emit with `executed: false`.
- **Retry policy:** exponential backoff (base 500ms, factor 2, max 3, jitter), only for transient failures (network, 5xx, timeout). 4xx and validation errors are terminal.

## Locked stack

Do not substitute these without checking — they are intentional commitments in `SPEC.md`, not defaults.

- Python 3.11
- Anthropic SDK
- Official MCP Python SDK (client side only — this project does **not** ship an MCP server as a deliverable)
- FastAPI (HTTP interface: `/chat`, `/chat/stream`, `/health`, `/tools`)
- Pydantic v2 for all internal contracts and MCP tool-argument validation
- Redis ≥ 6 for the MCP tool-catalog cache (key: `mcp:catalog:<server_id>`, TTL ≥ 5min). Redis is **not** used for conversation memory in v1.
- OpenTelemetry SDK for metric export

## Architecture (one-paragraph orientation)

Single Python process, three layers. The **HTTP layer** (FastAPI, stateless) accepts `/chat` requests. The **agent core** runs the Anthropic SDK orchestration loop, applies the selection heuristic, enforces the retry policy, emits traces, and supports dry-run. The **MCP client pool** holds one connection per configured server, exposes the merged tool catalog (cached in Redis), and routes returned `tool_use` blocks back to the originating server. See `docs/ARCHITECTURE.md` for the full module map, dependency direction, and entry points.

## Out of scope for v1 (don't volunteer these)

CLI client, web UI, multi-tenant auth, persistent conversation memory across `/chat` calls, MCP-server lifecycle management, multi-model orchestration, distributed deployment. If a request implies one of these, flag it as out-of-scope per `SPEC.md` rather than implementing.

## Available subagents

Three project subagents live under `.claude/agents/`. Main Claude should delegate proactively:

- **`code-reviewer`** — read-only review of an uncommitted or branch-scoped diff against project conventions. Use before commits or via `/review`.
- **`spec-guardian`** — verifies a diff against the commitments listed above + the out-of-scope guardrail. Use before merging changes to orchestration, selection, retry, traces, or model choice, or via `/spec-check`.
- **`test-writer`** — writes `pytest` tests to `tests/` mirroring `src/` layout. Use after substantial code changes or when coverage gaps are identified. Does not modify `src/`.

Slash commands `/review` and `/spec-check` are thin entrypoints that detect diff scope and delegate to `code-reviewer` and `spec-guardian` respectively.

## Available skills

- **`/mcp-builder`** — use this to scaffold any **supporting MCP servers** if v2 work explicitly requires a new MCP server not already present in `mcp.servers.json`. Do NOT use it for the ToolForge agent itself — ToolForge is an MCP client, not a server (see `SPEC.md § Non-Goals`).

## Build / lint / test

```bash
uv sync --extra dev          # install deps (first time or after pyproject.toml changes)
uv run ruff check src tests  # lint
uv run pytest -m "unit or integration"  # fast test suite (no live API/MCP)
uv run pytest -m live        # opt-in: requires ANTHROPIC_API_KEY + MCP servers running
uv run uvicorn toolforge.main:app --reload  # dev server (requires MCP servers + API key)
```

Trace output lands in `logs/traces.jsonl`. Set `TRACE_VERBOSE=1` to include raw arguments.
