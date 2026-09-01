"""
taxonomy_progress.py — how far along the 1b review is.

    python evals/taxonomy_progress.py

Counts `final:` lines in evals/FAILURES.md: decided vs still open, and lists
the open case ids so the remaining work has a name.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

FILE = Path(__file__).resolve().parent / "FAILURES.md"


def main() -> int:
    if not FILE.exists():
        print("no evals/FAILURES.md yet - run dump_failures.py first")
        return 1

    text = FILE.read_text(encoding="utf-8")
    cases = re.findall(r"^### (F\d+)", text, re.M)

    open_ids = []
    for cid in cases:
        block = re.search(
            rf"### {cid}.*?^final:\s*(.*?)$", text, re.M | re.S)
        if block and ("your call" in block.group(1) or not block.group(1)):
            open_ids.append(cid)

    decided = len(cases) - len(open_ids)
    print(f"1b review: {decided}/{len(cases)} decided, {len(open_ids)} open")
    if open_ids:
        print("open:", ", ".join(open_ids))
    else:
        print("complete - ready for schema-note fixes and the campaign block")
    return 0


if __name__ == "__main__":
    sys.exit(main())
