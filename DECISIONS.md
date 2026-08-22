# Design decisions

Why this system is built the way it is. Each entry states the choice, the
alternative that was rejected, and what breaks if you pick the other one.

> **This is a draft in someone else's words.** Rewrite each section in your own
> before you rely on it — the point of this file is that you can defend the
> reasoning, and reasoning you have only read is not reasoning you own.

---

## 1. One agent with three stages, not a multi-agent system

The pipeline is `plan → generate_and_execute → verify`, sharing a single state
object.

That is deliberately **not** described as multi-agent. Multi-agent means
independent agents with separate goals that delegate to or negotiate with each
other. This decomposes one task into stages. Calling it multi-agent invites
"so what are the three agents?" and there is no good answer.

Multi-agent would also be the worse engineering choice here: three times the
model calls per query, harder to evaluate, harder to debug, on a machine where
one call already takes 11 seconds.

## 2. The loop is what makes it an agent

A single prompt gets the SQL wrong often enough that the user sees an error.
The loop catches the error, feeds it back, and tries again — the system
observes the result of its own action and changes what it does next.

**The mechanism is the error text, not the retry.** Generation runs at
temperature 0, and Day 1 confirmed the model is byte-identical across runs. So
a retry whose prompt is unchanged regenerates the same broken query, three
times over. The exception string is the only new information entering the loop.
`no such column: o.price` is what redirects the model to `order_items`.

This is why `error` is a string and not a boolean, and it is the answer to
"how is this different from calling the model twice?"

## 3. The retry prompt carries every attempt, not just the last

With only the most recent failure visible, the model can oscillate: fix the
join and break the filter, then fix the filter and reinstate the join.

Showing the full history is what lets it avoid a query it already tried.
Measured cost: about 90 tokens across three attempts.

## 4. State holds a list of attempts, not a counter

`attempts: list[{sql, error}]` rather than `sql: str` plus `retry_count: int`.

The count becomes `len(attempts)`, so it cannot drift out of sync with what
actually happened. The per-attempt record is also what the first-pass-versus-
final accuracy metric and the failure taxonomy both need — retrofitting it
would have meant touching every node.

## 5. Three guardrail layers, each tested with the one above disabled

| Layer | Where | Catches |
|---|---|---|
| Input | before any model call | injection phrasings, off-topic questions |
| SQL | between generation and execution | writes, stacked statements, off-schema tables |
| Output | before the answer is returned | numbers that are not in the results |

Plus the connection itself, opened `?mode=ro`.

Each has a test that disables the layer above it. **A guardrail never tested
without its predecessor is an assumption, not a layer.**

## 6. Parsing, not string matching

`SELECT * FROM orders WHERE status = 'DELETE'` trips a regex for "DELETE".
`SELECT 1; DROP TABLE orders;` hides a second statement from one. Parsing
understands structure; matching guesses.

And the guard is code rather than a prompt instruction because **a prompt is a
request, not a constraint** — the model can be talked out of it. The system
prompt still says "never write DROP", but only to reduce how often the guard
has to fire.

## 7. SELECT-only is not enough — the allowlist is the fourth check

`SELECT * FROM sqlite_master` is an ordinary read. It passes SELECT-only
enforcement and returns the schema of every table in the database.

So the guard also checks every table referenced against the loaded schema.
Read-only is not the same as read-*permitted*.

The allowlist is read **at call time**, not frozen at import. A module-level
constant looked equivalent and was not: it left the guard permitting tables the
schema no longer contained, which is a guard that has stopped enforcing what it
claims to enforce.

## 8. Guard rejections have two categories

`security` (DROP, stacked statements, `sqlite_master`) stops immediately.
`correctable` (hallucinated table, malformed SQL) goes back into the loop.

Collapsing them costs one metric or the other. Always blocking turns a model
typo into a false positive on the functional suite. Always retrying reports a
real attack as an ordinary failure.

## 9. `?mode=ro`, not `PRAGMA query_only`

`PRAGMA query_only = ON` is reversible from SQL — a query containing
`PRAGMA query_only = OFF` re-enables writes on that same connection. Opening
the file `?mode=ro` is enforced by SQLite at the file level and no statement
can undo it.

