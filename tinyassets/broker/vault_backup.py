"""Ciphertext-only backup validation, usable by the stdlib-only host backup job."""
import hashlib
import json
import re
import sqlite3
from contextlib import closing
from pathlib import Path

FORMAT = "ta-broker-custody-v1"
VAULT_FILENAME = ".credential-vault.json"
FILENAME = ".subscription-state.json"


def validate_backup(root):
    """Inspect ciphertext structure/references, never decrypt or export credentials."""
    root = Path(root)
    key_path = root / ".broker/vault-keys.db"
    versions = set()
    if key_path.exists():
        with closing(sqlite3.connect(key_path.as_uri() + "?mode=ro", uri=True)) as conn:
            versions = set(conn.execute("SELECT owner,version FROM owner_keys"))

    def check(owner, payload):
        import base64

        if not re.fullmatch("[a-f0-9]{64}", owner):
            raise ValueError("backup owner digest invalid")
        failed = False
        try:
            document = json.loads(payload)
            if (set(document) != {"format", "version", "sealed"}
                    or document["format"] != FORMAT
                    or type(document["version"]) is not int
                    or (owner, document["version"]) not in versions
                    or len(base64.b64decode(document["sealed"], validate=True)) < 28):
                raise ValueError()
        except (ValueError, TypeError, KeyError):
            failed = True
        if failed:
            raise ValueError("backup contains unsealed or incomplete broker custody")

    for path in (root / ".broker/accounts").glob("*/credentials/*"):
        if path.name in {VAULT_FILENAME, FILENAME}:
            check(path.parent.parent.name, path.read_bytes())
    legacy = root / ".broker/browser-vault"
    if legacy.exists() and any(legacy.iterdir()):
        raise ValueError("backup contains legacy raw browser keys")
    ledger = root / ".broker/outbound.db"
    if ledger.exists():
        with closing(sqlite3.connect(ledger.as_uri() + "?mode=ro", uri=True)) as conn:
            if conn.execute("SELECT 1 FROM sqlite_master WHERE name='browser_vault'").fetchone():
                for owner, payload in conn.execute(
                    "SELECT owner,sealed FROM browser_vault WHERE length(sealed)>0"
                ):
                    check(hashlib.sha256(owner.encode()).hexdigest(), payload)
