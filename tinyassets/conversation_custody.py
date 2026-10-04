"""Dark private-conversation custody contracts.

This module has no signing key, issuer, app ingress, provider call, effect, or
MCP surface. A future authenticated app owner must sign the grants consumed
here; this runtime holds public verification material only.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import stat
import threading
import unicodedata
import weakref
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from functools import cache
from pathlib import Path
from types import MappingProxyType

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

PRIVATE_UNIVERSE_MODE = "private_universe"
CONVERSATION_CUSTODY_SCHEMA = "conversation-custody/v1"
CANONICAL_JSON_SCHEMA = "tinyassets-canonical-json/v1"
ACTIVE_SQLITE_DELETION_SCOPE = "active_private_universe_sqlite"
HISTORICAL_BACKUP_CAVEAT = (
    "historical backups, snapshots, media remanence, and external copies follow "
    "separate retention and deletion policies"
)

_ACTIONS = frozenset(
    {"create_thread", "append_message", "read_thread", "export_thread", "delete_thread"}
)
_MUTATIONS = frozenset({"create_thread", "append_message", "delete_thread"})
_REF = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,255}$")
_KIND = re.compile(r"^[a-z][a-z0-9_.:-]{0,63}$")
_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
_IDEMPOTENCY_KEY = re.compile(r"^ik_[A-Za-z0-9_-]{43}$")
_GRANT_ID = re.compile(r"^cg_[A-Za-z0-9_-]{43}$")
_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{6}Z$")
_GRANT_SIGNING_DOMAIN = b"conversation-custody/operation-grant/v1\0"
_MAX_GRANT_LIFETIME_SECONDS = 5 * 60

MAX_CANONICAL_DEPTH = 16
MAX_CANONICAL_MAPPING_MEMBERS = 128
MAX_CANONICAL_LIST_ITEMS = 256
MAX_CANONICAL_NODES = 4_096
MAX_CANONICAL_KEY_BYTES = 256
MAX_CANONICAL_STRING_BYTES = 32_768
MAX_CANONICAL_PAYLOAD_BYTES = 65_536


class ConversationCustodyValidationError(ValueError):
    """A custody contract value is malformed or unsupported."""


class ConversationCustodyAuthorizationError(PermissionError):
    """A custody operation lacks current exact authority."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code

    def __reduce__(self):
        # A worker process's refusal must reach the parent as itself, not as
        # a BrokenProcessPool that hides which authority check failed.
        return type(self), (self.code, str(self))


def _required_ref(value: object, name: str) -> str:
    if not isinstance(value, str) or _REF.fullmatch(value) is None:
        raise ConversationCustodyValidationError(f"{name} is not a canonical internal ref")
    return value


def _required_digest(value: object, name: str) -> str:
    if not isinstance(value, str) or _DIGEST.fullmatch(value) is None:
        raise ConversationCustodyValidationError(f"{name} is not a canonical sha256 digest")
    return value


