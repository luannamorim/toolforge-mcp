"""Selection-accuracy scorers for ToolForge eval harness.

Three scorers run together on each sample:
  selection_match()  — joint (server AND rule) accuracy; SPEC L35 headline metric.
  server_only()      — server-only accuracy; isolates routing bugs.
  rule_only()        — rule-only accuracy; isolates heuristic-ordering bugs.

All three read the per-sample trace JSONL (path stored in state.metadata) and
compare each tool call's (server, selection_rule) against the expected sequence
from sample metadata.expected_calls.  Score = matches / max(expected, actual)
so both missing and spurious calls are penalised.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from inspect_ai.scorer import Score, Target, accuracy, scorer, stderr
from inspect_ai.solver import TaskState


def _load_trace_calls(trace_path: str, session_id: str) -> list[dict[str, Any]]:
    """Return tool-call trace records for the given session, in step order."""
    path = Path(trace_path)
    if not path.exists():
        return []
    calls = []
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        rec = json.loads(line)
        if rec.get("session_id") == session_id:
            calls.append(rec)
    calls.sort(key=lambda r: r.get("step", 0))
    return calls


def _score_calls(
    actual: list[dict[str, Any]],
    expected: list[dict[str, Any]],
    match_server: bool,
    match_rule: bool,
) -> tuple[float, str]:
    """Return (score, explanation) for a list of actual vs expected call dicts."""
    if not expected and not actual:
        return 1.0, "no calls expected or emitted"
    total = max(len(expected), len(actual))
    if total == 0:
        return 1.0, "no calls"
    matches = 0
    lines = []
    for i in range(total):
        a = actual[i] if i < len(actual) else None
        e = expected[i] if i < len(expected) else None
        if a is None:
            lines.append(f"  step {i + 1}: MISSING (expected {e})")
            continue
        if e is None:
            lines.append(f"  step {i + 1}: SPURIOUS server={a.get('server')} rule={a.get('selection_rule')}")
            continue
        ok = True
        if match_server and a.get("server") != e.get("server"):
            ok = False
        if match_rule and a.get("selection_rule") != e.get("rule"):
            ok = False
        if ok:
            matches += 1
            lines.append(f"  step {i + 1}: OK server={a.get('server')} rule={a.get('selection_rule')}")
        else:
            lines.append(
                f"  step {i + 1}: FAIL"
                f" server={a.get('server')!r}(exp {e.get('server')!r})"
                f" rule={a.get('selection_rule')!r}(exp {e.get('rule')!r})"
            )
    return matches / total, "\n".join(lines)


def _make_scorer_fn(match_server: bool, match_rule: bool):
    """Return an async score function bound to the given matching flags."""
    async def score(state: TaskState, target: Target) -> Score:
        meta = state.metadata or {}
        trace_path = meta.get("trace_sink", "")
        session_id = meta.get("session_id", "")
        expected = meta.get("expected_calls", [])
        actual = _load_trace_calls(trace_path, session_id)
        value, explanation = _score_calls(actual, expected, match_server=match_server, match_rule=match_rule)
        return Score(value=value, answer=None, explanation=explanation)
    return score


@scorer(metrics=[accuracy(), stderr()])
def selection_match():
    """Joint (server + rule) selection accuracy — SPEC L35 headline metric."""
    return _make_scorer_fn(match_server=True, match_rule=True)


@scorer(metrics=[accuracy(), stderr()])
def server_only():
    """Server-only accuracy — isolates routing errors from rule-ordering bugs."""
    return _make_scorer_fn(match_server=True, match_rule=False)


@scorer(metrics=[accuracy(), stderr()])
def rule_only():
    """Rule-only accuracy — isolates heuristic-ordering bugs from routing bugs."""
    return _make_scorer_fn(match_server=False, match_rule=True)
