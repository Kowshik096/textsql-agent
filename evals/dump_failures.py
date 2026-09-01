"""
dump_failures.py — turn the benchmark failures into reviewable evidence.

    python evals/dump_failures.py                  # offline, from results.json
    python evals/dump_failures.py --live-missing   # re-run no-SQL cases live

Reads evals/results.json + evals/benchmark.yaml, re-runs the reference SQL
(and the agent's recorded SQL, if any) against the database, and writes
evals/FAILURES.md: a proposed-category histogram plus one evidence block per
failure.

The categories are MECHANICAL HINTS, not verdicts. A script can see that two
queries touch different tables or disagree about a status filter; it cannot
see which of them answers the question. Every case carries a `final:` field
for the human call — the histogram is the starting point for that review, not
its replacement.

The five retry-exhaustion cases store no SQL (the harness keeps only
successful statements), so --live-missing asks the model again to capture
what those attempts looked like. First attempts are reproducible at
temperature 0; retries run at 0.3 and may differ from the original run.
"""

from __future__ import annotations

import argparse
import collections
import datetime
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))

# Blank BEFORE any src import: env.py's load_dotenv(override=False) will not
# overwrite a key that already exists, so an empty string beats the .env value
# and the live reruns stay out of the real Langfuse project.
os.environ["LANGFUSE_PUBLIC_KEY"] = ""
os.environ["LANGFUSE_SECRET_KEY"] = ""

import sqlglot                                          # noqa: E402
from sqlglot import exp                                 # noqa: E402
import yaml                                             # noqa: E402

import executor                                         # noqa: E402

BENCH = Path(__file__).resolve().parent / "benchmark.yaml"
RESULTS = Path(__file__).resolve().parent / "results.json"
OUT = Path(__file__).resolve().parent / "FAILURES.md"

STATUS_TOKENS = ("canceled", "unavailable", "delivered", "shipped",
                 "invoiced", "processing", "created", "approved")

CATEGORY_LEGEND = {
    "retry_exhaustion":
        "all attempts failed; no SQL ever executed (see captured attempts)",
    "wrong_table_or_join_path":
        "generated and reference queries touch different table sets",
    "payments_fanout":
        "generated joins order_payments and the reference does not - "
        "one-to-many join inflating the aggregate",
    "identity_collision":
        "generated groups by customer_id while the reference groups by "
        "customer_unique_id (per-order id vs the actual person)",
    "label_presentation_mismatch":
        "numbers identical, labels differ - possibly a CORRECT answer scored "
        "wrong by exact-match (e.g. English vs Portuguese names)",
    "missing_outer_aggregation":
        "reference expects one number, generated returned grouped rows",
    "empty_result_overconstrained":
        "generated query returned zero rows where the reference has rows",
    "wrong_status_filter":
        "disagreement on order_status filtering (canceled/unavailable/delivered)",
    "freight_in_aggregate":
        "generated includes freight_value where the reference does not "
        "(revenue is SUM(price) only)",
    "count_granularity":
        "COUNT(*) where the reference counts DISTINCT ids (line items vs orders)",
    "date_basis":
        "different timestamp column or date-part arithmetic",
    "aggregation_value_mismatch":
        "same grouping labels, different numbers - metric/filter semantics",
    "grouping_dimension_wrong":
        "row labels do not overlap - grouped by something else entirely",
    "scalar_semantic_mismatch":
        "single number disagrees with no structural signal found",
    "crash":
        "agent raised; see error in the case block",
    "needs_human_review":
        "no mechanical signal fired - read the two queries side by side",
}


# --- parsing helpers ---------------------------------------------------------

def _tree(sql: str):
    try:
        return sqlglot.parse_one(sql, dialect="sqlite")
    except Exception:
        return None


def _tables(tree) -> set:
    if tree is None:
        return set()
    ctes = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}
    return {t.name.lower() for t in tree.find_all(exp.Table)
            if t.name and t.name.lower() not in ctes}


def _aggs(tree) -> set:
    if tree is None:
        return set()
    return {type(f).__name__ for f in tree.find_all(exp.AggFunc)}


def _group_by(tree) -> list:
    g = tree.find(exp.Group) if tree else None
    return [e.sql(dialect="sqlite").lower() for e in g.expressions] if g else []


