"""Per-destination effector consent grants — PR-122 Phase 2 Slice 1.

Per-universe SQLite store recording user grants of the form
*"this universe's effectors may write to destination D via sink S."*
The grant is one of the gates every external-write sink consults before
it fires (connection grant + consent grant + idempotency receipt).

Design source: ``drafts/concepts/external-write-phase-2-authority.md``
§3 "Per-destination consent surface".

Schema (per-universe, file: ``${universe_dir}/.effector_consents.db``):

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS effector_consents (
        sink         TEXT NOT NULL,
        destination  TEXT NOT NULL,
        granted_at   REAL NOT NULL,
        granted_by   TEXT NOT NULL,
        revoked_at   REAL,
        PRIMARY KEY (sink, destination)
    );

Reads filter ``revoked_at IS NULL``. Revocation flips ``revoked_at`` to
``time.time()``; future invocations dry-run. Re-granting after revoke
clears ``revoked_at`` and refreshes ``granted_at`` / ``granted_by``.

No wildcard grants in Slice 1 — that's a Slice 2 refinement once we
see real grant-list shape. The packet's ``destination`` field must
match a granted row exactly (case-sensitive).
"""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Any

from tinyassets.sqlite_connection import ClosingConnection

_DB_FILENAME = ".effector_consents.db"


def consents_db_path(universe_dir: str | Path) -> Path:
    """Resolve the per-universe consents DB path."""
    return Path(universe_dir) / _DB_FILENAME


