"""Offline D60/D61 owner-tree migration. No service or activation entry point.

The verified startup caller supplies broker-resolved center identities and a
complete classification of each center's immediate entries as work/protected.
All writers must be stopped. The layout lock alone does not stop engine writes.
Quarantine is permanent escrow, never automatically restored by rollback.
"""

from __future__ import annotations

import ctypes
import errno
import fcntl
import json
import os
import secrets
import stat
import struct
from contextlib import contextmanager
from pathlib import Path

STATE = ".role-owner-migration"
ACCESS = "system.posix_acl_access"
DEFAULT = "system.posix_acl_default"


class MigrationRefused(RuntimeError):
    """An unresolved inventory or changed inode prevents safe migration."""


def _parts(path):
    parts = path.split("/")
    if any(not p or p in {".", ".."} or "\\" in p for p in parts):
        raise MigrationRefused(f"invalid relative name: {path}")
    return parts


@contextmanager
def _directory(parent, path):
    fd = os.dup(parent)
    try:
        for part in _parts(path):
            child = os.open(
                part,
                os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME | os.O_CLOEXEC,
                dir_fd=fd,
            )
            os.close(fd)
            fd = child
        yield fd
    finally:
        os.close(fd)


@contextmanager
def _root(path):
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts:
        raise MigrationRefused("data root must be absolute without parent traversal")
    fd = os.open("/", os.O_PATH | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for part in path.parts[1:]:
            child = os.open(
                part, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC, dir_fd=fd
            )
            os.close(fd)
            fd = child
        readable = os.open(".", os.O_RDONLY | os.O_DIRECTORY | os.O_NOATIME, dir_fd=fd)
        try:
            yield readable
        finally:
            os.close(readable)
    finally:
        os.close(fd)


@contextmanager
def _parent(root, path):
    parts = _parts(path)
    if len(parts) == 1:
        yield root, parts[0]
    else:
        with _directory(root, "/".join(parts[:-1])) as parent:
            yield parent, parts[-1]


def _stat(root, path):
    try:
        with _parent(root, path) as (parent, name):
            return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _escrow(root, row):
    try:
        return _stat(root, STATE + "/quarantine/" + row["path"])
    except FileNotFoundError:
        return None


def _key(info):
    return [info.st_dev, info.st_ino, stat.S_IFMT(info.st_mode)]


def _read(root, name, *, private=False):
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME
    fd = os.open(name, flags, dir_fd=root)
    with os.fdopen(fd, "r", encoding="utf-8") as handle:
        info = os.fstat(handle.fileno())
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_nlink != 1
            or private
            and (info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o600)
        ):
            raise MigrationRefused(f"unsafe migration document: {name}")
        return json.load(handle)


def _write(root, name, document, *, uid=0):
    temporary = ".owner-migration-" + secrets.token_hex(16)
    fd = os.open(
        temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=root
    )
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        json.dump(document, handle, sort_keys=True)
        handle.flush()
        os.fchown(handle.fileno(), uid, uid)
        os.fsync(handle.fileno())
    os.replace(temporary, name, src_dir_fd=root, dst_dir_fd=root)
    os.fsync(root)


def _acl(owner, named, group=0):
    entries = [(1, owner, 0xFFFFFFFF)]
    entries += [(2, rights, uid) for uid, rights in sorted(named.items())]
    entries += [(4, group, 0xFFFFFFFF)]
    if named:
        mask = group
        for rights in named.values():
            mask |= rights
        entries += [(16, mask, 0xFFFFFFFF)]
    entries += [(32, 0, 0xFFFFFFFF)]
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)


def _xattr(fd, name):
    try:
        return os.getxattr(fd, name)
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise
        return None


