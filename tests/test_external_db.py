"""
External database mode: the same agent stack pointed at a schema it has
never seen (the Spider validation path).

The critical property is that point_at() mutates ONE module consistently -
tables, relationships, notes, cache - because the failure mode it replaces
(importlib.reload) leaves other modules holding stale references and two
halves of one process disagreeing about what tables exist.
"""

import sqlite3

import pytest

import db


@pytest.fixture
def external_db(tmp_path):
    """A tiny foreign-keyed sqlite file, plus guaranteed restore of db state."""
    path = tmp_path / "ext.sqlite"
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE parent (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE child  (id INTEGER PRIMARY KEY,
                             parent_id INTEGER REFERENCES parent(id),
                             weight REAL);
        INSERT INTO parent VALUES (1, 'a'), (2, 'b');
        INSERT INTO child VALUES (10, 1, 3.5);
    """)
    conn.commit()
    conn.close()

    saved = (db.DB_PATH, dict(db.TABLES), list(db.RELATIONSHIPS),
             list(db.SCHEMA_NOTES), dict(db.COLUMN_NOTES), db.EXTERNAL)
    db.point_at(path)
    yield path
    (db.DB_PATH, db.TABLES, db.RELATIONSHIPS,
     db.SCHEMA_NOTES, db.COLUMN_NOTES, db.EXTERNAL) = saved
    db._SCHEMA_CACHE.clear()


def test_point_at_introspects_tables_and_foreign_keys(external_db):
    assert set(db.TABLES) == {"parent", "child"}
    assert "child.parent_id -> parent.id" in db.RELATIONSHIPS


def test_external_mode_drops_olist_prompt_notes(external_db):
    assert db.SCHEMA_NOTES == []
    assert db.COLUMN_NOTES == {}
    text = db.describe_schema()
    assert "TABLE: parent" in text
    assert "TABLE: child" in text
    # The Olist note must not leak into someone else's prompt.
    assert "NO seller_id" not in text
    assert "child.parent_id -> parent.id" in text


def test_pruning_and_router_degrade_conservatively(external_db):
    # Olist keywords are meaningless against an unseen schema: never prune,
    # always plan.
    assert db.select_tables("which sellers have the most orders?") is None
    assert db.suggests_join("anything at all") is True
    assert db.schema_for_question("anything") == db.describe_schema()


def test_read_only_connection_still_enforced(external_db):
    conn = db.get_connection(read_only=True)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO parent VALUES (3, 'c')")
    finally:
        conn.close()


def test_guard_allowlist_tracks_the_external_schema(external_db):
    import sql_guard

    ok = sql_guard.check("SELECT c.name FROM child c JOIN parent p "
                         "ON c.parent_id = p.id")
    assert ok.ok
    blocked = sql_guard.check("SELECT * FROM orders")   # exists only on Olist
    assert not blocked.ok
    assert blocked.category == "correctable"
