"""Durable catalogue attempt facts; status is derived from these rows."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore, db_path

DEADLINE_SECONDS = 30
FRESH_SECONDS = 150


def _table(conn):
    conn.execute("""CREATE TABLE IF NOT EXISTS external_agent_catalogue_receipts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        owner_user_id TEXT NOT NULL,
        universe_id TEXT NOT NULL,
        agent TEXT NOT NULL,
        package_digest TEXT NOT NULL,
        custody_digest TEXT NOT NULL,
        admitted_at TEXT NOT NULL,
        started_at TEXT,
        completed_at TEXT,
        outcome TEXT,
        model_count INTEGER,
        reason TEXT,
        excerpt TEXT)""")
    conn.execute("""CREATE INDEX IF NOT EXISTS external_agent_catalogue_latest
        ON external_agent_catalogue_receipts(owner_user_id, universe_id, agent, id)""")


def _now():
    return datetime.now(timezone.utc).isoformat()


def admit(base: Path, *, owner: str, universe_id: str, agent: str,
          package_digest: str, custody_digest: str) -> int:
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        _table(conn)
        row = conn.execute("""INSERT INTO external_agent_catalogue_receipts
            (owner_user_id, universe_id, agent, package_digest, custody_digest, admitted_at)
            VALUES (?, ?, ?, ?, ?, ?)""",
            (owner, universe_id, agent, package_digest, custody_digest, _now()))
        conn.commit()
        return row.lastrowid


def start(base: Path, receipt_id: int):
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        _table(conn)
        changed = conn.execute("""UPDATE external_agent_catalogue_receipts
            SET started_at=? WHERE id=? AND started_at IS NULL AND completed_at IS NULL""",
            (_now(), receipt_id))
        if changed.rowcount != 1:
            raise RuntimeError("external-agent catalogue attempt was not admitted")
        conn.commit()


def finish(base: Path, receipt_id: int, *, outcome: str, model_count=0,
           reason="", excerpt=""):
    if outcome not in {"succeeded", "failed", "timed_out", "cancelled"}:
        raise ValueError("invalid external-agent catalogue outcome")
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        _table(conn)
        changed = conn.execute("""UPDATE external_agent_catalogue_receipts
            SET completed_at=?, outcome=?, model_count=?, reason=?, excerpt=?
            WHERE id=? AND started_at IS NOT NULL AND completed_at IS NULL""",
            (_now(), outcome, model_count, reason[:80], excerpt[:240], receipt_id))
        if changed.rowcount != 1:
            raise RuntimeError("external-agent catalogue attempt did not start")
        conn.commit()


def status(base: Path, *, owner: str, universe_id: str, agent: str,
           now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    path = db_path(base).resolve()
    if not path.is_file():
        return "never_run"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as conn:
        if conn.execute("SELECT 1 FROM sqlite_master WHERE name=?",
                        ("external_agent_catalogue_receipts",)).fetchone() is None:
            return "never_run"
        row = conn.execute("""SELECT admitted_at, started_at, completed_at,
            outcome, reason FROM external_agent_catalogue_receipts
            WHERE owner_user_id=? AND universe_id=? AND agent=? ORDER BY id DESC LIMIT 1""",
            (owner, universe_id, agent)).fetchone()
    if row is None:
        return "never_run"
    admitted, started, completed, outcome, reason = row
    if completed is None:
        age = (now - datetime.fromisoformat(started or admitted)).total_seconds()
        if age <= DEADLINE_SECONDS:
            return "refreshing" if started else "never_run"
        return "failed: timed_out" if started else "failed: not_started"
    if outcome == "succeeded":
        age = (now - datetime.fromisoformat(completed)).total_seconds()
        return "fresh" if 0 <= age <= FRESH_SECONDS else "stale"
    return "failed: " + (reason or outcome)
