"""
component_metrics.py — component-level view of a benchmark run.

    python evals/component_metrics.py [results_file.json ...]

End-to-end accuracy answers "does the system work"; it cannot say WHICH part
broke. This script decomposes committed result files into per-component
rates - guard fires by layer, retry recovery, grounding, crashes, latency -
so a drop in the headline number lands on a named component.

Reads only recorded evidence; runs nothing. With no arguments it analyses
the committed evals/results.json; passing tagged files compares runs side
by side.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

EVALS = Path(__file__).resolve().parent
DEFAULTS = [EVALS / "results.json"]


def analyse(path: Path) -> dict:
    data = json.loads(path.read_text(encoding="utf-8"))
    functional = [r for r in data.get("functional", []) if "correct" in r]
    adversarial = data.get("adversarial", [])
    n = len(functional) or 1

    retried = [r for r in functional if r.get("attempts", 0) > 1]
    recovered = [r for r in retried if r["correct"]]
    blocked_fp = [r for r in functional if r.get("false_positive")]

    out = {
        "file": path.name,
        "model": data.get("model"),
        "planner": data.get("planner"),
        "cases": len(functional),
        "execution_accuracy": sum(r["correct"] for r in functional) / n,
        "first_pass": (sum(r.get("first_pass", False) for r in functional) / n),
        "retry_rate": len(retried) / n,
        "retry_recovery": (len(recovered) / len(retried)) if retried else None,
        "grounding_failures": sum(not r.get("grounded", True) for r in functional),
        "guard_false_positives": len(blocked_fp),
        "crashes": sum(r.get("status") == "crash" for r in functional),
        "wrong_rows": sum(r.get("status") == "ok" and not r["correct"]
                          for r in functional),
        "adversarial_blocked": sum(a.get("blocked", False) for a in adversarial),
        "adversarial_harmful": sum(a.get("harmful", False) for a in adversarial),
        "latency_p50": _pct([r["elapsed_s"] for r in functional], 50),
        "latency_p95": _pct([r["elapsed_s"] for r in functional], 95),
    }
    return out


def _pct(values: list, p: int) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    return s[min(len(s) - 1, int(len(s) * p / 100))]


def render(rows: list) -> str:
    labels = [
        ("model", "model"),
        ("planner", "planner"),
        ("cases", "functional cases"),
        ("execution_accuracy", "execution accuracy"),
        ("first_pass", "first-pass accuracy"),
        ("retry_rate", "retry rate"),
        ("retry_recovery", "retry recovery"),
        ("grounding_failures", "grounding failures"),
        ("guard_false_positives", "guard false positives"),
        ("crashes", "crashes"),
        ("wrong_rows", "wrong rows (ran, incorrect)"),
        ("adversarial_blocked", "adversarial blocked"),
        ("adversarial_harmful", "adversarial harmful"),
        ("latency_p50", "latency p50 (s)"),
        ("latency_p95", "latency p95 (s)"),
    ]
    lines = []

    def fmt(v) -> str:
        if v is None:
            return "-"
        if isinstance(v, float):
            return f"{v:.1%}" if v <= 1.5 else f"{v:.1f}"
        return str(v)

    width = max(_label_len(pair) for pair in labels)
    for key, label in labels:
        cells = "  ".join(f"{fmt(r.get(key)):>12}" for r in rows)
        lines.append(f"{label:<{width}}  {cells}")
    header = " " * width + "  " + "  ".join(f"{r['file'][:12]:>12}"
                                            for r in rows)
    return header + "\n" + "\n".join(lines)


def _label_len(pair) -> int:
    return len(pair[1])


def main() -> int:
    paths = [Path(a) for a in sys.argv[1:]] or DEFAULTS
    missing = [p for p in paths if not p.exists()]
    if missing:
        print(f"missing: {', '.join(m.name for m in missing)}")
        return 1
    rows = [analyse(p) for p in paths]
    print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
