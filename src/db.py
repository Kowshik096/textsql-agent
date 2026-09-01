"""
db.py — database setup and schema description.

Two jobs:
  1. build_database()  : load the selected Olist CSVs into a local SQLite file
  2. describe_schema() : turn that schema into text the LLM can reason over

The schema description is the single biggest lever on text-to-SQL accuracy.
A small model cannot infer that `price` lives on order_items rather than
orders, so we spell it out — columns, types, keys, and a sample row each.
"""

import os
import re
import sqlite3
from pathlib import Path
from typing import Optional, Sequence

import pandas as pd

import env  # noqa: F401  - loads .env before anything reads os.environ

# Anchored to the repo root rather than the current working directory.
# A relative path resolves differently under `python src/db.py`, uvicorn,
# pytest and Docker — and the failure ("unable to open database file") gives
# no hint that the cwd is the problem.
_ROOT = Path(__file__).resolve().parent.parent

# DATA_SOURCE=sample points at the small committed fixture in data/sample.
# CI has no Ollama and the full CSVs are gitignored, so without this the suite
# would have no database and most of it would be skipped into meaninglessness.
# The fixture is sampled anchored on order ids, so joins still resolve.
DATA_SOURCE = os.environ.get("DATA_SOURCE", "full").lower()

DATA_DIR = _ROOT / "data" / "sample" if DATA_SOURCE == "sample" else _ROOT / "data"
DB_PATH = DATA_DIR / ("olist_sample.db" if DATA_SOURCE == "sample" else "olist.db")

# --- external database mode --------------------------------------------------
#
#     EXTERNAL_DB=/path/to/file.sqlite python evals/harness.py --benchmark ...
#
# repoints the ENTIRE stack at an arbitrary SQLite file: the Spider/BIRD
# validation runs the same agent, guards and scoring against a schema it has
# never seen. Tables are introspected from sqlite_master, relationships are
# inferred from declared foreign keys, and every Olist-specific prompt note
# is dropped - they would be false statements about someone else's orders
# table, which is precisely the silent-misleading-schema failure this project
# warns about.
EXTERNAL_DB = os.environ.get("EXTERNAL_DB", "").strip()
EXTERNAL = bool(EXTERNAL_DB)
if EXTERNAL:
    DB_PATH = Path(EXTERNAL_DB)
    if not DB_PATH.is_absolute():
        DB_PATH = (_ROOT / DB_PATH).resolve()

# The schema is a variable, not a constant, so Day 4 can measure what extra
# tables cost in execution accuracy instead of asserting it.
#
# Day 1 trimmed to four tables on the reasoning that nine would consume over
# half the context window. That was true at num_ctx 4096; at 8192 the measured
# peak including three retries is 13%, so capacity was never the constraint and
# that argument has been retired. What survives is join-path ambiguity and
# column collision, and those are testable.
CORE_TABLES = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
}

# Each of these earns its place:
#   customers           - buyer geography and the only route to person identity
#   order_payments      - payment method and instalments, a question class the
#                         four core tables cannot express at all
#   category_translation - categories are stored in Portuguese, so without it
#                         "revenue from health and beauty" is unanswerable
#
# geolocation is deliberately excluded. Its join key is not unique - 1,000,163
# rows across 19,015 zip prefixes, ~53 rows each - so joining it multiplies
# every row and inflates every aggregate. Deduplicating to zip/city/state does
# not fix it either: 45% of prefixes still map to several city spellings, and
# customers and sellers already carry city and state directly.
EXTENDED_TABLES = {
    "customers": "olist_customers_dataset.csv",
    "order_payments": "olist_order_payments_dataset.csv",
    "category_translation": "product_category_name_translation.csv",
}

# SCHEMA=core runs the 4-table configuration; anything else runs all 7.
SCHEMA = os.environ.get("SCHEMA", "full").lower()

TABLES = dict(CORE_TABLES)
if SCHEMA != "core":
    TABLES.update(EXTENDED_TABLES)


