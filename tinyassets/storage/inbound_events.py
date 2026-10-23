"""Owner-box inbox and dispatch outbox for verified inbound envelopes."""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from contextlib import closing
from pathlib import Path

from tinyassets.storage import db_path

FRESHNESS_SECONDS = 86400
SKEW_SECONDS = 300


def _connect(base: Path):
    db = sqlite3.connect(db_path(Path(base)), timeout=30, isolation_level=None)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA busy_timeout=30000")
    db.execute("CREATE TABLE IF NOT EXISTS inbound_receipts ("
               "receipt TEXT PRIMARY KEY, binding TEXT NOT NULL, revision INTEGER NOT NULL, "
               "event_id TEXT NOT NULL, event_at INTEGER NOT NULL, digest TEXT NOT NULL, "
               "body BLOB NOT NULL, owner TEXT NOT NULL, agent_ref TEXT NOT NULL, "
               "activity_id TEXT, accepted_at REAL NOT NULL, "
               "UNIQUE(binding,revision,event_id))")
    db.execute("CREATE INDEX IF NOT EXISTS inbound_receipts_pending "
               "ON inbound_receipts(activity_id,accepted_at)")
    return db


def accept(base: Path, *, binding: str, revision: int, event_id: str,
           event_at: int, body: bytes, owner: str, agent_ref: str,
           now: float | None = None) -> dict:
    """Commit raw bytes and pending dispatch atomically before acknowledging."""
    at = time.time() if now is None else now
    if (not isinstance(event_id, str) or not 0 < len(event_id) <= 256
            or isinstance(event_at, bool) or not isinstance(event_at, int)):
        raise ValueError("event_identity_required")
    if event_at > at + SKEW_SECONDS or at - event_at > FRESHNESS_SECONDS:
        raise ValueError("stale_event")
    if (not binding or type(revision) is not int or revision < 1 or not owner
            or not agent_ref or len(body) > 1024 * 1024):
        raise ValueError("invalid_inbound_binding")
    digest = hashlib.sha256(body).hexdigest()
    with closing(_connect(base)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DELETE FROM inbound_receipts WHERE activity_id IS NOT NULL "
                       "AND event_at < ?", (at - FRESHNESS_SECONDS - SKEW_SECONDS,))
            row = db.execute("SELECT receipt,digest,event_at FROM inbound_receipts "
                             "WHERE binding=? AND revision=? AND event_id=?",
                             (binding, revision, event_id)).fetchone()
            if row is not None:
                if row["digest"] != digest or row["event_at"] != event_at:
                    raise ValueError("event_id_conflict")
                result = {"receipt": row["receipt"], "accepted": False}
            else:
                receipt = "inb_" + secrets.token_hex(16)
                db.execute("INSERT INTO inbound_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                           (receipt, binding, revision, event_id, event_at, digest, body,
                            owner, agent_ref, None, at))
                result = {"receipt": receipt, "accepted": True}
            db.commit()
            return result
        except BaseException:
            db.rollback()
            raise


def pending(base: Path, *, limit: int = 100) -> list[dict]:
    with closing(_connect(base)) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM inbound_receipts WHERE activity_id IS NULL "
            "ORDER BY accepted_at,receipt LIMIT ?", (limit,))]


def mark_started(base: Path, receipt: str, activity_id: str) -> None:
    with closing(_connect(base)) as db, db:
        db.execute("UPDATE inbound_receipts SET activity_id=? "
                   "WHERE receipt=? AND activity_id IS NULL", (activity_id, receipt))
