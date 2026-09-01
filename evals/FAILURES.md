# Failure taxonomy — draft for human review

Generated 2026-08-24 by `evals/dump_failures.py` from `evals/results.json` (qwen2.5-coder:3b, schema=full, planner=on).

**35 failures / 63 functional.** Proposed categories are mechanical hints fired off the two queries and their rows — they say what *differs*, not which query is right. Confirm every `final:` line by reading the pair yourself.

## Histogram (proposed)

| proposed category | easy | medium | hard | total |
|---|---|---|---|---|
| `wrong_table_or_join_path` | 1 | 4 | 5 | 10 |
| `wrong_status_filter` | 0 | 3 | 5 | 8 |
| `grouping_dimension_wrong` | 0 | 3 | 3 | 6 |
| `retry_exhaustion` | 0 | 2 | 3 | 5 |
| `label_presentation_mismatch` | 1 | 1 | 1 | 3 |
| `aggregation_value_mismatch` | 0 | 0 | 1 | 1 |
| `payments_fanout` | 0 | 0 | 1 | 1 |
| `identity_collision` | 0 | 0 | 1 | 1 |

## Category legend

- `wrong_status_filter` — disagreement on order_status filtering (canceled/unavailable/delivered)
- `label_presentation_mismatch` — numbers identical, labels differ - possibly a CORRECT answer scored wrong by exact-match (e.g. English vs Portuguese names)
- `wrong_table_or_join_path` — generated and reference queries touch different table sets
- `retry_exhaustion` — all attempts failed; no SQL ever executed (see captured attempts)
- `grouping_dimension_wrong` — row labels do not overlap - grouped by something else entirely
- `aggregation_value_mismatch` — same grouping labels, different numbers - metric/filter semantics
- `payments_fanout` — generated joins order_payments and the reference does not - one-to-many join inflating the aggregate
- `identity_collision` — generated groups by customer_id while the reference groups by customer_unique_id (per-order id vs the actual person)

---

## Cases

### F05 · medium · "How many orders were cancelled?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(*) FROM orders WHERE order_status = 'canceled' LIMIT 1000
```

**reference**
```sql
SELECT COUNT(*) FROM orders WHERE order_status IN ('canceled','unavailable')
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  1234
```
**actual**
```
  625
```
signals: status filters differ: gen=['canceled'] ref=['canceled', 'unavailable']

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F07 · easy · "Which 5 product categories have the most products, using the Portuguese category names?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT ct.product_category_name_english, COUNT(*) AS product_count FROM products AS p JOIN category_translation AS ct ON p.product_category_name = ct.product_category_name GROUP BY ct.product_category_name_english ORDER BY product_count DESC LIMIT 5
```

**reference**
```sql
SELECT product_category_name, COUNT(*) AS n FROM products GROUP BY product_category_name ORDER BY n DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  cama_mesa_banho, 3029
  esporte_lazer, 2867
  moveis_decoracao, 2657
  beleza_saude, 2444
  ... (5 rows total)
```
**actual**
```
  bed_bath_table, 3029
  sports_leisure, 2867
  furniture_decor, 2657
  health_beauty, 2444
  ... (5 rows total)
```
signals: table sets differ: gen=['category_translation', 'products'] ref=['products']; GROUP BY differs: gen=['ct.product_category_name_english'] ref=['product_category_name']; non-label values identical; only the label column differs (possibly correct answer scored wrong by exact match)

proposed: **label_presentation_mismatch** — numbers identical, labels differ - possibly a CORRECT answer scored wrong by exact-match (e.g. English vs Portuguese names)

final: <!-- your call -->
---

### F08 · easy · "What is the average product weight in grams?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT AVG(product_weight_g) AS average_product_weight FROM products JOIN category_translation ON products.product_category_name = category_translation.product_category_name WHERE category_translation.product_category_name_english = 'health_beauty' LIMIT 1000
```

**reference**
```sql
SELECT AVG(product_weight_g) FROM products
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  2276.4724877841513
```
**actual**
```
  1434.7937806873977
```
signals: table sets differ: gen=['category_translation', 'products'] ref=['products']

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F14 · medium · "Which 5 product categories generated the most revenue?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT pt.product_category_name_english AS product_category, SUM(oi.price) AS total_revenue FROM orders AS o JOIN order_items AS oi ON o.order_id = oi.order_id JOIN products AS p ON oi.product_id = p.product_id JOIN category_translation AS pt ON p.product_category_name = pt.product_category_name WHERE NOT o.order_status IN ('canceled', 'unavailable') GROUP BY pt.product_category_name_english ORDER BY total_revenue DESC LIMIT 5
```

