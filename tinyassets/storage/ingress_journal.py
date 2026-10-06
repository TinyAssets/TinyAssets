"""Dormant durable acceptance seam for replaceable frontends (design D2).

No public route, pump, owner lease or execution authority is installed here.
Provision this separately versioned root OUTSIDE the runtime layout domain.
Trusted adapters supply authenticated scopes, a current-authority context
manager (held through each operation), and existing admission policy checks.
Never put bearer credentials in the payload. Success is returned after commit.

An importer reserves admission only, in the runtime transaction; it must never
execute an effect. Its mapping commits with admission, before this journal is
acknowledged. Lost acknowledgements therefore replay lookup, not execution.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
import time
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path


class Conflict(ValueError):
    """A stable identity was reused with different bytes or a different receipt."""


class Expired(ValueError):
    """A terminal identity remains a tombstone; it may never execute again."""


@dataclass(frozen=True)
class Scope:
    principal_id: str
    command_center_id: str
    thread_id: str
    operation: str = "converse"

    def values(self):
        values = (self.principal_id, self.command_center_id, self.thread_id, self.operation)
        if any(not isinstance(v, str) or not v or len(v) > 512 for v in values):
            raise ValueError("invalid authenticated ingress scope")
        return values


@dataclass(frozen=True)
class Envelope:
    ingress_id: str
    scope: Scope
    client_send_id: str
    digest: str
    payload: bytes
    state: str
    runtime_id: str | None


_SCHEMA = """
CREATE TABLE requests (
 ingress_id TEXT PRIMARY KEY, principal_id TEXT NOT NULL,
 command_center_id TEXT NOT NULL, thread_id TEXT NOT NULL, operation TEXT NOT NULL,
 client_send_id TEXT NOT NULL, digest TEXT NOT NULL, payload BLOB,
 state TEXT NOT NULL CHECK(state IN ('pending','imported','terminal','expired')),
 runtime_id TEXT, accepted_at REAL NOT NULL, terminal_at REAL,
 UNIQUE(principal_id,command_center_id,thread_id,operation,client_send_id)
);
CREATE TABLE events (
 ingress_id TEXT NOT NULL REFERENCES requests(ingress_id), sequence INTEGER NOT NULL,
 payload BLOB NOT NULL, terminal INTEGER NOT NULL CHECK(terminal IN (0,1)),
 PRIMARY KEY(ingress_id,sequence)
);
PRAGMA user_version=1;
"""


def initialize(root: Path):
    """Explicit schema provisioning only; normal opens never create or migrate."""
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root.chmod(0o700)
    path = root / "ingress-v1.sqlite3"
    with closing(sqlite3.connect(path)) as conn, conn:
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if version == 0:
            conn.executescript("BEGIN IMMEDIATE;" + _SCHEMA + "COMMIT;")
        elif version != 1:
            raise RuntimeError("incompatible ingress journal")
        conn.execute("PRAGMA journal_mode=WAL")
    path.chmod(0o600)


class IngressJournal:
    def __init__(self, root, *, authority, admission_policy, max_payload_bytes=1024 * 1024):
        self.path = Path(root).resolve() / "ingress-v1.sqlite3"
        self.authority = authority
        self.admission_policy = admission_policy
        self.max_payload_bytes = max_payload_bytes

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("PRAGMA synchronous=FULL")
            if conn.execute("PRAGMA user_version").fetchone()[0] != 1:
                raise RuntimeError("incompatible ingress journal")
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.commit()
        finally:
            conn.close()  # rolls back on every error, including failed commit

    @staticmethod
    def _key(key):
        if not isinstance(key, str) or re.fullmatch(r"[A-Za-z0-9_-]{1,128}", key) is None:
            raise ValueError("invalid client_send_id")
        return key

    def _row(self, conn, scope, key):
        return conn.execute(
            "SELECT * FROM requests WHERE principal_id=? AND command_center_id=? "
            "AND thread_id=? AND operation=? AND client_send_id=?",
            (*scope.values(), self._key(key)),
        ).fetchone()

    @staticmethod
    def _envelope(row):
        if row is None:
            raise LookupError("ingress receipt unavailable")
        if row["state"] == "expired":
            raise Expired("client_send_id expired; do not resubmit")
        return Envelope(row["ingress_id"], Scope(*(row[k] for k in (
            "principal_id", "command_center_id", "thread_id", "operation"))),
            row["client_send_id"], row["digest"], row["payload"], row["state"], row["runtime_id"])

    def accept(self, scope, client_send_id, payload):
        scope.values()
        self._key(client_send_id)
        if type(payload) is not bytes or len(payload) > self.max_payload_bytes:
            raise ValueError("invalid or oversized ingress payload")
        digest = hashlib.sha256(payload).hexdigest()
        with self.authority(scope), self._connection() as conn:
            row = self._row(conn, scope, client_send_id)
            if row is not None:
                envelope = self._envelope(row)
                if row["digest"] != digest:
                    raise Conflict("client_send_id payload conflict")
            else:
                # Pure rate/size/storage/consent CHECKS belong to the adapter;
                # no external charging here: a failed commit must not consume
                # a quota outside this transaction.
                self.admission_policy(scope, payload)
                conn.execute("INSERT INTO requests VALUES (?,?,?,?,?,?,?,?,?,?,?,?)", (
                    str(uuid.uuid4()), *scope.values(), client_send_id, digest, payload,
                    "pending", None, time.time(), None,
                ))
                envelope = self._envelope(self._row(conn, scope, client_send_id))
        return envelope

    def receipt(self, scope, client_send_id):
        with self.authority(scope), self._connection() as conn:
            return self._envelope(self._row(conn, scope, client_send_id))

    def pending(self, scope):
        """Scoped durable work list; polling/wake hints are not authoritative."""
        with self.authority(scope), self._connection() as conn:
            return [self._envelope(row) for row in conn.execute(
                "SELECT * FROM requests WHERE principal_id=? AND command_center_id=? "
                "AND thread_id=? AND operation=? AND state='pending' "
                "ORDER BY accepted_at,ingress_id",
                scope.values(),
            )]

    def replay(self, scope, client_send_id, importer):
        """Importer returns only AFTER its admission+mapping transaction commits.

        The platform owner will call this under S8b; no competing lease here.
        Revocation is rechecked and held through import and receipt transfer.
        """
        with self.authority(scope):
            with self._connection() as conn:
                envelope = self._envelope(self._row(conn, scope, client_send_id))
            if envelope.state != "pending":
                return envelope.runtime_id
            runtime_id = importer(envelope)
            if not isinstance(runtime_id, str) or not runtime_id:
                raise ValueError("importer did not return committed admission identity")
            with self._connection() as conn:
                row = self._row(conn, scope, client_send_id)
                if row["runtime_id"] not in (None, runtime_id):
                    raise Conflict("importer returned a different admission")
                conn.execute("UPDATE requests SET runtime_id=?,state='imported' "
                             "WHERE ingress_id=? AND state='pending'",
                             (runtime_id, envelope.ingress_id))
            return runtime_id

    def append(self, scope, client_send_id, sequence, payload, *, terminal=False):
        """Persist a producer's stable sequence BEFORE publishing; ack loss is safe."""
        if type(sequence) is not int or sequence < 1 or type(terminal) is not bool:
            raise ValueError("invalid event sequence/terminal flag")
        if type(payload) is not bytes or len(payload) > self.max_payload_bytes:
            raise ValueError("invalid or oversized reply event")
        with self.authority(scope), self._connection() as conn:
            envelope = self._envelope(self._row(conn, scope, client_send_id))
            existing = conn.execute("SELECT payload,terminal FROM events "
                                    "WHERE ingress_id=? AND sequence=?",
                                    (envelope.ingress_id, sequence)).fetchone()
            if existing:
                if (existing["payload"], existing["terminal"]) != (payload, int(terminal)):
                    raise Conflict("reply sequence conflict")
                return
            if envelope.state != "imported":
                raise Conflict("reply requires imported nonterminal admission")
            last = conn.execute("SELECT coalesce(max(sequence),0) FROM events WHERE ingress_id=?",
                                (envelope.ingress_id,)).fetchone()[0]
            if sequence != last + 1:
                raise Conflict("reply sequence gap")
            conn.execute("INSERT INTO events VALUES (?,?,?,?)",
                         (envelope.ingress_id, sequence, payload, int(terminal)))
            if terminal:
                conn.execute("UPDATE requests SET state='terminal',terminal_at=? "
                             "WHERE ingress_id=?",
                             (time.time(), envelope.ingress_id))

    def events(self, scope, client_send_id, *, after=0):
        if type(after) is not int or after < 0:
            raise ValueError("invalid reply cursor")
        with self.authority(scope), self._connection() as conn:
            envelope = self._envelope(self._row(conn, scope, client_send_id))
            return [dict(row) for row in conn.execute(
                "SELECT sequence,payload,terminal FROM events WHERE ingress_id=? "
                "AND sequence>? ORDER BY sequence", (envelope.ingress_id, after))]

    def expire(self, scope, client_send_id, *, terminal_before):
        """Custody policy supplies cutoff. Keep identity+digest forever for now.

        Never delete nonterminal work. A later principal-erasure integration is
        required before public enablement; do not silently drop dedupe keys.
        """
        with self.authority(scope), self._connection() as conn:
            row = self._row(conn, scope, client_send_id)
            self._envelope(row)
            if row["state"] != "terminal" or row["terminal_at"] >= terminal_before:
                return False
            conn.execute("DELETE FROM events WHERE ingress_id=?", (row["ingress_id"],))
            conn.execute("UPDATE requests SET state='expired',payload=NULL WHERE ingress_id=?",
                         (row["ingress_id"],))
            return True


