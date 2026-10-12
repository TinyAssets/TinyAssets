"""Durable catalogue attempt facts; status is derived from these rows."""
from __future__ import annotations

import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore, db_path

DEADLINE_SECONDS = 65
REFRESH_TIMEOUT_SECONDS = 55
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


def admit_job(base: Path, *, owner: str, universe_id: str, agent: str,
              package_digest: str, custody_digest: str, requested_model: str) -> int:
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS external_agent_job_receipts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            owner_user_id TEXT NOT NULL, universe_id TEXT NOT NULL,
            execution TEXT NOT NULL, native_agent TEXT NOT NULL,
            package_digest TEXT NOT NULL, custody_digest TEXT NOT NULL,
            requested_model TEXT NOT NULL, resolved_model TEXT NOT NULL DEFAULT '',
            reported_model TEXT NOT NULL DEFAULT '',
            model_status TEXT NOT NULL DEFAULT 'unknown',
            native_session TEXT NOT NULL DEFAULT '',
            input_tokens INTEGER, output_tokens INTEGER,
            usage_provenance TEXT NOT NULL DEFAULT 'unknown',
            admitted_at TEXT NOT NULL, completed_at TEXT, outcome TEXT,
            reason TEXT NOT NULL DEFAULT '')""")
        columns = {row[1] for row in conn.execute(
            "PRAGMA table_info(external_agent_job_receipts)")}
        for name, declaration in (
            ("resolved_model", "TEXT NOT NULL DEFAULT ''"),
            ("model_status", "TEXT NOT NULL DEFAULT 'unknown'"),
        ):
            if name not in columns:
                conn.execute(
                    f"ALTER TABLE external_agent_job_receipts ADD COLUMN {name} {declaration}")
        row = conn.execute("""INSERT INTO external_agent_job_receipts
            (owner_user_id, universe_id, execution, native_agent, package_digest,
             custody_digest, requested_model, admitted_at)
            VALUES (?, ?, 'native_agent', ?, ?, ?, ?, ?)""",
            (owner, universe_id, agent, package_digest, custody_digest,
             requested_model, _now()))
        conn.commit()
        return row.lastrowid


def finish_job(base: Path, receipt_id: int, *, outcome: str, reason: str = "",
               resolved_model: str = "", reported_model: str = "", native_session: str = "",
               input_tokens: int | None = None, output_tokens: int | None = None):
    if outcome not in {"succeeded", "failed", "timed_out", "cancelled"}:
        raise ValueError("invalid external-agent job outcome")
    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        changed = conn.execute("""UPDATE external_agent_job_receipts SET
            completed_at=?, outcome=?, reason=?, resolved_model=?, reported_model=?,
            model_status=?, native_session=?,
            input_tokens=?, output_tokens=?, usage_provenance=?
            WHERE id=? AND completed_at IS NULL""",
            (_now(), outcome, reason[:80], resolved_model[:200], reported_model[:200],
             "reported" if reported_model else "unknown", native_session[:200],
             input_tokens, output_tokens,
             "cli_reported" if input_tokens is not None and output_tokens is not None
             else "unknown", receipt_id))
        if changed.rowcount != 1:
            raise RuntimeError("external-agent job receipt already settled")
        conn.commit()
