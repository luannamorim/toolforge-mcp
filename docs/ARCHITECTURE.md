# Architecture

Companion to `SPEC.md`. SPEC defines *what* ToolForge commits to; this document
explains *where in the source tree* each commitment lives, so a new engineer can
read it alongside the code and immediately know where to land.

## One-paragraph orientation

Single Python process, three layers. The **HTTP layer** (FastAPI, stateless)
accepts requests on `/chat`, `/chat/stream`, `/health`, `/tools`. The **agent
core** runs the Anthropic SDK orchestration loop, applies the deterministic
tool-selection heuristic, enforces the retry and cost-ceiling policies, emits
traces, and supports dry-run. The **MCP client pool** holds one persistent
stdio connection per configured server, exposes the merged tool catalog (cached
in Redis, keyed by `mcp:catalog:<server_id>`), and routes returned `tool_use`
blocks back to the originating server. Cross-cutting concerns — guardrails,
observability, prompts, config — sit alongside the three layers and are imported
inward; nothing imports outward toward HTTP.

## Module map

```
src/toolforge/
├── app.py                  FastAPI factory; wires middleware, lifespan, routes
├── main.py                 ASGI entrypoint (uvicorn target)
├── config.py               Pydantic Settings — env vars + mcp.servers.json loader
│
├── http/                   HTTP layer
│   ├── chat.py             POST /chat, POST /chat/stream (SSE)
│   ├── health.py           GET /health  (Redis + MCP pool reachability)
│   ├── tools.py            GET /tools   (merged catalog, public shape)
│   └── _errors.py          429 RateLimitError handler, error envelopes
│
├── agent/                  Agent core
│   ├── orchestrator.py     Anthropic loop, cost tracking, retry, dry-run, emit
│   ├── selector.py         5-rule deterministic tool-server selection
│   └── embedder.py         Voyage REST + HashingEmbedder fallback (rule 4)
│
├── mcp_pool/               MCP client pool
│   ├── pool.py             One stdio ClientSession per server, parallel connect
│   ├── catalog_builder.py  Discovery + embedding + cache fill
│   └── catalog_cache.py    RedisCatalogCache + InMemoryCatalogCache
│
├── guardrails/             Edge enforcement
│   ├── payload.py          PayloadSizeMiddleware (Content-Length cap)
│   ├── credentials.py      Reject GitHub PAT / Slack token / AWS key in prompts
│   └── off_domain.py       Positive-allowlist classifier with negative patterns
│
├── observability/
│   └── metrics.py          OpenTelemetry counters + histograms
│
├── traces/
│   └── writer.py           TraceWriter (JSONL append) + Anthropic pricing table
│
├── prompts/
│   ├── __init__.py         Loaders for the three .md fragments
│   ├── system.md           System prompt
│   ├── tools_intro.md      Catalog preamble (concatenated into system block)
│   └── examples.md         Few-shot examples (concatenated into system block)
│
└── models/                 Pydantic v2 contracts
    ├── chat.py             ChatRequest, ChatResponse
    ├── catalog.py          MCPServerConfig, ToolDescriptor, ToolCatalog, ToolSummary
    └── trace.py            TraceRecord (schema version "1")
```

## HTTP layer

Four endpoints, all in `src/toolforge/http/`:

| Endpoint           | File          | Notes                                                  |
|--------------------|---------------|--------------------------------------------------------|
| `POST /chat`       | `chat.py`     | Non-streaming. Returns `ChatResponse`.                 |
| `POST /chat/stream`| `chat.py`     | SSE. Events: `session.start`, `tool.result`, `halt`, `error`, `final.response`. |
| `GET /health`      | `health.py`   | 200 if Redis pingable + all MCP servers connected; otherwise 503. |
| `GET /tools`       | `tools.py`    | Merged catalog as `ToolSummary[]` (no embeddings).     |

**Degraded gate.** Both `/chat` endpoints call `_guard_degraded()` before
running the orchestrator. If `pool.down_servers` is non-empty, return 503 —
*unless* `dry_run=True`, which short-circuits the gate (dry-run never contacts
MCP, so a down server does not block it).

**Streaming.** `/chat/stream` constructs an asyncio queue and passes an
`event_sink` callback into the orchestrator. The orchestrator publishes events
as it makes progress; the endpoint serializes each event as an SSE frame.

**Rate-limit.** `_errors.py` registers an exception handler for the Anthropic
SDK's `RateLimitError`. The handler reads `retry-after`, returns a 429 with the
delay, and emits a metric. The streaming path catches the same error inside the
generator and emits an `error` SSE event.

**Payload cap.** `PayloadSizeMiddleware` (registered in `app.py`) reads
`Content-Length` *before* the body is parsed and rejects oversized requests
without buffering. Default 32 KB; controlled by `MAX_REQUEST_BYTES`.

## Agent core

