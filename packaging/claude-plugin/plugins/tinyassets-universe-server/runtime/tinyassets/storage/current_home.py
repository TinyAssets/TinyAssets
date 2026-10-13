"""Shared transaction-local owner home/deletion guard, not authentication."""

from __future__ import annotations

import sqlite3


class CurrentHomeChanged(RuntimeError):
    """The authenticated caller's home was removed, rebound or deleted."""


def check_principal_not_deleted(conn: sqlite3.Connection, owner: str) -> None:
    """Transaction-local deletion fence without imposing founder-home ownership.

    Standalone legacy vaults may predate the account schema. Only actual schema
    absence means no deletion; malformed schemas and read failures propagate.
    Account deletion initializes and permanently retains the tombstone table.
    """
    from tinyassets.account_deletion import principal_digest

    if not conn.in_transaction:
        raise RuntimeError("principal deletion check requires a transaction")
    schema = conn.execute(
        "SELECT type FROM sqlite_master WHERE name = 'deleted_principals'",
    ).fetchone()
    if schema is None:
        return
    if schema[0] != "table":
        raise RuntimeError("invalid principal deletion schema")
    if conn.execute(
        "SELECT 1 FROM deleted_principals WHERE founder_sub = ?",
        (principal_digest(owner),),
    ).fetchone() is not None:
        raise CurrentHomeChanged("credential owner account was deleted")


def check_current_home(conn: sqlite3.Connection, owner: str, universe: str) -> None:
    from tinyassets.account_deletion import principal_digest

    if not conn.in_transaction:
        raise RuntimeError("current home check requires a transaction")
    home = conn.execute(
        "SELECT universe_id FROM founder_home WHERE founder_sub = ?",
        (owner,),
    ).fetchone()
    deleted = conn.execute(
        "SELECT 1 FROM deleted_principals WHERE founder_sub = ?",
        (principal_digest(owner),),
    ).fetchone()
    if home is None or home[0] != universe or deleted is not None:
        raise CurrentHomeChanged("current command center home changed")


def current_home(conn: sqlite3.Connection, owner: str) -> str | None:
    """The owner's bound home universe id in this transaction, or None.

    Tolerates an installation that never initialized home storage, the same
    way the deletion fence tolerates a missing tombstone table.
    """
    if conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'founder_home'",
    ).fetchone() is None:
        return None
    home = conn.execute(
        "SELECT universe_id FROM founder_home WHERE founder_sub = ?",
        (owner,),
    ).fetchone()
    return None if home is None or not home[0] else str(home[0])


def check_plan_scope(
    conn: sqlite3.Connection, owner: str, universe: str, *, home_scoped: bool,
) -> None:
    """Re-fence work captured for ``universe`` in the owner scope it was captured in.

    Home-scoped work re-runs the current-home fence. Work outside the owner's
    home (a second command center, a first connect before any home row exists)
    keeps the deletion fence, and refuses if the home has since moved onto this
    universe, because home-scoped preferences would then apply to it.
    """
    if home_scoped:
        check_current_home(conn, owner, universe)
        return
    check_principal_not_deleted(conn, owner)
    if current_home(conn, owner) == universe:
        raise CurrentHomeChanged("current command center home changed")
