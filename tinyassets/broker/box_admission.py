"""Broker-owned box reservations and root-only read admission."""

from __future__ import annotations

import sqlite3

from tinyassets.broker.owner_identities import validate_center, validate_principal


def resolve(identities, request: dict) -> dict:
    if set(request) != {"op", "principal", "center"} or request["op"] != "BOX_ADMISSION":
        raise ValueError("invalid box admission request")
    owner, center = request["principal"], request["center"]
    validate_principal(owner)
    validate_center(center)
    if identities is None or identities.admitted_owner(center) != owner:
        raise PermissionError("owner has no retained center admission")
    machine = identities.owner_machine(owner)
    with sqlite3.connect(identities.path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT machine, project_id, bhard_bytes FROM box_records "
                         "WHERE principal=?", (owner,)).fetchone()
    if (row is None or row[:2] != (machine, machine) or type(row[2]) is not int
            or row[2] <= 0 or row[2] % 1024):
        raise PermissionError("owner has no verified box registry record")
    return {"op": "BOX_ADMITTED", "uid": machine, "quota": row[2]}


def provision(identities, data_root, owner: str, center: str) -> None:
    """Idempotent completion of authenticated CENTER_ADMISSION, never an RPC quota."""
    from tinyassets.universe_owner import account_type_of
    from tinyassets.usage_policy import limits_for

    validate_principal(owner)
    validate_center(center)
    if identities is None or identities.admitted_owner(center) != owner:
        raise PermissionError("box provisioning requires retained center admission")
    uid = identities.owner_machine(owner)
    if type(uid) is not int or not 300001 <= uid <= 399999:
        raise PermissionError("invalid permanent owner box identity")
    # The one-shot layout migration creates this table. Runtime must not repair
    # an absent database/schema or allocate a new mapping on startup.
    with sqlite3.connect(identities.path.as_uri() + "?mode=rw", uri=True) as db:
        db.execute("PRAGMA synchronous=FULL")
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT machine, project_id FROM box_records WHERE principal=?",
                         (owner,)).fetchone()
        if row is not None:
            if row != (uid, uid):
                raise PermissionError("permanent box reservation changed")
            return  # center admission must never overwrite migrated paid limits
        quota = int(limits_for(account_type_of(data_root, owner)).storage_bytes)
        if quota <= 0 or quota % 1024:
            raise PermissionError("invalid canonical owner box bound")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", (owner, uid, uid, quota))
