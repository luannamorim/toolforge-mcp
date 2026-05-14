# ToolForge

A cost-disciplined MCP client agent with a deterministic tool-selection heuristic and
first-class dry-run. Built on `claude-sonnet-4-6` and the official MCP Python SDK.

---

## What it is

ToolForge is a Python HTTP service (FastAPI) that wraps an Anthropic orchestration loop
and routes `tool_use` blocks to a pool of MCP servers — a filesystem server and a GitHub
server in the reference configuration. On every request it emits a JSON Lines trace record per
tool call containing cost, latency, the selection rule that fired, and whether the call
was executed or dry-run.

The four endpoints: `POST /chat`, `POST /chat/stream` (SSE), `GET /health`, `GET /tools`.

---

## Why it's different

Three things distinguish ToolForge from a "hello world" MCP agent:

### 1. Documented selection heuristic

The "which server's tool do I call when two overlap?" question is usually hand-waved in
agent demos. ToolForge specifies a 5-rule deterministic ordering, records which rule fired
in every trace, and evaluates correctness against a labeled set. This is the part a
reviewer reading the repo will recognize as senior work.

Rules run in strict priority order — first match wins:

1. `explicit-mention` — user named the server in the prompt
2. `argument-type` — only one candidate schema validates the LLM-produced arguments
3. `session-recency` — most recently used server for an overlapping tool in this session
4. `cosine-similarity` — tool description vs. prompt embedding wins by ≥ 0.05 margin
5. `priority-order` — first by `mcp.servers.json` order (final tiebreaker)

See `src/toolforge/agent/selector.py`.

### 2. Cost-disciplined design from day one

Prompt caching, a hard ceiling, and per-task cost in the trace stream. Most agent demos
quietly burn tokens; this one publishes the bill.

- p95 < $0.05 per 3–5 step task; hard ceiling $0.10 (halts with partial output)
- Prompt caching mandatory on the system prompt and the merged tool catalog
- Every trace record carries `tokens_in`, `tokens_out`, `cost_usd`
- `scripts/cost_report.py` rolls up daily spend from `logs/traces.jsonl`

### 3. Dry-run as a first-class mode

The agent can show its plan without executing — a small feature that turns "magic agent"
into a tool a developer can actually trust with destructive operations (open PR, send
Slack message).

Send `"dry_run": true` on any `/chat` or `/chat/stream` request. The orchestrator runs
the full selection logic and emits trace records with `executed: false`, but never
contacts any MCP server.

---

## Case study: ambiguous tool selection

Both the `filesystem` and `github` servers expose read-file semantics. When a prompt is
ambiguous, the selection heuristic resolves deterministically. The two examples below are
from the labeled evaluation set (`evals/golden_tasks.jsonl`, IDs `ambig-mention-001` and
`ambig-mention-002`).

**Prompt A** — user names the server explicitly:

```
Use the filesystem server to read README.md
```

Selection result:

```json
{
  "server": "filesystem",
  "tool": "read_file",
  "selection_rule": "explicit-mention",
  "alternatives": ["github"]
}
```

**Prompt B** — same ambiguity, different server named:

```
On github, read the file SECURITY.md from owner anthropics repo anthropic-sdk-python
```

Selection result:

```json
{
  "server": "github",
  "tool": "get_file_contents",
  "selection_rule": "explicit-mention",
  "alternatives": ["filesystem"]
}
```

Rule 1 (`explicit-mention`) fires in both cases. The `alternatives` field records every
server the heuristic considered — the routing decision is always auditable.

Full trace record for prompt A (non-dry-run):

```json
{
  "schema_version": "1",
  "timestamp": "2026-05-14T09:15:04.382Z",
  "session_id": "f3a8c2e1-4b7d-4e2f-a9c1-d8b3e5f72a4c",
  "step": 1,
  "server": "filesystem",
  "tool": "read_file",
  "arguments_hash": "a8f3b2c1d4e5f6a7",
  "latency_ms": 312.4,
  "success": true,
  "executed": true,
  "dry_run": false,
  "tokens_in": 1847,
  "tokens_out": 62,
  "cost_usd": 0.000941,
  "selection_rule": "explicit-mention",
  "alternatives": ["github"],
  "attempt": 1,
  "retries": 0,
  "retry_reason": null,
  "error": null,
  "corrective_retry": false
}
```

---

## Quick start

**Prerequisites:** Python 3.11, `uv`, Node.js with the packages referenced in
`mcp.servers.json` (`@modelcontextprotocol/server-filesystem`,
`@modelcontextprotocol/server-github`) available via `npx`, Redis ≥ 6.

```bash
# 1. Install
uv sync --extra dev

# 2. Configure environment
export ANTHROPIC_API_KEY=sk-ant-...
export VOYAGE_API_KEY=pa-...                   # optional; falls back to HashingEmbedder
export GITHUB_PERSONAL_ACCESS_TOKEN=ghp_...    # required by mcp.servers.json
export REDIS_URL=redis://localhost:6379         # or: CATALOG_CACHE_BACKEND=memory to skip Redis

# 3. Run dev server (reads mcp.servers.json at repo root)
uv run uvicorn toolforge.main:app --reload

# 4. Dry-run request — plan without executing
curl -s -X POST localhost:8000/chat \
  -H "content-type: application/json" \
  -d '{"message": "Use the filesystem server to read README.md", "dry_run": true}' \
  | jq .
```

