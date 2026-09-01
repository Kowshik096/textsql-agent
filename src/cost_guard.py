"""
cost_guard.py — the fifth guardrail: refuse cartesian-cost queries BEFORE
they burn the executor's 10-second ceiling.

The documented limitation this attacks (README "What this is not",
DECISIONS #25): the 10s timeout can kill *correct* SQL — and when it does,
the retry loop burns three model calls re-asking for a query that will
always be too slow. The timeout diagnoses nothing. EXPLAIN QUERY PLAN
diagnoses the shape without running anything.

The signal is nested full scans over large tables. SQLite prints one plan
line per loop; "SCAN t" means read every row of t. Two or more such lines
on large tables means row-count multiplication — the 1.4-quadrillion-row
cartesian join from Day 2 shows up as exactly this. Equi-joins get
automatic transient indexes from SQLite and appear as SEARCH, so ordinary
joins pass; a cartesian has no join term to index and cannot hide.

Deliberately conservative: a SINGLE full scan is fine (SQLite reads 112k
rows in well under a second), and small-table scans are always fine.
Refusals are category "correctable" — the model can add the missing join
condition, which is precisely what the retry prompt is for.

Env: COST_GUARD=0 disables the check entirely (default on). Like every
behaviour switch, it participates in the harness cache key so an ablation
cannot silently reuse guarded results.
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from typing import Optional

import db

# Tables at or above this row count count toward the cartesian signature.
# order_items (112k) and orders (99k) sit far above it; sellers (3k) and
# everything smaller sit far below - which matches where the real failures
# came from.
LARGE_TABLE_MIN = 50_000

_SCAN_RE = re.compile(r"SCAN\s+([A-Za-z_][\w]*)")


@dataclass
class CostVerdict:
    ok: bool
    reason: Optional[str] = None
    large_scans: int = 0


def _row_counts(conn: sqlite3.Connection) -> dict:
    """Table -> row count, read once per call. External schemas included."""
    counts = {}
    for table in db.TABLES:
        try:
            counts[table] = conn.execute(
                f"SELECT COUNT(*) FROM \"{table}\"").fetchone()[0]
        except sqlite3.Error:
            counts[table] = 0
    return counts


def _plan_lines(conn: sqlite3.Connection, sql: str) -> list:
    try:
        return conn.execute(f"EXPLAIN QUERY PLAN {sql}").fetchall()
    except sqlite3.Error:
        # A query that cannot be explained cannot be executed either; the
        # SQL guard or the executor owns that failure, not this one.
        return []


def check(sql: str, conn: Optional[sqlite3.Connection] = None) -> CostVerdict:
    """
    Return ok=False for plans whose full scans over large tables nest.

    `conn` is injectable for tests; production passes a read-only connection
    so the check itself can never write.
    """
    if os.environ.get("COST_GUARD", "1") == "0":
        return CostVerdict(ok=True)

    own = conn is None
    if own:
        # get_connection never returns None; the assert only teaches the
        # type checker what the branch guarantees.
        conn = db.get_connection(read_only=True)
    assert conn is not None
    try:
        counts = _row_counts(conn)
        large = {t for t, n in counts.items() if n >= LARGE_TABLE_MIN}

        large_scans = set()
        for row in _plan_lines(conn, sql):
            detail = row[3] if len(row) > 3 else str(row)
            for match in _SCAN_RE.finditer(detail):
                if match.group(1).lower() in large:
                    large_scans.add(match.group(1).lower())

        if len(large_scans) >= 2:
            names = ", ".join(sorted(large_scans))
            return CostVerdict(
                ok=False,
                reason=(
                    f"the plan fully scans {names} in nested loops, which is "
                    "a cartesian join over very large tables; it will exceed "
                    "the execution time limit. Add or fix the JOIN condition "
                    "so each table is looked up by key rather than scanned."
                ),
                large_scans=len(large_scans),
            )
        return CostVerdict(ok=True, large_scans=len(large_scans))
    finally:
        if own:
            conn.close()
