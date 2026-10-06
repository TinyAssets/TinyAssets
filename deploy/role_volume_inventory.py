"""Read-only, stopped-volume authority inventory for the role migration.

The startup caller supplies the verified owner/egress helper namespaces. No
application import, service, allocation or mutation occurs in this module.
"""

from __future__ import annotations

import os
import stat


class InventoryRefused(RuntimeError):
    """A complete, unambiguous owner-to-tree assignment is unavailable."""


def discover(root, owner):
    """D64: every u-* name and every legacy universe.json tree participates."""
    found = []
    for name in sorted(os.listdir(root)):
        info = os.stat(name, dir_fd=root, follow_symlinks=False)
        if name.startswith("u-"):
            if not stat.S_ISDIR(info.st_mode):
                raise InventoryRefused(f"owner root is not a plain directory: {name}")
            found.append(name)
        elif stat.S_ISDIR(info.st_mode) and not name.startswith("."):
            with owner["_directory"](root, name) as directory:
                if owner["_stat"](directory, "universe.json") is not None:
                    found.append(name)
    return found


def _table(db, name):
    record = db.execute("SELECT type FROM sqlite_master WHERE name=?", (name,)).fetchone()
    if record is not None and record != ("table",):
        raise InventoryRefused(f"authority is not a stored table: {name}")
    return record is not None


def principals(root, centers, egress):
    """Use explicit home bindings; a legacy tree needs exactly one admin.

    Neither a basename, display name, host_path nor an engine-authored manifest
    confers authority. Missing and competing ownership records fail closed.
    """
    if not centers:
        return {}
    info = egress["_regular"](root, ".tinyassets.db")
    if info is None or info.st_uid != 1001:
        raise InventoryRefused("missing or non-daemon-owned authority database")
    result = {}
    with egress["_database_snapshot"](root, ".tinyassets.db") as db:
        homes = _table(db, "founder_home")
        admins = _table(db, "universe_acl")
        for center in centers:
            candidates = (
                db.execute("SELECT founder_sub FROM founder_home WHERE universe_id=?",
                           (center,)).fetchall() if homes else []
            )
            if not candidates and admins:
                candidates = db.execute(
                    "SELECT actor_id FROM universe_acl WHERE universe_id=? AND permission='admin'",
                    (center,),
                ).fetchall()
            if len(candidates) != 1:
                raise InventoryRefused(f"missing or ambiguous owner binding: {center}")
            principal = candidates[0][0]
            if (not isinstance(principal, str) or not principal.strip()
                    or principal != principal.strip() or not principal.isprintable()
                    or len(principal) > 512):
                raise InventoryRefused(f"invalid owner principal: {center}")
            result[center] = principal
    return result


def reserved(root, center_principals, owner, egress):
    """Read existing broker reservations without initializing or allocating.

    Unallocated principals are returned explicitly. The privileged caller must
    allocate them using the retired broker identity before mutating owner trees.
    An existing invalid map is never treated as an empty allocation database.
    """
    mapping = {}
    found_map = False
    if owner["_stat"](root, ".broker") is not None:
        with owner["_directory"](root, ".broker") as broker:
            if owner["_stat"](broker, "state") is not None:
                with owner["_directory"](broker, "state") as state:
                    info = os.fstat(state)
                    if info.st_uid != 1002 or stat.S_IMODE(info.st_mode) & 0o077:
                        raise InventoryRefused("identity state is not broker-private")
                    info = egress["_regular"](state, "owner-identities.db")
                    if info is not None:
                        found_map = True
                        if info.st_uid != 1002 or stat.S_IMODE(info.st_mode) != 0o600:
                            raise InventoryRefused("identity database is not broker-private")
                        with egress["_database_snapshot"](state, "owner-identities.db") as db:
                            if not _table(db, "owner_identities"):
                                raise InventoryRefused("identity database has no reservation table")
                            used = set()
                            for principal, machine in db.execute(
                                "SELECT principal,machine_id FROM owner_identities"
                            ):
                                if (not isinstance(principal, str) or not principal.strip()
                                        or principal in mapping or type(machine) is not int
                                        or not 300001 <= machine <= 399999 or machine in used):
                                    raise InventoryRefused("invalid durable identity reservation")
                                mapping[principal] = machine
                                used.add(machine)
    layout = owner["_read"](root, ".layout.json")
    if not found_map and "owners" in layout.get("roles", {}):
        raise InventoryRefused("owner migration exists but its permanent identity map is missing")
    if "owners" in layout.get("roles", {}):
        with owner["_directory"](root, owner["STATE"]) as journal_root:
            info = os.fstat(journal_root)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                raise InventoryRefused("owner journal is not root-private")
            journal = owner["_read"](journal_root, "journal.json", private=True)
        for center, machine in journal["configuration"]["bindings"].items():
            if machine not in mapping.values():
                raise InventoryRefused("permanent map lost a journaled owner reservation")
            principal = center_principals.get(center)
            if principal is not None and mapping.get(principal) != machine:
                raise InventoryRefused(f"authority changed for migrated owner tree: {center}")
    bindings = {center: mapping[principal] for center, principal in center_principals.items()
                if principal in mapping}
    missing = sorted(set(center_principals.values()) - mapping.keys())
    return bindings, missing


def inventory(data_root, *, owner, egress):
    """Caller must hold the common layout lock and have stopped all writers."""
    with owner["_root"](data_root) as root:
        centers = discover(root, owner)
        authority = principals(root, centers, egress)
        bindings, unallocated = reserved(root, authority, owner, egress)
        return {"principals": authority, "bindings": bindings, "unallocated": unallocated}
