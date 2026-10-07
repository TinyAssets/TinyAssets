"""Owner-scoped authoring persistence — sessions, events, versions, file
handles, and per-run effect confirmations.

Task 4.1 of ``openspec/changes/archive/2026-08-26-complete-independent-full-platform-targets``.

Substrate choice: one SQLite database under the canonical data root, the same
shape every shipped per-context store uses (see
``tinyassets/storage/effector_consents.py``) — WAL, a 30s busy timeout, and
``CREATE TABLE IF NOT EXISTS`` DDL applied on open. The prototype Postgres
mirror of these tables lives in
``prototype/full-platform-v0/migrations/012_authoring_sessions.sql`` for the
platform store; no decision has chosen a canonical store, so neither is treated as
the single substrate (design.md: "Target guarantees are technology-neutral where
PLAN has not chosen a substrate").

Invariants this module owns:

- **Owner scoping is in the SQL, not the caller.** Every session/event/handle
  read carries ``owner_id`` in its WHERE clause, and a miss raises the single
  indistinguishable :class:`~tinyassets.authoring.models.AuthoringAccessError`
  so a probe cannot tell "someone else's" from "nonexistent".
- **Draft advance is compare-and-swap.** ``commit_definition`` updates only when
  the stored ``draft_version`` still matches what the caller read, and inserts
  the session event in the *same* immediate transaction — so a committed edit
  can never lose its event and event ``seq`` stays contiguous.
- **Published versions are immutable.** There is no UPDATE path for
  ``authoring_versions``; a later edit publishes another row.
- **Confirmations are single-use rows**, not signed blobs, so consumption is
  atomic and a replay cannot be forged.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from tinyassets.authoring.models import (
    ArtifactVersion,
    AuthoringConflictError,
    AuthoringEvent,
    AuthoringSession,
    access_denied,
    canonical_json,
)
from tinyassets.ids import new_ulid

DB_FILENAME = ".authoring.db"
BLOB_DIRNAME = ".authoring_blobs"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS authoring_sessions (
    session_id        TEXT PRIMARY KEY,
    owner_id          TEXT NOT NULL,
    artifact_id       TEXT NOT NULL,
    artifact_kind     TEXT NOT NULL,
    seed_mode         TEXT NOT NULL,
    seed_ref          TEXT NOT NULL DEFAULT '',
    parent_version_id TEXT NOT NULL DEFAULT '',
    status            TEXT NOT NULL,
    draft_version     INTEGER NOT NULL,
    definition_json   TEXT NOT NULL,
    definition_hash   TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    retention_until   TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_authoring_sessions_owner
    ON authoring_sessions(owner_id, created_at);

CREATE TABLE IF NOT EXISTS authoring_events (
    event_id        TEXT PRIMARY KEY,
    session_id      TEXT NOT NULL,
    owner_id        TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    event_type      TEXT NOT NULL,
    created_at      TEXT NOT NULL,
    definition_hash TEXT NOT NULL,
    payload_json    TEXT NOT NULL,
    UNIQUE (session_id, seq)
);

CREATE INDEX IF NOT EXISTS idx_authoring_events_session
    ON authoring_events(session_id, seq);

CREATE TABLE IF NOT EXISTS authoring_versions (
    version_id        TEXT PRIMARY KEY,
    artifact_id       TEXT NOT NULL,
    artifact_kind     TEXT NOT NULL,
    version_no        INTEGER NOT NULL,
    owner_id          TEXT NOT NULL,
    visibility        TEXT NOT NULL,
    definition_json   TEXT NOT NULL,
    definition_hash   TEXT NOT NULL,
    parent_version_id TEXT NOT NULL DEFAULT '',
    change_message    TEXT NOT NULL DEFAULT '',
    created_at        TEXT NOT NULL,
    provenance_json   TEXT NOT NULL DEFAULT '{}',
    evidence_json     TEXT NOT NULL DEFAULT '{}',
    UNIQUE (artifact_id, version_no)
);

CREATE INDEX IF NOT EXISTS idx_authoring_versions_artifact
    ON authoring_versions(artifact_id, version_no);

CREATE TABLE IF NOT EXISTS authoring_file_handles (
    handle_id     TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    input_name    TEXT NOT NULL DEFAULT '',
    filename      TEXT NOT NULL,
    media_type    TEXT NOT NULL,
    size_bytes    INTEGER NOT NULL,
    sha256        TEXT NOT NULL,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    revoked_at    REAL
);

CREATE INDEX IF NOT EXISTS idx_authoring_handles_session
    ON authoring_file_handles(session_id, owner_id);

CREATE TABLE IF NOT EXISTS authoring_confirmations (
    token         TEXT PRIMARY KEY,
    session_id    TEXT NOT NULL,
    owner_id      TEXT NOT NULL,
    draft_version INTEGER NOT NULL,
    fingerprint   TEXT NOT NULL,
    created_at    REAL NOT NULL,
    expires_at    REAL NOT NULL,
    consumed_at   REAL
);
"""


