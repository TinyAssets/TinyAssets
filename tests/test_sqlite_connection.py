"""ClosingConnection: a per-call sqlite connection closes when its ``with`` exits.

Plain ``sqlite3.Connection.__exit__`` commits and leaves the handle open until a
garbage-collector finalizer closes it later, inside whatever is running then
(the shard-1 fd flake, #4204).
"""

from __future__ import annotations

import sqlite3

import pytest

from tinyassets.sqlite_connection import ClosingConnection


def _open(path):
    return sqlite3.connect(path, factory=ClosingConnection)


def _rows(path):
    reader = sqlite3.connect(path)
    try:
        return reader.execute("SELECT x FROM t ORDER BY x").fetchall()
    finally:
        reader.close()


def test_success_commits_then_closes(tmp_path):
    db = tmp_path / "a.db"
    with _open(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (1)")
    assert _rows(db) == [(1,)]
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_error_rolls_back_then_closes_and_the_error_propagates(tmp_path):
    db = tmp_path / "b.db"
    with _open(db) as conn:
        conn.execute("CREATE TABLE t (x)")
    with pytest.raises(RuntimeError, match="boom"):
        with _open(db) as conn:
            conn.execute("INSERT INTO t VALUES (2)")
            raise RuntimeError("boom")
    assert _rows(db) == []
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_a_plain_connection_is_what_leaked():
    """The behaviour being replaced: ``with`` on a plain connection leaves it open."""
    conn = sqlite3.connect(":memory:")
    try:
        with conn:
            conn.execute("SELECT 1")
        assert conn.execute("SELECT 1").fetchone() == (1,)  # still open
    finally:
        conn.close()


def test_factory_keeps_uri_and_isolation_level_options(tmp_path):
    db = tmp_path / "c.db"
    with _open(db) as conn:
        conn.execute("CREATE TABLE t (x)")
        conn.execute("INSERT INTO t VALUES (3)")
    uri = f"{db.resolve().as_uri()}?mode=ro"
    with sqlite3.connect(uri, uri=True, factory=ClosingConnection) as ro:
        assert ro.execute("SELECT x FROM t").fetchall() == [(3,)]
    with sqlite3.connect(db, isolation_level=None, factory=ClosingConnection) as auto:
        auto.execute("BEGIN IMMEDIATE")
        auto.execute("INSERT INTO t VALUES (4)")
        auto.execute("COMMIT")
    assert _rows(db) == [(3,), (4,)]
