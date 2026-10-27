"""Message bodies in the trusted zone, never on a box's mount (ADR-025, S30).

One broker-private SQLite per owner beside ``policy.db``. The ledger
(``message_ledger.py``) is the truth: a body row is written first, so a body
whose ledger commit never happened is an orphan nobody reads. A read re-checks
the ledger's hash; a mismatched body is withheld and flagged, never returned
as clean text.
"""
from __future__ import annotations

import hashlib
import time
from contextlib import closing
from pathlib import Path

from tinyassets.broker.policy import account_path, erase_account_db, open_account_db

FILENAME = "conversations.db"
MAX_BODY = 16384


def digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def erase(data_root: Path, owner: str) -> int:
    return erase_account_db(account_path(data_root, owner, FILENAME))


class Conversations:
    def __init__(self, data_root: Path, admissions) -> None:
        self._root = Path(data_root)
        self._admissions = admissions

    def _connect(self, owner: str):
        db = open_account_db(account_path(self._root, owner, FILENAME), self._admissions, owner)
        db.execute("CREATE TABLE IF NOT EXISTS message_bodies ("
                   "message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, "
                   "body TEXT NOT NULL CHECK(length(body) <= 16384), created_at REAL NOT NULL)")
        return closing(db)

    def put(self, owner: str, message_id: str, room_id: str, body: str) -> None:
        """Before the ledger commit. Replaces only an orphan: the ledger has no row yet."""
        if not isinstance(body, str) or not body or len(body) > MAX_BODY:
            raise ValueError(f"a message body is 1 to {MAX_BODY} characters")
        with self._connect(owner) as db:
            db.execute("INSERT OR REPLACE INTO message_bodies VALUES (?,?,?,?)",
                       (message_id, room_id, body, time.time()))

    def bodies(self, owner: str, expected: dict[str, str]) -> dict[str, dict]:
        """``{message_id: {body, integrity}}``; integrity is ``ok``, ``tampered`` or ``missing``."""
        found = {}
        if expected:
            with self._connect(owner) as db:
                marks = ",".join("?" * len(expected))
                found = dict(db.execute(f"SELECT message_id, body FROM message_bodies "
                                        f"WHERE message_id IN ({marks})", list(expected)))
        result = {}
        for message_id, sha in expected.items():
            body = found.get(message_id)
            if body is None:
                result[message_id] = {"body": None, "integrity": "missing"}
            elif digest(body) != sha:
                result[message_id] = {"body": None, "integrity": "tampered"}
            else:
                result[message_id] = {"body": body, "integrity": "ok"}
        return result
