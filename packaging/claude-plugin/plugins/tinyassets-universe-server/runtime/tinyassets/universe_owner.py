"""Whose account a universe's bytes and seats are charged to.

Founder decision 2026-09-30: usage limits are ONE storage pool and ONE seat count
per PERSON, shared across all of their universes. That needs a fact the platform
did not store: which single account owns a universe. `universe_acl` cannot answer
it -- a shared universe has several admins, and "the admin who granted themselves"
is a correlation, not a record (memory `never-infer-identity-from-adjacent-tables`).

So ownership is written by exactly two things, and read by one resolver pair:

* the transaction that CREATES a universe (`record_creation`), in the same commit
  as the creator's admin grant -- so no universe is ever born granted but unowned;
* a one-time backfill from `founder_home` (`backfill_from_founder_home`) for
  universes that predate this table. `founder_home` is an explicit identity
  binding. Nothing else is backfilled: every other pre-existing universe stays
  UNATTRIBUTED -- counted on host-only surfaces, never refused -- until an owner
  is known (founder decision 2026-09-30, account-storage-quota Q2).

`owner_of` / `account_type_of` are the ONLY resolvers. Seats and storage both
call them; a second pair would be a second definition of one fact. The account
type is the only per-account input behaviour may depend on (`usage_policy.
AccountType`), and this is the only module that reads it from billing storage
(`tests/test_owner_door_import_boundary.py` pins that).
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from tinyassets.principals import named_principal
from tinyassets.usage_policy import AccountType, account_type

_log = logging.getLogger(__name__)

SOURCE_CREATION = "creation"
SOURCE_FOUNDER_HOME = "founder_home"


class OwnershipConflict(RuntimeError):
    """A universe already belongs to a different account. Ownership never moves
    silently: a caller that meets this has a bug or an attack, not a race to win."""


_TABLE_DDL = (
    # The ONE account a universe's storage and seats are charged to
    # (account-storage-quota D2).
    "CREATE TABLE universe_owner ("
    " universe_id TEXT PRIMARY KEY,"
    " owner_id    TEXT NOT NULL,"
    " bound_at    REAL NOT NULL,"
    " source      TEXT NOT NULL CHECK (source IN ('creation', 'founder_home')))",
    "CREATE INDEX idx_universe_owner_owner ON universe_owner(owner_id)",
)


def migrate_universe_owner(conn: sqlite3.Connection) -> bool:
    """Create `universe_owner` and run its one-time backfill ATOMICALLY.

    The table's existence is the marker that the backfill ran, so the two must
    commit together: SQLite DDL is transactional, and both happen inside one
    ``BEGIN IMMEDIATE``. A crash anywhere inside leaves neither, and the next
    start retries. Returns True when this call created the table.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        exists = conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'universe_owner'"
        ).fetchone()
        if exists is None:
            for statement in _TABLE_DDL:
                conn.execute(statement)
            backfill_from_founder_home(conn)
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    return exists is None


def backfill_from_founder_home(conn: sqlite3.Connection) -> int:
    """Bind pre-existing universes to the founder whose HOME they are.

    Runs inside the author-server migration, once, when `universe_owner` is
    created. A universe that more than one founder_home row points at is
    ambiguous, and ambiguity is not resolved by picking one: it stays
    unattributed. Returns the number of universes bound.
    """
    from tinyassets.storage import _now

    rows = conn.execute(
        "SELECT universe_id, MIN(founder_sub) FROM founder_home "
        "WHERE universe_id <> '' AND founder_sub <> '' "
        "GROUP BY universe_id HAVING COUNT(*) = 1"
    ).fetchall()
    now = _now()
    bound = 0
    for universe_id, founder in rows:
        cursor = conn.execute(
            "INSERT INTO universe_owner (universe_id, owner_id, bound_at, source) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(universe_id) DO NOTHING",
            (str(universe_id), str(founder), now, SOURCE_FOUNDER_HOME),
        )
        bound += cursor.rowcount
    if bound:
        _log.info("universe_owner: backfilled %d universe(s) from founder_home", bound)
    return bound


