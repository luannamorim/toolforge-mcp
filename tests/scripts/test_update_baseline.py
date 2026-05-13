"""Unit tests for scripts/update_baseline.py.

No network, no API, no git required (captured_commit gracefully falls back to
'unknown' when git is unavailable or run outside a repo).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.update_baseline import main  # noqa: E402


def _write_log(log_dir: Path, task: str, scorer: str, accuracy: float) -> None:
    data = {
        "version": 2,
        "eval": {"task": task},
        "results": {
            "scores": [
                {
                    "name": scorer,
                    "metrics": {"accuracy": {"name": "accuracy", "value": accuracy}},
                }
            ]
        },
    }
    (log_dir / f"{task}.json").write_text(json.dumps(data))


def _write_thresholds(tmp_path: Path, tasks: dict[str, str]) -> Path:
    """tasks: {task_name: scorer_name}."""
    cfg = {
        "version": 1,
        "thresholds": {
            name: {"scorer": scorer, "min_accuracy": 0.50, "rationale": "test"}
            for name, scorer in tasks.items()
        },
    }
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps(cfg))
    return p


def test_writes_baseline_with_correct_values(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(log_dir, "sel_task", "selection_match", 0.87)
    _write_log(log_dir, "retry_task", "retry_recovery", 1.0)
    thresholds = _write_thresholds(tmp_path, {"sel_task": "selection_match", "retry_task": "retry_recovery"})
    output = tmp_path / "baseline.json"

    assert main(str(log_dir), str(thresholds), str(output)) == 0
    assert output.exists()

    data = json.loads(output.read_text())
    assert data["version"] == 1
    assert "captured_at" in data
    assert "captured_commit" in data
    assert data["tasks"]["sel_task"]["baseline_accuracy"] == pytest.approx(0.87)
    assert data["tasks"]["sel_task"]["scorer"] == "selection_match"
    assert data["tasks"]["retry_task"]["baseline_accuracy"] == pytest.approx(1.0)


def test_refuses_partial_baseline_when_task_missing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Only one of two expected tasks has a log
    _write_log(log_dir, "sel_task", "selection_match", 0.87)
    thresholds = _write_thresholds(tmp_path, {"sel_task": "selection_match", "missing_task": "retry_recovery"})
    output = tmp_path / "baseline.json"

    assert main(str(log_dir), str(thresholds), str(output)) == 1
    assert not output.exists()
    captured = capsys.readouterr()
    assert "missing_task" in captured.err