class AuthoringStore:
    """The authoring bounded-context store."""

    def __init__(self, base_path: str | Path | None = None) -> None:
        if base_path is None:
            from tinyassets.storage import data_dir

            base_path = data_dir()
        self._base = Path(base_path)

    # ── plumbing ──────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self._base / DB_FILENAME

    @property
    def blob_root(self) -> Path:
        return self._base / BLOB_DIRNAME

    @staticmethod
    def now() -> float:
        return time.time()

    @staticmethod
    def timestamp(now: float | None = None) -> str:
        moment = datetime.fromtimestamp(
            now if now is not None else time.time(), tz=timezone.utc
        )
        return moment.isoformat()

    def initialize(self) -> Path:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.executescript(_SCHEMA)
        return self.path

    def _connect(self, *, timeout: float = 30.0, existing: bool = False) -> sqlite3.Connection:
        if not existing:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        target = self.path.resolve().as_uri() + "?mode=rw" if existing else self.path
        conn = sqlite3.connect(target, timeout=timeout, isolation_level=None, uri=existing)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute(f"PRAGMA busy_timeout = {max(1, int(timeout * 1000))}")
        conn.execute("PRAGMA foreign_keys = ON")
        return conn

    @contextlib.contextmanager
    def _open(self, *, write: bool = False) -> Iterator[sqlite3.Connection]:
        """Yield a connection; ``write=True`` wraps the body in BEGIN IMMEDIATE."""
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
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

    # ── sessions ──────────────────────────────────────────────────────────

    def create_session(self, session: AuthoringSession) -> AuthoringSession:
        with self._open(write=True) as conn:
            conn.execute(
                """
                INSERT INTO authoring_sessions (
                    session_id, owner_id, artifact_id, artifact_kind, seed_mode,
                    seed_ref, parent_version_id, status, draft_version,
                    definition_json, definition_hash, created_at, updated_at,
                    retention_until
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    session.session_id,
                    session.owner_id,
                    session.artifact_id,
                    session.artifact_kind,
                    session.seed_mode,
                    session.seed_ref,
                    session.parent_version_id,
                    session.status,
                    session.draft_version,
                    canonical_json(session.definition),
                    session.definition_hash,
                    session.created_at,
                    session.updated_at,
                    session.retention_until,
                ),
            )
            self._insert_event(
                conn,
                session_id=session.session_id,
                owner_id=session.owner_id,
                event_type="created",
                definition_hash=session.definition_hash,
                payload={
                    "seed_mode": session.seed_mode,
                    "seed_ref": session.seed_ref,
                    "artifact_kind": session.artifact_kind,
                    "draft_version": session.draft_version,
                    # The seed document is kept on its event so a diff anchored
                    # at creation replays exactly, never against a substitute.
                    "definition": session.definition,
                },
                created_at=session.created_at,
            )
        return session

    def get_session(self, session_id: str, *, actor_id: str) -> AuthoringSession:
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM authoring_sessions WHERE session_id = ? AND owner_id = ?",
                (session_id, actor_id),
            ).fetchone()
        if row is None:
            raise access_denied()
        return self._session_from_row(row)

    def list_sessions(self, *, actor_id: str, limit: int = 50) -> list[AuthoringSession]:
        with self._open() as conn:
            rows = conn.execute(
                """
                SELECT * FROM authoring_sessions
                WHERE owner_id = ?
                ORDER BY created_at DESC, session_id DESC
                LIMIT ?
                """,
                (actor_id, max(1, min(int(limit), 500))),
            ).fetchall()
        return [self._session_from_row(row) for row in rows]

    def commit_definition(
        self,
        session_id: str,
        *,
        actor_id: str,
        expected_version: int,
        definition: dict[str, Any],
        event_type: str,
        payload: dict[str, Any],
    ) -> tuple[AuthoringSession, AuthoringEvent]:
        """CAS the draft forward and record its event in one transaction."""
        from tinyassets.authoring.models import definition_hash as hash_definition

        new_hash = hash_definition(definition)
        now = self.now()
        stamp = self.timestamp(now)
        with self._open(write=True) as conn:
            row = conn.execute(
                "SELECT * FROM authoring_sessions WHERE session_id = ? AND owner_id = ?",
                (session_id, actor_id),
            ).fetchone()
            if row is None:
                raise access_denied()
            if int(row["draft_version"]) != int(expected_version):
                raise AuthoringConflictError(
                    "draft advanced: expected version "
                    f"{expected_version}, stored {row['draft_version']}"
                )
            next_version = int(expected_version) + 1
            conn.execute(
                """
                UPDATE authoring_sessions
                   SET definition_json = ?, definition_hash = ?, draft_version = ?,
                       updated_at = ?
                 WHERE session_id = ? AND owner_id = ? AND draft_version = ?
                """,
                (
                    canonical_json(definition),
                    new_hash,
                    next_version,
                    stamp,
                    session_id,
                    actor_id,
                    int(expected_version),
                ),
            )
            event = self._insert_event(
                conn,
                session_id=session_id,
                owner_id=actor_id,
                event_type=event_type,
                definition_hash=new_hash,
                payload={**payload, "draft_version": next_version},
                created_at=stamp,
            )
            row = conn.execute(
                "SELECT * FROM authoring_sessions WHERE session_id = ?",
                (session_id,),
            ).fetchone()
        return self._session_from_row(row), event

    # ── events ────────────────────────────────────────────────────────────

    def append_event(
        self,
        session_id: str,
        *,
        actor_id: str,
        event_type: str,
        definition_hash: str,
        payload: dict[str, Any],
    ) -> AuthoringEvent:
        with self._open(write=True) as conn:
            row = conn.execute(
                "SELECT 1 FROM authoring_sessions WHERE session_id = ? AND owner_id = ?",
                (session_id, actor_id),
            ).fetchone()
            if row is None:
                raise access_denied()
            return self._insert_event(
                conn,
                session_id=session_id,
                owner_id=actor_id,
                event_type=event_type,
                definition_hash=definition_hash,
                payload=payload,
                created_at=self.timestamp(),
            )

    def list_events(
        self, session_id: str, *, actor_id: str, limit: int = 200
    ) -> list[AuthoringEvent]:
        with self._open() as conn:
            row = conn.execute(
                "SELECT 1 FROM authoring_sessions WHERE session_id = ? AND owner_id = ?",
                (session_id, actor_id),
            ).fetchone()
            if row is None:
                raise access_denied()
            rows = conn.execute(
                """
                SELECT * FROM authoring_events
                WHERE session_id = ? AND owner_id = ?
                ORDER BY seq ASC LIMIT ?
                """,
                (session_id, actor_id, max(1, min(int(limit), 1000))),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def get_event(self, session_id: str, event_id: str, *, actor_id: str) -> AuthoringEvent:
        with self._open() as conn:
            row = conn.execute(
                """
                SELECT * FROM authoring_events
                WHERE session_id = ? AND owner_id = ? AND event_id = ?
                """,
                (session_id, actor_id, event_id),
            ).fetchone()
        if row is None:
            raise access_denied()
        return self._event_from_row(row)

    def find_test_events(
        self, session_id: str, *, actor_id: str, definition_hash: str
    ) -> list[AuthoringEvent]:
        with self._open() as conn:
            rows = conn.execute(
                """
                SELECT * FROM authoring_events
                WHERE session_id = ? AND owner_id = ? AND event_type = 'test'
                  AND definition_hash = ?
                ORDER BY seq ASC
                """,
                (session_id, actor_id, definition_hash),
            ).fetchall()
        return [self._event_from_row(row) for row in rows]

    def _insert_event(
        self,
        conn: sqlite3.Connection,
        *,
        session_id: str,
        owner_id: str,
        event_type: str,
        definition_hash: str,
        payload: dict[str, Any],
        created_at: str,
    ) -> AuthoringEvent:
        seq = int(
            conn.execute(
                "SELECT COALESCE(MAX(seq), 0) + 1 FROM authoring_events WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
        )
        event = AuthoringEvent(
            event_id=f"evt_{new_ulid()}",
            session_id=session_id,
            seq=seq,
            event_type=event_type,
            created_at=created_at,
            definition_hash=definition_hash,
            payload=payload,
        )
        conn.execute(
            """
            INSERT INTO authoring_events (
                event_id, session_id, owner_id, seq, event_type, created_at,
                definition_hash, payload_json
            ) VALUES (?,?,?,?,?,?,?,?)
            """,
            (
                event.event_id,
                session_id,
                owner_id,
                seq,
                event_type,
                created_at,
                definition_hash,
                json.dumps(payload, default=str),
            ),
        )
        return event

    # ── versions ──────────────────────────────────────────────────────────

    def publish_version(
        self,
        *,
        artifact_id: str,
        artifact_kind: str,
        owner_id: str,
        visibility: str,
        definition: dict[str, Any],
        definition_hash: str,
        change_message: str,
        provenance: dict[str, Any],
        evidence: dict[str, Any],
        source_session_id: str = "",
        expected_draft_version: int | None = None,
    ) -> ArtifactVersion:
        """Insert one immutable version.

        When *source_session_id* / *expected_draft_version* are supplied, the
        session's version is re-checked **inside this transaction**, so a draft
        that advances between the caller's review and this insert cannot publish
        the stale reviewed definition. Re-publishing an already-published
        (definition hash, source draft version) pair is refused rather than
        producing duplicate lineage.
        """
        stamp = self.timestamp()
        with self._open(write=True) as conn:
            if source_session_id and expected_draft_version is not None:
                session_row = conn.execute(
                    """
                    SELECT draft_version, definition_hash FROM authoring_sessions
                    WHERE session_id = ? AND owner_id = ?
                    """,
                    (source_session_id, owner_id),
                ).fetchone()
                if session_row is None:
                    raise access_denied()
                if int(session_row["draft_version"]) != int(expected_draft_version):
                    raise AuthoringConflictError(
                        "the session advanced before publication committed: "
                        f"reviewed version {expected_draft_version}, stored "
                        f"{session_row['draft_version']}"
                    )
                if session_row["definition_hash"] != definition_hash:
                    raise AuthoringConflictError(
                        "the session definition changed before publication "
                        "committed; the reviewed definition was not published"
                    )
                duplicate = conn.execute(
                    """
                    SELECT version_no FROM authoring_versions
                    WHERE artifact_id = ? AND definition_hash = ?
                      AND json_extract(provenance_json, '$.source_draft_version') = ?
                    LIMIT 1
                    """,
                    (artifact_id, definition_hash, int(expected_draft_version)),
                ).fetchone()
                if duplicate is not None:
                    raise AuthoringConflictError(
                        "this exact draft version is already published as version "
                        f"{duplicate['version_no']}; edit the draft before "
                        "publishing again"
                    )
            row = conn.execute(
                """
                SELECT version_id, version_no FROM authoring_versions
                WHERE artifact_id = ? ORDER BY version_no DESC LIMIT 1
                """,
                (artifact_id,),
            ).fetchone()
            parent_version_id = row["version_id"] if row else ""
            version_no = (int(row["version_no"]) + 1) if row else 1
            version = ArtifactVersion(
                version_id=f"ver_{new_ulid()}",
                artifact_id=artifact_id,
                artifact_kind=artifact_kind,
                version_no=version_no,
                owner_id=owner_id,
                visibility=visibility,
                definition=definition,
                definition_hash=definition_hash,
                parent_version_id=parent_version_id,
                change_message=change_message,
                created_at=stamp,
                provenance=provenance,
                evidence=evidence,
            )
            conn.execute(
                """
                INSERT INTO authoring_versions (
                    version_id, artifact_id, artifact_kind, version_no, owner_id,
                    visibility, definition_json, definition_hash, parent_version_id,
                    change_message, created_at, provenance_json, evidence_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    version.version_id,
                    artifact_id,
                    artifact_kind,
                    version_no,
                    owner_id,
                    visibility,
                    canonical_json(definition),
                    definition_hash,
                    parent_version_id,
                    change_message,
                    stamp,
                    json.dumps(provenance, default=str),
                    json.dumps(evidence, default=str),
                ),
            )
        return version

    def get_version(self, version_id: str, *, actor_id: str) -> ArtifactVersion:
        """Read one version. Public versions are readable by any actor."""
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM authoring_versions WHERE version_id = ?",
                (version_id,),
            ).fetchone()
        if row is None:
            raise access_denied()
        if row["visibility"] != "public" and row["owner_id"] != actor_id:
            raise access_denied()
        return self._version_from_row(row)

    def latest_version_for_artifact(
        self, artifact_id: str, *, actor_id: str
    ) -> ArtifactVersion | None:
        with self._open() as conn:
            row = conn.execute(
                """
                SELECT * FROM authoring_versions WHERE artifact_id = ?
                ORDER BY version_no DESC LIMIT 1
                """,
                (artifact_id,),
            ).fetchone()
        if row is None:
            return None
        if row["visibility"] != "public" and row["owner_id"] != actor_id:
            return None
        return self._version_from_row(row)

    def list_versions(
        self, *, actor_id: str, artifact_id: str = "", limit: int = 50
    ) -> list[ArtifactVersion]:
        query = (
            "SELECT * FROM authoring_versions WHERE (visibility = 'public' OR owner_id = ?)"
        )
        params: list[Any] = [actor_id]
        if artifact_id:
            query += " AND artifact_id = ?"
            params.append(artifact_id)
        query += " ORDER BY created_at DESC, version_id DESC LIMIT ?"
        params.append(max(1, min(int(limit), 500)))
        with self._open() as conn:
            rows = conn.execute(query, tuple(params)).fetchall()
        return [self._version_from_row(row) for row in rows]

    # ── file handles ──────────────────────────────────────────────────────

    def put_file_handle(
        self,
        *,
        session_id: str,
        owner_id: str,
        input_name: str,
        filename: str,
        media_type: str,
        content: bytes,
        lifetime_seconds: float,
        now: float | None = None,
    ) -> dict[str, Any]:
        moment = self.now() if now is None else now
        handle_id = f"fh_{new_ulid()}"
        digest = hashlib.sha256(content).hexdigest()
        blob = self.blob_path(session_id, handle_id)
        blob.parent.mkdir(parents=True, exist_ok=True)
        blob.write_bytes(content)
        with self._open(write=True) as conn:
            conn.execute(
                """
                INSERT INTO authoring_file_handles (
                    handle_id, session_id, owner_id, input_name, filename,
                    media_type, size_bytes, sha256, created_at, expires_at, revoked_at
                ) VALUES (?,?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    handle_id,
                    session_id,
                    owner_id,
                    input_name,
                    filename,
                    media_type,
                    len(content),
                    digest,
                    moment,
                    moment + float(lifetime_seconds),
                ),
            )
        return {
            "handle_id": handle_id,
            "input_name": input_name,
            "filename": filename,
            "media_type": media_type,
            "size_bytes": len(content),
            "sha256": digest,
            "expires_at": moment + float(lifetime_seconds),
        }

    def blob_path(self, session_id: str, handle_id: str) -> Path:
        return self.blob_root / session_id / handle_id

    def get_file_handle(
        self,
        handle_id: str,
        *,
        actor_id: str,
        session_id: str = "",
        now: float | None = None,
    ) -> dict[str, Any]:
        moment = self.now() if now is None else now
        with self._open() as conn:
            row = conn.execute(
                "SELECT * FROM authoring_file_handles WHERE handle_id = ? AND owner_id = ?",
                (handle_id, actor_id),
            ).fetchone()
        return self._file_handle_metadata(row, session_id=session_id, moment=moment)

    @staticmethod
    def _file_handle_metadata(row, *, session_id, moment):
        if row is None:
            raise access_denied()
        if session_id and row["session_id"] != session_id:
            raise access_denied()
        if row["revoked_at"] is not None:
            raise access_denied()
        if float(row["expires_at"]) <= moment:
            raise access_denied()
        return {
            "handle_id": row["handle_id"],
            "session_id": row["session_id"],
            "owner_id": row["owner_id"],
            "input_name": row["input_name"],
            "filename": row["filename"],
            "media_type": row["media_type"],
            "size_bytes": int(row["size_bytes"]),
            "sha256": row["sha256"],
            "expires_at": float(row["expires_at"]),
        }

    @contextlib.contextmanager
    def file_handle_commit_fence(
        self, *, handle_id: str, actor_id: str, session_id: str, timeout_seconds: float = 2.0,
    ):
        """Short metadata-only source authority fence; no filesystem IO inside.

        Caller holds operation exclusion and canonical author/tombstone fence
        first, then this source-store fence, then the destination runs writer.
        Existing authoring writers touch only this store; never acquire the
        canonical author/runs writer from inside another authoring operation.
        """
        if (type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 5
                or not actor_id or not session_id or not handle_id):
            raise ValueError("invalid authoring source fence")
        conn = self._connect(timeout=timeout_seconds, existing=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT h.* FROM authoring_file_handles h JOIN authoring_sessions s "
                "ON s.session_id=h.session_id AND s.owner_id=h.owner_id "
                "WHERE h.handle_id=? AND h.owner_id=? AND h.session_id=?",
                (handle_id, actor_id, session_id),
            ).fetchone()
            yield self._file_handle_metadata(row, session_id=session_id, moment=self.now())
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    @contextlib.contextmanager
    def file_handles_commit_fence(self, *, sources, actor_id, timeout_seconds=2.0):
        """One source-store fence for a bundle, never nested writers per handle.

        Input is trusted service metadata, not an authority token. No byte IO
        inside; canonical author fence precedes this, runs binding follows it.
        """
        if (type(timeout_seconds) not in (int, float) or not 0 < timeout_seconds <= 5
                or type(actor_id) is not str or not actor_id
                or not isinstance(sources, list) or not sources):
            raise ValueError("invalid authoring source bundle fence")
        conn = self._connect(timeout=timeout_seconds, existing=True)
        try:
            conn.execute("BEGIN IMMEDIATE")
            result = []
            for source in sources:
                session_id, handle_id = source["session_id"], source["handle_id"]
                row = conn.execute(
                    "SELECT h.* FROM authoring_file_handles h JOIN authoring_sessions s "
                    "ON s.session_id=h.session_id AND s.owner_id=h.owner_id "
                    "WHERE h.handle_id=? AND h.owner_id=? AND h.session_id=?",
                    (handle_id, actor_id, session_id),
                ).fetchone()
                result.append(self._file_handle_metadata(
                    row, session_id=session_id, moment=self.now()
                ))
            yield result
            conn.execute("COMMIT")
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def revoke_file_handle(self, handle_id: str, *, actor_id: str) -> None:
        with self._open(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE authoring_file_handles SET revoked_at = ?
                 WHERE handle_id = ? AND owner_id = ? AND revoked_at IS NULL
                """,
                (self.now(), handle_id, actor_id),
            )
            if cursor.rowcount == 0:
                raise access_denied()

    # ── per-run effect confirmations ───────────────────────────────────────

    def create_confirmation(
        self,
        *,
        session_id: str,
        owner_id: str,
        draft_version: int,
        fingerprint: str,
        ttl_seconds: float,
        now: float | None = None,
    ) -> str:
        moment = self.now() if now is None else now
        token = f"cfm_{new_ulid()}"
        with self._open(write=True) as conn:
            conn.execute(
                """
                INSERT INTO authoring_confirmations (
                    token, session_id, owner_id, draft_version, fingerprint,
                    created_at, expires_at, consumed_at
                ) VALUES (?,?,?,?,?,?,?,NULL)
                """,
                (
                    token,
                    session_id,
                    owner_id,
                    int(draft_version),
                    fingerprint,
                    moment,
                    moment + float(ttl_seconds),
                ),
            )
        return token

    def consume_confirmation(
        self,
        token: str,
        *,
        session_id: str,
        draft_version: int,
        fingerprint: str,
        now: float | None = None,
    ) -> bool:
        """Atomically consume a matching, unexpired, unused confirmation."""
        moment = self.now() if now is None else now
        with self._open(write=True) as conn:
            cursor = conn.execute(
                """
                UPDATE authoring_confirmations SET consumed_at = ?
                 WHERE token = ? AND session_id = ? AND draft_version = ?
                   AND fingerprint = ? AND consumed_at IS NULL AND expires_at > ?
                """,
                (moment, token, session_id, int(draft_version), fingerprint, moment),
            )
            return cursor.rowcount == 1

    # ── row mapping ───────────────────────────────────────────────────────

    @staticmethod
    def _session_from_row(row: sqlite3.Row) -> AuthoringSession:
        return AuthoringSession(
            session_id=row["session_id"],
            owner_id=row["owner_id"],
            artifact_id=row["artifact_id"],
            artifact_kind=row["artifact_kind"],
            seed_mode=row["seed_mode"],
            seed_ref=row["seed_ref"],
            status=row["status"],
            draft_version=int(row["draft_version"]),
            definition=json.loads(row["definition_json"]),
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            retention_until=row["retention_until"],
            parent_version_id=row["parent_version_id"],
        )

    @staticmethod
    def _event_from_row(row: sqlite3.Row) -> AuthoringEvent:
        return AuthoringEvent(
            event_id=row["event_id"],
            session_id=row["session_id"],
            seq=int(row["seq"]),
            event_type=row["event_type"],
            created_at=row["created_at"],
            definition_hash=row["definition_hash"],
            payload=json.loads(row["payload_json"]),
        )

    @staticmethod
    def _version_from_row(row: sqlite3.Row) -> ArtifactVersion:
        return ArtifactVersion(
            version_id=row["version_id"],
            artifact_id=row["artifact_id"],
            artifact_kind=row["artifact_kind"],
            version_no=int(row["version_no"]),
            owner_id=row["owner_id"],
            visibility=row["visibility"],
            definition=json.loads(row["definition_json"]),
            definition_hash=row["definition_hash"],
            parent_version_id=row["parent_version_id"],
            change_message=row["change_message"],
            created_at=row["created_at"],
            provenance=json.loads(row["provenance_json"]),
            evidence=json.loads(row["evidence_json"]),
        )
