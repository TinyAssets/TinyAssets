"""External-write idempotency receipts — PR-122 Phase 2.

Per-universe SQLite store recording every external-write attempt so
concurrent runs do not produce duplicate side-effects.

Design source: ``drafts/concepts/external-write-phase-2-authority.md``
§2 "Idempotency store". The store is one of the gates every
external-write sink consults before it fires (connection grant + consent
grant + idempotency receipt).

Round-2 fix for Codex P1.1
--------------------------

Round-1 of this slice ran a lookup → invoke → write sequence that was
non-atomic; two concurrent run threads could both observe "no receipt"
and both invoke ``gh pr create``, producing duplicate PRs. SQLite
``database is locked`` errors were also silently treated as a miss,
which compounded the leak.

The fix is **atomic reservation**: a writer must call
:func:`try_reserve_receipt` BEFORE invoking the external side-effect.
The reservation uses ``INSERT … ON CONFLICT DO NOTHING`` so SQLite's
row-level lock makes the "is anyone else doing this right now?"
question answerable in one round-trip. The reservation lives in a
new ``status`` column with values:

* ``pending``    — reservation held; ``gh pr create`` is in flight.
* ``succeeded``  — receipt is final; future calls dedup-hit.
* ``failed``     — invocation failed; the row remains so the caller
                    can decide whether to retry under the same hint
                    or pick a new hint.

After the side-effect lands the writer calls
:func:`finalize_receipt` to update the row to ``succeeded`` with
final evidence. On failure the writer calls
:func:`release_reservation` so a retry can re-acquire the hint.

Pending reservations remain in-flight for
:data:`STALE_PENDING_THRESHOLD_SECONDS`. After that threshold a replay
returns ``reconciliation_required``; elapsed time alone never permits
another external effect.

SQLite "database is locked" handling
------------------------------------

The connection sets ``busy_timeout=30000`` so SQLite blocks-and-retries
for up to 30 seconds before raising
:class:`sqlite3.OperationalError`. We never catch and silently swallow
that error class — it propagates to the effector, which surfaces a
structured ``error_kind="receipt_store_locked"`` evidence record
rather than firing a duplicate side-effect.

Schema (per-universe, file: ``${universe_dir}/.external_write_receipts.db``):

.. code-block:: sql

    CREATE TABLE IF NOT EXISTS external_write_receipts (
        idempotency_hint TEXT NOT NULL,
        sink             TEXT NOT NULL,
        evidence_json    TEXT NOT NULL DEFAULT '{}',
        run_id           TEXT NOT NULL,
        created_at       REAL NOT NULL,
        status           TEXT NOT NULL DEFAULT 'succeeded',
        PRIMARY KEY (idempotency_hint, sink)
    );

Migration safety: the ``status`` column was added in round-2. Existing
rows are upgraded with the default ``'succeeded'`` value during
:func:`initialize_receipts_db` so round-1 receipts continue to behave
as terminal-success rows.
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from tinyassets.sqlite_connection import ClosingConnection

_DB_FILENAME = ".external_write_receipts.db"

# Receipt lifecycle states.
STATUS_PENDING = "pending"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"
STATUS_HELD = "held"

# After this many seconds a ``pending`` reservation requires destination
# reconciliation. The row is never reclaimed by elapsed time alone.
STALE_PENDING_THRESHOLD_SECONDS = 600.0


def receipts_db_path(universe_dir: str | Path) -> Path:
    """Resolve the per-universe receipts DB path."""
    return Path(universe_dir) / _DB_FILENAME


def _connect(universe_dir: str | Path) -> sqlite3.Connection:
    """Open the receipts DB with WAL + 30s busy timeout (run-path-safe).

    Note: ``isolation_level=None`` deliberately NOT set. We use Python's
    implicit-transaction wrapper around every ``INSERT … ON CONFLICT``
    so the conflict-check + insert are atomic without needing explicit
    BEGIN IMMEDIATE/COMMIT. SQLite's row-level lock on the unique key
    serializes concurrent writers.
    """
    path = receipts_db_path(universe_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=30.0, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# Base CREATE — never references columns added by later migrations so
# it stays compatible with round-1 DBs that pre-date the ``status``
# column. Migration steps run AFTER this base, in initialize_receipts_db.
_BASE_SCHEMA = """
CREATE TABLE IF NOT EXISTS external_write_receipts (
    idempotency_hint TEXT NOT NULL,
    sink             TEXT NOT NULL,
    evidence_json    TEXT NOT NULL DEFAULT '{}',
    run_id           TEXT NOT NULL,
    created_at       REAL NOT NULL,
    PRIMARY KEY (idempotency_hint, sink)
);

