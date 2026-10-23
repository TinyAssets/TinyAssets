"""Owner-box inbox and dispatch outbox for verified inbound envelopes."""

from __future__ import annotations

import hashlib
import json
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
    db.execute("BEGIN IMMEDIATE")
    columns = {row[1] for row in db.execute("PRAGMA table_info(inbound_receipts)")}
    if "universe" not in columns:
        db.execute("ALTER TABLE inbound_receipts ADD COLUMN universe TEXT NOT NULL DEFAULT ''")
    if "completed_at" not in columns:
        db.execute("ALTER TABLE inbound_receipts ADD COLUMN completed_at REAL")
    db.commit()
    db.execute("CREATE TABLE IF NOT EXISTS inbound_bindings ("
               "binding TEXT PRIMARY KEY, revision INTEGER NOT NULL, universe TEXT NOT NULL, "
               "agent_ref TEXT NOT NULL, enabled INTEGER NOT NULL, paused INTEGER NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS inbound_installation_view ("
               "binding TEXT PRIMARY KEY, revision INTEGER NOT NULL, metadata TEXT NOT NULL)")
    db.execute("CREATE TABLE IF NOT EXISTS inbound_shared_bindings (binding TEXT PRIMARY KEY)")
    return db


def record_shared(base: Path, binding: str) -> None:
    with closing(_connect(base)) as db:
        db.execute("INSERT OR IGNORE INTO inbound_shared_bindings VALUES (?)", (binding,))


def has_shared(base: Path) -> bool:
    if not db_path(base).exists():
        return False
    with closing(_connect(base)) as db:
        return db.execute("SELECT 1 FROM inbound_shared_bindings LIMIT 1").fetchone() is not None


def reconcile_installations(base: Path, rows: list[dict]) -> None:
    """Apply this box's authoritative view; a reconnect never re-enables old work."""
    with closing(_connect(base)) as db:
        db.execute("BEGIN IMMEDIATE")
        try:
            db.execute("DELETE FROM inbound_installation_view")
            for row in rows:
                db.execute("INSERT INTO inbound_installation_view VALUES (?,?,?)",
                           (row["binding"], row["revision"], json.dumps(row)))
            db.execute("UPDATE inbound_bindings SET enabled=0 WHERE binding IN "
                       "(SELECT binding FROM inbound_shared_bindings) AND NOT EXISTS "
                       "(SELECT 1 FROM inbound_installation_view v WHERE "
                       "v.binding=inbound_bindings.binding "
                       "AND v.revision=inbound_bindings.revision)")
            db.commit()
        except BaseException:
            db.rollback()
            raise


def installation_view(base: Path) -> list[dict]:
    with closing(_connect(base)) as db:
        return [json.loads(row[0]) for row in db.execute(
            "SELECT metadata FROM inbound_installation_view ORDER BY binding")]


