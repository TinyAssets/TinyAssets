"""Durable push intents: what was sent, so a lost outcome can be settled.

A push is not idempotent from the daemon's side. If the process dies between
sending and recording the receipt, nothing local says whether the remote took
it -- and the two wrong answers are both bad: reporting success for a push that
never landed, or retrying one that did.

So the intent is written BEFORE the wire and settled after. On resume, every
intent still ``sent`` is asked of the remote: the same sha already at the ref
is ``done`` (a repeated non-force push of the same commit is not a failure),
anything else is ``failed`` and names what was observed. Design D1, crash
safety.

The table lives in the runs database beside the pool's, because it is settled
by the same startup path.
"""

from __future__ import annotations

import logging
import secrets
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

__all__ = [
    "INTENT_STATES",
    "PushIntent",
    "ensure_schema",
    "open_intents",
    "record_push_intent",
    "reconcile_push_intents",
    "settle_push_intent",
]

#: The only states an intent may hold. ``unknown`` is for one the remote could
#: not be asked about -- it stays owed rather than being guessed either way.
INTENT_STATES = ("sent", "done", "failed", "unknown")

#: Bounded retry for an intent a pass could not settle.
_BASE_RETRY_DELAY_S = 30.0
_MAX_RETRY_DELAY_S = 3600.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS workspace_push_intents (
    intent_id        TEXT PRIMARY KEY,
    run_id           TEXT NOT NULL,
    node_id          TEXT NOT NULL,
    connection_id    TEXT NOT NULL,
    grant_id         TEXT NOT NULL DEFAULT '',
    universe_id      TEXT NOT NULL DEFAULT '',
    host             TEXT NOT NULL DEFAULT '',
    repo             TEXT NOT NULL,
    remote_ref       TEXT NOT NULL,
    expected_old_sha TEXT,
    sha              TEXT NOT NULL,
    state            TEXT NOT NULL CHECK (state IN ('sent', 'done', 'failed', 'unknown')),
    observed_sha     TEXT,
    attempts         INTEGER NOT NULL DEFAULT 0,
    next_after       REAL,
    created_at       REAL NOT NULL,
    completed_at     REAL
);
CREATE INDEX IF NOT EXISTS workspace_push_intents_state
    ON workspace_push_intents (state);
"""


@dataclass(frozen=True)
class PushIntent:
    intent_id: str
    run_id: str
    node_id: str
    connection_id: str
    repo: str
    remote_ref: str
    sha: str
    state: str
    grant_id: str = ""
    universe_id: str = ""
    host: str = ""
    expected_old_sha: str | None = None
    observed_sha: str | None = None
    attempts: int = 0
    next_after: float | None = None
    created_at: float = 0.0
    completed_at: float | None = None


def _db(base_path: str | Path) -> Path:
    from tinyassets import runs

    return runs.runs_db_path(base_path)


def _connect(base_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(_db(base_path), timeout=30)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the table if it is not there. Safe to call on every write."""
    conn.executescript(_SCHEMA)


def record_push_intent(
    base_path: str | Path,
    *,
    run_id: str,
    node_id: str,
    connection_id: str,
    repo: str,
    remote_ref: str,
    sha: str,
    host: str,
    grant_id: str = "",
    universe_id: str = "",
    expected_old_sha: str | None = None,
    now: Callable[[], float] = time.time,
) -> str:
    """Journal the intent BEFORE the wire; return its id.

    Committed before the push is sent, so a crash mid-flight always leaves a
    row to reconcile against.
    """
    intent_id = secrets.token_hex(16)
    conn = _connect(base_path)
    try:
        ensure_schema(conn)
        conn.execute(
            "INSERT INTO workspace_push_intents (intent_id, run_id, node_id, "
            "connection_id, grant_id, universe_id, host, repo, remote_ref, "
            "expected_old_sha, sha, state, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'sent', ?)",
            (
                intent_id,
                str(run_id),
                str(node_id),
                str(connection_id),
                str(grant_id),
                str(universe_id),
                str(host),
                str(repo),
                str(remote_ref),
                expected_old_sha,
                str(sha),
                float(now()),
            ),
        )
        conn.commit()
    finally:
        conn.close()
    return intent_id


