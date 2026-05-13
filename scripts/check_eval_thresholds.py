"""Read Inspect AI JSON eval logs and assert per-task accuracy floors.

Usage:
    uv run python scripts/check_eval_thresholds.py <log-dir> <thresholds.json>

Exits 0 if every task meets its configured floor, 1 otherwise.
Expects logs written with `inspect eval --log-format json`.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


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


def main(log_dir: str, thresholds_path: str) -> int:
    cfg = json.loads(Path(thresholds_path).read_text())
    logs = load_eval_logs(Path(log_dir))
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
        floor = spec["min_accuracy"]
        passed = acc >= floor
        status = "PASS" if passed else "FAIL"
        rationale = spec.get("rationale", "")
        print(f"[{status}] {task_name}/{scorer}: {acc:.3f} (floor {floor:.2f}) — {rationale}")
        if not passed:
            failures.append(f"{task_name}: {acc:.3f} < {floor:.2f}")

    if failures:
        print("\nThreshold failures:", file=sys.stderr)
        for f in failures:
            print(f"  - {f}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <log-dir> <thresholds.json>", file=sys.stderr)
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2]))
