"""Message bodies and collections in the trusted zone, never on a box's mount (ADR-025).

One broker-private SQLite per owner beside ``policy.db``. The ledger
(``message_ledger.py``) is the truth: a body row is written first, so a body
whose ledger commit never happened is an orphan nobody reads. A read re-checks
the ledger's hash; a mismatched body is withheld and flagged, never returned
as clean text.

``collection_rows`` is the repo's one revision primitive (S34): rows keyed by
``(collection, row_id)``, each write a compare-and-swap on the row's etag, each
commit stamped with the collection's next ``seq`` so a reader pulls after a
cursor. The board is ``collection='board'``; a Tier A app room is
``collection='room:<app>:<room>'``. Payloads are stored and returned verbatim.
A private row is visible to, and writable by, its writer alone.
"""
from __future__ import annotations

import hashlib
import time
from contextlib import closing
from pathlib import Path

from tinyassets.broker.policy import account_path, erase_account_db, open_account_db

FILENAME = "conversations.db"
MAX_BODY = 16384
MAX_ROW = 65536
MAX_ROWS = 1000
MAX_PULL = 100
_ROWS = ("CREATE TABLE IF NOT EXISTS collection_rows ("
         "collection TEXT NOT NULL, row_id TEXT NOT NULL, revision INTEGER NOT NULL, "
         "seq INTEGER NOT NULL, etag TEXT NOT NULL, writer TEXT NOT NULL, "
         "private INTEGER NOT NULL CHECK(private IN (0,1)), "
         "body TEXT NOT NULL CHECK(length(body) <= 65536), updated_at REAL NOT NULL, "
         "PRIMARY KEY(collection, row_id), UNIQUE(collection, seq))")


class Conflict(Exception):
    """The expected etag is not the row's; ``current`` is the row as stored, or None."""

    def __init__(self, current: dict | None):
        super().__init__("conflict")
        self.current = current


def digest(body: str) -> str:
    return hashlib.sha256(body.encode()).hexdigest()


def erase(data_root: Path, owner: str) -> int:
    return erase_account_db(account_path(data_root, owner, FILENAME))


def _name(value, what: str) -> str:
    if not isinstance(value, str) or not value or len(value) > 256 or not value.isprintable():
        raise ValueError(f"{what} is 1 to 256 printable characters")
    return value


def _row(row) -> dict:
    return {"row_id": row["row_id"], "revision": row["revision"], "etag": row["etag"],
            "seq": row["seq"], "writer": row["writer"], "private": bool(row["private"]),
            "body": row["body"]}


class Conversations:
    def __init__(self, data_root: Path, admissions) -> None:
        self._root = Path(data_root)
        self._admissions = admissions

    def _connect(self, owner: str):
        db = open_account_db(account_path(self._root, owner, FILENAME), self._admissions, owner)
        db.execute("CREATE TABLE IF NOT EXISTS message_bodies ("
                   "message_id TEXT PRIMARY KEY, room_id TEXT NOT NULL, "
                   "body TEXT NOT NULL CHECK(length(body) <= 16384), created_at REAL NOT NULL)")
        db.execute(_ROWS)
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

    # -- collections: the one revision primitive (S34) -----------------------

    def write(self, owner: str, *, caller: str, collection: str, row_id: str, body: str,
              expected: str, private: bool) -> dict:
        """Commit ``body`` only while ``expected`` is the row's etag; ``""`` creates the row.

        A mismatch raises :class:`Conflict` with the row as stored and changes nothing.
        """
        _name(collection, "collection")
        _name(row_id, "row_id")
        if not isinstance(body, str) or len(body) > MAX_ROW:
            raise ValueError(f"a row body is text of at most {MAX_ROW} characters")
        if not isinstance(expected, str) or type(private) is not bool:
            raise ValueError("invalid collection write")
        with self._connect(owner) as db:
            db.execute("BEGIN IMMEDIATE")  # one writer at a time across connections
            try:
                row = db.execute("SELECT * FROM collection_rows WHERE collection=? AND row_id=?",
                                 (collection, row_id)).fetchone()
                if row is not None and row["private"] and row["writer"] != caller:
                    raise PermissionError("a private row is its writer's own")
                if row is not None and bool(row["private"]) != private:
                    raise ValueError("a row keeps the privacy it was created with")
                if (row["etag"] if row is not None else "") != expected:
                    raise Conflict(None if row is None else _row(row))
                count, top = db.execute("SELECT COUNT(*), COALESCE(MAX(seq),0) FROM "
                                        "collection_rows WHERE collection=?",
                                        (collection,)).fetchone()
                if row is None and count >= MAX_ROWS:
                    raise PermissionError("the collection is full")
                seq = top + 1
                db.execute("INSERT OR REPLACE INTO collection_rows VALUES (?,?,?,?,?,?,?,?,?)",
                           (collection, row_id, 1 if row is None else row["revision"] + 1, seq,
                            digest(f"{seq}:{body}"), caller, int(private), body, time.time()))
                saved = db.execute("SELECT * FROM collection_rows WHERE collection=? AND "
                                   "row_id=?", (collection, row_id)).fetchone()
                db.execute("COMMIT")
            except BaseException:
                db.execute("ROLLBACK")
                raise
        return _row(saved)

    def pull(self, owner: str, *, caller: str, collection: str, after: int, limit: int) -> dict:
        """Rows past ``after`` this caller may see, in ``seq`` order, and the next cursor.

        The cursor is the collection's top when the page holds everything, so a
        reader never rescans; otherwise it is the page's last ``seq``.
        """
        _name(collection, "collection")
        if type(after) is not int or after < 0:
            raise ValueError("after is a collection seq")
        if type(limit) is not int or not 0 < limit <= MAX_PULL:
            raise ValueError(f"limit is 1 to {MAX_PULL}")
        with self._connect(owner) as db:
            top = db.execute("SELECT COALESCE(MAX(seq),0) FROM collection_rows WHERE "
                             "collection=?", (collection,)).fetchone()[0]
            rows = db.execute("SELECT * FROM collection_rows WHERE collection=? AND seq>? AND "
                              "seq<=? AND (private=0 OR writer=?) ORDER BY seq LIMIT ?",
                              (collection, after, top, caller, limit)).fetchall()
        return {"cursor": rows[-1]["seq"] if len(rows) == limit else top,
                "rows": [_row(row) for row in rows]}


def router_operation(conversations: Conversations, doc: dict) -> dict:
    """``COLLECTION`` on the router's owner channel (S34).

    ``principal`` is whose store and ``caller`` the router-verified account: the
    router picks both itself (a published app's room is its publisher's), never
    from a field of the client's request. A lost compare-and-swap is an answer,
    ``{"conflict": current}``, not a refusal.
    """
    from tinyassets.broker.admission import require_admitted
    from tinyassets.broker.owner_identities import validate_principal

    action, owner, caller = doc.get("action"), doc.get("principal"), doc.get("caller")
    validate_principal(owner)
    validate_principal(caller)
    require_admitted(conversations._admissions, owner)
    fields = {k: v for k, v in doc.items() if k not in {"op", "action", "principal", "caller"}}
    if action == "write" and set(fields) == {"collection", "row_id", "body", "expected",
                                             "private"}:
        try:
            return {"row": conversations.write(owner, caller=caller, **fields)}
        except Conflict as exc:
            return {"conflict": exc.current}
    if action == "pull" and set(fields) == {"collection", "after", "limit"}:
        return conversations.pull(owner, caller=caller, **fields)
    raise ValueError("invalid collection request")
