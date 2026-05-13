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

from scripts.check_eval_thresholds import _P95_MIN_SAMPLES, _compute_p95, main  # noqa: E402


def _write_baseline(tmp_path: Path, tasks: dict[str, tuple[str, float]]) -> Path:
    """Write a minimal baseline_metrics.json. tasks: {task_name: (scorer, baseline_accuracy)}."""
    data = {
        "version": 1,
        "tasks": {
            name: {"scorer": scorer, "baseline_accuracy": acc}
            for name, (scorer, acc) in tasks.items()
        },
    }
    p = tmp_path / "baseline_metrics.json"
    p.write_text(json.dumps(data))
    return p


def _write_thresholds(
    tmp_path: Path,
    task: str,
    scorer: str,
    floor: float,
    p95_latency_ms_max: float | None = None,
    p95_cost_usd_max: float | None = None,
) -> Path:
    entry: dict = {"scorer": scorer, "min_accuracy": floor, "rationale": "test"}
    if p95_latency_ms_max is not None:
        entry["p95_latency_ms_max"] = p95_latency_ms_max
    if p95_cost_usd_max is not None:
        entry["p95_cost_usd_max"] = p95_cost_usd_max
    cfg = {"version": 2, "thresholds": {task: entry}}
    p = tmp_path / "thresholds.json"
    p.write_text(json.dumps(cfg))
    return p


