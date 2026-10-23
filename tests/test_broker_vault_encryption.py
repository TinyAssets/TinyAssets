"""S28 real custody stores, rotations, sealed recovery and denied substitution."""
import json
import sqlite3

import pytest

from tests.support.broker_double import seed_owner_admission
from tests.support.vault_keys import install
from tinyassets.broker.admission import digest
from tinyassets.broker.key_service import KeyService
from tinyassets.broker.root_key import KeyServiceUnavailable, MemoryRootKey, ReconnectRequired
from tinyassets.broker.vault import write_credential_vault
from tinyassets.broker.vault_backup import validate_backup
from tinyassets.broker.vault_cutover import convert
from tinyassets.credential_vault import (
    credential_vault_path,
    custody_namespace,
    http_credential_record,
    load_credential_vault,
)

pytestmark = pytest.mark.verifies(
    "credential-vault-reshape:Broker-only owner envelope hierarchy")


def test_bound_ciphertext_rotation_and_root_failure(tmp_path):
    root = MemoryRootKey()
    keys = KeyService(tmp_path, root)
    owner = digest("alice")
    blob = keys.seal(owner, "http/a", b"secret")
    assert b"secret" not in blob
    assert keys.open(owner, "http/a", blob) == b"secret"
    with pytest.raises(ReconnectRequired):
        keys.open(digest("bob"), "http/a", blob)
    with pytest.raises(ReconnectRequired):
        keys.open(owner, "http/b", blob)
    bad = json.loads(blob)
    bad["sealed"] = "A" * len(bad["sealed"])
    with pytest.raises(ReconnectRequired):
        keys.open(owner, "http/a", json.dumps(bad))
    keys.rotate_owner(owner)
    new = keys.seal(owner, "http/a", b"rotated")
    assert keys.open(owner, "http/a", blob) == b"secret"
    replacement = MemoryRootKey()
    replacement.fail = True
    with pytest.raises(KeyServiceUnavailable):
        keys.rewrap(replacement)
    assert keys.open(owner, "http/a", blob) == b"secret"
    replacement.fail = False
    keys.rewrap(replacement)
    assert keys.open(owner, "http/a", new) == b"rotated"
    replacement.fail = True
    with pytest.raises(KeyServiceUnavailable):
        keys.open(owner, "http/a", blob)
    keys.erase(owner)
    with pytest.raises(ReconnectRequired):
        keys.open(owner, "http/a", blob)


def test_http_native_browser_sealed_restore(tmp_path):
    from tinyassets.broker.browser_vault import local_operation
    from tinyassets.broker.subscription_custody import operation
    from tinyassets.storage.outbound_connections import ConnectionLedger

    source = tmp_path / "source"
    source.mkdir()
    seed_owner_admission(source, principal="alice", center="center")
    namespace = custody_namespace(source, "alice")
    record = http_credential_record(destination="api", token="secret-http-unique")
    write_credential_vault(namespace, [record], owner_user_id="alice")
    entry = operation(source, "alice", {"action": "begin", "source": "native"})
    state = {"auth.json": "c2VjcmV0LW5hdGl2ZQ=="}
    operation(source, "alice", {"action": "save", "source": "native",
                                "revision": entry["revision"], "state": state})
    ledger = ConnectionLedger(source / ".broker/outbound.db", data_root=source,
                              allow_test_fixtures=True,
                              verify_authenticated_principal=lambda: "alice")

    def browser(ledger, document):
        return local_operation(ledger, principal="alice", command_center="center",
                               document=document)

    item = browser(ledger, {"action": "create", "url": "https://example.com",
                            "verify": {"url": "https://example.com/me", "selector": "#me"}})
    saved = browser(ledger, {"action": "save", "id": item["id"], "revision": item["revision"],
                             "state": {"cookie": "secret-browser-unique"}})
    validate_backup(source.resolve())
    for path in (source / ".broker").rglob("*"):
        if path.is_file():
            payload = path.read_bytes()
            assert b"secret-http-unique" not in payload
            assert b"secret-browser-unique" not in payload
            assert state["auth.json"].encode() not in payload
    target = tmp_path / "restore"
    from tinyassets.platform_backup import _copy_shared

    _copy_shared(source, target)
    validate_backup(target.resolve())
    from tinyassets.broker import key_service

    key_service._services[target.resolve()] = KeyService(target, install(source).provider)
    assert load_credential_vault(custody_namespace(target, "alice")) == [record]
    assert operation(target, "alice", {"action": "read", "source": "native"})["state"] == state
    restored = ConnectionLedger(target / ".broker/outbound.db", data_root=target,
                                 allow_test_fixtures=True,
                                 verify_authenticated_principal=lambda: "alice")
    assert browser(restored, {"action": "read", "id": saved["id"]})["state"] == {
        "cookie": "secret-browser-unique"}


def test_plaintext_refused_then_verified_offline_conversion(tmp_path):
    namespace = custody_namespace(tmp_path, "alice")
    path = credential_vault_path(namespace)
    path.parent.mkdir(parents=True)
    record = http_credential_record(destination="api", token="legacy-secret")
    path.write_text(json.dumps({"schema_version": 1, "credentials": [record]}))
    with pytest.raises(ReconnectRequired):
        load_credential_vault(namespace)
    with pytest.raises(ValueError, match="unsealed"):
        validate_backup(tmp_path.resolve())
    assert convert(tmp_path, legacy_browser_keys=tmp_path / "old-keys") == 1
    assert b"legacy-secret" not in path.read_bytes()
    assert load_credential_vault(namespace) == [record]
    assert convert(tmp_path, legacy_browser_keys=tmp_path / "old-keys") == 0
    validate_backup(tmp_path.resolve())