def settle_push_intent(
    base_path: str | Path,
    intent_id: str,
    state: str,
    *,
    observed_sha: str | None = None,
    now: Callable[[], float] = time.time,
) -> None:
    """Mark an intent after the wire answered. Never raises into the push path."""
    if state not in INTENT_STATES:
        raise ValueError(f"state must be one of {INTENT_STATES}, got {state!r}")
    conn = _connect(base_path)
    try:
        ensure_schema(conn)
        conn.execute(
            "UPDATE workspace_push_intents SET state = ?, observed_sha = ?, "
            "completed_at = ? WHERE intent_id = ?",
            (state, observed_sha, float(now()), str(intent_id)),
        )
        conn.commit()
    finally:
        conn.close()


def open_intents(base_path: str | Path) -> list[PushIntent]:
    """Every intent still ``sent``: the ones whose outcome nobody recorded."""
    conn = _connect(base_path)
    try:
        ensure_schema(conn)
        rows = conn.execute(
            "SELECT * FROM workspace_push_intents WHERE state = 'sent' "
            "ORDER BY created_at"
        ).fetchall()
    finally:
        conn.close()
    return [
        PushIntent(
            intent_id=row["intent_id"],
            run_id=row["run_id"],
            node_id=row["node_id"],
            connection_id=row["connection_id"],
            repo=row["repo"],
            remote_ref=row["remote_ref"],
            sha=row["sha"],
            state=row["state"],
            grant_id=row["grant_id"],
            universe_id=row["universe_id"],
            host=row["host"],
            expected_old_sha=row["expected_old_sha"],
            observed_sha=row["observed_sha"],
            attempts=row["attempts"],
            next_after=row["next_after"],
            created_at=row["created_at"],
            completed_at=row["completed_at"],
        )
        for row in rows
    ]


def reconcile_push_intents(
    base_path: str | Path,
    *,
    execute: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
    revalidate: Callable[[PushIntent], bool] | None = None,
    host: str = "",
    staging_root: str | Path | None = None,
) -> list[tuple[str, str]]:
    """Settle every open intent by ASKING the remote. Returns (intent_id, state).

    The same sha already at the ref is ``done``: a repeated non-force push of
    the same commit is success, not a failure. Anything else is ``failed`` with
    the observed ref recorded. An intent the remote could not be asked about
    becomes ``unknown`` and stays owed -- never guessed in either direction.
    """
    intents = open_intents(base_path)
    if not intents:
        return []
    if execute is None:
        from tinyassets.workspace_worker import execute_workspace_operation

        execute = execute_workspace_operation

    # No staging: the probe is one route-only ls-remote inside the owner's own
    # cell, so there is no local directory to make, hold or sweep.
    del staging_root
    settled: list[tuple[str, str]] = []
    for intent in intents:
        _reconcile_one(
            base_path, intent, settled,
            execute=execute, revalidate=revalidate, host=host,
        )
    return settled


