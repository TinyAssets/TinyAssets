"""Private-universe conversation persistence behind one-use custody grants.

This module is intentionally not re-exported from :mod:`tinyassets.storage`.
It owns no app ingress, provider call, workflow mutation, or public API.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from pathlib import Path

import tinyassets.conversation_custody as custody_domain
from tinyassets.conversation_custody import (
    ACTIVE_SQLITE_DELETION_SCOPE,
    CONVERSATION_CUSTODY_SCHEMA,
    HISTORICAL_BACKUP_CAVEAT,
    PRIVATE_UNIVERSE_MODE,
    ConversationCustodyAuthorizationError,
    ConversationCustodyGrantEvidence,
    ConversationCustodyOperationGrant,
    ConversationCustodyScope,
    ConversationDeletionReceipt,
    ConversationExport,
    ConversationMessage,
    ConversationSnapshot,
    ConversationThread,
    StorageFileIdentity,
    _parsed_timestamp,
    append_message_request_digest,
    canonical_json_bytes,
    consume_operation_grant,
    create_thread_request_digest,
    delete_thread_request_digest,
    deleted_target_digest,
    export_conversation,
    idempotency_key_digest,
    thread_request_digest,
    validate_private_universe_location,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS conversation_custody_database_binding (
    singleton_id INTEGER PRIMARY KEY CHECK (singleton_id = 1),
    universe_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_custody_consumed_grants (
    grant_id TEXT PRIMARY KEY,
    authority_key_id TEXT NOT NULL,
    expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_custody_threads (
    conversation_id TEXT PRIMARY KEY,
    schema_name TEXT NOT NULL,
    custody_mode TEXT NOT NULL,
    owner_user_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    agent_binding_id TEXT NOT NULL,
    interlocutor_ref TEXT NOT NULL,
    retention_until TEXT,
    created_at TEXT NOT NULL,
    record_json BLOB NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_custody_messages (
    message_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    ordinal INTEGER NOT NULL CHECK (ordinal >= 1),
    kind TEXT NOT NULL,
    participant_ref TEXT NOT NULL,
    source_event_ref TEXT NOT NULL,
    reply_to_message_id TEXT,
    payload_json BLOB NOT NULL,
    payload_digest TEXT NOT NULL,
    created_at TEXT NOT NULL,
    record_json BLOB NOT NULL,
    UNIQUE (conversation_id, ordinal),
    FOREIGN KEY (conversation_id)
        REFERENCES conversation_custody_threads(conversation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversation_custody_messages_thread
    ON conversation_custody_messages(conversation_id, ordinal);

CREATE TABLE IF NOT EXISTS conversation_custody_idempotency (
    owner_user_id TEXT NOT NULL,
    operation_kind TEXT NOT NULL,
    idempotency_key_digest TEXT NOT NULL,
    request_digest TEXT,
    conversation_id TEXT,
    result_ref TEXT,
    deleted_target_digest TEXT,
    PRIMARY KEY (owner_user_id, operation_kind, idempotency_key_digest),
    CHECK (operation_kind IN ('create_thread', 'append_message', 'delete_thread'))
);

CREATE TABLE IF NOT EXISTS conversation_custody_deletions (
    deleted_target_digest TEXT PRIMARY KEY,
    owner_user_id TEXT NOT NULL,
    universe_id TEXT NOT NULL,
    agent_binding_id TEXT NOT NULL,
    conversation_id TEXT NOT NULL,
    reason TEXT NOT NULL CHECK (reason IN ('owner_request', 'retention_expired')),
    logical_deleted_at TEXT NOT NULL,
    cleanup_completed_at TEXT,
    deleted_message_count INTEGER NOT NULL CHECK (deleted_message_count >= 0),
    deletion_scope TEXT NOT NULL,
    historical_backup_caveat TEXT NOT NULL,
    receipt_json BLOB
);
"""


class ConversationCustodyStoreError(RuntimeError):
    """Base class for private conversation store failures."""


class ConversationCustodyConflict(ConversationCustodyStoreError):
    """An idempotency key is already bound to different canonical input."""


class ConversationCustodyNotFound(ConversationCustodyStoreError):
    """No intact authorized conversation exists at the requested scope."""


class ConversationCustodyReplyError(ConversationCustodyStoreError):
    """A reply target is not an earlier message in the same thread."""


class ConversationCustodyIntegrityError(ConversationCustodyStoreError):
    """Persisted custody data disagrees with its canonical envelope."""


class ConversationCustodyDeleted(ConversationCustodyStoreError):
    """The addressed conversation or mutation key was tombstoned."""


class ConversationCustodyRetentionError(ConversationCustodyStoreError):
    """Retention-based deletion was requested before its stored boundary."""


class ConversationCustodyCleanupPending(ConversationCustodyStoreError):
    """Logical deletion committed but active-store cleanup is incomplete."""


def _canonical_record(value: dict[str, object]) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _thaw(value: object) -> object:
    from collections.abc import Mapping

    if isinstance(value, Mapping):
        return {key: _thaw(child) for key, child in value.items()}
    if type(value) is tuple:
        return [_thaw(child) for child in value]
    return value


