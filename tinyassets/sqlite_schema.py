"""Check a store's declared columns before using an existing SQLite table.

Stores explicitly classify each table. Only ephemeral flows/caches may be
discarded; durable drift requires an explicit migration. This runs wherever
the store lives, never by traversing owner stores from the shared router.
"""
from __future__ import annotations

import sqlite3
from collections.abc import Mapping
from contextlib import closing, suppress
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
                 kind: Literal["ephemeral", "durable"],
                 append_history: tuple[str, ...] = (),
                 historical_defaults: Mapping[str, str] | None = None) -> None:
    """Create/check one table without committing the caller's transaction.

    Column order, names, types, nullability, defaults, keys and generated-column
    flags must match. Index/constraint migrations remain the store's concern.
    append_history declares columns introduced by historical ADD COLUMNs.
    Their physical ordinal can vary across release histories; all their other
    attributes must match, as must the order of the original columns. This
    accepts those existing layouts without rebuilding a durable table.
    historical_defaults maps a column to one exact historical SQLite default
    (the pragma dflt_value, quotes included) accepted as the declared default.
    The table is not rebuilt and its rows stay untouched.
    """
    if kind not in {"ephemeral", "durable"}:
        raise ValueError("table kind must be ephemeral or durable")
    historical_defaults = historical_defaults or {}
    name, declared = _declared(sql)
    conn.execute("SAVEPOINT declared_table")
    try:
        found = _columns(conn, name)
        declared_defaults = {row[1]: row[4] for row in declared}

        def body(row):
            rest = row[1:]
            allowed = historical_defaults.get(rest[0])
            current = declared_defaults.get(rest[0])
            if (allowed is not None and current is not None and rest[3] == allowed
                    and allowed != current):
                rest = rest[:3] + (current,) + rest[4:]
            return rest

        def layout(columns):
            original = tuple(body(row) for row in columns if row[1] not in append_history)
            appended = tuple(sorted(body(row) for row in columns if row[1] in append_history))
            return original, appended

        if append_history or historical_defaults:
            matches = layout(found) == layout(declared)
        else:
            matches = found == declared
        if found and not matches:
            if kind == "durable":
                raise SchemaDrift(f"durable table {name!r} has schema drift; migration required")
            quoted = '"' + name.replace('"', '""') + '"'
            conn.execute(f"DROP TABLE {quoted}")  # noqa: S608 - quoted declared identifier
        conn.execute(sql)
    except BaseException:
        with suppress(sqlite3.Error):
            conn.execute("ROLLBACK TO declared_table")
        with suppress(sqlite3.Error):
            conn.execute("RELEASE declared_table")
        raise
    else:
        conn.execute("RELEASE declared_table")
