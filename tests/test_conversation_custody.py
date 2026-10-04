from __future__ import annotations

import ast
import asyncio
import base64
import importlib
import inspect
import multiprocessing
import os
import pickle
import secrets
import sqlite3
import threading
import weakref
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

_TEST_NOW = "2026-08-03T12:01:00.000000Z"
_TEST_AUTHORITY_KEY_ID = "custody-test-key-1"
_TEST_AUTHORITY_PRIVATE_KEY = Ed25519PrivateKey.from_private_bytes(bytes([71]) * 32)
_TEST_AUTHORITY_PUBLIC_KEY = (
    base64.urlsafe_b64encode(
        _TEST_AUTHORITY_PRIVATE_KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    )
    .decode("ascii")
    .rstrip("=")
)


def _test_clock() -> datetime:
    return datetime.strptime(_TEST_NOW, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=timezone.utc)


def _set_test_now(value: str) -> None:
    global _TEST_NOW
    _TEST_NOW = value


def _install_test_clock(custody, value: str = "2026-08-03T12:01:00.000000Z") -> None:
    _set_test_now(value)
    custody._utc_now = _test_clock  # noqa: SLF001 - test-only trusted clock seam


@pytest.fixture(autouse=True)
def _fixed_custody_clock(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("TINYASSETS_CUSTODY_AUTHORITY_KEY_ID", _TEST_AUTHORITY_KEY_ID)
    monkeypatch.setenv(
        "TINYASSETS_CUSTODY_AUTHORITY_PUBLIC_KEY_B64U",
        _TEST_AUTHORITY_PUBLIC_KEY,
    )
    custody = _custody()
    _set_test_now("2026-08-03T12:01:00.000000Z")
    monkeypatch.setattr(custody, "_utc_now", _test_clock)


def _custody():
    return importlib.import_module("tinyassets.conversation_custody")


def _storage():
    return importlib.import_module("tinyassets.storage.conversation_custody")


def _scope(custody, *, universe_id: str = "universe_1"):
    return custody.ConversationCustodyScope(
        owner_user_id="owner_1",
        universe_id=universe_id,
        agent_binding_id="agent_binding_1",
    )


def _key(value: int) -> str:
    encoded = base64.urlsafe_b64encode(bytes([value]) * 32).decode("ascii").rstrip("=")
    return f"ik_{encoded}"


def _process_refuse(code: str) -> None:
    raise _custody().ConversationCustodyAuthorizationError(code, "refused in a worker")


def _process_create_thread(args: tuple[str, str, str]) -> str:
    root_raw, universe_raw, key = args
    custody = _custody()
    _install_test_clock(custody)
    storage = _storage()
    root = Path(root_raw)
    universe = Path(universe_raw)
    return storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=key,
        ),
        scope=_scope(custody),
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    ).conversation_id


def _process_append_reply(args: tuple[str, str, str, str, str, int]) -> tuple[int, str]:
    root_raw, universe_raw, conversation_id, reply_to_message_id, key, index = args
    custody = _custody()
    _install_test_clock(custody)
    storage = _storage()
    root = Path(root_raw)
    universe = Path(universe_raw)
    payload = {"process": index}
    message = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=conversation_id,
            key=key,
            payload=payload,
            reply_to_message_id=reply_to_message_id,
        ),
        scope=_scope(custody),
        idempotency_key=key,
        conversation_id=conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=payload,
        reply_to_message_id=reply_to_message_id,
    )
    return message.ordinal, message.reply_to_message_id


def _evidence(
    custody,
    root: Path,
    universe: Path,
    *,
    action: str = "read_thread",
    request_digest: str = "sha256:" + "a" * 64,
    key_digest: str | None = None,
    scope=None,
):
    selected_scope = scope or _scope(custody)
    return custody.ConversationCustodyGrantEvidence(
        action=action,
        authority_key_id=_TEST_AUTHORITY_KEY_ID,
        grant_id=f"cg_{base64.urlsafe_b64encode(secrets.token_bytes(32)).decode('ascii').rstrip('=')}",
        request_digest=request_digest,
        idempotency_key_digest=key_digest,
        owner_user_id=selected_scope.owner_user_id,
        universe_id=selected_scope.universe_id,
        agent_binding_id=selected_scope.agent_binding_id,
        custody_mode="private_universe",
        selection_generation=1,
        registered_universe_path=str(universe.resolve()),
        platform_data_root=str(root.resolve()),
        issued_at="2026-08-03T12:00:00.000000Z",
        expires_at="2026-08-03T12:05:00.000000Z",
    )


def _grant(custody, evidence, *, current: bool = True):
    if not current:
        signing_key = Ed25519PrivateKey.from_private_bytes(bytes([72]) * 32)
    else:
        signing_key = _TEST_AUTHORITY_PRIVATE_KEY
    identifier = secrets.token_hex(32)
    issuer_pid = os.getpid()
    grant = object.__new__(custody.ConversationCustodyOperationGrant)
    object.__setattr__(grant, "_grant_id", identifier)
    object.__setattr__(grant, "_issuer_pid", issuer_pid)
    signature = (
        base64.urlsafe_b64encode(
            signing_key.sign(custody._operation_grant_signing_bytes(evidence))  # noqa: SLF001
        )
        .decode("ascii")
        .rstrip("=")
    )
    payload = custody._GrantPayload(  # noqa: SLF001 - explicit test-only registry injection
        evidence,
        signature,
    )
    with custody._GRANT_LOCK:  # noqa: SLF001
        custody._GRANTS[identifier] = (  # noqa: SLF001
            weakref.ref(grant),
            payload,
            issuer_pid,
        )
    weakref.finalize(grant, custody._discard_grant, identifier, issuer_pid)  # noqa: SLF001
    return grant


