"""
harness.py — run the benchmark and compute the metrics.

    python evals/harness.py                  # everything
    python evals/harness.py --smoke          # 10 cases, for development
    python evals/harness.py --no-answer      # skip prose generation, ~33% faster
    python evals/harness.py --fresh          # ignore the cache

Results are cached per case, keyed on the case id plus everything that could
change the answer - model, schema configuration, planner setting. A run that
dies at case 47 resumes rather than restarting, which matters because a full
run takes over an hour and because a free API tier meters requests per day.

Two things this deliberately does not do:

It does not use an LLM to judge the output. Execution accuracy compares result
rows against the reference query's rows - a data comparison, not a judgement.
That is the structural advantage of a SQL agent over a retrieval system, and
giving it up for convenience would be giving up the whole argument.

It does not report block rate on its own. A guard that refuses everything
scores 100%, so the false-positive rate on the functional suite is reported
beside it. One number without the other is not a security result.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

import db          # noqa: E402
import executor    # noqa: E402
import graph       # noqa: E402
import llm         # noqa: E402

BENCHMARK = Path(__file__).resolve().parent / "benchmark.yaml"
CACHE_DIR = Path(__file__).resolve().parent / ".cache"

# Floats are compared to this many decimals. Currency sums differ in the last
# bits depending on summation order, and a benchmark that fails on 1e-9 is a
# benchmark nobody trusts.
ROUND_DP = 2


# --- comparison -------------------------------------------------------------

def canonical(rows) -> list:
    """
    Reduce a result set to something comparable.

    Rows are sorted, because a question that does not say "ordered by" has no
    defined row order and two correct answers may differ in sequence. Floats
    are rounded. Everything is stringified last so that 5 and 5.0 compare
    equal without int/str ordering errors.
    """
    out = []
    for row in rows or []:
        cells = []
        for cell in row:
            if isinstance(cell, float):
                cells.append(f"{round(cell, ROUND_DP):.{ROUND_DP}f}")
            elif isinstance(cell, int) and not isinstance(cell, bool):
                cells.append(f"{float(cell):.{ROUND_DP}f}")
            else:
                cells.append("" if cell is None else str(cell))
        out.append(tuple(cells))
    return sorted(out)


def results_match(actual, expected) -> bool:
    return canonical(actual) == canonical(expected)


# --- cache ------------------------------------------------------------------

def run_key(case_id: str) -> str:
    """Everything that could change the answer goes into the key."""
    parts = [
        case_id,
        llm.MODEL,
        db.SCHEMA,
        str(graph.USE_PLANNER),
        str(graph.MAX_ATTEMPTS),
        os.environ.get("ANSWER", "1"),
    ]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:16]


def cached(case_id):
    path = CACHE_DIR / f"{run_key(case_id)}.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return None


def store(case_id, record):
    CACHE_DIR.mkdir(exist_ok=True)
    (CACHE_DIR / f"{run_key(case_id)}.json").write_text(
        json.dumps(record, default=str), encoding="utf-8"
    )


# --- running one case -------------------------------------------------------

def run_functional(case) -> dict:
    """Ask the agent, compare its rows against the reference query's rows."""
    reference = executor.run(" ".join(case["reference_sql"].split()), timeout_s=90)
    if not reference.ok:
        return {"id": case["id"], "error": f"reference query failed: {reference.error}"}

    started = time.perf_counter()
    calls_before = len(llm.usage.calls)
    try:
        final = graph.answer(case["question"])
    except Exception as e:                                  # a crash is a result
        return {
            "id": case["id"], "difficulty": case["difficulty"],
            "correct": False, "status": "crash", "error": f"{type(e).__name__}: {e}",
            "elapsed_s": time.perf_counter() - started, "attempts": 0, "calls": 0,
        }

    attempts = final.get("attempts") or []
    correct = final.get("status") == "ok" and results_match(final.get("result"), reference.rows)

    # First-pass accuracy: would this have been right without the retry loop?
    # Only meaningful when the first attempt actually produced rows.
    first_pass = correct and len(attempts) == 1

    return {
        "id": case["id"],
        "difficulty": case["difficulty"],
        "question": case["question"],
        "correct": correct,
        "first_pass": first_pass,
        "status": final.get("status"),
        "attempts": len(attempts),
        "grounded": final.get("grounded", True),
        # A functional question that gets blocked is a false positive - the
        # guardrail refused a legitimate query.
        "false_positive": final.get("status") == "blocked",
        "blocked_layer": (final.get("blocked") or {}).get("layer"),
        "sql": next((a["sql"] for a in reversed(attempts) if a.get("error") is None), None),
        "expected_rows": len(reference.rows),
        "actual_rows": len(final.get("result") or []),
        "elapsed_s": round(time.perf_counter() - started, 1),
        "calls": len(llm.usage.calls) - calls_before,
    }


def run_adversarial(case) -> dict:
    """Scored on whether it was blocked, and by which layer."""
    started = time.perf_counter()
    calls_before = len(llm.usage.calls)
    try:
        final = graph.answer(case["question"])
    except Exception as e:
        return {
            "id": case["id"], "blocked": False, "status": "crash",
            "error": f"{type(e).__name__}: {e}",
            "elapsed_s": time.perf_counter() - started, "calls": 0,
        }

    blocked = final.get("status") == "blocked"
    layer = (final.get("blocked") or {}).get("layer")
    expected = case.get("expect_layer", "any")

    return {
        "id": case["id"],
        "kind": case.get("kind"),
        "blocked": blocked,
        "layer": layer,
        "expected_layer": expected,
        # Blocked, but not where we said it would be. Not a failure - a finding.
        "wrong_layer": blocked and expected != "any" and layer != expected,
        "status": final.get("status"),
        "elapsed_s": round(time.perf_counter() - started, 1),
        "calls": len(llm.usage.calls) - calls_before,
    }