def _limit(tree) -> int | None:
    if tree is None:
        return None
    lim = tree.args.get("limit")
    if lim is None:
        return None
    try:
        return int(lim.expression.name)
    except (AttributeError, ValueError):
        return None


def _has(sql: str, *tokens: str) -> bool:
    low = sql.lower()
    return any(t in low for t in tokens)


def status_tokens(sql: str) -> set:
    low = sql.lower()
    return {t for t in STATUS_TOKENS if f"'{t}'" in low}


# --- signal collection -------------------------------------------------------

def collect_signals(gen_sql: str | None, ref_sql: str,
                    exp_rows: list, act_rows: list | None) -> tuple[list, str]:
    """Return (human-readable signals, proposed category)."""
    sig: list[str] = []
    gt = _tree(gen_sql or "")
    rt = _tree(ref_sql)
    g_tables, r_tables = _tables(gt), _tables(rt)

    if gen_sql:
        if g_tables != r_tables:
            sig.append(f"table sets differ: gen={sorted(g_tables)} "
                       f"ref={sorted(r_tables)}")
        if "order_payments" in _has_join_tables(gt) - _has_join_tables(rt):
            sig.append("generated joins order_payments, reference does not "
                       "(one-to-many fan-out)")
        g_status, r_status = status_tokens(gen_sql), status_tokens(ref_sql)
        if g_status != r_status:
            sig.append(f"status filters differ: gen={sorted(g_status)} "
                       f"ref={sorted(r_status)}")
        if _has(gen_sql, "freight_value") and not _has(ref_sql, "freight_value"):
            sig.append("generated includes freight_value, reference does not")
        if _has(ref_sql, "customer_unique_id") and \
           _has(gen_sql, "customer_id") and \
           not _has(gen_sql, "customer_unique_id"):
            sig.append("reference groups by customer_unique_id, generated "
                       "uses customer_id")
        g_cnt_star = bool(_has(gen_sql, "count(*)"))
        r_cnt_distinct = bool(_has(ref_sql, "count(distinct"))
        if g_cnt_star and r_cnt_distinct:
            sig.append("COUNT(*) vs reference COUNT(DISTINCT ...)")
        for token, label in (("julianday", "julianday arithmetic"),
                             ("strftime", "strftime date part")):
            if _has(gen_sql, token) != _has(ref_sql, token):
                sig.append(f"{label} present in "
                           f"{'generated' if _has(gen_sql, token) else 'reference'} only")
        g_agg, r_agg = _aggs(gt), _aggs(rt)
        if g_agg != r_agg:
            sig.append(f"aggregate functions differ: gen={sorted(g_agg)} "
                       f"ref={sorted(r_agg)}")
        g_group, r_group = _group_by(gt), _group_by(rt)
        if g_group != r_group:
            sig.append(f"GROUP BY differs: gen={g_group} ref={r_group}")
        glim, rlim = _limit(gt), _limit(rt)
        if glim != rlim and None not in (glim, rlim):
            sig.append(f"LIMIT differs: gen={glim} ref={rlim}")

    # Row-shape signals, independent of SQL parsing.
    if act_rows is not None and exp_rows:
        if len(exp_rows) == 1 and len(exp_rows[0]) == 1 and len(act_rows) > 1:
            sig.append("reference expects a single number, generated returned "
                       f"{len(act_rows)} grouped rows")
        if not act_rows:
            sig.append("generated query returns zero rows where the "
                       f"reference has {len(exp_rows)}")
        el, al = _labels(exp_rows), _labels(act_rows)
        # Presentation mismatch needs a real label column to compare against
        # the values. Single-column rows have an empty tail, so the tail
        # comparison would be trivially equal for any two results of the
        # same length - which would call 625-vs-1234 a "presentation" issue.
        if (len(exp_rows) == len(act_rows) and el != al and el and al
                and len(exp_rows[0]) >= 2):
            tail_e = sorted(tuple(r[1:]) for r in exp_rows)
            tail_a = sorted(tuple(r[1:]) for r in act_rows)
            if tail_e == tail_a:
                sig.append("non-label values identical; only the label "
                           "column differs (possibly correct answer scored "
                           "wrong by exact match)")

    cat = _propose(sig, g_tables, r_tables, exp_rows, act_rows, bool(gen_sql))
    return sig, cat