def _permissions(fd, row, reverse):
    directory = row["key"][2] == stat.S_IFDIR
    executable = bool(row["mode"] & 0o111)
    uid = 1001 if reverse or row["kind"] == "root" else row["machine"]
    gid = 1001 if reverse else row["machine"]
    rights = 7 if directory or executable else 6
    named = {} if reverse else {1001: rights}
    if row["kind"] == "root" and not reverse:
        # Protected roots retain daemon modification; engines only traverse.
        named = {row["machine"]: 5, 1002: 1}
    access = _acl(rights, named)
    default = _acl(7, {1001: 7}) if directory and row["kind"] != "root" and not reverse else None
    mode = (0o700 if directory or executable else 0o600) if reverse else None
    info = os.fstat(fd)
    changed = False
    if (info.st_uid, info.st_gid) != (uid, gid):
        os.fchown(fd, uid, gid)
        changed = True
    # Neither setuid nor sticky bits are inherited from legacy payloads.
    # SETGID is already in the approved pre-drop set (D17).
    special = stat.S_ISGID if directory and not reverse else 0
    current = os.fstat(fd).st_mode
    if current & 0o7000 != special:
        previous = os.getegid()
        try:
            os.setegid(gid)
            os.fchmod(fd, (stat.S_IMODE(current) & 0o777) | special)
        finally:
            os.setegid(previous)
        changed = True
    if default != _xattr(fd, DEFAULT):
        if default is None:
            os.removexattr(fd, DEFAULT)
        else:
            os.setxattr(fd, DEFAULT, default)
        changed = True
    if reverse:
        if _xattr(fd, ACCESS) is not None:
            os.removexattr(fd, ACCESS)
            changed = True
        if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
            os.fchmod(fd, mode)
            changed = True
    elif _xattr(fd, ACCESS) != access:
        # Setting an access ACL can clear setgid just like chmod (D17).
        previous = os.getegid()
        try:
            os.setegid(gid)
            os.setxattr(fd, ACCESS, access)
        finally:
            os.setegid(previous)
        changed = True
    if changed:
        os.fsync(fd)
    final = os.fstat(fd)
    if (final.st_uid, final.st_gid) != (uid, gid):
        raise MigrationRefused(f"ownership readback: {row['path']}")
    if final.st_mode & 0o7000 != special:
        raise MigrationRefused(f"special-mode readback: {row['path']}")
    if not reverse and _xattr(fd, ACCESS) != access:
        raise MigrationRefused(f"ACL readback: {row['path']}")
    return changed


def _inventory(root, bindings, work, reverse):
    rows = []
    aliases = {}
    device = os.fstat(root).st_dev

    def visit(parent, name, path, center, kind):
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        machine = bindings[center]
        if info.st_dev != device:
            raise MigrationRefused(f"cross-filesystem entry: {path}")
        if stat.S_ISLNK(info.st_mode) and kind == "work":
            rows.append(
                dict(
                    path=path,
                    key=_key(info),
                    kind="symlink",
                    machine=machine,
                    mode=stat.S_IMODE(info.st_mode),
                    nlink=info.st_nlink,
                    original_ids=[info.st_uid, info.st_gid],
                )
            )
            return
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise MigrationRefused(f"nonregular protected/special entry: {path}")
        allowed = {
            (1001, 1001),
            (1001, 1100),
            (1003, 1100),
            (1003, 1003),
            (1001, machine),
            (machine, machine),
        }
        if kind == "protected":
            allowed = {(1001, 1001), (1001, 1100), (1001, 1102), (1001, machine)}
        if (info.st_uid, info.st_gid) not in allowed:
            raise MigrationRefused(f"foreign identity at {path}: {info.st_uid}:{info.st_gid}")
        row = dict(
            path=path,
            key=_key(info),
            kind=kind,
            machine=machine,
            mode=stat.S_IMODE(info.st_mode),
            nlink=info.st_nlink,
            original_ids=[info.st_uid, info.st_gid],
        )
        rows.append(row)
        if stat.S_ISREG(info.st_mode):
            aliases.setdefault((info.st_dev, info.st_ino), []).append(row)
        else:
            with _directory(parent, name) as child:
                before = os.fstat(child)
                if _key(before) != row["key"]:
                    raise MigrationRefused(f"directory changed: {path}")
                for entry in sorted(os.listdir(child)):
                    entry_kind = (
                        ("work" if entry in work[center] else "protected")
                        if kind == "root"
                        else kind
                    )
                    visit(child, entry, f"{path}/{entry}", center, entry_kind)
                after = os.fstat(child)
                if (before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise MigrationRefused(f"directory changed during scan: {path}")

    for center in sorted(bindings):
        visit(root, center, center, center, "root")
    for group in aliases.values():
        if any(row["nlink"] != len(group) for row in group):
            raise MigrationRefused(f"unresolved inode names: {group[0]['path']}")
        if len(group) > 1 and any(row["kind"] != "work" for row in group):
            raise MigrationRefused(f"protected hardlink: {group[0]['path']}")
        if len({row["path"].split("/")[0] for row in group}) > 1:
            if reverse:
                raise MigrationRefused(f"cross-tree alias after activation: {group[0]['path']}")
            for row in group:
                row["kind"] = "quarantine"
    return rows


def _names(root, bindings):
    """Revalidate namespace completeness even during a partly moved quarantine."""
    found = set()

    def visit(parent, name, path):
        found.add(path)
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
        if stat.S_ISDIR(info.st_mode):
            with _directory(parent, name) as child:
                if _key(os.fstat(child)) != _key(info):
                    raise MigrationRefused(f"directory replaced: {path}")
                for entry in os.listdir(child):
                    visit(child, entry, path + "/" + entry)

    for center in bindings:
        visit(root, center, center)
    return found


def _mkdirs(root, path):
    fd = os.dup(root)
    try:
        for part in _parts(path):
            try:
                os.mkdir(part, mode=0o700, dir_fd=fd)
                os.fsync(fd)
            except FileExistsError:
                pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            info = os.fstat(child)
            if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                os.close(child)
                raise MigrationRefused("quarantine parent is not root-private")
            os.close(fd)
            fd = child
        os.fsync(fd)
    finally:
        os.close(fd)


def _move(root, state, row):
    preserved = _escrow(root, row)
    if preserved is not None and _key(preserved) == row["key"]:
        source = _stat(root, row["path"])
        if source is not None and _key(source) == row["key"]:
            raise MigrationRefused(f"duplicate quarantined name: {row['path']}")
        return False
    destination = "quarantine/" + row["path"]
    _mkdirs(state, destination.rsplit("/", 1)[0])
    with (
        _parent(root, row["path"]) as (source, name),
        _parent(state, destination) as (target, leaf),
    ):
        current = _stat(source, name)
        moved = _stat(target, leaf)
        if moved is not None and _key(moved) == row["key"]:
            if current is not None and _key(current) == row["key"]:
                raise MigrationRefused(f"duplicate quarantined name: {row['path']}")
            return False
        if current is None or _key(current) != row["key"] or moved is not None:
            raise MigrationRefused(f"quarantine move conflicts: {row['path']}")
        libc = ctypes.CDLL(None, use_errno=True)
        rename = libc.renameat2
        rename.argtypes = [
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.c_char_p,
            ctypes.c_uint,
        ]
        rename.restype = ctypes.c_int
        if rename(source, os.fsencode(name), target, os.fsencode(leaf), 1):
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code), row["path"])
        os.fsync(target)
        os.fsync(source)
        return True


