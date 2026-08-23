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
import sqlite3
from pathlib import Path

import pandas as pd

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
SCHEMA_NOTES = [
    "Use the fewest tables that answer the question. If every column you need "
    "is in one table, query that table alone - an unnecessary JOIN silently "
    "drops rows that do not match and changes the answer.",
    "orders does not contain seller_id or product_id. IF a question needs both "
    "orders and sellers, or orders and products, join through order_items.",
    "products has no product_name or title column. A product is identified by "
    "product_id; its only descriptive field is product_category_name.",
]

# Short notes on columns whose meaning isn't obvious from the name.
COLUMN_NOTES = {
    "order_items.price": "item price in BRL; seller revenue = SUM(price) only, NOT including freight_value",
    "order_items.freight_value": "shipping cost in BRL for this item; NOT part of seller revenue",
    "order_items.order_item_id": "sequence number within an order (1, 2, 3...), not a global id",
    "orders.order_status": "one of: delivered, shipped, canceled, unavailable, invoiced, processing, created, approved. Exclude 'canceled' and 'unavailable' from revenue questions.",
    "products.product_category_name": "category label in Portuguese",
    # The three collisions introduced by the customers table. Each note names
    # the other column explicitly, because the failure is silent: picking
    # seller_state for a buyer question returns a plausible state and a
    # plausible number, and nothing downstream can tell it was the wrong one.
    "customers.customer_state": "the BUYER's state. For where a SELLER is based use sellers.seller_state",
    "sellers.seller_state": "the SELLER's state. For where a BUYER is located use customers.customer_state",
    "customers.customer_city": "the BUYER's city. Seller city is sellers.seller_city",
    "customers.customer_unique_id": (
        "the actual person. customer_id is unique per order (99,441 orders, "
        "99,441 customer_ids), so repeat-buyer questions must group by "
        "customer_unique_id, never customer_id"
    ),
    # order_payments is one-to-many with orders (up to 29 payments on a single
    # order), so a join fans out and double-counts anything summed across it.
    # Stated as a prohibition because the failure is silent: the query runs and
    # returns an inflated but plausible total.
    "order_payments.payment_value": (
        "amount of ONE payment. An order can be paid in several parts, so "
        "joining orders to order_payments multiplies order rows - never SUM "
        "order revenue across that join. Order revenue is SUM(order_items.price)"
    ),
    "order_payments.payment_type": "one of: credit_card, boleto, voucher, debit_card, not_defined. boleto is a Brazilian bank slip paid at an agent; there is no cash-on-delivery. An order may mix types.",
    "order_payments.payment_installments": "number of instalments; 1 means paid in full",
    "order_payments.payment_sequential": "sequence number within an order (1, 2, 3...), not a global id",
    "category_translation.product_category_name_english": (
        "English name for the Portuguese products.product_category_name. Join "
        "on products.product_category_name to answer questions that name a "
        "category in English"
    ),
}


def build_database(force: bool = False) -> Path:
    """Load the CSVs into SQLite. Skips work if the DB already exists."""
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


def describe_schema(sample_rows: int = 1) -> str:
    """
    Build the schema text that goes into the prompt.

    Includes column names + types, a sample row per table, the join paths,
    and notes on ambiguous columns. Sample rows matter more than you'd expect:
    they show the model what values actually look like, which stops it
    inventing filters like WHERE order_status = 'DELIVERED' when the data
    stores lowercase.
    """
    conn = get_connection(read_only=True)
    parts = ["DATABASE SCHEMA", "=" * 60, ""]

    for table in TABLES:
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
                    f"{c}={_truncate(v)}" for c, v in zip(col_names, row)
                )
                parts.append(f"  sample: {pairs}")
        parts.append("")

    if SCHEMA_NOTES:
        parts.append("IMPORTANT")
        parts.extend(f"  - {n}" for n in SCHEMA_NOTES)
        parts.append("")

    parts.append("RELATIONSHIPS (join paths)")
    parts.extend(f"  {r}" for r in RELATIONSHIPS)
    parts.append("")

    conn.close()
    return "\n".join(parts)


def _truncate(value, limit: int = 22) -> str:
    """Keep sample values short so the schema prompt stays compact."""
    text = str(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


if __name__ == "__main__":
    build_database()
    print()
    print(describe_schema())