def _has_join_tables(tree) -> set:
    return _tables(tree)


def _labels(rows) -> set:
    return {str(r[0]).strip().lower() for r in (rows or [])}


def _propose(sig: list, g_tables: set, r_tables: set,
             exp_rows: list, act_rows: list | None, has_sql: bool) -> str:
    if not has_sql:
        return "retry_exhaustion"

    def fired(prefix: str) -> bool:
        return any(s.startswith(prefix) for s in sig)

    # Most specific first. A specific signal (identity collision, label
    # mismatch) says more than a generic one (table sets differ), so the
    # generic check must not shadow it.
    if fired("reference groups by customer_unique_id"):
        return "identity_collision"
    if fired("generated joins order_payments"):
        return "payments_fanout"
    if fired("non-label values identical"):
        return "label_presentation_mismatch"
    if fired("single number, generated returned"):
        return "missing_outer_aggregation"
    if fired("zero rows where"):
        return "empty_result_overconstrained"
    if fired("table sets differ"):
        return "wrong_table_or_join_path"
    if fired("status filters differ"):
        return "wrong_status_filter"
    if fired("GROUP BY differs") or fired("aggregate functions differ"):
        return "grouping_dimension_wrong"
    if fired("generated includes freight_value"):
        return "freight_in_aggregate"
    if fired("COUNT(*)"):
        return "count_granularity"
    if fired("julianday") or fired("strftime"):
        return "date_basis"
    if act_rows is not None and exp_rows is not None:
        el, al = _labels(exp_rows), _labels(act_rows)
        if el and al and el == al and len(exp_rows) == len(act_rows):
            return "aggregation_value_mismatch"
        if el and al and not (el & al):
            return "grouping_dimension_wrong"
        if len(exp_rows) == 1 and len(act_rows) == 1:
            return "scalar_semantic_mismatch"
    return "needs_human_review"


# --- rendering ---------------------------------------------------------------

def _rows_preview(rows, n: int = 4) -> str:
    if rows is None:
        return "(not executed)"
    if not rows:
        return "(empty)"
    head = rows[:n]
    lines = [", ".join("" if c is None else str(c)[:28] for c in r)
             for r in head]
    more = "" if len(rows) <= n else f"\n  ... ({len(rows)} rows total)"
    return "\n".join(f"  {line}" for line in lines) + more


def render_case(case: dict, rec: dict, live_attempts: list | None) -> list:
    out = []
    cid = case["id"]
    q = case.get("question", "?")
    diff = case.get("difficulty", "?")
    status = rec.get("status", "?")
    attempts = rec.get("attempts", 0)
    grounded = rec.get("grounded")
    out.append(f"### {cid} \u00b7 {diff} \u00b7 \"{q}\"")
    meta = f"status={status} \u00b7 attempts={attempts}"
    if grounded is not None:
        meta += f" \u00b7 grounded={'yes' if grounded else 'NO'}"
    out.append(meta + "\n")

    gen_sql = rec.get("sql")

    if "error" in rec:
        out.append(f"**record error**: {rec['error']}\n")

    if live_attempts:
        out.append("**captured attempts** (live rerun; retry temp 0.3):\n")
        for i, a in enumerate(live_attempts, 1):
            err = a.get("error")
            out.append(f"Attempt {i}: " + (err or "ok"))
        out.append("")

    exp_ref = executor.run(" ".join(case["reference_sql"].split()), timeout_s=90)
    exp_rows = exp_ref.rows if exp_ref.ok else None
    act_rows = None
    if gen_sql:
        got = executor.run(gen_sql, timeout_s=90)
        act_rows = got.rows if got.ok else None

    if gen_sql:
        out.append("**generated**\n```sql\n" + gen_sql.rstrip() + "\n```")
    else:
        out.append("**generated**: (none recorded)")
    out.append("")
    out.append("**reference**\n```sql\n"
               + " ".join(case["reference_sql"].split()) + "\n```")
    out.append("")

    sig, cat = collect_signals(gen_sql, case["reference_sql"],
                               exp_rows, act_rows)
    out.append(f"rows: expected {_shape(exp_rows)} \u00b7 "
               f"actual {_shape(act_rows)}")
    out.append("\n**expected**\n```\n" + _rows_preview(exp_rows) + "\n```")
    out.append("**actual**\n```\n" + _rows_preview(act_rows) + "\n```")
    out.append("signals: " + ("; ".join(sig) if sig else "(none fired)"))
    out.append("")
    out.append(f"proposed: **{cat}** \u2014 {CATEGORY_LEGEND[cat]}")
    out.append("")
    out.append("final: <!-- your call -->")
    out.append("---")
    return out