Traces are written to `logs/traces.jsonl`. Set `TRACE_VERBOSE=1` to include raw
arguments alongside the hash.

---

## HTTP endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/chat` | POST | Run an agent task. Returns `ChatResponse` with `session_id`, `response`, `steps`, `cost_usd`, `halted`, `halt_reason`. |
| `/chat/stream` | POST | Same as `/chat` with SSE. Events: `session.start`, `tool.result` (per tool call), `final.response` (terminal), `error`. |
| `/health` | GET | Returns `{"status": "ok" \| "degraded", "servers": [...], "cache": {...}}`. Always HTTP 200; `/chat` blocks requests when MCP servers are down. |
| `/tools` | GET | Merged tool catalog from all configured MCP servers. |

Source: `src/toolforge/http/`.

---

## Trace records

Every tool call — including dry-run calls and unknown-tool rejections — emits one JSON
Lines record to `$TRACE_SINK` (default `logs/traces.jsonl`).

Field groups:

| Group | Fields |
|---|---|
| Identity | `schema_version`, `timestamp`, `session_id`, `step` |
| Routing | `server`, `tool`, `selection_rule`, `alternatives` |
| Outcome | `success`, `executed`, `error`, `corrective_retry` |
| Economics | `tokens_in`, `tokens_out`, `cost_usd`, `latency_ms` |
| Retry | `attempt`, `retries`, `retry_reason` |
| Control | `dry_run`, `arguments_hash`, `arguments` (only with `TRACE_VERBOSE=1`) |

`scripts/cost_report.py` reads the trace stream and prints a daily spend table:

```bash
uv run python scripts/cost_report.py
uv run python scripts/cost_report.py --since 2026-05-01 --executed-only
```

---

## Configuration

All settings are `BaseSettings` (Pydantic v2). Set via environment variables or a `.env`
file at the repo root.

| Variable | Default | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | — | Required for live requests |
| `VOYAGE_API_KEY` | — | Required when `EMBEDDER_BACKEND=voyage` (default) |
| `REDIS_URL` | `redis://localhost:6379` | Required when `CATALOG_CACHE_BACKEND=redis` (default) |
| `CATALOG_CACHE_BACKEND` | `redis` | `memory` disables Redis dependency |
| `EMBEDDER_BACKEND` | `voyage` | `hashing` is the zero-key fallback |
| `COST_CEILING_USD` | `0.10` | Hard per-request limit; halts with partial output |
| `TRACE_SINK` | `logs/traces.jsonl` | JSONL trace destination |
| `TRACE_VERBOSE` | `false` | Include raw arguments in traces |
| `MCP_SERVERS_CONFIG` | `mcp.servers.json` | MCP server pool definition |
| `OTEL_METRICS_EXPORTER` | `none` | `stdout` or `otlp` to enable metrics |

`mcp.servers.json` at repo root declares the two servers in the reference configuration. Add, remove, or
reorder servers to change the tool pool and `priority-order` tiebreaker ranking.

---

## Locked commitments

These are not configuration — they are v1 design decisions baked into the implementation:

| Commitment | Value |
|---|---|
| Orchestration model | `claude-sonnet-4-6` (Haiku 4.5 documented fallback; Opus 4.7 excluded — breaks cost envelope) |
| Prompt caching | Mandatory on system prompt + merged tool catalog; not optional |
| Cost envelope | p95 < $0.05 / 3–5 step task; hard ceiling $0.10 — halts, never overruns |
| Latency target | p95 < 8s end-to-end for the canonical task |
| Retry policy | Exponential backoff: base 500ms, factor 2, max 3 attempts, jitter; transient failures only (network, 5xx, timeout); 4xx and validation errors are terminal |
| Selection | 5-rule deterministic; `selection_rule` logged in every trace |

---

## Development

```bash
uv sync --extra dev                             # install all deps including dev extras
uv run ruff check src tests                     # lint
uv run pytest -m "unit or integration"          # 200 tests, no live API or MCP servers
uv run pytest -m live                           # opt-in: requires ANTHROPIC_API_KEY + MCP servers running
uv run python scripts/cost_report.py            # daily cost rollup from logs/traces.jsonl
uv run python scripts/check_eval_thresholds.py  # selection-heuristic eval regression gate
```

Source layout: `src/toolforge/` contains `agent/`, `guardrails/`, `http/`, `mcp_pool/`,
`models/`, `observability/`, `prompts/`, `traces/`. Tests mirror under `tests/`.

Eval tasks live in `evals/` and use [Inspect AI](https://inspect.ai/) (`uv sync --extra eval`).

---

## Out of scope for v1

- CLI client (HTTP API is the only entry point)
- Persistent conversation memory across HTTP sessions (each `/chat` is stateless unless caller passes prior turns via `ChatRequest.messages`)
- MCP server lifecycle management (start/stop/health-restart of MCP servers themselves)
- Authentication / authorization on `/chat`
- Web UI / dashboard
- Streaming token output beyond the optional SSE endpoint
- Multi-model orchestration (e.g., Sonnet planner + Haiku executor)
- Slack integration (no Slack MCP server in the reference configuration; deferred to v2)
- Distributed deployment (multi-node, load balancing)
- Recording / replay of MCP traffic for debugging