def test_operation_grant_is_exact_and_single_use(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "data"
    universe = root / "universe_1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    grant = _grant(custody, evidence)

    consumed = custody.consume_operation_grant(
        grant,
        expected_action="read_thread",
        expected_request_digest="sha256:" + "a" * 64,
        expected_idempotency_key_digest=None,
    )

    assert consumed == evidence
    with pytest.raises(custody.ConversationCustodyAuthorizationError) as replay:
        custody.consume_operation_grant(
            grant,
            expected_action="read_thread",
            expected_request_digest="sha256:" + "a" * 64,
            expected_idempotency_key_digest=None,
        )
    assert replay.value.code == "grant_consumed"


def test_mismatched_grant_is_consumed_without_opening_storage(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "data"
    universe = root / "universe_1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    grant = _grant(custody, evidence)

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as mismatch:
        custody.consume_operation_grant(
            grant,
            expected_action="export_thread",
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert mismatch.value.code == "grant_mismatch"
    assert not (universe / ".tinyassets.db").exists()

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as replay:
        custody.consume_operation_grant(
            grant,
            expected_action="read_thread",
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert replay.value.code == "grant_consumed"


@pytest.mark.parametrize(
    ("now", "current", "expected_code"),
    [
        ("2026-08-03T12:05:00.000001Z", True, "grant_expired"),
        ("2026-08-03T12:01:00.000000Z", False, "grant_signature_invalid"),
    ],
)
def test_expired_or_untrusted_grant_fails_closed(
    tmp_path: Path,
    now: str,
    current: bool,
    expected_code: str,
) -> None:
    custody = _custody()
    root = tmp_path / "data"
    universe = root / "universe_1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    _set_test_now(now)

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.consume_operation_grant(
            _grant(custody, evidence, current=current),
            expected_action=evidence.action,
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert blocked.value.code == expected_code


def test_private_registry_injection_cannot_authorize_an_untrusted_signature(
    tmp_path: Path,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.consume_operation_grant(
            _grant(custody, evidence, current=False),
            expected_action=evidence.action,
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert blocked.value.code == "grant_signature_invalid"


def test_operation_grant_refuses_more_than_five_minutes_of_authority(
    tmp_path: Path,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = replace(
        _evidence(custody, root, universe),
        expires_at="2026-08-03T12:05:00.000001Z",
    )

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.consume_operation_grant(
            _grant(custody, evidence),
            expected_action=evidence.action,
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert blocked.value.code == "grant_lifetime_invalid"


def test_operation_grant_wire_rejects_noncanonical_grant_id_and_signature(
    tmp_path: Path,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    with pytest.raises(custody.ConversationCustodyValidationError):
        replace(evidence, grant_id=f"cg_{'A' * 42}B")

    valid_grant = _grant(custody, evidence)
    with custody._GRANT_LOCK:  # noqa: SLF001 - inspect signed test envelope
        valid_payload = custody._GRANTS.pop(valid_grant._grant_id)[1]  # noqa: SLF001
    alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    last_index = alphabet.index(valid_payload.signature[-1])
    noncanonical_signature = valid_payload.signature[:-1] + alphabet[last_index + 1]
    wrapper_id = secrets.token_hex(32)
    issuer_pid = os.getpid()
    invalid_grant = object.__new__(custody.ConversationCustodyOperationGrant)
    object.__setattr__(invalid_grant, "_grant_id", wrapper_id)
    object.__setattr__(invalid_grant, "_issuer_pid", issuer_pid)
    with custody._GRANT_LOCK:  # noqa: SLF001 - malformed signed-envelope probe
        custody._GRANTS[wrapper_id] = (  # noqa: SLF001
            weakref.ref(invalid_grant),
            custody._GrantPayload(evidence, noncanonical_signature),  # noqa: SLF001
            issuer_pid,
        )
    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.consume_operation_grant(
            invalid_grant,
            expected_action=evidence.action,
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert blocked.value.code == "grant_signature_invalid"


def test_grant_cannot_be_consumed_before_its_issue_time(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    _set_test_now("2026-08-03T11:59:59.999999Z")

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.consume_operation_grant(
            _grant(custody, evidence),
            expected_action=evidence.action,
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert blocked.value.code == "grant_not_yet_valid"
    assert not (universe / ".tinyassets.db").exists()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="POSIX fork is unavailable")
def test_grant_cannot_be_consumed_in_fork_child_and_parent(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    grant = _grant(custody, evidence)
    read_fd, write_fd = os.pipe()
    child_pid = os.fork()
    if child_pid == 0:
        os.close(read_fd)
        try:
            custody.consume_operation_grant(
                grant,
                expected_action=evidence.action,
                expected_request_digest=evidence.request_digest,
                expected_idempotency_key_digest=None,
            )
            result = b"accepted"
        except custody.ConversationCustodyAuthorizationError:
            result = b"rejected"
        os.write(write_fd, result)
        os.close(write_fd)
        os._exit(0)

    os.close(write_fd)
    child_result = os.read(read_fd, 32)
    os.close(read_fd)
    _pid, status = os.waitpid(child_pid, 0)
    assert os.waitstatus_to_exitcode(status) == 0
    consumed = custody.consume_operation_grant(
        grant,
        expected_action=evidence.action,
        expected_request_digest=evidence.request_digest,
        expected_idempotency_key_digest=None,
    )
    assert child_result == b"rejected"
    assert consumed == evidence


def test_grant_is_bound_to_issuer_process_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    grant = _grant(custody, evidence)
    issuer_pid = os.getpid()
    with monkeypatch.context() as process:
        process.setattr(custody.os, "getpid", lambda: issuer_pid + 1)
        with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
            custody.consume_operation_grant(
                grant,
                expected_action=evidence.action,
                expected_request_digest=evidence.request_digest,
                expected_idempotency_key_digest=None,
            )
        assert blocked.value.code == "grant_invalid"

    assert (
        custody.consume_operation_grant(
            grant,
            expected_action=evidence.action,
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
        == evidence
    )


def test_read_grant_requires_null_key_and_mutation_requires_digest(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "data"
    universe = root / "universe_1"
    universe.mkdir(parents=True)

    with pytest.raises(custody.ConversationCustodyValidationError):
        _evidence(
            custody,
            root,
            universe,
            action="read_thread",
            key_digest="sha256:" + "b" * 64,
        )
    with pytest.raises(custody.ConversationCustodyValidationError):
        _evidence(custody, root, universe, action="create_thread", key_digest=None)


def test_registered_universe_allows_missing_sqlite_files(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "data"
    universe = root / "universe_1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)

    consumed = custody.consume_operation_grant(
        _grant(custody, evidence),
        expected_action=evidence.action,
        expected_request_digest=evidence.request_digest,
        expected_idempotency_key_digest=None,
    )

    assert consumed.registered_universe_path == str(universe.resolve())
    assert not (universe / ".tinyassets.db").exists()


def test_platform_root_or_missing_universe_is_refused(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "data"
    root.mkdir()

    for universe in (root, root / "missing"):
        evidence = _evidence(custody, root, universe)
        with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
            custody.consume_operation_grant(
                _grant(custody, evidence),
                expected_action=evidence.action,
                expected_request_digest=evidence.request_digest,
                expected_idempotency_key_digest=None,
            )
        assert blocked.value.code == "storage_location_invalid"


def test_hard_linked_database_is_refused(tmp_path: Path) -> None:
    custody = _custody()
    root = tmp_path / "data"
    universe = root / "universe_1"
    universe.mkdir(parents=True)
    outside = tmp_path / "outside.db"
    outside.write_bytes(b"not a custody database")
    try:
        os.link(outside, universe / ".tinyassets.db")
    except OSError as exc:  # pragma: no cover - unsupported filesystem
        pytest.skip(f"hard links unavailable: {exc}")
    evidence = _evidence(custody, root, universe)

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.consume_operation_grant(
            _grant(custody, evidence),
            expected_action=evidence.action,
            expected_request_digest=evidence.request_digest,
            expected_idempotency_key_digest=None,
        )
    assert blocked.value.code == "storage_location_invalid"


def test_existing_database_identity_is_stable_while_sidecars_may_transition(
    tmp_path: Path,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    database = universe / ".tinyassets.db"
    database.write_bytes(b"first")

    initial = custody.validate_private_universe_location(evidence)
    assert initial.primary_identity is not None
    wal = universe / ".tinyassets.db-wal"
    wal.write_bytes(b"transient")
    custody.validate_private_universe_location(
        evidence,
        expected_primary_identity=initial.primary_identity,
    )
    wal.unlink()
    custody.validate_private_universe_location(
        evidence,
        expected_primary_identity=initial.primary_identity,
    )

    original = universe / ".tinyassets.db-original"
    database.replace(original)
    database.write_bytes(b"replacement")
    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.validate_private_universe_location(
            evidence,
            expected_primary_identity=initial.primary_identity,
        )
    assert blocked.value.code == "storage_location_invalid"


@pytest.mark.parametrize("vanishing", [".tinyassets.db-wal", ".tinyassets.db-shm"])
def test_sidecar_removed_between_exists_and_lstat_is_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, vanishing: str,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    (universe / ".tinyassets.db").write_bytes(b"first")
    initial = custody.validate_private_universe_location(evidence)
    sidecar = universe / vanishing
    real_lexists = os.path.lexists
    seen = []

    def lexists(path):
        # Report the sidecar once, as a concurrent close is about to unlink it.
        if Path(path) == sidecar and not seen:
            seen.append(path)
            return True
        return real_lexists(path)

    monkeypatch.setattr(custody.os.path, "lexists", lexists)
    location = custody.validate_private_universe_location(
        evidence,
        expected_primary_identity=initial.primary_identity,
    )
    assert seen and location.primary_identity == initial.primary_identity


def test_database_removed_between_exists_and_lstat_is_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    database = universe / ".tinyassets.db"
    real_lexists = os.path.lexists
    seen = []

    def lexists(path):
        # The primary file vanishing mid-check is a real change, not a close.
        if Path(path) == database and not seen:
            seen.append(path)
            return True
        return real_lexists(path)

    monkeypatch.setattr(custody.os.path, "lexists", lexists)
    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.validate_private_universe_location(evidence)
    assert seen and blocked.value.code == "storage_location_invalid"


def test_unreadable_sidecar_is_still_refused(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    custody = _custody()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    evidence = _evidence(custody, root, universe)
    (universe / ".tinyassets.db").write_bytes(b"first")
    sidecar = universe / ".tinyassets.db-wal"
    sidecar.write_bytes(b"transient")
    real_lstat = Path.lstat

    def lstat(self):
        # Only a sidecar that is gone may be skipped; any other failure refuses.
        if self == sidecar:
            raise PermissionError("denied")
        return real_lstat(self)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        custody.validate_private_universe_location(evidence)
    assert blocked.value.code == "storage_location_invalid"


def test_authorization_error_crosses_a_process_boundary() -> None:
    custody = _custody()
    error = custody.ConversationCustodyAuthorizationError(
        "storage_location_invalid", "registered custody path is unavailable"
    )
    restored = pickle.loads(pickle.dumps(error))
    assert type(restored) is custody.ConversationCustodyAuthorizationError
    assert (restored.code, str(restored)) == (error.code, str(error))

    context = multiprocessing.get_context("spawn")
    with ProcessPoolExecutor(max_workers=1, mp_context=context) as executor:
        future = executor.submit(_process_refuse, "grant_consumed")
        with pytest.raises(custody.ConversationCustodyAuthorizationError) as raised:
            future.result(timeout=60)
    assert raised.value.code == "grant_consumed"


def test_canonical_json_has_exact_bytes_and_preserves_unknown_members() -> None:
    custody = _custody()
    left = {"z": [True, None, -7], "a": {"unknown": "hello\nworld"}}
    right = {"a": {"unknown": "hello\nworld"}, "z": [True, None, -7]}
    expected = b'{"a":{"unknown":"hello\\nworld"},"z":[true,null,-7]}'

    assert custody.canonical_json_bytes(left) == expected
    assert custody.canonical_json_bytes(right) == expected
    assert custody.canonical_json_digest(left) == (
        "sha256:f43b6adc1f8bc1176ac226c62730f59de4d9ed29d68565c090c2735000f7cc9d"
    )


@pytest.mark.parametrize(
    "payload",
    [
        "raw-json-text",
        {"value": 1.5},
        {"value": 2**63},
        {"value": "e\u0301"},
        {"value": "\ud800"},
        {1: "non-string-key"},
        {"k" * 257: "too-long-key"},
        {"value": "x" * 32_769},
        {f"key_{index}": index for index in range(129)},
        {"items": list(range(257))},
        {"left": "x" * 32_768, "right": "y" * 32_768},
    ],
)
def test_canonical_json_rejects_ambiguous_or_oversized_values(payload: object) -> None:
    custody = _custody()

    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.canonical_json_bytes(payload)


def test_canonical_json_depth_counts_root_at_zero() -> None:
    custody = _custody()
    accepted: object = "leaf"
    for _ in range(15):
        accepted = [accepted]
    custody.canonical_json_bytes({"value": accepted})

    rejected: object = [accepted]
    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.canonical_json_bytes({"value": rejected})


def test_canonical_json_node_count_and_escape_boundaries_are_exact() -> None:
    custody = _custody()
    exact_4096 = [[0] * 255 for _ in range(15)] + [[0] * 253]
    custody.canonical_json_bytes({"batches": exact_4096})
    exact_4096[-1].append(0)
    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.canonical_json_bytes({"batches": exact_4096})

    assert (
        custody.canonical_json_bytes({"controls": '"\\\b\t\n\f\r\x00\x1f/é'})
        == '{"controls":"\\"\\\\\\b\\t\\n\\f\\r\\u0000\\u001f/é"}'.encode()
    )


def test_canonical_json_rejects_custom_container_and_scalar_types() -> None:
    custody = _custody()

    class CustomMapping(dict):
        pass

    class CustomString(str):
        pass

    for payload in (CustomMapping(value=1), {"value": CustomString("text")}):
        with pytest.raises(custody.ConversationCustodyValidationError):
            custody.canonical_json_bytes(payload)


def test_idempotency_key_wire_and_digest_are_canonical() -> None:
    custody = _custody()
    key = "ik_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    assert custody.idempotency_key_digest(key) == (
        "sha256:7c02713014568e7c6a23ccce8e98f0d6e165f7f779f274859610460060faf803"
    )
    for invalid in (
        "A" * 43,
        "ik_" + "A" * 42,
        "ik_" + "A" * 42 + "=",
        "ik_" + "A" * 42 + "+",
        "ik_" + "A" * 42 + "B",  # non-canonical trailing pad bits
    ):
        with pytest.raises(custody.ConversationCustodyValidationError):
            custody.idempotency_key_digest(invalid)


def test_operation_request_digest_vectors_match_contract() -> None:
    custody = _custody()
    scope = custody.ConversationCustodyScope(
        owner_user_id="owner_1",
        universe_id="universe_1",
        agent_binding_id="agent_binding_1",
    )

    assert (
        custody.create_thread_request_digest(
            scope,
            interlocutor_ref="interlocutor_1",
            retention_until="2030-01-02T03:04:05.000006Z",
        )
        == "sha256:2e16d89e186ea01130b06c77c544394f1bdc84159d7fd816419acd65826dd78f"
    )
    assert (
        custody.append_message_request_digest(
            scope,
            conversation_id="conversation_1",
            kind="text",
            participant_ref="participant_1",
            source_event_ref="event_1",
            payload={"text": "hello\nworld"},
            reply_to_message_id=None,
        )
        == "sha256:03d0dce3eba96d9efa1c8bf8ab383c90a2724c6c6e4a935201653649805fc3d5"
    )
    assert (
        custody.thread_request_digest("read_thread", scope, conversation_id="conversation_1")
        == "sha256:6b9114c5a4161548e7bca566a340d73f7ddab83c21527f53a375c2c47531b143"
    )
    assert (
        custody.thread_request_digest("export_thread", scope, conversation_id="conversation_1")
        == "sha256:f0d5c9697fbac581b93f42a4c52750388e1cb8c825fe653d52d2e91b902bef42"
    )
    assert custody.deleted_target_digest(scope, conversation_id="conversation_1") == (
        "sha256:1720128239c73ade4c587c137126e013dde5617751676294b5029815154cc1f5"
    )
    assert (
        custody.delete_thread_request_digest(
            scope,
            conversation_id="conversation_1",
            reason="owner_request",
        )
        == "sha256:a326ce1489645ec9083d739e9a27bfb2c88870a63cf7e833877b77a59acb00be"
    )


def test_request_digest_validation_rejects_noncanonical_metadata() -> None:
    custody = _custody()
    scope = custody.ConversationCustodyScope(
        owner_user_id="owner_1",
        universe_id="universe_1",
        agent_binding_id="agent_binding_1",
    )

    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.create_thread_request_digest(
            scope,
            interlocutor_ref="bad ref",
            retention_until=None,
        )
    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.append_message_request_digest(
            scope,
            conversation_id="conversation_1",
            kind="Text",
            participant_ref="participant_1",
            source_event_ref="event_1",
            payload={"text": "hello"},
            reply_to_message_id=None,
        )
    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.delete_thread_request_digest(
            scope,
            conversation_id="conversation_1",
            reason="other",
        )


def test_metadata_ref_kind_and_timestamp_boundaries_are_exact() -> None:
    custody = _custody()
    scope = custody.ConversationCustodyScope(
        owner_user_id="o" + "x" * 255,
        universe_id="universe_1",
        agent_binding_id="agent_binding_1",
    )
    custody.create_thread_request_digest(
        scope,
        interlocutor_ref="i" + "x" * 255,
        retention_until="2030-01-02T03:04:05.000006Z",
    )
    custody.append_message_request_digest(
        scope,
        conversation_id="conversation_1",
        kind="a" + "x" * 63,
        participant_ref="participant_1",
        source_event_ref="event_1",
        payload={},
        reply_to_message_id=None,
    )

    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.ConversationCustodyScope(
            owner_user_id="o" + "x" * 256,
            universe_id="universe_1",
            agent_binding_id="agent_binding_1",
        )
    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.append_message_request_digest(
            scope,
            conversation_id="conversation_1",
            kind="a" + "x" * 64,
            participant_ref="participant_1",
            source_event_ref="event_1",
            payload={},
            reply_to_message_id=None,
        )
    for invalid_time in (
        "2030-01-02T03:04:05Z",
        "2030-01-02T03:04:05.00000Z",
        "2030-01-02T03:04:05.0000000Z",
        "2030-01-02T03:04:05.000006+00:00",
        "2030-01-02T03:04:60.000006Z",
        "2030-02-30T03:04:05.000006Z",
    ):
        with pytest.raises(custody.ConversationCustodyValidationError):
            custody.create_thread_request_digest(
                scope,
                interlocutor_ref="interlocutor_1",
                retention_until=invalid_time,
            )


def _thread(custody):
    return custody.ConversationThread(
        conversation_id="conversation_1",
        owner_user_id="owner_1",
        universe_id="universe_1",
        agent_binding_id="agent_binding_1",
        interlocutor_ref="slack:user_1",
        retention_until="2030-01-02T03:04:05.000006Z",
        created_at="2026-08-03T12:00:00.000001Z",
    )


def _message(custody, *, ordinal: int = 1, reply_to_message_id: str | None = None):
    return custody.ConversationMessage(
        conversation_id="conversation_1",
        message_id=f"message_{ordinal}",
        ordinal=ordinal,
        kind="text",
        participant_ref="slack:user_1" if ordinal == 1 else "agent:agent_binding_1",
        source_event_ref=f"slack:event_{ordinal}",
        payload={"text": "hello" if ordinal == 1 else "hi", "unknown": [1, True]},
        reply_to_message_id=reply_to_message_id,
        created_at=f"2026-08-03T12:00:0{ordinal}.00000{ordinal}Z",
    )


def test_thread_and_message_records_are_immutable_and_payload_is_detached() -> None:
    custody = _custody()
    payload = {"nested": {"items": [1, 2]}}
    message = custody.ConversationMessage(
        conversation_id="conversation_1",
        message_id="message_1",
        ordinal=1,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=payload,
        reply_to_message_id=None,
        created_at="2026-08-03T12:00:01.000001Z",
    )
    payload["nested"]["items"].append(3)

    assert message.payload == {"nested": {"items": (1, 2)}}
    assert message.payload_digest == (
        "sha256:107bb99f37f332725c2b986d24cddf07e3c104989e28673e5132012eb651324a"
    )
    with pytest.raises((AttributeError, TypeError)):
        message.kind = "edited"
    with pytest.raises(TypeError):
        message.payload["nested"] = {}


@pytest.mark.parametrize(
    "factory",
    [
        lambda custody: custody.ConversationThread(
            conversation_id="bad ref",
            owner_user_id="owner_1",
            universe_id="universe_1",
            agent_binding_id="agent_binding_1",
            interlocutor_ref="slack:user_1",
            retention_until=None,
            created_at="2026-08-03T12:00:00.000001Z",
        ),
        lambda custody: custody.ConversationThread(
            conversation_id="conversation_1",
            owner_user_id="owner_1",
            universe_id="universe_1",
            agent_binding_id="agent_binding_1",
            interlocutor_ref="slack:user_1",
            retention_until=None,
            created_at="2026-08-03T12:00:00Z",
        ),
        lambda custody: custody.ConversationMessage(
            conversation_id="conversation_1",
            message_id="message_1",
            ordinal=True,
            kind="Text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload={},
            reply_to_message_id=None,
            created_at="2026-08-03T12:00:01.000001Z",
        ),
    ],
)
def test_records_reject_noncanonical_metadata(factory) -> None:
    custody = _custody()

    with pytest.raises(custody.ConversationCustodyValidationError):
        factory(custody)


def test_export_is_exact_deterministic_and_digest_is_separate() -> None:
    custody = _custody()
    thread = _thread(custody)
    messages = (
        _message(custody),
        _message(custody, ordinal=2, reply_to_message_id="message_1"),
    )

    first = custody.export_conversation(thread, messages)
    second = custody.export_conversation(thread, messages)

    expected = (
        b'{"canonical_json":"tinyassets-canonical-json/v1","custody_mode":"private_universe",'
        b'"messages":[{"created_at":"2026-08-03T12:00:01.000001Z","kind":"text",'
        b'"message_id":"message_1","ordinal":1,"participant_ref":"slack:user_1",'
        b'"payload":{"text":"hello","unknown":[1,true]},'
        b'"payload_digest":"sha256:4f2111c5f527321f6cc054e6c1058ee25cfebf12f7764b248463378e5d577adc",'
        b'"reply_to_message_id":null,"source_event_ref":"slack:event_1"},'
        b'{"created_at":"2026-08-03T12:00:02.000002Z","kind":"text",'
        b'"message_id":"message_2","ordinal":2,"participant_ref":"agent:agent_binding_1",'
        b'"payload":{"text":"hi","unknown":[1,true]},'
        b'"payload_digest":"sha256:9f6e57e293e2f28174b45a7891799e27a90aee41441ff44e7cc63b5945a496de",'
        b'"reply_to_message_id":"message_1","source_event_ref":"slack:event_2"}],'
        b'"schema":"conversation-custody/v1","thread":{"agent_binding_id":"agent_binding_1",'
        b'"conversation_id":"conversation_1","created_at":"2026-08-03T12:00:00.000001Z",'
        b'"interlocutor_ref":"slack:user_1","owner_user_id":"owner_1",'
        b'"retention_until":"2030-01-02T03:04:05.000006Z","universe_id":"universe_1"}}'
    )
    assert first.content == expected
    assert first == second
    assert first.digest == (
        "sha256:53085850ce0e6e7c2e68ed014d0a7a84de115226a00247fef8db0a953e7c1f91"
    )
    assert b'"digest"' not in first.content


@pytest.mark.parametrize(
    "messages",
    [
        (),
        ("wrong-thread",),
        ("gap",),
        ("reply-missing",),
        ("reply-forward",),
    ],
)
def test_export_refuses_incomplete_or_invalid_message_sequences(messages) -> None:
    custody = _custody()
    thread = _thread(custody)
    cases = {
        (): (_message(custody, ordinal=2),),
        ("wrong-thread",): (
            custody.ConversationMessage(
                conversation_id="conversation_2",
                message_id="message_1",
                ordinal=1,
                kind="text",
                participant_ref="slack:user_1",
                source_event_ref="slack:event_1",
                payload={},
                reply_to_message_id=None,
                created_at="2026-08-03T12:00:01.000001Z",
            ),
        ),
        ("gap",): (_message(custody), _message(custody, ordinal=3)),
        ("reply-missing",): (
            _message(custody),
            _message(custody, ordinal=2, reply_to_message_id="message_9"),
        ),
        ("reply-forward",): (
            _message(custody, reply_to_message_id="message_2"),
            _message(custody, ordinal=2),
        ),
    }

    with pytest.raises(custody.ConversationCustodyValidationError):
        custody.export_conversation(thread, cases[messages])


def _create_grant(
    custody,
    root: Path,
    universe: Path,
    *,
    interlocutor_ref: str,
    key: str,
    scope=None,
    retention_until: str | None = None,
):
    selected_scope = scope or _scope(custody)
    request_digest = custody.create_thread_request_digest(
        selected_scope,
        interlocutor_ref=interlocutor_ref,
        retention_until=retention_until,
    )
    return _grant(
        custody,
        _evidence(
            custody,
            root,
            universe,
            action="create_thread",
            request_digest=request_digest,
            key_digest=custody.idempotency_key_digest(key),
            scope=selected_scope,
        ),
    )


def _append_grant(
    custody,
    root: Path,
    universe: Path,
    *,
    conversation_id: str,
    key: str,
    payload: dict[str, object],
    reply_to_message_id: str | None = None,
    scope=None,
    participant_ref: str = "slack:user_1",
    source_event_ref: str = "slack:event_1",
):
    selected_scope = scope or _scope(custody)
    request_digest = custody.append_message_request_digest(
        selected_scope,
        conversation_id=conversation_id,
        kind="text",
        participant_ref=participant_ref,
        source_event_ref=source_event_ref,
        payload=payload,
        reply_to_message_id=reply_to_message_id,
    )
    return _grant(
        custody,
        _evidence(
            custody,
            root,
            universe,
            action="append_message",
            request_digest=request_digest,
            key_digest=custody.idempotency_key_digest(key),
            scope=selected_scope,
        ),
    )


def _read_grant(
    custody,
    root: Path,
    universe: Path,
    *,
    conversation_id: str,
    scope=None,
):
    selected_scope = scope or _scope(custody)
    request_digest = custody.thread_request_digest(
        "read_thread",
        selected_scope,
        conversation_id=conversation_id,
    )
    return _grant(
        custody,
        _evidence(
            custody,
            root,
            universe,
            action="read_thread",
            request_digest=request_digest,
            scope=selected_scope,
        ),
    )


def _export_grant(custody, root: Path, universe: Path, *, conversation_id: str):
    scope = _scope(custody)
    return _grant(
        custody,
        _evidence(
            custody,
            root,
            universe,
            action="export_thread",
            request_digest=custody.thread_request_digest(
                "export_thread",
                scope,
                conversation_id=conversation_id,
            ),
            scope=scope,
        ),
    )


def _delete_grant(
    custody,
    root: Path,
    universe: Path,
    *,
    conversation_id: str,
    reason: str,
    key: str,
):
    scope = _scope(custody)
    return _grant(
        custody,
        _evidence(
            custody,
            root,
            universe,
            action="delete_thread",
            request_digest=custody.delete_thread_request_digest(
                scope,
                conversation_id=conversation_id,
                reason=reason,
            ),
            key_digest=custody.idempotency_key_digest(key),
            scope=scope,
        ),
    )


def test_private_store_creates_replays_and_reads_exact_thread(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    key = "ik_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"

    created = storage.create_thread(
        _create_grant(custody, root, universe, interlocutor_ref="slack:user_1", key=key),
        scope=_scope(custody),
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    replayed = storage.create_thread(
        _create_grant(custody, root, universe, interlocutor_ref="slack:user_1", key=key),
        scope=_scope(custody),
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    snapshot = storage.read_thread(
        _read_grant(custody, root, universe, conversation_id=created.conversation_id),
        scope=_scope(custody),
        conversation_id=created.conversation_id,
    )

    assert created == replayed == snapshot.thread
    assert created.conversation_id.startswith("conversation_")
    assert created.created_at == "2026-08-03T12:01:00.000000Z"
    assert snapshot.messages == ()
    assert (universe / ".tinyassets.db").is_file()
    assert not (root / ".tinyassets.db").exists()


def test_private_store_durably_refuses_a_copied_signed_grant(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    key = _key(79)
    original_grant = _create_grant(
        custody,
        root,
        universe,
        interlocutor_ref="slack:user_1",
        key=key,
    )
    with custody._GRANT_LOCK:  # noqa: SLF001 - adversarial copy of a real envelope
        copied_payload = custody._GRANTS[original_grant._grant_id][1]  # noqa: SLF001
    storage.create_thread(
        original_grant,
        scope=_scope(custody),
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )

    wrapper_id = secrets.token_hex(32)
    issuer_pid = os.getpid()
    copied_grant = object.__new__(custody.ConversationCustodyOperationGrant)
    object.__setattr__(copied_grant, "_grant_id", wrapper_id)
    object.__setattr__(copied_grant, "_issuer_pid", issuer_pid)
    with custody._GRANT_LOCK:  # noqa: SLF001 - adversarial private-registry replay
        custody._GRANTS[wrapper_id] = (  # noqa: SLF001
            weakref.ref(copied_grant),
            copied_payload,
            issuer_pid,
        )
    weakref.finalize(copied_grant, custody._discard_grant, wrapper_id, issuer_pid)  # noqa: SLF001

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as replay:
        storage.create_thread(
            copied_grant,
            scope=_scope(custody),
            idempotency_key=key,
            interlocutor_ref="slack:user_1",
            retention_until=None,
        )
    assert replay.value.code == "grant_consumed"
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute(
            "SELECT grant_id FROM conversation_custody_consumed_grants"
        ).fetchall() == [(copied_payload.evidence.grant_id,)]


def test_private_store_create_idempotency_conflict_preserves_original(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    key = "ik_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    original = storage.create_thread(
        _create_grant(custody, root, universe, interlocutor_ref="slack:user_1", key=key),
        scope=_scope(custody),
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )

    with pytest.raises(storage.ConversationCustodyConflict):
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_2",
                key=key,
            ),
            scope=_scope(custody),
            idempotency_key=key,
            interlocutor_ref="slack:user_2",
            retention_until=None,
        )
    snapshot = storage.read_thread(
        _read_grant(custody, root, universe, conversation_id=original.conversation_id),
        scope=_scope(custody),
        conversation_id=original.conversation_id,
    )
    assert snapshot.thread == original


def test_private_store_append_replay_conflict_and_contiguous_reply(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = "ik_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    append_key = "ik_AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQE"
    second_key = "ik_AgICAgICAgICAgICAgICAgICAgICAgICAgICAgICAgI"
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=create_key,
        ),
        scope=_scope(custody),
        idempotency_key=create_key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    payload = {"text": "hello"}
    first = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=append_key,
            payload=payload,
        ),
        scope=_scope(custody),
        idempotency_key=append_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=payload,
        reply_to_message_id=None,
    )
    replay = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=append_key,
            payload=payload,
        ),
        scope=_scope(custody),
        idempotency_key=append_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=payload,
        reply_to_message_id=None,
    )
    assert replay == first

    changed = {"text": "changed"}
    with pytest.raises(storage.ConversationCustodyConflict):
        storage.append_message(
            _append_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                key=append_key,
                payload=changed,
            ),
            scope=_scope(custody),
            idempotency_key=append_key,
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload=changed,
            reply_to_message_id=None,
        )

    second_payload = {"text": "reply"}
    second = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=second_key,
            payload=second_payload,
            reply_to_message_id=first.message_id,
        ),
        scope=_scope(custody),
        idempotency_key=second_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=second_payload,
        reply_to_message_id=first.message_id,
    )
    assert second.ordinal == 2
    assert second.reply_to_message_id == first.message_id


def test_private_store_append_replay_refuses_a_dangling_persisted_reply(
    tmp_path: Path,
) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    scope = _scope(custody)
    create_key = _key(80)
    first_key = _key(81)
    reply_key = _key(82)
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=create_key,
        ),
        scope=scope,
        idempotency_key=create_key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    first_payload = {"text": "first"}
    first = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=first_key,
            payload=first_payload,
        ),
        scope=scope,
        idempotency_key=first_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=first_payload,
        reply_to_message_id=None,
    )
    reply_payload = {"text": "reply"}
    storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=reply_key,
            payload=reply_payload,
            reply_to_message_id=first.message_id,
        ),
        scope=scope,
        idempotency_key=reply_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=reply_payload,
        reply_to_message_id=first.message_id,
    )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute(
            "DELETE FROM conversation_custody_messages WHERE message_id = ?",
            (first.message_id,),
        )

    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.append_message(
            _append_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                key=reply_key,
                payload=reply_payload,
                reply_to_message_id=first.message_id,
            ),
            scope=scope,
            idempotency_key=reply_key,
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload=reply_payload,
            reply_to_message_id=first.message_id,
        )
    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.read_thread(
            _read_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
            ),
            scope=scope,
            conversation_id=thread.conversation_id,
        )


def test_private_store_missing_reply_fails_without_consuming_ordinal(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = "ik_AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
    bad_key = "ik_AwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwM"
    good_key = "ik_BAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQ"
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=create_key,
        ),
        scope=_scope(custody),
        idempotency_key=create_key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    payload = {"text": "hello"}
    with pytest.raises(storage.ConversationCustodyReplyError):
        storage.append_message(
            _append_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                key=bad_key,
                payload=payload,
                reply_to_message_id="message_missing",
            ),
            scope=_scope(custody),
            idempotency_key=bad_key,
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload=payload,
            reply_to_message_id="message_missing",
        )
    appended = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=good_key,
            payload=payload,
        ),
        scope=_scope(custody),
        idempotency_key=good_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=payload,
        reply_to_message_id=None,
    )
    assert appended.ordinal == 1


def test_private_store_rejects_malformed_input_before_grant_consumption(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    key = _key(5)
    grant = _create_grant(
        custody,
        root,
        universe,
        interlocutor_ref="slack:user_1",
        key=key,
    )

    with pytest.raises(custody.ConversationCustodyValidationError):
        storage.create_thread(
            grant,
            scope=_scope(custody),
            idempotency_key="invalid",
            interlocutor_ref="slack:user_1",
            retention_until=None,
        )
    created = storage.create_thread(
        grant,
        scope=_scope(custody),
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    assert created.conversation_id.startswith("conversation_")


def test_private_store_same_key_is_independent_across_universes(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe_1 = root / "universes" / "u1"
    universe_2 = root / "universes" / "u2"
    universe_1.mkdir(parents=True)
    universe_2.mkdir(parents=True)
    key = _key(6)

    created = []
    for universe_id, universe in (("universe_1", universe_1), ("universe_2", universe_2)):
        scope = _scope(custody, universe_id=universe_id)
        created.append(
            storage.create_thread(
                _create_grant(
                    custody,
                    root,
                    universe,
                    interlocutor_ref="slack:user_1",
                    key=key,
                    scope=scope,
                ),
                scope=scope,
                idempotency_key=key,
                interlocutor_ref="slack:user_1",
                retention_until=None,
            )
        )

    assert created[0].conversation_id != created[1].conversation_id
    assert created[0].universe_id == "universe_1"
    assert created[1].universe_id == "universe_2"


def test_private_store_refuses_two_universes_at_one_registered_database(
    tmp_path: Path,
) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "shared"
    universe.mkdir(parents=True)
    key = _key(65)
    first_scope = _scope(custody, universe_id="universe_1")
    second_scope = _scope(custody, universe_id="universe_2")
    first = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=key,
            scope=first_scope,
        ),
        scope=first_scope,
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_2",
                key=key,
                scope=second_scope,
            ),
            scope=second_scope,
            idempotency_key=key,
            interlocutor_ref="slack:user_2",
            retention_until=None,
        )
    assert blocked.value.code == "storage_universe_mismatch"
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        rows = conn.execute(
            "SELECT DISTINCT universe_id FROM conversation_custody_threads"
        ).fetchall()
        binding = conn.execute(
            "SELECT singleton_id, universe_id FROM conversation_custody_database_binding"
        ).fetchall()
    assert rows == [(first.universe_id,)]
    assert binding == [(1, first.universe_id)]

    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute("DELETE FROM conversation_custody_database_binding")
    with pytest.raises(custody.ConversationCustodyAuthorizationError) as migrated:
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_2",
                key=_key(66),
                scope=second_scope,
            ),
            scope=second_scope,
            idempotency_key=_key(66),
            interlocutor_ref="slack:user_2",
            retention_until=None,
        )
    assert migrated.value.code == "storage_universe_mismatch"

    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute(
            "UPDATE conversation_custody_threads SET universe_id = ?",
            (second_scope.universe_id,),
        )
    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_2",
                key=_key(67),
                scope=second_scope,
            ),
            scope=second_scope,
            idempotency_key=_key(67),
            interlocutor_ref="slack:user_2",
            retention_until=None,
        )


