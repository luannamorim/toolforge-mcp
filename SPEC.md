# ToolForge

> Conversational agent that orchestrates multiple MCP servers in parallel to automate developer productivity workflows.

## Problem & Why It Matters

Developers spend material time gluing together a small set of recurring actions: read a local file, propose a change, open a PR on GitHub, ping the team on Slack with the link. Each step lives in a different tool, each tool has its own UI and friction, and the orchestration between them — the part that takes ~80% of the wall-clock time — is unautomated. The result is context-switching tax that scales with team size.

The Model Context Protocol (MCP), released by Anthropic in late 2024 and adopted broadly through 2025, standardizes how LLM clients connect to external tools. Most public attention has gone to building MCP *servers*. The *consumer* side — an agent that talks to multiple MCP servers, picks the right tool when several overlap, and runs them in parallel where independent — is far less well-explored as a portfolio artifact. ToolForge fills that gap with a vertical workflow (developer productivity) instead of a generic assistant: it shows that the interesting engineering is in selection, orchestration, and observability, not in re-implementing every tool from scratch.

## Goals

- Demonstrate MCP **consumption** as a first-class pattern: connect to ≥3 MCP servers (own + third-party), discover tools dynamically, route LLM tool calls to the right server
- Complete a representative multi-tool developer workflow (read file → edit → open PR → notify Slack) end-to-end in a single conversational turn
- Make tool selection a **documented, deterministic heuristic** when multiple servers expose similar tools — not a black box
- Produce per-call structured traces (timestamp, latency, token cost, success/error) consumable by humans and downstream observability stacks
- Stay inside a tight cost and latency envelope (p95 < 8s, cost < $0.05) on the canonical 3–5 step task

## Non-Goals

- Not a generic chat assistant — refuses or no-ops on workflows outside the developer-productivity vertical
- Not an MCP server — ToolForge consumes MCP, it does not expose its own tools over MCP
- No web UI in v1 — HTTP API only, callable from any HTTP client (curl, Postman, future CLI)
- No multi-tenant auth, no per-user data isolation — single-user, local-or-trusted-network deployment
- No model-agnostic abstraction layer — Anthropic SDK only; portability is not a goal
- Not a replacement for full agent frameworks (LangGraph, CrewAI) — this is a focused vertical, not a framework

## Success Criteria

| Criterion           | Measurement                                                        | Target              |
| ------------------- | ------------------------------------------------------------------ | ------------------- |
| Workflow completion | Canonical "edit file → open PR → notify Slack" task succeeds       | ≥ 95% over 20 runs  |
| End-to-end latency  | p95 wall-clock for the canonical 3–5 step task                     | < 8s                |
| Cost per task       | p95 USD cost (input + output tokens, including cached reads)       | < $0.05             |
| Tool selection      | Correct server picked when 2+ servers expose a similar tool        | ≥ 90% on labeled set (n=30) |
| Trace completeness  | Every tool call emits a full JSON Lines record (no missing fields)  | 100% (enforced)     |
| Dry-run fidelity    | Dry-run plan matches actual execution sequence                     | 100% on test set    |

## Users & Use Cases

**Primary user:** Mid-to-senior backend developer who already uses Claude or another LLM client, is comfortable running local services, and wants to automate the "boring glue" between their editor, GitHub, and Slack.

**Top use cases:**

1. *"Update the changelog in `CHANGELOG.md`, open a PR titled 'docs: changelog Q1', and post the PR link in `#engineering`."* — one prompt, four tool calls across three servers, parallel where independent.
2. *"What's the diff between `main` and my current branch, and which files are in the PR you just opened?"* — agent reuses prior conversation context, picks `filesystem` over `github` for local diff.
3. *"Dry-run: same as last time but for the `release/2026-Q2` branch."* — agent returns the execution plan without side effects; user confirms, then re-runs without `dry-run`.

## Functional Requirements

The system MUST:

