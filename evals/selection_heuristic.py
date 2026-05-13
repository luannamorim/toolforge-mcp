"""ToolForge heuristic eval — measures rules 1, 2, and 5 of the selector.

Uses two in-repo stub MCP servers (local + cloud, both exposing 'search' and
'lookup' with different schemas/descriptions) to create tool-name overlap.
Without overlap the selector's single-candidate shortcut fires before any
heuristic rule runs.

Run:
    uv run inspect eval evals/selection_heuristic.py \\
        --model anthropic/claude-sonnet-4-6

Limit to 2 samples for a quick smoke-test:
    uv run inspect eval evals/selection_heuristic.py \\
        --model anthropic/claude-sonnet-4-6 --limit 2

--- Rule coverage ---
  rule 1  explicit-mention  — tested: heur-mention-*
  rule 2  argument-type     — tested: heur-argtype-*
  rule 3  session-recency   — NOT testable: recency list only updates after
                               real successful calls (orchestrator.py:232);
                               dry_run=True never populates it.
  rule 4  cosine-similarity — tested: heur-cosine-* (requires EMBEDDER_BACKEND=voyage
                               + VOYAGE_API_KEY at eval time; under HashingEmbedder
                               these fall through to rule 5).
  rule 5  priority-order    — tested: heur-priority-*

Acceptance threshold (SPEC L35): joint_accuracy >= 0.90 on n=30 corpus.
At n=6 (current seed), the gate is informational.
"""

from __future__ import annotations

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset

from evals.scorers import rule_only, selection_match, server_only
from evals.solvers import stub_solver


@task
def selection_heuristic() -> Task:
    return Task(
        dataset=json_dataset("evals/heuristic_tasks.jsonl"),
        solver=stub_solver(),
        scorer=[selection_match(), server_only(), rule_only()],
    )
