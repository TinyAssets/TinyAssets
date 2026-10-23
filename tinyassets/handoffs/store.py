"""Handoff lifecycle persistence, and the additive extension of the existing
``outcome_event`` registry.

Task 5.1 of ``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets``
(capability ``real-world-handoffs-and-outcomes``).

Where this lives, and why
-------------------------

Every table here is created in ``<data_dir>/.runs.db`` — the same database that
already holds ``outcome_event`` (``tinyassets/api/goals.py`` resolves the
registry there), ``gate_events``, ``run_lineage``, and the attribution chain. It
is one database on purpose: an outcome claim, its evidence transitions, and the
handoff that produced it are written in a single transaction, and the base
``outcome_event`` row is created by the registry's own DDL owner
(:func:`tinyassets.outcomes.schema.migrate_outcome_schema`) rather than by a
copy of it here.

**This is an extension, not a second registry.** ``outcome_event`` remains the
sole generic owner of an outcome claim. ``outcome_evidence`` is a 1:1 side table
carrying the provenance and evidence-level columns the base table does not have,
and ``outcome_evidence_transition`` is its append-only journal. Their DDL and
legacy backfill live in ``tinyassets/outcomes/schema.py``; this store only
creates the handoff-owned tables and writes the registry transactionally.

Invariants this module owns
---------------------------

- **Owner scoping is in the SQL.** Every handoff read carries ``owner_id`` in its
  WHERE clause and a miss raises the single indistinguishable
  :class:`~tinyassets.handoffs.models.HandoffAccessError`, so a probe cannot
  tell "someone else's" from "does not exist".
- **One handoff per effect identity.** ``UNIQUE (effect_key, sink)`` mirrors the
  receipt store's primary key, so the lifecycle row and the receipt row cannot
  disagree about how many effects exist.
- **State advance is compare-and-swap, and the transition is written in the same
  immediate transaction.** A committed advance can never lose its transition, and
  ``seq`` stays contiguous.
- **Confirmations are single-use rows, not signed blobs**, so consumption is one
  atomic ``UPDATE ... WHERE consumed_at IS NULL`` and a replay cannot be forged.
- **Evidence transitions are append-only.** There is no UPDATE path that erases a
  prior level; the extension row's ``evidence_level`` is a cached head whose
  history is the transition table.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from tinyassets.handoffs.models import (
    HandoffAccessError,
    HandoffConflictError,
    HandoffRecord,
    HandoffTransition,
    HandoffValidationError,
    assert_evidence_transition,
    assert_transition,
    canonical_json,
    event_type_for,
    normalize_external_ref,
)
from tinyassets.ids import new_ulid

_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoff (
    handoff_id        TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    effect_key        TEXT NOT NULL,
    sink              TEXT NOT NULL,
    adapter_action    TEXT NOT NULL,
    destination       TEXT NOT NULL,
    branch_def_id     TEXT NOT NULL DEFAULT '',
    branch_version_id TEXT NOT NULL,
    content_hash      TEXT NOT NULL,
    run_id            TEXT NOT NULL,
    output_field      TEXT NOT NULL,
    output_sha256     TEXT NOT NULL,
    effect_class      TEXT NOT NULL
                        CHECK (effect_class IN ('reversible', 'irreversible')),
    outcome_kind      TEXT NOT NULL,
    credential_class  TEXT NOT NULL DEFAULT '',
    state             TEXT NOT NULL
                        CHECK (state IN (
                            'reserved', 'submitted', 'accepted', 'verified',
                            'rejected', 'uncertain', 'orphaned', 'cancelled'
                        )),
    external_id       TEXT NOT NULL DEFAULT '',
    declaration_json  TEXT NOT NULL DEFAULT '{}',
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    UNIQUE (effect_key, sink),
    -- An accepted/verified handoff must carry the provider's stable external
    -- id. Without one the claim is unverifiable later, which is exactly how a
    -- transport success comes to read as a durable destination acceptance.
    CHECK (state NOT IN ('accepted', 'verified') OR external_id <> '')
);

CREATE INDEX IF NOT EXISTS idx_handoff_owner ON handoff(owner_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_handoff_run ON handoff(run_id);
CREATE INDEX IF NOT EXISTS idx_handoff_state ON handoff(state);

CREATE TABLE IF NOT EXISTS handoff_transition (
    transition_id   TEXT PRIMARY KEY,
    handoff_id      TEXT NOT NULL REFERENCES handoff(handoff_id),
    seq             INTEGER NOT NULL,
    from_state      TEXT NOT NULL DEFAULT '',
    to_state        TEXT NOT NULL,
    evidence_source TEXT NOT NULL,
    evidence_json   TEXT NOT NULL DEFAULT '{}',
    recorded_at     TEXT NOT NULL,
    UNIQUE (handoff_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_handoff_transition_handoff
    ON handoff_transition(handoff_id, seq);

CREATE TABLE IF NOT EXISTS handoff_confirmation (
    token       TEXT PRIMARY KEY,
    owner_id    TEXT NOT NULL,
    effect_key  TEXT NOT NULL,
    sink        TEXT NOT NULL,
    fingerprint TEXT NOT NULL,
    created_at  REAL NOT NULL,
    expires_at  REAL NOT NULL,
    consumed_at REAL
);

CREATE INDEX IF NOT EXISTS idx_handoff_confirmation_effect
    ON handoff_confirmation(effect_key, sink, owner_id);

"""