CREATE INDEX IF NOT EXISTS idx_receipts_sink_created
    ON external_write_receipts(sink, created_at DESC);

CREATE TABLE IF NOT EXISTS external_write_identity_aliases (
    caller_hint       TEXT NOT NULL,
    sink              TEXT NOT NULL,
    system_effect_key TEXT NOT NULL,
    recorded_at       REAL NOT NULL,
    PRIMARY KEY (caller_hint, sink),
    UNIQUE (system_effect_key, sink)
);
"""


def initialize_receipts_db(universe_dir: str | Path) -> Path:
    """Ensure the receipts DB exists and is migrated. Returns the DB path.

    Round-2 migration: probe for the ``status`` column and add it with
    a default of ``'succeeded'`` so round-1 receipts (which represent
    terminal-success rows) keep their semantics. The status-indexed
    secondary key is created only AFTER the column exists.
    """
    path = receipts_db_path(universe_dir)
    with _connect(universe_dir) as conn:
        conn.executescript(_BASE_SCHEMA)
        existing_cols = {
            row["name"]
            for row in conn.execute(
                "PRAGMA table_info(external_write_receipts)"
            )
        }
        if "status" not in existing_cols:
            # SQLite lacks ADD COLUMN IF NOT EXISTS.
            conn.execute(
                "ALTER TABLE external_write_receipts "
                "ADD COLUMN status TEXT NOT NULL DEFAULT 'succeeded'"
            )
        # Status-indexed key — safe to (re-)create after the column
        # exists; CREATE INDEX IF NOT EXISTS is a no-op on subsequent
        # initializations.
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_receipts_status "
            "ON external_write_receipts(status, created_at)"
        )
        conn.commit()
    return path


def lookup_receipt(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
) -> dict[str, Any] | None:
    """Return the receipt row for ``(idempotency_hint, sink)`` or None.

    Empty ``idempotency_hint`` returns ``None`` — the effector treats
    "no hint" as "always miss" so the caller can opt out of dedup by
    omitting the field.

    The returned dict now includes ``status`` so callers can distinguish
    succeeded (dedup-hit), pending (concurrent in-flight), and failed
    (retry-eligible) receipts.
    """
    if not idempotency_hint:
        return None
    initialize_receipts_db(universe_dir)
    with _connect(universe_dir) as conn:
        row = conn.execute(
            """
            SELECT idempotency_hint, sink, evidence_json, run_id,
                   created_at, status
              FROM external_write_receipts
             WHERE idempotency_hint = ? AND sink = ?
            """,
            (idempotency_hint, sink),
        ).fetchone()
    if row is None:
        return None
    try:
        evidence = json.loads(row["evidence_json"])
    except (TypeError, ValueError):
        evidence = {}
    return {
        "idempotency_hint": row["idempotency_hint"],
        "sink": row["sink"],
        "evidence": evidence,
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "status": row["status"],
    }


def record_identity_alias(
    universe_dir: str | Path,
    *,
    caller_hint: str,
    sink: str,
    system_effect_key: str,
    recorded_at: float | None = None,
) -> bool:
    """Dual-write one strict caller-hint -> system-key parity mapping."""
    if not caller_hint or not sink or not system_effect_key:
        raise ValueError("identity alias fields must be non-empty")
    initialize_receipts_db(universe_dir)
    timestamp = time.time() if recorded_at is None else recorded_at
    with _connect(universe_dir) as conn:
        try:
            cursor = conn.execute(
                """
                INSERT INTO external_write_identity_aliases (
                    caller_hint, sink, system_effect_key, recorded_at
                ) VALUES (?, ?, ?, ?)
                ON CONFLICT(caller_hint, sink) DO NOTHING
                """,
                (caller_hint, sink, system_effect_key, timestamp),
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError("system effect identity parity conflict") from exc
        row = conn.execute(
            """
            SELECT system_effect_key
              FROM external_write_identity_aliases
             WHERE caller_hint = ? AND sink = ?
            """,
            (caller_hint, sink),
        ).fetchone()
        if row is None or row["system_effect_key"] != system_effect_key:
            raise ValueError("caller hint maps to a different system effect key")
        conn.commit()
        return cursor.rowcount > 0


def lookup_identity_alias(
    universe_dir: str | Path,
    *,
    caller_hint: str,
    sink: str,
) -> str | None:
    initialize_receipts_db(universe_dir)
    with _connect(universe_dir) as conn:
        row = conn.execute(
            """
            SELECT system_effect_key
              FROM external_write_identity_aliases
             WHERE caller_hint = ? AND sink = ?
            """,
            (caller_hint, sink),
        ).fetchone()
    return row["system_effect_key"] if row is not None else None


def identity_receipts_have_parity(
    universe_dir: str | Path,
    *,
    caller_hint: str,
    sink: str,
    system_effect_key: str,
) -> bool:
    """Return true only after both identity journals reached equal terminal state."""
    caller = lookup_receipt(
        universe_dir,
        idempotency_hint=caller_hint,
        sink=sink,
    )
    system = lookup_receipt(
        universe_dir,
        idempotency_hint=system_effect_key,
        sink=sink,
    )
    if caller is None or system is None:
        return False
    if caller["status"] not in (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_HELD):
        return False
    return (
        caller["status"] == system["status"]
        and caller["run_id"] == system["run_id"]
        and caller["evidence"] == system["evidence"]
    )


def identity_sink_has_parity(
    universe_dir: str | Path,
    *,
    sink: str,
) -> bool:
    """Return true after at least one dual identity for a sink proves parity."""
    initialize_receipts_db(universe_dir)
    with _connect(universe_dir) as connection:
        aliases = connection.execute(
            """
            SELECT caller_hint, system_effect_key
              FROM external_write_identity_aliases
             WHERE sink = ?
            """,
            (sink,),
        ).fetchall()
    return bool(aliases) and all(
        identity_receipts_have_parity(
            universe_dir,
            caller_hint=row["caller_hint"],
            sink=sink,
            system_effect_key=row["system_effect_key"],
        )
        for row in aliases
    )


def _identity_keys(
    connection: sqlite3.Connection,
    *,
    idempotency_hint: str,
    sink: str,
) -> tuple[str, ...]:
    row = connection.execute(
        """
        SELECT caller_hint, system_effect_key
          FROM external_write_identity_aliases
         WHERE sink = ?
           AND (caller_hint = ? OR system_effect_key = ?)
        """,
        (sink, idempotency_hint, idempotency_hint),
    ).fetchone()
    if row is None:
        return (idempotency_hint,)
    peer = (
        row["system_effect_key"]
        if idempotency_hint == row["caller_hint"]
        else row["caller_hint"]
    )
    return (idempotency_hint, peer)


def try_reserve_receipt(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    run_id: str,
    now: float | None = None,
    stale_after_seconds: float = STALE_PENDING_THRESHOLD_SECONDS,
    max_failed_retries: int | None = None,
    reservation_evidence: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Atomically reserve a receipt slot for ``(idempotency_hint, sink)``.

    Returns one of:

    * ``{"status": "reserved", "row": <receipt>}`` — caller acquired
      a fresh pending row; proceed with the side-effect and call
      :func:`finalize_receipt` or :func:`release_reservation` next.

    * ``{"status": "duplicate", "row": <existing receipt>}`` — a
      terminal ``succeeded`` row already exists; caller should return
      the dedup-hit evidence WITHOUT invoking the side-effect.

    * ``{"status": "in_flight", "row": <existing pending receipt>}``
      — another writer holds a non-stale ``pending`` reservation;
      caller should dry-run with a ``concurrent_in_flight`` reason
      rather than fire a duplicate side-effect.

    * ``{"status": "reconciliation_required", "row": <pending receipt>}``
      — the pending row aged past the in-flight window; reconcile with
      the destination or hold for explicit remediation.

    * ``{"status": "reserved_after_failed", "row": <existing receipt>}``
      — the prior attempt under this hint failed and was released to
      ``failed``. Round-2 contract: a fresh retry under the same hint
      MAY re-reserve. We delete the failed row and acquire a new
      pending slot. Returned status is ``"reserved"`` (or
      ``"reserved_after_failed"``) so the caller flow stays uniform.

    Empty ``idempotency_hint`` returns ``{"status": "no_hint"}`` —
    callers that opt out of dedup must not pretend they reserved.

    Raises :class:`sqlite3.OperationalError` on lock timeout; the caller
    must surface this loudly, NOT swallow it as a miss.
    """
    if max_failed_retries is not None and max_failed_retries < 0:
        raise ValueError("max_failed_retries must be non-negative")
    reservation_payload = json.dumps(
        dict(reservation_evidence or {}),
        sort_keys=True,
        separators=(",", ":"),
    )
    if not idempotency_hint:
        return {"status": "no_hint"}
    initialize_receipts_db(universe_dir)
    ts = now if now is not None else time.time()
    with _connect(universe_dir) as conn:
        identity_keys = _identity_keys(
            conn,
            idempotency_hint=idempotency_hint,
            sink=sink,
        )
        # Atomic INSERT … ON CONFLICT DO NOTHING. If we win the race,
        # ``changes()`` returns 1. If we lose, 0.
        cursor = conn.execute(
            """
            INSERT INTO external_write_receipts (
                idempotency_hint, sink, evidence_json, run_id,
                created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_hint, sink) DO NOTHING
            """,
            (
                idempotency_hint,
                sink,
                reservation_payload,
                run_id,
                ts,
                STATUS_PENDING,
            ),
        )
        rowcount = cursor.rowcount
        if rowcount > 0:
            for peer_key in identity_keys[1:]:
                peer_cursor = conn.execute(
                    """
                    INSERT INTO external_write_receipts (
                        idempotency_hint, sink, evidence_json, run_id,
                        created_at, status
                    ) VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(idempotency_hint, sink) DO NOTHING
                    """,
                    (
                        peer_key,
                        sink,
                        reservation_payload,
                        run_id,
                        ts,
                        STATUS_PENDING,
                    ),
                )
                if peer_cursor.rowcount != 1:
                    conn.rollback()
                    raise sqlite3.IntegrityError(
                        "dual identity reservation parity conflict"
                    )
        conn.commit()
        if rowcount > 0:
            # Won the race — fresh reservation.
            row = conn.execute(
                "SELECT idempotency_hint, sink, evidence_json, run_id, "
                "       created_at, status "
                "FROM external_write_receipts "
                "WHERE idempotency_hint = ? AND sink = ?",
                (idempotency_hint, sink),
            ).fetchone()
            return {"status": "reserved", "row": _row_to_dict(row)}

        # Lost the race — read the existing row to decide what kind of
        # collision this is. The decision is made in a second
        # transaction so SQLite's row-lock semantics on the conflict
        # check above don't leak into the lookup.
        row = conn.execute(
            "SELECT idempotency_hint, sink, evidence_json, run_id, "
            "       created_at, status "
            "FROM external_write_receipts "
            "WHERE idempotency_hint = ? AND sink = ?",
            (idempotency_hint, sink),
        ).fetchone()
        if row is None:
            # Extremely unlikely: someone deleted the row between our
            # INSERT and the SELECT. Retry once.
            conn.execute(
                """
                INSERT INTO external_write_receipts (
                    idempotency_hint, sink, evidence_json, run_id,
                    created_at, status
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(idempotency_hint, sink) DO NOTHING
                """,
                (
                    idempotency_hint,
                    sink,
                    reservation_payload,
                    run_id,
                    ts,
                    STATUS_PENDING,
                ),
            )
            conn.commit()
            row = conn.execute(
                "SELECT idempotency_hint, sink, evidence_json, run_id, "
                "       created_at, status "
                "FROM external_write_receipts "
                "WHERE idempotency_hint = ? AND sink = ?",
                (idempotency_hint, sink),
            ).fetchone()
            if row is None:
                # Give up; surface as lock-class error so the caller
                # treats it as fail-loud, not miss.
                raise sqlite3.OperationalError(
                    "receipt row vanished mid-reservation; refusing to "
                    "treat as miss"
                )
            return {"status": "reserved", "row": _row_to_dict(row)}

        existing = _row_to_dict(row)
        status = existing.get("status")
        if status == STATUS_SUCCEEDED:
            return {"status": "duplicate", "row": existing}
        if status == STATUS_HELD:
            return {"status": "held", "row": existing}
        if status == STATUS_PENDING:
            age = ts - float(existing.get("created_at") or 0.0)
            if age < stale_after_seconds:
                return {"status": "in_flight", "row": existing}
            return {
                "status": "reconciliation_required",
                "row": existing,
            }
        if status == STATUS_FAILED:
            failed_attempts_raw = existing["evidence"].get("failed_attempts", 1)
            failed_attempts = (
                failed_attempts_raw
                if isinstance(failed_attempts_raw, int)
                and not isinstance(failed_attempts_raw, bool)
                and failed_attempts_raw > 0
                else 1
            )
            if (
                max_failed_retries is not None
                and failed_attempts > max_failed_retries
            ):
                return {"status": "retry_exhausted", "row": existing}
            pending_payload = dict(reservation_evidence or {})
            if max_failed_retries is not None:
                pending_payload["failed_attempts"] = failed_attempts
            pending_evidence = json.dumps(
                pending_payload,
                sort_keys=True,
                separators=(",", ":"),
            )
            # Failed-prior policy: a retry under the same hint replaces
            # the failed row with a fresh reservation. UPDATE WHERE
            # status='failed' so we don't clobber a concurrent retry
            # that already moved the row to pending.
            cur = conn.execute(
                """
                UPDATE external_write_receipts
                   SET run_id = ?, created_at = ?, status = ?,
                       evidence_json = ?
                 WHERE idempotency_hint = ? AND sink = ?
                   AND status = ?
                """,
                (
                    run_id, ts, STATUS_PENDING, pending_evidence,
                    idempotency_hint, sink, STATUS_FAILED,
                ),
            )
            if cur.rowcount == 1:
                for peer_key in identity_keys[1:]:
                    peer_cursor = conn.execute(
                        """
                        UPDATE external_write_receipts
                           SET run_id = ?, created_at = ?, status = ?,
                               evidence_json = ?
                         WHERE idempotency_hint = ? AND sink = ?
                           AND status = ?
                        """,
                        (
                            run_id,
                            ts,
                            STATUS_PENDING,
                            pending_evidence,
                            peer_key,
                            sink,
                            STATUS_FAILED,
                        ),
                    )
                    if peer_cursor.rowcount != 1:
                        conn.rollback()
                        raise sqlite3.IntegrityError(
                            "dual identity retry parity conflict"
                        )
            conn.commit()
            if cur.rowcount == 0:
                row = conn.execute(
                    "SELECT idempotency_hint, sink, evidence_json, "
                    "       run_id, created_at, status "
                    "FROM external_write_receipts "
                    "WHERE idempotency_hint = ? AND sink = ?",
                    (idempotency_hint, sink),
                ).fetchone()
                if row is None:
                    raise sqlite3.OperationalError(
                        "failed-row replace race resolved by deletion"
                    )
                replaced = _row_to_dict(row)
                # Re-classify by current status.
                if replaced.get("status") == STATUS_SUCCEEDED:
                    return {"status": "duplicate", "row": replaced}
                if replaced.get("status") == STATUS_PENDING:
                    return {"status": "in_flight", "row": replaced}
                return {"status": "in_flight", "row": replaced}
            row = conn.execute(
                "SELECT idempotency_hint, sink, evidence_json, run_id, "
                "       created_at, status "
                "FROM external_write_receipts "
                "WHERE idempotency_hint = ? AND sink = ?",
                (idempotency_hint, sink),
            ).fetchone()
            return {
                "status": "reserved_after_failed",
                "row": _row_to_dict(row),
            }
        # Unknown status — treat conservatively as in_flight so the
        # caller dry-runs rather than firing a duplicate side-effect.
        return {"status": "in_flight", "row": existing}