**reference**
```sql
SELECT p.product_category_name, SUM(oi.price) AS revenue FROM order_items oi JOIN orders o ON oi.order_id = o.order_id JOIN products p ON oi.product_id = p.product_id WHERE o.order_status NOT IN ('canceled','unavailable') GROUP BY p.product_category_name ORDER BY revenue DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  beleza_saude, 1255695.13
  relogios_presentes, 1198185.21
  cama_mesa_banho, 1035964.06
  esporte_lazer, 979740.92
  ... (5 rows total)
```
**actual**
```
  health_beauty, 1255695.13
  watches_gifts, 1198185.21
  bed_bath_table, 1035964.06
  sports_leisure, 979740.92
  ... (5 rows total)
```
signals: table sets differ: gen=['category_translation', 'order_items', 'orders', 'products'] ref=['order_items', 'orders', 'products']; GROUP BY differs: gen=['pt.product_category_name_english'] ref=['p.product_category_name']; non-label values identical; only the label column differs (possibly correct answer scored wrong by exact match)

proposed: **label_presentation_mismatch** — numbers identical, labels differ - possibly a CORRECT answer scored wrong by exact-match (e.g. English vs Portuguese names)

final: <!-- your call -->
---

### F15 · medium · "What is the average freight value per item?"
status=failed · attempts=3 · grounded=yes

**captured attempts** (live rerun; retry temp 0.3):

Attempt 1: no such column: order_status
Attempt 2: no such column: order_status
Attempt 3: no such column: order_status

**generated**: (none recorded)

**reference**
```sql
SELECT AVG(freight_value) FROM order_items
```

rows: expected 1 row(s) · actual —

**expected**
```
  19.990319928983578
```
**actual**
```
(not executed)
```
signals: (none fired)

proposed: **retry_exhaustion** — all attempts failed; no SQL ever executed (see captured attempts)

final: <!-- your call -->
---

### F17 · medium · "How many orders are there in each status?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT order_status, COUNT(*) AS order_count FROM orders WHERE NOT order_status IN ('canceled', 'unavailable') GROUP BY order_status LIMIT 10
```

**reference**
```sql
SELECT order_status, COUNT(*) AS n FROM orders GROUP BY order_status ORDER BY n DESC
```

rows: expected 8 row(s) · actual 6 row(s)

**expected**
```
  delivered, 96478
  shipped, 1107
  canceled, 625
  unavailable, 609
  ... (8 rows total)
```
**actual**
```
  approved, 2
  created, 5
  delivered, 96478
  invoiced, 314
  ... (6 rows total)
```
signals: status filters differ: gen=['canceled', 'unavailable'] ref=[]

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F18 · medium · "What was the total revenue in 2017?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT SUM(T2.price) AS total_revenue FROM orders AS T1 JOIN order_items AS T2 ON T1.order_id = T2.order_id WHERE STRFTIME('%Y', T1.order_approved_at) = '2017' LIMIT 1000
```

**reference**
```sql
SELECT SUM(oi.price) FROM order_items oi JOIN orders o ON oi.order_id = o.order_id WHERE o.order_status NOT IN ('canceled','unavailable') AND o.order_purchase_timestamp LIKE '2017%'
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  6108492.2700000005
```
**actual**
```
  6145900.29
```
signals: status filters differ: gen=[] ref=['canceled', 'unavailable']; strftime date part present in generated only

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F19 · medium · "Which 5 products generated the most revenue?"
status=failed · attempts=3 · grounded=yes

**captured attempts** (live rerun; retry temp 0.3):

Attempt 1: no such column: p.product_name
Attempt 2: no such column: p.product_name
Attempt 3: no such column: p.product_name

**generated**: (none recorded)

**reference**
```sql
SELECT oi.product_id, SUM(oi.price) AS revenue FROM order_items oi JOIN orders o ON oi.order_id = o.order_id WHERE o.order_status NOT IN ('canceled','unavailable') GROUP BY oi.product_id ORDER BY revenue DESC LIMIT 5
```

rows: expected 5 row(s) · actual —

**expected**
```
  bb50f2e236e5eea0100680137654, 63885.0
  6cdd53843498f92890544667809f, 54730.2
  d6160fb7873f184099d9bc95e303, 48899.340000000004
  d1c427060a0f73f6b889a5c7c61f, 46916.51
  ... (5 rows total)
```
**actual**
```
(not executed)
```
signals: (none fired)

proposed: **retry_exhaustion** — all attempts failed; no SQL ever executed (see captured attempts)

final: <!-- your call -->
---

