"""Check a store's declared columns before using an existing SQLite table.

Stores explicitly classify each table. Only ephemeral flows/caches may be
discarded; durable drift requires an explicit migration. This runs wherever
the store lives, never by traversing owner stores from the shared router.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from functools import lru_cache
from typing import Literal


class SchemaDrift(RuntimeError):
    """A durable table needs migration; its existing rows remain untouched."""


def _columns(conn, name):
    return tuple(tuple(row) for row in conn.execute(
        "SELECT * FROM pragma_table_xinfo(?, 'main')", (name,)))


@lru_cache(maxsize=256)
def _declared(sql):
    # SQLite parses the declaration, including quoted names, defaults and
    # table constraints. There is no second, hand-maintained column list.
    with closing(sqlite3.connect(":memory:")) as model:
        model.execute(sql)
        tables = model.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'"
        ).fetchall()
        if len(tables) != 1:
            raise ValueError("declare exactly one SQLite table")
        name = tables[0][0]
        return name, _columns(model, name)


def ensure_table(conn: sqlite3.Connection, sql: str, *,
                 kind: Literal["ephemeral", "durable"]) -> None:
    """Create/check one table without committing the caller's transaction.

    Column order, names, types, nullability, defaults, keys and generated-column
    flags must match. Index/constraint migrations remain the store's concern.
    """
    if kind not in {"ephemeral", "durable"}:
        raise ValueError("table kind must be ephemeral or durable")
    name, declared = _declared(sql)
    conn.execute("SAVEPOINT declared_table")
    try:
        found = _columns(conn, name)
        if found and found != declared:
            if kind == "durable":
                raise SchemaDrift(f"durable table {name!r} has schema drift; migration required")
            quoted = '"' + name.replace('"', '""') + '"'
            conn.execute(f"DROP TABLE {quoted}")  # noqa: S608 - quoted declared identifier
        conn.execute(sql)
    except BaseException:
        conn.execute("ROLLBACK TO declared_table")
        raise
    finally:
        conn.execute("RELEASE declared_table")