def _thread_record(thread: ConversationThread) -> dict[str, object]:
    return {
        "agent_binding_id": thread.agent_binding_id,
        "conversation_id": thread.conversation_id,
        "created_at": thread.created_at,
        "custody_mode": thread.custody_mode,
        "interlocutor_ref": thread.interlocutor_ref,
        "owner_user_id": thread.owner_user_id,
        "retention_until": thread.retention_until,
        "schema": thread.schema,
        "universe_id": thread.universe_id,
    }


def _message_record(message: ConversationMessage) -> dict[str, object]:
    return {
        "conversation_id": message.conversation_id,
        "created_at": message.created_at,
        "kind": message.kind,
        "message_id": message.message_id,
        "ordinal": message.ordinal,
        "participant_ref": message.participant_ref,
        "payload": _thaw(message.payload),
        "payload_digest": message.payload_digest,
        "reply_to_message_id": message.reply_to_message_id,
        "source_event_ref": message.source_event_ref,
    }


def _decoded_record(raw: object, *, expected_members: frozenset[str]) -> dict[str, object]:
    if type(raw) is not bytes:
        raise ConversationCustodyIntegrityError("canonical record is not a SQLite BLOB")
    try:
        decoded = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConversationCustodyIntegrityError("canonical record is invalid JSON") from exc
    if type(decoded) is not dict or frozenset(decoded) != expected_members:
        raise ConversationCustodyIntegrityError("canonical record member set disagrees")
    if _canonical_record(decoded) != raw:
        raise ConversationCustodyIntegrityError("canonical record bytes disagree")
    return decoded


_THREAD_MEMBERS = frozenset(
    {
        "agent_binding_id",
        "conversation_id",
        "created_at",
        "custody_mode",
        "interlocutor_ref",
        "owner_user_id",
        "retention_until",
        "schema",
        "universe_id",
    }
)
_MESSAGE_MEMBERS = frozenset(
    {
        "conversation_id",
        "created_at",
        "kind",
        "message_id",
        "ordinal",
        "participant_ref",
        "payload",
        "payload_digest",
        "reply_to_message_id",
        "source_event_ref",
    }
)
_RECEIPT_MEMBERS = frozenset(
    {
        "agent_binding_id",
        "cleanup_completed_at",
        "conversation_id",
        "deleted_message_count",
        "deletion_scope",
        "historical_backup_caveat",
        "logical_deleted_at",
        "owner_user_id",
        "reason",
        "universe_id",
    }
)


def _receipt_record(receipt: ConversationDeletionReceipt) -> dict[str, object]:
    return {
        "agent_binding_id": receipt.agent_binding_id,
        "cleanup_completed_at": receipt.cleanup_completed_at,
        "conversation_id": receipt.conversation_id,
        "deleted_message_count": receipt.deleted_message_count,
        "deletion_scope": receipt.deletion_scope,
        "historical_backup_caveat": receipt.historical_backup_caveat,
        "logical_deleted_at": receipt.logical_deleted_at,
        "owner_user_id": receipt.owner_user_id,
        "reason": receipt.reason,
        "universe_id": receipt.universe_id,
    }


def _load_deletion_receipt(row: sqlite3.Row) -> ConversationDeletionReceipt | None:
    try:
        expected_target = deleted_target_digest(
            ConversationCustodyScope(
                owner_user_id=row["owner_user_id"],
                universe_id=row["universe_id"],
                agent_binding_id=row["agent_binding_id"],
            ),
            conversation_id=row["conversation_id"],
        )
    except (custody_domain.ConversationCustodyValidationError, TypeError, ValueError) as exc:
        raise ConversationCustodyIntegrityError("deletion scope columns are invalid") from exc
    if (
        expected_target != row["deleted_target_digest"]
        or row["deletion_scope"] != ACTIVE_SQLITE_DELETION_SCOPE
        or row["historical_backup_caveat"] != HISTORICAL_BACKUP_CAVEAT
    ):
        raise ConversationCustodyIntegrityError("deletion target or scope disagrees")
    if row["cleanup_completed_at"] is None and row["receipt_json"] is None:
        return None
    if row["cleanup_completed_at"] is None or row["receipt_json"] is None:
        raise ConversationCustodyIntegrityError("deletion completion state is partial")
    record = _decoded_record(row["receipt_json"], expected_members=_RECEIPT_MEMBERS)
    try:
        receipt = ConversationDeletionReceipt(
            owner_user_id=record["owner_user_id"],
            universe_id=record["universe_id"],
            agent_binding_id=record["agent_binding_id"],
            conversation_id=record["conversation_id"],
            reason=record["reason"],
            logical_deleted_at=record["logical_deleted_at"],
            cleanup_completed_at=record["cleanup_completed_at"],
            deleted_message_count=record["deleted_message_count"],
        )
    except (TypeError, ValueError) as exc:
        raise ConversationCustodyIntegrityError("deletion receipt is invalid") from exc
    indexed = (
        row["owner_user_id"],
        row["universe_id"],
        row["agent_binding_id"],
        row["conversation_id"],
        row["reason"],
        row["logical_deleted_at"],
        row["cleanup_completed_at"],
        row["deleted_message_count"],
        row["deletion_scope"],
        row["historical_backup_caveat"],
    )
    canonical = (
        receipt.owner_user_id,
        receipt.universe_id,
        receipt.agent_binding_id,
        receipt.conversation_id,
        receipt.reason,
        receipt.logical_deleted_at,
        receipt.cleanup_completed_at,
        receipt.deleted_message_count,
        receipt.deletion_scope,
        receipt.historical_backup_caveat,
    )
    if indexed != canonical or _canonical_record(_receipt_record(receipt)) != row["receipt_json"]:
        raise ConversationCustodyIntegrityError("deletion receipt columns disagree")
    return receipt