def test_private_store_first_failed_read_permanently_binds_empty_database(
    tmp_path: Path,
) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "shared"
    universe.mkdir(parents=True)
    first_scope = _scope(custody, universe_id="universe_1")
    second_scope = _scope(custody, universe_id="universe_2")

    with pytest.raises(storage.ConversationCustodyNotFound):
        storage.read_thread(
            _read_grant(
                custody,
                root,
                universe,
                conversation_id="conversation_missing",
                scope=first_scope,
            ),
            scope=first_scope,
            conversation_id="conversation_missing",
        )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute(
            "SELECT universe_id FROM conversation_custody_database_binding"
        ).fetchall() == [(first_scope.universe_id,)]

    with pytest.raises(custody.ConversationCustodyAuthorizationError) as blocked:
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_2",
                key=_key(68),
                scope=second_scope,
            ),
            scope=second_scope,
            idempotency_key=_key(68),
            interlocutor_ref="slack:user_2",
            retention_until=None,
        )
    assert blocked.value.code == "storage_universe_mismatch"


def test_private_store_legacy_deletion_binding_uses_canonical_receipt(
    tmp_path: Path,
) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "shared"
    universe.mkdir(parents=True)
    first_scope = _scope(custody, universe_id="universe_1")
    second_scope = _scope(custody, universe_id="universe_2")
    create_key = _key(69)
    delete_key = _key(70)
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=create_key,
            scope=first_scope,
        ),
        scope=first_scope,
        idempotency_key=create_key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    storage.delete_thread(
        _grant(
            custody,
            _evidence(
                custody,
                root,
                universe,
                action="delete_thread",
                request_digest=custody.delete_thread_request_digest(
                    first_scope,
                    conversation_id=thread.conversation_id,
                    reason="owner_request",
                ),
                key_digest=custody.idempotency_key_digest(delete_key),
                scope=first_scope,
            ),
        ),
        scope=first_scope,
        idempotency_key=delete_key,
        conversation_id=thread.conversation_id,
        reason="owner_request",
    )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute("DELETE FROM conversation_custody_database_binding")
        conn.execute(
            "UPDATE conversation_custody_deletions SET universe_id = ?",
            (second_scope.universe_id,),
        )

    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_2",
                key=_key(71),
                scope=second_scope,
            ),
            scope=second_scope,
            idempotency_key=_key(71),
            interlocutor_ref="slack:user_2",
            retention_until=None,
        )

    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute(
            "UPDATE conversation_custody_deletions SET universe_id = ?",
            ("not canonical!",),
        )
    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_2",
                key=_key(72),
                scope=second_scope,
            ),
            scope=second_scope,
            idempotency_key=_key(72),
            interlocutor_ref="slack:user_2",
            retention_until=None,
        )


