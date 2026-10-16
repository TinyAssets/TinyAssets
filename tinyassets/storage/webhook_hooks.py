"""Per-branch inbound webhook tokens + atomic inbound gates (channel-agnostic inbound).

A hook token is an unguessable secret that binds ONE public URL to exactly one
(universe, branch): a `POST /hooks/<token>` runs that branch as that universe. The
token is the ONLY authority — nothing in the request can redirect the run.

Secret-free at rest: a row holds identifiers plus the token's SHA-256 HASH (never the raw
token) and a short non-secret prefix for display (Codex #6). This module also owns the
three ATOMIC inbound gates, each serialized by a single `BEGIN IMMEDIATE` transaction so
they hold under CONCURRENCY (Codex round-2):

- ``claim_delivery`` — server-side replay dedupe by INSERT-under-PRIMARY-KEY (Codex #4).
- ``reserve_dispatch`` — one transaction that re-checks the token is ACTIVE (serializing
  with a concurrent ``revoke`` on the same DB — closes the revocation TOCTOU, Codex #3) AND
  reserves an in-flight slot IFF the universe is under its cap (closes the count-then-act
  back-pressure race, Codex #5). Both the plain and source paths reserve here, so one
  counter bounds both.
- ``admit`` — durable per-token/per-universe rolling-window RATE limit (Codex #3), atomic.
"""

from __future__ import annotations

import hashlib
import secrets
import sqlite3
import threading
import time
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from tinyassets.storage import db_path

#: Bound the ids we persist — a webhook binding is identifiers, never a body/secret.
MAX_ID_LEN = 256

#: Characters of the raw token kept in plaintext for display/identification only. A
#: prefix this short cannot be used to invoke (the full token is 43 url-safe chars).
_PREFIX_LEN = 12

_SCHEMA = """
CREATE TABLE IF NOT EXISTS webhook_hooks (
    token_hash     TEXT PRIMARY KEY,
    token_prefix   TEXT NOT NULL DEFAULT '',
    universe_id    TEXT NOT NULL,
    branch_def_id  TEXT NOT NULL,
    created_at     REAL NOT NULL,
    revoked_at     REAL,
    source_id      TEXT,
    owner_principal_id TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_webhook_hooks_universe
    ON webhook_hooks(universe_id);

-- Durable, aggregate RATE admission log (Codex #3). One row per admitted request; a
-- sliding-window count bounds per-token AND per-universe rate, survives restart, shared
-- across workers. Keyed by the token HASH.
CREATE TABLE IF NOT EXISTS webhook_admissions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    token_hash   TEXT NOT NULL,
    universe_id  TEXT NOT NULL,
    ts           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_adm_ts ON webhook_admissions(ts);
CREATE INDEX IF NOT EXISTS idx_webhook_adm_token ON webhook_admissions(token_hash, ts);
CREATE INDEX IF NOT EXISTS idx_webhook_adm_universe
    ON webhook_admissions(universe_id, ts);

-- Durable server-side replay dedupe (Codex #4). The key is derived server-side from
-- (token, exact body) — NEVER a caller header — and is the PRIMARY KEY, so a concurrent
-- duplicate loses the INSERT race and is refused as a replay.
CREATE TABLE IF NOT EXISTS webhook_deliveries (
    dedupe_key   TEXT PRIMARY KEY,
    ts           REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_deliveries_ts ON webhook_deliveries(ts);

-- In-flight concurrency reservations (Codex #5). One row per accepted-but-not-terminal
-- inbound run. Reserved atomically under the per-universe cap; released when its linked
-- run reaches a terminal state (reconciled), on explicit failure, or by TTL if abandoned.
CREATE TABLE IF NOT EXISTS webhook_inflight (
    reservation_id TEXT PRIMARY KEY,
    universe_id    TEXT NOT NULL,
    run_id         TEXT,
    ts             REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_webhook_inflight_universe ON webhook_inflight(universe_id);
"""

