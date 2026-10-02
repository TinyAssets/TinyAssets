"""Contribution events ledger — Phase B item 8.

Single-table append-only events ledger. All five contribution surfaces emit
into ``contribution_events``; the bounty calculator (Phase 2 dispatch) reads.

Spec: ``docs/design-notes/2026-04-25-contribution-ledger-proposal.md`` §1.

Schema layer landed in Task #71. Emit-wiring layer landed in Task #72+;
``record_contribution_event`` is the helper Phase 2 emit-sites call. Future
list / aggregate helpers belong in this module too — single-source layout
matches ``branch_versions.py``.
"""

from __future__ import annotations

import logging
import sqlite3
import time
import uuid
from pathlib import Path

from tinyassets.sqlite_connection import ClosingConnection

_logger = logging.getLogger(__name__)

# Counter incremented on every record_contribution_event failure recovered
# by an upstream try/except. Should remain 0 in healthy operation. Phase 2
# emit-sites (Task #72: execute_step in update_run_status) wrap the call in
# try/except, log a warning, and increment this counter. Operators grep for
# non-zero in production — same observability shape as
# ``_LEGACY_FALLBACK_HITS`` from Task #64.
_EMIT_FAILURES: dict[str, int] = {"count": 0}

# ── DDL ──────────────────────────────────────────────────────────────────────

CONTRIBUTION_EVENTS_SCHEMA = """
    -- Phase B item 8 (Task #71). One row per contribution event.
    -- Append-only: weight is REAL signed (positive = credit; negative =
    -- regression). source_run_id NULLable for non-run events (PR webhook,
    -- gate-cite of pre-existing wiki page). FK to runs(run_id) is
    -- enforceable because both tables live in the same SQLite file.
    CREATE TABLE IF NOT EXISTS contribution_events (
        event_id              TEXT PRIMARY KEY,
        event_type            TEXT NOT NULL,
        actor_id              TEXT NOT NULL,
        actor_handle          TEXT NOT NULL DEFAULT '',
        owner_user_id         TEXT NOT NULL DEFAULT '',
        daemon_id             TEXT NOT NULL DEFAULT '',
        runtime_instance_id   TEXT NOT NULL DEFAULT '',
        worker_id             TEXT NOT NULL DEFAULT '',
        source_run_id         TEXT,
        source_artifact_id    TEXT,
        source_artifact_kind  TEXT NOT NULL DEFAULT '',
        weight                REAL NOT NULL DEFAULT 1.0,
        occurred_at           REAL NOT NULL,
        metadata_json         TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY (source_run_id) REFERENCES runs(run_id)
    );

    CREATE INDEX IF NOT EXISTS idx_contribution_events_window
        ON contribution_events(occurred_at);
    CREATE INDEX IF NOT EXISTS idx_contribution_events_actor
        ON contribution_events(actor_id, occurred_at);
    CREATE INDEX IF NOT EXISTS idx_contribution_events_artifact
        ON contribution_events(source_artifact_id, source_artifact_kind);
    CREATE INDEX IF NOT EXISTS idx_contribution_events_run
        ON contribution_events(source_run_id);
"""

CONTRIBUTION_EVENT_IDENTITY_COLUMNS = (
    ("owner_user_id", "TEXT NOT NULL DEFAULT ''"),
    ("daemon_id", "TEXT NOT NULL DEFAULT ''"),
    ("runtime_instance_id", "TEXT NOT NULL DEFAULT ''"),
    ("worker_id", "TEXT NOT NULL DEFAULT ''"),
)


def migrate_contribution_events_schema(conn: sqlite3.Connection) -> None:
    """Additive migrations for existing contribution_events tables."""
    existing = {
        row["name"] if hasattr(row, "keys") else row[1]
        for row in conn.execute("PRAGMA table_info(contribution_events)")
    }
    for col, ddl in CONTRIBUTION_EVENT_IDENTITY_COLUMNS:
        if col not in existing:
            conn.execute(f"ALTER TABLE contribution_events ADD COLUMN {col} {ddl}")


def _connect(base_path: str | Path) -> sqlite3.Connection:
    from tinyassets.runs import runs_db_path

    path = runs_db_path(base_path)
    conn = sqlite3.connect(str(path), timeout=30.0, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def initialize_contribution_events_db(base_path: str | Path) -> None:
    """Create contribution_events table + indexes if not present.

    Idempotent — uses ``IF NOT EXISTS`` clauses throughout. Concatenated
    into the runs DB schema by ``tinyassets.runs.initialize_runs_db`` so
    a single ``initialize_runs_db`` call brings up all run-side tables.
    """
    from tinyassets.runs import runs_db_path

    runs_db_path(base_path)  # ensure parent runs DB path exists
    with _connect(base_path) as conn:
        conn.executescript(CONTRIBUTION_EVENTS_SCHEMA)
        migrate_contribution_events_schema(conn)


def record_contribution_event(
    base_path: str | Path,
    *,
    event_id: str | None = None,
    event_type: str,
    actor_id: str,
    actor_handle: str = "",
    owner_user_id: str = "",
    daemon_id: str = "",
    runtime_instance_id: str = "",
    worker_id: str = "",
    source_run_id: str | None = None,
    source_artifact_id: str | None = None,
    source_artifact_kind: str = "",
    weight: float = 1.0,
    occurred_at: float | None = None,
    metadata_json: str = "{}",
    conn: sqlite3.Connection | None = None,
) -> bool:
    """INSERT a contribution event. Returns True if inserted, False if
    skipped via ``INSERT OR IGNORE`` on caller-supplied ``event_id`` collision.

    Caller-supplied ``event_id`` enables idempotent emits — Phase 2 emit-sites
    use deterministic IDs like ``"execute_step:{run_id}:{terminal_status}"``
    so re-emit attempts (e.g. duplicate run-completion paths) silently dedup.
    When ``event_id`` is None (caller doesn't need idempotency), a fresh
    UUID4 hex is generated.

    ``conn`` lets callers share an open SQLite transaction — the INSERT
    runs inside the caller's ``with _connect()`` block, atomic with whatever
    write the caller is doing. When None, opens a fresh connection.
    """
    if event_id is None:
        event_id = uuid.uuid4().hex
    if occurred_at is None:
        occurred_at = time.time()

    sql = (
        "INSERT OR IGNORE INTO contribution_events "
        "(event_id, event_type, actor_id, actor_handle, owner_user_id, "
        " daemon_id, runtime_instance_id, worker_id, source_run_id, "
        " source_artifact_id, source_artifact_kind, weight, occurred_at, "
        " metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
    )
    params = (
        event_id, event_type, actor_id, actor_handle, owner_user_id or "",
        daemon_id or "", runtime_instance_id or "", worker_id or "",
        source_run_id, source_artifact_id, source_artifact_kind, weight,
        occurred_at, metadata_json,
    )

    if conn is not None:
        cur = conn.execute(sql, params)
        return cur.rowcount > 0

    with _connect(base_path) as own_conn:
        cur = own_conn.execute(sql, params)
        return cur.rowcount > 0
