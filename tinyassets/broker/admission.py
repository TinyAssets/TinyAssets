"""C6/T5/T9: the broker's sole owner admission record and mutation API."""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
import threading
import weakref
from contextlib import asynccontextmanager, closing
from dataclasses import dataclass
from pathlib import Path

# C6: checkout intent must commit before erasure, or observe the erased record.
transition_lock = threading.RLock()
_owners = {}
_authorities = weakref.WeakValueDictionary()


def owner_lock(path, owner):
    """C5: wake and every admission/credential transition share one owner lock."""
    with transition_lock:
        return _owners.setdefault((Path(path), owner), threading.RLock())


@asynccontextmanager
async def channel_lock(path, owner):
    """Hold the same thread-owned lifecycle lock across an async channel open."""
    lock = owner_lock(path, owner)
    loop = asyncio.get_running_loop()
    acquired = asyncio.Event()
    released = threading.Event()
    finished = asyncio.Event()

    def hold():
        with lock:
            if not released.is_set():
                loop.call_soon_threadsafe(acquired.set)
                released.wait()
        if not loop.is_closed():
            loop.call_soon_threadsafe(finished.set)

    threading.Thread(target=hold, daemon=True).start()
    try:
        await acquired.wait()
        yield
    finally:
        released.set()
        # A cancelled waiter must not delay withdrawal while it owns the lock.
        if acquired.is_set():
            await finished.wait()


def register_authority(path, authority):
    _authorities[Path(path)] = authority


def changed(path, owner):
    authority = _authorities.get(Path(path))
    if authority is not None:
        authority.changed(owner)


class OwnerSuspended(PermissionError):
    """Work admission is suspended; authentication remains available."""


class OwnerUnadmitted(PermissionError, LookupError):
    """A missing admission is both a permission and a route-lookup refusal."""


class OwnerErased(OwnerUnadmitted):
    """Terminal refusal, including callers that resolve missing routes."""


@dataclass(frozen=True)
class OwnerAdmissionRecord:
    state: str
    reasons: frozenset[str]

    def require_work(self):
        if self.state == "suspended":
            raise OwnerSuspended("owner account is suspended")
        if self.state == "erased":
            raise OwnerErased("owner account is deleted (erased)")
        if self.state != "admitted":
            raise OwnerUnadmitted("owner has no admission")


def schema(db):
    db.execute("CREATE TABLE IF NOT EXISTS owner_admission ("
               "digest TEXT PRIMARY KEY NOT NULL, "
               "state TEXT NOT NULL CHECK(state IN ('admitted','suspended','erased')), "
               "reasons TEXT NOT NULL)")


def digest(owner):
    from tinyassets.broker.owner_identities import validate_principal

    validate_principal(owner)
    return hashlib.sha256(owner.encode()).hexdigest()


def admission(db, owner):
    """Every work question has the same answer, independent of centers."""
    row = db.execute("SELECT state,reasons FROM owner_admission WHERE digest=?",
                     (digest(owner),)).fetchone()
    if row is None:
        return OwnerAdmissionRecord("absent", frozenset())
    state, raw = row
    reasons = json.loads(raw)
    if (not isinstance(reasons, list) or any(r not in {"billing", "abuse"} for r in reasons)
            or state not in {"admitted", "suspended", "erased"}
            or (state == "suspended") != bool(reasons)):
        raise ValueError("invalid owner admission record")
    return OwnerAdmissionRecord(state, frozenset(reasons))


def mutate(db, owner, action, *, reason=None, suspended=None, erased_digest=None):
    """Only writer; callers own the identity-database transaction.

    Digest-only erasures preserve pre-cutover tombstones without inventing an
    identity. Runtime writers always supply the authenticated owner.
    """
    key = digest(owner) if owner is not None else erased_digest
    if (not isinstance(key, str) or len(key) != 64
            or any(c not in "0123456789abcdef" for c in key)):
        raise ValueError("invalid owner admission key")
    if owner is None and action != "erase":
        raise ValueError("only migration erasures may omit identity")
    row = db.execute("SELECT state,reasons FROM owner_admission WHERE digest=?", (key,)).fetchone()
    if action == "erase":
        state, reasons = "erased", set()
    elif action == "admit":
        if row is not None:
            if row[0] == "erased":
                raise OwnerErased("owner account is deleted (erased)")
            return
        state, reasons = "admitted", set()
    elif action == "suspend":
        if reason not in {"billing", "abuse"} or type(suspended) is not bool:
            raise ValueError("invalid suspension source")
        if row is None:
            raise LookupError("owner admission is missing")
        if row[0] == "erased":
            return
        reasons = set(json.loads(row[1]))
        if suspended:
            reasons.add(reason)
        else:
            reasons.discard(reason)
        state = "suspended" if reasons else "admitted"
    else:
        raise ValueError("invalid admission transition")
    db.execute("INSERT INTO owner_admission VALUES (?,?,?) ON CONFLICT(digest) "
               "DO UPDATE SET state=excluded.state,reasons=excluded.reasons",
               (key, state, json.dumps(sorted(reasons))))


def read(identities, owner):
    if identities is None:
        raise PermissionError("owner admission authority unavailable")
    with closing(identities._connect(write=False)) as db:
        return admission(db, owner)


def require_admitted(identities, owner):
    read(identities, owner).require_work()


def local_read(root, owner):
    """Broker-internal read; other processes use the authenticated protocol."""
    path = Path(root) / ".broker/state/owner-identities.db"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return admission(db, owner)


def local_mutate(path, owner, action, **arguments):
    with owner_lock(path, owner):
        with transition_lock, closing(sqlite3.connect(Path(path).as_uri() + "?mode=rw", uri=True,
                                     timeout=30)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            mutate(db, owner, action, **arguments)
        changed(path, owner)