#: Per-process guard so we do not re-run the schema DDL / migration on every request.
_initialized: set[str] = set()
_init_lock = threading.Lock()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _connect(base_path: str | Path) -> sqlite3.Connection:
    path = db_path(Path(base_path))
    conn = sqlite3.connect(path, timeout=30.0)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 30000")
    key = str(path)
    if key not in _initialized:
        with _init_lock:
            if key not in _initialized:
                conn.executescript(_SCHEMA)
                _migrate_hooks_to_hashed(conn)
                _migrate_hooks_owner(conn)
                _initialized.add(key)
    return conn


def _migrate_hooks_owner(conn: sqlite3.Connection) -> None:
    """Add ``owner_principal_id`` to a store created before hooks recorded
    their owner (authenticated-owner boundary D2). Rows keep '' and REFUSE to
    deliver until their owner re-creates them: a hook that runs as nobody is
    the thing this change removes."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(webhook_hooks)")}
    if "owner_principal_id" not in columns:
        conn.execute(
            "ALTER TABLE webhook_hooks ADD COLUMN owner_principal_id TEXT NOT NULL DEFAULT ''"
        )


def _migrate_hooks_to_hashed(conn: sqlite3.Connection) -> None:
    """Transactionally migrate a pre-existing plaintext-token table to the hashed schema.

    The prior committed schema keyed ``webhook_hooks`` by the RAW ``token`` (and lacked
    ``source_id``). ``CREATE TABLE IF NOT EXISTS`` cannot alter it, so without this the new
    code hits ``no column named token_hash`` / ``no such column: source_id`` and the raw
    token stays plaintext (Codex #4-migration). This rebuilds the table: hash each existing
    token into ``token_hash`` + ``token_prefix``, carry ``source_id`` if present, and drop
    the plaintext column — all in one transaction.
    """
    cols = {row[1] for row in conn.execute("PRAGMA table_info(webhook_hooks)")}
    if "token_hash" in cols or "token" not in cols:
        return  # already hashed (fresh table) or nothing to migrate
    had_source = "source_id" in cols
    conn.create_function("_sha256_hex", 1, lambda t: _hash_token(t or ""))
    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("ALTER TABLE webhook_hooks RENAME TO webhook_hooks_legacy")
        conn.executescript(
            "CREATE TABLE webhook_hooks ("
            " token_hash TEXT PRIMARY KEY, token_prefix TEXT NOT NULL DEFAULT '',"
            " universe_id TEXT NOT NULL, branch_def_id TEXT NOT NULL,"
            " created_at REAL NOT NULL, revoked_at REAL, source_id TEXT);"
        )
        source_expr = "source_id" if had_source else "NULL"
        conn.execute(
            "INSERT INTO webhook_hooks "
            "(token_hash, token_prefix, universe_id, branch_def_id, created_at, "
            " revoked_at, source_id) "
            f"SELECT _sha256_hex(token), substr(token, 1, {_PREFIX_LEN}), universe_id, "
            f"branch_def_id, created_at, revoked_at, {source_expr} FROM webhook_hooks_legacy"
        )
        conn.execute("DROP TABLE webhook_hooks_legacy")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_webhook_hooks_universe "
            "ON webhook_hooks(universe_id)"
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def mint(
    base_path: str | Path,
    *,
    universe_id: str,
    branch_def_id: str,
    owner_principal_id: str,
    source_id: str | None = None,
    now: float | None = None,
) -> str:
    """Mint a fresh unguessable hook token. Returns the RAW token (shown once); only its
    hash + prefix are persisted. The mint OPERATION checks ownership + authorship.

    ``owner_principal_id`` is the authenticated caller; every delivery on this
    hook runs as them (authenticated-owner boundary D2). Required and non-empty."""
    for name, val in (
        ("universe_id", universe_id),
        ("branch_def_id", branch_def_id),
        ("owner_principal_id", owner_principal_id),
    ):
        if not isinstance(val, str) or not val:
            raise ValueError(f"webhook_hooks: {name} must be a non-empty string")
        if len(val) > MAX_ID_LEN:
            raise ValueError(f"webhook_hooks: {name} exceeds {MAX_ID_LEN} chars")
    if source_id is not None:
        if not isinstance(source_id, str) or not source_id:
            raise ValueError("webhook_hooks: source_id must be a non-empty string or None")
        if len(source_id) > MAX_ID_LEN:
            raise ValueError(f"webhook_hooks: source_id exceeds {MAX_ID_LEN} chars")
    from tinyassets.owner_runtime import current, router_credential

    runtime = current()
    token = (router_credential("hook_mint")["token"] if runtime is not None
             else secrets.token_urlsafe(32))
    if runtime is not None:
        owner_principal_id = runtime.principal
    ts = time.time() if now is None else now
    conn = None
    try:
        conn = _connect(base_path)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "INSERT INTO webhook_hooks "
            "(token_hash, token_prefix, universe_id, branch_def_id, created_at, source_id, "
            "owner_principal_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                _hash_token(token), token[:_PREFIX_LEN], universe_id, branch_def_id, ts,
                source_id, owner_principal_id,
            ),
        )
        conn.commit()
        return token
    except Exception:
        if runtime is not None:
            router_credential("hook_revoke", token_hash=_hash_token(token))
        raise
    finally:
        if conn is not None:
            conn.close()


def resolve(base_path: str | Path, *, token: str) -> dict[str, Any] | None:
    """Return ``{universe_id, branch_def_id, source_id, owner_principal_id}`` for an
    ACTIVE token, else None.

    None covers unknown, revoked, and empty/malformed tokens alike (no enumeration signal).
    Resolution hashes the presented token; the raw token is never stored."""
    if not isinstance(token, str) or not token:
        return None
    conn = _connect(base_path)
    try:
        row = conn.execute(
            "SELECT universe_id, branch_def_id, source_id, owner_principal_id "
            "FROM webhook_hooks WHERE token_hash = ? AND revoked_at IS NULL",
            (_hash_token(token),),
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def admit(
    base_path: str | Path,
    *,
    token: str,
    universe_id: str,
    token_max: int,
    window_s: float,
    now: float | None = None,
) -> bool:
    """Durable atomic sliding-window per-TOKEN rate admission. True if admitted.

    Per-token only. The per-universe aggregate that used to sit beside it was an
    account limit, and an account has exactly two -- cloud bytes and concurrent
    agent seats (founder, 2026-09-30). This one survives because it is not aimed
    at the account holder: ``/hooks/<token>`` is an unauthenticated public
    ingress, and the flood it stops comes from a stranger who found the URL.
    """
    token_hash = _hash_token(token)
    ts = time.time() if now is None else now
    cutoff = ts - window_s
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM webhook_admissions WHERE ts <= ?", (cutoff,))
        tok_count = conn.execute(
            "SELECT COUNT(*) FROM webhook_admissions WHERE token_hash = ? AND ts > ?",
            (token_hash, cutoff),
        ).fetchone()[0]
        if tok_count >= token_max:
            conn.commit()
            return False
        conn.execute(
            "INSERT INTO webhook_admissions (token_hash, universe_id, ts) VALUES (?, ?, ?)",
            (token_hash, universe_id, ts),
        )
        conn.commit()
        return True
    finally:
        conn.close()


def claim_delivery(
    base_path: str | Path,
    *,
    dedupe_key: str,
    window_s: float,
    now: float | None = None,
) -> bool:
    """ATOMIC replay dedupe (Codex #4). True if this delivery is NEW (claimed), False if it
    is a replay. The claim is an INSERT under the ``dedupe_key`` PRIMARY KEY, so N concurrent
    identical deliveries produce exactly ONE winner; the losers get IntegrityError → replay.
    Deliveries older than the window are pruned first, so an identical body far apart re-runs."""
    ts = time.time() if now is None else now
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM webhook_deliveries WHERE ts <= ?", (ts - window_s,))
        try:
            conn.execute(
                "INSERT INTO webhook_deliveries (dedupe_key, ts) VALUES (?, ?)",
                (dedupe_key, ts),
            )
        except sqlite3.IntegrityError:
            conn.rollback()
            return False
        conn.commit()
        return True
    finally:
        conn.close()


def release_delivery(base_path: str | Path, *, dedupe_key: str) -> None:
    """Un-claim a delivery so a legitimate retry can proceed (called when a downstream gate
    rejects AFTER the claim — rate-limited/busy/revoked/enqueue-failed)."""
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM webhook_deliveries WHERE dedupe_key = ?", (dedupe_key,))
        conn.commit()
    finally:
        conn.close()


def reserve_dispatch(
    base_path: str | Path,
    *,
    token: str,
    universe_id: str,
    ttl_s: float,
    terminal_run_ids: Iterable[str] = (),
    now: float | None = None,
) -> tuple[str | None, str]:
    """ATOMIC combined active-check + in-flight reservation (Codex #3).

    In ONE ``BEGIN IMMEDIATE`` transaction: (1) re-verify the token is still ACTIVE — this
    serializes with a concurrent ``revoke`` on the same DB, so a revoke cannot land between
    the check and the reservation; (2) reconcile away reservations whose run finished
    (``terminal_run_ids``) or that were abandoned (unlinked past TTL); (3) reserve.
    Returns ``(reservation_id, "ok")`` on success, else ``(None, "revoked")``.

    There is no in-flight ceiling. The ``cap`` this took used to answer ``"busy"``
    and drop the delivery with a 503 past 20 concurrent runs; an inbound-triggered
    run now queues for a seat like any other run (founder, 2026-09-30: over the
    seat count work WAITS and is never refused). The reservation row stays: it is
    what serializes against revoke and what ``link_dispatch`` binds to a run."""
    token_hash = _hash_token(token)
    ts = time.time() if now is None else now
    terminal = [t for t in terminal_run_ids if t]
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        active = conn.execute(
            "SELECT 1 FROM webhook_hooks WHERE token_hash = ? AND revoked_at IS NULL",
            (token_hash,),
        ).fetchone()
        if active is None:
            conn.commit()
            return None, "revoked"
        if terminal:
            conn.execute(
                "DELETE FROM webhook_inflight WHERE run_id IN "
                f"({','.join('?' * len(terminal))})",
                tuple(terminal),
            )
        conn.execute(
            "DELETE FROM webhook_inflight WHERE run_id IS NULL AND ts <= ?",
            (ts - ttl_s,),
        )
        reservation_id = secrets.token_hex(16)
        conn.execute(
            "INSERT INTO webhook_inflight (reservation_id, universe_id, run_id, ts) "
            "VALUES (?, ?, ?, ?)",
            (reservation_id, universe_id, None, ts),
        )
        conn.commit()
        return reservation_id, "ok"
    finally:
        conn.close()


def link_dispatch(base_path: str | Path, *, reservation_id: str, run_id: str) -> None:
    """Bind a reservation to the run it authorized, so it is released when the run finishes."""
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "UPDATE webhook_inflight SET run_id = ? WHERE reservation_id = ?",
            (run_id, reservation_id),
        )
        conn.commit()
    finally:
        conn.close()


def release_dispatch(base_path: str | Path, *, reservation_id: str) -> None:
    """Release a reservation immediately (a downstream step failed before/without a run)."""
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            "DELETE FROM webhook_inflight WHERE reservation_id = ?", (reservation_id,)
        )
        conn.commit()
    finally:
        conn.close()


def list_active_for_branch(base_path: str | Path, *, branch_def_id: str) -> list[dict[str, Any]]:
    """Active (unrevoked) hooks that would enqueue this branch on delivery.
    Returns non-secret rows: ``{token_prefix, universe_id, source_id}``."""
    conn = _connect(base_path)
    try:
        rows = conn.execute(
            "SELECT token_prefix, universe_id, source_id FROM webhook_hooks "
            "WHERE branch_def_id = ? AND revoked_at IS NULL ORDER BY created_at",
            (branch_def_id,),
        ).fetchall()
    finally:
        conn.close()
    return [
        {
            "token_prefix": r["token_prefix"],
            "universe_id": r["universe_id"],
            "source_id": r["source_id"],
        }
        for r in rows
    ]


def _revoke_routes(conn, where: str, values: tuple):
    from tinyassets.owner_runtime import current, router_credential

    if current() is not None:
        for row in conn.execute(f"SELECT token_hash FROM webhook_hooks WHERE {where}", values):
            router_credential("hook_revoke", token_hash=row["token_hash"])


def revoke(base_path: str | Path, *, token: str, now: float | None = None) -> bool:
    """Revoke a token by its value (soft: kept for audit). Returns True if an active token
    was revoked. The token is hashed for lookup; the raw value is never stored."""
    ts = time.time() if now is None else now
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _revoke_routes(conn, "token_hash = ? AND revoked_at IS NULL", (_hash_token(token),))
        cur = conn.execute(
            "UPDATE webhook_hooks SET revoked_at = ? "
            "WHERE token_hash = ? AND revoked_at IS NULL",
            (ts, _hash_token(token)),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def revoke_by_prefix(
    base_path: str | Path, *, universe_id: str, token_prefix: str, now: float | None = None,
) -> bool:
    """Revoke one plain (non-Source) hook of ``universe_id`` by its listed prefix.

    The raw token is shown once and never stored, so an owner who has lost it can
    only name a hook by the non-secret prefix ``list_for_universe`` returns. The
    match is confined to the owning universe and must be EXACTLY one active row:
    an ambiguous prefix revokes nothing rather than guessing which hook was meant.
    """
    prefix = (token_prefix or "").strip()
    if not universe_id or len(prefix) != _PREFIX_LEN:
        return False
    ts = time.time() if now is None else now
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        where = (
            "universe_id = ? AND token_prefix = ? AND revoked_at IS NULL "
            "AND (source_id IS NULL OR source_id = '')"
        )
        matches = conn.execute(
            f"SELECT COUNT(*) FROM webhook_hooks WHERE {where}", (universe_id, prefix),
        ).fetchone()[0]
        if matches != 1:
            conn.rollback()
            return False
        _revoke_routes(conn, where, (universe_id, prefix))
        cur = conn.execute(
            f"UPDATE webhook_hooks SET revoked_at = ? WHERE {where}",
            (ts, universe_id, prefix),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def revoke_source(
    base_path: str | Path, *, universe_id: str, source_id: str, now: float | None = None,
) -> bool:
    """Revoke a Source's hook by (universe_id, source_id), scoped to the owning universe."""
    ts = time.time() if now is None else now
    conn = _connect(base_path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _revoke_routes(conn, "universe_id = ? AND source_id = ? AND revoked_at IS NULL",
                       (universe_id, source_id))
        cur = conn.execute(
            "UPDATE webhook_hooks SET revoked_at = ? "
            "WHERE universe_id = ? AND source_id = ? AND revoked_at IS NULL",
            (ts, universe_id, source_id),
        )
        conn.commit()
        return bool(cur.rowcount)
    finally:
        conn.close()


def list_for_universe(
    base_path: str | Path, *, universe_id: str,
) -> list[dict[str, Any]]:
    """All hooks (active + revoked) for one universe, newest first. Returns the non-secret
    ``token_prefix`` — NEVER the raw token (shown only once at mint). Owner-only."""
    conn = _connect(base_path)
    try:
        rows = conn.execute(
            "SELECT token_prefix, branch_def_id, created_at, revoked_at, source_id "
            "FROM webhook_hooks WHERE universe_id = ? ORDER BY created_at DESC",
            (universe_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()
