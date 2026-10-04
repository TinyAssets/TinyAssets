"""Execution-owner leases: one owner commits per command center, fenced by generation.

Change ``execution-owner-lease`` (target architecture #4263 S8a, D11 as amended:
per-command-center owners plus one platform owner). This is slice B1: the lease
store, the owner TREE, and the generation every owner write is fenced on
(``tinyassets.storage.owner_fence``). Today one daemon process (plus the engine
children it spawns) is the only owner; B1 makes that explicit and provable so a
second owner process can exist later (slice C) without ever committing beside the
first.

Keys
----
``cc:<command_center_id>`` for one command center's executions; ``platform`` for
the scheduler/outbox duties (cp-scheduler's S8b seam). Each key has its own row,
generation and proof.

The owner tree, and why death is proven by locks rather than a clock
--------------------------------------------------------------------
A lease row names a TREE, not a process: the daemon process and every engine
child it spawns act for the same owner, and an engine can outlive the server
that spawned it (``engine_mcp_http`` children are ordinary subprocesses). So the
proof that an old owner can no longer act must cover every member.

Each member holds an EXCLUSIVE lock on its own member file
``<data_root>/.owner_tree/<tree_id>/<member>.lock`` for its whole life, opened by
the member itself (never inherited: ``process_liveness`` closes inherited lock
descriptors after fork). Joining takes the tree's ``.gate`` lock around creating
and locking the member file. A contender proves the tree dead by taking the gate
and then succeeding on EVERY member file; it keeps the gate until its new lease
row commits. A member that was spawned but had not locked yet blocks on the gate
and, once in, re-validates that its tree still holds the lease before acting
(round-3 refute finding 1) -- so "no member holds a lock" cannot be outrun by a
late joiner. Exclusive-only locks keep this identical on Linux (``flock``) and
Windows (``msvcrt``), which has no shared lock.

A holder that is quiet is never displaced by a clock: a paused process still
holds its member lock, so a contender waits. A tree whose files are missing is
UNKNOWN, which blocks -- it is never read as dead.

Restore
-------
After a platform restore the lease store may be behind the stores it fences.
``scripts/owner_lease_restore.py`` sets ``restore_state='in_progress'`` first;
every acquisition refuses while it is set, so an interrupted restore fails
closed. It then computes each key's high-water from the recovered lease row, every
recovered fence and every ``agent_turns.owner_generation`` (found by each store
kind's own path enumerator, independent of the catalog), writes the rows
``released`` at that high-water and clears the state in one transaction.
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Iterator

from tinyassets.singleton_lock import _lock_fd, _unlock_fd

LEASE_DB_NAME = ".owner_leases.db"
TREE_DIR = ".owner_tree"
#: Set in the owner's environment at start, so every child it spawns joins the
#: same tree. A process without it is its own (private) tree.
TREE_ENV = "TINYASSETS_OWNER_TREE"
PLATFORM_KEY = "platform"
#: The file naming the founder's member lock, inside each tree directory.
_FOUNDER = "founder"
#: The join gate inside each tree directory (under ``.owner_tree``, accounted as
#: platform bytes in ``storage_accounting.ROOT_ENTRIES``).
_GATE = ".gate"
_KEY_RE = re.compile(r"^(platform|cc:[A-Za-z0-9_.-]{1,128})$")
_TREE_RE = re.compile(r"^[0-9a-f]{32}$")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS owner_lease (
      owner_key TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation > 0),
      holder_tree TEXT NOT NULL, proof_sha256 TEXT NOT NULL,
      state TEXT NOT NULL CHECK(state IN ('open', 'released')),
      acquired_at TEXT NOT NULL, released_at TEXT)""",
    """CREATE TABLE IF NOT EXISTS restore_state (
      singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
      state TEXT NOT NULL CHECK(state IN ('none', 'in_progress')),
      started_at TEXT, manifest_json TEXT)""",
    """CREATE TABLE IF NOT EXISTS owner_store_catalog (
      store_path TEXT PRIMARY KEY, store_kind TEXT NOT NULL, registered_at TEXT NOT NULL)""",
    """CREATE TABLE IF NOT EXISTS fence_high_water (
      owner_key TEXT NOT NULL, store_path TEXT NOT NULL, generation INTEGER NOT NULL,
      PRIMARY KEY(owner_key, store_path))""",
)