### F20 · medium · "What is the average number of items per order?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(order_id) / COUNT(DISTINCT order_id) AS average_items_per_order FROM order_items LIMIT 1000
```

**reference**
```sql
SELECT CAST(COUNT(*) AS REAL) / COUNT(DISTINCT order_id) FROM order_items
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  1.1417306873695092
```
**actual**
```
  1
```
signals: (none fired)

proposed: **grouping_dimension_wrong** — row labels do not overlap - grouped by something else entirely

final: <!-- your call -->
---

### F22 · medium · "How many orders contain more than one item?"
status=ok · attempts=3 · grounded=yes

**generated**
```sql
SELECT COUNT(T1.order_id) AS order_count FROM orders AS T1 JOIN order_items AS T2 ON T1.order_id = T2.order_id GROUP BY T1.order_id HAVING COUNT(T2.order_item_id) > 1 LIMIT 1000
```

**reference**
```sql
SELECT COUNT(*) FROM ( SELECT order_id FROM order_items GROUP BY order_id HAVING COUNT(*) > 1 )
```

rows: expected 1 row(s) · actual 1000 row(s)

**expected**
```
  9803
```
**actual**
```
  2
  3
  3
  2
  ... (1000 rows total)
```
signals: table sets differ: gen=['order_items', 'orders'] ref=['order_items']; GROUP BY differs: gen=['t1.order_id'] ref=['order_id']; reference expects a single number, generated returned 1000 grouped rows

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F23 · medium · "Which 5 sellers received the most orders?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT s.seller_id, COUNT(*) AS order_count FROM orders AS o JOIN order_items AS oi ON o.order_id = oi.order_id JOIN sellers AS s ON oi.seller_id = s.seller_id GROUP BY s.seller_id ORDER BY order_count DESC LIMIT 5
```

**reference**
```sql
SELECT seller_id, COUNT(DISTINCT order_id) AS n FROM order_items GROUP BY seller_id ORDER BY n DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  6560211a19b47992c3666cc44a7e, 1854
  4a3ca9315b744ce9f8e937436149, 1806
  cc419e0650a3c5ba77189a1882b7, 1706
  1f50f920176fa81dab994f902352, 1404
  ... (5 rows total)
```
**actual**
```
  6560211a19b47992c3666cc44a7e, 2033
  4a3ca9315b744ce9f8e937436149, 1987
  1f50f920176fa81dab994f902352, 1931
  cc419e0650a3c5ba77189a1882b7, 1775
  ... (5 rows total)
```
signals: table sets differ: gen=['order_items', 'orders', 'sellers'] ref=['order_items']; COUNT(*) vs reference COUNT(DISTINCT ...); GROUP BY differs: gen=['s.seller_id'] ref=['seller_id']

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F25 · medium · "Which 5 product categories have the highest average item price?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT p.product_category_name, AVG(oi.price) AS average_item_price FROM order_items AS oi JOIN products AS p ON oi.product_id = p.product_id JOIN orders AS o ON oi.order_id = o.order_id WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'processing', 'created') GROUP BY p.product_category_name ORDER BY average_item_price DESC LIMIT 5
```

**reference**
```sql
SELECT p.product_category_name, AVG(oi.price) AS avg_price FROM order_items oi JOIN products p ON oi.product_id = p.product_id GROUP BY p.product_category_name ORDER BY avg_price DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  pcs, 1098.3405418719212
  portateis_casa_forno_e_cafe, 624.2856578947368
  eletrodomesticos_2, 476.1249579831933
  agro_industria_e_comercio, 342.12485849056606
  ... (5 rows total)
```
**actual**
```
  pcs, 1098.3405418719212
  portateis_casa_forno_e_cafe, 624.2856578947368
  eletrodomesticos_2, 470.84995744680856
  agro_industria_e_comercio, 342.12485849056606
  ... (5 rows total)
```
signals: table sets differ: gen=['order_items', 'orders', 'products'] ref=['order_items', 'products']; status filters differ: gen=['created', 'delivered', 'invoiced', 'processing', 'shipped'] ref=[]

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F30 · hard · "What was the total revenue in each year?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT STRFTIME('%Y', order_approved_at) AS year, SUM(price) AS total_revenue FROM orders JOIN order_items ON orders.order_id = order_items.order_id WHERE NOT order_status IN ('canceled', 'unavailable') GROUP BY year ORDER BY year LIMIT 1000
```

**reference**
```sql
SELECT SUBSTR(o.order_purchase_timestamp, 1, 4) AS yr, SUM(oi.price) AS revenue FROM order_items oi JOIN orders o ON oi.order_id = o.order_id WHERE o.order_status NOT IN ('canceled','unavailable') GROUP BY yr ORDER BY yr
```