def record_creation(conn: sqlite3.Connection, *, universe_id: str, owner_id: str) -> None:
    """Record the creator as owner, inside the CALLER's creation transaction.

    Idempotent for the same owner (a retried create); raises `OwnershipConflict`
    for a different one -- ownership is never rebound by a create.
    """
    from tinyassets.storage import _now

    owner = named_principal(owner_id)
    uid = (universe_id or "").strip()
    if not owner or not uid:
        raise ValueError("a command center owner needs a named principal and a command center id")
    conn.execute(
        "INSERT INTO universe_owner (universe_id, owner_id, bound_at, source) "
        "VALUES (?, ?, ?, ?) ON CONFLICT(universe_id) DO NOTHING",
        (uid, owner, _now(), SOURCE_CREATION),
    )
    row = conn.execute(
        "SELECT owner_id FROM universe_owner WHERE universe_id = ?", (uid,)
    ).fetchone()
    if row is None or str(row[0]) != owner:
        raise OwnershipConflict(f"command center {uid!r} is already owned by another account")


def owner_of(base_path: str | Path, universe_id: str) -> str | None:
    """The account a universe is charged to, or None when unattributed."""
    from tinyassets.daemon_server import _connect, initialize_author_server

    uid = (universe_id or "").strip()
    if not uid:
        return None
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT owner_id FROM universe_owner WHERE universe_id = ?", (uid,)
        ).fetchone()
    return str(row[0]) if row else None


def owned_universes(base_path: str | Path, owner_id: str) -> list[str]:
    """Every universe charged to this account, sorted. Empty for no account."""
    from tinyassets.daemon_server import _connect, initialize_author_server

    owner = named_principal(owner_id)
    if not owner:
        return []
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT universe_id FROM universe_owner WHERE owner_id = ? ORDER BY universe_id",
            (owner,),
        ).fetchall()
    return [str(row[0]) for row in rows]


def unattributed_universes(base_path: str | Path) -> list[str]:
    """Universes that exist but that no account is charged for.

    "A universe" is `daemon_server.owned_universe_ids` -- somebody has a grant or
    a home binding -- never "a directory under the data root", which would count
    the platform's own scratch, backups and archives. Host-only diagnostics:
    these are counted, never refused, until an owner is known.
    """
    from tinyassets.daemon_server import _connect, owned_universe_ids

    universes = owned_universe_ids(base_path)
    with _connect(base_path) as conn:
        charged = {str(row[0]) for row in conn.execute("SELECT universe_id FROM universe_owner")}
    return sorted(universes - charged)


def account_type_of(base_path: str | Path, owner_id: str) -> AccountType:
    """The account's type: the subscription recorded on its HOME universe.

    That is where Stripe checkout already writes it. No home, no account, or an
    unreadable record all resolve to FREE -- never to the subscription.
    """
    # read_tier, not get_tier: get_tier creates the database and leaves its
    # connection to the GC, which on Windows held a handle in the universe dir
    # and blocked account deletion's rename (#4158).
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.storage.subscription_state import read_tier

    owner = named_principal(owner_id)
    if not owner:
        return AccountType.FREE
    try:
        home = get_founder_home(base_path, owner)
    except Exception:  # noqa: BLE001 -- an unreadable binding is free, never paid
        _log.warning("account_type_of: founder_home unreadable; using free", exc_info=True)
        return AccountType.FREE
    if not home or Path(home).name != home or home.startswith("."):
        return AccountType.FREE
    return account_type(read_tier(Path(base_path) / home))


def account_type_for_universe(universe_dir: str | Path) -> AccountType:
    """The type of the ACCOUNT that owns this universe; FREE when unattributed.

    Never raises: seat admission and the upgrade line call it, and an unreadable
    owner record must cost the least, not break the run.
    """
    path = Path(universe_dir)
    try:
        owner = owner_of(path.parent, path.name)
    except Exception:  # noqa: BLE001 -- unresolvable ownership is free, never paid
        _log.warning("account_type_for_universe: owner unreadable; using free", exc_info=True)
        return AccountType.FREE
    if not owner:
        return AccountType.FREE
    return account_type_of(path.parent, owner)


__all__ = [
    "OwnershipConflict",
    "SOURCE_CREATION",
    "SOURCE_FOUNDER_HOME",
    "backfill_from_founder_home",
    "owned_universes",
    "owner_of",
    "record_creation",
    "account_type_of",
    "account_type_for_universe",
    "unattributed_universes",
]