def test_private_store_concurrent_identical_create_has_one_result(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    key = _key(7)
    grants = [
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=key,
        )
        for _ in range(8)
    ]

    def create(grant):
        return storage.create_thread(
            grant,
            scope=_scope(custody),
            idempotency_key=key,
            interlocutor_ref="slack:user_1",
            retention_until=None,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(create, grants))

    assert len({result.conversation_id for result in results}) == 1
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_custody_threads").fetchone()[0] == 1
        assert (
            conn.execute("SELECT COUNT(*) FROM conversation_custody_idempotency").fetchone()[0] == 1
        )


def test_private_store_concurrent_distinct_appends_are_contiguous(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = _key(8)
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=create_key,
        ),
        scope=_scope(custody),
        idempotency_key=create_key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    calls = []
    for index in range(9, 17):
        payload = {"index": index}
        key = _key(index)
        calls.append(
            (
                _append_grant(
                    custody,
                    root,
                    universe,
                    conversation_id=thread.conversation_id,
                    key=key,
                    payload=payload,
                ),
                key,
                payload,
            )
        )

    def append(call):
        grant, key, payload = call
        return storage.append_message(
            grant,
            scope=_scope(custody),
            idempotency_key=key,
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload=payload,
            reply_to_message_id=None,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(append, calls))

    assert sorted(result.ordinal for result in results) == list(range(1, 9))
    snapshot = storage.read_thread(
        _read_grant(custody, root, universe, conversation_id=thread.conversation_id),
        scope=_scope(custody),
        conversation_id=thread.conversation_id,
    )
    assert tuple(message.ordinal for message in snapshot.messages) == tuple(range(1, 9))


