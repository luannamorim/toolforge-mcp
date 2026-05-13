"""Read Inspect AI JSON eval logs and assert per-task accuracy floors.

Usage:
    uv run python scripts/check_eval_thresholds.py <log-dir> <thresholds.json> [--baseline <path>]

Exits 0 if every task meets its absolute floor, has not dropped more than 5pp
below the stored baseline (SPEC L131 relative gate), and has p95 latency/cost
within the configured maxima (SPEC L131 absolute gates). Exits 1 otherwise.
Expects logs written with `inspect eval --log-format json`.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections.abc import Callable
from pathlib import Path

# Inspect AI stores wall-clock time at EvalSample.total_time (seconds).
# Cost lives in EvalSample.metadata.cost_usd, written by evals/solvers.py.

_P95_MIN_SAMPLES = 5
_BASELINE_DROP_THRESHOLD = 0.05  # SPEC L131: >5pp relative accuracy regression


def load_eval_logs(log_dir: Path) -> dict[str, dict]:
    """Return {task_name: parsed_log} for each *.json file in log_dir."""
    results: dict[str, dict] = {}
    for path in sorted(log_dir.glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            continue
        task = (data.get("eval") or {}).get("task")
        if task:
            results[task] = data
    return results


def extract_accuracy(log: dict, scorer_name: str) -> float | None:
    """Return accuracy float for scorer_name from an Inspect AI JSON log."""
    scores = (log.get("results") or {}).get("scores") or []
    for score in scores:
        if score.get("name") == scorer_name:
            return (score.get("metrics") or {}).get("accuracy", {}).get("value")
    return None


def _extract_latency_ms(log: dict) -> list[float]:
    return [
        float(s["total_time"]) * 1000
        for s in (log.get("samples") or [])
        if s.get("total_time") is not None
    ]


def _extract_metadata_values(log: dict, key: str) -> list[float]:
    return [
        float(v)
        for s in (log.get("samples") or [])
        if (v := (s.get("metadata") or {}).get(key)) is not None
    ]


def _compute_p95(values: list[float]) -> float | None:
    """Return p95 of values, or None when insufficient data."""
    if not values:
        return None
    if len(values) < _P95_MIN_SAMPLES:
        return max(values)
    return statistics.quantiles(values, n=20, method="inclusive")[18]


def _check_p95_gate(
    task_name: str,
    values: list[float],
    maximum: float,
    label: str,
    display_value: Callable[[float], str],
    display_max: Callable[[float], str],
    failures: list[str],
) -> None:
    p95 = _compute_p95(values)
    if p95 is None:
        print(f"[WARN] {task_name}: no {label} data — skipping p95 {label} gate", file=sys.stderr)
        return
    if len(values) < _P95_MIN_SAMPLES:
        print(
            f"[WARN] {task_name}: only {len(values)} samples — p95 {label} approximated as max",
            file=sys.stderr,
        )
    passed = p95 <= maximum
    status = "PASS" if passed else "FAIL"
    print(f"[{status}] {task_name} p95 {label}: {display_value(p95)} (max {display_max(maximum)})")
    if not passed:
        failures.append(f"{task_name} p95 {label}: {display_value(p95)} > {display_max(maximum)}")


def main(log_dir: str, thresholds_path: str, baseline_path: str | None = None) -> int:
    cfg = json.loads(Path(thresholds_path).read_text())
    logs = load_eval_logs(Path(log_dir))

    baseline_tasks: dict[str, dict] = {}
    if baseline_path is not None:
        try:
            baseline_tasks = json.loads(Path(baseline_path).read_text()).get("tasks") or {}
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[WARN] could not read baseline {baseline_path!r}: {exc}", file=sys.stderr)

    failures: list[str] = []

    for task_name, spec in cfg["thresholds"].items():
        log = logs.get(task_name)
        if log is None:
            msg = f"{task_name}: no log found in {log_dir}"
            print(f"[FAIL] {msg}")
            failures.append(msg)
            continue

        scorer = spec["scorer"]
        acc = extract_accuracy(log, scorer)
        if acc is None:
            msg = f"{task_name}: scorer {scorer!r} not found in log"
            print(f"[FAIL] {msg}")
            failures.append(msg)
            continue

        # Absolute floor
        floor = spec["min_accuracy"]
        abs_passed = acc >= floor
        status = "PASS" if abs_passed else "FAIL"
        rationale = spec.get("rationale", "")
        print(f"[{status}] {task_name}/{scorer}: {acc:.3f} (floor {floor:.2f}) — {rationale}")
        if not abs_passed:
            failures.append(f"{task_name}: {acc:.3f} < floor {floor:.2f}")

        # Relative regression gate (SPEC L131)
        baseline_entry = baseline_tasks.get(task_name)
        if baseline_entry is None:
            if baseline_path is not None:
                print(f"[WARN] no baseline for {task_name} — skipping relative gate", file=sys.stderr)
        else:
            baseline_acc = baseline_entry.get("baseline_accuracy")
            if baseline_acc is not None:
                delta = baseline_acc - acc
                if delta > _BASELINE_DROP_THRESHOLD:
                    drop_pp = round(delta * 100)
                    msg = (
                        f"{task_name}/{scorer}: current {acc:.3f} < baseline {baseline_acc:.3f}"
                        f" (drop {drop_pp}pp > 5pp gate)"
                    )
                    print(f"[FAIL] {msg}")
                    failures.append(msg)

        # p95 latency gate (SPEC L131)
        if (latency_max := spec.get("p95_latency_ms_max")) is not None:
            _check_p95_gate(
                task_name, _extract_latency_ms(log), latency_max, "latency",
                lambda v: f"{v:.0f}ms", lambda v: f"{v:.0f}ms", failures,
            )

        # p95 cost gate (SPEC L131)
        if (cost_max := spec.get("p95_cost_usd_max")) is not None:
            _check_p95_gate(
                task_name, _extract_metadata_values(log, "cost_usd"), cost_max, "cost",
                lambda v: f"${v:.6f}", lambda v: f"${v:.2f}", failures,
            )

    if failures:
        print("\nThreshold failures:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Check Inspect AI eval logs against configured accuracy thresholds."
    )
    parser.add_argument("log_dir", help="Directory containing *.json eval logs")
    parser.add_argument("thresholds", help="Path to thresholds.json")
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        help="Optional baseline_metrics.json; enables SPEC L131 relative >5pp gate",
    )
    args = parser.parse_args()
    sys.exit(main(args.log_dir, args.thresholds, args.baseline))
