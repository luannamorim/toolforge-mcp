"""ToolForge selection-accuracy eval — SPEC L35 headline metric.

Measures the fraction of tool calls where the correct server (and heuristic
rule) was chosen, across tasks covering all four SPEC categories:
single-server, cross-server, ambiguous, and failure-recovery.

Run:
    uv run inspect eval evals/selection_accuracy.py \\
        --model anthropic/claude-sonnet-4-6

Limit to 3 samples for a quick smoke-test:
    uv run inspect eval evals/selection_accuracy.py \\
        --model anthropic/claude-sonnet-4-6 --limit 3

Acceptance threshold (SPEC L35): joint_accuracy >= 0.90 on n=30 corpus.
At n=15 (current seed), the gate is informational; binds when corpus reaches 30.

--- Rule coverage ---
The current 2-server fleet (filesystem + github) exposes zero overlapping tool
names, so the selector always shortcuts to rule "single-candidate" (selector.py
line 50-51) before any of the 5 heuristic rules run.  Exercising rules 1-5
requires ≥2 servers that expose the same tool name.  Next step: add a stub
MCP server to mcp.servers.eval.json that deliberately shares a tool name with
an existing server (e.g. both expose a "search" tool), then add tasks that
route through that overlap.

rule "session-recency" is additionally blocked in dry_run=True mode: rule 3
only updates the recency list after a *real successful call*
(orchestrator.py:232), so the list is always empty in eval and the rule can
never fire.  Testing session-recency requires multi-turn live execution.
"""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset

from evals.scorers import rule_only, selection_match, server_only
from evals.solvers import toolforge_solver


@task
def selection_accuracy() -> Task:
    return Task(
        dataset=json_dataset("evals/golden_tasks.jsonl"),
        solver=toolforge_solver(),
        scorer=[selection_match(), server_only(), rule_only()],
    )
