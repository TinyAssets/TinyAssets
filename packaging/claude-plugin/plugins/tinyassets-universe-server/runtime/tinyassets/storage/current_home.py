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
