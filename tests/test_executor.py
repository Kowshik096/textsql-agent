"""
Executor behaviour: results out, errors as text, and a real time ceiling.
"""

import sqlite3

import pytest

import executor


def test_successful_query_returns_rows_and_columns():
    r = executor.run("SELECT seller_id FROM sellers LIMIT 3")
    assert r.ok
    assert r.row_count == 3
    assert r.columns == ["seller_id"]
    assert r.error is None


def test_failure_comes_back_as_usable_error_text():
    """
    The retry loop feeds this string back into the prompt, so it has to name
    the actual problem. A boolean would leave the next attempt identical.
    """
    r = executor.run("SELECT o.price FROM orders o")
    assert not r.ok
    assert "no such column" in r.error.lower()
    assert "price" in r.error


def test_executor_never_raises_on_bad_sql():
    r = executor.run("SELECT FROM WHERE")
    assert not r.ok and r.error


def test_timeout_aborts_a_runaway_query():
    """
    sqlite3.connect(timeout=) would NOT stop this - it is the busy-lock
    timeout, not a query timeout. This passes only because of the
    progress handler.
    """
    r = executor.run(
        "SELECT COUNT(*) FROM order_items a, order_items b, order_items c",
        timeout_s=1.0,
    )
    assert not r.ok
    assert "time limit" in r.error
    assert r.elapsed_s < 4.0, "the ceiling did not hold"


def test_write_is_refused_by_the_connection_itself():
    """
    Layer 2, checked independently of the sqlglot guard. If this ever fails,
    the 'defense in depth' claim in the README is false.
    """
    r = executor.run("DROP TABLE sellers")
    assert not r.ok
    assert "readonly" in r.error.lower() or "read-only" in r.error.lower()
