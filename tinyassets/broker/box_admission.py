"""Broker-owned box reservations and root-only read admission."""

from __future__ import annotations

import sqlite3

from tinyassets.broker.admission import OwnerSuspended, require_admitted  # noqa: F401
from tinyassets.broker.owner_identities import validate_center, validate_principal


def owner_record(identities, owner: str, *, rotate: bool) -> dict:
    """Root boxhostd's launch inputs; no center or UID is selected by a caller."""
    require_admitted(identities, owner)
    with identities._connect(write=False) as db:
        centers = [row[0] for row in db.execute(
            "SELECT a.center FROM center_admissions a WHERE a.principal=? AND a.event='admit' "
            "AND NOT EXISTS (SELECT 1 FROM center_admissions r WHERE r.center=a.center "
            "AND r.event='retire')", (owner,))]
    record = _box_record(identities, owner)
    result = {"uid": record["uid"], "quota": record["quota"], "centers": centers}
    from tinyassets.broker.owner_volumes import binding

    volume = binding(identities, owner)
    if volume["work"] and volume["work"]["kind"] == "restore":
        raise PermissionError("owner restore has not committed")
    result.update({key: volume[key] for key in (
        "volume_generation", "snapshot_id", "snapshot_sha256")})
    from tinyassets.usage_policy import limits_for

    result["quota"] = int(limits_for(identities.plans.read(owner)["tier"]).storage_bytes)
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
    require_admitted(identities, owner)
    # Until centers move inside the box, retain only their positive ownership
    # binding here. Retirement changes the next launch list, not admission.
    with identities._connect(write=False) as db:
        binding = db.execute("SELECT principal FROM center_admissions "
                             "WHERE center=? AND event='admit'", (center,)).fetchone()
    if binding != (owner,):
        raise PermissionError("owner has no retained center binding")
    return _box_record(identities, owner)


def resolve_owner(identities, request: dict, *, data_root=None) -> dict:
    """Public egress belongs to the owner's box, independently of its centers."""
    if set(request) != {"op", "principal"} or request["op"] != "BOX_PUBLIC":
        raise ValueError("invalid owner public admission request")
    owner = request["principal"]
    validate_principal(owner)
    if identities is None:
        raise PermissionError("owner box admission unavailable")
    require_admitted(identities, owner)
    return _box_record(identities, owner)


def _box_record(identities, owner: str) -> dict:
    machine = identities.owner_machine(owner)
    with sqlite3.connect(identities.path.as_uri() + "?mode=ro", uri=True) as db:
        row = db.execute("SELECT machine, project_id, bhard_bytes FROM box_records "
                         "WHERE principal=?", (owner,)).fetchone()
    if (machine is None or row is None or row[:2] != (machine, machine) or type(row[2]) is not int
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
            if db.execute("SELECT uid FROM owner_volumes WHERE principal=?",
                          (owner,)).fetchone() != (uid,):
                raise PermissionError("permanent volume binding is missing")
            return  # center admission must never overwrite migrated paid limits
        quota = int(limits_for(Plans(data_root).read(owner)["tier"]).storage_bytes)
        if quota <= 0 or quota % 1024:
            raise PermissionError("invalid canonical owner box bound")
        db.execute("INSERT INTO box_records VALUES (?,?,?,?)", (owner, uid, uid, quota))
        from tinyassets.broker.owner_volumes import provision as bind_volume

        bind_volume(db, owner, uid)