def _parsed_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or _TIMESTAMP.fullmatch(value) is None:
        raise ConversationCustodyValidationError(f"{name} must use YYYY-MM-DDTHH:MM:SS.ffffffZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)
    except ValueError as exc:
        raise ConversationCustodyValidationError(f"{name} is not a valid UTC time") from exc
    if parsed.second >= 60:
        raise ConversationCustodyValidationError(f"{name} cannot contain a leap second")
    return parsed


def _utc_now() -> datetime:
    """Return the trusted process clock; tests replace this internal source."""

    return datetime.now(timezone.utc)


def _canonical_now() -> str:
    observed = _utc_now()
    if observed.tzinfo is None or observed.utcoffset() != timezone.utc.utcoffset(observed):
        raise RuntimeError("conversation custody clock must be UTC-aware")
    return observed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _normalized_scalar(value: str, name: str, *, maximum_bytes: int) -> str:
    if any(0xD800 <= ord(character) <= 0xDFFF for character in value):
        raise ConversationCustodyValidationError(f"{name} contains a surrogate code point")
    if unicodedata.normalize("NFC", value) != value:
        raise ConversationCustodyValidationError(f"{name} must already be NFC normalized")
    try:
        size = len(value.encode("utf-8"))
    except UnicodeEncodeError as exc:
        raise ConversationCustodyValidationError(f"{name} is not Unicode scalar text") from exc
    if size > maximum_bytes:
        raise ConversationCustodyValidationError(f"{name} exceeds {maximum_bytes} UTF-8 bytes")
    return value


def _normalized_json(value: object, *, depth: int, nodes: list[int], name: str) -> object:
    if depth > MAX_CANONICAL_DEPTH:
        raise ConversationCustodyValidationError("canonical JSON exceeds depth 16")
    nodes[0] += 1
    if nodes[0] > MAX_CANONICAL_NODES:
        raise ConversationCustodyValidationError("canonical JSON exceeds 4096 value nodes")
    if value is None or type(value) is bool:
        return value
    if type(value) is int:
        if value < -(2**63) or value > 2**63 - 1:
            raise ConversationCustodyValidationError("canonical JSON integer exceeds int64")
        return value
    if type(value) is str:
        return _normalized_scalar(
            value,
            name,
            maximum_bytes=MAX_CANONICAL_STRING_BYTES,
        )
    if type(value) is list:
        if len(value) > MAX_CANONICAL_LIST_ITEMS:
            raise ConversationCustodyValidationError("canonical JSON list exceeds 256 items")
        return [
            _normalized_json(child, depth=depth + 1, nodes=nodes, name=f"{name}[{index}]")
            for index, child in enumerate(value)
        ]
    if type(value) is dict:
        if len(value) > MAX_CANONICAL_MAPPING_MEMBERS:
            raise ConversationCustodyValidationError("canonical JSON mapping exceeds 128 members")
        normalized: dict[str, object] = {}
        for key, child in value.items():
            if type(key) is not str:
                raise ConversationCustodyValidationError("canonical JSON keys must be strings")
            normalized_key = _normalized_scalar(
                key,
                f"{name}.key",
                maximum_bytes=MAX_CANONICAL_KEY_BYTES,
            )
            normalized[normalized_key] = _normalized_json(
                child,
                depth=depth + 1,
                nodes=nodes,
                name=f"{name}.{normalized_key}",
            )
        return normalized
    raise ConversationCustodyValidationError(
        "canonical JSON accepts only null, bool, int64, NFC strings, lists, and mappings"
    )


def canonical_json_bytes(payload: object) -> bytes:
    """Return exact ``tinyassets-canonical-json/v1`` bytes for one mapping."""

    if type(payload) is not dict:
        raise ConversationCustodyValidationError("canonical JSON root must be a mapping")
    normalized = _normalized_json(payload, depth=0, nodes=[0], name="payload")
    encoded = _encode_canonical_json(normalized)
    if len(encoded) > MAX_CANONICAL_PAYLOAD_BYTES:
        raise ConversationCustodyValidationError("canonical JSON exceeds 65536 bytes")
    return encoded


def _encode_canonical_json(normalized: object) -> bytes:
    return json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def canonical_json_digest(payload: object) -> str:
    return f"sha256:{hashlib.sha256(canonical_json_bytes(payload)).hexdigest()}"


def idempotency_key_digest(key: object) -> str:
    if not isinstance(key, str) or _IDEMPOTENCY_KEY.fullmatch(key) is None:
        raise ConversationCustodyValidationError("idempotency key wire form is invalid")
    suffix = key[3:]
    try:
        decoded = base64.urlsafe_b64decode(suffix + "=")
    except (ValueError, UnicodeEncodeError) as exc:
        raise ConversationCustodyValidationError("idempotency key is invalid base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if len(decoded) != 32 or not hmac.compare_digest(canonical, suffix):
        raise ConversationCustodyValidationError("idempotency key is not canonical base64url")
    return f"sha256:{hashlib.sha256(key.encode('ascii')).hexdigest()}"


@dataclass(frozen=True, slots=True)
class ConversationCustodyScope:
    owner_user_id: str
    universe_id: str
    agent_binding_id: str

    def __post_init__(self) -> None:
        _required_ref(self.owner_user_id, "owner_user_id")
        _required_ref(self.universe_id, "universe_id")
        _required_ref(self.agent_binding_id, "agent_binding_id")


def _scope(scope: object) -> ConversationCustodyScope:
    if type(scope) is not ConversationCustodyScope:
        raise ConversationCustodyValidationError("scope must be ConversationCustodyScope")
    return scope


def _scope_mapping(scope: ConversationCustodyScope) -> dict[str, str]:
    return {
        "agent_binding_id": scope.agent_binding_id,
        "owner_user_id": scope.owner_user_id,
        "universe_id": scope.universe_id,
    }


def create_thread_request_digest(
    scope: ConversationCustodyScope,
    *,
    interlocutor_ref: str,
    retention_until: str | None,
) -> str:
    selected = _scope(scope)
    interlocutor = _required_ref(interlocutor_ref, "interlocutor_ref")
    if retention_until is not None:
        _parsed_timestamp(retention_until, "retention_until")
    return canonical_json_digest(
        {
            **_scope_mapping(selected),
            "custody_mode": PRIVATE_UNIVERSE_MODE,
            "domain": "conversation-custody/create-thread/v1",
            "interlocutor_ref": interlocutor,
            "retention_until": retention_until,
        }
    )


def append_message_request_digest(
    scope: ConversationCustodyScope,
    *,
    conversation_id: str,
    kind: str,
    participant_ref: str,
    source_event_ref: str,
    payload: object,
    reply_to_message_id: str | None,
) -> str:
    selected = _scope(scope)
    conversation = _required_ref(conversation_id, "conversation_id")
    participant = _required_ref(participant_ref, "participant_ref")
    source = _required_ref(source_event_ref, "source_event_ref")
    if not isinstance(kind, str) or _KIND.fullmatch(kind) is None:
        raise ConversationCustodyValidationError("kind is not canonical")
    reply = None
    if reply_to_message_id is not None:
        reply = _required_ref(reply_to_message_id, "reply_to_message_id")
    normalized_payload = json.loads(canonical_json_bytes(payload))
    return canonical_json_digest(
        {
            **_scope_mapping(selected),
            "conversation_id": conversation,
            "domain": "conversation-custody/append-message/v1",
            "kind": kind,
            "participant_ref": participant,
            "payload": normalized_payload,
            "reply_to_message_id": reply,
            "source_event_ref": source,
        }
    )


def thread_request_digest(
    action: str,
    scope: ConversationCustodyScope,
    *,
    conversation_id: str,
) -> str:
    if action not in {"read_thread", "export_thread"}:
        raise ConversationCustodyValidationError(
            "thread action must be read_thread or export_thread"
        )
    selected = _scope(scope)
    conversation = _required_ref(conversation_id, "conversation_id")
    operation = action.removesuffix("_thread").replace("_", "-")
    return canonical_json_digest(
        {
            **_scope_mapping(selected),
            "conversation_id": conversation,
            "domain": f"conversation-custody/{operation}-thread/v1",
        }
    )


def _deleted_target_mapping(
    scope: ConversationCustodyScope,
    conversation_id: str,
) -> dict[str, str]:
    selected = _scope(scope)
    return {
        **_scope_mapping(selected),
        "conversation_id": _required_ref(conversation_id, "conversation_id"),
        "domain": "conversation-custody/deleted-target/v1",
    }


def deleted_target_digest(
    scope: ConversationCustodyScope,
    *,
    conversation_id: str,
) -> str:
    return canonical_json_digest(_deleted_target_mapping(scope, conversation_id))


def delete_thread_request_digest(
    scope: ConversationCustodyScope,
    *,
    conversation_id: str,
    reason: str,
) -> str:
    if reason not in {"owner_request", "retention_expired"}:
        raise ConversationCustodyValidationError("deletion reason is unsupported")
    return canonical_json_digest(
        {
            "deleted_target_digest": deleted_target_digest(
                scope,
                conversation_id=conversation_id,
            ),
            "domain": "conversation-custody/delete-thread/v1",
            "reason": reason,
        }
    )


def _freeze_json(value: object) -> object:
    if isinstance(value, Mapping):
        return MappingProxyType({key: _freeze_json(child) for key, child in value.items()})
    if type(value) is list:
        return tuple(_freeze_json(child) for child in value)
    return value


def _thaw_json(value: object) -> object:
    if isinstance(value, Mapping):
        return {key: _thaw_json(child) for key, child in value.items()}
    if type(value) is tuple:
        return [_thaw_json(child) for child in value]
    return value


@dataclass(frozen=True, slots=True)
class ConversationThread:
    """Immutable identity and scope for one private conversation."""

    conversation_id: str
    owner_user_id: str
    universe_id: str
    agent_binding_id: str
    interlocutor_ref: str
    retention_until: str | None
    created_at: str
    custody_mode: str = field(default=PRIVATE_UNIVERSE_MODE, init=False)
    schema: str = field(default=CONVERSATION_CUSTODY_SCHEMA, init=False)

    def __post_init__(self) -> None:
        _required_ref(self.conversation_id, "conversation_id")
        _required_ref(self.owner_user_id, "owner_user_id")
        _required_ref(self.universe_id, "universe_id")
        _required_ref(self.agent_binding_id, "agent_binding_id")
        _required_ref(self.interlocutor_ref, "interlocutor_ref")
        if self.retention_until is not None:
            _parsed_timestamp(self.retention_until, "retention_until")
        _parsed_timestamp(self.created_at, "created_at")


@dataclass(frozen=True, slots=True)
class ConversationMessage:
    """Immutable append result with a detached canonical payload."""

    conversation_id: str
    message_id: str
    ordinal: int
    kind: str
    participant_ref: str
    source_event_ref: str
    payload: Mapping[str, object]
    reply_to_message_id: str | None
    created_at: str
    payload_digest: str = field(init=False)

    def __post_init__(self) -> None:
        _required_ref(self.conversation_id, "conversation_id")
        _required_ref(self.message_id, "message_id")
        if type(self.ordinal) is not int or self.ordinal < 1:
            raise ConversationCustodyValidationError("ordinal must be an integer >= 1")
        if not isinstance(self.kind, str) or _KIND.fullmatch(self.kind) is None:
            raise ConversationCustodyValidationError("kind is not canonical")
        _required_ref(self.participant_ref, "participant_ref")
        _required_ref(self.source_event_ref, "source_event_ref")
        if self.reply_to_message_id is not None:
            _required_ref(self.reply_to_message_id, "reply_to_message_id")
        _parsed_timestamp(self.created_at, "created_at")
        payload_bytes = canonical_json_bytes(self.payload)
        normalized_payload = json.loads(payload_bytes)
        object.__setattr__(self, "payload", _freeze_json(normalized_payload))
        object.__setattr__(
            self,
            "payload_digest",
            f"sha256:{hashlib.sha256(payload_bytes).hexdigest()}",
        )


@dataclass(frozen=True, slots=True)
class ConversationExport:
    """Deterministic private export bytes plus their detached digest."""

    content: bytes
    digest: str


@dataclass(frozen=True, slots=True)
class ConversationSnapshot:
    """One integrity-checked thread and its complete ordered messages."""

    thread: ConversationThread
    messages: tuple[ConversationMessage, ...]

    def __post_init__(self) -> None:
        if type(self.messages) is not tuple:
            raise ConversationCustodyValidationError("snapshot messages must be a tuple")
        export_conversation(self.thread, self.messages)


@dataclass(frozen=True, slots=True)
class ConversationDeletionReceipt:
    """Content-free proof that active private-universe cleanup completed."""

    owner_user_id: str
    universe_id: str
    agent_binding_id: str
    conversation_id: str
    reason: str
    logical_deleted_at: str
    cleanup_completed_at: str
    deleted_message_count: int
    deletion_scope: str = field(default=ACTIVE_SQLITE_DELETION_SCOPE, init=False)
    historical_backup_caveat: str = field(default=HISTORICAL_BACKUP_CAVEAT, init=False)

    def __post_init__(self) -> None:
        _required_ref(self.owner_user_id, "owner_user_id")
        _required_ref(self.universe_id, "universe_id")
        _required_ref(self.agent_binding_id, "agent_binding_id")
        _required_ref(self.conversation_id, "conversation_id")
        if self.reason not in {"owner_request", "retention_expired"}:
            raise ConversationCustodyValidationError("deletion reason is unsupported")
        logical = _parsed_timestamp(self.logical_deleted_at, "logical_deleted_at")
        completed = _parsed_timestamp(self.cleanup_completed_at, "cleanup_completed_at")
        if completed < logical:
            raise ConversationCustodyValidationError(
                "cleanup_completed_at cannot precede logical_deleted_at"
            )
        if type(self.deleted_message_count) is not int or self.deleted_message_count < 0:
            raise ConversationCustodyValidationError(
                "deleted_message_count must be an integer >= 0"
            )


def _thread_export_mapping(thread: ConversationThread) -> dict[str, object]:
    return {
        "agent_binding_id": thread.agent_binding_id,
        "conversation_id": thread.conversation_id,
        "created_at": thread.created_at,
        "interlocutor_ref": thread.interlocutor_ref,
        "owner_user_id": thread.owner_user_id,
        "retention_until": thread.retention_until,
        "universe_id": thread.universe_id,
    }


def _message_export_mapping(message: ConversationMessage) -> dict[str, object]:
    return {
        "created_at": message.created_at,
        "kind": message.kind,
        "message_id": message.message_id,
        "ordinal": message.ordinal,
        "participant_ref": message.participant_ref,
        "payload": _thaw_json(message.payload),
        "payload_digest": message.payload_digest,
        "reply_to_message_id": message.reply_to_message_id,
        "source_event_ref": message.source_event_ref,
    }


def export_conversation(
    thread: ConversationThread,
    messages: tuple[ConversationMessage, ...] | list[ConversationMessage],
) -> ConversationExport:
    """Build the exact deterministic export envelope for an intact thread."""

    if type(thread) is not ConversationThread:
        raise ConversationCustodyValidationError("thread must be ConversationThread")
    if type(messages) not in {tuple, list}:
        raise ConversationCustodyValidationError("messages must be an ordered sequence")
    observed_message_ids: set[str] = set()
    envelope_messages: list[dict[str, object]] = []
    for expected_ordinal, message in enumerate(messages, start=1):
        if type(message) is not ConversationMessage:
            raise ConversationCustodyValidationError("message record is invalid")
        if message.conversation_id != thread.conversation_id:
            raise ConversationCustodyValidationError("message belongs to another thread")
        if message.ordinal != expected_ordinal:
            raise ConversationCustodyValidationError("message ordinals are not contiguous")
        if message.message_id in observed_message_ids:
            raise ConversationCustodyValidationError("message identifiers are not unique")
        if (
            message.reply_to_message_id is not None
            and message.reply_to_message_id not in observed_message_ids
        ):
            raise ConversationCustodyValidationError("reply target is not an earlier message")
        observed_message_ids.add(message.message_id)
        envelope_messages.append(_message_export_mapping(message))

    content = _encode_canonical_json(
        {
            "canonical_json": CANONICAL_JSON_SCHEMA,
            "custody_mode": PRIVATE_UNIVERSE_MODE,
            "messages": envelope_messages,
            "schema": CONVERSATION_CUSTODY_SCHEMA,
            "thread": _thread_export_mapping(thread),
        }
    )
    return ConversationExport(
        content=content,
        digest=f"sha256:{hashlib.sha256(content).hexdigest()}",
    )


@dataclass(frozen=True, slots=True)
class ConversationCustodyGrantEvidence:
    """Detached evidence returned by a future authority's live grant check."""

    action: str
    authority_key_id: str
    grant_id: str
    request_digest: str
    idempotency_key_digest: str | None
    owner_user_id: str
    universe_id: str
    agent_binding_id: str
    custody_mode: str
    selection_generation: int
    registered_universe_path: str
    platform_data_root: str
    issued_at: str
    expires_at: str

    def __post_init__(self) -> None:
        if self.action not in _ACTIONS:
            raise ConversationCustodyValidationError("action is unsupported")
        _required_ref(self.authority_key_id, "authority_key_id")
        _required_grant_id(self.grant_id)
        _required_digest(self.request_digest, "request_digest")
        if self.action in _MUTATIONS:
            _required_digest(self.idempotency_key_digest, "idempotency_key_digest")
        elif self.idempotency_key_digest is not None:
            raise ConversationCustodyValidationError(
                "read/export grant idempotency_key_digest must be null"
            )
        _required_ref(self.owner_user_id, "owner_user_id")
        _required_ref(self.universe_id, "universe_id")
        _required_ref(self.agent_binding_id, "agent_binding_id")
        if self.custody_mode != PRIVATE_UNIVERSE_MODE:
            raise ConversationCustodyValidationError("custody_mode is unsupported")
        if (
            isinstance(self.selection_generation, bool)
            or not isinstance(self.selection_generation, int)
            or self.selection_generation < 1
        ):
            raise ConversationCustodyValidationError("selection_generation must be an integer >= 1")
        for value, name in (
            (self.registered_universe_path, "registered_universe_path"),
            (self.platform_data_root, "platform_data_root"),
        ):
            if not isinstance(value, str) or not value or not Path(value).is_absolute():
                raise ConversationCustodyValidationError(f"{name} must be absolute")
        issued = _parsed_timestamp(self.issued_at, "issued_at")
        expires = _parsed_timestamp(self.expires_at, "expires_at")
        if expires <= issued:
            raise ConversationCustodyValidationError("expires_at must be after issued_at")


@dataclass(frozen=True, slots=True)
class StorageFileIdentity:
    device: int
    inode: int


@dataclass(frozen=True, slots=True)
class ConversationCustodyStorageLocation:
    universe_path: Path
    database_path: Path
    primary_identity: StorageFileIdentity | None


def _is_reparse(metadata: os.stat_result) -> bool:
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    attributes = getattr(metadata, "st_file_attributes", 0)
    return bool(attributes & reparse)


def _lstat_no_alias(path: Path, *, expect_directory: bool) -> os.stat_result:
    try:
        metadata = path.lstat()
        if (not expect_directory and stat.S_ISREG(metadata.st_mode)
                and not _is_reparse(metadata) and metadata.st_nlink == 0):
            # SQLite can unlink a sidecar after name lookup but before stat.
            # Preserve the missing-file cause: only optional sidecars may skip it.
            raise FileNotFoundError("storage file was unlinked during stat")
    except OSError as exc:
        raise ConversationCustodyAuthorizationError(
            "storage_location_invalid", "registered custody path is unavailable"
        ) from exc
    valid_kind = (
        stat.S_ISDIR(metadata.st_mode) if expect_directory else stat.S_ISREG(metadata.st_mode)
    )
    if (
        not valid_kind
        or stat.S_ISLNK(metadata.st_mode)
        or _is_reparse(metadata)
        or (not expect_directory and metadata.st_nlink != 1)
    ):
        raise ConversationCustodyAuthorizationError(
            "storage_location_invalid", "registered custody path is aliased or invalid"
        )
    return metadata


def _existing_ancestors(path: Path) -> tuple[Path, ...]:
    return tuple(reversed((path, *path.parents)))


def validate_private_universe_location(
    evidence: ConversationCustodyGrantEvidence,
    *,
    expected_primary_identity: StorageFileIdentity | None = None,
) -> ConversationCustodyStorageLocation:
    """Validate the registered directory and every present SQLite file."""

    universe = Path(evidence.registered_universe_path)
    platform_root = Path(evidence.platform_data_root)
    try:
        universe_resolved = universe.resolve(strict=True)
        platform_resolved = platform_root.resolve(strict=True)
    except OSError as exc:
        raise ConversationCustodyAuthorizationError(
            "storage_location_invalid", "registered custody directory is unavailable"
        ) from exc
    if universe_resolved != universe or universe_resolved == platform_resolved:
        raise ConversationCustodyAuthorizationError(
            "storage_location_invalid", "registered custody directory is not exact"
        )
    for component in _existing_ancestors(universe):
        _lstat_no_alias(component, expect_directory=True)

    database = universe / ".tinyassets.db"
    identity = None
    for candidate in (
        database,
        database.with_name(database.name + "-wal"),
        database.with_name(database.name + "-shm"),
    ):
        if not os.path.lexists(candidate):
            continue
        try:
            metadata = _lstat_no_alias(candidate, expect_directory=False)
        except ConversationCustodyAuthorizationError as exc:
            # Another connection's last close removes the WAL sidecars. One
            # that vanished after lexists is as absent as one never seen.
            if candidate != database and isinstance(exc.__cause__, FileNotFoundError):
                continue
            raise
        if candidate == database:
            identity = StorageFileIdentity(metadata.st_dev, metadata.st_ino)
    if expected_primary_identity is not None and identity != expected_primary_identity:
        raise ConversationCustodyAuthorizationError(
            "storage_location_invalid", "custody database identity changed across open"
        )
    return ConversationCustodyStorageLocation(universe_resolved, database, identity)


class ConversationCustodyOperationGrant:
    __slots__ = ("_grant_id", "_issuer_pid", "__weakref__")

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("conversation custody grants are authority-issued")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("conversation custody grants are immutable")

    def __reduce__(self):
        raise TypeError("conversation custody grants are non-serializable")


@dataclass(frozen=True, slots=True)
class _GrantPayload:
    evidence: ConversationCustodyGrantEvidence
    signature: str


_GRANT_LOCK = threading.Lock()
_GRANTS: dict[
    str,
    tuple[weakref.ReferenceType[ConversationCustodyOperationGrant], _GrantPayload, int],
] = {}


def _reset_grants_after_fork() -> None:
    global _GRANT_LOCK
    global _GRANTS
    _GRANT_LOCK = threading.Lock()
    _GRANTS = {}


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_grants_after_fork)


def _discard_grant(identifier: str, issuer_pid: int) -> None:
    if issuer_pid != os.getpid():
        return
    with _GRANT_LOCK:
        _GRANTS.pop(identifier, None)


def _decode_canonical_base64url(
    value: object,
    *,
    encoded_length: int,
    decoded_length: int,
    name: str,
) -> bytes:
    if (
        not isinstance(value, str)
        or len(value) != encoded_length
        or re.fullmatch(r"[A-Za-z0-9_-]+", value) is None
    ):
        raise ConversationCustodyValidationError(f"{name} wire form is invalid")
    padding = "=" * ((4 - len(value) % 4) % 4)
    try:
        decoded = base64.urlsafe_b64decode(value + padding)
    except (ValueError, UnicodeEncodeError) as exc:
        raise ConversationCustodyValidationError(f"{name} is invalid base64url") from exc
    canonical = base64.urlsafe_b64encode(decoded).decode("ascii").rstrip("=")
    if len(decoded) != decoded_length or not hmac.compare_digest(canonical, value):
        raise ConversationCustodyValidationError(f"{name} is not canonical base64url")
    return decoded


def _required_grant_id(value: object) -> str:
    if not isinstance(value, str) or _GRANT_ID.fullmatch(value) is None:
        raise ConversationCustodyValidationError("grant_id wire form is invalid")
    _decode_canonical_base64url(
        value[3:],
        encoded_length=43,
        decoded_length=32,
        name="grant_id",
    )
    return value


def _operation_grant_record(evidence: ConversationCustodyGrantEvidence) -> dict[str, object]:
    return {
        "action": evidence.action,
        "agent_binding_id": evidence.agent_binding_id,
        "authority_key_id": evidence.authority_key_id,
        "custody_mode": evidence.custody_mode,
        "expires_at": evidence.expires_at,
        "grant_id": evidence.grant_id,
        "idempotency_key_digest": evidence.idempotency_key_digest,
        "issued_at": evidence.issued_at,
        "owner_user_id": evidence.owner_user_id,
        "platform_data_root": evidence.platform_data_root,
        "registered_universe_path": evidence.registered_universe_path,
        "request_digest": evidence.request_digest,
        "selection_generation": evidence.selection_generation,
        "universe_id": evidence.universe_id,
    }


def _operation_grant_signing_bytes(evidence: ConversationCustodyGrantEvidence) -> bytes:
    if type(evidence) is not ConversationCustodyGrantEvidence:
        raise ConversationCustodyValidationError("grant evidence type is invalid")
    return _GRANT_SIGNING_DOMAIN + canonical_json_bytes(_operation_grant_record(evidence))


@cache
def _trusted_authority_public_key() -> tuple[str, Ed25519PublicKey]:
    key_id = os.environ.get("TINYASSETS_CUSTODY_AUTHORITY_KEY_ID")
    public_key_wire = os.environ.get("TINYASSETS_CUSTODY_AUTHORITY_PUBLIC_KEY_B64U")
    try:
        _required_ref(key_id, "configured authority key id")
        raw = _decode_canonical_base64url(
            public_key_wire,
            encoded_length=43,
            decoded_length=32,
            name="configured authority public key",
        )
        public_key = Ed25519PublicKey.from_public_bytes(raw)
    except (ConversationCustodyValidationError, ValueError) as exc:
        raise ConversationCustodyAuthorizationError(
            "grant_verifier_unconfigured",
            "conversation custody authority verifier is unavailable",
        ) from exc
    return key_id, public_key


def _verify_grant_signature(payload: _GrantPayload) -> None:
    evidence = payload.evidence
    trusted_key_id, public_key = _trusted_authority_public_key()
    if evidence.authority_key_id != trusted_key_id:
        raise ConversationCustodyAuthorizationError(
            "grant_signature_invalid", "conversation custody grant key is not trusted"
        )
    try:
        signature = _decode_canonical_base64url(
            payload.signature,
            encoded_length=86,
            decoded_length=64,
            name="grant signature",
        )
        public_key.verify(signature, _operation_grant_signing_bytes(evidence))
    except (ConversationCustodyValidationError, InvalidSignature, ValueError) as exc:
        raise ConversationCustodyAuthorizationError(
            "grant_signature_invalid", "conversation custody grant signature is invalid"
        ) from exc


def consume_operation_grant(
    grant: ConversationCustodyOperationGrant,
    *,
    expected_action: str,
    expected_request_digest: str,
    expected_idempotency_key_digest: str | None,
) -> ConversationCustodyGrantEvidence:
    """Consume one exact grant after request, freshness, live, and path checks."""

    try:
        current_pid = os.getpid()
        exact = (
            type(grant) is ConversationCustodyOperationGrant and grant._issuer_pid == current_pid
        )
    except (AttributeError, TypeError):
        exact = False
    if not exact:
        raise ConversationCustodyAuthorizationError(
            "grant_invalid", "conversation custody requires an authority-issued grant"
        )
    with _GRANT_LOCK:
        entry = _GRANTS.get(grant._grant_id)
        if entry is not None and entry[0]() is grant and entry[2] == current_pid:
            _GRANTS.pop(grant._grant_id, None)
        else:
            entry = None
    if entry is None:
        raise ConversationCustodyAuthorizationError(
            "grant_consumed", "conversation custody grant was already consumed"
        )

    payload = entry[1]
    _verify_grant_signature(payload)
    evidence = payload.evidence
    expected = (expected_action, expected_request_digest, expected_idempotency_key_digest)
    actual = (evidence.action, evidence.request_digest, evidence.idempotency_key_digest)
    if actual != expected:
        raise ConversationCustodyAuthorizationError(
            "grant_mismatch", "conversation custody grant does not match the request"
        )
    observed_at = _parsed_timestamp(_canonical_now(), "system_now")
    issued_at = _parsed_timestamp(evidence.issued_at, "issued_at")
    expires_at = _parsed_timestamp(evidence.expires_at, "expires_at")
    if (expires_at - issued_at).total_seconds() > _MAX_GRANT_LIFETIME_SECONDS:
        raise ConversationCustodyAuthorizationError(
            "grant_lifetime_invalid", "custody grant lifetime exceeds five minutes"
        )
    if observed_at < issued_at:
        raise ConversationCustodyAuthorizationError(
            "grant_not_yet_valid", "custody grant is not valid before its issue time"
        )
    if observed_at >= expires_at:
        raise ConversationCustodyAuthorizationError("grant_expired", "custody grant expired")
    validate_private_universe_location(evidence)
    return evidence


__all__ = [
    "ACTIVE_SQLITE_DELETION_SCOPE",
    "CANONICAL_JSON_SCHEMA",
    "CONVERSATION_CUSTODY_SCHEMA",
    "HISTORICAL_BACKUP_CAVEAT",
    "PRIVATE_UNIVERSE_MODE",
    "ConversationCustodyAuthorizationError",
    "ConversationCustodyScope",
    "ConversationCustodyGrantEvidence",
    "ConversationCustodyOperationGrant",
    "ConversationCustodyStorageLocation",
    "ConversationCustodyValidationError",
    "ConversationDeletionReceipt",
    "ConversationExport",
    "ConversationMessage",
    "ConversationSnapshot",
    "ConversationThread",
    "StorageFileIdentity",
    "append_message_request_digest",
    "canonical_json_bytes",
    "canonical_json_digest",
    "consume_operation_grant",
    "create_thread_request_digest",
    "delete_thread_request_digest",
    "deleted_target_digest",
    "export_conversation",
    "idempotency_key_digest",
    "thread_request_digest",
    "validate_private_universe_location",
]
