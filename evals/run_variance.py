"""
run_variance.py — measure run-to-run noise, so the accuracy gate's band is a
measurement rather than an opinion.

    python evals/run_variance.py --runs 3
    python evals/run_variance.py --runs 3 -- PLANNER=auto        # any config
    python evals/run_variance.py --runs 2 --no-answer            # faster

Runs the full benchmark N times, each with its own cache namespace
(HARNESS_CACHE_DIR), then reports mean/stdev/min/max for overall and per-tier
accuracy plus the cases whose correctness differed between runs.

What variance is expected here: first attempts are temperature-0 and
byte-deterministic (verified Day 1). The only stochastic surface is the retry
path at temperature 0.3 - roughly one query in seven reaches it. So the
interesting output is not "is there variance" but WHICH cases flip: those are
the queries whose correctness hangs on a hot retry, and the honest size of
the gate's tolerance band.

Each repeat writes results_var<i>.json/txt via the harness --tag mechanism,
so committed evidence is never overwritten.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HARNESS = ROOT / "evals" / "harness.py"

TIERS = ("easy", "medium", "hard")


def accuracy(records: list) -> float:
    scored = [r for r in records if "correct" in r]
    if not scored:
        return 0.0
    return sum(r["correct"] for r in scored) / len(scored)


def tier_accuracy(records: list, tier: str) -> float:
    band = [r for r in records if r.get("difficulty") == tier and "correct" in r]
    if not band:
        return 0.0
    return sum(r["correct"] for r in band) / len(band)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=3)
    ap.add_argument("--no-answer", action="store_true")
    ap.add_argument("config", nargs="*",
                    help="KEY=VALUE env pairs applied to every repeat "
                         "(e.g. PLANNER=auto PRUNE=1)")
    args = ap.parse_args()

    extra_env = dict(pair.split("=", 1) for pair in args.config)

    per_run: list[dict] = []
    for i in range(1, args.runs + 1):
        env = os.environ.copy()
        env["LANGFUSE_PUBLIC_KEY"] = ""
        env["ANSWER"] = "0" if args.no_answer else os.environ.get("ANSWER", "1")
        env["HARNESS_CACHE_DIR"] = str(ROOT / "evals" / f".cache_var{i}")
        env.update(extra_env)

        tag = f"var{i}"
        print(f"=== run {i}/{args.runs} "
              f"({' '.join(args.config) or 'default'}) ", flush=True)
        proc = subprocess.run(
            [sys.executable, str(HARNESS), "--tag", tag],
            env=env, cwd=str(ROOT),
        )
        if proc.returncode != 0:
            print(f"harness exited {proc.returncode}; aborting")
            return 1

        results = json.loads(
            (ROOT / "evals" / f"results_{tag}.json").read_text(encoding="utf-8"))
        functional = results["functional"]
        per_run.append({
            "overall": accuracy(functional),
            **{tier: tier_accuracy(functional, tier) for tier in TIERS},
            "by_case": {r["id"]: r.get("correct")
                        for r in functional if "correct" in r},
        })

    lines = ["# Run-to-run variance", ""]
    config_label = " ".join(args.config) or "default configuration"
    lines.append(f"{args.runs} runs | {config_label} | model/qwen local")
    lines.append("")
    lines.append("| metric | mean | stdev | min | max |")
    lines.append("|---|---|---|---|---|")

    def row(label: str, values: list):
        lines.append(
            f"| {label} | {statistics.mean(values):.1%} "
            f"| {statistics.stdev(values):.1%} "
            f"| {min(values):.1%} | {max(values):.1%} |")

    row("execution accuracy", [r["overall"] for r in per_run])
    for tier in TIERS:
        row(f"  {tier}", [r[tier] for r in per_run])

    flippers = sorted(
        cid for cid in per_run[0]["by_case"]
        if len({r["by_case"].get(cid) for r in per_run}) > 1
    )
    lines.append("")
    if flippers:
        lines.append(f"Cases that flipped between runs ({len(flippers)}): "
                     + ", ".join(flippers))
        lines.append("")
        lines.append("These are the queries whose correctness depends on the "
                     "stochastic retry path - the population the gate's band "
                     "must absorb.")
    else:
        lines.append("No case flipped across runs - fully deterministic at "
                     "this configuration.")

    report = "\n".join(lines) + "\n"
    out = ROOT / "evals" / "results_variance.md"
    out.write_text(report, encoding="utf-8")
    print()
    print(report)
    print(f"written to {out.name}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
