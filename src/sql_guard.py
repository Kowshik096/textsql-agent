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

DIALECT = "sqlite"

# The four tables the agent is allowed to touch. Anything else — including
# sqlite_master and any table a future migration adds — is refused.
ALLOWED_TABLES = {"orders", "order_items", "products", "sellers"}

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


@dataclass
class GuardResult:
    """
    ok=True  -> `sql` is the (possibly rewritten) query that is safe to run.
    ok=False -> `reason` says which check failed and why, for the user-facing
                refusal, the Langfuse trace, and the adversarial eval score.
    """
    ok: bool
    sql: Optional[str] = None
    reason: Optional[str] = None


def check(sql: str) -> GuardResult:
    """Validate `sql`, and on success return it with a row limit applied."""

    # 1. does it parse
    try:
        statements = sqlglot.parse(sql, dialect=DIALECT)
    except Exception as e:
        return GuardResult(False, reason=f"could not be parsed as SQL: {e}")

    statements = [s for s in statements if s is not None]
    if not statements:
        return GuardResult(False, reason="no SQL statement found")

    # 2. exactly one statement
    if len(statements) > 1:
        return GuardResult(
            False,
            reason=f"expected one statement, found {len(statements)}; "
                   "stacked statements are not allowed",
        )

    tree = statements[0]

    # 3. no write or command node anywhere in the tree
    for node in tree.walk():
        if isinstance(node, FORBIDDEN_NODES):
            return GuardResult(
                False,
                reason=f"{type(node).__name__.upper()} is not permitted; "
                       "this agent may only read",
            )

    if not isinstance(tree, ALLOWED_ROOTS):
        return GuardResult(
            False,
            reason=f"statement type {type(tree).__name__.upper()} is not a read",
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
            return GuardResult(
                False,
                reason=f"table '{table.name}' is not in the allowed schema "
                       f"({', '.join(sorted(ALLOWED_TABLES))})",
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