def _load_thread_row(row: sqlite3.Row) -> ConversationThread:
    record = _decoded_record(row["record_json"], expected_members=_THREAD_MEMBERS)
    if (
        record["schema"] != CONVERSATION_CUSTODY_SCHEMA
        or record["custody_mode"] != PRIVATE_UNIVERSE_MODE
    ):
        raise ConversationCustodyIntegrityError("thread contract or custody mode disagrees")
    try:
        thread = ConversationThread(
            conversation_id=record["conversation_id"],
            owner_user_id=record["owner_user_id"],
            universe_id=record["universe_id"],
            agent_binding_id=record["agent_binding_id"],
            interlocutor_ref=record["interlocutor_ref"],
            retention_until=record["retention_until"],
            created_at=record["created_at"],
        )
    except (TypeError, ValueError) as exc:
        raise ConversationCustodyIntegrityError("thread record is invalid") from exc
    indexed = (
        row["conversation_id"],
        row["schema_name"],
        row["custody_mode"],
        row["owner_user_id"],
        row["universe_id"],
        row["agent_binding_id"],
        row["interlocutor_ref"],
        row["retention_until"],
        row["created_at"],
    )
    canonical = (
        thread.conversation_id,
        thread.schema,
        thread.custody_mode,
        thread.owner_user_id,
        thread.universe_id,
        thread.agent_binding_id,
        thread.interlocutor_ref,
        thread.retention_until,
        thread.created_at,
    )
    if indexed != canonical or _canonical_record(_thread_record(thread)) != row["record_json"]:
        raise ConversationCustodyIntegrityError("thread indexed columns disagree")
    return thread


def _load_message_row(row: sqlite3.Row) -> ConversationMessage:
    record = _decoded_record(row["record_json"], expected_members=_MESSAGE_MEMBERS)
    try:
        message = ConversationMessage(
            conversation_id=record["conversation_id"],
            message_id=record["message_id"],
            ordinal=record["ordinal"],
            kind=record["kind"],
            participant_ref=record["participant_ref"],
            source_event_ref=record["source_event_ref"],
            payload=record["payload"],
            reply_to_message_id=record["reply_to_message_id"],
            created_at=record["created_at"],
        )
    except (TypeError, ValueError) as exc:
        raise ConversationCustodyIntegrityError("message record is invalid") from exc
    payload_json = canonical_json_bytes(_thaw(message.payload))
    indexed = (
        row["message_id"],
        row["conversation_id"],
        row["ordinal"],
        row["kind"],
        row["participant_ref"],
        row["source_event_ref"],
        row["reply_to_message_id"],
        row["payload_json"],
        row["payload_digest"],
        row["created_at"],
    )
    canonical = (
        message.message_id,
        message.conversation_id,
        message.ordinal,
        message.kind,
        message.participant_ref,
        message.source_event_ref,
        message.reply_to_message_id,
        payload_json,
        message.payload_digest,
        message.created_at,
    )
    if (
        record["payload_digest"] != message.payload_digest
        or indexed != canonical
        or _canonical_record(_message_record(message)) != row["record_json"]
    ):
        raise ConversationCustodyIntegrityError("message indexed columns disagree")
    return message


def _scope_tuple(scope: ConversationCustodyScope) -> tuple[str, str, str]:
    return (scope.owner_user_id, scope.universe_id, scope.agent_binding_id)


def _consume_for_scope(
    grant: ConversationCustodyOperationGrant,
    *,
    action: str,
    request_digest: str,
    key_digest: str | None,
    scope: ConversationCustodyScope,
) -> ConversationCustodyGrantEvidence:
    evidence = consume_operation_grant(
        grant,
        expected_action=action,
        expected_request_digest=request_digest,
        expected_idempotency_key_digest=key_digest,
    )
    if _scope_tuple(scope) != (
        evidence.owner_user_id,
        evidence.universe_id,
        evidence.agent_binding_id,
    ):
        raise ConversationCustodyAuthorizationError(
            "grant_mismatch", "conversation custody grant scope does not match the request"
        )
    return evidence


def _configure(conn: sqlite3.Connection) -> None:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    deadline = time.monotonic() + 30
    while True:
        try:
            current_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
            mode = (
                current_mode
                if current_mode == "wal"
                else str(conn.execute("PRAGMA journal_mode=WAL").fetchone()[0]).lower()
            )
            break
        except sqlite3.OperationalError as exc:
            if "locked" not in str(exc).lower() or time.monotonic() >= deadline:
                raise
            time.sleep(0.01)
    conn.execute("PRAGMA synchronous=FULL")
    conn.execute("PRAGMA secure_delete=ON")
    if mode != "wal":
        raise ConversationCustodyStoreError("conversation custody requires SQLite WAL mode")
    if conn.execute("PRAGMA foreign_keys").fetchone()[0] != 1:
        raise ConversationCustodyStoreError("conversation custody requires foreign keys")
    if conn.execute("PRAGMA secure_delete").fetchone()[0] != 1:
        raise ConversationCustodyStoreError("conversation custody requires secure_delete")