class LeaseLost(RuntimeError):
    """The owner lease for this key is no longer held; the caller must not act."""


class LeaseBusy(RuntimeError):
    """Another LIVE owner holds the key; it was not taken (it is never stolen)."""


class RestoreInProgress(RuntimeError):
    """A platform restore has not finished; no owner may start until it does."""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _sha(proof: str) -> str:
    return hashlib.sha256(proof.encode("ascii")).hexdigest()


def key_for(command_center_id: str) -> str:
    key = f"cc:{command_center_id}"
    if not _KEY_RE.match(key):
        raise ValueError("invalid command center id for an owner key")
    return key


def lease_db_path(base_path: str | Path) -> Path:
    return Path(base_path) / LEASE_DB_NAME


@contextmanager
def lease_db(base_path: str | Path) -> Iterator[sqlite3.Connection]:
    path = lease_db_path(base_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30.0, isolation_level=None)
    try:
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout = 30000")
        for statement in _SCHEMA:
            conn.execute(statement)
        yield conn
    finally:
        conn.close()


# --------------------------------------------------------------------------- #
# Locks
# --------------------------------------------------------------------------- #


def _try_lock(path: Path) -> int | None:
    """Open (creating) and exclusively lock ``path``; the fd, or None if held."""
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    if _lock_fd(fd):
        return fd
    os.close(fd)
    return None


def _unlock(fd: int) -> None:
    _unlock_fd(fd)
    try:
        os.close(fd)
    except OSError:
        pass


def _lock_blocking(path: Path, *, timeout_s: float) -> int:
    deadline = time.monotonic() + timeout_s
    while True:
        fd = _try_lock(path)
        if fd is not None:
            return fd
        if time.monotonic() >= deadline:
            raise LeaseBusy(f"timed out waiting for {path.name}")
        time.sleep(0.02)


def _tree_dir(base_path: Path, tree_id: str) -> Path:
    if not _TREE_RE.match(tree_id or ""):
        raise ValueError("invalid owner tree id")
    return base_path / TREE_DIR / tree_id


class OwnerTree:
    """This process's membership of one owner tree. Keep it for the process life.

    The FOUNDER is the process that started the tree (the daemon); every other
    member is a child acting for it. A child acts only while its founder lives:
    a child that outlives its daemon, or joins late after it died, refuses to act
    (round-1 B1 code review finding 2), and a child never takes a key by death
    recovery -- succeeding a dead owner is a founder's job.
    """

    def __init__(self, base_path: str | Path, tree_id: str) -> None:
        self.base_path = Path(base_path)
        self.tree_id = tree_id
        self._member_fd: int | None = None
        self.member_path: Path | None = None
        self.founder = False

    @classmethod
    def start(cls, base_path: str | Path) -> OwnerTree:
        """A new tree, with this process as its founder."""
        tree = cls(base_path, uuid.uuid4().hex)
        tree.join(founder=True)
        return tree

    def join(self, *, timeout_s: float = 30.0, founder: bool = False) -> OwnerTree:
        directory = _tree_dir(self.base_path, self.tree_id)
        directory.mkdir(parents=True, exist_ok=True)
        gate = _lock_blocking(directory / _GATE, timeout_s=timeout_s)
        try:
            member = directory / f"{os.getpid()}-{uuid.uuid4().hex[:12]}.lock"
            fd = _try_lock(member)
            if fd is None:  # a brand-new name: only a filesystem fault holds it
                raise OSError(f"could not lock owner tree member {member.name}")
            self._member_fd, self.member_path, self.founder = fd, member, founder
            if founder:
                (directory / _FOUNDER).write_text(member.name, encoding="utf-8")
        finally:
            _unlock(gate)
        return self

    def founder_alive(self) -> bool:
        """Whether this tree's founder process still holds its member lock."""
        if self.founder:
            return self.alive
        directory = _tree_dir(self.base_path, self.tree_id)
        try:
            name = (directory / _FOUNDER).read_text(encoding="utf-8").strip()
        except OSError:
            return False
        if not name.endswith(".lock") or "/" in name or "\\" in name:
            return False
        fd = _try_lock(directory / name)
        if fd is None:
            return True
        _unlock(fd)
        return False

    def _close_after_fork(self) -> None:
        """In a forked child: drop the inherited descriptor WITHOUT unlocking it
        (an flock is shared by the open file description; unlocking here would
        release the parent's membership). The child joins on its own."""
        if self._member_fd is not None:
            try:
                os.close(self._member_fd)
            except OSError:
                pass
            self._member_fd = None

    def leave(self) -> None:
        """Stop being a member. The kernel does this at death; tests do it to die."""
        if self._member_fd is not None:
            _unlock(self._member_fd)
            self._member_fd = None
        if self.member_path is not None:
            try:
                self.member_path.unlink()
            except OSError:
                pass

    @property
    def alive(self) -> bool:
        return self._member_fd is not None