def _reconcile_one(
    base_path: str | Path,
    intent: PushIntent,
    settled: list[tuple[str, str]],
    *,
    execute: Callable[[dict[str, Any]], dict[str, Any]],
    revalidate: Callable[[PushIntent], bool] | None,
    host: str,
) -> None:
    """Settle one intent. Appends its (intent_id, state) to ``settled``."""
    # The AUTHORITY is revalidated before the host is contacted: a grant
    # revoked since the push must not be used to ask about it.
    if revalidate is not None:
        try:
            allowed = bool(revalidate(intent))
        except Exception:
            logging.getLogger(__name__).exception(
                "intent revalidation crashed for %s", intent.intent_id
            )
            allowed = False
        if not allowed:
            _defer(base_path, intent, reason="authority")
            settled.append((intent.intent_id, "sent"))
            return

    try:
        principal = _intent_principal(base_path, intent)
    except Exception:
        # No unknown authority may reach even a read-only remote probe.
        _defer(base_path, intent, reason="broker authority unavailable")
        settled.append((intent.intent_id, "sent"))
        return

    # The intent's OWN host, never a module default: defaulting to
    # github.com would contact a host this push never used (round 3 P0 #1).
    intent_host = intent.host or host
    if not intent_host:
        _defer(base_path, intent, reason="no host recorded")
        settled.append((intent.intent_id, "sent"))
        return

    answer: dict[str, Any]
    try:
        answer = execute(
            {
                "op": "ls_remote",
                "universe_dir": str(base_path),
                "principal": principal,
                "agent": f"workspace-intent-{intent.intent_id[:16]}",
                "grant_id": intent.grant_id,
                "connection_id": intent.connection_id,
                "host": intent_host,
                "owner_repo": intent.repo,
                "remote_ref": intent.remote_ref,
            }
        )
    except Exception:
        answer = {"ok": False}

    if not answer.get("ok"):
        # A TRANSPORT failure answers nothing. Leaving it `sent` keeps it
        # claimable by the next pass; marking it `unknown` here would
        # retire an intent nobody ever asked about (round 3, P1 #5).
        _defer(base_path, intent, reason="transport")
        settled.append((intent.intent_id, "sent"))
        return

    # A SUCCESSFUL ls-remote with no sha means the ref is absent, which is
    # a real answer: the push did not land.
    observed = str(answer.get("observed_sha") or "")
    state = "done" if observed and observed == intent.sha else "failed"
    settle_push_intent(base_path, intent.intent_id, state, observed_sha=observed or None)
    settled.append((intent.intent_id, state))


def _defer(base_path: str | Path, intent: PushIntent, *, reason: str) -> None:
    """Leave an intent claimable, recording that a pass could not settle it.

    ``attempts`` and ``next_after`` are the bounded-retry metadata: a pass can
    skip one it tried moments ago without losing it.
    """
    delay = min(_MAX_RETRY_DELAY_S, _BASE_RETRY_DELAY_S * (2 ** min(intent.attempts, 8)))
    conn = _connect(base_path)
    try:
        ensure_schema(conn)
        conn.execute(
            "UPDATE workspace_push_intents SET attempts = attempts + 1, "
            "next_after = ? WHERE intent_id = ?",
            (time.time() + delay, intent.intent_id),
        )
        conn.commit()
    except Exception:
        logging.getLogger(__name__).exception("could not defer intent %s", intent.intent_id)
    finally:
        conn.close()
    logging.getLogger(__name__).info(
        "push intent %s deferred (%s), attempt %d", intent.intent_id, reason, intent.attempts + 1
    )


def _intent_principal(base_path: str | Path, intent: PushIntent) -> str:
    """The owner whose authority settles this intent.

    Recovered from the daemon's persisted run, never from intent payloads, and
    revalidated end to end -- grant, host, scope, consent -- before anything
    reaches even a read-only remote probe. What comes back is the PRINCIPAL: no
    credential crosses this boundary any more, because the probe runs in that
    owner's own cell and only the broker resolves the token.
    """
    from tinyassets import runs
    from tinyassets.broker.ledger_queries import authorized_connection
    from tinyassets.effectors.workspace import _require_consent, _require_scope, transport_host_for
    from tinyassets.storage.workspace_authority import connection_access_mode

    base = Path(base_path)
    run = runs.get_run(base.parent, intent.run_id)
    if (not run or not run.get("owner_user_id")
            or run.get("queue_universe_id") != base.name or intent.universe_id != base.name):
        raise PermissionError("intent has no admitted run scope")
    _, resource, _ = authorized_connection(
        base.parent, principal=run["owner_user_id"], command_center=base.name,
        grant_id=intent.grant_id, connection_id=intent.connection_id)
    host = transport_host_for(resource)
    if not intent.host or intent.host != host:
        raise PermissionError("intent destination changed")
    _require_scope(resource, "push", host, intent.repo)
    _require_consent(base, "push", host, intent.repo, intent.connection_id,
                     access_mode=connection_access_mode(resource))
    if not resource.credential_ref:
        raise PermissionError("intent custody unavailable")
    return run["owner_user_id"]
