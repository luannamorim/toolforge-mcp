"""Unit tests for scripts/check_eval_thresholds.py.

No network, no API, no inspect_ai dependency.  Each test writes synthetic
JSON log files to tmp_path and calls the module's main() directly.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

# Add the repo root to sys.path so we can import the scripts/ module without
# installing it as a package.
_REPO_ROOT = Path(__file__).parent.parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_eval_thresholds import main  # noqa: E402


def _write_log(log_dir: Path, task: str, scorer: str, accuracy: float) -> None:
    """Write a minimal synthetic Inspect AI JSON log to log_dir."""
    data = {
        "version": 2,
        "status": "success",
        "eval": {"task": task},
        "results": {
            "scores": [
                {
                    "name": scorer,
                    "scorer": scorer,
                    "metrics": {
                        "accuracy": {"name": "accuracy", "value": accuracy},
                        "stderr": {"name": "stderr", "value": 0.0},
                    },
                }
            ]
        },
    }
    (log_dir / f"{task}.json").write_text(json.dumps(data))


def _write_thresholds(tmp_path: Path, task: str, scorer: str, floor: float) -> Path:
    cfg = {
        "version": 1,
        "thresholds": {
            task: {"scorer": scorer, "min_accuracy": floor, "rationale": "test"},
        },
    }
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps(cfg))
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_pass_when_above_floor(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(log_dir, task="my_task", scorer="selection_match", accuracy=0.85)
    thresholds = _write_thresholds(tmp_path, task="my_task", scorer="selection_match", floor=0.50)

    assert main(str(log_dir), str(thresholds)) == 0


def test_fail_when_below_floor(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(log_dir, task="my_task", scorer="selection_match", accuracy=0.30)
    thresholds = _write_thresholds(tmp_path, task="my_task", scorer="selection_match", floor=0.50)

    assert main(str(log_dir), str(thresholds)) == 1
    captured = capsys.readouterr()
    assert "my_task" in captured.err


def test_fail_when_log_missing(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # No log file written — log_dir is empty
    thresholds = _write_thresholds(tmp_path, task="missing_task", scorer="retry_recovery", floor=1.0)

    assert main(str(log_dir), str(thresholds)) == 1
    captured = capsys.readouterr()
    assert "missing_task" in captured.err
