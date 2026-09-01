"""
online_report.py — the online-eval half of the picture: analyse LIVE traffic
from a Langfuse observation export instead of the curated benchmark.

    python evals/online_report.py <langfuse-export.csv>

The benchmark is offline evaluation: cases chosen by the author, run in
controlled conditions. This is the complement - what actually arrived, which
guards fired on it, what it cost. The export file is gitignored (it carries
real user questions); this script is the committable half, reading any
export in Langfuse's standard CSV shape.

Every number here is descriptive, not gated: live traffic has no ground
truth to score against. Its job is to show drift and surprise - a guard
suddenly firing, latency climbing, a token budget ballooning.
"""

from __future__ import annotations

import csv
import json
import statistics
import sys
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path


def _parse_json(raw: str) -> dict:
    try:
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_ts(raw: str) -> datetime:
    return datetime.fromisoformat((raw or "").replace("Z", "+00:00")[:26])


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    path = Path(sys.argv[1])
    if not path.exists():
        print(f"no such file: {path}")
        return 1

    traces = defaultdict(lambda: {"gens": 0, "latency": 0.0,
                                  "prompt_tokens": 0, "output_tokens": 0,
                                  "errors": 0, "day": None})
    guard_fires: Counter = Counter()
    blocked = 0
    errors = 0
    generations = 0

    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            trace = traces[row.get("traceId", "")]
            ts = row.get("startTime", "")
            if ts and trace["day"] is None:
                trace["day"] = _parse_ts(ts).date().isoformat()

            if row.get("type") == "GENERATION":
                generations += 1
                trace["gens"] += 1
                try:
                    trace["latency"] += float(row.get("latencyMs") or 0)
                except ValueError:
                    pass
                usage = _parse_json(row.get("usageDetails"))
                trace["prompt_tokens"] += sum(
                    v for k, v in usage.items() if "input" in k.lower())
                trace["output_tokens"] += sum(
                    v for k, v in usage.items() if "output" in k.lower())
                if (row.get("level") or "").upper() == "ERROR":
                    errors += 1
                    trace["errors"] += 1

            meta = _parse_json(row.get("metadata"))
            if meta.get("blocked"):
                blocked += 1
                guard_fires[meta.get("guard", "unknown")] += 1

    if not traces:
        print("no traces found in the export")
        return 1

    per_trace_latency = sorted(t["latency"] / 1000 for t in traces.values())
    prompt = [t["prompt_tokens"] for t in traces.values() if t["prompt_tokens"]]
    output = [t["output_tokens"] for t in traces.values() if t["output_tokens"]]
    days = Counter(t["day"] for t in traces.values())

    def pct(values, p):
        return values[min(len(values) - 1, int(len(values) * p / 100))]

    print("=" * 62)
    print("ONLINE TRAFFIC REPORT -", path.name)
    print("=" * 62)
    print(f"traces              {len(traces)}   across {len(days)} day(s)")
    for day, n in sorted(days.items()):
        print(f"  {day}          {n}")
    print(f"generations         {generations}")
    print(f"generation errors   {errors}")
    print(f"guard blocks        {blocked}   "
          f"({', '.join(f'{k}: {v}' for k, v in guard_fires.most_common()) or 'none'})")
    if per_trace_latency:
        print(f"trace latency p50   {pct(per_trace_latency, 50):6.1f}s   "
              f"p95 {pct(per_trace_latency, 95):6.1f}s")
    if prompt:
        print(f"tokens per trace    p50 {int(statistics.median(prompt))} in / "
              f"{int(statistics.median(output))} out")
    print("=" * 62)
    print("descriptive only - live traffic has no ground truth to score.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