def finalize_receipt(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    evidence: dict[str, Any],
    run_id: str,
    status: str = STATUS_SUCCEEDED,
    now: float | None = None,
) -> bool:
    """Mark a reservation terminal with final evidence.

    Returns True when the row was updated (the caller held the
    reservation). Returns False when no row matched the caller's
    ``run_id`` — that means another writer raced past us and the
    caller should not overwrite their evidence. The caller's invocation
    already succeeded; the worst case is a slightly stale evidence
    record under the canonical key.

    ``status`` defaults to :data:`STATUS_SUCCEEDED`. Pass
    :data:`STATUS_FAILED` from a release path to mark the row as
    "tried and failed" without deleting it.

    Empty ``idempotency_hint`` is a silent no-op (matches
    :func:`record_receipt`'s semantics).

    Raises :class:`sqlite3.OperationalError` on lock timeout.
    """
    if not idempotency_hint:
        return False
    initialize_receipts_db(universe_dir)
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    ts = now if now is not None else time.time()
    with _connect(universe_dir) as conn:
        identity_keys = _identity_keys(
            conn,
            idempotency_hint=idempotency_hint,
            sink=sink,
        )
        updated: list[int] = []
        for identity_key in identity_keys:
            cursor = conn.execute(
                """
                UPDATE external_write_receipts
                   SET evidence_json = ?,
                       run_id = ?,
                       created_at = ?,
                       status = ?
                 WHERE idempotency_hint = ? AND sink = ?
                   AND run_id = ?
                """,
                (payload, run_id, ts, status, identity_key, sink, run_id),
            )
            updated.append(cursor.rowcount)
        if any(rowcount != 1 for rowcount in updated):
            conn.rollback()
            return False
        conn.commit()
        return True