def test_private_store_concurrent_identical_append_has_one_result(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = _key(28)
    append_key = _key(29)
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=create_key,
        ),
        scope=_scope(custody),
        idempotency_key=create_key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    payload = {"same": True}
    grants = [
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=append_key,
            payload=payload,
        )
        for _ in range(8)
    ]

    def append(grant):
        return storage.append_message(
            grant,
            scope=_scope(custody),
            idempotency_key=append_key,
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload=payload,
            reply_to_message_id=None,
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        results = tuple(executor.map(append, grants))
    assert len({result.message_id for result in results}) == 1
    assert {result.ordinal for result in results} == {1}


def test_private_store_cross_thread_reply_fails_without_ordinal(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    threads = []
    for index in (30, 31):
        key = _key(index)
        threads.append(
            storage.create_thread(
                _create_grant(
                    custody,
                    root,
                    universe,
                    interlocutor_ref=f"slack:user_{index}",
                    key=key,
                ),
                scope=_scope(custody),
                idempotency_key=key,
                interlocutor_ref=f"slack:user_{index}",
                retention_until=None,
            )
        )
    first_payload = {"thread": 1}
    first = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=threads[0].conversation_id,
            key=_key(32),
            payload=first_payload,
        ),
        scope=_scope(custody),
        idempotency_key=_key(32),
        conversation_id=threads[0].conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=first_payload,
        reply_to_message_id=None,
    )
    reply_payload = {"thread": 2}
    with pytest.raises(storage.ConversationCustodyReplyError):
        storage.append_message(
            _append_grant(
                custody,
                root,
                universe,
                conversation_id=threads[1].conversation_id,
                key=_key(33),
                payload=reply_payload,
                reply_to_message_id=first.message_id,
            ),
            scope=_scope(custody),
            idempotency_key=_key(33),
            conversation_id=threads[1].conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload=reply_payload,
            reply_to_message_id=first.message_id,
        )
    appended = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=threads[1].conversation_id,
            key=_key(34),
            payload=reply_payload,
        ),
        scope=_scope(custody),
        idempotency_key=_key(34),
        conversation_id=threads[1].conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=reply_payload,
        reply_to_message_id=None,
    )
    assert appended.ordinal == 1


