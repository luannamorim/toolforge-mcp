"""Roll up daily cost from a ToolForge trace sink (JSONL).

Usage:
    uv run python scripts/cost_report.py [trace_file] [--since YYYY-MM-DD] [--until YYYY-MM-DD] [--executed-only]

Reads one JSON object per line from *trace_file* (default: logs/traces.jsonl),
groups records by UTC calendar date, and prints a summary table to stdout.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import UTC, date, datetime
from pathlib import Path


def _parse_date(record: dict) -> date | None:
    ts = record.get("timestamp")
    if not ts:
        return None
    try:
        return datetime.fromisoformat(ts).astimezone(UTC).date()
    except ValueError:
        return None


def main(
    trace_file: str,
    since: date | None = None,
    until: date | None = None,
    executed_only: bool = False,
) -> int:
    path = Path(trace_file)
    if not path.exists():
        print(f"[ERROR] trace file not found: {path}", file=sys.stderr)
        return 1

    # buckets: date -> {cost, records, sessions, errors}
    costs: dict[date, float] = defaultdict(float)
    records: dict[date, int] = defaultdict(int)
    sessions: dict[date, set[str]] = defaultdict(set)
    errors: dict[date, int] = defaultdict(int)

    with path.open(encoding="utf-8") as f:
        for lineno, raw in enumerate(f, start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                rec = json.loads(raw)
            except json.JSONDecodeError:
                print(f"[WARN] line {lineno}: malformed JSON — skipping", file=sys.stderr)
                continue

            if executed_only and not rec.get("executed", True):
                continue

            day = _parse_date(rec)
            if day is None:
                print(f"[WARN] line {lineno}: missing or invalid timestamp — skipping", file=sys.stderr)
                continue

            if since is not None and day < since:
                continue
            if until is not None and day > until:
                continue

            cost = rec.get("cost_usd")
            if cost is None:
                continue

            costs[day] += cost
            records[day] += 1
            if sid := rec.get("session_id"):
                sessions[day].add(sid)
            if not rec.get("success", True):
                errors[day] += 1

    all_days = sorted(costs.keys())

    header = f"{'date':<12}  {'records':>9}  {'sessions':>9}  {'errors':>7}  {'cost_usd':>12}"
    print(header)

    total_cost = 0.0
    total_recs = 0
    total_sessions: set[str] = set()
    total_errors = 0

    for day in all_days:
        c = costs[day]
        r = records[day]
        s = len(sessions[day])
        e = errors[day]
        print(f"{day!s:<12}  {r:>9}  {s:>9}  {e:>7}  ${c:>11.6f}")
        total_cost += c
        total_recs += r
        total_sessions |= sessions[day]
        total_errors += e

    print(f"{'TOTAL':<12}  {total_recs:>9}  {len(total_sessions):>9}  {total_errors:>7}  ${total_cost:>11.6f}")
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Roll up daily cost from a ToolForge JSONL trace sink."
    )
    parser.add_argument(
        "trace_file",
        nargs="?",
        default="logs/traces.jsonl",
        help="Path to traces.jsonl (default: logs/traces.jsonl)",
    )
    parser.add_argument(
        "--since",
        metavar="YYYY-MM-DD",
        help="Only include records on or after this date (UTC)",
    )
    parser.add_argument(
        "--until",
        metavar="YYYY-MM-DD",
        help="Only include records on or before this date (UTC)",
    )
    parser.add_argument(
        "--executed-only",
        action="store_true",
        help="Exclude dry-run records (executed=false)",
    )
    args = parser.parse_args()

    since_date = date.fromisoformat(args.since) if args.since else None
    until_date = date.fromisoformat(args.until) if args.until else None

    sys.exit(main(args.trace_file, since=since_date, until=until_date, executed_only=args.executed_only))
