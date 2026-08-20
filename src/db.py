"""
db.py — database setup and schema description.

Two jobs:
  1. build_database()  : load the 4 Olist CSVs into a local SQLite file
  2. describe_schema() : turn that schema into text the LLM can reason over

The schema description is the single biggest lever on text-to-SQL accuracy.
A small model cannot infer that `price` lives on order_items rather than
orders, so we spell it out — columns, types, keys, and a sample row each.
"""

import sqlite3
from pathlib import Path

import pandas as pd

# Anchored to the repo root rather than the current working directory.
# A relative path resolves differently under `python src/db.py`, uvicorn,
# pytest and Docker — and the failure ("unable to open database file") gives
# no hint that the cwd is the problem.
DATA_DIR = Path(__file__).resolve().parent.parent / "data"
DB_PATH = DATA_DIR / "olist.db"

# Only these four. Fewer tables = shorter prompt = higher accuracy on a 3B model.
TABLES = {
    "orders": "olist_orders_dataset.csv",
    "order_items": "olist_order_items_dataset.csv",
    "products": "olist_products_dataset.csv",
    "sellers": "olist_sellers_dataset.csv",
}

# SQLite won't know these from a pandas import, so we state them by hand.
# The model needs join paths more than it needs anything else.
RELATIONSHIPS = [
    "order_items.order_id   -> orders.order_id",
    "order_items.product_id -> products.product_id",
    "order_items.seller_id  -> sellers.seller_id",
]

# Short notes on columns whose meaning isn't obvious from the name.
COLUMN_NOTES = {
    "order_items.price": "item price in BRL; seller revenue = SUM(price) only, NOT including freight_value",
    "order_items.freight_value": "shipping cost in BRL for this item; NOT part of seller revenue",
    "order_items.order_item_id": "sequence number within an order (1, 2, 3...), not a global id",
    "orders.order_status": "one of: delivered, shipped, canceled, unavailable, invoiced, processing, created, approved. Exclude 'canceled' and 'unavailable' from revenue questions.",
    "products.product_category_name": "category label in Portuguese",
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