rows: expected 3 row(s) · actual 4 row(s)

**expected**
```
  2016, 44726.06
  2017, 6108492.2700000005
  2018, 7341182.41
```
**actual**
```
  , 1670.43
  2016, 44726.06
  2017, 6098585.58
  2018, 7349418.67
```
signals: strftime date part present in generated only; GROUP BY differs: gen=['year'] ref=['yr']

proposed: **grouping_dimension_wrong** — row labels do not overlap - grouped by something else entirely

final: <!-- your call -->
---

### F31 · hard · "How many sellers have sold products in more than 3 different categories?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(DISTINCT seller_id) AS seller_count FROM order_items JOIN products ON order_items.product_id = products.product_id GROUP BY seller_id, product_category_name HAVING COUNT(DISTINCT product_category_name) > 3 LIMIT 1000
```

**reference**
```sql
SELECT COUNT(*) FROM ( SELECT oi.seller_id FROM order_items oi JOIN products p ON oi.product_id = p.product_id GROUP BY oi.seller_id HAVING COUNT(DISTINCT p.product_category_name) > 3 )
```

rows: expected 1 row(s) · actual 0 rows

**expected**
```
  412
```
**actual**
```
(empty)
```
signals: GROUP BY differs: gen=['seller_id', 'product_category_name'] ref=['oi.seller_id']; generated query returns zero rows where the reference has 1

proposed: **grouping_dimension_wrong** — row labels do not overlap - grouped by something else entirely

final: <!-- your call -->
---

### F32 · hard · "Which 5 product categories have the highest freight cost as a share of revenue?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT p.product_category_name, SUM(oi.price) AS total_revenue, SUM(oi.freight_value) AS total_freight_cost, (SUM(oi.freight_value) * 100.0 / SUM(oi.price)) AS freight_cost_share FROM order_items AS oi JOIN products AS p ON oi.product_id = p.product_id GROUP BY p.product_category_name ORDER BY freight_cost_share DESC LIMIT 5
```

**reference**
```sql
SELECT p.product_category_name, SUM(oi.freight_value) / SUM(oi.price) AS freight_share FROM order_items oi JOIN products p ON oi.product_id = p.product_id GROUP BY p.product_category_name HAVING SUM(oi.price) > 0 ORDER BY freight_share DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  casa_conforto_2, 0.5396898470280296
  flores, 0.44040755288097727
  moveis_colchao_e_estofado, 0.37326697313236024
  artigos_de_natal, 0.36693171772630273
  ... (5 rows total)
```
**actual**
```
  casa_conforto_2, 760.27, 410.31, 53.968984702802956
  flores, 1110.04, 488.87, 44.04075528809773
  moveis_colchao_e_estofado, 4368.08, 1630.46, 37.326697313236025
  artigos_de_natal, 8800.82, 3229.2999999999997, 36.69317177263028
  ... (5 rows total)
```
signals: (none fired)

proposed: **aggregation_value_mismatch** — same grouping labels, different numbers - metric/filter semantics

final: <!-- your call -->
---

### F34 · hard · "What is the average revenue per seller?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT s.seller_id, AVG(oi.price) AS average_revenue FROM order_items AS oi JOIN products AS p ON oi.product_id = p.product_id JOIN sellers AS s ON oi.seller_id = s.seller_id JOIN orders AS o ON oi.order_id = o.order_id WHERE NOT o.order_status IN ('canceled', 'unavailable') GROUP BY s.seller_id ORDER BY average_revenue DESC LIMIT 10
```

**reference**
```sql
SELECT SUM(oi.price) / COUNT(DISTINCT oi.seller_id) FROM order_items oi JOIN orders o ON oi.order_id = o.order_id WHERE o.order_status NOT IN ('canceled','unavailable')
```

rows: expected 1 row(s) · actual 10 row(s)

**expected**
```
  4420.046098919096
```
**actual**
```
  80ceebb4ee9b31afb6c6a916a574, 6729.0
  ee27a8f15b1dded4d213a468ba4e, 6499.0
  585175ec331ea177fa47199e39a6, 3549.0
  abe021b01ba992245271b9aa4220, 3360.0
  ... (10 rows total)
```
signals: table sets differ: gen=['order_items', 'orders', 'products', 'sellers'] ref=['order_items', 'orders']; aggregate functions differ: gen=['Avg'] ref=['Count', 'Sum']; GROUP BY differs: gen=['s.seller_id'] ref=[]; reference expects a single number, generated returned 10 grouped rows

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F35 · hard · "How many orders were delivered later than the estimated delivery date?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(T1.order_id) FROM orders AS T1 JOIN orders AS T2 ON T1.order_id = T2.order_id WHERE T1.order_status = 'delivered' AND T1.order_delivered_carrier_date > T2.order_estimated_delivery_date LIMIT 1000
```