def _write_log_with_samples(
    log_dir: Path,
    task: str,
    scorer: str,
    accuracy: float,
    *,
    per_sample_latency_s: list[float] | None = None,
    per_sample_cost_usd: list[float] | None = None,
) -> None:
    """Write a synthetic Inspect AI JSON log with per-sample timing/cost data."""
    n = max(len(per_sample_latency_s or []), len(per_sample_cost_usd or []))
    samples = []
    for i in range(n):
        sample: dict = {"id": i}
        if per_sample_latency_s and i < len(per_sample_latency_s):
            sample["total_time"] = per_sample_latency_s[i]
        if per_sample_cost_usd and i < len(per_sample_cost_usd):
            sample["metadata"] = {"cost_usd": per_sample_cost_usd[i]}
        samples.append(sample)

    data = {
        "version": 2,
        "status": "success",
        "eval": {"task": task},
        "samples": samples,
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


def _write_log(log_dir: Path, task: str, scorer: str, accuracy: float) -> None:
    """Write a minimal synthetic Inspect AI JSON log with no sample-level data."""
    _write_log_with_samples(log_dir, task, scorer, accuracy)


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


# ---------------------------------------------------------------------------
# Baseline / relative gate tests
# ---------------------------------------------------------------------------


def test_baseline_pass_when_current_equals_baseline(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(log_dir, task="my_task", scorer="selection_match", accuracy=0.85)
    thresholds = _write_thresholds(tmp_path, task="my_task", scorer="selection_match", floor=0.50)
    baseline = _write_baseline(tmp_path, {"my_task": ("selection_match", 0.85)})

    assert main(str(log_dir), str(thresholds), str(baseline)) == 0


def test_baseline_fail_when_drop_exceeds_5pp(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(log_dir, task="my_task", scorer="selection_match", accuracy=0.72)
    thresholds = _write_thresholds(tmp_path, task="my_task", scorer="selection_match", floor=0.50)
    baseline = _write_baseline(tmp_path, {"my_task": ("selection_match", 0.85)})

    assert main(str(log_dir), str(thresholds), str(baseline)) == 1
    captured = capsys.readouterr()
    assert "5pp gate" in captured.out
    assert "my_task" in captured.err


def test_baseline_pass_at_exact_5pp_boundary(tmp_path: Path) -> None:
    # Strict ">5pp" means exactly 5pp is a pass
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(log_dir, task="my_task", scorer="selection_match", accuracy=0.80)
    thresholds = _write_thresholds(tmp_path, task="my_task", scorer="selection_match", floor=0.50)
    baseline = _write_baseline(tmp_path, {"my_task": ("selection_match", 0.85)})

    assert main(str(log_dir), str(thresholds), str(baseline)) == 0


def test_baseline_warn_when_task_missing_from_baseline(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    _write_log(log_dir, task="my_task", scorer="selection_match", accuracy=0.85)
    thresholds = _write_thresholds(tmp_path, task="my_task", scorer="selection_match", floor=0.50)
    # baseline has no entry for my_task
    baseline = _write_baseline(tmp_path, {})

    assert main(str(log_dir), str(thresholds), str(baseline)) == 0
    captured = capsys.readouterr()
    assert "[WARN]" in captured.err
    assert "my_task" in captured.err


# ---------------------------------------------------------------------------
# _compute_p95 helper tests
# ---------------------------------------------------------------------------


def test_compute_p95_empty_returns_none() -> None:
    assert _compute_p95([]) is None


def test_compute_p95_small_n_returns_max() -> None:
    values = [float(i) for i in range(1, _P95_MIN_SAMPLES)]  # one below the threshold
    assert _compute_p95(values) == max(values)


def test_compute_p95_large_n() -> None:
    values = list(range(1, 31))  # 1..30
    p95 = _compute_p95(values)
    assert p95 is not None
    assert p95 >= 28.0  # p95 of 1..30 should be near the top


# ---------------------------------------------------------------------------
# p95 latency gate tests
# ---------------------------------------------------------------------------


def test_p95_latency_pass_when_below_max(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # 30 samples all well under 8s
    latencies_s = [1.0 + i * 0.05 for i in range(30)]
    _write_log_with_samples(
        log_dir, "my_task", "selection_match", accuracy=0.85, per_sample_latency_s=latencies_s
    )
    thresholds = _write_thresholds(
        tmp_path, "my_task", "selection_match", floor=0.50, p95_latency_ms_max=8000.0
    )

    assert main(str(log_dir), str(thresholds)) == 0


def test_p95_latency_fail_when_over_max(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # 30 samples; push p95 above 8s by making the last few very slow
    latencies_s = [1.0] * 25 + [10.0, 10.5, 11.0, 11.5, 12.0]
    _write_log_with_samples(
        log_dir, "my_task", "selection_match", accuracy=0.85, per_sample_latency_s=latencies_s
    )
    thresholds = _write_thresholds(
        tmp_path, "my_task", "selection_match", floor=0.50, p95_latency_ms_max=8000.0
    )

    assert main(str(log_dir), str(thresholds)) == 1
    captured = capsys.readouterr()
    assert "p95 latency" in captured.out
    assert "my_task" in captured.err


def test_p95_latency_warn_when_no_timing_data(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Log has no samples → latency data absent → warn, don't fail
    _write_log(log_dir, "my_task", "selection_match", accuracy=0.85)
    thresholds = _write_thresholds(
        tmp_path, "my_task", "selection_match", floor=0.50, p95_latency_ms_max=8000.0
    )

    assert main(str(log_dir), str(thresholds)) == 0
    captured = capsys.readouterr()
    assert "[WARN]" in captured.err


# ---------------------------------------------------------------------------
# p95 cost gate tests
# ---------------------------------------------------------------------------


def test_p95_cost_pass_when_below_max(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    costs = [0.001 + i * 0.001 for i in range(30)]
    _write_log_with_samples(
        log_dir, "my_task", "selection_match", accuracy=0.85, per_sample_cost_usd=costs
    )
    thresholds = _write_thresholds(
        tmp_path, "my_task", "selection_match", floor=0.50, p95_cost_usd_max=0.05
    )

    assert main(str(log_dir), str(thresholds)) == 0


def test_p95_cost_fail_when_over_max(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Push p95 well above $0.05
    costs = [0.001] * 25 + [0.08, 0.09, 0.10, 0.11, 0.12]
    _write_log_with_samples(
        log_dir, "my_task", "selection_match", accuracy=0.85, per_sample_cost_usd=costs
    )
    thresholds = _write_thresholds(
        tmp_path, "my_task", "selection_match", floor=0.50, p95_cost_usd_max=0.05
    )

    assert main(str(log_dir), str(thresholds)) == 1
    captured = capsys.readouterr()
    assert "p95 cost" in captured.out
    assert "my_task" in captured.err


def test_p95_skipped_when_threshold_absent(tmp_path: Path) -> None:
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    # Even with extreme latency values, the gate should not fire if not configured
    latencies_s = [60.0] * 30
    _write_log_with_samples(
        log_dir, "my_task", "selection_match", accuracy=0.85, per_sample_latency_s=latencies_s
    )
    # No p95 keys in thresholds
    thresholds = _write_thresholds(tmp_path, "my_task", "selection_match", floor=0.50)

    assert main(str(log_dir), str(thresholds)) == 0