def finalize_reconciliation(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    evidence: dict[str, Any],
    run_id: str,
    status: str,
    now: float | None = None,
) -> bool:
    """Persist reconciliation over a prior pending intent without reclaiming it."""
    if not idempotency_hint:
        return False
    if status not in (STATUS_SUCCEEDED, STATUS_FAILED, STATUS_HELD):
        raise ValueError("reconciliation status must be terminal or held")
    initialize_receipts_db(universe_dir)
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    timestamp = time.time() if now is None else now
    with _connect(universe_dir) as conn:
        identity_keys = _identity_keys(
            conn,
            idempotency_hint=idempotency_hint,
            sink=sink,
        )
        updated: list[int] = []
        for identity_key in identity_keys:
            cursor = conn.execute(
                """
                UPDATE external_write_receipts
                   SET evidence_json = ?, run_id = ?, created_at = ?, status = ?
                 WHERE idempotency_hint = ? AND sink = ? AND status = ?
                """,
                (
                    payload,
                    run_id,
                    timestamp,
                    status,
                    identity_key,
                    sink,
                    STATUS_PENDING,
                ),
            )
            updated.append(cursor.rowcount)
        if any(rowcount != 1 for rowcount in updated):
            conn.rollback()
            return False
        conn.commit()
        return True


