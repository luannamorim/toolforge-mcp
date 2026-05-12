---
name: commit
description: Produce atomic, Conventional-Commits-formatted git commits grouped by logical scope. Use after finishing a change — never mid-WIP. Enforces subject format, body with *why*, and explicit file staging (never `git add -A`).
---

# Professional Commit Guide (ToolForge)

## Overview

Tech leads reviewing a portfolio judge commit history for atomicity, message quality, and discipline. This skill turns any working-tree state into a clean sequence of atomic commits following the [Conventional Commits](https://www.conventionalcommits.org/) specification.

---

# Workflow

## Step 1 — Inspect

Run in parallel:

```bash
git status
git diff HEAD
```

If nothing changed, stop and say so. Do not create empty commits.

## Step 2 — Group into atomic units

Apply the **Grouping heuristic** (see below) to decide how many commits to produce and which files belong to each.

Present the proposed commit plan to the user **before staging anything**:

```
Proposed commits (3):

1. feat(selector): add cosine-similarity tie-breaker for overlapping tools
   Files: src/agent/selector.py, src/agent/selector_types.py

2. test(selector): cover step-4 fallback when descriptions are identical
   Files: tests/agent/test_selector.py

3. docs(spec): record decision on embedding source (Voyage hosted)
   Files: SPEC.md
```

Then proceed with each commit in order.

## Step 3 — Stage and commit (one group at a time)

For each group:

1. Stage files by explicit path — **never** `git add -A` or `git add .`:

```bash
git add src/agent/selector.py src/agent/selector_types.py
```

2. Commit via HEREDOC to preserve formatting:

```bash
git commit -m "$(cat <<'EOF'
feat(selector): add cosine-similarity tie-breaker for overlapping tools

Without a tie-breaker, equal-priority tools were selected
non-deterministically across sessions, violating SPEC § heuristic
determinism. Step-4 cosine scoring now acts as the final discriminator
before falling back to mcp.servers.json priority order.
EOF
)"
```

## Step 4 — Confirm

```bash
git log --oneline -n <N>
```

Print the created commits as a final summary.

---

# Conventional Commits format

```
<type>(<scope>): <subject>

<body>

<footer>
```

## Subject line rules

- **≤72 characters**
- **Imperative mood**: "add", "fix", "remove" — not "added" / "fixes"
- **Lowercase** after the colon
- **No trailing period**

## Types

| type | when |
|------|------|
| `feat` | new capability visible to callers or users |
| `fix` | corrects a defect |
| `refactor` | restructures code without changing behaviour |
| `perf` | improves speed or resource use |
| `test` | adds or fixes tests (only when standalone, not accompanying a feat/fix) |
| `docs` | documentation only (SPEC.md, README, ARCHITECTURE.md) |
| `chore` | tooling, config, repo hygiene — nothing runtime |
| `build` | build system, pyproject.toml, Dockerfile |
| `ci` | GitHub Actions, CI pipeline only |
| `style` | automated formatting (ruff, black) — never mixed with logic changes |
| `revert` | reverts a previous commit |

## Scopes (ToolForge-specific)

Use the scope that best matches where the change lives:

`agent` · `mcp` · `http` · `selector` · `retry` · `trace` · `cache` · `spec` · `claude` · `infra` · `deps`

Omit scope only for changes that genuinely cross-cut multiple areas with no dominant owner.

## Body

Short paragraph explaining **why**, not what (the diff already shows what). Wrap at 72 chars. Separated from subject by a blank line. **Required for any non-trivial commit** — skip only for single-file mechanical changes like dependency bumps.

## Footer (when applicable)

```
BREAKING CHANGE: <description of the incompatibility>
Refs: #<issue-number>
Co-authored-by: Name <email>
```

## Examples

```
feat(selector): add cosine-similarity tie-breaker for overlapping tools

Without a tie-breaker, equal-priority tools were selected
non-deterministically across sessions, violating SPEC § heuristic
determinism. Step-4 cosine scoring now acts as the final discriminator
before falling back to mcp.servers.json priority order.
```

```
fix(retry): cap jitter to prevent thundering herd on 5xx bursts

The previous unbounded jitter occasionally produced delays of 0ms,
which negated the backoff entirely under burst-failure conditions.
Capped at base_delay to keep retries spread.
```

```
chore(claude): add /commit skill

Formalises commit conventions so portfolio reviewers see atomic,
well-described history rather than ad-hoc messages.
```

---

# Atomicity rules

- **One commit = one intention.** If reverting it would undo unrelated work, split it.
- Do not mix refactor with feature.
- Do not mix a code change with a dependency bump.
- Tests that validate a `feat` or `fix` belong in the **same** commit as the code — do not create a separate `test:` commit for them.
- `docs:` is its own commit only when there is no accompanying code change.
- Automated formatting (`style:`) is always a separate commit, never mixed with logic.

---

# Grouping heuristic

Split by this priority order:

1. **Top-level directory**: `src/agent/`, `src/mcp/`, `src/http/`, `tests/`, `docs/`, `.claude/`, root config files — each maps to a natural scope.
2. **File independence within a directory**: if `selector.py` and `retry.py` changed for unrelated reasons, produce two separate commits.
3. **Spec-then-code ordering**: when `SPEC.md` changes alongside the code that implements it, commit `docs(spec):` first, then the implementing code. Decision visible in history before the implementation.

Sanity check: *"Can I revert this commit and leave the repo in a coherent, buildable state?"* If yes, it is atomic enough.

---

# Constraints

| Rule | Reason |
|------|--------|
| Never `git add -A` or `git add .` | `block_sensitive_stage.py` hook blocks `.env*`, `.pem`, `.key`, `credentials*`, `secrets*` — explicit paths keep that gate effective |
| Never `--amend` on a pushed commit | Rewrites shared history |
| Never `--no-verify` | If a hook fires, fix the root cause |
| Never `git push` | Pushing is a deliberate, separate action — out of scope for this skill |
| Message via HEREDOC | Preserves newlines and body formatting |

---

# Output format

```
Commits created (3):

1. feat(selector): add cosine-similarity tie-breaker for overlapping tools
2. test(selector): cover step-4 fallback when descriptions are identical
3. docs(spec): record decision on embedding source (Voyage hosted)

Run `git log --oneline` to review the full history.
```

---

# Notes

- Commit messages are in **English** — consistent with the rest of the repo (SPEC.md, CLAUDE.md, all agents and commands).
- Run `/review` before committing non-trivial changes to catch quality issues before they land in history.
- Run `/spec-check` before committing anything that touches orchestration, tool selection, retry policy, trace emission, or model choice.
