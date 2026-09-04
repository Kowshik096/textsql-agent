# Data

The full database is built from the Olist Brazilian-ecommerce CSVs
([Kaggle: olistbr/brazilian-ecommerce](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce)).
Place these seven files in this directory:

- `olist_orders_dataset.csv`
- `olist_order_items_dataset.csv`
- `olist_products_dataset.csv`
- `olist_sellers_dataset.csv`
- `olist_customers_dataset.csv`
- `olist_order_payments_dataset.csv`
- `product_category_name_translation.csv`

Then build:

```bash
python src/db.py
```

This writes `data/olist.db` (~50 MB). The CSVs and the built database are
gitignored — they are large and reproducible, so they stay out of history.

The committed `data/sample/` fixture (~1 MB, 2,000 orders anchored on order
ids with referential integrity verified) is what CI and Docker use. Rebuild
it after changing the sampler:

```bash
python scripts/make_sample.py
DATA_SOURCE=sample python src/db.py
```
