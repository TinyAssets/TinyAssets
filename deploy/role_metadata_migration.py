"""Offline protected/shared metadata phase of the complete role migration.

All helpers and mode declarations are supplied from the verified immutable
startup chain. Owner work is handled by the separate inode/quarantine journal.
"""

from __future__ import annotations

import fcntl
import os
import re
import stat


def migrate(data_root, *, bindings, work, owner, modes, reverse=False,
            dry_run=False, layout_lock=None, after_step=None, reconcile_work=False,
            previous_bindings=None, explained=None):
    if os.geteuid() != 0:
        raise owner["MigrationRefused"]("metadata migration requires the pre-drop window")
    refused = owner["MigrationRefused"]
    direction = "reverse" if reverse else "forward"
    configuration = {"bindings": bindings, "work": {k: sorted(v) for k, v in work.items()}}

    def checkpoint(step):
        if after_step:
            after_step(step)

    def target(path, info):
        parts = path.split("/")
        directory = stat.S_ISDIR(info.st_mode)
        uid, gid = 1001, 1001
        mode = 0o700 if directory else 0o600 | (stat.S_IMODE(info.st_mode) & 0o111)
        named = {}
        if parts[0] == ".broker":
            uid, gid = 1002, 1101
            mode = 0o2700 if directory else 0o600
        elif parts[0] == ".consumer_liveness" and not reverse:
            gid = modes["BROKER_READ_GID"]
            mode = modes["LIVENESS_DIRECTORY_MODE" if directory else "LIVENESS_FILE_MODE"]
        elif parts[0] == ".universe-sidecars" and not reverse:
            gid = 1001 if len(parts) == 1 else modes["WORK_GID"]
            mode = (modes["SIDECAR_PARENT_MODE" if len(parts) == 1 else "SIDECAR_DIRECTORY_MODE"]
                    if directory else stat.S_IMODE(info.st_mode))
        elif parts[0] in bindings and not reverse:
            if parts[1] in {".credentials", ".credential-vault.json", "provider_definitions.json"}:
                gid = modes["BROKER_READ_GID"]
                mode = 0o2750 if directory else modes["VAULT_FILE_MODE"]
            elif parts[1] == ".runtime":
                # Only sealed launch snapshots are made readable; unrelated
                # caches/session material retain daemon-only protection.
                if len(parts) == 2 or parts[2] == "provider-launch-credentials":
                    rights = (1 if len(parts) <= 3 else 5) if directory else 4
                    named = {str(bindings[parts[0]]): rights}
                    mode = 0o700 if directory else 0o400
        if path == ".layout.lock":
            mode = 0o666
        return uid, gid, mode, named

    with owner["_root"](data_root) as root:
        if os.fstat(root).st_uid != 1001:
            raise refused("data root is not daemon-owned")
        lock = (os.dup(layout_lock) if layout_lock is not None else
                os.open(".layout.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root))
        try:
            info = os.fstat(lock)
            expected = owner["_stat"](root, ".layout.lock")
            if (expected is None or not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or not os.path.samestat(info, expected)):
                raise refused("invalid metadata layout lock")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            layout = owner["_read"](root, ".layout.json")
            if (layout.get("layout") != 2
                    or layout.get("moves", {}).get("consents_outside_command_centers") != "done"
                    or layout.get("state") not in {"stable", "migrating"}
                    or layout.get("state") == "migrating"
                    and layout.get("roles", {}).get("state") != "migrating"):
                raise refused("complete unrelated layout migration first")
            journal = None
            if owner["_stat"](root, owner["STATE"]) is not None:
                with owner["_directory"](root, owner["STATE"]) as state:
                    state_info = os.fstat(state)
                    if state_info.st_uid != 0 or stat.S_IMODE(state_info.st_mode) != 0o700:
                        raise refused("metadata journal parent is not root-private")
                    if owner["_stat"](state, "metadata.json") is not None:
                        journal = owner["_read"](state, "metadata.json", private=True)
            configuration_changed = journal and journal["configuration"] != configuration
            # DA7: a completed journal may lag one reconciled admission
            # generation; ``explained`` is the contract's phase_explained.
            if configuration_changed and not (
                reconcile_work and journal["state"] == "stable"
                and (journal["configuration"]["bindings"] == bindings
                     or explained is not None and explained(
                         journal["configuration"]["bindings"], previous_bindings, bindings))
            ):
                raise refused("metadata authority/classification changed")
            if journal and journal["direction"] != direction and journal["state"] != "stable":
                raise refused("finish interrupted metadata direction before reversing")
            rows = []
            originals = {tuple(r["key"]): r for r in journal["rows"]
                         if not r["cleanup"]} if journal else {}
            migrated = owner["_migrated"](journal)

            def visit(parent, name, path):
                parts = path.split("/")
                if parts[0] == owner["STATE"] or path == ".layout.json":
                    return
                # D12 relocation owns these inodes in both locations. Recording
                # their post-forward broker UID as an original would undo its
                # reverse chown when the very same inode returns to /data.
                egress = parts[1:] if parts[0] == ".broker" else parts
                if egress and egress[0] in {
                    "outbound.db", "outbound.db-wal", "outbound.db-shm",
                    "outbound.db-journal", ".outbound-proxy",
                }:
                    return
                if len(parts) == 2 and parts[0] in bindings and parts[1] in work[parts[0]]:
                    return
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if info.st_dev != os.fstat(root).st_dev:
                    raise refused(f"metadata crosses filesystem: {path}")
                cleanup = path == ".broker/owner.json"
                cleanup |= (len(parts) == 3 and parts[0] == ".universe-sidecars"
                            and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", parts[1]) is not None
                            and re.fullmatch(r"(?:egress-[0-9]+|engine-[0-9]+-[a-f0-9]{12})\.sock",
                                             name) is not None
                            and stat.S_ISSOCK(info.st_mode))
                if cleanup and not stat.S_ISDIR(info.st_mode):
                    rows.append(dict(path=path, key=owner["_key"](info), cleanup=True))
                    return
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise refused(f"protected metadata link/special entry: {path}")
                if stat.S_ISREG(info.st_mode) and info.st_nlink != 1:
                    raise refused(f"protected metadata hardlink: {path}")
                allowed = {1001, 1002} if parts[0] == ".broker" else {1001}
                if info.st_uid not in allowed:
                    raise refused(f"foreign protected metadata identity: {path}")
                if len(parts) != 1 or name not in bindings:
                    uid, gid, mode, named = target(path, info)
                    generation = owner["_generation"](parent, name, info)
                    original = owner["_provenance"](
                        originals.get(tuple(owner["_key"](info))), generation,
                        (info.st_uid, info.st_gid), stat.S_IMODE(info.st_mode), migrated)
                    if reverse and parts[0] != ".broker":
                        uid, gid = original["ids"]
                        mode = stat.S_IMODE(info.st_mode)
                        named = {}
                    if named:
                        mask = 0
                        for rights in named.values():
                            mask |= rights
                        mode = (mode & ~0o070) | (mask << 3)
                    mode &= stat.S_IMODE(info.st_mode)
                    if (stat.S_ISREG(info.st_mode) and not reverse
                            and (mode & 0o6111) & ~stat.S_IMODE(info.st_mode)):
                        raise refused(f"metadata would add executable/special file bits: {path}")
                    rows.append(dict(path=path, key=owner["_key"](info), cleanup=False,
                                     uid=uid, gid=gid, mode=mode, named=named,
                                     original=original, generation=generation))
                if stat.S_ISDIR(info.st_mode):
                    with owner["_directory"](parent, name) as directory:
                        before = os.fstat(directory)
                        if not os.path.samestat(info, before):
                            raise refused(f"metadata directory changed: {path}")
                        for entry in sorted(os.listdir(directory)):
                            visit(directory, entry, path + "/" + entry)
                        after = os.fstat(directory)
                        if (before.st_mtime_ns, before.st_ctime_ns) != (
                                after.st_mtime_ns, after.st_ctime_ns):
                            raise refused(f"metadata changed during inventory: {path}")

            for name in sorted(os.listdir(root)):
                visit(root, name, name)
            if journal and journal["state"] == "stable" and journal["direction"] == direction:
                paths = {row["path"] for row in rows}
                rows += [row for row in journal["rows"]
                         if row["cleanup"] and row["path"] not in paths]
                rows.sort(key=lambda row: row["path"])
            else:
                rows.sort(key=lambda row: row["path"])
            if journal and journal["state"] != "stable":
                previous = {r["path"]: r for r in journal["rows"]}
                actual = {r["path"]: r for r in rows}
                required = {name for name, row in previous.items() if not row["cleanup"]}
                if not required <= actual.keys() or not actual.keys() <= previous.keys():
                    raise refused("metadata namespace changed since journal")
                for path, row in actual.items():
                    if (row["key"], row.get("generation")) != (
                            previous[path]["key"], previous[path].get("generation")):
                        raise refused(f"metadata inode changed since journal: {path}")
                rows = journal["rows"]
            report = {"direction": direction, "entries": len(rows),
                      "cleanup": [r["path"] for r in rows if r["cleanup"]], "changed": 0}
            if dry_run:
                return report
            owner["_mkdirs"](root, owner["STATE"])
            with owner["_directory"](root, owner["STATE"]) as state:
                current = dict(configuration=configuration, direction=direction,
                               state="migrating", rows=rows)
                if (journal is None or journal["direction"] != direction
                        or journal["rows"] != rows or configuration_changed):
                    owner["_write"](state, "metadata.json", current)
                    checkpoint("metadata-journal")
                progress = {"direction": direction, "state": "migrating"}
                if (journal is None or journal["state"] != "stable"
                        or journal["direction"] != direction or journal["rows"] != rows
                        or configuration_changed
                        or layout.get("roles", {}).get("metadata") != {
                            "direction": direction, "state": "stable"}):
                    layout = {**layout, "state": "migrating", "roles": {
                        **layout.get("roles", {}), "state": "migrating", "metadata": progress}}
                    owner["_write"](root, ".layout.json", layout, uid=1001)
                    checkpoint("metadata-marker")
                for row in rows:
                    with owner["_parent"](root, row["path"]) as (parent, name):
                        info = owner["_stat"](parent, name)
                        if row["cleanup"] and info is None:
                            continue
                        if info is None or owner["_key"](info) != row["key"]:
                            raise refused(f"metadata changed before mutation: {row['path']}")
                        if row["cleanup"]:
                            os.unlink(name, dir_fd=parent)
                            os.fsync(parent)
                            report["changed"] += 1
                            checkpoint("metadata-cleanup")
                            continue
                        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                                     | os.O_NOATIME, dir_fd=parent)
                        try:
                            if owner["_key"](os.fstat(fd)) != row["key"]:
                                raise refused(f"metadata descriptor replaced: {row['path']}")
                            report["changed"] += _apply(fd, row, owner)
                        finally:
                            os.close(fd)
                    checkpoint("metadata-entry")
                report["changed"] += _apply(
                    root, dict(uid=1001, gid=1001, mode=0o755, named={}), owner)
                checkpoint("metadata-root")
                complete = {**current, "state": "stable"}
                if journal != complete:
                    owner["_write"](state, "metadata.json", complete)
                    checkpoint("metadata-complete")
                progress = {"direction": direction, "state": "stable"}
                if layout.get("roles", {}).get("metadata") != progress:
                    layout["roles"]["metadata"] = progress
                    owner["_write"](root, ".layout.json", layout, uid=1001)
                return report
        finally:
            os.close(lock)


def _apply(fd, row, owner):
    info = os.fstat(fd)
    before = (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
              owner["_xattr"](fd, owner["ACCESS"]), owner["_xattr"](fd, owner["DEFAULT"]))
    acl = None
    # D211 also covers chmod after the inventory or an interrupted apply.
    mode = row["mode"] & stat.S_IMODE(info.st_mode)
    if row["named"]:
        acl = owner["_acl"]((mode >> 6) & 7,
                            {int(k): v for k, v in row["named"].items()},
                            mask=(mode >> 3) & 7)
    expected = (row["uid"], row["gid"], mode, acl, None)
    if before == expected:
        return False
    if (info.st_uid, info.st_gid) != (row["uid"], row["gid"]):
        os.fchown(fd, row["uid"], row["gid"])
    previous = os.getegid()
    try:
        os.setegid(row["gid"])
        for attribute in (owner["DEFAULT"], owner["ACCESS"]):
            if owner["_xattr"](fd, attribute) is not None:
                os.removexattr(fd, attribute)
        if acl is not None:
            os.setxattr(fd, owner["ACCESS"], acl)
        os.fchmod(fd, mode)
    finally:
        os.setegid(previous)
    final = os.fstat(fd)
    after = (final.st_uid, final.st_gid, stat.S_IMODE(final.st_mode),
             owner["_xattr"](fd, owner["ACCESS"]), owner["_xattr"](fd, owner["DEFAULT"]))
    if after != expected:
        raise owner["MigrationRefused"]("protected metadata permission readback differs")
    os.fsync(fd)
    return True