def _connect(universe_dir: str | Path) -> sqlite3.Connection:
    """Open the consents DB with WAL + 30s busy timeout."""
    path = consents_db_path(universe_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


_SCHEMA = """
CREATE TABLE IF NOT EXISTS effector_consents (
    sink         TEXT NOT NULL,
    destination  TEXT NOT NULL,
    granted_at   REAL NOT NULL,
    granted_by   TEXT NOT NULL,
    revoked_at   REAL,
    PRIMARY KEY (sink, destination)
);

CREATE INDEX IF NOT EXISTS idx_consents_active
    ON effector_consents(sink) WHERE revoked_at IS NULL;
"""


def initialize_consents_db(universe_dir: str | Path) -> Path:
    """Ensure the consents DB exists and is migrated. Returns the DB path."""
    path = consents_db_path(universe_dir)
    with _connect(universe_dir) as conn:
        conn.executescript(_SCHEMA)
        conn.commit()
    return path


def grant_consent(
    universe_dir: str | Path,
    *,
    sink: str,
    destination: str,
    granted_by: str,
    granted_at: float | None = None,
) -> dict[str, Any]:
    """Record an active grant for ``(sink, destination)``.

    Re-granting after revoke clears ``revoked_at`` and refreshes the
    metadata so the chatbot's grant-list view shows the new approver.

    Raises ``ValueError`` when required fields are empty — a chatbot
    that calls without specifying which destination it's granting
    consent to is a contract violation, not a recoverable error.
    """
    from tinyassets.principals import named_principal

    granted_by = named_principal(granted_by)
    if not sink:
        raise ValueError("grant_consent requires non-empty sink")
    if not destination:
        raise ValueError("grant_consent requires non-empty destination")
    if not granted_by:
        raise ValueError("grant_consent requires non-empty granted_by")
    initialize_consents_db(universe_dir)
    ts = granted_at if granted_at is not None else time.time()
    with _connect(universe_dir) as conn:
        conn.execute(
            """
            INSERT INTO effector_consents (
                sink, destination, granted_at, granted_by, revoked_at
            ) VALUES (?, ?, ?, ?, NULL)
            ON CONFLICT(sink, destination) DO UPDATE SET
                granted_at = excluded.granted_at,
                granted_by = excluded.granted_by,
                revoked_at = NULL
            """,
            (sink, destination, ts, granted_by),
        )
        conn.commit()
    return {
        "sink": sink,
        "destination": destination,
        "granted_at": ts,
        "granted_by": granted_by,
        "revoked_at": None,
    }


def revoke_consent(
    universe_dir: str | Path,
    *,
    sink: str,
    destination: str,
    revoked_at: float | None = None,
) -> bool:
    """Flip ``revoked_at`` on the grant row. Returns True when a row was hit.

    No error when the row is absent — revoking a never-granted
    destination is a no-op (the desired end-state is "not granted",
    which is already true).

    Re-revoking an already-revoked grant updates the timestamp so the
    chatbot can see the most recent revocation moment if it cares.
    """
    if not sink or not destination:
        return False
    initialize_consents_db(universe_dir)
    ts = revoked_at if revoked_at is not None else time.time()
    with _connect(universe_dir) as conn:
        cur = conn.execute(
            """
            UPDATE effector_consents
               SET revoked_at = ?
             WHERE sink = ? AND destination = ?
            """,
            (ts, sink, destination),
        )
        conn.commit()
        return cur.rowcount > 0


#: The sink whose consent destinations embed the connection id
#: (``"<op>:<connection_id>:<host>/<owner>/<name>"``, per
#: ``workspace_consent_destination``).
_CONNECTION_KEYED_SINK = "workspace"
#: The sink whose consent destination is the connection's DESTINATION LABEL
#: with no id in it at all -- so it cannot be found by scanning for the id, and
#: survived removal to be inherited by the next deposit under the same name.
_DESTINATION_KEYED_SINK = "authenticated_external_call"


def _keyed_on_connection(
    sink: str,
    destination: str,
    connection_id: str,
    connection_destination: str,
) -> bool:
    """Whether this consent row belongs to THIS connection.

    Per sink, because the two sinks key differently and a single positional
    guess was wrong in both directions (Codex code review round 1): scanning
    every sink for ``split(":")[1]`` swept in a foreign row whose destination
    merely LOOKED like ``x:<id>:y``, and missed the connection's own outbound
    consent, which carries the destination label and no id.
    """
    if sink == _CONNECTION_KEYED_SINK:
        return destination.split(":")[1:2] == [connection_id]
    if sink == _DESTINATION_KEYED_SINK:
        return bool(connection_destination) and destination == connection_destination
    return False


def revoke_consents_for_connection(
    universe_dir: str | Path,
    *,
    connection_id: str,
    destination: str = "",
    revoked_at: float | None = None,
) -> list[str]:
    """Revoke every consent keyed on ``connection_id``. Returns their
    destinations, so the caller can tell the owner what it took back.

    A workspace consent destination is ``"<op>:<connection_id>:<host>/<repo>"``
    (``workspace_consent_destination``), so the connection is IN the key and a
    removal can find its own rows without touching anyone else's. Scoped on
    purpose: a universe holds several connections, and a universe-wide sweep
    would revoke consents for keys the owner never removed.

    Why it exists: ``remove_http`` deleted the ledger row and left these
    active, and a connection id is deterministic per ``(universe,
    destination)`` -- so re-depositing the same destination silently inherited
    the old repository consents. Taking a key back has to take back everything
    it authorized (full-channel-access D6).
    """
    token = str(connection_id or "").strip()
    destination_token = str(destination or "").strip()
    if not token:
        return []
    initialize_consents_db(universe_dir)
    ts = revoked_at if revoked_at is not None else time.time()
    # The connection id is the SECOND colon-separated field. Matching on the
    # exact position, not a substring, so a repository or host that happens to
    # contain another connection's name cannot be swept in.
    with _connect(universe_dir) as conn:
        rows = conn.execute(
            "SELECT sink, destination FROM effector_consents WHERE revoked_at IS NULL"
        ).fetchall()
        hits = [
            (row["sink"], row["destination"])
            for row in rows
            if _keyed_on_connection(
                str(row["sink"]), str(row["destination"]), token, destination_token
            )
        ]
        for sink, destination in hits:
            conn.execute(
                "UPDATE effector_consents SET revoked_at = ? "
                "WHERE sink = ? AND destination = ?",
                (ts, sink, destination),
            )
        conn.commit()
    return sorted(destination for _, destination in hits)


def is_consent_active(
    universe_dir: str | Path,
    *,
    sink: str,
    destination: str,
) -> bool:
    """Return True iff a row exists with the (sink, destination) and
    ``revoked_at IS NULL``. Exact-match (case-sensitive). Empty inputs
    return False so a packet missing ``destination`` cannot accidentally
    match a wildcard-shaped row.
    """
    if not sink or not destination:
        return False
    initialize_consents_db(universe_dir)
    with _connect(universe_dir) as conn:
        row = conn.execute(
            """
            SELECT 1 FROM effector_consents
             WHERE sink = ? AND destination = ? AND revoked_at IS NULL
             LIMIT 1
            """,
            (sink, destination),
        ).fetchone()
    return row is not None


def list_consents(
    universe_dir: str | Path,
    *,
    sink: str | None = None,
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """Return consent rows. By default, only active grants.

    Used by the ``list_effector_consents`` MCP action so the chatbot
    can render the current grant table to the user.
    """
    initialize_consents_db(universe_dir)
    clauses: list[str] = []
    params: list[Any] = []
    if sink:
        clauses.append("sink = ?")
        params.append(sink)
    if active_only:
        clauses.append("revoked_at IS NULL")
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect(universe_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT sink, destination, granted_at, granted_by, revoked_at
              FROM effector_consents
              {where}
          ORDER BY granted_at DESC
            """,
            params,
        ).fetchall()
    return [
        {
            "sink": row["sink"],
            "destination": row["destination"],
            "granted_at": row["granted_at"],
            "granted_by": row["granted_by"],
            "revoked_at": row["revoked_at"],
        }
        for row in rows
    ]


__all__ = [
    "consents_db_path",
    "initialize_consents_db",
    "grant_consent",
    "revoke_consent",
    "is_consent_active",
    "list_consents",
]
