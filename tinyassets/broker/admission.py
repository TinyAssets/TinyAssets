"""C6/T5/T9: the broker's sole owner admission record and mutation API."""

from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import sqlite3
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

# Only the broker event loop accesses this registry. SQL transactions serialize
# the shared database; lifecycle work is serialized per owner, never globally.
_owners = {}


def owner_lock(path, owner):
    """C15: one asyncio lock, shared by lifecycle and channel admission."""
    key = (asyncio.get_running_loop(), Path(path), owner)
    return _owners.setdefault(key, asyncio.Lock())


async def owner_call(path, owner, function, *args, **kwargs):
    """Keep serialization until a worker finishes, including on cancellation."""
    async with owner_lock(path, owner):
        if inspect.iscoroutinefunction(function):
            return await function(*args, **kwargs)
        return await thread_work(function, *args, **kwargs)


async def thread_work(function, *args, **kwargs):
    """A cancelled caller must still wait for its blocking operation to finish."""
    worker = asyncio.create_task(asyncio.to_thread(function, *args, **kwargs))
    try:
        return await asyncio.shield(worker)
    except asyncio.CancelledError:
        while not worker.done():
            try:
                await asyncio.shield(worker)
            except asyncio.CancelledError:
                pass
        worker.result()
        raise


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
    # C14: a restored identity map cannot admit work before startup reconciliation.
    if identities is not None and not identities.work_ready:
        raise OwnerUnadmitted("platform recovery admission is closed")
    read(identities, owner).require_work()


def local_read(root, owner):
    """Broker-internal read; other processes use the authenticated protocol."""
    path = Path(root) / ".broker/state/owner-identities.db"
    with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
        return admission(db, owner)


async def local_mutate(path, owner, action, *, routing, **arguments):
    """Caller holds the async owner lock; database work runs on its worker."""
    if routing is None:
        raise RuntimeError("admission mutation requires routing")
    await thread_work(_local_mutate, path, owner, action, **arguments)
    return await routing.changed(owner)


def _local_mutate(path, owner, action, **arguments):
    with closing(sqlite3.connect(Path(path).as_uri() + "?mode=rw", uri=True,
                                 timeout=30)) as db, db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        mutate(db, owner, action, **arguments)