**reference**
```sql
SELECT COUNT(*) FROM orders WHERE order_delivered_customer_date > order_estimated_delivery_date
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  7827
```
**actual**
```
  465
```
signals: status filters differ: gen=['delivered'] ref=[]

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F37 · hard · "What share of orders were cancelled?"
status=ok · attempts=1 · grounded=NO

**generated**
```sql
SELECT CAST(COUNT(CASE WHEN order_status = 'canceled' THEN 1 ELSE NULL END) AS REAL) * 100 / COUNT(*) AS canceled_order_share FROM orders LIMIT 1000
```

**reference**
```sql
SELECT CAST(SUM(CASE WHEN order_status IN ('canceled','unavailable') THEN 1 ELSE 0 END) AS REAL) / COUNT(*) FROM orders
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  0.012409368369183738
```
**actual**
```
  0.6285133898492573
```
signals: status filters differ: gen=['canceled'] ref=['canceled', 'unavailable']; aggregate functions differ: gen=['Count'] ref=['Count', 'Sum']

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F40 · hard · "Which 5 product categories have the heaviest average product weight?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT ct.product_category_name_english, AVG(p.product_weight_g) AS average_weight FROM products AS p JOIN category_translation AS ct ON p.product_category_name = ct.product_category_name WHERE NOT ct.product_category_name IN ('canceled', 'unavailable') GROUP BY ct.product_category_name_english ORDER BY average_weight DESC LIMIT 5
```

**reference**
```sql
SELECT product_category_name, AVG(product_weight_g) AS avg_weight FROM products WHERE product_weight_g IS NOT NULL GROUP BY product_category_name ORDER BY avg_weight DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  moveis_colchao_e_estofado, 13190.0
  moveis_escritorio, 12740.867313915858
  moveis_cozinha_area_de_servi, 11598.563829787234
  moveis_quarto, 9997.222222222223
  ... (5 rows total)
```
**actual**
```
  furniture_mattress_and_uphol, 13190.0
  office_furniture, 12740.867313915858
  kitchen_dining_laundry_garde, 11598.563829787234
  furniture_bedroom, 9997.222222222223
  ... (5 rows total)
```
signals: table sets differ: gen=['category_translation', 'products'] ref=['products']; status filters differ: gen=['canceled', 'unavailable'] ref=[]; GROUP BY differs: gen=['ct.product_category_name_english'] ref=['product_category_name']; non-label values identical; only the label column differs (possibly correct answer scored wrong by exact match)

proposed: **label_presentation_mismatch** — numbers identical, labels differ - possibly a CORRECT answer scored wrong by exact-match (e.g. English vs Portuguese names)

final: <!-- your call -->
---

### F41 · hard · "How many orders have not been delivered yet?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(order_id) FROM orders WHERE order_status <> 'delivered' LIMIT 1000
```

**reference**
```sql
SELECT COUNT(*) FROM orders WHERE order_status NOT IN ('delivered','canceled','unavailable')
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  1729
```
**actual**
```
  2963
```
signals: status filters differ: gen=['delivered'] ref=['canceled', 'delivered', 'unavailable']

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F42 · hard · "What is the average delivery time in days?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT AVG(CAST((JULIANDAY(order_delivered_carrier_date) - JULIANDAY(order_purchase_timestamp)) AS INTEGER)) AS average_delivery_time FROM orders WHERE NOT order_status IN ('canceled', 'unavailable') LIMIT 1000
```

**reference**
```sql
SELECT AVG(julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp)) FROM orders WHERE order_delivered_customer_date IS NOT NULL
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  12.558702304031995
```
**actual**
```
  2.7445661641884342
```
signals: status filters differ: gen=['canceled', 'unavailable'] ref=[]

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F43 · hard · "What is the average time in hours between an order being placed and approved?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT AVG((STRFTIME('%s', order_approved_at) - STRFTIME('%s', order_purchase_timestamp)) / 3600) AS average_time_hours FROM orders JOIN order_payments ON orders.order_id = order_payments.order_id WHERE order_payments.payment_type = 'credit_card' OR order_payments.payment_type = 'debit_card' OR order_payments.payment_type = 'voucher' LIMIT 1000
```