@contextmanager
def _dead_tree_gate(base_path: Path, tree_id: str) -> Iterator[bool]:
    """Yield True, holding the tree's gate, iff no member of ``tree_id`` is alive.

    The gate is held for the whole ``with`` block, so a late joiner cannot slip
    in between the proof and the caller's commit. A tree with no directory, or no
    gate file, is UNKNOWN and yields False: a missing proof is never death.
    """
    try:
        directory = _tree_dir(base_path, tree_id)
    except ValueError:
        yield False
        return
    if not (directory / _GATE).is_file():
        yield False
        return
    gate = _try_lock(directory / _GATE)
    if gate is None:
        yield False  # someone is joining right now: alive
        return
    try:
        held: list[int] = []
        dead = True
        try:
            for member in sorted(directory.glob("*.lock")):
                fd = _try_lock(member)
                if fd is None:
                    dead = False
                    break
                held.append(fd)
        finally:
            for fd in held:
                _unlock(fd)
        yield dead
    finally:
        _unlock(gate)


_trees: dict[str, OwnerTree] = {}
_trees_lock = threading.Lock()


def _reset_after_fork() -> None:
    """A forked child inherits neither membership nor a possibly-held lock."""
    global _trees_lock
    for tree in _trees.values():
        tree._close_after_fork()
    _trees.clear()
    _trees_lock = threading.Lock()


if hasattr(os, "register_at_fork"):  # pragma: no branch - POSIX only
    os.register_at_fork(after_in_child=_reset_after_fork)


def current_tree(base_path: str | Path) -> OwnerTree:
    """This process's tree for ``base_path``: the inherited one, else a private one."""
    key = str(Path(base_path).resolve())
    with _trees_lock:
        tree = _trees.get(key)
        if tree is not None and tree.alive:
            return tree
        inherited = (os.environ.get(TREE_ENV) or "").strip()
        tree = OwnerTree(base_path, inherited).join() if inherited else OwnerTree.start(base_path)
        _trees[key] = tree
        return tree


def start_owner_tree(base_path: str | Path) -> OwnerTree:
    """The daemon's start: a tree of its own, advertised to every child it spawns."""
    key = str(Path(base_path).resolve())
    with _trees_lock:
        tree = OwnerTree.start(base_path)
        _trees[key] = tree
    os.environ[TREE_ENV] = tree.tree_id
    recover_dead_keys(base_path)
    return tree


