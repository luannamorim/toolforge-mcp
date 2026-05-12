# /spec-check

Verifies that a proposed change (staged diff or current branch) does not violate any of the locked commitments in `SPEC.md`. Delegates to the `spec-guardian` agent.

## When to use

- Before merging any change that touches: the orchestration loop, the tool-selection logic, model selection, retry policy, trace emission, or the dry-run path
- After scaffolding new modules — an easy moment to drift from spec
- When upgrading SDK dependencies that might shift defaults (e.g., Anthropic SDK version bump)

## What I'll do

1. Detect change scope: uncommitted (`git diff`) if present, else branch (`git diff main...HEAD`).
2. Spawn the `spec-guardian` agent with the diff, `SPEC.md`, and `CLAUDE.md`.
3. Agent checks the change against each locked commitment and reports per-commitment status.

## Output

```
SPEC Conformance Report — uncommitted

1. Model:          ✅ still claude-sonnet-4-6 with prompt caching
2. Cost ceiling:   ✅ hard ceiling enforced at $0.10/task
3. Latency:        — not touched
4. Tool selection: 🔴 rule #2 replaced by LLM judge in agent/selector.py:48 — violates SPEC § heuristic
5. Trace schema:   🟡 new field `request_id` added without bumping schema_version
6. Dry-run:        ✅ still short-circuits before any MCP call
7. Retry policy:   ✅ exponential backoff (500ms, factor 2, max 3) preserved
8. Out-of-scope:   — not touched

Verdict: 🔴 Blocking violations
```

## Notes

- This command is intentionally narrow: it only checks SPEC commitments, not general code quality. Pair with `/review`.
- If a deviation is intentional, update `SPEC.md` first, then re-run.
