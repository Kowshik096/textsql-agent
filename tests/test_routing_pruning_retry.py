"""
The Day 5+ mechanisms: planner routing, schema pruning, retry variants.

All three are env-gated with the committed configuration as default, so the
existing numbers stay reproducible. These tests pin the new machinery itself:
the heuristic decisions were fitted against the per-case planner flips
(results_planner_on/off), so they are asserted against those same cases here -
a heuristic that drifts from its evidence should fail loudly.
"""

import pytest

import db
import executor
import graph
import prompts


# --- planner routing ---------------------------------------------------------
#
# Ground truth: which cases flipped correct when the planner was turned on
# (results_planner_on/off). The heuristic plans for four of the six wins and
# skips every loss. F36 and F51 are accepted misses: their text names one
# table with no comparison phrase, and catching them needs a model call -
# which defeats the point of a router.

PLANNER_LOSSES = [
    "What is the average product weight in grams?",
    "What is the average freight value per item?",
    "What was the total revenue in each year?",
    "What is the total value paid by credit card?",
]

PLANNER_WIN_PLANNED = [
    "How many orders were placed in each year?",
    "Which 5 product categories are sold by the most distinct sellers?",
    "How many sellers generated more revenue than the average seller?",
    "How many orders were paid using more than one payment type?",
]

# Wins the router deliberately gives up on. Recorded so a future change that
# starts planning here is visible rather than accidental.
PLANNER_WIN_ACCEPTED_MISS = [
    "Which 5 sellers generated the most revenue in 2018?",
    "Which payment types are used most often?",
]


@pytest.mark.parametrize("question", PLANNER_LOSSES)
def test_known_planner_losses_are_not_planned(question):
    assert not db.suggests_join(question)


@pytest.mark.parametrize("question", PLANNER_WIN_PLANNED)
def test_known_planner_wins_are_planned(question):
    assert db.suggests_join(question)


@pytest.mark.parametrize("question", PLANNER_WIN_ACCEPTED_MISS)
def test_accepted_misses_stay_unplanned(question):
    assert not db.suggests_join(question)


def test_trigger_requires_a_table_match():
    # The F26/F30 separator: identical bucketing phrase, only F26 names a table.
    assert db.suggests_join("How many orders were placed in each year?")
    assert not db.suggests_join("What was the total revenue in each year?")


def test_planner_mode_respected(monkeypatch):
    q = "Which 5 sellers generated the most revenue?"
    monkeypatch.setattr(graph, "PLANNER_MODE", "off")
    assert graph.planner_for_question(q) is False
    monkeypatch.setattr(graph, "PLANNER_MODE", "on")
    assert graph.planner_for_question(q) is True
    monkeypatch.setattr(graph, "PLANNER_MODE", "auto")
    assert graph.planner_for_question(q) == db.suggests_join(q)


# --- schema pruning ----------------------------------------------------------

def test_select_tables_expands_one_hop():
    tables = db.select_tables("Which sellers generated the most revenue?")
    assert "sellers" in tables
    # Revenue lives on order_items; without it the question is unanswerable.
    assert "order_items" in tables
    # One hop only: products is two hops away and was never mentioned.
    assert "products" not in tables


def test_select_tables_returns_none_when_nothing_matches():
    assert db.select_tables("What is the meaning of life?") is None


def test_pruned_schema_omits_unselected_tables():
    text = db.describe_schema(tables=["sellers", "order_items"])
    assert "TABLE: sellers" in text
    assert "TABLE: order_items" in text
    assert "TABLE: products" not in text
    # Relationships are kept only when BOTH ends survive pruning - a join path
    # into a table the model was never shown teaches it to hallucinate one.
    assert "-> orders.order_id" not in text
    assert "-> customers.customer_id" not in text
    assert "order_items.seller_id  -> sellers.seller_id" in text


def test_schema_for_question_defaults_to_full(monkeypatch):
    monkeypatch.setenv("PRUNE", "0")
    assert db.schema_for_question("anything") == db.describe_schema()


def test_schema_for_question_prunes_when_enabled(monkeypatch):
    monkeypatch.setenv("PRUNE", "1")
    db._SCHEMA_CACHE.clear()
    pruned = db.schema_for_question("Which sellers generated the most revenue?")
    full = db.describe_schema()
    monkeypatch.setenv("PRUNE", "0")
    assert pruned != full
    assert "TABLE: order_payments" not in pruned
    # Cached per subset: second call returns identical text without rebuilding.
    monkeypatch.setenv("PRUNE", "1")
    assert db.schema_for_question("Which sellers generated the most revenue?") == pruned


# --- retry hint lookup -------------------------------------------------------

def test_hint_names_where_the_column_actually_lives():
    hint = executor.retry_hint("no such column: o.price")
    assert hint is not None
    assert "order_items" in hint
    assert "'price'" in hint
    assert "Columns on order_items:" in hint


def test_hint_for_unknown_column_lists_nothing_false():
    hint = executor.retry_hint("no such column: x.nonexistent_field")
    assert hint is not None
    assert "no table in this database" in hint


def test_hint_for_missing_table_lists_available_tables():
    hint = executor.retry_hint("no such table: geolocations")
    assert hint is not None
    assert "geolocations" in hint
    assert "sellers" in hint


def test_hint_for_ambiguous_column_advises_qualifying():
    hint = executor.retry_hint("ambiguous column name: order_id")
    assert hint is not None
    assert "Qualify" in hint


def test_hint_returns_none_for_unrecognised_errors():
    assert executor.retry_hint(None) is None
    assert executor.retry_hint("query exceeded the 10s time limit") is None


# --- prompt variants ---------------------------------------------------------

ATTEMPTS = [{"sql": "SELECT o.price FROM orders o LIMIT 5",
             "error": "no such column: o.price"}]
HINT = "HINT: fabricated for the test."


def test_full_mode_shows_failed_sql():
    p = prompts.build_generator_prompt("SCHEMA", "Q?", attempts=ATTEMPTS,
                                       retry_mode="full")
    assert "SELECT o.price FROM orders o LIMIT 5" in p
    assert HINT not in p


def test_error_only_mode_withholds_the_sql():
    p = prompts.build_generator_prompt("SCHEMA", "Q?", attempts=ATTEMPTS,
                                       retry_mode="error_only")
    assert "o.price FROM orders" not in p
    assert "withheld" in p


def test_hint_mode_appends_the_hint():
    p = prompts.build_generator_prompt("SCHEMA", "Q?", attempts=ATTEMPTS,
                                       retry_mode="hint", retry_hint=HINT)
    assert "SELECT o.price" in p
    assert HINT in p


def test_default_mode_is_the_committed_configuration():
    assert prompts.RETRY_MODE == "full"
