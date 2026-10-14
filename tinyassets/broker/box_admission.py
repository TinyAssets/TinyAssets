"""Read-only permanent box admission, callable only by the root box host."""

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