1. Connect to ≥2 MCP servers at startup via config (`mcp.servers.json`), one of which is filesystem and one of which is third-party (GitHub or Slack)
2. Discover available tools from each server on connect, cache the catalog in Redis with TTL ≥ 5min, refresh on cache miss or explicit invalidation
3. Accept user prompts via `POST /chat` (FastAPI), return responses as JSON; streaming responses via `POST /chat/stream` (SSE) optional
4. Pass the merged tool catalog to the LLM in a single `tools` parameter, route returned `tool_use` blocks to the originating server
5. Execute independent tool calls in parallel within a single LLM turn (when the model returns multiple `tool_use` blocks)
6. Apply the **tool selection heuristic** (below) when ≥2 servers expose tools whose names or descriptions overlap above a similarity threshold
7. Retry transient tool failures with exponential backoff (base 500ms, factor 2, max 3 attempts, jitter), distinguishing transient (network, 5xx, timeout) from terminal (4xx, validation) errors
8. Emit one JSON Lines record per tool call to a configurable sink (default: `logs/traces.jsonl`) with: `timestamp`, `session_id`, `step`, `server`, `tool`, `arguments_hash`, `latency_ms`, `success`, `tokens_in`, `tokens_out`, `cost_usd`, `error?`
9. Support `dry_run: true` request flag — the agent produces the full tool-call plan as it would execute, but skips actual server invocation; trace records are emitted with `dry_run: true` and `executed: false`
10. Validate every tool argument against the server-provided Pydantic-derived schema before invocation; reject malformed calls without contacting the server

The system SHOULD:

11. Surface a `/health` endpoint reporting per-server connectivity status
12. Cache prompt prefixes (system prompt + tool catalog) using Anthropic prompt caching to keep cost inside envelope

The system MAY:

13. Expose `/tools` for human inspection of the merged catalog
14. Support hot-reload of `mcp.servers.json` without restart

### Tool selection heuristic (when ≥2 servers expose overlapping tools)

Selection runs deterministically in this priority order; first matching rule wins:

1. **Explicit user mention** — user names the server ("on GitHub", "in the filesystem"): pick that server
2. **Argument-type match** — only one candidate server's tool schema accepts the argument types the LLM is producing
3. **Recent successful use** — within the current session, the server most recently used for a similar tool wins (tie-broken by recency)
4. **Cosine similarity of tool descriptions** to the user prompt embedding, computed at catalog-load time and cached
5. **Server priority order** declared in `mcp.servers.json` (final tie-breaker, never silent)

The selected server, the rule that fired, and the alternatives considered are recorded in the trace.

## Non-Functional Requirements

- **Latency:** see Success Criteria. Network calls to MCP servers and Anthropic dominate; orchestration overhead target < 200ms per LLM turn.
- **Cost envelope:** p95 < $0.05/task; hard ceiling $0.10 (request rejected past ceiling). Prompt caching mandatory for system prompt + tool catalog.
- **Availability:** best-effort. No uptime SLA. Crash-only design: state is per-request or in Redis, no in-process critical state.
- **Security:** secrets (GitHub PAT, Slack token) loaded from environment, never logged. Trace records contain `arguments_hash` not raw arguments by default; raw arguments behind `TRACE_VERBOSE=1` flag.
- **Concurrency:** FastAPI workers handle ≥5 concurrent sessions on a 4-core dev machine without latency p95 degrading more than 50%.

## AI/LLM Design Decisions

### Model selection

| Component                                      | Model             | Rationale                                                                         | Fallback                                                    |
| ---------------------------------------------- | ----------------- | --------------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Main orchestration loop                        | claude-sonnet-4-6 | Strong tool-use + reasoning at moderate cost; fits envelope when system prompt is cached | claude-haiku-4-5 for high-volume or cost-sensitive deployments |
| Catalog embedding (selection heuristic step 4) | `voyage-3-lite` or local `bge-m3` | Cheap, one-shot at catalog-load, no recurring cost              | Skip step 4; fall through to step 5                         |

Sonnet 4.6 over Opus 4.7: Opus' marginal capability gain on tool-routing tasks does not justify the cost-envelope risk for v1. Sonnet over Haiku: Haiku's tool-selection accuracy drops measurably on ambiguous overlap cases, which would hurt the headline success criterion.

### Prompt strategy