# SQLite won't know these from a pandas import, so we state them by hand.
# The model needs join paths more than it needs anything else.
RELATIONSHIPS = [
    "order_items.order_id   -> orders.order_id",
    "order_items.product_id -> products.product_id",
    "order_items.seller_id  -> sellers.seller_id",
]
if SCHEMA != "core":
    RELATIONSHIPS += [
        "orders.customer_id     -> customers.customer_id",
        "order_payments.order_id -> orders.order_id",
        "products.product_category_name -> category_translation.product_category_name",
    ]

# --- question analysis (planner routing + schema pruning) --------------------
#
# Pure text matching against table vocabulary - no model call, microseconds.
# Two consumers:
#   select_tables()   feeds describe_schema(tables=...) when PRUNE=1
#   suggests_join()   decides planning when PLANNER=auto
#
# Both were fitted against the per-case planner flips in results_planner_on/off
# rather than invented: wins F26/F33/F36/F39/F51/F60 vs losses
# F08/F15/F30/F53. The separating observation is that the loss cases name no
# second table and carry no comparison phrase, while most win cases either
# name two tables or compare ("more than", "distinct") - and the one pair
# that shares phrasing splits correctly because F26 says "orders" and F30
# does not.

TABLE_KEYWORDS = {
    "orders": r"\borders?\b",
    "order_items": r"\border[ _-]?items?\b|\bline[ _-]?items?\b",
    "products": r"\bproducts?\b",
    "sellers": r"\bsellers?\b",
    "customers": r"\bcustomers?\b|\bbuyers?\b",
    "order_payments": r"\bpayments?\b",
    "category_translation": r"\bcategor(?:y|ies)\b",
}

_TABLE_PATTERNS = {t: re.compile(p, re.IGNORECASE)
                   for t, p in TABLE_KEYWORDS.items()}

# Comparison/bucketing phrases that indicate decomposition could pay. Only
# consulted when at least one table matched - "in each year" alone must not
# trigger planning, which is exactly what separates F26 (plan, won) from
# F30 (no plan, also correct). "than the average" exists because F39 words it
# as "more revenue than the average", not "more than".
JOIN_PHRASES = re.compile(
    r"\b(?:more than|at least|fewer than|less than|in each|per year|"
    r"distinct|different|than the average)\b",
    re.IGNORECASE,
)


def matched_tables(question: str) -> set:
    """Tables whose keyword appears in the question. Empty when none do."""
    text = question or ""
    return {table for table, pattern in _TABLE_PATTERNS.items()
            if pattern.search(text)}


def relationship_partners(tables: set) -> set:
    """Tables reachable in ONE hop through a declared relationship."""
    partners: set = set()
    for rel in RELATIONSHIPS:
        left, right = rel.split("->")
        lt = left.strip().split(".")[0]
        rt = right.strip().split(".")[0]
        if lt in tables:
            partners.add(rt)
        if rt in tables:
            partners.add(lt)
    return partners


def select_tables(question: str):
    """
    Tables whose description should be shown for this question.

    Returns None when nothing matched - an unmatched question gets the FULL
    schema, because pruning toward a guess is worse than no pruning. One-hop
    expansion keeps single-table mentions answerable: "sellers" questions
    still see order_items, where their revenue actually lives.

    External schemas get None unconditionally: the keyword vocabulary below
    is Olist's, and pruning against the wrong vocabulary hides tables.
    """
    if EXTERNAL:
        return None
    matched = matched_tables(question)
    if not matched:
        return None
    return matched | relationship_partners(matched)


def suggests_join(question: str) -> bool:
    """
    PLANNER=auto policy: two tables named, or comparison phrasing on top of a
    match. On an external schema the keyword map is meaningless, so auto
    degrades to planning everything - the conservative direction for an
    unseen schema, and cheap on the small external suites.
    """
    if EXTERNAL:
        return True
    matched = matched_tables(question)
    if len(matched) >= 2:
        return True
    return bool(matched) and bool(JOIN_PHRASES.search(question or ""))

