"""
executor.py — runs guarded SQL against the database and captures failures.

Two jobs, and the second one matters as much as the first:

  1. Run the query read-only, with a wall-clock ceiling.
  2. Turn any failure into an error *string* the generator can act on.

That second job is the self-correction loop. At temperature 0 the model
regenerates byte-identical SQL from an identical prompt, so a retry only
differs from the original attempt if something new enters the prompt. The
exception text is that something. `no such column: o.price` is what tells the
model to look at order_items instead of orders — a boolean "it failed" would
change nothing.

A note on the timeout, because the obvious approach does not work:

    sqlite3.connect(DB_PATH, timeout=5)   # NOT a query timeout

That parameter is the *busy-lock* timeout — how long the driver waits for a
lock held by another connection before giving up. A single slow query on an
unlocked database ignores it completely and runs to completion. A cartesian
join over order_items is 112,650^2 rows and would hang the agent until the
process was killed.

The mechanism that does work is set_progress_handler: SQLite calls back every
N virtual-machine instructions, and returning non-zero aborts the statement.
That gives a real ceiling on execution time regardless of what the query does.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Optional

import db

# Wall-clock ceiling for one query. Generous next to an 11s model call — the
# point is to catch pathological queries, not to make fast ones faster.
TIMEOUT_S = 10.0

# How often SQLite checks in with us. Low enough that the deadline is accurate,
# high enough that the callback is not itself a cost.
PROGRESS_INTERVAL = 10_000


@dataclass
class ExecResult:
    ok: bool
    rows: Optional[list] = None
    columns: Optional[list] = None
    error: Optional[str] = None
    elapsed_s: float = 0.0

    @property
    def row_count(self) -> int:
        return len(self.rows) if self.rows else 0


def run(sql: str, timeout_s: float = TIMEOUT_S) -> ExecResult:
    """
    Execute `sql` read-only. Never raises — a failure comes back as
    ok=False with `error` set, because the caller's job is to feed that
    text to the model, not to handle an exception.
    """
    started = time.perf_counter()
    conn = None

    try:
        conn = db.get_connection(read_only=True)

        deadline = started + timeout_s
        conn.set_progress_handler(
            lambda: 1 if time.perf_counter() > deadline else 0,
            PROGRESS_INTERVAL,
        )

        cursor = conn.execute(sql)
        rows = cursor.fetchall()
        columns = [c[0] for c in cursor.description] if cursor.description else []

        return ExecResult(
            ok=True,
            rows=rows,
            columns=columns,
            elapsed_s=time.perf_counter() - started,
        )

    except sqlite3.OperationalError as e:
        elapsed = time.perf_counter() - started
        # An aborted progress handler surfaces as a generic "interrupted",
        # which tells the model nothing useful. Say what actually happened.
        if "interrupted" in str(e).lower():
            return ExecResult(
                ok=False,
                error=(
                    f"query exceeded the {timeout_s:.0f}s time limit and was "
                    "cancelled; it is probably scanning far more rows than "
                    "needed - check the join conditions"
                ),
                elapsed_s=elapsed,
            )
        return ExecResult(ok=False, error=str(e), elapsed_s=elapsed)

    except sqlite3.Error as e:
        # Everything else the driver raises: type errors, integrity, etc.
        return ExecResult(
            ok=False,
            error=f"{type(e).__name__}: {e}",
            elapsed_s=time.perf_counter() - started,
        )

    finally:
        if conn is not None:
            conn.set_progress_handler(None, 0)
            conn.close()
