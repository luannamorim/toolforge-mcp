"""Capture per-task accuracy from a completed eval run into baseline_metrics.json.

Usage:
    uv run python scripts/update_baseline.py <log-dir> <thresholds.json> <output-baseline.json>

Reads the eval logs in <log-dir>, extracts accuracy for each (task, scorer) pair
defined in <thresholds.json>, and writes the result to <output-baseline.json>.

Exits 1 if any expected task is missing from the logs — refuses to write a partial
baseline, which could mask regressions on newly added tasks.

Run on a known-good main branch after every intentional accuracy improvement,
then commit the updated evals/baseline_metrics.json.
"""

from __future__ import annotations

import datetime
import json
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.check_eval_thresholds import extract_accuracy, load_eval_logs  # noqa: E402


def _git_short_sha() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except (subprocess.CalledProcessError, FileNotFoundError):
        return "unknown"


def main(log_dir: str, thresholds_path: str, output_path: str) -> int:
    cfg = json.loads(Path(thresholds_path).read_text())
    logs = load_eval_logs(Path(log_dir))
    tasks: dict[str, dict] = {}
    missing: list[str] = []

    for task_name, spec in cfg["thresholds"].items():
        scorer = spec["scorer"]
        log = logs.get(task_name)
        if log is None:
            print(f"[ERROR] no log for task {task_name!r} in {log_dir}", file=sys.stderr)
            missing.append(task_name)
            continue
        acc = extract_accuracy(log, scorer)
        if acc is None:
            print(f"[ERROR] scorer {scorer!r} not in log for task {task_name!r}", file=sys.stderr)
            missing.append(task_name)
            continue
        tasks[task_name] = {"scorer": scorer, "baseline_accuracy": round(acc, 6)}

    if missing:
        print(
            f"\nRefusing to write partial baseline — missing: {', '.join(missing)}",
            file=sys.stderr,
        )
        return 1

    output = {
        "version": 1,
        "_note": (
            "Per-task baseline accuracy for SPEC L131 relative >5pp gate. "
            "Refreshed manually via scripts/update_baseline.py after a known-good main run, "
            "then committed. retry_recovery_task is included for completeness; "
            "its n=3 corpus makes the >5pp gate effectively binary."
        ),
        "captured_at": datetime.date.today().isoformat(),
        "captured_commit": _git_short_sha(),
        "tasks": tasks,
    }
    Path(output_path).write_text(json.dumps(output, indent=2) + "\n")
    sha = output["captured_commit"]
    print(f"Baseline written to {output_path} ({sha}, tasks: {sorted(tasks)})")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 4:
        print(
            f"Usage: {sys.argv[0]} <log-dir> <thresholds.json> <output-baseline.json>",
            file=sys.stderr,
        )
        sys.exit(2)
    sys.exit(main(sys.argv[1], sys.argv[2], sys.argv[3]))
