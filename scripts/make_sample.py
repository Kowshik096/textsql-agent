"""
make_sample.py — build a small, referentially-intact sample of the dataset.

    python scripts/make_sample.py

The full CSVs are ~60 MB and gitignored, so CI has no database and most of the
suite would have nothing to run against. This produces a few hundred KB that
can be committed, so the pipeline is exercised for real in CI rather than
mocked into meaninglessness.

Sampling has to preserve joins. Taking N rows from each table independently
produces order_items whose order_id is absent from orders, and every join test
then passes or fails for the wrong reason. So the sample is anchored on orders:
pick order ids first, then take exactly the rows in other tables that those
orders reach.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
OUT = DATA / "sample"

N_ORDERS = 2000
SEED = 20260823        # fixed, so the fixture is reproducible


def main() -> int:
    if not (DATA / "olist_orders_dataset.csv").exists():
        print(f"Full CSVs not found in {DATA}. Nothing to sample.")
        return 1

    OUT.mkdir(exist_ok=True)

    orders = pd.read_csv(DATA / "olist_orders_dataset.csv")
    # Stratify by status so the sample keeps cancelled and unavailable rows -
    # a uniform sample of 2,000 would likely contain none, and the guardrail
    # and policy tests are precisely about those.
    # Select row LABELS per group and index back into the original frame.
    # groupby().apply() consumes the grouping column in pandas 3, which
    # silently produced a sample with no order_status at all - every status
    # test then failed with "no such column" rather than a wrong count.
    picked = []
    for _, group in orders.groupby("order_status"):
        n = min(len(group), max(1, round(N_ORDERS * len(group) / len(orders))))
        picked.extend(group.sample(n, random_state=SEED).index)
    keep = orders.loc[picked]
    if len(keep) < N_ORDERS:
        rest = orders[~orders.order_id.isin(keep.order_id)]
        keep = pd.concat([keep, rest.sample(min(len(rest), N_ORDERS - len(keep)),
                                            random_state=SEED)])
    order_ids = set(keep.order_id)

    items = pd.read_csv(DATA / "olist_order_items_dataset.csv")
    items = items[items.order_id.isin(order_ids)]

    products = pd.read_csv(DATA / "olist_products_dataset.csv")
    products = products[products.product_id.isin(set(items.product_id))]

    sellers = pd.read_csv(DATA / "olist_sellers_dataset.csv")
    sellers = sellers[sellers.seller_id.isin(set(items.seller_id))]

    customers = pd.read_csv(DATA / "olist_customers_dataset.csv")
    customers = customers[customers.customer_id.isin(set(keep.customer_id))]

    payments = pd.read_csv(DATA / "olist_order_payments_dataset.csv")
    payments = payments[payments.order_id.isin(order_ids)]

    translation = pd.read_csv(DATA / "product_category_name_translation.csv")

    written = {
        "olist_orders_dataset.csv": keep,
        "olist_order_items_dataset.csv": items,
        "olist_products_dataset.csv": products,
        "olist_sellers_dataset.csv": sellers,
        "olist_customers_dataset.csv": customers,
        "olist_order_payments_dataset.csv": payments,
        "product_category_name_translation.csv": translation,
    }
    for name, frame in written.items():
        frame.to_csv(OUT / name, index=False)
        print(f"  {name:<44} {len(frame):>6,} rows")

    total = sum((OUT / n).stat().st_size for n in written)
    print(f"\nwritten to {OUT}  ({total/1024:.0f} KB)")

    # Referential integrity is the whole point, so assert it rather than hope.
    assert set(items.order_id) <= set(keep.order_id)
    assert set(items.product_id) <= set(products.product_id)
    assert set(items.seller_id) <= set(sellers.seller_id)
    assert set(keep.customer_id) <= set(customers.customer_id)
    assert set(payments.order_id) <= set(keep.order_id)
    print("referential integrity verified")
    return 0


if __name__ == "__main__":
    sys.exit(main())
