"""Owner fences: a write commits only while its owner's generation is current.

Every database an owner writes carries ``owner_fence(owner_key, generation)``.
Two primitives, deliberately separate (round-1 refute finding 6):

* :func:`check_fence` -- inside an ordinary owner mutation's OWN ``BEGIN
  IMMEDIATE`` transaction, the stored fence must EQUAL the writer's generation.
  The read and the mutation share the write lock, so a fence advanced by a
  successor serialises before or after it and a stale owner commits nothing.
* :func:`advance_fence` -- at acquisition only, after the lease proof verifies:
  raise the stored fence to the new generation (a missing row is initialised).
  It is never usable by an ordinary write, so the equality rule cannot reject it.

Keyed per owner, never a singleton per database: moving command center B's key
must not fence command center A's still-running turn in the same shared store
(round-1 refute finding 12).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from tinyassets.owner_lease import KeyLease, LeaseLost, record_fence

FENCE_SCHEMA = (
    "CREATE TABLE IF NOT EXISTS owner_fence ("
    "owner_key TEXT PRIMARY KEY, generation INTEGER NOT NULL CHECK(generation > 0))"
)


def ensure_fence_table(conn: sqlite3.Connection) -> None:
    conn.execute(FENCE_SCHEMA)


def check_fence(conn: sqlite3.Connection, lease: KeyLease) -> None:
    """Raise :class:`LeaseLost` unless ``lease`` is the current fence. Call it
    inside the mutation's own write transaction, after ``BEGIN IMMEDIATE``."""
    if not conn.in_transaction:
        raise RuntimeError("check_fence must run inside the mutation's write transaction")
    row = conn.execute(
        "SELECT generation FROM owner_fence WHERE owner_key = ?", (lease.owner_key,),
    ).fetchone()
    stored = None if row is None else int(row[0])
    if stored != lease.generation:
        raise LeaseLost(
            f"owner fence for {lease.owner_key} is {stored}, not this owner's "
            f"generation {lease.generation}"
        )


def advance_fence(store_path: str | Path, lease: KeyLease) -> int:
    """Raise ``store_path``'s fence for the key to ``lease.generation``.

    Only the acquiring tree calls this, right after its lease row committed, and
    only for a lease that still verifies. Never lowers a fence. Returns the fence.
    """
    if not lease.held():
        raise LeaseLost(f"cannot advance a fence for a lost lease {lease.owner_key}")
    conn = sqlite3.connect(str(store_path), timeout=30.0, isolation_level=None)
    try:
        conn.execute("PRAGMA busy_timeout = 30000")
        ensure_fence_table(conn)
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT generation FROM owner_fence WHERE owner_key = ?", (lease.owner_key,),
        ).fetchone()
        current = 0 if row is None else int(row[0])
        if current < lease.generation:
            conn.execute(
                "INSERT INTO owner_fence VALUES (?, ?) ON CONFLICT(owner_key) "
                "DO UPDATE SET generation = excluded.generation",
                (lease.owner_key, lease.generation),
            )
            current = lease.generation
        conn.execute("COMMIT")
    except BaseException:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    # The store first, then the lease store's memory of it: a crash between the
    # two leaves the store AHEAD, which restore reads from the store itself.
    record_fence(lease.base_path, lease.owner_key, store_path, current)
    return current


def stored_fences(store_path: str | Path) -> dict[str, int]:
    """Every fence in a store, read-only (restore's high-water input)."""
    path = Path(store_path)
    if not path.is_file():
        return {}
    conn = sqlite3.connect(path.as_uri() + "?mode=rw", uri=True, timeout=10.0)
    try:
        if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                            "AND name='owner_fence'").fetchone():
            return {}
        return {str(k): int(g) for k, g in conn.execute("SELECT owner_key, generation "
                                                        "FROM owner_fence")}
    finally:
        conn.close()


__all__ = ["FENCE_SCHEMA", "advance_fence", "check_fence", "ensure_fence_table",
           "stored_fences"]
