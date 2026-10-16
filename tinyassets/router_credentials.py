"""Router-held hook token -> owner map. No branch, payload or owner-store reads.

The cutover seeds digests from existing hooks. Box token publication uses this
same map through the shared-state broker seam; delivery never opens hook rows.
"""
from __future__ import annotations

import hashlib
import sqlite3
from contextlib import closing
from pathlib import Path


class HookOwners:
    def __init__(self, root: Path):
        self.path = root / ".hook-owners.sqlite3"

    def publish(self, token_hash: str, owner: str):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS hook_owners ("
                       "token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL)")
            db.execute("INSERT INTO hook_owners VALUES (?, ?)", (token_hash, owner))

    def revoke(self, token_hash: str):
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("DELETE FROM hook_owners WHERE token_hash=?", (token_hash,))

    def resolve(self, token: str) -> str | None:
        if not self.path.exists():
            return None
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            row = db.execute("SELECT owner FROM hook_owners WHERE token_hash=?",
                             (hashlib.sha256(token.encode()).hexdigest(),)).fetchone()
        return row[0] if row else None


def hook_identity(token: str):
    from tinyassets.auth.provider import Identity
    from tinyassets.storage import data_dir
    from tinyassets.webhook_inbound import inbound_enabled

    if not inbound_enabled():
        return None
    owner = HookOwners(data_dir()).resolve(token)
    return Identity(user_id=owner, username=owner) if owner else None
