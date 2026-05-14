"""Unit tests for scripts/cost_report.py.

No network, no API, no FastAPI app.  Each test writes a synthetic JSONL trace
file to tmp_path and calls main() directly.
"""

from __future__ import annotations

import json
import sys
from datetime import date
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.cost_report import main  # noqa: E402


def _rec(
    session_id: str,
    timestamp: str,
    cost_usd: float,
    success: bool = True,
    executed: bool = True,
) -> dict:
    return {
        "schema_version": "1",
        "session_id": session_id,
        "timestamp": timestamp,
        "step": 1,
        "server": "filesystem",
        "tool": "read_file",
        "arguments_hash": "abc",
        "latency_ms": 120.0,
        "success": success,
        "executed": executed,
        "tokens_in": 100,
        "tokens_out": 50,
        "cost_usd": cost_usd,
        "selection_rule": "priority",
    }


def _write_jsonl(path: Path, records: list) -> None:
    with path.open("w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")


@pytest.mark.unit
def test_basic_rollup(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    sink = tmp_path / "traces.jsonl"
    _write_jsonl(sink, [
        _rec("s1", "2026-05-12T10:00:00+00:00", 0.001),
        _rec("s1", "2026-05-12T11:00:00+00:00", 0.002),
        _rec("s2", "2026-05-13T09:00:00+00:00", 0.004),
    ])

    rc = main(str(sink))

    assert rc == 0
    out = capsys.readouterr().out
    lines = out.splitlines()
    assert len(lines) == 4  # header + 2 day rows + TOTAL
    assert "2026-05-12" in lines[1]
    assert "2026-05-13" in lines[2]
    assert "TOTAL" in lines[3]
    # Day 1: $0.003000
    assert "0.003000" in lines[1]
    # Day 2: $0.004000
    assert "0.004000" in lines[2]
    # Total: $0.007000
    assert "0.007000" in lines[3]


@pytest.mark.unit
def test_malformed_line_tolerance(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    sink = tmp_path / "traces.jsonl"
    with sink.open("w") as f:
        f.write(json.dumps(_rec("s1", "2026-05-12T10:00:00+00:00", 0.001)) + "\n")
        f.write("this is not json\n")
        f.write(json.dumps(_rec("s1", "2026-05-12T11:00:00+00:00", 0.002)) + "\n")

    rc = main(str(sink))

    assert rc == 0
    captured = capsys.readouterr()
    assert "[WARN]" in captured.err
    assert "0.003000" in captured.out  # two valid records aggregated


@pytest.mark.unit
def test_executed_only_filter(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    sink = tmp_path / "traces.jsonl"
    _write_jsonl(sink, [
        _rec("s1", "2026-05-12T10:00:00+00:00", 0.005, executed=True),
        _rec("s2", "2026-05-12T10:30:00+00:00", 0.100, executed=False),
    ])

    rc = main(str(sink), executed_only=True)

    assert rc == 0
    out = capsys.readouterr().out
    # Only the executed record contributes
    assert "0.005000" in out
    assert "0.100" not in out


@pytest.mark.unit
def test_date_window_filter(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    sink = tmp_path / "traces.jsonl"
    _write_jsonl(sink, [
        _rec("s1", "2026-05-10T10:00:00+00:00", 0.001),  # before window
        _rec("s2", "2026-05-12T10:00:00+00:00", 0.002),  # in window
        _rec("s3", "2026-05-14T10:00:00+00:00", 0.003),  # after window
    ])

    rc = main(str(sink), since=date(2026, 5, 12), until=date(2026, 5, 13))

    assert rc == 0
    out = capsys.readouterr().out
    assert "2026-05-12" in out
    assert "2026-05-10" not in out
    assert "2026-05-14" not in out
    assert "0.002000" in out


@pytest.mark.unit
def test_missing_file(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    rc = main(str(tmp_path / "no_such_file.jsonl"))

    assert rc == 1
    err = capsys.readouterr().err
    assert "[ERROR]" in err
    assert "not found" in err