def try_record_held_receipt(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    evidence: dict[str, Any],
    run_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Insert a held intent without overwriting any existing journal state."""
    if not idempotency_hint:
        raise ValueError("idempotency_hint must be non-empty")
    initialize_receipts_db(universe_dir)
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    timestamp = time.time() if now is None else now
    with _connect(universe_dir) as conn:
        cursor = conn.execute(
            """
            INSERT INTO external_write_receipts (
                idempotency_hint, sink, evidence_json, run_id,
                created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_hint, sink) DO NOTHING
            """,
            (
                idempotency_hint,
                sink,
                payload,
                run_id,
                timestamp,
                STATUS_HELD,
            ),
        )
        row = conn.execute(
            """
            SELECT idempotency_hint, sink, evidence_json, run_id,
                   created_at, status
              FROM external_write_receipts
             WHERE idempotency_hint = ? AND sink = ?
            """,
            (idempotency_hint, sink),
        ).fetchone()
        conn.commit()
    if row is None:
        raise sqlite3.OperationalError("held receipt insert produced no row")
    return {
        "status": "held_created" if cursor.rowcount > 0 else "existing",
        "row": _row_to_dict(row),
    }


def confirm_held_receipt(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    confirmation: dict[str, Any],
    expected_grant_id: str,
) -> dict[str, Any]:
    """Atomically attach owner confirmation to the matching held intent."""
    initialize_receipts_db(universe_dir)
    with _connect(universe_dir) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT idempotency_hint, sink, evidence_json, run_id,
                   created_at, status
              FROM external_write_receipts
             WHERE idempotency_hint = ? AND sink = ?
            """,
            (idempotency_hint, sink),
        ).fetchone()
        current = _row_to_dict(row)
        if not current or current["status"] != STATUS_HELD:
            conn.rollback()
            raise LookupError("held effect does not exist")
        evidence = dict(current["evidence"])
        if evidence.get("grant_id") != expected_grant_id:
            conn.rollback()
            raise PermissionError("held effect belongs to a different grant")
        evidence["confirmation"] = confirmation
        payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
        cursor = conn.execute(
            """
            UPDATE external_write_receipts
               SET evidence_json = ?
             WHERE idempotency_hint = ? AND sink = ? AND status = ?
            """,
            (payload, idempotency_hint, sink, STATUS_HELD),
        )
        conn.commit()
    if cursor.rowcount != 1:
        raise RuntimeError("held effect confirmation lost its journal race")
    return evidence