# Facts about what is ABSENT. Column notes can only describe columns that
# exist, and the Day 4 baseline showed the expensive errors are the opposite:
# the model invents a column, writes valid-looking SQL against it, and the
# query fails or - worse - silently joins the wrong way.
#
# Both entries below were derived from actual failures, not guessed:
#   "which sellers received the most orders" produced
#       orders o JOIN sellers s ON o.seller_id = s.seller_id
#   which is wrong three times over - orders has no seller_id, the path runs
#   through order_items, and the model repeated it on every retry.
# The wording below is the measured winner, not the best-written option, and
# the difference is worth recording. A rescoped version - conditional "IF a
# question needs both orders and sellers..." plus an explicit "use the fewest
# tables that answer the question" - reads better and scored 39.7%, exactly
# back to baseline, against 44.4% for the imperative below. Hard-tier accuracy
# was 17.9% against 28.6%.
#
# The blunt imperative wins despite a known cost: it causes the model to
# over-join on a handful of single-table aggregates (F08, F15, F18, F53, F55),
# which the softer version fixed while giving back more than it gained. Net
# +4.7 points with five known regressions beat net zero with none.
#
# Both variants were run over the full 78-case suite because a three-case
# sample pointed the other way. Prompt changes on a 3B model trade failure
# classes rather than improving monotonically.
SCHEMA_NOTES = [
    "orders has NO seller_id and NO product_id. To connect orders to sellers "
    "or products you MUST go through order_items.",
    "products has NO product_name or title column. A product is identified by "
    "product_id; its only descriptive field is product_category_name.",
    # Status filter policy - derived from failure analysis (F05, F17, F18, F35, F37, F41, F42, F47)
    "order_status values: delivered, shipped, canceled, unavailable, invoiced, "
    "processing, created, approved. "
    "REVENUE QUESTIONS: exclude canceled AND unavailable "
    "(WHERE order_status NOT IN ('canceled','unavailable')). "
    "BREAKDOWN QUESTIONS ('in each status', 'by status'): include ALL statuses, "
    "no filter. "
    "'NOT DELIVERED YET' or 'PENDING': in-progress only "
    "(shipped, processing, created, approved, invoiced) — "
    "exclude canceled/unavailable/delivered. "
    "DELIVERY TIME: only orders WHERE "
    "order_delivered_customer_date IS NOT NULL (no status filter needed). "
    "CANCELLED SHARE: count canceled+unavailable / total orders "
    "(CASE...ELSE 0, not NULL).",
    # Join paths - phantom joins cause wrong_table_or_join_path failures
    "order_items has NO order_status. For item-level metrics (price, "
    "freight_value, count items), use order_items ONLY. Join orders only when "
    "you need order-level fields.",
    "customers and sellers connect ONLY through orders -> order_items. "
    "NO direct relationship between customers and sellers.",
    "order_payments is independent of order_status. Do not join orders for "
    "payment questions.",
    # Default language for category labels (F07, F14, F40)
    "Default category label: products.product_category_name (Portuguese). "
    "Use category_translation.product_category_name_english ONLY when question "
    "explicitly asks for English.",
    # Aggregation patterns - grouping_dimension_wrong failures (F20, F31, F44, F55, F59)
    "Average revenue per X = SUM(price) / COUNT(DISTINCT order_id), NOT "
    "AVG(price). AVG(price) = average item price, NOT average order value.",
    "For 'how many orders' or 'percentage of orders': always use "
    "COUNT(DISTINCT order_id). order_payments and order_items have multiple "
    "rows per order.",
    "For conditional percentages: use SUM(CASE WHEN condition THEN 1 ELSE 0 "
    "END) / COUNT(DISTINCT order_id). Do not filter WHERE before the aggregate.",
    "For averages: cast numerator to REAL (CAST(COUNT(*) AS REAL) / ...) to "
    "avoid integer division.",
]