def test_failed_rewrap_rolls_back_all_versions(tmp_path):
    keys = KeyService(tmp_path, MemoryRootKey())
    owner = digest("alice")
    first = keys.seal(owner, "a", b"one")
    keys.rotate_owner(owner)

    class FailsSecond(MemoryRootKey):
        calls = 0

        def wrap(self, key, context):
            self.calls += 1
            if self.calls == 2:
                raise KeyServiceUnavailable("synthetic unavailable")
            return super().wrap(key, context)

    with pytest.raises(KeyServiceUnavailable):
        keys.rewrap(FailsSecond())
    assert keys.open(owner, "a", first) == b"one"
    with sqlite3.connect(keys.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM owner_keys").fetchone() == (2,)


def test_local_root_refused_in_production(tmp_path, monkeypatch):
    root = MemoryRootKey()
    monkeypatch.setenv("TINYASSETS_ENV", "production")
    with pytest.raises(RuntimeError, match="production"):
        KeyService(tmp_path, root)


def test_unwrapped_owner_key_is_cached_until_erase_or_rewrap(tmp_path):
    class Counting(MemoryRootKey):
        def __init__(self):
            super().__init__()
            self.unwraps = 0

        def unwrap(self, blob, context, **kwargs):
            self.unwraps += 1
            return super().unwrap(blob, context, **kwargs)

    root = Counting()
    keys = KeyService(tmp_path, root)
    owner = digest("alice")
    blob = keys.seal(owner, "slot", b"secret")
    assert root.unwraps == 0
    assert keys.open(owner, "slot", blob) == b"secret"
    keys.seal(owner, "slot", b"again")
    assert keys.open(owner, "slot", blob) == b"secret"
    assert root.unwraps == 1
    keys.erase(owner)
    blob = keys.seal(owner, "slot", b"fresh")
    assert keys.open(owner, "slot", blob) == b"fresh"
    assert root.unwraps == 2
    replacement = Counting()
    keys.rewrap(replacement)
    after_rewrap = replacement.unwraps
    assert keys.open(owner, "slot", blob) == b"fresh"
    assert replacement.unwraps == after_rewrap + 1


def test_production_refuses_a_subprocess_test_root(tmp_path, monkeypatch):
    from tinyassets.broker import key_service

    monkeypatch.setenv("TINYASSETS_ENV", "production")
    monkeypatch.setenv(key_service.TEST_ROOT_ENV, "memory")
    with pytest.raises(KeyServiceUnavailable, match="KMS"):
        key_service.service_for(tmp_path)


def test_browser_legacy_conversion_and_erasure_without_kms(tmp_path):
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from tinyassets.broker.key_service import erase_owner_keys

    owner = digest("alice")
    legacy = tmp_path / "legacy"
    legacy.mkdir()
    key = AESGCM.generate_key(bit_length=256)
    (legacy / (owner + ".key")).write_bytes(key)
    nonce = b"x" * 12
    raw = b'{"cookie":"synthetic-legacy-cookie"}'
    sealed = nonce + AESGCM(key).encrypt(nonce, raw, json.dumps([1, "alice", "a", 1]).encode())
    ledger = tmp_path / ".broker/outbound.db"
    ledger.parent.mkdir()
    with sqlite3.connect(ledger) as conn:
        conn.execute("CREATE TABLE browser_vault (id TEXT,owner TEXT,revision INTEGER,sealed BLOB)")
        conn.execute("INSERT INTO browser_vault VALUES (?,?,?,?)", ("a", "alice", 1, sealed))
    assert convert(tmp_path, legacy_browser_keys=legacy) == 1
    assert not list(legacy.iterdir())
    with sqlite3.connect(ledger) as conn:
        current = conn.execute("SELECT sealed FROM browser_vault").fetchone()[0]
    keys = install(tmp_path)
    assert keys.open(owner, "browser/a/1", current) == raw
    assert convert(tmp_path, legacy_browser_keys=legacy) == 0
    keys.provider.fail = True
    erase_owner_keys(tmp_path, owner)
    with sqlite3.connect(keys.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM owner_keys").fetchone() == (0,)


def test_backup_missing_wrapped_version_refused(tmp_path):
    namespace = custody_namespace(tmp_path, "alice")
    path = credential_vault_path(namespace)
    path.parent.mkdir(parents=True)
    keys = install(tmp_path)
    path.write_bytes(keys.seal(namespace.name, path.name, b"{}"))
    keys.erase(namespace.name)
    with pytest.raises(ValueError, match="incomplete"):
        validate_backup(tmp_path.resolve())


def test_aws_root_uses_bound_context_and_key_reference():
    import base64

    from tinyassets.broker.root_key import AwsKmsRootKey, encryption_context, kms_http_failure

    requests = []

    def transport(url, headers, payload):
        requests.append(json.loads(payload))
        return {"Plaintext": base64.b64encode(b"x" * 32).decode(),
                "CiphertextBlob": base64.b64encode(b"wrapped").decode()}

    root = AwsKmsRootKey(key_arn="arn:aws:kms:us-west-2:123:key/synthetic",
                         access_key_id="synthetic", secret_access_key="synthetic",
                         transport=transport)
    context = encryption_context(digest("alice"))
    key, wrapped = root.generate(context)
    assert root.unwrap(wrapped, context) == key
    root.wrap(key, context)
    assert all(r["EncryptionContext"] == context and r["KeyId"] == root.key_ref for r in requests)
    assert isinstance(kms_http_failure("AccessDeniedException"), KeyServiceUnavailable)