def try_activate_confirmed_hold(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    run_id: str,
    expected_grant_id: str,
    now: float | None = None,
) -> dict[str, Any]:
    """Atomically acquire a confirmed hold for one execution attempt."""
    initialize_receipts_db(universe_dir)
    timestamp = time.time() if now is None else now
    with _connect(universe_dir) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT idempotency_hint, sink, evidence_json, run_id,
                   created_at, status
              FROM external_write_receipts
             WHERE idempotency_hint = ? AND sink = ?
            """,
            (idempotency_hint, sink),
        ).fetchone()
        current = _row_to_dict(row)
        if not current:
            conn.rollback()
            return {"status": "missing"}
        if current["status"] == STATUS_SUCCEEDED:
            conn.rollback()
            return {"status": "duplicate", "row": current}
        if current["status"] == STATUS_PENDING:
            conn.rollback()
            return {"status": "in_flight", "row": current}
        if current["status"] not in (STATUS_HELD, STATUS_FAILED):
            conn.rollback()
            return {"status": "not_confirmable", "row": current}
        evidence = current["evidence"]
        if evidence.get("grant_id") != expected_grant_id:
            conn.rollback()
            raise PermissionError("held effect belongs to a different grant")
        if not evidence.get("confirmation"):
            conn.rollback()
            return {"status": "held", "row": current}
        cursor = conn.execute(
            """
            UPDATE external_write_receipts
               SET run_id = ?, created_at = ?, status = ?
             WHERE idempotency_hint = ? AND sink = ?
               AND status IN (?, ?)
            """,
            (
                run_id,
                timestamp,
                STATUS_PENDING,
                idempotency_hint,
                sink,
                STATUS_HELD,
                STATUS_FAILED,
            ),
        )
        conn.commit()
    if cursor.rowcount != 1:
        return {"status": "in_flight", "row": current}
    return {"status": "reserved", "row": current}


def release_reservation(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    run_id: str,
    mark_failed: bool = True,
    now: float | None = None,
) -> bool:
    """Release a pending reservation after a side-effect failure.

    With ``mark_failed=True`` (the default) the row is set to
    :data:`STATUS_FAILED` so a future retry under the same hint can
    re-reserve via :func:`try_reserve_receipt`. With
    ``mark_failed=False`` the row is deleted entirely.

    Only releases if the row is still ``pending`` AND owned by the
    caller's ``run_id`` — concurrent reclaim is safe.

    Returns True when the row was released.
    """
    if not idempotency_hint:
        return False
    initialize_receipts_db(universe_dir)
    ts = now if now is not None else time.time()
    with _connect(universe_dir) as conn:
        identity_keys = _identity_keys(
            conn,
            idempotency_hint=idempotency_hint,
            sink=sink,
        )
        updated: list[int] = []
        for identity_key in identity_keys:
            if mark_failed:
                cursor = conn.execute(
                    """
                    UPDATE external_write_receipts
                       SET status = ?, created_at = ?
                     WHERE idempotency_hint = ? AND sink = ?
                       AND status = ? AND run_id = ?
                    """,
                    (
                        STATUS_FAILED,
                        ts,
                        identity_key,
                        sink,
                        STATUS_PENDING,
                        run_id,
                    ),
                )
            else:
                cursor = conn.execute(
                    """
                    DELETE FROM external_write_receipts
                     WHERE idempotency_hint = ? AND sink = ?
                       AND status = ? AND run_id = ?
                    """,
                    (
                        identity_key,
                        sink,
                        STATUS_PENDING,
                        run_id,
                    ),
                )
            updated.append(cursor.rowcount)
        if any(rowcount != 1 for rowcount in updated):
            conn.rollback()
            return False
        conn.commit()
        return True


def record_receipt(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
    evidence: dict[str, Any],
    run_id: str,
    created_at: float | None = None,
    status: str = STATUS_SUCCEEDED,
) -> None:
    """Idempotently upsert a terminal receipt (legacy/test path).

    The round-1 callers used this as a single-step "record success"
    helper. Round-2 effector code prefers
    :func:`try_reserve_receipt` + :func:`finalize_receipt` because
    that pair is race-safe. This function remains for callers that
    KNOW they hold the only writer (tests, replays, host scripts) and
    just want to upsert a terminal row.

    Last-write-wins on the same key — an existing row is replaced
    regardless of its prior status. Use the reservation pair instead
    when concurrent writers may exist.

    Empty ``idempotency_hint`` is a silent no-op.
    """
    if not idempotency_hint:
        return
    initialize_receipts_db(universe_dir)
    payload = json.dumps(evidence, sort_keys=True, separators=(",", ":"))
    ts = created_at if created_at is not None else time.time()
    with _connect(universe_dir) as conn:
        conn.execute(
            """
            INSERT INTO external_write_receipts (
                idempotency_hint, sink, evidence_json, run_id,
                created_at, status
            ) VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(idempotency_hint, sink) DO UPDATE SET
                evidence_json = excluded.evidence_json,
                run_id        = excluded.run_id,
                created_at    = excluded.created_at,
                status        = excluded.status
            """,
            (idempotency_hint, sink, payload, run_id, ts, status),
        )
        conn.commit()


def delete_receipt(
    universe_dir: str | Path,
    *,
    idempotency_hint: str,
    sink: str,
) -> bool:
    """Remove the receipt for ``(idempotency_hint, sink)``. Returns True on hit.

    Used by tests and host scripts to clear a stale row. Production
    callers should prefer :func:`release_reservation` for pending
    rows so they don't accidentally clobber a concurrent reservation.
    """
    if not idempotency_hint:
        return False
    initialize_receipts_db(universe_dir)
    with _connect(universe_dir) as conn:
        cur = conn.execute(
            "DELETE FROM external_write_receipts "
            "WHERE idempotency_hint = ? AND sink = ?",
            (idempotency_hint, sink),
        )
        conn.commit()
        return cur.rowcount > 0


def list_receipts(
    universe_dir: str | Path,
    *,
    sink: str | None = None,
    status: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """Return receipts, most-recent first. Optional sink + status filter.

    Diagnostic surface for the chatbot and tests. Bounded by ``limit``.
    """
    initialize_receipts_db(universe_dir)
    limit = max(1, min(int(limit), 1000))
    clauses: list[str] = []
    params: list[Any] = []
    if sink:
        clauses.append("sink = ?")
        params.append(sink)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
    with _connect(universe_dir) as conn:
        rows = conn.execute(
            f"""
            SELECT idempotency_hint, sink, evidence_json, run_id,
                   created_at, status
              FROM external_write_receipts
              {where}
          ORDER BY created_at DESC
             LIMIT ?
            """,
            (*params, limit),
        ).fetchall()
    return [_row_to_dict(row) for row in rows]


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any]:
    if row is None:
        return {}
    try:
        evidence = json.loads(row["evidence_json"])
    except (TypeError, ValueError):
        evidence = {}
    return {
        "idempotency_hint": row["idempotency_hint"],
        "sink": row["sink"],
        "evidence": evidence,
        "run_id": row["run_id"],
        "created_at": row["created_at"],
        "status": row["status"] if "status" in row.keys() else STATUS_SUCCEEDED,
    }


__all__ = [
    "STATUS_PENDING",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED",
    "STATUS_HELD",
    "STALE_PENDING_THRESHOLD_SECONDS",
    "receipts_db_path",
    "initialize_receipts_db",
    "lookup_receipt",
    "record_identity_alias",
    "lookup_identity_alias",
    "identity_receipts_have_parity",
    "identity_sink_has_parity",
    "try_reserve_receipt",
    "finalize_receipt",
    "finalize_reconciliation",
    "try_record_held_receipt",
    "confirm_held_receipt",
    "try_activate_confirmed_hold",
    "release_reservation",
    "record_receipt",
    "delete_receipt",
    "list_receipts",
]
