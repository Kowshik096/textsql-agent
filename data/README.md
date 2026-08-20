# Data

The database is **not committed** — the four CSVs plus the built SQLite file
come to ~73 MB, which does not belong in git history.

## Rebuild

1. Download the [Brazilian E-Commerce Public Dataset by Olist][olist] from Kaggle.
2. Place these four files in this directory:
   - `olist_orders_dataset.csv`
   - `olist_order_items_dataset.csv`
   - `olist_products_dataset.csv`
   - `olist_sellers_dataset.csv`
3. Run `python src/db.py`

This produces `data/olist.db`:

| Table | Rows |
|---|---|
| order_items | 112,650 |
| orders | 99,441 |
| products | 32,951 |
| sellers | 3,095 |

Only four of the nine Olist tables are used. The constraint is prompt size, not
disk: nine tables is ~2,000–2,500 schema tokens and roughly twelve join paths
versus three, and Olist's three near-identical `*_zip_code_prefix` columns are a
common silent-failure source for small models.

[olist]: https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce
