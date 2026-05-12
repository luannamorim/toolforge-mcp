"""Unit tests for selection-accuracy scorer internals.

Tests target the pure helper functions (_load_trace_calls, _score_calls)
directly — no Inspect AI runtime, no live Anthropic API, no MCP servers.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from evals.scorers import _load_trace_calls, _score_calls

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _write_trace(tmp_path: Path, records: list[dict]) -> Path:
    sink = tmp_path / "traces.jsonl"
    with sink.open("w") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")
    return sink


def _call(session_id: str, step: int, server: str, rule: str, success: bool = True) -> dict:
    return {
        "session_id": session_id,
        "step": step,
        "server": server,
        "selection_rule": rule,
        "success": success,
        "tool": "read_file",
    }


# ---------------------------------------------------------------------------
# _load_trace_calls
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_load_returns_only_matching_session(tmp_path):
    sink = _write_trace(tmp_path, [
        _call("sess-A", 1, "filesystem", "argument-type"),
        _call("sess-B", 1, "github", "explicit-mention"),
        _call("sess-A", 2, "github", "session-recency"),
    ])
    calls = _load_trace_calls(str(sink), "sess-A")
    assert len(calls) == 2
    assert calls[0]["step"] == 1
    assert calls[1]["step"] == 2


@pytest.mark.unit
def test_load_returns_empty_for_missing_session(tmp_path):
    sink = _write_trace(tmp_path, [_call("sess-A", 1, "filesystem", "argument-type")])
    assert _load_trace_calls(str(sink), "sess-X") == []


@pytest.mark.unit
def test_load_returns_empty_for_missing_file():
    assert _load_trace_calls("/nonexistent/path.jsonl", "sess-A") == []


@pytest.mark.unit
def test_load_orders_by_step(tmp_path):
    sink = _write_trace(tmp_path, [
        _call("s", 3, "github", "argument-type"),
        _call("s", 1, "filesystem", "explicit-mention"),
        _call("s", 2, "filesystem", "argument-type"),
    ])
    calls = _load_trace_calls(str(sink), "s")
    assert [c["step"] for c in calls] == [1, 2, 3]


# ---------------------------------------------------------------------------
# _score_calls — joint (server + rule)
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_score_all_match_returns_1():
    actual = [{"server": "filesystem", "selection_rule": "argument-type"}]
    expected = [{"server": "filesystem", "rule": "argument-type"}]
    value, explanation = _score_calls(actual, expected, match_server=True, match_rule=True)
    assert value == 1.0
    assert "OK" in explanation


@pytest.mark.unit
def test_score_one_miss_of_two_returns_half():
    actual = [
        {"server": "filesystem", "selection_rule": "argument-type"},
        {"server": "filesystem", "selection_rule": "priority-order"},  # wrong server
    ]
    expected = [
        {"server": "filesystem", "rule": "argument-type"},
        {"server": "github", "rule": "argument-type"},
    ]
    value, _ = _score_calls(actual, expected, match_server=True, match_rule=True)
    assert value == pytest.approx(0.5)


@pytest.mark.unit
def test_score_spurious_extra_call_penalised():
    actual = [
        {"server": "filesystem", "selection_rule": "argument-type"},
        {"server": "github", "selection_rule": "session-recency"},  # unexpected
    ]
    expected = [{"server": "filesystem", "rule": "argument-type"}]
    value, explanation = _score_calls(actual, expected, match_server=True, match_rule=True)
    assert value == pytest.approx(0.5)
    assert "SPURIOUS" in explanation


@pytest.mark.unit
def test_score_no_calls_emitted_returns_zero():
    expected = [{"server": "filesystem", "rule": "argument-type"}]
    value, explanation = _score_calls([], expected, match_server=True, match_rule=True)
    assert value == 0.0
    assert "MISSING" in explanation


@pytest.mark.unit
def test_score_both_empty_returns_one():
    value, _ = _score_calls([], [], match_server=True, match_rule=True)
    assert value == 1.0


# ---------------------------------------------------------------------------
# _score_calls — server-only and rule-only modes
# ---------------------------------------------------------------------------


@pytest.mark.unit
def test_score_server_only_ignores_rule_mismatch():
    actual = [{"server": "filesystem", "selection_rule": "priority-order"}]
    expected = [{"server": "filesystem", "rule": "argument-type"}]
    value, _ = _score_calls(actual, expected, match_server=True, match_rule=False)
    assert value == 1.0


@pytest.mark.unit
def test_score_rule_only_ignores_server_mismatch():
    actual = [{"server": "github", "selection_rule": "argument-type"}]
    expected = [{"server": "filesystem", "rule": "argument-type"}]
    value, _ = _score_calls(actual, expected, match_server=False, match_rule=True)
    assert value == 1.0