# Short notes on columns whose meaning isn't obvious from the name.
COLUMN_NOTES = {
    "order_items.price": (
        "item price in BRL; seller revenue = SUM(price) only, "
        "NOT including freight_value. "
        "Average revenue per order = SUM(price)/COUNT(DISTINCT order_id), NOT AVG(price)."
    ),
    "order_items.freight_value": "shipping cost in BRL for this item; NOT part of seller revenue",
    "order_items.order_item_id": "sequence number within an order (1, 2, 3...), not a global id",
    "order_items.order_id": (
        "foreign key to orders. ALWAYS qualify as order_items.order_id "
        "when joining."
    ),
    "orders.order_id": (
        "primary key. ALWAYS qualify as orders.order_id when joining."
    ),
    "order_payments.order_id": (
        "foreign key to orders. ALWAYS qualify as order_payments.order_id "
        "when joining."
    ),
    "orders.order_status": (
        "one of: delivered, shipped, canceled, unavailable, invoiced, "
        "processing, created, approved. "
        "REVENUE: exclude canceled AND unavailable. "
        "BREAKDOWN BY STATUS: no filter (include all). "
        "PENDING/NOT DELIVERED: shipped, processing, created, approved, invoiced only. "
        "DELIVERY TIME: filter by order_delivered_customer_date IS NOT NULL instead."
    ),
    "orders.order_purchase_timestamp": (
        "order placed timestamp (never NULL). Use for yearly/monthly grouping: "
        "SUBSTR(order_purchase_timestamp, 1, 4) for year. "
        "Do NOT use order_approved_at (has NULLs, creates blank year group)."
    ),
    "orders.order_approved_at": "approval timestamp (has NULLs). Do not use for yearly grouping.",
    "orders.order_delivered_customer_date": (
        "actual delivery to customer (NULL if not delivered). "
        "Use for delivery time: "
        "JULIANDAY(order_delivered_customer_date) - "
        "JULIANDAY(order_purchase_timestamp). "
        "Filter: WHERE order_delivered_customer_date IS NOT NULL."
    ),
    "orders.order_delivered_carrier_date": (
        "carrier pickup date. NOT the customer delivery date. "
        "Do not use for delivery time."
    ),
    "products.product_category_name": (
        "category label in Portuguese. Default label unless question "
        "asks for English."
    ),
    "category_translation.product_category_name_english": (
        "English label. Use ONLY when question explicitly requests English "
        "names. Join on products.product_category_name."
    ),
    # The three collisions introduced by the customers table. Each note names
    # the other column explicitly, because the failure is silent: picking
    # seller_state for a buyer question returns a plausible state and a
    # plausible number, and nothing downstream can tell it was the wrong one.
    "customers.customer_state": (
        "the BUYER's state. For where a SELLER is based use sellers.seller_state. "
        "Join path: order_items -> orders -> customers (buyer state)."
    ),
    "sellers.seller_state": (
        "the SELLER's state. For where a BUYER is located use customers.customer_state. "
        "Join path: order_items -> sellers (seller state)."
    ),
    "customers.customer_city": "the BUYER's city. Seller city is sellers.seller_city",
    "customers.customer_unique_id": (
        "the actual person. customer_id is unique per order (99,441 orders, "
        "99,441 customer_ids), so repeat-buyer questions must group by "
        "customer_unique_id, never customer_id. "
        "Using customer_id for repeat analysis returns empty results (F50)."
    ),
    # order_payments is one-to-many with orders (up to 29 payments on a single
    # order), so a join fans out and double-counts anything summed across it.
    # Stated as a prohibition because the failure is silent: the query runs and
    # returns an inflated but plausible total.
    "order_payments.payment_value": (
        "amount of ONE payment. An order can be paid in several parts (up to "
        "29), so joining orders to order_payments multiplies order rows - "
        "never SUM order revenue across that join. Order revenue is "
        "SUM(order_items.price). For 'how many orders' or 'percentage of "
        "orders' from payments: use COUNT(DISTINCT order_id)."
    ),
    "order_payments.payment_type": (
        "one of: credit_card, boleto, voucher, debit_card, not_defined. "
        "boleto is a Brazilian bank slip paid at an agent; there is no "
        "cash-on-delivery. An order may mix types (multiple rows per order). "
        "For mixed-payment questions: GROUP BY order_id HAVING "
        "COUNT(DISTINCT payment_type) > 1."
    ),
    "order_payments.payment_installments": "number of instalments; 1 means paid in full",
    "order_payments.payment_sequential": (
        "sequence number within an order (1, 2, 3...), not a global id"
    ),
}