**reference**
```sql
SELECT AVG((julianday(order_approved_at) - julianday(order_purchase_timestamp)) * 24) FROM orders WHERE order_approved_at IS NOT NULL
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  10.419094301927661
```
**actual**
```
  4.640292054265874
```
signals: table sets differ: gen=['order_payments', 'orders'] ref=['orders']; generated joins order_payments, reference does not (one-to-many fan-out); julianday arithmetic present in reference only; strftime date part present in generated only

proposed: **payments_fanout** — generated joins order_payments and the reference does not - one-to-many join inflating the aggregate

final: <!-- your call -->
---

### F44 · hard · "What is the average revenue per order?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT AVG(T2.price) AS average_revenue_per_order FROM orders AS T1 JOIN order_items AS T2 ON T1.order_id = T2.order_id WHERE NOT T1.order_status IN ('canceled', 'unavailable') LIMIT 1000
```

**reference**
```sql
SELECT SUM(oi.price) / COUNT(DISTINCT oi.order_id) FROM order_items oi JOIN orders o ON oi.order_id = o.order_id WHERE o.order_status NOT IN ('canceled','unavailable')
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  137.418922188617
```
**actual**
```
  120.37716648379586
```
signals: aggregate functions differ: gen=['Avg'] ref=['Count', 'Sum']

proposed: **grouping_dimension_wrong** — row labels do not overlap - grouped by something else entirely

final: <!-- your call -->
---

### F47 · hard · "Which 5 customer states have the highest average order value?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT c.customer_state, AVG(oi.price) AS average_order_value FROM orders AS o JOIN order_items AS oi ON o.order_id = oi.order_id JOIN customers AS c ON o.customer_id = c.customer_id WHERE o.order_status IN ('delivered', 'shipped', 'invoiced', 'processing', 'created') GROUP BY c.customer_state ORDER BY average_order_value DESC LIMIT 5
```

**reference**
```sql
SELECT c.customer_state, SUM(oi.price) / COUNT(DISTINCT o.order_id) AS avg_order_value FROM order_items oi JOIN orders o ON oi.order_id = o.order_id JOIN customers c ON o.customer_id = c.customer_id WHERE o.order_status NOT IN ('canceled','unavailable') GROUP BY c.customer_state ORDER BY avg_order_value DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  PB, 216.33540489642186
  AP, 198.15147058823527
  AC, 197.32037037037037
  AL, 195.41316301703162
  ... (5 rows total)
```
**actual**
```
  PB, 191.45683333333335
  AL, 180.8892117117117
  AC, 173.72771739130437
  RO, 166.1792057761733
  ... (5 rows total)
```
signals: status filters differ: gen=['created', 'delivered', 'invoiced', 'processing', 'shipped'] ref=['canceled', 'unavailable']; aggregate functions differ: gen=['Avg'] ref=['Count', 'Sum']

proposed: **wrong_status_filter** — disagreement on order_status filtering (canceled/unavailable/delivered)

final: <!-- your call -->
---

### F49 · hard · "How many customers have placed more than one order?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(DISTINCT customer_id) AS num_customers FROM orders GROUP BY customer_id HAVING COUNT(order_id) > 1 LIMIT 1000
```

**reference**
```sql
SELECT COUNT(*) FROM ( SELECT customer_unique_id FROM customers GROUP BY customer_unique_id HAVING COUNT(*) > 1 )
```

rows: expected 1 row(s) · actual 0 rows

**expected**
```
  2997
```
**actual**
```
(empty)
```
signals: table sets differ: gen=['orders'] ref=['customers']; reference groups by customer_unique_id, generated uses customer_id; GROUP BY differs: gen=['customer_id'] ref=['customer_unique_id']; generated query returns zero rows where the reference has 1

proposed: **identity_collision** — generated groups by customer_id while the reference groups by customer_unique_id (per-order id vs the actual person)

final: <!-- your call -->
---

### F50 · hard · "What share of customers are repeat buyers?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT CAST(COUNT(CASE WHEN NOT T2.order_status IN ('canceled', 'unavailable') THEN 1 ELSE NULL END) AS REAL) * 100 / COUNT(T1.customer_unique_id) AS repeat_buyer_percentage FROM customers AS T1 JOIN orders AS T2 ON T1.customer_id = T2.customer_id LIMIT 1000
```

**reference**
```sql
SELECT CAST(COUNT(*) AS REAL) / (SELECT COUNT(DISTINCT customer_unique_id) FROM customers) FROM ( SELECT customer_unique_id FROM customers GROUP BY customer_unique_id HAVING COUNT(*) > 1 )
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  0.031187562437562436
```
**actual**
```
  98.75906316308162
```
signals: table sets differ: gen=['customers', 'orders'] ref=['customers']; status filters differ: gen=['canceled', 'unavailable'] ref=[]; GROUP BY differs: gen=[] ref=['customer_unique_id']

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F53 · medium · "What is the total value paid by credit card?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT SUM(payment_value) AS total_credit_card_payments FROM order_payments JOIN orders ON order_payments.order_id = orders.order_id WHERE NOT orders.order_status IN ('canceled', 'unavailable') AND payment_type = 'credit_card' LIMIT 1000
```

**reference**
```sql
SELECT SUM(payment_value) FROM order_payments WHERE payment_type = 'credit_card'
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  12542084.19
```
**actual**
```
  12350042.56