def test_private_store_cross_process_create_append_and_reply_races(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = _key(20)
    context = multiprocessing.get_context("spawn")
    create_args = (str(root), str(universe), create_key)
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as executor:
        conversation_ids = tuple(executor.map(_process_create_thread, (create_args,) * 6))
    assert len(set(conversation_ids)) == 1
    conversation_id = conversation_ids[0]

    first_key = _key(21)
    first_payload = {"seed": True}
    first = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=conversation_id,
            key=first_key,
            payload=first_payload,
        ),
        scope=_scope(custody),
        idempotency_key=first_key,
        conversation_id=conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=first_payload,
        reply_to_message_id=None,
    )
    append_args = tuple(
        (
            str(root),
            str(universe),
            conversation_id,
            first.message_id,
            _key(index),
            index,
        )
        for index in range(22, 28)
    )
    with ProcessPoolExecutor(max_workers=4, mp_context=context) as executor:
        replies = tuple(executor.map(_process_append_reply, append_args))

    assert sorted(ordinal for ordinal, _reply in replies) == list(range(2, 8))
    assert {reply for _ordinal, reply in replies} == {first.message_id}


def test_private_store_detects_indexed_and_payload_tampering(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = _key(17)
    append_key = _key(18)
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=create_key,
        ),
        scope=_scope(custody),
        idempotency_key=create_key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )
    payload = {"private_sentinel": "do-not-leak"}
    storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=append_key,
            payload=payload,
        ),
        scope=_scope(custody),
        idempotency_key=append_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=payload,
        reply_to_message_id=None,
    )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute(
            "UPDATE conversation_custody_messages SET payload_digest = ?",
            ("sha256:" + "0" * 64,),
        )

    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.read_thread(
            _read_grant(custody, root, universe, conversation_id=thread.conversation_id),
            scope=_scope(custody),
            conversation_id=thread.conversation_id,
        )


def test_private_store_is_not_exported_and_uses_required_sqlite_mode(tmp_path: Path) -> None:
    custody = _custody()
    storage_package = importlib.import_module("tinyassets.storage")
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    key = _key(19)
    _storage().create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_1",
            key=key,
        ),
        scope=_scope(custody),
        idempotency_key=key,
        interlocutor_ref="slack:user_1",
        retention_until=None,
    )

    assert not hasattr(storage_package, "create_thread")
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        foreign_keys = conn.execute(
            "PRAGMA foreign_key_list(conversation_custody_messages)"
        ).fetchall()
        assert any(row[2] == "conversation_custody_threads" for row in foreign_keys)


def _stored_conversation(
    tmp_path: Path,
    *,
    retention_until: str | None = None,
    payload: dict[str, object] | None = None,
):
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = _key(40)
    append_key = _key(41)
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:user_private_sentinel",
            key=create_key,
            retention_until=retention_until,
        ),
        scope=_scope(custody),
        idempotency_key=create_key,
        interlocutor_ref="slack:user_private_sentinel",
        retention_until=retention_until,
    )
    selected_payload = payload or {"private_sentinel": "custody-secret-9f4c1a"}
    message = storage.append_message(
        _append_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            key=append_key,
            payload=selected_payload,
        ),
        scope=_scope(custody),
        idempotency_key=append_key,
        conversation_id=thread.conversation_id,
        kind="text",
        participant_ref="slack:user_1",
        source_event_ref="slack:event_1",
        payload=selected_payload,
        reply_to_message_id=None,
    )
    return custody, storage, root, universe, create_key, append_key, thread, message


def test_private_store_export_is_authorized_and_byte_stable(tmp_path: Path) -> None:
    custody, storage, root, universe, *_rest, thread, message = _stored_conversation(tmp_path)

    first = storage.export_thread(
        _export_grant(custody, root, universe, conversation_id=thread.conversation_id),
        scope=_scope(custody),
        conversation_id=thread.conversation_id,
    )
    second = storage.export_thread(
        _export_grant(custody, root, universe, conversation_id=thread.conversation_id),
        scope=_scope(custody),
        conversation_id=thread.conversation_id,
    )

    assert first == second
    assert first == custody.export_conversation(thread, (message,))
    assert b"custody-secret-9f4c1a" in first.content