- System prompt + merged tool catalog stored in `src/prompts/system.md` and `src/prompts/tools_intro.md`, loaded at startup, **prompt-cached** via Anthropic's ephemeral cache (1h TTL by default, refreshed on catalog change).
- Few-shot examples for tool-selection ambiguity live in `src/prompts/examples.md`, rotated from labeled set to prevent overfit.
- Prompts versioned with the code. No DSPy / no prompt optimizer in v1.

### Cost envelope

Target task: 3–5 tool calls, ~6k input tokens (mostly catalog, cached), ~1.5k output tokens cumulative.

- Uncached system+catalog (~5k tokens): write cost ~$0.019 (paid once per cache window)
- Per-task cached read (~5k tokens @ Sonnet cache read $0.30/Mtok): ~$0.0015
- Per-task output (~1.5k @ $15/Mtok): ~$0.022
- **Per-task total: ~$0.025**, p95 estimated at $0.04, hard ceiling $0.10 — meets envelope.

At ~100 tasks/day: ~$2.50/day ≈ $75/month. Affordable as portfolio infra.

## Evaluation Strategy

- **Golden dataset:** 30 labeled tasks covering: (a) single-server happy path, (b) cross-server orchestration, (c) ambiguous tool selection, (d) failure-and-recovery. Stored in `evals/golden_tasks.jsonl`.
- **Metrics:**
  - Workflow success (binary, judged by post-condition checks — e.g., PR exists with expected title, Slack message present)
  - Tool selection correctness on overlap cases (exact match against labeled correct server)
  - p50/p95 latency and cost
- **Frequency:** per-PR via GitHub Actions using a recorded MCP server fixture (no real GitHub/Slack writes in CI).
- **Tools:** Inspect AI for orchestration; custom checker scripts for post-conditions.
- **Acceptance threshold:** PR blocked if workflow success drops >5pp, or if p95 latency exceeds 8s, or if p95 cost exceeds $0.05.

## Guardrails & Failure Modes

**Input guardrails:**

- Request payload size capped at 32KB
- Prompts containing credential patterns (e.g., `ghp_`, `xoxb-`) are rejected with `400` and not logged
- Off-domain prompts (e.g., "write me a poem") are detected by a cheap classifier and refused with a fixed message — preserves the vertical focus

**Output guardrails:**

- LLM `tool_use` arguments validated against MCP server schema before dispatch; validation failure triggers one retry with corrective system message, then surfaces the error
- Cost meter checked after each LLM turn; if cumulative task cost exceeds the hard ceiling, the agent halts and returns partial result with explicit truncation marker

**Known failure modes:**

1. MCP server down at startup → degraded mode: agent boots without that server's tools, `/health` reports red, `/chat` returns 503 if a request needs that server
2. MCP server times out mid-task → retry with backoff (max 3); on terminal failure, agent reports the failing step and returns the partial trace
3. Anthropic API rate-limited → exponential backoff at the SDK layer; if request budget exhausted, return 429 with retry-after
4. Tool returns ambiguous success (no error but unexpected payload shape) → schema-validated; if validation fails, treat as terminal error for that step
5. Model returns parallel tool calls that depend on each other → executed in parallel anyway; failures surface as normal step errors and are recorded in the trace (signal to improve prompt, not silently corrected)

## Observability

- **Traces:** JSON Lines to `logs/traces.jsonl` (configurable sink — file, stdout, or HTTP webhook). One line per tool call. Schema versioned via `schema_version` field.
- **Metrics:** counters and histograms exported via OpenTelemetry (default exporter: stdout for dev; OTLP for prod-style deployments). Key series: `toolforge.task.latency_ms`, `toolforge.task.cost_usd`, `toolforge.tool.errors_total`, `toolforge.selection.heuristic_rule_fired`.
- **Storage:** traces live on disk by default. Optional rotation via standard `logrotate`. No bundled UI; users point Grafana / Datadog at the file or OTLP stream.
- **Alerts:** none in v1. A simple script `scripts/cost_report.py` rolls up daily cost from traces.

## Architecture Overview

Single Python 3.11 process. Three layers:

1. **HTTP layer** — FastAPI app exposing `/chat`, `/chat/stream`, `/health`, `/tools`. Stateless; sessions are request-scoped.
2. **Agent core** — orchestration loop calling Anthropic SDK with the merged tool catalog. Implements the selection heuristic, retry policy, dry-run mode, and trace emission. Pydantic models for all internal contracts.
3. **MCP client pool** — one connection per configured MCP server using the official MCP Python SDK. Tool catalog cached in Redis (key: `mcp:catalog:<server_id>`, TTL 5min).

External dependencies: Anthropic SDK, MCP Python SDK, FastAPI, Pydantic v2, Redis client, OpenTelemetry SDK. Detailed component design lives in `docs/ARCHITECTURE.md` (not part of this spec).

## Constraints & Assumptions

- Assumes Python 3.11+ available locally
- Assumes Redis ≥ 6 reachable at startup (in-process Redis acceptable for dev)
- Assumes user has Anthropic API key with prompt-caching tier
- Assumes ≥2 MCP servers are running and reachable when the agent starts — agent does not manage MCP server lifecycle
- Assumes MCP protocol version locked at the SDK's pinned version; protocol churn between releases is handled as a normal dependency bump
- Assumes single-user trust model — no per-tenant credential scoping
- Assumes deployment is local or trusted network; FastAPI is not behind a reverse proxy in v1

## Out of Scope (v1)

- CLI client (HTTP API is the only entry point)
- Persistent conversation memory across HTTP sessions (each `/chat` is stateless unless caller passes prior turns)
- MCP server lifecycle management (start/stop/health-restart of MCP servers themselves)
- Authentication / authorization on `/chat`
- Web UI / dashboard
- Streaming token output beyond the optional SSE endpoint
- Multi-model orchestration (e.g., Sonnet planner + Haiku executor)
- Distributed deployment (multi-node, load balancing)
- Recording / replay of MCP traffic for debugging

## Open Questions

1. **[RESOLVED 2026-05-14] Persistent conversation memory.** v1 ships stateless. Callers MAY supply prior turns via `ChatRequest.messages` and the orchestrator concatenates them ahead of the current turn; `session_id` remains a per-request correlation token (written to every trace record, never read back). A Redis-backed turn store keyed by `session_id` is a candidate for v2. Revisit if (a) operators report painful client-side history management, or (b) selection rule 3 (`session-recency`) needs cross-request signal.
2. **[RESOLVED 2026-05-12] GitHub MCP server choice.** Anthropic reference (`@modelcontextprotocol/server-github`) picked for parity with the filesystem launch pattern and SDK alignment; community fork reconsidered if benchmark gaps surface. Use Anthropic's reference `github-mcp-server` or the community `mcp-server-github`? Need to benchmark tool ergonomics and PR-creation reliability against the golden dataset.
3. **Slack MCP server availability.** Is there a stable third-party Slack MCP server, or does this project need to ship a thin own-server as scaffolding? If so, mark it clearly in the README as supporting infrastructure, not the deliverable.
4. **Embedding source** — RESOLVED 2026-05-13: Voyage hosted `voyage-3-lite` via REST; `HashingEmbedder` retained as zero-key fallback. Revisit if rule-4 eval accuracy drops or if Voyage availability becomes a concern.
5. **Cost-ceiling behavior.** When ceiling is hit mid-task, halt-with-partial (current spec) or one-shot finish with cheaper model? Current spec commits to halt; revisit after first eval pass.

## Differentiation (Portfolio Note)

Three things distinguish ToolForge from a "hello world" MCP agent:

1. **Documented selection heuristic.** The "which server's tool do I call when two overlap?" question is usually hand-waved in agent demos. ToolForge specifies a 5-rule deterministic ordering, records which rule fired in every trace, and evaluates correctness against a labeled set. This is the part a reviewer reading the repo will recognize as senior work.
2. **Cost-disciplined design from day one.** Prompt caching, a hard ceiling, and per-task cost in the trace stream. Most agent demos quietly burn tokens; this one publishes the bill.
3. **Dry-run as a first-class mode.** The agent can show its plan without executing — a small feature that turns "magic agent" into a tool a developer can actually trust with destructive operations (open PR, send Slack message).

The README will include a one-page case study: a side-by-side of two ambiguous prompts, the selection rule that fired, and the resulting trace.