The PRAGMA is still set, but it is not the guarantee.

## 10. The query timeout is `set_progress_handler`, not `connect(timeout=)`

`sqlite3.connect(timeout=5)` is the **busy-lock** timeout — how long the driver
waits for a lock held by another connection. It does nothing for a single slow
query against an unlocked file.

Verified: a three-way cartesian join over `order_items` (1.4 quadrillion rows)
runs until killed under the connect parameter, and aborts on the deadline with
a progress handler.

## 11. Grounding tolerates formatting, not magnitude

Every number in the answer must trace to the rows, the row count, the question,
or be a small integer used as prose.

Matching allows decimal and significant-figure rounding — 229,237.63 written as
"229,238" or "about 229,000" is formatting. It deliberately does **not** use a
relative tolerance: 1% of a six-figure revenue figure is ±2,000, which would
have let 198,450 match 200,326. That is exactly the plausible-but-invented
number the check exists to catch.

On failure the prose is discarded and the rows are kept. The SQL ran and the
data is sound; only the narration was suspect. No second model call to rewrite
— it would cost another 11 seconds and could invent a different number.

## 12. Seven tables

`orders, order_items, products, sellers, customers, order_payments,
category_translation`

The constraint is not context capacity — the schema is 1,406 tokens, 17% of
`num_ctx` 8192. It is join-path ambiguity and column collision, both of which
degrade small-model accuracy silently.

`geolocation` is excluded on measurement: 1,000,163 rows across 19,015 zip
prefixes, so its join key is not unique and joining it multiplies every row,
inflating aggregates roughly 53×. Deduplicating to zip/city/state does not
rescue it — 45% of prefixes still map to several city spellings — and
`customers` and `sellers` already carry city and state directly.

`SCHEMA=core` runs the four-table configuration, so the cost of the extra
tables is measured rather than asserted.

## 13. Schema description is the primary accuracy lever

Column notes, join paths and one sample row per table. Sample rows stop the
model inventing `WHERE order_status = 'DELIVERED'` when the data stores
lowercase.

Two added column notes worth 25 tokens corrected two silent semantic errors on
Day 1 — the first generated query summed `price + freight_value` as revenue and
counted cancelled orders. When evaluation surfaces failures, try prompt-side
fixes before agent logic.

Several notes exist purely to disambiguate collisions: `customer_state` versus
`seller_state`, `customer_unique_id` versus `customer_id`, and the warning that
`order_payments` is one-to-many with orders.

## 14. Benchmark cases store reference SQL, not transcribed numbers

Expected results are generated by running the reference query, so they stay
regenerable if the database is rebuilt. Human verification effort goes on
reviewing whether the SQL means what the question asks — which is where the
errors actually are.

Every case whose reading is not forced states its assumption inline. Five
semantic policies (revenue definition, status filter, date basis, order
counting, filter scope) are recorded in `evals/POLICY.md` and applied
throughout.

## 15. Local model, and the comparison is the point

Qwen2.5-Coder-3B via Ollama: zero API cost, no data leaves the machine, and it
fits a 4–5 GB budget — 2.4 GB resident at 8192 context, measured.

Accuracy is lower than a hosted model would give. The defensible framing is not
that the number is good, but that the constraint was treated as a design
parameter and measured: accuracy against model size, holding tuning constant so
the comparison isolates size rather than confounding it with coder-tuning.

## 16. Known limitations

- **Prompt injection is not solved.** The input filter catches known phrasings
  and nothing more. The honest defence is that layer 2 does not trust layer 1.
- **A valid SELECT can still read data it should not.** SELECT-only plus a
  table allowlist says nothing about column-level authorisation.
- **The 10s query timeout can fail correct SQL.** A semantically right query
  that happens to be a correlated subquery over an unindexed column will time
  out. Check whether a slow case is wrong or merely unindexed.
- **Answer prose is weak on a 3B model.** It tends to return bare identifier
  lists rather than sentences.
- **Repeat-customer analysis is thin.** 2,997 of 96,096 people ordered more
  than once; only 19 have five or more orders.