def _shape(rows) -> str:
    if rows is None:
        return "\u2014"
    if not rows:
        return "0 rows"
    return f"{len(rows)} row(s){'' if len(rows) > 1 or len(rows[0]) < 2 else ''}"


# --- live capture ------------------------------------------------------------

def live_capture(question: str) -> list:
    """Re-run one question through the agent to capture its failed attempts."""
    import graph
    final = graph.answer(question)
    return final.get("attempts") or []


# --- main --------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--live-missing", action="store_true",
                    help="re-run retry-exhaustion cases through the agent "
                         "to capture their attempts (needs Ollama)")
    args = ap.parse_args()

    spec = yaml.safe_load(BENCH.read_text(encoding="utf-8"))
    data = json.loads(RESULTS.read_text(encoding="utf-8"))
    by_id = {r["id"]: r for r in data["functional"]}
    failures = [(c, by_id[c["id"]]) for c in spec["functional"]
                if not by_id[c["id"]].get("correct")]

    lines = [
        "# Failure taxonomy \u2014 draft for human review",
        "",
        f"Generated {datetime.date.today().isoformat()} by "
        "`evals/dump_failures.py` from `evals/results.json` "
        f"({data.get('model')}, schema={data.get('schema')}, "
        f"planner={'on' if data.get('planner') else 'off'}).",
        "",
        f"**{len(failures)} failures / {len(spec['functional'])} functional.** "
        "Proposed categories are mechanical hints fired off the two queries "
        "and their rows \u2014 they say what *differs*, not which query is "
        "right. Confirm every `final:` line by reading the pair yourself.",
        "",
    ]

    rendered = []
    counter = collections.Counter()
    tier_counter = collections.defaultdict(collections.Counter)

    for case, rec in failures:
        attempts = None
        if args.live_missing and rec.get("status") == "failed" \
                and not rec.get("sql"):
            print(f"[live] {case['id']} \u2014 capturing attempts...",
                  flush=True)
            try:
                attempts = live_capture(case["question"])
                if attempts:
                    last = attempts[-1]
                    if last.get("error") is None:
                        rec = dict(rec, sql=last["sql"])
                        attempts = attempts[:-1]
            except Exception as e:
                print(f"[live] {case['id']} failed to rerun: {e}", flush=True)

        block = render_case(case, rec, attempts)
        cat = "needs_human_review"
        for line in reversed(block):
            if line.strip().startswith("proposed: **"):
                cat = line.split("**")[1]
                break
        counter[cat] += 1
        tier_counter[cat][case.get("difficulty", "?")] += 1
        rendered.append((case["id"], block))
        print(f"  {case['id']:<5} {cat}")

    lines.append("## Histogram (proposed)")
    lines.append("")
    lines.append("| proposed category | easy | medium | hard | total |")
    lines.append("|---|---|---|---|---|")
    for cat, n in counter.most_common():
        t = tier_counter[cat]
        lines.append(f"| `{cat}` | {t.get('easy', 0)} | {t.get('medium', 0)} "
                     f"| {t.get('hard', 0)} | {n} |")
    lines.append("")
    lines.append("## Category legend")
    lines.append("")
    for cat in counter:
        lines.append(f"- `{cat}` \u2014 {CATEGORY_LEGEND[cat]}")
    lines.append("")
    lines.append("---")
    lines.append("")
    lines.append("## Cases")
    lines.append("")
    for _, block in sorted(rendered):
        lines.extend(block)
        lines.append("")

    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nwrote {OUT}")
    print("\nproposed distribution:")
    for cat, n in counter.most_common():
        print(f"  {n:>3}  {cat}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
