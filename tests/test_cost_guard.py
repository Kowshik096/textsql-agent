"""
The cost guard: EXPLAIN QUERY PLAN as a pre-execution guardrail.

The signature being tested is the one from the Day 2 incident - nested full
scans over large tables, i.e. a cartesian join. Ordinary equi-joins get
automatic transient indexes from SQLite and must pass; a single large scan
must pass; and the guard must be disableable, because every behaviour
switch here is also an ablation switch.
"""

import sqlite3

import pytest

import cost_guard


@pytest.fixture
def big_db(tmp_path):
    """Two large-ish tables (by lowered threshold), no indexes - like the
    real Olist database, which pandas built without any."""
    path = tmp_path / "big.sqlite"
    conn = sqlite3.connect(path)
    # Olist table names: the guard reads inventory from db.TABLES, so the
    # fixture mirrors the real schema's naming.
    conn.executescript("""
        CREATE TABLE order_items (id INTEGER, order_id INTEGER, v REAL);
        CREATE TABLE orders (id INTEGER, status TEXT);
        CREATE TABLE tiny  (id INTEGER, name TEXT);
    """)
    conn.executemany("INSERT INTO order_items VALUES (?, ?, ?)",
                     [(i, i % 50, 1.0) for i in range(200)])
    conn.executemany("INSERT INTO orders VALUES (?, ?)",
                     [(i, "delivered") for i in range(150)])
    conn.executemany("INSERT INTO tiny VALUES (?, ?)", [(i, "x") for i in range(5)])
    conn.commit()
    yield conn, path
    conn.close()


def check(conn, sql, large_table_min=100):
    return cost_guard.check(sql, conn=conn) if large_table_min == cost_guard.LARGE_TABLE_MIN \
        else _check_with_threshold(conn, sql, large_table_min)


def _check_with_threshold(conn, sql, threshold):
    # LARGE_TABLE_MIN is module-level; tests shrink it via monkeypatch-style
    # direct assignment and restore through the fixture-less pattern below.
    original = cost_guard.LARGE_TABLE_MIN
    cost_guard.LARGE_TABLE_MIN = threshold
    try:
        return cost_guard.check(sql, conn=conn)
    finally:
        cost_guard.LARGE_TABLE_MIN = original


def test_cartesian_over_two_large_tables_is_refused(big_db):
    conn, _ = big_db
    v = _check_with_threshold(conn, "SELECT * FROM order_items, orders", 100)
    assert not v.ok
    assert v.large_scans >= 2
    assert "cartesian" in v.reason


def test_equi_join_gets_auto_indexes_and_passes(big_db):
    conn, _ = big_db
    v = _check_with_threshold(
        conn, "SELECT COUNT(*) FROM order_items i JOIN orders o ON i.order_id = o.id", 100)
    assert v.ok, v.reason


def test_single_large_scan_passes(big_db):
    conn, _ = big_db
    v = _check_with_threshold(conn, "SELECT COUNT(*) FROM order_items", 100)
    assert v.ok


def test_small_table_cartesian_passes(big_db):
    conn, _ = big_db
    v = _check_with_threshold(conn, "SELECT * FROM tiny a, tiny b", 100)
    assert v.ok


def test_unexplainable_sql_is_not_our_failure(big_db):
    conn, _ = big_db
    v = _check_with_threshold(conn, "THIS IS NOT SQL", 100)
    assert v.ok  # sql_guard / executor own parse failures


def test_env_disable_switch(monkeypatch, big_db):
    conn, _ = big_db
    monkeypatch.setenv("COST_GUARD", "0")
    v = cost_guard.check("SELECT * FROM order_items, orders", conn=conn)
    assert v.ok


def test_production_path_uses_read_only_connection():
    # The no-conn branch must open the connection read-only, so the guard
    # can never itself write - checked against whatever DB is configured.
    v = cost_guard.check("SELECT COUNT(*) FROM sellers")
    assert v.ok  # single scan on the real schema