def test_private_store_owner_deletion_tombstones_content_and_keys(tmp_path: Path) -> None:
    (
        custody,
        storage,
        root,
        universe,
        create_key,
        append_key,
        thread,
        _message,
    ) = _stored_conversation(tmp_path)
    delete_key = _key(42)
    receipt = storage.delete_thread(
        _delete_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            reason="owner_request",
            key=delete_key,
        ),
        scope=_scope(custody),
        idempotency_key=delete_key,
        conversation_id=thread.conversation_id,
        reason="owner_request",
    )

    assert receipt.conversation_id == thread.conversation_id
    assert receipt.reason == "owner_request"
    assert receipt.deleted_message_count == 1
    assert receipt.deletion_scope == "active_private_universe_sqlite"
    assert "historical backups" in receipt.historical_backup_caveat
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_custody_threads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM conversation_custody_messages").fetchone()[0] == 0
        tombstones = conn.execute(
            """
            SELECT operation_kind, request_digest, conversation_id, result_ref,
                   deleted_target_digest
            FROM conversation_custody_idempotency
            WHERE operation_kind IN ('create_thread', 'append_message')
            ORDER BY operation_kind
            """
        ).fetchall()
        assert tombstones == [
            ("append_message", None, None, None, None),
            ("create_thread", None, None, None, None),
        ]

    for path in (
        universe / ".tinyassets.db",
        universe / ".tinyassets.db-wal",
        universe / ".tinyassets.db-shm",
    ):
        if path.exists():
            assert b"custody-secret-9f4c1a" not in path.read_bytes()
            assert b"slack:user_private_sentinel" not in path.read_bytes()

    with pytest.raises(storage.ConversationCustodyDeleted):
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:user_private_sentinel",
                key=create_key,
            ),
            scope=_scope(custody),
            idempotency_key=create_key,
            interlocutor_ref="slack:user_private_sentinel",
            retention_until=None,
        )
    with pytest.raises(storage.ConversationCustodyDeleted):
        storage.create_thread(
            _create_grant(
                custody,
                root,
                universe,
                interlocutor_ref="slack:changed",
                key=create_key,
            ),
            scope=_scope(custody),
            idempotency_key=create_key,
            interlocutor_ref="slack:changed",
            retention_until=None,
        )
    with pytest.raises(storage.ConversationCustodyDeleted):
        storage.append_message(
            _append_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                key=append_key,
                payload={"private_sentinel": "custody-secret-9f4c1a"},
            ),
            scope=_scope(custody),
            idempotency_key=append_key,
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload={"private_sentinel": "custody-secret-9f4c1a"},
            reply_to_message_id=None,
        )
    with pytest.raises(storage.ConversationCustodyDeleted):
        storage.append_message(
            _append_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                key=_key(45),
                payload={"fresh": True},
            ),
            scope=_scope(custody),
            idempotency_key=_key(45),
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:user_1",
            source_event_ref="slack:event_1",
            payload={"fresh": True},
            reply_to_message_id=None,
        )
    for operation, grant in (
        (
            storage.read_thread,
            _read_grant(custody, root, universe, conversation_id=thread.conversation_id),
        ),
        (
            storage.export_thread,
            _export_grant(custody, root, universe, conversation_id=thread.conversation_id),
        ),
    ):
        with pytest.raises(storage.ConversationCustodyDeleted):
            operation(
                grant,
                scope=_scope(custody),
                conversation_id=thread.conversation_id,
            )
    fresh_key = _key(63)
    fresh = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:fresh",
            key=fresh_key,
        ),
        scope=_scope(custody),
        idempotency_key=fresh_key,
        interlocutor_ref="slack:fresh",
        retention_until=None,
    )
    assert fresh.conversation_id != thread.conversation_id


def test_private_store_retention_deletion_refuses_early_then_succeeds(tmp_path: Path) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(
        tmp_path,
        retention_until="2026-08-03T12:04:00.000000Z",
    )
    early_key = _key(43)
    _set_test_now("2026-08-03T12:03:59.999999Z")
    with pytest.raises(storage.ConversationCustodyRetentionError):
        storage.delete_thread(
            _delete_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                reason="retention_expired",
                key=early_key,
            ),
            scope=_scope(custody),
            idempotency_key=early_key,
            conversation_id=thread.conversation_id,
            reason="retention_expired",
        )
    snapshot = storage.read_thread(
        _read_grant(custody, root, universe, conversation_id=thread.conversation_id),
        scope=_scope(custody),
        conversation_id=thread.conversation_id,
    )
    assert len(snapshot.messages) == 1

    delete_key = _key(44)
    _set_test_now("2026-08-03T12:04:00.000000Z")
    receipt = storage.delete_thread(
        _delete_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            reason="retention_expired",
            key=delete_key,
        ),
        scope=_scope(custody),
        idempotency_key=delete_key,
        conversation_id=thread.conversation_id,
        reason="retention_expired",
    )
    assert receipt.reason == "retention_expired"


def test_private_store_delete_retries_and_competing_keys_are_deterministic(
    tmp_path: Path,
) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(tmp_path)
    first_key = _key(46)

    def delete(key: str, reason: str, now: str):
        _set_test_now(now)
        return storage.delete_thread(
            _delete_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                reason=reason,
                key=key,
            ),
            scope=_scope(custody),
            idempotency_key=key,
            conversation_id=thread.conversation_id,
            reason=reason,
        )

    first = delete(first_key, "owner_request", "2026-08-03T12:02:00.000000Z")
    assert delete(first_key, "owner_request", "2026-08-03T12:02:01.000000Z") == first
    assert delete(_key(47), "owner_request", "2026-08-03T12:02:02.000000Z") == first
    with pytest.raises(storage.ConversationCustodyConflict):
        delete(first_key, "retention_expired", "2026-08-03T12:02:03.000000Z")
    with pytest.raises(storage.ConversationCustodyConflict):
        delete(_key(48), "retention_expired", "2026-08-03T12:02:04.000000Z")


def test_private_store_cleanup_interruption_resumes_without_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(tmp_path)
    delete_key = _key(49)
    original_checkpoint = storage._checkpoint_truncate

    def interrupted(*_args, **_kwargs):
        raise storage.ConversationCustodyCleanupPending("injected busy checkpoint")

    monkeypatch.setattr(storage, "_checkpoint_truncate", interrupted)
    _set_test_now("2026-08-03T12:02:00.000000Z")
    with pytest.raises(storage.ConversationCustodyCleanupPending):
        storage.delete_thread(
            _delete_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                reason="owner_request",
                key=delete_key,
            ),
            scope=_scope(custody),
            idempotency_key=delete_key,
            conversation_id=thread.conversation_id,
            reason="owner_request",
        )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_custody_threads").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM conversation_custody_messages").fetchone()[0] == 0
        assert (
            conn.execute(
                "SELECT cleanup_completed_at FROM conversation_custody_deletions"
            ).fetchone()[0]
            is None
        )
    with pytest.raises(storage.ConversationCustodyDeleted):
        storage.read_thread(
            _read_grant(custody, root, universe, conversation_id=thread.conversation_id),
            scope=_scope(custody),
            conversation_id=thread.conversation_id,
        )

    monkeypatch.setattr(storage, "_checkpoint_truncate", original_checkpoint)
    _set_test_now("2026-08-03T12:02:02.000000Z")
    receipt = storage.delete_thread(
        _delete_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            reason="owner_request",
            key=delete_key,
        ),
        scope=_scope(custody),
        idempotency_key=delete_key,
        conversation_id=thread.conversation_id,
        reason="owner_request",
    )
    assert receipt.logical_deleted_at == "2026-08-03T12:02:00.000000Z"
    assert receipt.cleanup_completed_at == "2026-08-03T12:02:02.000000Z"


def test_private_store_deletes_corrupt_content_but_refuses_corrupt_scope(
    tmp_path: Path,
) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(tmp_path)
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute(
            "UPDATE conversation_custody_messages SET record_json = ?",
            (b'{"corrupt":"custody-secret-9f4c1a"}',),
        )
    receipt = storage.delete_thread(
        _delete_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            reason="owner_request",
            key=_key(50),
        ),
        scope=_scope(custody),
        idempotency_key=_key(50),
        conversation_id=thread.conversation_id,
        reason="owner_request",
    )
    assert receipt.deleted_message_count == 1

    second = _stored_conversation(tmp_path / "second")
    custody, storage, root, universe, *_rest, thread, _message = second
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute("UPDATE conversation_custody_threads SET owner_user_id = 'owner_other'")
    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.delete_thread(
            _delete_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                reason="owner_request",
                key=_key(51),
            ),
            scope=_scope(custody),
            idempotency_key=_key(51),
            conversation_id=thread.conversation_id,
            reason="owner_request",
        )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_custody_threads").fetchone()[0] == 1


def test_retention_deletion_rejects_corrupted_duplicate_boundary(tmp_path: Path) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(
        tmp_path,
        retention_until="2030-01-01T00:00:00.000000Z",
    )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        conn.execute(
            """
            UPDATE conversation_custody_threads
            SET retention_until = '2020-01-01T00:00:00.000000Z'
            WHERE conversation_id = ?
            """,
            (thread.conversation_id,),
        )

    with pytest.raises(storage.ConversationCustodyIntegrityError):
        storage.delete_thread(
            _delete_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                reason="retention_expired",
                key=_key(64),
            ),
            scope=_scope(custody),
            idempotency_key=_key(64),
            conversation_id=thread.conversation_id,
            reason="retention_expired",
        )
    with sqlite3.connect(universe / ".tinyassets.db") as conn:
        assert conn.execute("SELECT COUNT(*) FROM conversation_custody_threads").fetchone()[0] == 1


