"""Read-only D61 legacy provenance inventory; never reads file payloads.

Run as root on Linux to open directories with O_NOATIME. No symlink is followed,
including in root ancestors. A live scan is observational, not a migration lock.
Default discovery matches daemon filesystem discovery (universe.json), also
including every u-* directory so an incomplete tree cannot evade inventory.
Explicit --owner NAME options scan a synthetic or operator-inventoried set.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
from collections import Counter
from pathlib import Path


def directory(parent: int, name: str) -> int:
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                   | os.O_CLOEXEC | os.O_NOATIME, dir_fd=parent)


def root_descriptor(path: Path) -> int:
    if not path.is_absolute() or ".." in path.parts:
        raise ValueError("scan root must be absolute without parent traversal")
    fd = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_NOATIME)
    try:
        for part in path.parts[1:]:
            child = directory(fd, part)
            os.close(fd)
            fd = child
        return fd
    except BaseException:
        os.close(fd)
        raise


def scan(root: Path, owners: list[str] | None = None,
         legacy_ids: frozenset[tuple[int, int]] = frozenset({(1001, 1001)})) -> dict:
    """Inventory names by (device,inode); unresolved names block assignment.

    All results are metadata. Special entries include symlinks (reported but
    never traversed); existing workspace symlinks remain D9 skip candidates.
    A cross-tree inode is a quarantine candidate, NEVER an assignment candidate.
    The mutating migration must revalidate this inventory while writers are off.
    """
    inodes: dict[tuple[int, int], dict] = {}
    findings: list[dict] = []
    counts: Counter = Counter()
    scanned: list[str] = []
    fd = root_descriptor(root)

    def error(path, exc):
        findings.append(dict(kind="scan_error", path=path, errno=exc.errno))

    def walk(handle, owner, relative):
        before = os.fstat(handle)
        for name in sorted(os.listdir(handle)):
            path = f"{relative}/{name}"
            try:
                info = os.stat(name, dir_fd=handle, follow_symlinks=False)
                counts["entries"] += 1
                if (info.st_uid, info.st_gid) not in legacy_ids:
                    findings.append(dict(kind="foreign_identity", path=path,
                                         uid=info.st_uid, gid=info.st_gid))
                if stat.S_ISDIR(info.st_mode):
                    child = directory(handle, name)
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            findings.append(dict(kind="changed_entry", path=path))
                        else:
                            walk(child, owner, path)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    counts["regular_names"] += 1
                    key = (info.st_dev, info.st_ino)
                    row = inodes.setdefault(key, dict(device=info.st_dev, inode=info.st_ino,
                        nlink=info.st_nlink, owners=set(), paths=[]))
                    if row["nlink"] != info.st_nlink:
                        findings.append(dict(kind="changed_link_count", path=path))
                    row["owners"].add(owner)
                    row["paths"].append(path)
                else:
                    findings.append(dict(kind="special", path=path,
                        file_type=stat.S_IFMT(info.st_mode), uid=info.st_uid, gid=info.st_gid))
            except OSError as exc:
                error(path, exc)
        after = os.fstat(handle)
        if (before.st_mtime_ns, before.st_ctime_ns) != (after.st_mtime_ns, after.st_ctime_ns):
            findings.append(dict(kind="changed_directory", path=relative))

    try:
        if owners is None:
            owners = []
            for name in sorted(os.listdir(fd)):
                if name.startswith("u-"):
                    owners.append(name)
                    continue
                info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                if not stat.S_ISDIR(info.st_mode):
                    continue
                child = directory(fd, name)
                try:
                    if "universe.json" in os.listdir(child):
                        owners.append(name)
                finally:
                    os.close(child)
        if len(set(owners)) != len(owners):
            raise ValueError("duplicate owner roots")
        for owner in sorted(owners):
            if not owner or owner in {".", ".."} or "/" in owner or "\\" in owner:
                raise ValueError("owner must be one direct child name")
            try:
                child = directory(fd, owner)
                try:
                    info = os.fstat(child)
                    if (info.st_uid, info.st_gid) not in legacy_ids:
                        findings.append(dict(kind="foreign_identity", path=owner,
                                             uid=info.st_uid, gid=info.st_gid))
                    walk(child, owner, owner)
                    scanned.append(owner)
                finally:
                    os.close(child)
            except OSError as exc:
                error(owner, exc)
    finally:
        os.close(fd)
    for row in inodes.values():
        row["owners"] = sorted(row["owners"])
        if len(row["owners"]) > 1:
            findings.append(dict(kind="cross_owner_inode", **row))
        elif row["nlink"] != len(row["paths"]):
            findings.append(dict(kind="unseen_inode_names", **row))
        else:
            counts["sole_owner_inodes"] += 1
    kinds = Counter(row["kind"] for row in findings)
    return dict(schema=1, read_only=True, snapshot_locked=False, owners=scanned,
                counts=dict(counts), finding_counts=dict(kinds), findings=findings,
                assignment_ready=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path("/data"))
    parser.add_argument("--owner", action="append")
    parser.add_argument("--legacy-id", action="append", help="allowed UID:GID; default 1001:1001")
    args = parser.parse_args()
    identities = frozenset(tuple(map(int, value.split(":")))
                           for value in (args.legacy_id or ["1001:1001"]))
    if any(len(pair) != 2 for pair in identities):
        parser.error("legacy identities must be UID:GID pairs")
    report = scan(args.root, args.owner, identities)
    print(json.dumps(report, sort_keys=True))
    return 3 if report["findings"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
