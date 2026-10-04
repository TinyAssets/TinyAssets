"""Verified, restartable cutover of requests to the protected activity store.

The old file is retained. Persistent refusal triggers stop old binaries from
writing it after the snapshot; rollback requires compatible protected handlers.
The destination marker commits in the same transaction as the verified copy.
"""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

from tinyassets import agent_activities
from tinyassets.owner_control import ControlUnavailable, control

TABLES = ("pending_requests", "request_suppressions", "request_item_answers", "request_unmutes")


class MigrationUnavailable(ControlUnavailable):
    kind = "request_migration_unavailable"


def _marker(conn):
    conn.execute(
        "CREATE TABLE IF NOT EXISTS request_storage_migration "
        "(singleton INTEGER PRIMARY KEY CHECK(singleton=1), state TEXT NOT NULL)"
    )
    row = conn.execute("SELECT state FROM request_storage_migration WHERE singleton=1").fetchone()
    return row[0] if row else ""


def _rows(conn, table, columns):
    return sorted(conn.execute(f"SELECT {columns} FROM {table}").fetchall(), key=repr)


def ensure_protected(universe_dir: Path, *, recover: bool = False) -> Path:
    from tinyassets.storage import pending_requests as requests

    destination = agent_activities.store_path(universe_dir)
    # Fast read path does not contend with an admitted mutation.
    if destination.is_file():
        with closing(sqlite3.connect(destination)) as conn:
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE name='request_storage_migration'"
            ).fetchone()
            if exists and _marker(conn) == "complete":
                return destination
    with (
        control(universe_dir),
        closing(agent_activities._connect(universe_dir, create=True)) as dst,
    ):
        state = _marker(dst)
        if state == "complete":
            return destination
        if state == "paused" and not recover:
            raise MigrationUnavailable("Request migration is paused; wait for recovery.")
        dst.execute("INSERT OR REPLACE INTO request_storage_migration VALUES (1,'paused')")
        dst.commit()  # Persist the pause even if opening/draining the legacy file fails.
        source = Path(universe_dir) / requests._DB_NAME
        try:
            with closing(sqlite3.connect(source, timeout=0)) as src:
                src.executescript(requests._SCHEMA)
                requests._ensure_columns(src)
                src.commit()
                requests._migrate_itemless_keys(src)
                # Drain all legacy transactions and fence every later old writer.
                src.execute("BEGIN EXCLUSIVE")
                for table in TABLES:
                    for operation in ("INSERT", "UPDATE", "DELETE"):
                        src.execute(
                            f"CREATE TRIGGER IF NOT EXISTS migrated_{table}_{operation} "
                            f"BEFORE {operation} ON {table} BEGIN "
                            "SELECT RAISE(ABORT, 'request_migration_unavailable'); END"
                        )
                src.commit()
                dst.executescript(requests._SCHEMA)
                requests._ensure_columns(dst)
                dst.execute("BEGIN IMMEDIATE")
                try:
                    for table in TABLES:
                        columns = ",".join(
                            row[1] for row in src.execute(f"PRAGMA table_info({table})")
                        )
                        rows = _rows(src, table, columns)
                        dst.execute(f"DELETE FROM {table}")
                        if rows:
                            placeholders = ",".join("?" for _ in rows[0])
                            dst.executemany(
                                f"INSERT INTO {table} ({columns}) VALUES ({placeholders})", rows
                            )
                        if _rows(dst, table, columns) != rows:
                            raise MigrationUnavailable(
                                "Request copy verification failed; writes remain paused."
                            )
                    dst.execute(
                        "UPDATE request_storage_migration SET state='complete' WHERE singleton=1"
                    )
                    dst.commit()
                except BaseException:
                    dst.rollback()
                    raise
        except sqlite3.Error as exc:
            raise MigrationUnavailable(
                "Request migration is paused; retry after recovery."
            ) from exc
    return destination