def test_private_store_append_and_delete_follow_transaction_order(tmp_path: Path) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(tmp_path)
    append_key = _key(52)
    delete_key = _key(53)
    payload = {"racing": True}
    append_grant = _append_grant(
        custody,
        root,
        universe,
        conversation_id=thread.conversation_id,
        key=append_key,
        payload=payload,
    )
    delete_grant = _delete_grant(
        custody,
        root,
        universe,
        conversation_id=thread.conversation_id,
        reason="owner_request",
        key=delete_key,
    )
    barrier = threading.Barrier(2)

    def append():
        barrier.wait()
        try:
            return storage.append_message(
                append_grant,
                scope=_scope(custody),
                idempotency_key=append_key,
                conversation_id=thread.conversation_id,
                kind="text",
                participant_ref="slack:user_1",
                source_event_ref="slack:event_1",
                payload=payload,
                reply_to_message_id=None,
            )
        except storage.ConversationCustodyDeleted as exc:
            return exc

    def delete():
        barrier.wait()
        return storage.delete_thread(
            delete_grant,
            scope=_scope(custody),
            idempotency_key=delete_key,
            conversation_id=thread.conversation_id,
            reason="owner_request",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        append_future = executor.submit(append)
        delete_future = executor.submit(delete)
        append_result = append_future.result()
        receipt = delete_future.result()

    if isinstance(append_result, storage.ConversationCustodyDeleted):
        assert receipt.deleted_message_count == 1
    else:
        assert append_result.ordinal == 2
        assert receipt.deleted_message_count == 2


@pytest.mark.parametrize("operation_name", ["read_thread", "export_thread"])
def test_private_store_read_or_export_and_delete_follow_transaction_order(
    tmp_path: Path,
    operation_name: str,
) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(tmp_path)
    operation = getattr(storage, operation_name)
    operation_grant = (
        _read_grant(custody, root, universe, conversation_id=thread.conversation_id)
        if operation_name == "read_thread"
        else _export_grant(custody, root, universe, conversation_id=thread.conversation_id)
    )
    delete_key = _key(54)
    delete_grant = _delete_grant(
        custody,
        root,
        universe,
        conversation_id=thread.conversation_id,
        reason="owner_request",
        key=delete_key,
    )
    barrier = threading.Barrier(2)

    def observe():
        barrier.wait()
        try:
            return operation(
                operation_grant,
                scope=_scope(custody),
                conversation_id=thread.conversation_id,
            )
        except storage.ConversationCustodyDeleted as exc:
            return exc

    def delete():
        barrier.wait()
        return storage.delete_thread(
            delete_grant,
            scope=_scope(custody),
            idempotency_key=delete_key,
            conversation_id=thread.conversation_id,
            reason="owner_request",
        )

    with ThreadPoolExecutor(max_workers=2) as executor:
        observed_future = executor.submit(observe)
        deleted_future = executor.submit(delete)
        observed = observed_future.result()
        receipt = deleted_future.result()

    assert receipt.deleted_message_count == 1
    if not isinstance(observed, storage.ConversationCustodyDeleted):
        if operation_name == "read_thread":
            assert len(observed.messages) == 1
        else:
            assert b"custody-secret-9f4c1a" in observed.content


def test_private_store_concurrent_delete_keys_return_one_receipt(tmp_path: Path) -> None:
    custody, storage, root, universe, *_rest, thread, _message = _stored_conversation(tmp_path)
    keys = tuple(_key(index) for index in range(55, 63))
    grants = tuple(
        _delete_grant(
            custody,
            root,
            universe,
            conversation_id=thread.conversation_id,
            reason="owner_request",
            key=key,
        )
        for key in keys
    )

    def delete(call):
        key, grant = call
        return storage.delete_thread(
            grant,
            scope=_scope(custody),
            idempotency_key=key,
            conversation_id=thread.conversation_id,
            reason="owner_request",
        )

    with ThreadPoolExecutor(max_workers=8) as executor:
        receipts = tuple(executor.map(delete, zip(keys, grants, strict=True)))
    assert len(set(receipts)) == 1


def test_private_store_production_shaped_concurrent_load(tmp_path: Path) -> None:
    custody = _custody()
    storage = _storage()
    root = tmp_path / "platform"
    universe = root / "universes" / "u1"
    universe.mkdir(parents=True)
    create_key = _key(90)
    thread = storage.create_thread(
        _create_grant(
            custody,
            root,
            universe,
            interlocutor_ref="slack:load_user",
            key=create_key,
        ),
        scope=_scope(custody),
        idempotency_key=create_key,
        interlocutor_ref="slack:load_user",
        retention_until=None,
    )
    inputs = tuple((_key(index), {"load_index": index}) for index in range(100, 164))

    def call(item):
        key, payload = item
        return storage.append_message(
            _append_grant(
                custody,
                root,
                universe,
                conversation_id=thread.conversation_id,
                key=key,
                payload=payload,
                participant_ref="slack:load_user",
                source_event_ref="slack:load_event",
            ),
            scope=_scope(custody),
            idempotency_key=key,
            conversation_id=thread.conversation_id,
            kind="text",
            participant_ref="slack:load_user",
            source_event_ref="slack:load_event",
            payload=payload,
            reply_to_message_id=None,
        )

    with ThreadPoolExecutor(max_workers=16) as executor:
        created = tuple(executor.map(call, inputs))
    with ThreadPoolExecutor(max_workers=16) as executor:
        replayed = tuple(executor.map(call, inputs))

    assert [message.message_id for message in replayed] == [
        message.message_id for message in created
    ]
    assert sorted(message.ordinal for message in created) == list(range(1, 65))
    snapshot = storage.read_thread(
        _read_grant(custody, root, universe, conversation_id=thread.conversation_id),
        scope=_scope(custody),
        conversation_id=thread.conversation_id,
    )
    exported = storage.export_thread(
        _export_grant(custody, root, universe, conversation_id=thread.conversation_id),
        scope=_scope(custody),
        conversation_id=thread.conversation_id,
    )
    assert tuple(message.ordinal for message in snapshot.messages) == tuple(range(1, 65))
    assert exported == custody.export_conversation(snapshot.thread, snapshot.messages)


def test_packaged_runtime_mirrors_exist_and_are_byte_identical() -> None:
    root = Path(__file__).parents[1]
    runtime = (
        root
        / "packaging"
        / "claude-plugin"
        / "plugins"
        / "tinyassets-universe-server"
        / "runtime"
        / "tinyassets"
    )
    pairs = (
        (root / "tinyassets" / "conversation_custody.py", runtime / "conversation_custody.py"),
        (
            root / "tinyassets" / "storage" / "conversation_custody.py",
            runtime / "storage" / "conversation_custody.py",
        ),
    )

    for canonical, mirror in pairs:
        assert mirror.is_file(), f"required packaged mirror is missing: {mirror}"
        assert mirror.read_bytes() == canonical.read_bytes()


def test_custody_adds_no_public_handle_or_production_consumer() -> None:
    import ast

    import tinyassets.universe_server as universe_server

    advertised = {
        tool.name for tool in asyncio.run(universe_server.mcp.list_tools(run_middleware=True))
    }
    assert advertised == {
        "read_graph",
        "write_graph",
        "run_graph",
        "read_page",
        "write_page",
        "converse",
        "get_status",
    }

    root = Path(__file__).parents[1]
    owners = {
        root / "tinyassets" / "conversation_custody.py",
        root / "tinyassets" / "storage" / "conversation_custody.py",
    }
    consumers = []
    for path in (root / "tinyassets").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        if path == root / "tinyassets" / "owner_stores.py":
            # The pending-fence inventory names stores as data; it does not use
            # them. Exclude only that literal declaration, keeping imports and
            # every other reference in this module subject to the same guard.
            for node in ast.parse(source).body:
                if (isinstance(node, ast.AnnAssign)
                        and isinstance(node.target, ast.Name)
                        and node.target.id == "FENCE_BEFORE_C2"):
                    assert isinstance(node.value, ast.Call)
                    assert isinstance(node.value.func, ast.Name)
                    assert node.value.func.id == "frozenset"
                    assert len(node.value.args) == 1 and not node.value.keywords
                    assert isinstance(ast.literal_eval(node.value.args[0]), set)
                    source = source.replace(ast.get_source_segment(source, node), "", 1)
        if path not in owners and "conversation_custody" in source:
            consumers.append(path.relative_to(root).as_posix())
    # The two former consumers (app_conversation_authority.py, app_reply_authority.py)
    # were deleted in the channel-agnostic rip; conversation_custody now has ZERO
    # production consumers, which is even more aligned with this test's intent (no
    # public handle, no production consumer).
    assert consumers == []
    custody = _custody()
    assert not hasattr(custody, "_issue_operation_grant")
    source = (root / "tinyassets" / "conversation_custody.py").read_text(encoding="utf-8")
    assert "Ed25519PrivateKey" not in source
    assert ".sign(" not in source
    assert "CUSTODY_AUTHORITY_PRIVATE" not in source
    assert "now" not in inspect.signature(custody.consume_operation_grant).parameters
    storage = _storage()
    for operation in (
        storage.create_thread,
        storage.append_message,
        storage.read_thread,
        storage.export_thread,
        storage.delete_thread,
    ):
        assert "now" not in inspect.signature(operation).parameters


def test_custody_owners_have_no_network_app_provider_or_public_server_imports() -> None:
    root = Path(__file__).parents[1]
    forbidden_roots = {
        "aiohttp",
        "fastmcp",
        "httpx",
        "requests",
        "slack",
        "socket",
        "tinyassets.providers",
        "tinyassets.universe_server",
        "urllib",
    }
    for relative in (
        Path("tinyassets/conversation_custody.py"),
        Path("tinyassets/storage/conversation_custody.py"),
    ):
        source = (root / relative).read_text(encoding="utf-8")
        imported = set()
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
        assert not {
            name
            for name in imported
            if any(
                name == root_name or name.startswith(f"{root_name}.")
                for root_name in forbidden_roots
            )
        }
        assert "UPDATE agent_" not in source
        assert "INSERT INTO agent_" not in source
