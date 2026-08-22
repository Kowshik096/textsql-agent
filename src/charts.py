"""
charts.py — render a chart when the result set is worth looking at.

The interesting part is not the plotting, it is deciding when *not* to plot.
An agent that emits a chart for every query produces a bar chart of one bar
for "how many sellers are there", and a 1,000-category axis nobody can read
for anything ungrouped. Both are worse than no chart, because they cost the
reader time before they can dismiss them.

So the rule is narrow and stated up front: a result is chartable when it has
one label column, at least one numeric column, and between 2 and 25 rows.
Everything else returns None and the caller shows the table.

Rendering happens through the Agg backend - no display server is involved,
which matters because this has to work inside a container on Day 5 and in CI
where there is no screen at all.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional, Sequence

import matplotlib

matplotlib.use("Agg")          # must precede pyplot; no display, no window
import matplotlib.pyplot as plt        # noqa: E402

CHART_DIR = Path(__file__).resolve().parent.parent / "charts"

# Below 2 rows there is nothing to compare; above 25 the labels overlap into
# an unreadable smear and a table serves the reader better.
MIN_ROWS = 2
MAX_ROWS = 25

# Labels longer than this are truncated - seller ids are 32-character hashes
# and would otherwise consume the whole figure.
LABEL_CHARS = 22

_DATE_LIKE = re.compile(r"^\d{4}(-\d{2}){0,2}$")


def is_chartable(columns: Sequence[str], rows: Sequence) -> bool:
    """One label column, one numeric column, a readable number of rows."""
    if not rows or not columns or len(columns) < 2:
        return False
    if not (MIN_ROWS <= len(rows) <= MAX_ROWS):
        return False
    return _numeric_index(rows) is not None


def _numeric_index(rows) -> Optional[int]:
    """The first column after the label that is numeric in every row."""
    for i in range(1, len(rows[0])):
        if all(isinstance(r[i], (int, float)) and not isinstance(r[i], bool) for r in rows):
            return i
    return None


def _looks_like_a_series(labels) -> bool:
    """
    Ordered periods - 2017, 2017-05 - read better as a line than as bars,
    because the eye is being asked about a trend rather than a ranking.
    """
    return all(_DATE_LIKE.match(str(l)) for l in labels)


def render(question: str, columns: Sequence[str], rows: Sequence,
           out_dir: Path = CHART_DIR) -> Optional[str]:
    """
    Draw the result, or return None if it is not worth drawing.

    Returns the path to a PNG. Never raises - a chart is a nicety, and an
    agent that fails a correct query because matplotlib had an opinion about
    a font is worse than an agent with no charts.
    """
    try:
        if not is_chartable(columns, rows):
            return None

        value_index = _numeric_index(rows)
        labels = [str(r[0])[:LABEL_CHARS] for r in rows]
        values = [float(r[value_index]) for r in rows]

        out_dir.mkdir(exist_ok=True)
        fig, ax = plt.subplots(figsize=(8, 4.5), dpi=110)

        if _looks_like_a_series(labels):
            ax.plot(labels, values, marker="o", linewidth=2)
            ax.grid(axis="y", alpha=0.3)
        else:
            ax.barh(labels[::-1], values[::-1])       # largest at the top
            ax.grid(axis="x", alpha=0.3)

        ax.set_xlabel(columns[value_index])
        ax.set_title(question[:80], fontsize=11)
        ax.spines[["top", "right"]].set_visible(False)
        fig.tight_layout()

        path = out_dir / f"{_slug(question)}.png"
        fig.savefig(path)
        plt.close(fig)
        return str(path)

    except Exception:
        # Deliberately swallowed. See the docstring.
        plt.close("all")
        return None


def _slug(text: str, limit: int = 48) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "chart").lower()).strip("_")
    return (slug[:limit] or "chart")