def _open_database(
    evidence: ConversationCustodyGrantEvidence,
) -> tuple[sqlite3.Connection, Path, StorageFileIdentity]:
    before = validate_private_universe_location(evidence)
    conn = sqlite3.connect(str(before.database_path), timeout=30, isolation_level=None)
    try:
        _configure(conn)
        conn.executescript(_SCHEMA)
        after = validate_private_universe_location(
            evidence,
            expected_primary_identity=before.primary_identity,
        )
        if after.primary_identity is None:
            raise ConversationCustodyStoreError("SQLite did not create the custody database")
        conn.execute("BEGIN IMMEDIATE")
        _require_database_universe_binding(conn, evidence)
        _consume_database_grant(conn, evidence)
        validate_private_universe_location(
            evidence,
            expected_primary_identity=after.primary_identity,
        )
        conn.execute("COMMIT")
        return conn, after.database_path, after.primary_identity
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        conn.close()
        raise


def _finish_transaction(
    conn: sqlite3.Connection,
    evidence: ConversationCustodyGrantEvidence,
    identity: StorageFileIdentity,
) -> None:
    validate_private_universe_location(evidence, expected_primary_identity=identity)
    conn.execute("COMMIT")


def _require_database_universe_binding(
    conn: sqlite3.Connection,
    evidence: ConversationCustodyGrantEvidence,
) -> None:
    binding = conn.execute(
        """
        SELECT singleton_id, universe_id
        FROM conversation_custody_database_binding
        WHERE singleton_id = 1
        """
    ).fetchone()
    if binding is not None:
        if binding["singleton_id"] != 1 or binding["universe_id"] != evidence.universe_id:
            raise ConversationCustodyAuthorizationError(
                "storage_universe_mismatch",
                "custody database belongs to another command center",
            )
        return

    persisted_universes: set[str] = set()
    thread_rows = conn.execute("SELECT * FROM conversation_custody_threads").fetchall()
    for row in thread_rows:
        persisted_universes.add(_load_thread_row(row).universe_id)
    deletion_rows = conn.execute("SELECT * FROM conversation_custody_deletions").fetchall()
    for row in deletion_rows:
        receipt = _load_deletion_receipt(row)
        if receipt is None:
            raise ConversationCustodyIntegrityError(
                "pending legacy deletion cannot establish database ownership"
            )
        persisted_universes.add(receipt.universe_id)
    if len(persisted_universes) > 1:
        raise ConversationCustodyIntegrityError("custody database contains more than one "
            "command center")
    if persisted_universes and next(iter(persisted_universes)) != evidence.universe_id:
        raise ConversationCustodyAuthorizationError(
            "storage_universe_mismatch",
            "custody database belongs to another command center",
        )
    conn.execute(
        """
        INSERT INTO conversation_custody_database_binding (singleton_id, universe_id)
        VALUES (1, ?)
        """,
        (evidence.universe_id,),
    )


def _consume_database_grant(
    conn: sqlite3.Connection,
    evidence: ConversationCustodyGrantEvidence,
) -> None:
    conn.execute(
        "DELETE FROM conversation_custody_consumed_grants WHERE expires_at <= ?",
        (custody_domain._canonical_now(),),
    )
    try:
        conn.execute(
            """
            INSERT INTO conversation_custody_consumed_grants (
                grant_id, authority_key_id, expires_at
            ) VALUES (?, ?, ?)
            """,
            (evidence.grant_id, evidence.authority_key_id, evidence.expires_at),
        )
    except sqlite3.IntegrityError as exc:
        existing = conn.execute(
            "SELECT 1 FROM conversation_custody_consumed_grants WHERE grant_id = ?",
            (evidence.grant_id,),
        ).fetchone()
        if existing is not None:
            raise ConversationCustodyAuthorizationError(
                "grant_consumed", "conversation custody grant was already consumed"
            ) from exc
        raise ConversationCustodyIntegrityError("custody grant ledger rejected admission") from exc


def _validated_snapshot(
    thread: ConversationThread,
    messages: tuple[ConversationMessage, ...],
) -> ConversationSnapshot:
    try:
        return ConversationSnapshot(thread=thread, messages=messages)
    except custody_domain.ConversationCustodyValidationError as exc:
        raise ConversationCustodyIntegrityError(
            "persisted conversation sequence is invalid"
        ) from exc


