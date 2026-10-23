"""Project-scope persistent memory primitive.

Provides a simple key/value store keyed by ``project_id`` — an opaque
caller-supplied string (repo name, goal slug, or arbitrary identifier).
"What is a project" at the data-model level is deliberately deferred to a
follow-up spec; this module ships the minimal persistent-KV surface.

Canonical values are owner files. A pre-conversion SQLite snapshot in
``<base_path>/.project_memory.db`` is readable until file authority is published.

Spec invariants:
- Writes are append-only (audit trail row) — the primary row holds current
  value; history is retained for moderation.
- No cross-project reads without explicit project_id.
- No per-project size cap. The 1 MB one that used to be here was a second
  storage number beside the universe's tier storage, and storage is one of the
  two limits an account actually has (founder, 2026-09-30) -- it is measured and
  enforced once, over the writer's ACCOUNT (`tinyassets.storage_accounting`,
  store ``project_memory``), not per primitive.
- ``version`` field supports optimistic concurrency: writes require matching
  ``expected_version`` or return a ``{"conflict": ...}`` error.
- ``version`` increments monotonically per ``(project_id, key)`` pair.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any


def _db_path(base_path: str | Path) -> Path:
    return Path(base_path) / ".project_memory.db"


def _connect(base_path: str | Path) -> sqlite3.Connection:
    db = _db_path(base_path)
    db.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db), timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def _init_db(base_path: str | Path) -> None:
    with _connect(base_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_memory (
                project_id  TEXT NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                updated_by  TEXT NOT NULL DEFAULT '',
                version     INTEGER NOT NULL DEFAULT 1,
                PRIMARY KEY (project_id, key)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS project_memory_history (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id  TEXT NOT NULL,
                key         TEXT NOT NULL,
                value       TEXT NOT NULL,
                updated_at  TEXT NOT NULL,
                updated_by  TEXT NOT NULL DEFAULT '',
                version     INTEGER NOT NULL
            )
            """
        )


def project_memory_get(
    base_path: str | Path,
    *,
    project_id: str,
    key: str,
) -> dict[str, Any] | None:
    """Return the stored value for *key* in *project_id*, or None if not found.

    The returned dict has keys: ``project_id``, ``key``, ``value``,
    ``updated_at``, ``updated_by``, ``version``. A file outranks the database.
    """
    from tinyassets.knowledge_authority import files_are_authority, read_project_memory

    filed = read_project_memory(base_path, project_id=project_id, key=key)
    if filed is not None:
        return filed
    if files_are_authority(base_path):
        return None
    _init_db(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM project_memory WHERE project_id = ? AND key = ?",
            (project_id, key),
        ).fetchone()
    if row is None:
        return None
    return {
        "project_id": row["project_id"],
        "key": row["key"],
        "value": json.loads(row["value"]),
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
        "version": int(row["version"]),
    }


def project_memory_list(
    base_path: str | Path,
    *,
    project_id: str,
    key_prefix: str = "",
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List all entries for *project_id*, optionally filtered by *key_prefix*.

    Returns a list of ``{project_id, key, value, updated_at, updated_by,
    version}`` dicts, ordered by key. Files outrank the database.
    """
    from tinyassets.knowledge_authority import files_are_authority, list_project_memory_files

    filed = list_project_memory_files(base_path, project_id=project_id)
    by_key = {str(row["key"]): row for row in filed}
    if not files_are_authority(base_path):
        _init_db(base_path)
        with _connect(base_path) as conn:
            rows = conn.execute(
                "SELECT * FROM project_memory WHERE project_id = ? ORDER BY key",
                (project_id,),
            ).fetchall()
        for row in rows:
            by_key.setdefault(row["key"], {
                "project_id": row["project_id"],
                "key": row["key"],
                "value": json.loads(row["value"]),
                "updated_at": row["updated_at"],
                "updated_by": row["updated_by"],
                "version": int(row["version"]),
            })
    ordered = [by_key[key] for key in sorted(by_key)]
    if key_prefix:
        ordered = [row for row in ordered if str(row["key"]).startswith(key_prefix)]
    return ordered[: max(1, limit)]
