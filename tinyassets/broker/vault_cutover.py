"""Offline conversion of shared broker custody only. Never reads an owner volume."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from tinyassets.broker.admission import digest
from tinyassets.broker.subscription_custody import FILENAME
from tinyassets.broker.vault import _fsync_directory, _persist_role_vault
from tinyassets.broker.vault_backup import FORMAT
from tinyassets.credential_vault import VAULT_FILENAME, _records_from_payload


def _seal_verified(keys, owner, slot, raw):
    sealed = keys.seal(owner, slot, raw)
    if keys.open(owner, slot, sealed) != raw:
        raise ValueError("custody conversion verification failed")
    return sealed


def convert(root, *, legacy_browser_keys):
    # Legacy parsers and cryptographic exceptions can retain secret input.
    failed = False
    try:
        return _convert(root, legacy_browser_keys=legacy_browser_keys)
    except Exception:
        failed = True
    if failed:
        raise RuntimeError("offline custody conversion failed; originals not yet replaced remain")


def _convert(root, *, legacy_browser_keys):
    """Caller stops all broker writers and verifies a full backup first.

    Publication is restartable per document. No legacy bytes are removed before
    the sealed replacement is opened and compared. Legacy backup rewrite is a
    separate founder operation; this function does not delete backup objects.
    """
    from tinyassets.broker.key_service import service_for

    root = Path(root)
    keys = service_for(root)
    count = 0
    accounts = root / ".broker/accounts"
    for path in accounts.glob("*/credentials/*"):
        if path.name not in {VAULT_FILENAME, FILENAME}:
            continue
        owner = path.parent.parent.name
        raw = path.read_bytes()
        document = json.loads(raw)
        if isinstance(document, dict) and document.get("format") == FORMAT:
            keys.open(owner, path.name, raw)
            continue
        if path.name == VAULT_FILENAME:
            _records_from_payload(document)
        else:
            from tinyassets.broker.subscription_custody import validate_state

            for entry in document.values():
                validate_state(entry["state"])
        sealed = _seal_verified(keys, owner, path.name, raw)
        if path.read_bytes() != raw:
            raise RuntimeError("custody changed during offline conversion")
        _persist_role_vault(path, sealed.decode())
        count += 1
    ledger = root / ".broker/outbound.db"
    if ledger.exists():
        with closing(sqlite3.connect(ledger)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='browser_vault'").fetchone():
                with conn:
                    conn.execute("BEGIN IMMEDIATE")
                    for ident, principal, revision, blob in conn.execute(
                        "SELECT id,owner,revision,sealed FROM browser_vault WHERE length(sealed)>0"
                    ).fetchall():
                        owner = digest(principal)
                        slot = f"browser/{ident}/{revision}"
                        # The sealed format is JSON; legacy blobs are nonce+ciphertext.
                        try:
                            envelope = json.loads(blob)
                        except (ValueError, UnicodeError):
                            envelope = None
                        if isinstance(envelope, dict) and envelope.get("format") == FORMAT:
                            keys.open(owner, slot, blob)
                            continue
                        raw_key = (Path(legacy_browser_keys) / (owner + ".key")).read_bytes()
                        aad = json.dumps([1, principal, ident, revision]).encode()
                        raw = AESGCM(raw_key).decrypt(blob[:12], blob[12:], aad)
                        sealed = _seal_verified(keys, owner, slot, raw)
                        conn.execute("UPDATE browser_vault SET sealed=? WHERE id=?",
                                     (sealed, ident))
                        count += 1
                # Database commit precedes destruction; restarts verify sealed rows above.
                owners = [digest(row[0]) for row in conn.execute(
                    "SELECT DISTINCT owner FROM browser_vault")]
                for owner in owners:
                    path = Path(legacy_browser_keys) / (owner + ".key")
                    path.unlink(missing_ok=True)
                if Path(legacy_browser_keys).exists():
                    _fsync_directory(Path(legacy_browser_keys))
    return count