def _rollback(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        conn.execute("ROLLBACK")


def _new_ref(prefix: str) -> str:
    return f"{prefix}_{secrets.token_urlsafe(24)}"


def _select_deletion(
    conn: sqlite3.Connection,
    target_digest: str,
) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT * FROM conversation_custody_deletions
        WHERE deleted_target_digest = ?
        """,
        (target_digest,),
    ).fetchone()


def _raise_if_deleted(
    conn: sqlite3.Connection,
    scope: ConversationCustodyScope,
    conversation_id: str,
) -> None:
    target = deleted_target_digest(scope, conversation_id=conversation_id)
    if _select_deletion(conn, target) is not None:
        raise ConversationCustodyDeleted("conversation was deleted")


def _checkpoint_truncate(
    conn: sqlite3.Connection,
    evidence: ConversationCustodyGrantEvidence,
    identity: StorageFileIdentity,
) -> None:
    deadline = time.monotonic() + 30
    while True:
        validate_private_universe_location(evidence, expected_primary_identity=identity)
        busy, remaining, _checkpointed = conn.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy == 0 and remaining == 0:
            validate_private_universe_location(evidence, expected_primary_identity=identity)
            return
        if time.monotonic() >= deadline:
            raise ConversationCustodyCleanupPending(
                "logical deletion committed but WAL cleanup is busy"
            )
        time.sleep(0.01)


def _select_thread(conn: sqlite3.Connection, conversation_id: str) -> ConversationThread:
    row = conn.execute(
        "SELECT * FROM conversation_custody_threads WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    if row is None:
        raise ConversationCustodyNotFound("conversation is unavailable")
    return _load_thread_row(row)


def _require_thread_scope(
    thread: ConversationThread,
    scope: ConversationCustodyScope,
) -> None:
    if _scope_tuple(scope) != (
        thread.owner_user_id,
        thread.universe_id,
        thread.agent_binding_id,
    ):
        raise ConversationCustodyNotFound("conversation is unavailable")


def _select_message(conn: sqlite3.Connection, message_id: str) -> ConversationMessage:
    row = conn.execute(
        "SELECT * FROM conversation_custody_messages WHERE message_id = ?",
        (message_id,),
    ).fetchone()
    if row is None:
        raise ConversationCustodyIntegrityError("idempotency result message is missing")
    return _load_message_row(row)


def create_thread(
    grant: ConversationCustodyOperationGrant,
    *,
    scope: ConversationCustodyScope,
    idempotency_key: str,
    interlocutor_ref: str,
    retention_until: str | None,
) -> ConversationThread:
    """Create or replay one immutable thread under exact one-use authority."""

    request_digest = create_thread_request_digest(
        scope,
        interlocutor_ref=interlocutor_ref,
        retention_until=retention_until,
    )
    key_digest = idempotency_key_digest(idempotency_key)
    evidence = _consume_for_scope(
        grant,
        action="create_thread",
        request_digest=request_digest,
        key_digest=key_digest,
        scope=scope,
    )
    conn, _path, identity = _open_database(evidence)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_database_universe_binding(conn, evidence)
        existing = conn.execute(
            """
            SELECT request_digest, conversation_id, result_ref
            FROM conversation_custody_idempotency
            WHERE owner_user_id = ? AND operation_kind = 'create_thread'
              AND idempotency_key_digest = ?
            """,
            (scope.owner_user_id, key_digest),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] is None:
                raise ConversationCustodyDeleted("mutation key belongs to a deleted conversation")
            if existing["request_digest"] != request_digest:
                raise ConversationCustodyConflict(
                    "create_thread idempotency key is bound to different input"
                )
            if existing["conversation_id"] != existing["result_ref"]:
                raise ConversationCustodyIntegrityError("create result identity disagrees")
            thread = _select_thread(conn, existing["conversation_id"])
            _require_thread_scope(thread, scope)
            bound_digest = create_thread_request_digest(
                scope,
                interlocutor_ref=thread.interlocutor_ref,
                retention_until=thread.retention_until,
            )
            if bound_digest != request_digest:
                raise ConversationCustodyIntegrityError("create request ledger disagrees")
            _finish_transaction(conn, evidence, identity)
            return thread

        created_at = custody_domain._canonical_now()
        thread = ConversationThread(
            conversation_id=_new_ref("conversation"),
            owner_user_id=scope.owner_user_id,
            universe_id=scope.universe_id,
            agent_binding_id=scope.agent_binding_id,
            interlocutor_ref=interlocutor_ref,
            retention_until=retention_until,
            created_at=created_at,
        )
        conn.execute(
            """
            INSERT INTO conversation_custody_threads (
                conversation_id, schema_name, custody_mode, owner_user_id,
                universe_id, agent_binding_id, interlocutor_ref,
                retention_until, created_at, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                thread.conversation_id,
                thread.schema,
                thread.custody_mode,
                thread.owner_user_id,
                thread.universe_id,
                thread.agent_binding_id,
                thread.interlocutor_ref,
                thread.retention_until,
                thread.created_at,
                _canonical_record(_thread_record(thread)),
            ),
        )
        conn.execute(
            """
            INSERT INTO conversation_custody_idempotency (
                owner_user_id, operation_kind, idempotency_key_digest,
                request_digest, conversation_id, result_ref
            ) VALUES (?, 'create_thread', ?, ?, ?, ?)
            """,
            (
                scope.owner_user_id,
                key_digest,
                request_digest,
                thread.conversation_id,
                thread.conversation_id,
            ),
        )
        _finish_transaction(conn, evidence, identity)
        return thread
    except BaseException:
        _rollback(conn)
        raise
    finally:
        conn.close()