def ensure_owner_tree(base_path: str | Path) -> OwnerTree:
    """The founder tree this process already started, else start one now."""
    key = str(Path(base_path).resolve())
    with _trees_lock:
        tree = _trees.get(key)
        if tree is not None and tree.alive and tree.founder:
            return tree
    return start_owner_tree(base_path)


def join_inherited_tree(base_path: str | Path) -> OwnerTree | None:
    """A spawned executor's startup: join the advertised tree before doing any
    work, and refuse to run if its founder is already gone. None when the process
    was not spawned by an owner (a CLI, a test)."""
    if not (os.environ.get(TREE_ENV) or "").strip():
        return None
    tree = current_tree(base_path)
    if not tree.founder_alive():
        tree.leave()
        raise LeaseLost("the owner process that spawned this executor is gone")
    return tree


@contextmanager
def using_tree(tree: OwnerTree) -> Iterator[OwnerTree]:
    """Run as ``tree`` (tests: play an old owner, then a successor)."""
    key = str(tree.base_path.resolve())
    with _trees_lock:
        previous = _trees.get(key)
        _trees[key] = tree
    try:
        yield tree
    finally:
        with _trees_lock:
            if previous is None:
                _trees.pop(key, None)
            else:
                _trees[key] = previous


# --------------------------------------------------------------------------- #
# The store catalog: every store a key's fence lives in
# --------------------------------------------------------------------------- #

#: store kind -> enumerator over a data root of every path a store of that kind
#: can have. Restore uses these, NOT the catalog, to find stores (round-3 refute
#: finding 3: a store the catalog never saw must still be found).
STORE_ENUMERATORS: dict[str, Callable[[Path], list[Path]]] = {}


def store_kind(name: str):
    def register(enumerate_paths: Callable[[Path], list[Path]]):
        STORE_ENUMERATORS[name] = enumerate_paths
        return enumerate_paths
    return register


@store_kind("agent_turn_journal")
def _journal_paths(data_root: Path) -> list[Path]:
    from tinyassets.storage import DB_FILENAME

    path = data_root / DB_FILENAME
    return [path] if path.is_file() else []


def register_store(base_path: str | Path, store_path: str | Path, kind: str) -> None:
    if kind not in STORE_ENUMERATORS:
        raise ValueError(f"unknown owner store kind {kind!r}")
    with lease_db(base_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO owner_store_catalog VALUES (?, ?, ?)",
            (str(Path(store_path).resolve()), kind, _now()),
        )


def catalog(base_path: str | Path) -> list[tuple[Path, str]]:
    with lease_db(base_path) as conn:
        return [(Path(row["store_path"]), row["store_kind"])
                for row in conn.execute("SELECT store_path, store_kind FROM owner_store_catalog")]


# --------------------------------------------------------------------------- #
# The lease
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class KeyLease:
    """One key's lease as held by this process's tree. Read ``generation`` from it."""

    base_path: Path
    owner_key: str
    generation: int
    tree_id: str
    _proof: str | None = None

    @property
    def proof(self) -> str:
        if self._proof is None:
            raise LeaseLost("this member did not acquire the key; it holds no proof")
        return self._proof

    def held(self) -> bool:
        with lease_db(self.base_path) as conn:
            row = conn.execute(
                "SELECT generation, holder_tree, state FROM owner_lease WHERE owner_key = ?",
                (self.owner_key,),
            ).fetchone()
        return (row is not None and row["state"] == "open"
                and row["holder_tree"] == self.tree_id and row["generation"] == self.generation)

    def check(self) -> None:
        if not self.held():
            raise LeaseLost(f"owner lease {self.owner_key} generation {self.generation} lost")

    def verify(self, generation: int, proof: str) -> bool:
        with lease_db(self.base_path) as conn:
            row = conn.execute(
                "SELECT generation, holder_tree, state, proof_sha256 FROM owner_lease "
                "WHERE owner_key = ?", (self.owner_key,),
            ).fetchone()
        return bool(
            row is not None and row["state"] == "open"
            and row["holder_tree"] == self.tree_id
            and int(generation) == row["generation"] == self.generation
            and isinstance(proof, str) and proof
            and hmac.compare_digest(_sha(proof), row["proof_sha256"])
        )