def accept(base: Path, *, binding: str, revision: int, event_id: str,
           event_at: int, body: bytes, owner: str, agent_ref: str,
           now: float | None = None, universe: str = "") -> dict:
    """Commit raw bytes and pending dispatch atomically before acknowledging."""
    at = time.time() if now is None else now
    if (not isinstance(event_id, str) or not event_id.strip() or not 0 < len(event_id) <= 256
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
            db.execute("DELETE FROM inbound_receipts WHERE completed_at IS NOT NULL "
                       "AND event_at < ?", (at - FRESHNESS_SECONDS - SKEW_SECONDS,))
            row = db.execute("SELECT receipt,digest,event_at FROM inbound_receipts "
                             "WHERE binding=? AND revision=? AND event_id=?",
                             (binding, revision, event_id)).fetchone()
            if row is not None:
                if row["digest"] != digest or row["event_at"] != event_at:
                    raise ValueError("event_id_conflict")
                result = {"receipt": row["receipt"], "accepted": False}
            else:
                count = db.execute("SELECT count(*) FROM inbound_receipts WHERE binding=? "
                                   "AND accepted_at>?", (binding, at - 60)).fetchone()[0]
                if count >= 600:
                    raise OverflowError("rate_limited")
                receipt = "inb_" + secrets.token_hex(16)
                db.execute("INSERT INTO inbound_receipts VALUES (?,?,?,?,?,?,?,?,?,?,?,?,NULL)",
                           (receipt, binding, revision, event_id, event_at, digest, body,
                            owner, agent_ref, None, at, universe))
                result = {"receipt": receipt, "accepted": True}
            db.commit()
            return result
        except sqlite3.IntegrityError:
            db.rollback()
            row = db.execute("SELECT receipt,digest,event_at FROM inbound_receipts "
                             "WHERE binding=? AND revision=? AND event_id=?",
                             (binding, revision, event_id)).fetchone()
            if row is None or row["digest"] != digest or row["event_at"] != event_at:
                raise ValueError("event_id_conflict") from None
            return {"receipt": row["receipt"], "accepted": False}
        except BaseException:
            db.rollback()
            raise


def pending(base: Path, *, limit: int = 100) -> list[dict]:
    with closing(_connect(base)) as db:
        return [dict(row) for row in db.execute(
            "SELECT * FROM inbound_receipts WHERE activity_id IS NULL "
            "AND completed_at IS NULL ORDER BY accepted_at,receipt LIMIT ?", (limit,))]


def mark_started(base: Path, receipt: str, activity_id: str) -> None:
    with closing(_connect(base)) as db, db:
        db.execute("UPDATE inbound_receipts SET activity_id=? "
                   "WHERE receipt=? AND activity_id IS NULL", (activity_id, receipt))


def finish(base: Path, key: str) -> None:
    with closing(_connect(base)) as db, db:
        db.execute("UPDATE inbound_receipts SET completed_at=? WHERE receipt=?",
                   (time.time(), key))


def configure(base: Path, binding: str, revision: int, universe: str, agent_ref: str,
              *, enabled: bool = True, paused: bool = False) -> None:
    """Owner-local connector configuration; identifiers never select another box."""
    if (not binding or type(revision) is not int or revision < 1 or not agent_ref
            or not universe or Path(universe).name != universe or universe.startswith(".")
            or "/" in universe or "\\" in universe):
        raise ValueError("invalid inbound configuration")
    with closing(_connect(base)) as db, db:
        db.execute("INSERT INTO inbound_bindings VALUES (?,?,?,?,?,?) "
                   "ON CONFLICT(binding) DO UPDATE SET revision=excluded.revision, "
                   "universe=excluded.universe, agent_ref=excluded.agent_ref, "
                   "enabled=excluded.enabled, paused=excluded.paused",
                   (binding, revision, universe, agent_ref, enabled, paused))


def configuration(base: Path, binding: str) -> dict | None:
    with closing(_connect(base)) as db:
        row = db.execute("SELECT * FROM inbound_bindings WHERE binding=?", (binding,)).fetchone()
        return dict(row) if row else None


def receipt(base: Path, key: str) -> dict:
    with closing(_connect(base)) as db:
        row = db.execute("SELECT * FROM inbound_receipts WHERE receipt=?", (key,)).fetchone()
        if row is None:
            raise LookupError("unknown inbound receipt")
        return dict(row)


def migrate_hooks(base: Path, agents: dict[str, str], *, terminal_runs=()) -> list[str]:
    """Stopped, in-box cutover. Explicit branch/source-to-agent choices only.

    Preserve token digests and legacy replay evidence. Unlinked legacy work has
    no recoverable envelope, so it must be drained before this migration.
    """
    from tinyassets.storage import webhook_hooks

    with closing(webhook_hooks._connect(base)) as db:
        rows = db.execute("SELECT * FROM webhook_hooks WHERE revoked_at IS NULL").fetchall()
        inflight = db.execute("SELECT run_id FROM webhook_inflight").fetchall()
        if any(row[0] is None or row[0] not in terminal_runs for row in inflight):
            raise RuntimeError("legacy inbound work must drain before cutover")
    unmapped = []
    for row in rows:
        key = row["source_id"] or row["branch_def_id"]
        agent = agents.get(key)
        if not agent:
            unmapped.append(row["token_prefix"])
            continue
        configure(base, row["token_hash"], 1, row["universe_id"], agent)
    return unmapped


def needs_wake(base: Path) -> bool:
    from tinyassets.conversation_store import owner_send_state

    path = db_path(base)
    if not path.exists():
        return False
    with closing(sqlite3.connect(path)) as db:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE name='inbound_receipts'").fetchone():
            return False
        db.row_factory = sqlite3.Row
        rows = db.execute("SELECT r.* FROM inbound_receipts r JOIN inbound_bindings b "
                          "ON r.binding=b.binding AND r.revision=b.revision "
                          "WHERE b.enabled=1 AND b.paused=0 AND r.completed_at IS NULL").fetchall()
    for row in rows:
        home = base / row["universe"]
        if (home / ".pause").exists():
            continue
        if row["activity_id"] is None:
            return True
        state, _ = owner_send_state(home, row["activity_id"], row["receipt"])
        if state == "pending":
            return True
    return False
