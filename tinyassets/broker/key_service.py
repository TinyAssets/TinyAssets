"""Broker-only owner envelope keys. Disk and backups contain only wrapped keys."""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import threading
from contextlib import closing, contextmanager
from pathlib import Path

from tinyassets.broker.root_key import (
    AwsKmsRootKey,
    KeyServiceUnavailable,
    MemoryRootKey,
    ReconnectRequired,
    RootKeyError,
    encryption_context,
)
from tinyassets.broker.vault_backup import FORMAT
from tinyassets.vault_envelope import EnvelopeError, gcm_open, gcm_seal

_services = {}
_lock = threading.Lock()
KMS_CONFIG = Path("/etc/tinyassets/vault-kms.json")
TEST_ROOT_ENV = "TINYASSETS_VAULT_TEST_ROOT"


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _owner(owner):
    if not isinstance(owner, str) or not re.fullmatch("[a-f0-9]{64}", owner):
        raise RootKeyError("owner digest required")
    return owner


class KeyService:
    def __init__(self, root, provider):
        if isinstance(provider, MemoryRootKey) and os.getenv("TINYASSETS_ENV") == "production":
            raise RootKeyError("local root refused in production")
        self.provider = provider
        self._unwrapped = {}
        self._cache_lock = threading.Lock()
        self.path = Path(root) / ".broker/vault-keys.db"
        self.path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
        with self._transaction() as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS owner_keys ("
                         "owner TEXT, version INTEGER, provider TEXT, reference TEXT, "
                         "wrapped BLOB NOT NULL, PRIMARY KEY(owner,version))")

    @contextmanager
    def _transaction(self):
        with closing(sqlite3.connect(self.path, timeout=45)) as conn:
            conn.execute("PRAGMA secure_delete=ON")
            with conn:
                conn.execute("BEGIN IMMEDIATE")
                yield conn

    def _key(self, conn, owner, version=None, *, create=False):
        _owner(owner)
        row = conn.execute(
            "SELECT version,provider,reference,wrapped FROM owner_keys WHERE owner=? "
            + ("AND version=?" if version is not None else "ORDER BY version DESC LIMIT 1"),
            (owner, version) if version is not None else (owner,),
        ).fetchone()
        if row is None:
            if not create:
                raise ReconnectRequired("owner key unavailable")
            return self._new_key(conn, owner)
        version, provider, reference, wrapped = row
        if (provider, reference) != (self.provider.provider_id, self.provider.key_ref):
            raise KeyServiceUnavailable("configured root does not match wrapped key")
        with self._cache_lock:
            key = self._unwrapped.get((owner, version))
            if key is None:
                key = self.provider.unwrap(wrapped, encryption_context(owner))
                self._unwrapped[(owner, version)] = key
        return version, key

    def _new_key(self, conn, owner):
        version = conn.execute("SELECT COALESCE(MAX(version),0)+1 FROM owner_keys WHERE owner=?",
                               (_owner(owner),)).fetchone()[0]
        key, wrapped = self.provider.generate(encryption_context(owner))
        if len(key) != 32:
            raise RootKeyError("invalid owner key")
        conn.execute("INSERT INTO owner_keys VALUES (?,?,?,?,?)",
                     (owner, version, self.provider.provider_id, self.provider.key_ref, wrapped))
        return version, key

    def seal(self, owner, slot, plaintext):
        if not isinstance(slot, str) or not slot:
            raise RootKeyError("custody slot required")
        with self._transaction() as conn:
            version, key = self._key(conn, owner, create=True)
        aad = _json([FORMAT, owner, slot, version])
        return _json({"format": FORMAT, "version": version,
                      "sealed": base64.b64encode(gcm_seal(key, plaintext, aad)).decode()})

    def open(self, owner, slot, payload):
        malformed = False
        try:
            envelope = json.loads(payload)
            version = envelope["version"]
            if (set(envelope) != {"format", "version", "sealed"}
                    or envelope["format"] != FORMAT or type(version) is not int or version < 1):
                raise ValueError()
            blob = base64.b64decode(envelope["sealed"], validate=True)
        except (ValueError, KeyError, TypeError):
            malformed = True
        if malformed:
            raise ReconnectRequired("sealed custody required; conversion or reconnect needed")
        with self._transaction() as conn:
            _, key = self._key(conn, owner, version)
        failed = False
        try:
            return gcm_open(key, blob, _json([FORMAT, owner, slot, version]))
        except EnvelopeError:
            failed = True
        if failed:
            raise ReconnectRequired("custody authentication failed")

    def rotate_owner(self, owner):
        """New writes use the new version; retained backups keep opening old versions."""
        with self._transaction() as conn:
            return self._new_key(conn, owner)[0]

    def rewrap(self, replacement):
        """Offline root change: all versions move together or none do."""
        if isinstance(replacement, MemoryRootKey) and os.getenv("TINYASSETS_ENV") == "production":
            raise RootKeyError("local root refused in production")
        with self._transaction() as conn:
            for owner, version in conn.execute("SELECT owner,version FROM owner_keys").fetchall():
                _, key = self._key(conn, owner, version)
                context = encryption_context(owner)
                wrapped = replacement.wrap(key, context)
                if replacement.unwrap(wrapped, context) != key:
                    raise RootKeyError("rewrapped owner key verification failed")
                conn.execute("UPDATE owner_keys SET provider=?,reference=?,wrapped=? "
                             "WHERE owner=? AND version=?",
                             (replacement.provider_id, replacement.key_ref,
                              wrapped, owner, version))
        self.provider = replacement
        with self._cache_lock:
            self._unwrapped.clear()

    def erase(self, owner):
        owner = _owner(owner)
        with self._transaction() as conn:
            conn.execute("DELETE FROM owner_keys WHERE owner=?", (owner,))
        with self._cache_lock:
            for identity in [item for item in self._unwrapped if item[0] == owner]:
                del self._unwrapped[identity]


