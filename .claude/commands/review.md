# /review

Reviews the current diff (uncommitted changes or current branch vs. main) by delegating to the `code-reviewer` agent. Focused on ToolForge's locked conventions from CLAUDE.md.

## When to use

- Before committing a non-trivial change
- Before opening a PR
- After substantial AI-generated edits, as a sanity sweep

## What I'll do

1. Detect scope: uncommitted (`git diff`) if present, else branch (`git diff main...HEAD`).
2. If neither shows changes, stop and say so.
3. Spawn the `code-reviewer` agent with the diff and CLAUDE.md as context.
4. Surface findings grouped by severity, with `path:line` references and concrete fixes.

## Output

Issue list grouped by severity, then a one-line verdict: `✅ Approve`, `🟡 Approve with comments`, or `🔴 Request changes`.

## Notes

- Style nits covered by `ruff` are NOT flagged here. Run `ruff check` separately when tooling lands.
- For SPEC-conformance specifically (cost ceiling, heuristic determinism, retry policy), use `/spec-check` instead.