def append_message(
    grant: ConversationCustodyOperationGrant,
    *,
    scope: ConversationCustodyScope,
    idempotency_key: str,
    conversation_id: str,
    kind: str,
    participant_ref: str,
    source_event_ref: str,
    payload: dict[str, object],
    reply_to_message_id: str | None,
) -> ConversationMessage:
    """Append or replay one identified message with a contiguous ordinal."""

    request_digest = append_message_request_digest(
        scope,
        conversation_id=conversation_id,
        kind=kind,
        participant_ref=participant_ref,
        source_event_ref=source_event_ref,
        payload=payload,
        reply_to_message_id=reply_to_message_id,
    )
    key_digest = idempotency_key_digest(idempotency_key)
    evidence = _consume_for_scope(
        grant,
        action="append_message",
        request_digest=request_digest,
        key_digest=key_digest,
        scope=scope,
    )
    conn, _path, identity = _open_database(evidence)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_database_universe_binding(conn, evidence)
        existing = conn.execute(
            """
            SELECT request_digest, conversation_id, result_ref
            FROM conversation_custody_idempotency
            WHERE owner_user_id = ? AND operation_kind = 'append_message'
              AND idempotency_key_digest = ?
            """,
            (scope.owner_user_id, key_digest),
        ).fetchone()
        if existing is not None:
            if existing["request_digest"] is None:
                raise ConversationCustodyDeleted("mutation key belongs to a deleted conversation")
            if existing["request_digest"] != request_digest:
                raise ConversationCustodyConflict(
                    "append_message idempotency key is bound to different input"
                )
            if existing["conversation_id"] != conversation_id:
                raise ConversationCustodyIntegrityError("append conversation identity disagrees")
            message = _select_message(conn, existing["result_ref"])
            if message.conversation_id != conversation_id:
                raise ConversationCustodyIntegrityError("append result identity disagrees")
            thread = _select_thread(conn, message.conversation_id)
            _require_thread_scope(thread, scope)
            bound_digest = append_message_request_digest(
                scope,
                conversation_id=message.conversation_id,
                kind=message.kind,
                participant_ref=message.participant_ref,
                source_event_ref=message.source_event_ref,
                payload=_thaw(message.payload),
                reply_to_message_id=message.reply_to_message_id,
            )
            if bound_digest != request_digest:
                raise ConversationCustodyIntegrityError("append request ledger disagrees")
            if message.reply_to_message_id is not None:
                target_row = conn.execute(
                    """
                    SELECT * FROM conversation_custody_messages
                    WHERE message_id = ? AND conversation_id = ? AND ordinal < ?
                    """,
                    (
                        message.reply_to_message_id,
                        message.conversation_id,
                        message.ordinal,
                    ),
                ).fetchone()
                if target_row is None:
                    raise ConversationCustodyIntegrityError(
                        "persisted append reply target is missing or not earlier"
                    )
                _load_message_row(target_row)
            _finish_transaction(conn, evidence, identity)
            return message

        _raise_if_deleted(conn, scope, conversation_id)
        thread = _select_thread(conn, conversation_id)
        _require_thread_scope(thread, scope)
        next_ordinal = conn.execute(
            """
            SELECT COALESCE(MAX(ordinal), 0) + 1
            FROM conversation_custody_messages
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()[0]
        if reply_to_message_id is not None:
            target_row = conn.execute(
                """
                SELECT * FROM conversation_custody_messages
                WHERE message_id = ? AND conversation_id = ? AND ordinal < ?
                """,
                (reply_to_message_id, conversation_id, next_ordinal),
            ).fetchone()
            if target_row is None:
                raise ConversationCustodyReplyError(
                    "reply target must be an earlier message in this conversation"
                )
            _load_message_row(target_row)

        created_at = custody_domain._canonical_now()
        message = ConversationMessage(
            conversation_id=conversation_id,
            message_id=_new_ref("message"),
            ordinal=next_ordinal,
            kind=kind,
            participant_ref=participant_ref,
            source_event_ref=source_event_ref,
            payload=payload,
            reply_to_message_id=reply_to_message_id,
            created_at=created_at,
        )
        payload_json = canonical_json_bytes(payload)
        conn.execute(
            """
            INSERT INTO conversation_custody_messages (
                message_id, conversation_id, ordinal, kind, participant_ref,
                source_event_ref, reply_to_message_id, payload_json,
                payload_digest, created_at, record_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                message.message_id,
                message.conversation_id,
                message.ordinal,
                message.kind,
                message.participant_ref,
                message.source_event_ref,
                message.reply_to_message_id,
                payload_json,
                message.payload_digest,
                message.created_at,
                _canonical_record(_message_record(message)),
            ),
        )
        conn.execute(
            """
            INSERT INTO conversation_custody_idempotency (
                owner_user_id, operation_kind, idempotency_key_digest,
                request_digest, conversation_id, result_ref
            ) VALUES (?, 'append_message', ?, ?, ?, ?)
            """,
            (
                scope.owner_user_id,
                key_digest,
                request_digest,
                conversation_id,
                message.message_id,
            ),
        )
        _finish_transaction(conn, evidence, identity)
        return message
    except BaseException:
        _rollback(conn)
        raise
    finally:
        conn.close()