def initialize_imports(conn):
    """Explicit runtime schema provisioning alongside existing admission tables."""
    conn.execute("CREATE TABLE IF NOT EXISTS ingress_imports ("
                 "ingress_id TEXT PRIMARY KEY, scope_digest TEXT NOT NULL, "
                 "payload_digest TEXT NOT NULL, runtime_id TEXT NOT NULL UNIQUE, "
                 "runtime_principal_id TEXT NOT NULL, runtime_center_id TEXT NOT NULL, "
                 "runtime_session_id TEXT NOT NULL, runtime_operation TEXT NOT NULL "
                 "CHECK(runtime_operation='converse'))")
    columns = {r[1] for r in conn.execute("PRAGMA table_info(ingress_imports)")}
    if columns != {"ingress_id", "scope_digest", "payload_digest", "runtime_id",
                   "runtime_principal_id", "runtime_center_id", "runtime_session_id",
                   "runtime_operation"}:
        raise RuntimeError("incompatible runtime ingress mapping; explicit migration required")


def import_in_transaction(conn, runtime_scope, envelope, reserve):
    """Reserve canonical conversation admission under its actual authorized scope.

    Caller holds current authority and owns BEGIN IMMEDIATE/commit. ``reserve``
    writes admission in this exact connection; no network/effects/own commit.
    This first adapter supports only the canonical principal thread/converse.
    Other threads/operations require their own authorized runtime adapter; never
    alias them into the canonical session. No execution queue is introduced.
    """
    import json

    from tinyassets.storage import conversation_run_admissions as cr

    cr._transaction(conn, runtime_scope)
    runtime_identity = (runtime_scope.owner, runtime_scope.universe,
                        runtime_scope.session, "converse")
    if envelope.scope.values() != runtime_identity:
        raise PermissionError("ingress identity does not match authorized runtime scope")
    scope_digest = hashlib.sha256(json.dumps(
        [*envelope.scope.values(), envelope.client_send_id], ensure_ascii=False,
    ).encode()).hexdigest()
    if hashlib.sha256(envelope.payload).hexdigest() != envelope.digest:
        raise Conflict("ingress payload digest mismatch")
    row = conn.execute("SELECT scope_digest,payload_digest,runtime_id,runtime_principal_id,"
                       "runtime_center_id,runtime_session_id,runtime_operation "
                       "FROM ingress_imports "
                       "WHERE ingress_id=?", (envelope.ingress_id,)).fetchone()
    if row:
        if ((row[0], row[1]) != (scope_digest, envelope.digest)
                or tuple(row[3:]) != runtime_identity):
            raise Conflict("ingress import identity conflict")
        cr._read(conn, runtime_scope, row[2])
        return row[2]
    runtime_id = reserve(conn, envelope)
    if not isinstance(runtime_id, str) or not runtime_id or not conn.in_transaction:
        raise RuntimeError("reservation must remain in the admission transaction")
    # Verify the reservation actually belongs to the supplied authority, not
    # merely that an adapter returned a plausible-looking ID.
    cr._read(conn, runtime_scope, runtime_id)
    conn.execute("INSERT INTO ingress_imports VALUES (?,?,?,?,?,?,?,?)",
                 (envelope.ingress_id, scope_digest, envelope.digest, runtime_id,
                  *runtime_identity))
    return runtime_id