_proofs: dict[tuple[str, str, int], str] = {}
_fenced_once: set[tuple[str, str, int]] = set()  # (store, key, generation)


def _high_water(conn: sqlite3.Connection, owner_key: str) -> int:
    row = conn.execute(
        "SELECT MAX(generation) FROM fence_high_water WHERE owner_key = ?", (owner_key,),
    ).fetchone()
    return int(row[0] or 0)


def _restoring(conn: sqlite3.Connection) -> bool:
    row = conn.execute("SELECT state FROM restore_state WHERE singleton = 1").fetchone()
    return row is not None and row["state"] == "in_progress"


def acquire(base_path: str | Path, owner_key: str, *, wait_s: float = 30.0) -> KeyLease:
    """This tree's lease on ``owner_key``: the one it holds, or a fresh generation.

    Taken only when the row is absent, released, or held by a tree proven dead
    (every member's lock free, under the dead tree's gate held through the
    commit). A live holder is waited for up to ``wait_s`` and never displaced.
    """
    if not _KEY_RE.match(owner_key or ""):
        raise ValueError("invalid owner key")
    base = Path(base_path)
    tree = current_tree(base)
    if not tree.founder and not tree.founder_alive():
        raise LeaseLost("this executor's owner process is gone; it must not act")
    deadline = time.monotonic() + wait_s
    while True:
        with lease_db(base) as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if _restoring(conn):
                    raise RestoreInProgress("platform restore in progress; no owner may start")
                row = conn.execute(
                    "SELECT * FROM owner_lease WHERE owner_key = ?", (owner_key,),
                ).fetchone()
                mine = row is not None and row["holder_tree"] == tree.tree_id
                if mine and row["state"] == "open":
                    conn.commit()
                    lease = KeyLease(base, owner_key, row["generation"], tree.tree_id,
                                     _proofs.get((str(base), owner_key, row["generation"])))
                    break
                if row is not None and row["state"] == "open" and not tree.founder:
                    conn.rollback()  # a child never succeeds a dead owner: wait, then fail
                elif row is not None and row["state"] == "open":
                    with _dead_tree_gate(base, row["holder_tree"]) as dead:
                        if dead:
                            lease = _take(conn, base, owner_key, row, tree)
                            conn.commit()  # inside the gate: no late joiner can act
                            break
                    conn.rollback()
                else:
                    lease = _take(conn, base, owner_key, row, tree)
                    conn.commit()
                    break
            except BaseException:
                if conn.in_transaction:
                    conn.rollback()
                raise
        if time.monotonic() >= deadline:
            raise LeaseBusy(f"owner key {owner_key} is held by a live owner")
        time.sleep(0.05)
    _ensure_fences(lease)
    return lease


def _take(conn, base: Path, owner_key: str, row, tree: OwnerTree) -> KeyLease:
    generation = max(int(row["generation"]) if row is not None else 0,
                     _high_water(conn, owner_key)) + 1
    proof = secrets.token_hex(32)
    conn.execute(
        "INSERT INTO owner_lease VALUES (?, ?, ?, ?, 'open', ?, NULL) "
        "ON CONFLICT(owner_key) DO UPDATE SET generation = excluded.generation, "
        "holder_tree = excluded.holder_tree, proof_sha256 = excluded.proof_sha256, "
        "state = 'open', acquired_at = excluded.acquired_at, released_at = NULL",
        (owner_key, generation, tree.tree_id, _sha(proof), _now()),
    )
    _proofs[(str(base), owner_key, generation)] = proof
    return KeyLease(base, owner_key, generation, tree.tree_id, proof)