`Orchestrator` in `agent/orchestrator.py` owns the run loop. The shape of one
turn:

1. Build the request to `messages.create` — system prompt + tool catalog, both
   marked `cache_control: {"type": "ephemeral"}` (this is the **mandatory
   prompt caching** per SPEC).
2. Call `claude-sonnet-4-6`.
3. For each `tool_use` block returned: validate args against the candidate
   schema, then call `selector.select_server()`.
4. If `dry_run`, emit a synthetic tool result and a trace with
   `executed=False`; do **not** touch the MCP pool.
5. Otherwise, dispatch via `pool.call_tool()` with retry policy applied.
6. Update `session_used_servers` (feeds selection rule 3 on subsequent turns).
7. Update `total_cost` from `response.usage`; if `total_cost >
   cost_ceiling_usd`, halt with `halt_reason="cost_ceiling"` and return what is
   buffered so far (this is the SPEC OQ#5 "halt with partial" behavior).

**Retry policy.** Only transient transport failures retry — `TimeoutError`,
`ConnectionError`, `OSError`, `BrokenPipeError`. Schema-validation failures and
remote 4xx are terminal. Backoff is `base * factor^attempt` plus optional
jitter, capped at `retry_max_attempts` (3 default). The first validation
failure of a given tool gets a *corrective-retry hint* — the corrected schema is
sent back to the model as an error message — but only once per tool per
session.

**Cost accounting.** `traces/writer.py` carries the Sonnet 4.6 pricing table
(input $3/M, output $15/M, cache read $0.30/M, cache write $3.75/M). Every
response's `usage` block is mapped to a USD cost and accumulated on the
orchestrator; the final figure ships in both `ChatResponse.cost_usd` and the
per-call `TraceRecord.cost_usd`.

### Tool-selection heuristic (the differentiator)

`agent/selector.py` exposes `select_server(tool_name, tool_input, candidates,
ctx) -> (chosen, rule_name, alternatives)`. The five rules run in strict
priority order, first match wins. The `rule_name` returned is the value that
lands in `TraceRecord.selection_rule` — every selection is auditable.

| Order | Rule name           | Fires when                                                                                          |
|-------|---------------------|-----------------------------------------------------------------------------------------------------|
| 0     | `single-candidate`  | Only one server exposes the tool. (Fast path; not one of the five SPEC rules.)                      |
| 1     | `explicit-mention`  | User named exactly one candidate server in the prompt (case-insensitive token match).               |
| 2     | `argument-type`     | Exactly one candidate's JSON Schema validates the LLM-produced `tool_input`.                        |
| 3     | `session-recency`   | Walk `ctx.session_used_servers` LIFO; first candidate seen wins.                                    |
| 4     | `cosine-similarity` | Embed prompt + tool descriptions; best dot product wins **if margin ≥ 0.05** over the runner-up.    |
| 5     | `priority-order`    | Final tiebreaker — order in `mcp.servers.json` decides.                                             |

Alternatives that lost are recorded in `TraceRecord.alternatives` so an audit
can show *why* the rule fired.

## MCP client pool

`mcp_pool/pool.py` holds one `ClientSession` (from the official MCP Python SDK)
per configured server. `connect_all()` opens them in parallel via
`stdio_client()`. A connection failure is tolerated — the failing server's ID
goes into `down_servers`, which feeds the `/health` 503 gate.

`mcp_pool/catalog_builder.py` discovers each server's tools, embeds their
descriptions (for selection rule 4), and persists the result in the catalog
cache. The cache key is composite — `<server_id>:<embedder_id>` — so swapping
the embedder doesn't return a stale catalog with wrong-dimensional vectors.

`mcp_pool/catalog_cache.py` has two implementations behind one interface:

- `RedisCatalogCache` — production. Keys live under `mcp:catalog:<server_id>`
  (prefix configurable), TTL ≥ 5 min enforced server-side via `SETEX`.
- `InMemoryCatalogCache` — fallback when Redis isn't configured; monotonic-time
  expiry.

Chosen via `CATALOG_CACHE_BACKEND`.

## Guardrails

All three guardrails sit at the HTTP edge — the agent core trusts its inputs.

- **Payload cap** — middleware, rejects on `Content-Length` before body is read.
- **Credential rejection** — regex scan for GitHub PAT (`ghp_…` family), Slack
  tokens (`xox[bparso]-…`), AWS access keys (`AKIA…`). Returns the *pattern
  name*, never the matched bytes.
- **Off-domain classifier** — positive-allowlist first (file, repo, code, PR…)
  so an operational prompt that incidentally mentions a blocked word still
  passes; otherwise negative patterns (poem, joke, weather, recipe…) trigger a
  fixed 400.

The one in-loop guardrail is the **cost ceiling**, enforced inside the
orchestrator (see *Agent core* above).

## Observability

`traces/writer.py` is the canonical sink. Every tool call — successful,
validation-failed, transport-errored, or dry-run — produces exactly one
`TraceRecord` (JSONL, default path `logs/traces.jsonl`). The schema is locked
at version `"1"`.

Fields most often needed during debugging: `timestamp`, `session_id`, `step`,
`server`, `tool`, `latency_ms`, `success`, `cost_usd`, `selection_rule`,
`alternatives`, `executed`, `dry_run`, `attempt`/`retries`/`retry_reason`,
`error`. `arguments_hash` is always present; the raw `arguments` body is
included only when `TRACE_VERBOSE=1`.

`observability/metrics.py` exports OpenTelemetry metrics independently of the
JSONL trace stream:

- Histogram `toolforge.task.latency_ms`
- Histogram `toolforge.task.cost_usd` (tagged `halted`, `halt_reason`)
- Counter `toolforge.tool.errors_total` (tagged `server`, `tool`, `reason`)
- Counter `toolforge.selection.heuristic_rule_fired` (tagged `rule`, `server`)

Exporter chosen via `OTEL_METRICS_EXPORTER` (`stdout` | `otlp` | `none`).

`scripts/cost_report.py` rolls up daily spend from the JSONL stream — a
lightweight close on the SPEC `SHOULD` for cost visibility without standing up
an OTel collector for local development.

## Configuration

`config.py` is a single Pydantic `BaseSettings`. The env vars that matter:

| Variable                  | Default                 | Purpose                                |
|---------------------------|-------------------------|----------------------------------------|
| `ANTHROPIC_API_KEY`       | —                       | Required for non-dry-run.              |
| `MCP_SERVERS_CONFIG`      | `mcp.servers.json`      | Path to the server catalog.            |
| `REDIS_URL`               | `redis://localhost:6379`| Catalog cache backend connection.      |
| `CATALOG_CACHE_BACKEND`   | `redis`                 | `redis` or `memory`.                   |
| `COST_CEILING_USD`        | `0.10`                  | Hard halt threshold (SPEC).            |
| `MAX_REQUEST_BYTES`       | `32768`                 | Payload guardrail.                     |
| `TRACE_SINK`              | `logs/traces.jsonl`     | JSONL output path.                     |
| `TRACE_VERBOSE`           | `0`                     | `1` includes raw `arguments` field.    |
| `RETRY_MAX_ATTEMPTS`      | `3`                     | Transient-failure retry cap.           |
| `RETRY_BASE_DELAY_MS`     | `500`                   | Exponential backoff base.              |
| `RETRY_BACKOFF_FACTOR`    | `2.0`                   |                                        |
| `RETRY_JITTER`            | `true`                  |                                        |
| `EMBEDDER_BACKEND`        | `voyage` if key present | `voyage` or `hashing` (zero-key).      |
| `VOYAGE_API_KEY`          | —                       | Required only when backend is `voyage`.|
| `OTEL_METRICS_EXPORTER`   | `none`                  | `stdout` \| `otlp` \| `none`.          |

`mcp.servers.json` is a flat array of `{id, command, args, env}`. Env values
support `${VAR}` expansion so the GitHub PAT can sit in the parent process
environment instead of the JSON file.

## Dependency direction

```
        ┌────────────────────────────┐
        │           http/            │
        │   (FastAPI routes, edge)   │
        └──────────────┬─────────────┘
                       │ imports
                       ▼
        ┌────────────────────────────┐
        │           agent/           │
        │  (Anthropic loop, selector)│
        └──┬───────────┬─────────────┘
           │           │
           ▼           ▼
   ┌────────────┐  ┌────────────────┐
   │  mcp_pool/ │  │ traces/, obs/, │
   │            │  │  prompts/      │
   └────────────┘  └────────────────┘
           │           │
           ▼           ▼
        ┌────────────────────────────┐
        │          models/           │
        │   (Pydantic contracts)     │
        └────────────────────────────┘
```

`guardrails/` and `config.py` are leaf modules — they import only stdlib and
`models/`, and are imported by `http/` and `agent/` respectively. Nothing
imports outward toward `http/`; this is the boundary that keeps the orchestrator
testable without a running ASGI app.

## Key entry points

For dropping a debugger:

- **App startup** — `app.py::create_app` → lifespan initializes the pool, the
  catalog cache, the embedder, and the orchestrator.
- **Chat request** — `http/chat.py::chat` → guardrails → degraded gate →
  catalog build → `orchestrator.run`.
- **Tool dispatch** — `agent/orchestrator.py::_dispatch_one_block` → schema
  validate → `selector.select_server` → `pool.call_tool` with retry wrapper.
- **Trace emit** — `agent/orchestrator.py::_emit` → `traces/writer.py::write`.
- **Selection audit** — search `logs/traces.jsonl` for the
  `selection_rule` and `alternatives` fields on any record.