def read_thread(
    grant: ConversationCustodyOperationGrant,
    *,
    scope: ConversationCustodyScope,
    conversation_id: str,
) -> ConversationSnapshot:
    """Read one complete integrity-checked thread under exact authority."""

    request_digest = thread_request_digest(
        "read_thread",
        scope,
        conversation_id=conversation_id,
    )
    evidence = _consume_for_scope(
        grant,
        action="read_thread",
        request_digest=request_digest,
        key_digest=None,
        scope=scope,
    )
    conn, _path, identity = _open_database(evidence)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_database_universe_binding(conn, evidence)
        _raise_if_deleted(conn, scope, conversation_id)
        thread = _select_thread(conn, conversation_id)
        _require_thread_scope(thread, scope)
        rows = conn.execute(
            """
            SELECT * FROM conversation_custody_messages
            WHERE conversation_id = ?
            ORDER BY ordinal ASC
            """,
            (conversation_id,),
        ).fetchall()
        messages = tuple(_load_message_row(row) for row in rows)
        snapshot = _validated_snapshot(thread, messages)
        _finish_transaction(conn, evidence, identity)
        return snapshot
    except BaseException:
        _rollback(conn)
        raise
    finally:
        conn.close()


def export_thread(
    grant: ConversationCustodyOperationGrant,
    *,
    scope: ConversationCustodyScope,
    conversation_id: str,
) -> ConversationExport:
    """Export one complete thread as deterministic private canonical bytes."""

    request_digest = thread_request_digest(
        "export_thread",
        scope,
        conversation_id=conversation_id,
    )
    evidence = _consume_for_scope(
        grant,
        action="export_thread",
        request_digest=request_digest,
        key_digest=None,
        scope=scope,
    )
    conn, _path, identity = _open_database(evidence)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_database_universe_binding(conn, evidence)
        _raise_if_deleted(conn, scope, conversation_id)
        thread = _select_thread(conn, conversation_id)
        _require_thread_scope(thread, scope)
        rows = conn.execute(
            """
            SELECT * FROM conversation_custody_messages
            WHERE conversation_id = ?
            ORDER BY ordinal ASC
            """,
            (conversation_id,),
        ).fetchall()
        messages = tuple(_load_message_row(row) for row in rows)
        snapshot = _validated_snapshot(thread, messages)
        exported = export_conversation(snapshot.thread, snapshot.messages)
        _finish_transaction(conn, evidence, identity)
        return exported
    except BaseException:
        _rollback(conn)
        raise
    finally:
        conn.close()


def _validate_deletion_scope_row(
    row: sqlite3.Row,
    scope: ConversationCustodyScope,
    conversation_id: str,
) -> None:
    indexed = (
        row["conversation_id"],
        row["schema_name"],
        row["custody_mode"],
        row["owner_user_id"],
        row["universe_id"],
        row["agent_binding_id"],
    )
    expected = (
        conversation_id,
        CONVERSATION_CUSTODY_SCHEMA,
        PRIVATE_UNIVERSE_MODE,
        scope.owner_user_id,
        scope.universe_id,
        scope.agent_binding_id,
    )
    if indexed != expected:
        raise ConversationCustodyIntegrityError("conversation deletion scope columns disagree")


def _finalize_deletion(
    conn: sqlite3.Connection,
    evidence: ConversationCustodyGrantEvidence,
    identity: StorageFileIdentity,
    *,
    target_digest: str,
) -> ConversationDeletionReceipt:
    _checkpoint_truncate(conn, evidence, identity)
    conn.execute("BEGIN IMMEDIATE")
    try:
        _require_database_universe_binding(conn, evidence)
        row = _select_deletion(conn, target_digest)
        if row is None:
            raise ConversationCustodyIntegrityError("deletion intent is missing")
        receipt = _load_deletion_receipt(row)
        if receipt is None:
            cleanup_completed_at = custody_domain._canonical_now()
            receipt = ConversationDeletionReceipt(
                owner_user_id=row["owner_user_id"],
                universe_id=row["universe_id"],
                agent_binding_id=row["agent_binding_id"],
                conversation_id=row["conversation_id"],
                reason=row["reason"],
                logical_deleted_at=row["logical_deleted_at"],
                cleanup_completed_at=cleanup_completed_at,
                deleted_message_count=row["deleted_message_count"],
            )
            conn.execute(
                """
                UPDATE conversation_custody_deletions
                SET cleanup_completed_at = ?, receipt_json = ?
                WHERE deleted_target_digest = ? AND cleanup_completed_at IS NULL
                """,
                (
                    cleanup_completed_at,
                    _canonical_record(_receipt_record(receipt)),
                    target_digest,
                ),
            )
        _finish_transaction(conn, evidence, identity)
    except BaseException:
        _rollback(conn)
        raise
    _checkpoint_truncate(conn, evidence, identity)
    final_row = _select_deletion(conn, target_digest)
    if final_row is None:
        raise ConversationCustodyIntegrityError("completed deletion is missing")
    final_receipt = _load_deletion_receipt(final_row)
    if final_receipt is None:
        raise ConversationCustodyCleanupPending("deletion cleanup is still pending")
    return final_receipt