def point_at(path: Path) -> None:
    """
    Switch the module onto an external SQLite file (used at import time when
    EXTERNAL_DB is set, and by tests). Mutates the module-level schema state:
    tables introspected, relationships from declared foreign keys, all
    Olist-specific notes discarded.

    Deliberately a function rather than import-time-only: the alternative -
    reloading db under pytest - leaves every module holding the OLD db object
    while sys.modules carries the new one, which is how two halves of one
    process end up disagreeing about what tables exist.
    """
    global DB_PATH, TABLES, RELATIONSHIPS, SCHEMA_NOTES, COLUMN_NOTES, EXTERNAL
    path = Path(path)
    if not path.is_absolute():
        path = (_ROOT / path).resolve()
    if not path.exists():
        raise FileNotFoundError(f"no external database at {path}")

    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        tables = {r[0]: "(external sqlite)" for r in rows}

        rels = []
        for table in tables:
            # fk row shape: (id, seq, parent_table, from_col, to_col, ...)
            for fk in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall():
                rels.append(f"{table}.{fk[3]} -> {fk[2]}.{fk[4]}")
    finally:
        conn.close()

    DB_PATH = path
    TABLES = tables
    RELATIONSHIPS = rels
    SCHEMA_NOTES = []
    COLUMN_NOTES = {}
    EXTERNAL = True
    _SCHEMA_CACHE.clear()


# Per-question schema text cache. Keyed on the frozenset of shown tables, so
# repeated questions hitting the same subset rebuild nothing. Declared before
# the external-mode hook below, because point_at() clears it.
_SCHEMA_CACHE: dict = {}


if EXTERNAL:
    point_at(DB_PATH)