def _ensure_fences(lease: KeyLease) -> None:
    """Advance every cataloged store's fence for this key to the lease generation.

    Idempotent and re-run once per process per (key, generation), so a crash
    between the lease commit and a fence advance heals on the next acquire.
    """
    from tinyassets.storage.owner_fence import advance_fence

    for store_path, _kind in catalog(lease.base_path):
        marker = (str(store_path), lease.owner_key, lease.generation)
        if marker in _fenced_once or not store_path.is_file():
            continue
        advance_fence(store_path, lease)
        _fenced_once.add(marker)


def record_fence(base_path: str | Path, owner_key: str, store_path: str | Path,
                 generation: int) -> None:
    """Remember the highest fence written for (key, store) -- AFTER the store write."""
    with lease_db(base_path) as conn:
        conn.execute(
            "INSERT INTO fence_high_water VALUES (?, ?, ?) ON CONFLICT(owner_key, store_path) "
            "DO UPDATE SET generation = MAX(generation, excluded.generation)",
            (owner_key, str(Path(store_path).resolve()), int(generation)),
        )


def recover_dead_keys(base_path: str | Path) -> list[str]:
    """The founder's startup: take every key a DEAD owner tree still holds.

    Children never succeed a dead owner (they would wait and fail), so a key the
    previous daemon left open must be recovered here, before anything is
    spawned -- whether or not it had progressing turns (round-2 B1 code review
    finding 2). A key whose holder is still alive is left alone. Returns the
    keys taken.
    """
    tree = current_tree(base_path)
    if not tree.founder:
        raise LeaseLost("only an owner tree's founder recovers keys")
    if not lease_db_path(base_path).is_file():
        return []
    with lease_db(base_path) as conn:
        open_keys = [row["owner_key"] for row in conn.execute(
            "SELECT owner_key FROM owner_lease WHERE state = 'open' AND holder_tree != ?",
            (tree.tree_id,),
        )]
    taken = []
    for owner_key in open_keys:
        try:
            acquire(base_path, owner_key, wait_s=0)
        except LeaseBusy:
            continue  # its holder is alive
        taken.append(owner_key)
    return taken


def release(lease: KeyLease) -> bool:
    """Voluntary release (D4: only at the command center's idle instant)."""
    with lease_db(lease.base_path) as conn:
        cursor = conn.execute(
            "UPDATE owner_lease SET state = 'released', released_at = ? WHERE owner_key = ? "
            "AND holder_tree = ? AND generation = ? AND state = 'open'",
            (_now(), lease.owner_key, lease.tree_id, lease.generation),
        )
        return cursor.rowcount == 1


def held_generation(base_path: str | Path, owner_key: str) -> tuple[int, str] | None:
    """(generation, holder_tree) of an OPEN lease, read-only, or None."""
    path = lease_db_path(base_path)
    if not path.is_file():
        return None
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10.0)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='owner_lease'").fetchone():
            return None
        row = conn.execute("SELECT generation, holder_tree, state FROM owner_lease "
                           "WHERE owner_key = ?", (owner_key,)).fetchone()
    finally:
        conn.close()
    if row is None or row[2] != "open":
        return None
    return int(row[0]), str(row[1])


def tree_alive(base_path: str | Path, tree_id: str) -> bool:
    """Whether any member of ``tree_id`` is alive. Unknown (no proof) reads alive."""
    with _dead_tree_gate(Path(base_path), tree_id) as dead:
        return not dead


__all__ = [
    "LEASE_DB_NAME",
    "PLATFORM_KEY",
    "STORE_ENUMERATORS",
    "TREE_ENV",
    "KeyLease",
    "LeaseBusy",
    "LeaseLost",
    "OwnerTree",
    "RestoreInProgress",
    "acquire",
    "catalog",
    "current_tree",
    "ensure_owner_tree",
    "join_inherited_tree",
    "held_generation",
    "key_for",
    "record_fence",
    "register_store",
    "recover_dead_keys",
    "release",
    "start_owner_tree",
    "store_kind",
    "tree_alive",
    "using_tree",
]