```
signals: table sets differ: gen=['order_payments', 'orders'] ref=['order_payments']; status filters differ: gen=['canceled', 'unavailable'] ref=[]

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F55 · medium · "How many orders were paid in more than one instalment?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(order_id) FROM order_payments WHERE payment_installments > 1 LIMIT 1000
```

**reference**
```sql
SELECT COUNT(DISTINCT order_id) FROM order_payments WHERE payment_installments > 1
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  51170
```
**actual**
```
  51338
```
signals: (none fired)

proposed: **grouping_dimension_wrong** — row labels do not overlap - grouped by something else entirely

final: <!-- your call -->
---

### F56 · hard · "What is the total item revenue from orders paid by credit card?"
status=failed · attempts=3 · grounded=yes

**captured attempts** (live rerun; retry temp 0.3):

Attempt 1: no such column: order_payments.payment_type
Attempt 2: no such column: products.product_category_name
Attempt 3: no such column: products.product_category_name

**generated**: (none recorded)

**reference**
```sql
SELECT SUM(oi.price) FROM order_items oi WHERE oi.order_id IN ( SELECT order_id FROM order_payments WHERE payment_type = 'credit_card' )
```

rows: expected 1 row(s) · actual —

**expected**
```
  10883802.26
```
**actual**
```
(not executed)
```
signals: (none fired)

proposed: **retry_exhaustion** — all attempts failed; no SQL ever executed (see captured attempts)

final: <!-- your call -->
---

### F57 · hard · "What share of order items were bought from a seller in the same state as the customer?"
status=failed · attempts=3 · grounded=yes

**captured attempts** (live rerun; retry temp 0.3):

Attempt 1: no such column: T1.customer_state
Attempt 2: no such column: T1.customer_state
Attempt 3: no such column: T1.customer_state

**generated**: (none recorded)

**reference**
```sql
SELECT AVG(CASE WHEN c.customer_state = s.seller_state THEN 1.0 ELSE 0 END) FROM order_items oi JOIN orders o ON oi.order_id = o.order_id JOIN customers c ON o.customer_id = c.customer_id JOIN sellers s ON oi.seller_id = s.seller_id
```

rows: expected 1 row(s) · actual —

**expected**
```
  0.36179316466932976
```
**actual**
```
(not executed)
```
signals: (none fired)

proposed: **retry_exhaustion** — all attempts failed; no SQL ever executed (see captured attempts)

final: <!-- your call -->
---

### F58 · hard · "Which 5 customer states rely most on sellers from other states?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT c.customer_state, COUNT(s.seller_state) AS seller_count FROM customers AS c JOIN sellers AS s ON c.customer_id = s.seller_id WHERE c.customer_state <> s.seller_state GROUP BY c.customer_state ORDER BY seller_count DESC LIMIT 5
```

**reference**
```sql
SELECT c.customer_state, COUNT(*) AS items, AVG(CASE WHEN c.customer_state = s.seller_state THEN 1.0 ELSE 0 END) AS local_share FROM order_items oi JOIN orders o ON oi.order_id = o.order_id JOIN customers c ON o.customer_id = c.customer_id JOIN sellers s ON oi.seller_id = s.seller_id GROUP BY c.customer_state HAVING items > 500 ORDER BY local_share ASC, items DESC LIMIT 5
```

rows: expected 5 row(s) · actual 0 rows

**expected**
```
  PA, 1080, 0.0
  MS, 819, 0.0
  PB, 602, 0.0016611295681063123
  PI, 542, 0.0018450184501845018
  ... (5 rows total)
```
**actual**
```
(empty)
```
signals: table sets differ: gen=['customers', 'sellers'] ref=['customers', 'order_items', 'orders', 'sellers']; aggregate functions differ: gen=['Count'] ref=['Avg', 'Count']; generated query returns zero rows where the reference has 5

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F59 · medium · "What percentage of orders were paid by credit card?"
status=ok · attempts=2 · grounded=yes

**generated**
```sql
SELECT CAST(SUM(CASE WHEN payment_type = 'credit_card' THEN 1 ELSE 0 END) AS REAL) * 100 / COUNT(T1.order_id) AS credit_card_percentage FROM orders AS T1 JOIN order_payments AS T2 ON T1.order_id = T2.order_id WHERE payment_type = 'credit_card' LIMIT 1000
```

**reference**
```sql
SELECT CAST(COUNT(DISTINCT order_id) AS REAL) / (SELECT COUNT(*) FROM orders) FROM order_payments WHERE payment_type = 'credit_card'
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  0.7693506702466789
```
**actual**
```
  100.0