def _now_iso(now: float | None = None) -> str:
    moment = (
        datetime.now(timezone.utc)
        if now is None
        else datetime.fromtimestamp(now, tz=timezone.utc)
    )
    return moment.isoformat()


class HandoffStore:
    """The handoff bounded-context store, colocated with the outcome registry."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        if base_path is None:
            from tinyassets.storage import data_dir

            base_path = data_dir()
        self._base = Path(base_path)

    # ── plumbing ──────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        from tinyassets.runs import runs_db_path

        return Path(runs_db_path(self._base))

    def _connect(self) -> sqlite3.Connection:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    def initialize(self) -> Path:
        """Create the handoff tables and, via its own owner, the base registry."""
        from tinyassets.outcomes.schema import migrate_outcome_schema

        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = self._connect()
        try:
            migrate_outcome_schema(conn)
            conn.executescript(_SCHEMA)
        finally:
            conn.close()
        return self.path

    @contextlib.contextmanager
    def _open(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        conn = self._connect()
        try:
            if write:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    yield conn
                except BaseException:
                    conn.execute("ROLLBACK")
                    raise
                conn.execute("COMMIT")
            else:
                yield conn
        finally:
            conn.close()

    # ── handoffs ──────────────────────────────────────────────────────────

    def create_handoff(
        self,
        record: HandoffRecord,
        *,
        evidence_source: str,
        evidence: dict[str, Any] | None = None,
    ) -> HandoffRecord:
        """Insert the lifecycle row plus its opening transition, atomically.

        A duplicate ``(effect_key, sink)`` raises
        :class:`~tinyassets.handoffs.models.HandoffConflictError` rather than
        overwriting: the existing row is the authoritative one for that identity.
        """
        with self._open(write=True) as conn:
            try:
                conn.execute(
                    """
                    INSERT INTO handoff (
                        handoff_id, owner_id, effect_key, sink, adapter_action,
                        destination, branch_def_id, branch_version_id,
                        content_hash, run_id, output_field, output_sha256,
                        effect_class, outcome_kind, credential_class, state,
                        external_id, declaration_json, created_at, updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        record.handoff_id, record.owner_id, record.effect_key,
                        record.sink, record.adapter_action, record.destination,
                        record.branch_def_id, record.branch_version_id,
                        record.content_hash, record.run_id, record.output_field,
                        record.output_sha256, record.effect_class,
                        record.outcome_kind, record.credential_class,
                        record.state, record.external_id,
                        canonical_json(record.declaration),
                        record.created_at, record.updated_at,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                raise HandoffConflictError(
                    "a handoff already exists for this effect identity"
                ) from exc
            self._insert_transition(
                conn,
                handoff_id=record.handoff_id,
                seq=1,
                from_state="",
                to_state=record.state,
                evidence_source=evidence_source,
                evidence=evidence or {},
                recorded_at=record.created_at,
            )
        return record

    def get_handoff(self, handoff_id: str, *, actor_id: str) -> HandoffRecord:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM handoff WHERE handoff_id = ? AND owner_id = ?",
                ((handoff_id or "").strip(), (actor_id or "").strip()),
            ).fetchone()
        if row is None:
            raise HandoffAccessError(f"handoff {handoff_id!r} not found")
        return _row_to_record(row)

    def find_by_effect(
        self,
        *,
        effect_key: str,
        sink: str,
        actor_id: str,
    ) -> HandoffRecord | None:
        """Owner-scoped lookup by effect identity. ``None`` when absent.

        Deliberately returns ``None`` rather than raising for an owner miss too:
        the caller is asking "do I already own this identity?", and a foreign
        row is not the caller's to see either way.
        """
        with self._open() as conn:
            row = conn.execute(
                """
                SELECT * FROM handoff
                 WHERE effect_key = ? AND sink = ? AND owner_id = ?
                """,
                (effect_key, sink, (actor_id or "").strip()),
            ).fetchone()
        return _row_to_record(row) if row is not None else None

    def list_handoffs(
        self,
        *,
        actor_id: str,
        run_id: str = "",
        state: str = "",
        limit: int = 50,
    ) -> list[HandoffRecord]:
        clauses = ["owner_id = ?"]
        params: list[Any] = [(actor_id or "").strip()]
        if run_id:
            clauses.append("run_id = ?")
            params.append(run_id)
        if state:
            clauses.append("state = ?")
            params.append(state)
        params.append(max(1, min(int(limit or 50), 200)))
        with self._open() as conn:
            rows = conn.execute(
                f"""
                SELECT * FROM handoff WHERE {' AND '.join(clauses)}
                 ORDER BY created_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [_row_to_record(row) for row in rows]

    def advance_handoff(
        self,
        handoff_id: str,
        *,
        actor_id: str,
        expected_state: str,
        to_state: str,
        evidence_source: str,
        evidence: dict[str, Any] | None = None,
        external_id: str = "",
        now: float | None = None,
    ) -> HandoffRecord:
        """Compare-and-swap the lifecycle state and journal the transition.

        ``expected_state`` is the state the caller read. If a concurrent writer
        moved the row first, the UPDATE matches nothing and this raises
        :class:`~tinyassets.handoffs.models.HandoffConflictError` — the caller
        must re-read rather than assume its observation still holds.
        """
        assert_transition(expected_state, to_state)
        stamp = _now_iso(now)
        owner = (actor_id or "").strip()
        with self._open(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM handoff WHERE handoff_id = ? AND owner_id = ?",
                ((handoff_id or "").strip(), owner),
            ).fetchone()
            if row is None:
                raise HandoffAccessError(f"handoff {handoff_id!r} not found")
            cursor = conn.execute(
                """
                UPDATE handoff
                   SET state = ?, updated_at = ?,
                       external_id = CASE WHEN ? <> '' THEN ? ELSE external_id END
                 WHERE handoff_id = ? AND owner_id = ? AND state = ?
                """,
                (
                    to_state, stamp, external_id, external_id,
                    (handoff_id or "").strip(), owner, expected_state,
                ),
            )
            if cursor.rowcount != 1:
                raise HandoffConflictError(
                    f"handoff {handoff_id!r} is no longer in state "
                    f"{expected_state!r}; re-read before advancing"
                )
            next_seq = int(
                conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM handoff_transition "
                    "WHERE handoff_id = ?",
                    ((handoff_id or "").strip(),),
                ).fetchone()[0]
            )
            self._insert_transition(
                conn,
                handoff_id=(handoff_id or "").strip(),
                seq=next_seq,
                from_state=expected_state,
                to_state=to_state,
                evidence_source=evidence_source,
                evidence=evidence or {},
                recorded_at=stamp,
            )
            refreshed = conn.execute(
                "SELECT * FROM handoff WHERE handoff_id = ? AND owner_id = ?",
                ((handoff_id or "").strip(), owner),
            ).fetchone()
        return _row_to_record(refreshed)

    def list_transitions(
        self,
        handoff_id: str,
        *,
        actor_id: str,
    ) -> list[HandoffTransition]:
        with self._open() as conn:
            owned = conn.execute(
                "SELECT 1 FROM handoff WHERE handoff_id = ? AND owner_id = ?",
                ((handoff_id or "").strip(), (actor_id or "").strip()),
            ).fetchone()
            if owned is None:
                raise HandoffAccessError(f"handoff {handoff_id!r} not found")
            rows = conn.execute(
                "SELECT * FROM handoff_transition WHERE handoff_id = ? ORDER BY seq",
                ((handoff_id or "").strip(),),
            ).fetchall()
        return [
            HandoffTransition(
                transition_id=row["transition_id"],
                handoff_id=row["handoff_id"],
                seq=int(row["seq"]),
                from_state=row["from_state"],
                to_state=row["to_state"],
                evidence_source=row["evidence_source"],
                evidence=_load_json(row["evidence_json"]),
                recorded_at=row["recorded_at"],
            )
            for row in rows
        ]

    @staticmethod
    def _insert_transition(
        conn: sqlite3.Connection,
        *,
        handoff_id: str,
        seq: int,
        from_state: str,
        to_state: str,
        evidence_source: str,
        evidence: dict[str, Any],
        recorded_at: str,
    ) -> None:
        conn.execute(
            """
            INSERT INTO handoff_transition (
                transition_id, handoff_id, seq, from_state, to_state,
                evidence_source, evidence_json, recorded_at
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                new_ulid(), handoff_id, seq, from_state, to_state,
                evidence_source, canonical_json(evidence), recorded_at,
            ),
        )

    # ── confirmations ─────────────────────────────────────────────────────

    def create_confirmation(
        self,
        *,
        owner_id: str,
        effect_key: str,
        sink: str,
        fingerprint: str,
        ttl_seconds: float,
        now: float,
    ) -> dict[str, Any]:
        token = new_ulid()
        expires_at = now + max(1.0, float(ttl_seconds))
        with self._open(write=True) as conn:
            conn.execute(
                """
                INSERT INTO handoff_confirmation (
                    token, owner_id, effect_key, sink, fingerprint,
                    created_at, expires_at, consumed_at
                ) VALUES (?,?,?,?,?,?,?,NULL)
                """,
                (token, owner_id, effect_key, sink, fingerprint, now, expires_at),
            )
        return {
            "token": token,
            "effect_key": effect_key,
            "sink": sink,
            "fingerprint": fingerprint,
            "created_at": now,
            "expires_at": expires_at,
        }

    def consume_confirmation(
        self,
        token: str,
        *,
        owner_id: str,
        effect_key: str,
        sink: str,
        fingerprint: str,
        now: float,
    ) -> dict[str, Any] | None:
        """Atomically spend a matching, unexpired, unconsumed confirmation.

        Every binding is in the WHERE clause — owner, effect identity, and the
        fingerprint that covers effect summary, destination, and source
        version/hash. A confirmation issued for source version N therefore does
        not match an initiation from a later version, and a second execution
        cannot reuse the same token.
        """
        with self._open(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE handoff_confirmation
                   SET consumed_at = ?
                 WHERE token = ? AND owner_id = ? AND effect_key = ?
                   AND sink = ? AND fingerprint = ?
                   AND consumed_at IS NULL AND expires_at > ?
                """,
                (now, (token or "").strip(), owner_id, effect_key, sink, fingerprint, now),
            )
            if cursor.rowcount != 1:
                return None
            row = conn.execute(
                "SELECT * FROM handoff_confirmation WHERE token = ?",
                ((token or "").strip(),),
            ).fetchone()
        return {
            "token": row["token"],
            "effect_key": row["effect_key"],
            "sink": row["sink"],
            "fingerprint": row["fingerprint"],
            "created_at": row["created_at"],
            "consumed_at": row["consumed_at"],
        }

    # ── outcome registry extension ────────────────────────────────────────

    def record_outcome_evidence(
        self,
        *,
        account_id: str,
        outcome_kind: str,
        evidence_source: str,
        evidence_level: str,
        run_id: str = "",
        branch_def_id: str = "",
        branch_version_id: str = "",
        content_hash: str = "",
        output_field: str = "",
        output_sha256: str = "",
        handoff_id: str = "",
        effect_key: str = "",
        sink: str = "",
        external_id: str = "",
        evidence_url: str = "",
        note: str = "",
        payload: dict[str, Any] | None = None,
        now: float | None = None,
    ) -> dict[str, Any]:
        """Create one outcome claim: base ``outcome_event`` row + extension.

        Written in one transaction so a claim can never exist without its
        evidence level, which is the failure mode that would let an unverified
        attestation read as verified.
        """
        from tinyassets.handoffs.models import PERSISTABLE_EVIDENCE_LEVELS

        if evidence_level not in PERSISTABLE_EVIDENCE_LEVELS:
            raise HandoffValidationError(
                f"evidence_level must be one of {sorted(PERSISTABLE_EVIDENCE_LEVELS)}, "
                f"got {evidence_level!r}"
            )
        if not (account_id or "").strip():
            raise HandoffValidationError("account_id is required to record an outcome")
        if not (outcome_kind or "").strip():
            raise HandoffValidationError("outcome_kind is required to record an outcome")

        outcome_id = new_ulid()
        stamp = _now_iso(now)
        artifact_ref = normalize_external_ref(outcome_kind, external_id)
        base_payload = dict(payload or {})
        base_payload.update({
            "outcome_kind": outcome_kind,
            "evidence_level": evidence_level,
            "evidence_source": evidence_source,
            "handoff_id": handoff_id,
            "external_id": external_id,
        })

        with self._open(write=True) as conn:
            if handoff_id:
                existing = conn.execute(
                    """
                    SELECT outcome_id, account_id
                      FROM outcome_evidence
                     WHERE handoff_id = ?
                     LIMIT 1
                    """,
                    (handoff_id,),
                ).fetchone()
                if existing is not None:
                    if existing["account_id"] != account_id:
                        raise HandoffAccessError(
                            f"handoff {handoff_id!r} is not available to this account"
                        )
                    return self.get_outcome_evidence(
                        existing["outcome_id"],
                        actor_id=account_id,
                    )
            conn.execute(
                """
                INSERT INTO outcome_event (
                    outcome_id, run_id, outcome_type, evidence_url,
                    verified_at, verified_by, claim_run_id, payload,
                    recorded_at, note
                ) VALUES (?,?,?,?,NULL,NULL,?,?,?,?)
                """,
                (
                    outcome_id, run_id, event_type_for(outcome_kind),
                    evidence_url or None, run_id or None,
                    canonical_json(base_payload), stamp, note,
                ),
            )
            conn.execute(
                """
                INSERT INTO outcome_evidence (
                    outcome_id, account_id, branch_def_id, branch_version_id,
                    content_hash, run_id, output_field, output_sha256,
                    handoff_id, effect_key, sink, outcome_kind, evidence_source,
                    evidence_level, external_id, normalized_external_ref,
                    attested_by, recorded_at, updated_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    outcome_id, account_id, branch_def_id, branch_version_id,
                    content_hash, run_id, output_field, output_sha256,
                    handoff_id, effect_key, sink, outcome_kind, evidence_source,
                    evidence_level, external_id, artifact_ref, account_id,
                    stamp, stamp,
                ),
            )
            conn.execute(
                """
                INSERT INTO outcome_evidence_transition (
                    transition_id, outcome_id, seq, from_level, to_level,
                    evidence_source, actor_id, evidence_json, recorded_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    new_ulid(), outcome_id, 1, "", evidence_level,
                    evidence_source, account_id,
                    canonical_json({"external_id": external_id, "note": note}),
                    stamp,
                ),
            )
            if artifact_ref:
                # The artifact row is the de-duplication boundary: a second
                # source contributing to the same normalized external id joins
                # the existing artifact instead of creating another one, so the
                # artifact counts once while both attributions survive.
                conn.execute(
                    """
                    INSERT INTO outcome_artifact (
                        artifact_ref, outcome_kind, external_id, first_seen_at
                    ) VALUES (?,?,?,?)
                    ON CONFLICT(artifact_ref) DO NOTHING
                    """,
                    (artifact_ref, outcome_kind, external_id, stamp),
                )
                conn.execute(
                    """
                    INSERT INTO outcome_artifact_source (
                        artifact_ref, outcome_id, contributed_by, recorded_at
                    ) VALUES (?,?,?,?)
                    ON CONFLICT(artifact_ref, outcome_id) DO NOTHING
                    """,
                    (artifact_ref, outcome_id, account_id, stamp),
                )
        return self.get_outcome_evidence(outcome_id, actor_id=account_id)

    def transition_outcome_evidence(
        self,
        outcome_id: str,
        *,
        actor_id: str,
        expected_level: str,
        to_level: str,
        evidence_source: str,
        evidence: dict[str, Any] | None = None,
        external_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        """Append an evidence transition. Never rewrites the original claim.

        ``actor_id`` is recorded as the transitioning actor; the original
        ``attested_by`` column is left untouched, so "who claimed it" survives a
        later verification or dispute by someone else.
        """
        assert_evidence_transition(expected_level, to_level)
        stamp = _now_iso(now)
        with self._open(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM outcome_evidence WHERE outcome_id = ?",
                ((outcome_id or "").strip(),),
            ).fetchone()
            if row is None:
                raise HandoffAccessError(f"outcome {outcome_id!r} not found")
            cursor = conn.execute(
                """
                UPDATE outcome_evidence
                   SET evidence_level = ?, evidence_source = ?, updated_at = ?,
                       external_id = CASE WHEN ? <> '' THEN ? ELSE external_id END,
                       normalized_external_ref = CASE
                           WHEN ? <> '' THEN ? ELSE normalized_external_ref END
                 WHERE outcome_id = ? AND evidence_level = ?
                """,
                (
                    to_level, evidence_source, stamp,
                    external_id, external_id,
                    external_id,
                    normalize_external_ref(row["outcome_kind"], external_id),
                    (outcome_id or "").strip(), expected_level,
                ),
            )
            if cursor.rowcount != 1:
                raise HandoffConflictError(
                    f"outcome {outcome_id!r} is no longer at evidence level "
                    f"{expected_level!r}; re-read before transitioning"
                )
            next_seq = int(
                conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) + 1 FROM outcome_evidence_transition "
                    "WHERE outcome_id = ?",
                    ((outcome_id or "").strip(),),
                ).fetchone()[0]
            )
            conn.execute(
                """
                INSERT INTO outcome_evidence_transition (
                    transition_id, outcome_id, seq, from_level, to_level,
                    evidence_source, actor_id, evidence_json, recorded_at
                ) VALUES (?,?,?,?,?,?,?,?,?)
                """,
                (
                    new_ulid(), (outcome_id or "").strip(), next_seq,
                    expected_level, to_level, evidence_source,
                    (actor_id or "").strip(),
                    canonical_json(evidence or {}), stamp,
                ),
            )
        return self.get_outcome_evidence(outcome_id, actor_id=actor_id)

    def get_outcome_evidence(
        self,
        outcome_id: str,
        *,
        actor_id: str = "",
    ) -> dict[str, Any]:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM outcome_evidence WHERE outcome_id = ?",
                ((outcome_id or "").strip(),),
            ).fetchone()
            if row is None:
                raise HandoffAccessError(f"outcome {outcome_id!r} not found")
            transitions = conn.execute(
                "SELECT * FROM outcome_evidence_transition WHERE outcome_id = ? "
                "ORDER BY seq",
                ((outcome_id or "").strip(),),
            ).fetchall()
            sources = conn.execute(
                "SELECT outcome_id, contributed_by FROM outcome_artifact_source "
                "WHERE artifact_ref = ? AND artifact_ref <> ''",
                (row["normalized_external_ref"],),
            ).fetchall()
        return {
            "outcome_id": row["outcome_id"],
            "account_id": row["account_id"],
            "branch_def_id": row["branch_def_id"],
            "branch_version_id": row["branch_version_id"],
            "content_hash": row["content_hash"],
            "run_id": row["run_id"],
            "output_field": row["output_field"],
            "output_sha256": row["output_sha256"],
            "handoff_id": row["handoff_id"],
            "effect_key": row["effect_key"],
            "sink": row["sink"],
            "outcome_kind": row["outcome_kind"],
            "evidence_source": row["evidence_source"],
            "evidence_level": row["evidence_level"],
            "external_id": row["external_id"],
            "normalized_external_ref": row["normalized_external_ref"],
            "attested_by": row["attested_by"],
            "recorded_at": row["recorded_at"],
            "updated_at": row["updated_at"],
            "transitions": [
                {
                    "seq": int(item["seq"]),
                    "from_level": item["from_level"],
                    "to_level": item["to_level"],
                    "evidence_source": item["evidence_source"],
                    "actor_id": item["actor_id"],
                    "evidence": _load_json(item["evidence_json"]),
                    "recorded_at": item["recorded_at"],
                }
                for item in transitions
            ],
            "artifact_sources": [
                {
                    "outcome_id": item["outcome_id"],
                    "contributed_by": item["contributed_by"],
                }
                for item in sources
            ],
        }

    def outcome_evidence_summary(
        self,
        *,
        account_id: str = "",
        outcome_kind: str = "",
    ) -> dict[str, Any]:
        """Structured counts separated by evidence level and outcome kind.

        Never a single success count: a consumer that wants one has to decide
        which levels it is willing to conflate, in the open. ``artifact_count``
        counts distinct normalized external artifacts, so two sources on one
        artifact do not double it.
        """
        clauses: list[str] = []
        params: list[Any] = []
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if outcome_kind:
            clauses.append("outcome_kind = ?")
            params.append(outcome_kind)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        with self._open() as conn:
            rows = conn.execute(
                f"""
                SELECT outcome_kind, evidence_level, COUNT(*) AS n
                  FROM outcome_evidence {where}
                 GROUP BY outcome_kind, evidence_level
                """,
                params,
            ).fetchall()
            artifacts = conn.execute(
                f"""
                SELECT COUNT(DISTINCT normalized_external_ref) AS n
                  FROM outcome_evidence {where}
                  {'AND' if where else 'WHERE'} normalized_external_ref <> ''
                """,
                params,
            ).fetchone()
        by_kind: dict[str, dict[str, int]] = {}
        by_level: dict[str, int] = {}
        for row in rows:
            kind = row["outcome_kind"]
            level = row["evidence_level"]
            count = int(row["n"])
            by_kind.setdefault(kind, {})[level] = count
            by_level[level] = by_level.get(level, 0) + count
        return {
            "by_outcome_kind": by_kind,
            "by_evidence_level": by_level,
            "artifact_count": int(artifacts["n"] or 0),
            "total_claims": sum(by_level.values()),
        }

    def list_outcome_evidence(
        self,
        *,
        account_id: str = "",
        handoff_id: str = "",
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = []
        params: list[Any] = []
        if account_id:
            clauses.append("account_id = ?")
            params.append(account_id)
        if handoff_id:
            clauses.append("handoff_id = ?")
            params.append(handoff_id)
        where = ("WHERE " + " AND ".join(clauses)) if clauses else ""
        params.append(max(1, min(int(limit or 50), 200)))
        with self._open() as conn:
            rows = conn.execute(
                f"""
                SELECT outcome_id FROM outcome_evidence {where}
                 ORDER BY recorded_at DESC LIMIT ?
                """,
                params,
            ).fetchall()
        return [
            self.get_outcome_evidence(row["outcome_id"], actor_id=account_id)
            for row in rows
        ]


def _load_json(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _row_to_record(row: sqlite3.Row) -> HandoffRecord:
    return HandoffRecord(
        handoff_id=row["handoff_id"],
        owner_id=row["owner_id"],
        effect_key=row["effect_key"],
        sink=row["sink"],
        adapter_action=row["adapter_action"],
        destination=row["destination"],
        branch_def_id=row["branch_def_id"],
        branch_version_id=row["branch_version_id"],
        content_hash=row["content_hash"],
        run_id=row["run_id"],
        output_field=row["output_field"],
        output_sha256=row["output_sha256"],
        effect_class=row["effect_class"],
        outcome_kind=row["outcome_kind"],
        credential_class=row["credential_class"],
        state=row["state"],
        external_id=row["external_id"],
        declaration=_load_json(row["declaration_json"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


__all__ = ["HandoffStore"]