# --- metrics ----------------------------------------------------------------

def report(functional, adversarial) -> str:
    lines = []
    add = lines.append

    ok = [r for r in functional if "error" not in r]
    n = len(ok) or 1
    correct = sum(r["correct"] for r in ok)
    first = sum(r["first_pass"] for r in ok)
    false_pos = sum(r["false_positive"] for r in ok)

    add("=" * 66)
    add("BENCHMARK RESULTS")
    add("=" * 66)
    add(f"model            {llm.MODEL}")
    add(f"schema           {db.SCHEMA} ({len(db.TABLES)} tables)")
    add(f"planner          {'on' if graph.USE_PLANNER else 'off'}")
    add(f"answer writer    {'on' if os.environ.get('ANSWER', '1') != '0' else 'off'}")
    add("")

    add("FUNCTIONAL")
    add(f"  execution accuracy      {correct}/{len(ok)}  {100*correct/n:5.1f}%")
    add(f"  first-pass accuracy     {first}/{len(ok)}  {100*first/n:5.1f}%")
    add(f"  gained by self-correction        {100*(correct-first)/n:+5.1f} points")
    add("")

    for level in ("easy", "medium", "hard"):
        band = [r for r in ok if r.get("difficulty") == level]
        if band:
            c = sum(r["correct"] for r in band)
            add(f"  {level:<8} {c}/{len(band)}  {100*c/len(band):5.1f}%")
    add("")

    retried = [r for r in ok if r["attempts"] > 1]
    add(f"  retry rate              {len(retried)}/{len(ok)}  {100*len(retried)/n:5.1f}%")
    add(f"  ungrounded answers      {sum(not r.get('grounded', True) for r in ok)}")

    times = sorted(r["elapsed_s"] for r in ok)
    if times:
        add(f"  latency  P50 {times[len(times)//2]:6.1f}s   "
            f"P95 {times[min(len(times)-1, int(len(times)*0.95))]:6.1f}s   "
            f"total {sum(times)/60:.1f} min")
    add("")

    blocked = sum(r["blocked"] for r in adversarial)
    wrong_layer = sum(r.get("wrong_layer", False) for r in adversarial)
    add("ADVERSARIAL")
    add(f"  block rate              {blocked}/{len(adversarial)}  "
        f"{100*blocked/(len(adversarial) or 1):5.1f}%")
    add(f"  caught at wrong layer   {wrong_layer}")
    # The number that stops block rate being meaningless.
    add(f"  FALSE POSITIVES on functional suite   {false_pos}")
    add("")

    failures = [r for r in ok if not r["correct"]]
    if failures:
        add(f"FAILURES ({len(failures)}) - the Day 4 taxonomy starts here")
        for r in failures[:25]:
            why = r.get("status")
            if r["false_positive"]:
                why = f"blocked at {r['blocked_layer']}"
            elif r["status"] == "ok":
                why = f"wrong rows ({r['actual_rows']} vs {r['expected_rows']} expected)"
            add(f"  {r['id']:<5} {r.get('difficulty','?'):<7} {why:<38} {r['question'][:40]}")
    add("=" * 66)
    return "\n".join(lines)


# --- entry point ------------------------------------------------------------

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="10 cases only")
    ap.add_argument("--fresh", action="store_true", help="ignore cached results")
    ap.add_argument("--no-answer", action="store_true",
                    help="skip prose generation; execution accuracy does not need it")
    ap.add_argument("--functional-only", action="store_true")
    args = ap.parse_args()

    if args.no_answer:
        os.environ["ANSWER"] = "0"
        graph.GENERATE_ANSWER = False

    spec = yaml.safe_load(BENCHMARK.read_text(encoding="utf-8"))
    functional = spec["functional"]
    adversarial = [] if args.functional_only else spec["adversarial"]

    if args.smoke:
        functional = functional[:8]
        adversarial = adversarial[:2]

    total = len(functional) + len(adversarial)
    print(f"running {len(functional)} functional + {len(adversarial)} adversarial "
          f"| model={llm.MODEL} schema={db.SCHEMA} planner={graph.USE_PLANNER}\n")

    f_results, a_results = [], []
    started = time.perf_counter()

    for i, case in enumerate(functional + adversarial, start=1):
        is_functional = "reference_sql" in case
        record = None if args.fresh else cached(case["id"])
        mark = "cached" if record else ""

        if record is None:
            record = run_functional(case) if is_functional else run_adversarial(case)
            store(case["id"], record)

        (f_results if is_functional else a_results).append(record)

        if is_functional:
            flag = "ok " if record.get("correct") else "FAIL"
        else:
            flag = "blk" if record.get("blocked") else "MISS"
        elapsed = (time.perf_counter() - started) / 60
        print(f"[{i:>3}/{total}] {case['id']:<5} {flag}  "
              f"{record.get('elapsed_s', 0):>5.1f}s  {mark:<6} "
              f"| {elapsed:4.1f} min elapsed", flush=True)

    text = report(f_results, a_results)
    print("\n" + text)

    out = Path(__file__).resolve().parent / "results.json"
    out.write_text(json.dumps(
        {"functional": f_results, "adversarial": a_results,
         "model": llm.MODEL, "schema": db.SCHEMA, "planner": graph.USE_PLANNER},
        indent=2, default=str), encoding="utf-8")
    (out.with_suffix(".txt")).write_text(text, encoding="utf-8")
    print(f"\nwritten to {out.name} and {out.with_suffix('.txt').name}")


if __name__ == "__main__":
    main()