```
signals: aggregate functions differ: gen=['Count', 'Sum'] ref=['Count']

proposed: **grouping_dimension_wrong** — row labels do not overlap - grouped by something else entirely

final: <!-- your call -->
---

### F61 · hard · "How many orders were paid with a voucher combined with another payment type?"
status=ok · attempts=1 · grounded=yes

**generated**
```sql
SELECT COUNT(DISTINCT T1.order_id) FROM order_payments AS T1 JOIN orders AS T2 ON T1.order_id = T2.order_id WHERE T1.payment_type = 'voucher' AND NOT T2.order_status IN ('canceled', 'unavailable') LIMIT 1000
```

**reference**
```sql
SELECT COUNT(*) FROM ( SELECT order_id FROM order_payments GROUP BY order_id HAVING SUM(payment_type = 'voucher') > 0 AND COUNT(DISTINCT payment_type) > 1 )
```

rows: expected 1 row(s) · actual 1 row(s)

**expected**
```
  2245
```
**actual**
```
  3745
```
signals: table sets differ: gen=['order_payments', 'orders'] ref=['order_payments']; status filters differ: gen=['canceled', 'unavailable'] ref=[]; aggregate functions differ: gen=['Count'] ref=['Count', 'Sum']; GROUP BY differs: gen=[] ref=['order_id']

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---

### F62 · hard · "What is the median delivery time in days?"
status=failed · attempts=3 · grounded=yes

**captured attempts** (live rerun; retry temp 0.3):

Attempt 1: ambiguous column name: order_id
Attempt 2: ambiguous column name: order_id
Attempt 3: ambiguous column name: order_id

**generated**: (none recorded)

**reference**
```sql
SELECT julianday(order_delivered_customer_date) - julianday(order_purchase_timestamp) AS d FROM orders WHERE order_delivered_customer_date IS NOT NULL ORDER BY d LIMIT 1 OFFSET ( SELECT COUNT(*) / 2 FROM orders WHERE order_delivered_customer_date IS NOT NULL )
```

rows: expected 1 row(s) · actual —

**expected**
```
  10.218009259086102
```
**actual**
```
(not executed)
```
signals: (none fired)

proposed: **retry_exhaustion** — all attempts failed; no SQL ever executed (see captured attempts)

final: <!-- your call -->
---

### F63 · hard · "Which 5 customer states have the slowest average delivery time?"
status=ok · attempts=2 · grounded=yes

**generated**
```sql
SELECT c.customer_state, AVG(JULIANDAY(o.order_delivered_customer_date) - JULIANDAY(o.order_purchase_timestamp)) AS average_delivery_time FROM orders AS o JOIN customers AS c ON o.customer_id = c.customer_id JOIN order_items AS oi ON o.order_id = oi.order_id WHERE NOT o.order_status IN ('canceled', 'unavailable') GROUP BY c.customer_state ORDER BY average_delivery_time ASC LIMIT 5
```

**reference**
```sql
SELECT c.customer_state, AVG(julianday(o.order_delivered_customer_date) - julianday(o.order_purchase_timestamp)) AS avg_days FROM orders o JOIN customers c ON o.customer_id = c.customer_id WHERE o.order_delivered_customer_date IS NOT NULL GROUP BY c.customer_state HAVING COUNT(*) > 300 ORDER BY avg_days DESC LIMIT 5
```

rows: expected 5 row(s) · actual 5 row(s)

**expected**
```
  AL, 24.543855361979436
  PA, 23.77291691136826
  MA, 21.572976474119596
  SE, 21.51978828081889
  ... (5 rows total)
```
**actual**
```
  SP, 8.723844844303574
  PR, 11.947217257805436
  MG, 11.982503629221824
  DF, 12.958993124358175
  ... (5 rows total)
```
signals: table sets differ: gen=['customers', 'order_items', 'orders'] ref=['customers', 'orders']; status filters differ: gen=['canceled', 'unavailable'] ref=[]; aggregate functions differ: gen=['Avg'] ref=['Avg', 'Count']

proposed: **wrong_table_or_join_path** — generated and reference queries touch different table sets

final: <!-- your call -->
---