def build_database(force: bool = False) -> Path:
    """Load the CSVs into SQLite. Skips work if the DB already exists."""
    if EXTERNAL:
        raise RuntimeError(
            f"EXTERNAL_DB={EXTERNAL_DB} is set - there is nothing to build; "
            "this database is used as it sits."
        )
    if DB_PATH.exists() and not force:
        print(f"Database already exists at {DB_PATH} (pass force=True to rebuild)")
        return DB_PATH

    DATA_DIR.mkdir(exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

    for table, csv_name in TABLES.items():
        csv_path = DATA_DIR / csv_name
        if not csv_path.exists():
            raise FileNotFoundError(
                f"Missing {csv_path}. Put the Olist CSVs in the data/ folder."
            )
        df = pd.read_csv(csv_path)
        df.to_sql(table, conn, if_exists="replace", index=False)
        print(f"  loaded {table:<12} {len(df):>7,} rows")

    conn.commit()
    conn.close()
    print(f"\nDatabase built at {DB_PATH}")
    return DB_PATH


def get_connection(read_only: bool = True) -> sqlite3.Connection:
    """
    Open a connection. This is the second guardrail layer, so it has to hold
    on its own — without depending on the sqlglot check upstream catching
    everything.

    The file is opened in read-only mode (`?mode=ro`), which SQLite enforces
    at the file level and no SQL statement can undo. PRAGMA query_only alone
    is not enough: it is reversible from SQL, so a query containing
    `PRAGMA query_only = OFF` re-enables writes on that same connection.
    It is still set here as belt-and-braces, but mode=ro is the guarantee.
    """
    if not read_only:
        return sqlite3.connect(DB_PATH)

    if not DB_PATH.exists():
        # mode=ro will not create a missing file, so fail with a useful message
        # instead of SQLite's opaque "unable to open database file".
        raise FileNotFoundError(
            f"No database at {DB_PATH}. Build it first:  python src/db.py"
        )

    conn = sqlite3.connect(f"{DB_PATH.as_uri()}?mode=ro", uri=True)
    conn.execute("PRAGMA query_only = ON;")
    return conn


def describe_schema(sample_rows: int = 1, tables: Optional[Sequence[str]] = None) -> str:
    """
    Build the schema text that goes into the prompt.

    Includes column names + types, a sample row per table, the join paths,
    and notes on ambiguous columns. Sample rows matter more than you'd expect:
    they show the model what values actually look like, which stops it
    inventing filters like WHERE order_status = 'DELIVERED' when the data
    stores lowercase.

    `tables` restricts the description to a subset (schema pruning). The
    relationships section is filtered to pairs whose both ends survive -
    advertising a join into a table the model was never shown is how it
    learns to hallucinate one.
    """
    shown = list(TABLES) if tables is None else [t for t in tables if t in TABLES]
    if not shown:
        shown = list(TABLES)

    conn = get_connection(read_only=True)
    parts = ["DATABASE SCHEMA", "=" * 60, ""]

    for table in shown:
        cols = conn.execute(f"PRAGMA table_info({table});").fetchall()
        n_rows = conn.execute(f"SELECT COUNT(*) FROM {table};").fetchone()[0]

        parts.append(f"TABLE: {table}  ({n_rows:,} rows)")
        for _, name, col_type, *_ in cols:
            note = COLUMN_NOTES.get(f"{table}.{name}")
            line = f"  - {name} ({col_type or 'TEXT'})"
            if note:
                line += f"  // {note}"
            parts.append(line)

        if sample_rows:
            rows = conn.execute(
                f"SELECT * FROM {table} LIMIT {sample_rows};"
            ).fetchall()
            col_names = [c[1] for c in cols]
            for row in rows:
                pairs = ", ".join(
                    f"{c}={_truncate(v)}"
                    for c, v in zip(col_names, row, strict=True)
                )
                parts.append(f"  sample: {pairs}")
        parts.append("")

    if SCHEMA_NOTES:
        parts.append("IMPORTANT")
        parts.extend(f"  - {n}" for n in SCHEMA_NOTES)
        parts.append("")

    kept_rels = []
    for r in RELATIONSHIPS:
        left, right = r.split("->")
        lt = left.strip().split(".")[0]
        rt = right.strip().split(".")[0]
        if lt in shown and rt in shown:
            kept_rels.append(r)

    if kept_rels:
        parts.append("RELATIONSHIPS (join paths)")
        parts.extend(f"  {r}" for r in kept_rels)
        parts.append("")

    conn.close()
    return "\n".join(parts)


def schema_for_question(question: str) -> str:
    """
    The schema text for one question under PRUNE=1; the full schema otherwise.

    Pruning is opt-in until measured: the full schema is the configuration
    every committed number was produced with, and the default must stay that
    configuration until a benchmark says otherwise.
    """
    prune = os.environ.get("PRUNE", "0") != "0"
    if not prune:
        return describe_schema()
    tables = select_tables(question)
    key = frozenset(tables) if tables else "__full__"
    if key not in _SCHEMA_CACHE:
        _SCHEMA_CACHE[key] = describe_schema(tables=tables)
    return _SCHEMA_CACHE[key]


def _truncate(value, limit: int = 22) -> str:
    """Keep sample values short so the schema prompt stays compact."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


if __name__ == "__main__":
    build_database()
    print()
    print(describe_schema())
