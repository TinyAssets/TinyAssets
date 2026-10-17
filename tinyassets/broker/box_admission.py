"""Broker-owned box reservations and root-only read admission."""

from __future__ import annotations

import sqlite3

from tinyassets.broker.owner_identities import validate_center, validate_principal


def owner_record(identities, owner: str, *, rotate: bool) -> dict:
    """Root boxhostd's launch inputs; no center or UID is selected by a caller."""
    validate_principal(owner)
    with identities._connect(write=False) as db:
        if db.execute("SELECT 1 FROM owner_box_revocations WHERE principal=? AND deleted=1",
                      (owner,)).fetchone():
            raise LookupError("owner account is deleted")
        centers = [row[0] for row in db.execute(
            "SELECT a.center FROM center_admissions a WHERE a.principal=? AND a.event='admit' "
            "AND NOT EXISTS (SELECT 1 FROM center_admissions r WHERE r.center=a.center "
            "AND r.event='retire')", (owner,))]
    if not centers:
        raise LookupError("owner has no live admission")
    if identities.plans.suspended(owner):
        raise PermissionError("owner account is suspended")
    record = resolve(identities, {"op": "BOX_ADMISSION", "principal": owner,
                                  "center": centers[0]})
    result = {"uid": record["uid"], "quota": record["quota"], "centers": centers}
    if rotate:
        uid, result["secret"] = identities.box_route(owner, rotate=True)
        if uid != result["uid"]:
            raise RuntimeError("owner reservation changed")
    return result


def lifecycle(identities, doc):
    if (set(doc) != {"op", "principal", "rotate"}
            or doc["op"] != "BOX_LIFECYCLE" or type(doc["rotate"]) is not bool):
        raise ValueError("invalid lifecycle request")
    return owner_record(identities, doc["principal"], rotate=doc["rotate"])


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
    from tinyassets.broker.owner_reads import Plans
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
        quota = int(limits_for(Plans(data_root).read(owner)["tier"]).storage_bytes)
        if quota <= 0 or quota % 1024:
            raise PermissionError("invalid canonical owner box bound")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", (owner, uid, uid, quota))
