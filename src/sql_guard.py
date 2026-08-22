"""
sql_guard.py — the SQL execution guardrail.

This is the layer that stands between the model's output and the database.
It is a hard parse check, not a prompt instruction: the model is *asked* to
write only SELECTs in the system prompt, but asking is a request, not a
constraint. This module is the constraint.

Why parse instead of matching strings:

    SELECT * FROM orders WHERE status = 'DELETE'   <- a regex for "DELETE" fires
    SELECT 1; DROP TABLE orders;                   <- a regex may miss the second
                                                      statement entirely

Parsing understands structure; text matching guesses. Verified against both
of the above in test_sql_guard.py.

Four checks, in order of what they catch:

  1. It must parse at all. If sqlglot cannot read it, we cannot reason about
     what it does, so we refuse to run it.
  2. Exactly one statement. Stacked statements are the classic way to smuggle
     a second command past a check that only inspects the first.
  3. No write/DDL/command node anywhere in the tree — not just at the root.
  4. Every table referenced must be on the allowlist.

Check 4 is the one people leave out. `SELECT * FROM sqlite_master` is a
perfectly ordinary SELECT — it passes checks 1-3 — and it returns the schema
of every table in the database, including ones the agent was never meant to
see. SELECT-only is not the same as read-*permitted*.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import sqlglot
from sqlglot import exp

import db

DIALECT = "sqlite"

# Derived from the loaded schema rather than duplicated, so the guard cannot
# drift out of sync with the database - including under the Day 4 ablation,
# where INCLUDE_CUSTOMERS=0 must also make `customers` un-queryable. Anything
# outside it, including sqlite_master and the four Olist CSVs we chose not to
# load, is refused.
ALLOWED_TABLES = set(db.TABLES)

# Cost control, not security: stops one bad GROUP BY from streaming 112k rows
# into the prompt. Applied by rewriting the query, not by trusting the model.
MAX_ROWS = 1000

# Anything that writes, changes structure, or reaches outside the query.
# Checked across the whole tree, so a write nested inside a subquery or CTE
# is caught as readily as one at the root.
FORBIDDEN_NODES = (
    exp.Insert,
    exp.Update,
    exp.Delete,
    exp.Drop,
    exp.Create,
    exp.Alter,
    exp.TruncateTable,
    exp.Pragma,     # PRAGMA query_only = OFF would undo the read-only connection
    exp.Attach,     # ATTACH could bring a writable database into scope
    exp.Detach,
    exp.Command,    # sqlglot's catch-all for statements it does not model
)

# Only these can sit at the root. Set operations are reads, so UNION of two
# SELECTs is fine — its branches are checked by the tree walk like everything else.
ALLOWED_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except, exp.Subquery)


# System tables. Asking for these is never a model typo - sqlite_master
# returns the schema of everything, so a request for one is a probe.
SYSTEM_TABLE_PREFIX = "sqlite_"


@dataclass
class GuardResult:
    """
    ok=True  -> `sql` is the (possibly rewritten) query that is safe to run.
    ok=False -> `reason` says which check failed and why, for the user-facing
                refusal, the Langfuse trace, and the adversarial eval score.

    `category` decides what the agent does next, and the distinction matters
    for both metrics the guardrails are judged on:

      "security"    -> stop. No legitimate question makes the model emit DROP
                       or probe sqlite_master, so retrying burns calls and
                       lets a real attack look like a mere failure.
      "correctable" -> retry. A hallucinated table name or malformed SQL is an
                       ordinary model mistake; feeding the reason back usually
                       fixes it.

    Collapsing these into one behaviour costs you one metric or the other:
    always blocking turns a hallucinated column into a false positive on the
    functional suite, and always retrying means an attack is reported as a
    failed query rather than a blocked one.
    """
    ok: bool
    sql: Optional[str] = None
    reason: Optional[str] = None
    category: Optional[str] = None  # "security" | "correctable"


def check(sql: str) -> GuardResult:
    """Validate `sql`, and on success return it with a row limit applied."""

    # 1. does it parse
    try:
        statements = sqlglot.parse(sql, dialect=DIALECT)
    except Exception as e:
        return GuardResult(False, reason=f"could not be parsed as SQL: {e}",
                           category="correctable")

    statements = [s for s in statements if s is not None]
    if not statements:
        return GuardResult(False, reason="no SQL statement found",
                           category="correctable")

    # 2. exactly one statement
    if len(statements) > 1:
        return GuardResult(
            False,
            reason=f"expected one statement, found {len(statements)}; "
                   "stacked statements are not allowed",
            category="security",
        )

    tree = statements[0]

    # 3. no write or command node anywhere in the tree
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return GuardResult(
                False,
                reason=f"{type(node).__name__.upper()} is not permitted; "
                       "this agent may only read",
                category="security",
            )

    if not isinstance(tree, ALLOWED_ROOTS):
        return GuardResult(
            False,
            reason=f"statement type {type(tree).__name__.upper()} is not a read",
            category="security",
        )

    # 4. table allowlist
    #
    # CTE names look like tables to the parser (`WITH t AS (...) SELECT * FROM t`
    # gives a Table node named `t`), so collect the CTE aliases first and treat
    # them as legitimate — otherwise every valid CTE query gets refused.
    cte_names = {
        cte.alias_or_name.lower()
        for cte in tree.find_all(exp.CTE)
        if cte.alias_or_name
    }

    for table in tree.find_all(exp.Table):
        name = (table.name or "").lower()
        if not name or name in cte_names:
            continue
        if name not in ALLOWED_TABLES:
            probing_system_table = name.startswith(SYSTEM_TABLE_PREFIX)
            return GuardResult(
                False,
                reason=f"table '{table.name}' is not in the allowed schema "
                       f"({', '.join(sorted(ALLOWED_TABLES))})",
                category="security" if probing_system_table else "correctable",
            )

    return GuardResult(True, sql=_apply_row_limit(tree))


def _apply_row_limit(tree: exp.Expression) -> str:
    """
    Cap the number of rows returned.

    Rewriting the query is the point: a LIMIT the model was asked to include
    is a suggestion, a LIMIT we add to the parsed tree is a fact. An existing
    smaller limit is left alone, so "top 5 sellers" still returns five.
    """
    existing = tree.args.get("limit")
    if existing is not None:
        try:
            if int(existing.expression.name) <= MAX_ROWS:
                return tree.sql(dialect=DIALECT)
        except (AttributeError, ValueError):
            # A non-literal limit (an expression or parameter) is not something
            # we can compare against, so replace it with ours.
            pass

    return tree.limit(MAX_ROWS).sql(dialect=DIALECT)
