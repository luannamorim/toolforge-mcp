"""Retry-recovery eval — smoke sample for SPEC FR7.

One sample exercises the full retry path:
  STUB_FAIL_TIMES=2 causes the first two call_tool invocations to raise
  ConnectionError; the third succeeds.  The retry_recovery scorer asserts
  the final trace record has retries>=1 and success=True.

This is a smoke sample, not a corpus.  A broader n>=30 retry corpus with
varied transient failure patterns is a later slice.

CLI usage:
    uv run inspect eval evals/retry_recovery.py \\
        --model anthropic/claude-sonnet-4-6 --limit 1
"""

from __future__ import annotations

from pathlib import Path

from inspect_ai import Task, task
from inspect_ai.dataset import json_dataset

from evals.scorers import retry_recovery
from evals.solvers import flaky_solver


@task
def retry_recovery_task() -> Task:
    return Task(
        dataset=json_dataset(str(Path(__file__).parent / "retry_recovery_tasks.jsonl")),
        solver=flaky_solver(),
        scorer=retry_recovery(),
    )