def migrate(data_root, *, bindings, work, reverse=False, dry_run=False, after_step=None):
    """Migrate all supplied owner trees under one durable inventory.

    ``bindings`` maps canonical center names to already-reserved broker IDs.
    ``work`` maps the same centers to classified immediate work-entry names;
    remaining entries are protected and validated, never reassigned to engines.
    Neither argument is accepted from an engine or untrusted environment.
    The full startup orchestrator must verify complete center discovery first.
    """
    if os.geteuid() != 0:
        raise MigrationRefused("owner migration requires the pre-drop startup window")
    if set(work) != set(bindings) or not bindings:
        raise MigrationRefused("complete nonempty owner classification required")
    for center, machine in bindings.items():
        if (
            len(_parts(center)) != 1
            or center.startswith(".")
            or type(machine) is not int
            or not 300001 <= machine <= 399999
        ):
            raise MigrationRefused("invalid reserved owner binding")
        for entry in work[center]:
            if len(_parts(entry)) != 1 or entry in {
                ".credential-vault.json",
                ".credentials",
                ".runtime",
            }:
                raise MigrationRefused("protected entry cannot be owner work")
    direction = "reverse" if reverse else "forward"
    configuration = {"bindings": bindings, "work": {k: sorted(v) for k, v in work.items()}}

    def checkpoint(step):
        if after_step:
            after_step(step)

    with _root(data_root) as root:
        lock = os.open(".layout.lock", os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=root)
        try:
            info = os.fstat(lock)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise MigrationRefused("invalid layout lock")
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            layout = _read(root, ".layout.json")
            if (
                layout.get("layout") != 2
                or layout.get("moves", {}).get("consents_outside_command_centers") != "done"
                or layout.get("state") not in {"stable", "migrating"}
                or layout.get("state") == "migrating"
                and layout.get("roles", {}).get("state") != "migrating"
            ):
                raise MigrationRefused("finish unrelated layout migration first")
            journal = None
            if _stat(root, STATE) is not None:
                with _directory(root, STATE) as state:
                    info = os.fstat(state)
                    if info.st_uid != 0 or stat.S_IMODE(info.st_mode) != 0o700:
                        raise MigrationRefused("migration state is not root-private")
                    if _stat(state, "journal.json") is not None:
                        journal = _read(state, "journal.json", private=True)
            if journal and journal["configuration"] != configuration:
                raise MigrationRefused("owner bindings/classification differ from durable journal")
            if journal and journal["direction"] != direction and journal["state"] != "stable":
                raise MigrationRefused("finish interrupted direction before reversing")
            if journal is None or journal["direction"] != direction:
                escrow = (
                    [r for r in journal["rows"] if r["kind"] == "quarantine"] if journal else []
                )
                journal = dict(
                    schema=1,
                    configuration=configuration,
                    direction=direction,
                    state="migrating",
                    rows=_inventory(root, bindings, work, reverse) + escrow,
                )
            elif journal["state"] == "stable":
                fresh = _inventory(root, bindings, work, reverse)
                expected = {
                    r["path"]: (r["key"], r["nlink"], r["kind"])
                    for r in journal["rows"]
                    if r["kind"] != "quarantine"
                }
                actual = {r["path"]: (r["key"], r["nlink"], r["kind"]) for r in fresh}
                if actual != expected:
                    # A completed migration may have served real owner writes.
                    # Re-scan ALL names before admitting its next generation;
                    # never extend an incomplete journal in this way.
                    escrow = [r for r in journal["rows"] if r["kind"] == "quarantine"]
                    journal = {**journal, "state": "migrating", "rows": fresh + escrow}
            rows = journal["rows"]
            present = _names(root, bindings)
            required = {r["path"] for r in rows if r["kind"] != "quarantine"}
            optional = {r["path"] for r in rows if r["kind"] == "quarantine"}
            if not required <= present or not present <= required | optional:
                raise MigrationRefused("owner namespace changed since durable inventory")
            # Validate every recorded source/destination before the first mutation.
            for row in rows:
                info = _stat(root, row["path"])
                if row["kind"] == "quarantine":
                    moved = _escrow(root, row)
                    if moved is not None:
                        info = moved
                if info is None or _key(info) != row["key"] or info.st_nlink != row["nlink"]:
                    raise MigrationRefused(f"inventory changed: {row['path']}")
                allowed_ids = [row["original_ids"]]
                if row["kind"] in {"work", "root"}:
                    allowed_ids.append(
                        [
                            1001 if reverse or row["kind"] == "root" else row["machine"],
                            1001 if reverse else row["machine"],
                        ]
                    )
                if [info.st_uid, info.st_gid] not in allowed_ids:
                    raise MigrationRefused(f"identity changed since inventory: {row['path']}")
            report = dict(
                direction=direction,
                quarantined=[r["path"] for r in rows if r["kind"] == "quarantine"],
                work_entries=sum(r["kind"] in {"work", "root"} for r in rows),
                changed=0,
            )
            if dry_run:
                return report
            _mkdirs(root, STATE)
            with _directory(root, STATE) as state:
                if journal["state"] != "stable":
                    _write(state, "journal.json", journal)
                    checkpoint("journal")
                progress = {"state": "migrating", "direction": direction}
                if layout.get("roles", {}).get("owners") != {
                    "state": "stable",
                    "direction": direction,
                }:
                    layout = {
                        **layout,
                        "state": "migrating",
                        "roles": {
                            **layout.get("roles", {}),
                            "state": "migrating",
                            "owners": progress,
                        },
                    }
                    _write(root, ".layout.json", layout, uid=1001)
                    checkpoint("marker")
                for row in rows:
                    if row["kind"] == "quarantine":
                        report["changed"] += _move(root, state, row)
                        checkpoint("quarantine-name")
                # Root labels last: incomplete descendants never look admitted.
                for row in sorted(rows, key=lambda r: r["kind"] == "root"):
                    if row["kind"] not in {"work", "root"}:
                        continue
                    with _parent(root, row["path"]) as (parent, name):
                        fd = os.open(
                            name,
                            os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_NOATIME,
                            dir_fd=parent,
                        )
                        try:
                            if _key(os.fstat(fd)) != row["key"]:
                                raise MigrationRefused(f"entry changed before chown: {row['path']}")
                            report["changed"] += _permissions(fd, row, reverse)
                        finally:
                            os.close(fd)
                    checkpoint("ownership")
                if journal["state"] != "stable":
                    _write(state, "journal.json", {**journal, "state": "stable"})
                    checkpoint("complete-journal")
                progress = {"state": "stable", "direction": direction}
                if layout.get("roles", {}).get("owners") != progress:
                    layout["roles"]["owners"] = progress
                    _write(root, ".layout.json", layout, uid=1001)
                return report
        finally:
            os.close(lock)