def memory_test_root(root):
    """Deterministic in-memory root shared by a test and its subprocesses.

    Production never reaches this. The bytes are derived from the custody root
    so a spawned broker opens what the parent sealed, and nothing is written down.
    """
    if os.getenv("TINYASSETS_ENV") == "production":
        raise RootKeyError("local root refused in production")
    provider = MemoryRootKey()
    provider._root = hashlib.sha256(
        b"synthetic-test-only:" + str(Path(root).resolve()).encode()
    ).digest()
    return provider


def _configured_provider(root):
    if os.getenv("TINYASSETS_ENV") != "production" and os.getenv(TEST_ROOT_ENV) == "memory":
        return memory_test_root(root)
    try:
        info = KMS_CONFIG.stat()
        if os.name == "posix" and (info.st_mode & 0o077 or info.st_uid != os.geteuid()):
            raise ValueError()
        config = json.loads(KMS_CONFIG.read_bytes())
        return AwsKmsRootKey(**config)
    except (OSError, ValueError, TypeError, RootKeyError):
        raise KeyServiceUnavailable("broker KMS configuration unavailable")


def service_for(root):
    """Only broker entry points call this. No automatic local key or plaintext fallback."""
    root = Path(root).resolve()
    with _lock:
        if root not in _services:
            _services[root] = KeyService(root, _configured_provider(root))
        return _services[root]


def seal_document(namespace, slot, plaintext):
    from tinyassets.credential_vault import custody_data_root

    return service_for(custody_data_root(namespace)).seal(Path(namespace).name, slot, plaintext)


def open_document(namespace, slot, payload):
    from tinyassets.credential_vault import custody_data_root

    return service_for(custody_data_root(namespace)).open(Path(namespace).name, slot, payload)


def erase_owner_keys(root, owner):
    """Erasure needs no KMS access and never creates a missing key ledger."""
    path = Path(root) / ".broker/vault-keys.db"
    if not path.exists():
        return
    with closing(sqlite3.connect(path, timeout=45)) as conn:
        conn.execute("PRAGMA secure_delete=ON")
        with conn:
            conn.execute("DELETE FROM owner_keys WHERE owner=?", (_owner(owner),))