def delete_thread(
    grant: ConversationCustodyOperationGrant,
    *,
    scope: ConversationCustodyScope,
    idempotency_key: str,
    conversation_id: str,
    reason: str,
) -> ConversationDeletionReceipt:
    """Logically delete, clean, and return one content-free immutable receipt."""

    request_digest = delete_thread_request_digest(
        scope,
        conversation_id=conversation_id,
        reason=reason,
    )
    key_digest = idempotency_key_digest(idempotency_key)
    target_digest = deleted_target_digest(scope, conversation_id=conversation_id)
    evidence = _consume_for_scope(
        grant,
        action="delete_thread",
        request_digest=request_digest,
        key_digest=key_digest,
        scope=scope,
    )
    conn, _path, identity = _open_database(evidence)
    try:
        conn.execute("BEGIN IMMEDIATE")
        _require_database_universe_binding(conn, evidence)
        logical_deleted_at = custody_domain._canonical_now()
        observed_at = _parsed_timestamp(logical_deleted_at, "system_now")
        key_row = conn.execute(
            """
            SELECT request_digest, deleted_target_digest
            FROM conversation_custody_idempotency
            WHERE owner_user_id = ? AND operation_kind = 'delete_thread'
              AND idempotency_key_digest = ?
            """,
            (scope.owner_user_id, key_digest),
        ).fetchone()
        deletion_row = _select_deletion(conn, target_digest)
        if key_row is not None:
            if (
                key_row["request_digest"] != request_digest
                or key_row["deleted_target_digest"] != target_digest
            ):
                raise ConversationCustodyConflict(
                    "delete_thread idempotency key is bound to different input"
                )
            if deletion_row is None:
                raise ConversationCustodyIntegrityError("delete idempotency target is missing")
            if deletion_row["reason"] != reason:
                raise ConversationCustodyIntegrityError("delete reason disagrees")
        elif deletion_row is not None:
            if deletion_row["reason"] != reason:
                raise ConversationCustodyConflict(
                    "conversation deletion target is bound to a different reason"
                )
            conn.execute(
                """
                INSERT INTO conversation_custody_idempotency (
                    owner_user_id, operation_kind, idempotency_key_digest,
                    request_digest, deleted_target_digest
                ) VALUES (?, 'delete_thread', ?, ?, ?)
                """,
                (scope.owner_user_id, key_digest, request_digest, target_digest),
            )
        else:
            thread_row = conn.execute(
                """
                SELECT * FROM conversation_custody_threads
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()
            if thread_row is None:
                raise ConversationCustodyNotFound("conversation is unavailable")
            _validate_deletion_scope_row(thread_row, scope, conversation_id)
            if reason == "retention_expired":
                canonical_thread = _load_thread_row(thread_row)
                _require_thread_scope(canonical_thread, scope)
                retention_until = canonical_thread.retention_until
                if retention_until is None:
                    raise ConversationCustodyRetentionError(
                        "conversation has no retention-expiry boundary"
                    )
                try:
                    retained_until = _parsed_timestamp(retention_until, "retention_until")
                except ValueError as exc:
                    raise ConversationCustodyIntegrityError(
                        "stored retention boundary is invalid"
                    ) from exc
                if observed_at < retained_until:
                    raise ConversationCustodyRetentionError(
                        "stored retention boundary has not passed"
                    )
            deleted_message_count = conn.execute(
                """
                SELECT COUNT(*) FROM conversation_custody_messages
                WHERE conversation_id = ?
                """,
                (conversation_id,),
            ).fetchone()[0]
            conn.execute(
                """
                INSERT INTO conversation_custody_deletions (
                    deleted_target_digest, owner_user_id, universe_id,
                    agent_binding_id, conversation_id, reason,
                    logical_deleted_at, cleanup_completed_at,
                    deleted_message_count, deletion_scope,
                    historical_backup_caveat, receipt_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, NULL, ?, ?, ?, NULL)
                """,
                (
                    target_digest,
                    scope.owner_user_id,
                    scope.universe_id,
                    scope.agent_binding_id,
                    conversation_id,
                    reason,
                    logical_deleted_at,
                    deleted_message_count,
                    ACTIVE_SQLITE_DELETION_SCOPE,
                    HISTORICAL_BACKUP_CAVEAT,
                ),
            )
            conn.execute(
                """
                UPDATE conversation_custody_idempotency
                SET request_digest = NULL, conversation_id = NULL,
                    result_ref = NULL, deleted_target_digest = NULL
                WHERE operation_kind IN ('create_thread', 'append_message')
                  AND conversation_id = ?
                """,
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM conversation_custody_messages WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute(
                "DELETE FROM conversation_custody_threads WHERE conversation_id = ?",
                (conversation_id,),
            )
            conn.execute(
                """
                INSERT INTO conversation_custody_idempotency (
                    owner_user_id, operation_kind, idempotency_key_digest,
                    request_digest, deleted_target_digest
                ) VALUES (?, 'delete_thread', ?, ?, ?)
                """,
                (scope.owner_user_id, key_digest, request_digest, target_digest),
            )
        _finish_transaction(conn, evidence, identity)
        return _finalize_deletion(
            conn,
            evidence,
            identity,
            target_digest=target_digest,
        )
    except BaseException:
        _rollback(conn)
        raise
    finally:
        conn.close()


__all__ = [
    "ConversationCustodyCleanupPending",
    "ConversationCustodyConflict",
    "ConversationCustodyDeleted",
    "ConversationCustodyIntegrityError",
    "ConversationCustodyNotFound",
    "ConversationCustodyReplyError",
    "ConversationCustodyRetentionError",
    "ConversationCustodyStoreError",
    "append_message",
    "create_thread",
    "delete_thread",
    "export_thread",
    "read_thread",
]
