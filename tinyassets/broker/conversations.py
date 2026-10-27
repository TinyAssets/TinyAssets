"""Message bodies and collections in the trusted zone, never on a box's mount (ADR-025).

One broker-private SQLite per owner beside ``policy.db``. The ledger
(``message_ledger.py``) is the truth: a body row is written first, so a body
whose ledger commit never happened is an orphan nobody reads. A read re-checks
the ledger's hash; a mismatched body is withheld and flagged, never returned
as clean text.

``collection_rows`` is the repo's one revision primitive (S34): rows keyed by
``(collection, row_id)``, each write a compare-and-swap on the row's etag, each
commit stamped with the collection's next ``seq`` so a reader pulls after a
cursor, or gets one row by its key. The board is ``collection='board'``; a Tier A app room is
``collection='room:<app>:<room>'``. Payloads are stored and returned verbatim.
A private row is visible to, and writable by, its writer alone. Rows and payload
bytes are bounded per writer (other than the owner) in a collection, per
collection and per owner; past a bound a write is refused. Only the owner
clears a collection, or one writer's rows in it.
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
#: Bounds on payload bytes and rows, each refused loudly. The broker holds collections
#: outside any volume quota, so these, not the owner's storage pool, keep it finite.
#: A writer other than the store's owner gets a small share of one collection; one
#: collection gets a slice of the owner's total, so one room cannot starve another.
MAX_WRITER_ROWS = 64
MAX_WRITER_BYTES = 512 * 1024
MAX_ROWS = 1000
MAX_COLLECTION_BYTES = 8 * 1024 * 1024
MAX_OWNER_ROWS = 10_000
MAX_OWNER_BYTES = 64 * 1024 * 1024
MAX_PULL = 100
_ROWS = ("CREATE TABLE IF NOT EXISTS collection_rows ("
         "collection TEXT NOT NULL, row_id TEXT NOT NULL, revision INTEGER NOT NULL, "
         "seq INTEGER NOT NULL, etag TEXT NOT NULL, writer TEXT NOT NULL, "
         "private INTEGER NOT NULL CHECK(private IN (0,1)), "
         "body TEXT NOT NULL CHECK(length(body) <= 65536), updated_at REAL NOT NULL, "
         # Who created the row; a takeover never changes it (the row share counts it).
         "creator TEXT, "
         "PRIMARY KEY(collection, row_id), UNIQUE(collection, seq));"
         "CREATE INDEX IF NOT EXISTS collection_writers ON collection_rows(collection, writer);"
         # The high-water seq survives a clear, so a reader's cursor never goes back.
         "CREATE TABLE IF NOT EXISTS collection_seqs ("
         "collection TEXT PRIMARY KEY, seq INTEGER NOT NULL);")
_SIZE = "SELECT COUNT(*), COALESCE(SUM(length(CAST(body AS BLOB))),0) FROM collection_rows"


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
        db.executescript(_ROWS)
        if "creator" not in {column[1] for column in db.execute(
                "PRAGMA table_info(collection_rows)")}:
            # A store from before creators were kept: each row's writer created it.
            db.execute("ALTER TABLE collection_rows ADD COLUMN creator TEXT")
            db.execute("UPDATE collection_rows SET creator=writer WHERE creator IS NULL")
        db.execute("CREATE INDEX IF NOT EXISTS collection_creators ON "
                   "collection_rows(collection, creator)")
        return closing(db)

    @staticmethod
    def _bound(db, owner: str, caller: str, collection: str, row, body: str) -> None:
        """Refuse a write that would take its writer, collection or owner past a bound."""
        size, old = len(body.encode()), 0 if row is None else len(row["body"].encode())
        add = 1 if row is None else 0
        checks = [("the owner's collections are full", "", (), MAX_OWNER_ROWS,
                   MAX_OWNER_BYTES, add, size - old),
                  ("the collection is full", " WHERE collection=?", (collection,), MAX_ROWS,
                   MAX_COLLECTION_BYTES, add, size - old)]
        if caller != owner:
            # Rows count against their creator, whoever takes them over later, so
            # neither a turn on a shared row nor a second account frees a share.
            # Bytes count against the current writer: a takeover charges the row's size.
            mine = row is not None and row["writer"] == caller
            share = "this writer's share of the collection is full"
            checks[:0] = [(share, " WHERE collection=? AND creator=?", (collection, caller),
                           MAX_WRITER_ROWS, MAX_WRITER_BYTES, add, 0),
                          (share, " WHERE collection=? AND writer=?", (collection, caller),
                           MAX_WRITER_ROWS, MAX_WRITER_BYTES, 0, size - (old if mine else 0))]
        for reason, where, params, max_rows, max_bytes, more_rows, more_bytes in checks:
            rows, used = db.execute(_SIZE + where, params).fetchone()
            if rows + more_rows > max_rows and more_rows or (
                    used + more_bytes > max_bytes and more_bytes > 0):
                raise PermissionError(reason)

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
              expected_etag: str, private: bool) -> dict:
        """Commit ``body`` only while ``expected_etag`` is the row's; ``""`` creates the row.

        A mismatch raises :class:`Conflict` with the row as stored and changes nothing.
        """
        _name(collection, "collection")
        _name(row_id, "row_id")
        if not isinstance(body, str) or len(body) > MAX_ROW:
            raise ValueError(f"a row body is text of at most {MAX_ROW} characters")
        if not isinstance(expected_etag, str) or type(private) is not bool:
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
                if (row["etag"] if row is not None else "") != expected_etag:
                    raise Conflict(None if row is None else _row(row))
                self._bound(db, owner, caller, collection, row, body)
                seq = self._top(db, collection) + 1
                db.execute("INSERT INTO collection_seqs VALUES (?,?) ON CONFLICT(collection) "
                           "DO UPDATE SET seq=excluded.seq", (collection, seq))
                # An upsert on the row's key alone: a seq collision fails loudly on
                # UNIQUE(collection, seq) instead of replacing another row.
                db.execute("INSERT INTO collection_rows (collection,row_id,revision,seq,etag,"
                           "writer,private,body,updated_at,creator) VALUES (?,?,?,?,?,?,?,?,?,?) "
                           "ON CONFLICT(collection, row_id) DO UPDATE SET "
                           "revision=excluded.revision, seq=excluded.seq, etag=excluded.etag, "
                           "writer=excluded.writer, private=excluded.private, "
                           "body=excluded.body, updated_at=excluded.updated_at",
                           (collection, row_id, 1 if row is None else row["revision"] + 1, seq,
                            digest(f"{seq}:{body}"), caller, int(private), body, time.time(),
                            caller))
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
            top = self._top(db, collection)
            rows = db.execute("SELECT * FROM collection_rows WHERE collection=? AND seq>? AND "
                              "seq<=? AND (private=0 OR writer=?) ORDER BY seq LIMIT ?",
                              (collection, after, top, caller, limit)).fetchall()
        return {"cursor": rows[-1]["seq"] if len(rows) == limit else top,
                "rows": [_row(row) for row in rows]}

    def get(self, owner: str, *, caller: str, collection: str, row_id: str) -> dict:
        """One row by its key, as ``pull`` would show it to this caller, or None."""
        _name(collection, "collection")
        _name(row_id, "row_id")
        with self._connect(owner) as db:
            row = db.execute("SELECT * FROM collection_rows WHERE collection=? AND row_id=? "
                             "AND (private=0 OR writer=?)",
                             (collection, row_id, caller)).fetchone()
        return {"row": None if row is None else _row(row)}

    def clear(self, owner: str, *, collection: str, writer: str | None) -> dict:
        """Delete a collection's rows, or every row ``writer`` created or last wrote.

        The owner's act alone (the router's). Matching creator or writer removes an
        abuser's rows even after another account took them over.
        """
        _name(collection, "collection")
        if writer is not None:
            _name(writer, "writer")
        with self._connect(owner) as db:
            deleted = db.execute(
                "DELETE FROM collection_rows WHERE collection=?" + (
                    "" if writer is None else " AND (writer=? OR creator=?)"),
                (collection,) if writer is None else (collection, writer, writer)).rowcount
        return {"deleted": deleted}

    @staticmethod
    def _top(db, collection: str) -> int:
        """The collection's high-water seq: never below a stored row's, never lowered by a clear."""
        return db.execute("SELECT MAX(COALESCE((SELECT seq FROM collection_seqs WHERE "
                          "collection=?),0), COALESCE((SELECT MAX(seq) FROM collection_rows "
                          "WHERE collection=?),0))", (collection, collection)).fetchone()[0]


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
    if action == "write" and set(fields) == {"collection", "row_id", "body", "expected_etag",
                                             "private"}:
        try:
            return {"row": conversations.write(owner, caller=caller, **fields)}
        except Conflict as exc:
            return {"conflict": exc.current}
    if action == "pull" and set(fields) == {"collection", "after", "limit"}:
        return conversations.pull(owner, caller=caller, **fields)
    if action == "get" and set(fields) == {"collection", "row_id"}:
        return conversations.get(owner, caller=caller, **fields)
    if action == "clear" and set(fields) == {"collection", "writer"}:
        if caller != owner:
            raise PermissionError("only the owner clears a collection")
        return conversations.clear(owner, **fields)
    raise ValueError("invalid collection request")
