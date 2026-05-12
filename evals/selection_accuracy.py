"""ToolForge selection-accuracy eval — SPEC L35 headline metric.

Measures the fraction of tool calls where the correct server (and heuristic
rule) was chosen, across 10 seed tasks covering all four SPEC categories:
single-server, cross-server, ambiguous, and failure-recovery.

Run:
    uv run inspect eval evals/selection_accuracy.py \\
        --model anthropic/claude-sonnet-4-6

Limit to 3 samples for a quick smoke-test:
    uv run inspect eval evals/selection_accuracy.py \\
        --model anthropic/claude-sonnet-4-6 --limit 3

Acceptance threshold (SPEC L35): joint_accuracy >= 0.90 on n=30 corpus.
At n=10 (seed), the gate is informational; binds when corpus reaches 30.
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
