"""D12 offline egress relocation, called inside the startup migration window.

Linux only, stdlib only. The caller must have stopped every role/container using
the volume. We take the common layout lock, pin directories, refuse aliases and
never replace a destination. Completion of this SUBSTEP leaves the top-level
layout migrating: only the full role migration may admit normal service.
"""
from __future__ import annotations

import ctypes
import json
import os
import secrets
import sqlite3
import stat
from contextlib import ExitStack, contextmanager
from pathlib import Path

BROKER_DIR = ".broker"
LEDGER = "outbound.db"
PROXY = ".outbound-proxy"
SIDECARS = (LEDGER + "-wal", LEDGER + "-shm", LEDGER + "-journal")
PRIVATE_DIR_MODE = 0o2700
PRIVATE_FILE_MODE = 0o600


class MigrationRefused(RuntimeError):
    """Unsafe or inconsistent input; retained data needs operator attention."""


@contextmanager
def _directory(name, *, parent=None):
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
    try:
        yield fd
    finally:
        os.close(fd)


def _stat(parent, name):
    try:
        return os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        return None


def _regular(parent, name):
    info = _stat(parent, name)
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise MigrationRefused(f"non-regular file or hardlink: {name}")
    return info


def _tree(parent, name, *, uid=None, gid=None):
    """Validate before mutation; then fsync and re-mode using pinned descriptors."""
    info = _stat(parent, name)
    if info is None:
        return
    if stat.S_ISDIR(info.st_mode):
        with _directory(name, parent=parent) as fd:
            if os.fstat(fd).st_dev != os.fstat(parent).st_dev:
                raise MigrationRefused(f"cross-filesystem broker tree: {name}")
            for child in sorted(os.listdir(fd)):
                _tree(fd, child, uid=uid, gid=gid)
            if uid is not None:
                _permissions(fd, uid, gid, PRIVATE_DIR_MODE if uid == 1002 else 0o700)
                os.fsync(fd)
    else:
        _regular(parent, name)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
        try:
            actual = os.fstat(fd)
            if (actual.st_dev, actual.st_ino) != (info.st_dev, info.st_ino):
                raise MigrationRefused(f"entry changed during validation: {name}")
            if uid is not None:
                _permissions(fd, uid, gid, PRIVATE_FILE_MODE)
                os.fsync(fd)
        finally:
            os.close(fd)


def _permissions(fd, uid, gid, mode):
    info = os.fstat(fd)
    if (info.st_uid, info.st_gid) != (uid, gid):
        os.fchown(fd, uid, gid)
    if stat.S_IMODE(os.fstat(fd).st_mode) != mode:
        os.fchmod(fd, mode)


def _rename(source, destination, name):
    """Atomic no-replace rename, including directories; never clobber data."""
    libc = ctypes.CDLL(None, use_errno=True)
    rename = libc.renameat2
    rename.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p,
                       ctypes.c_uint]
    rename.restype = ctypes.c_int
    if rename(source, os.fsencode(name), destination, os.fsencode(name), 1) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), name)
    os.fsync(destination)
    os.fsync(source)


def _read_marker(root):
    if _regular(root, ".layout.json") is None:
        raise MigrationRefused("missing .layout.json; initialize the data layout first")
    fd = os.open(".layout.json", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
    with os.fdopen(fd, encoding="utf-8") as handle:
        document = json.load(handle)
    if not isinstance(document, dict) or document.get("layout") not in (1, 2):
        raise MigrationRefused("unsupported layout marker")
    return document


def _mark(root, document, progress):
    document = {**document, "state": "migrating", "roles": {
        **document.get("roles", {}), "state": "migrating", "egress": progress,
    }}
    temporary = ".layout.roles." + secrets.token_hex(16) + ".tmp"
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                 0o600, dir_fd=root)
    # The temporary is ours (O_EXCL); only this file may be removed on error.
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(document, handle, sort_keys=True)
            handle.flush()
            os.fchown(handle.fileno(), 1001, 1001)
            os.fsync(handle.fileno())
        os.replace(temporary, ".layout.json", src_dir_fd=root, dst_dir_fd=root)
        os.fsync(root)
    finally:
        if _stat(root, temporary) is not None:
            os.unlink(temporary, dir_fd=root)
    return document


def _checkpoint(parent):
    if _regular(parent, LEDGER) is None:
        if any(_stat(parent, name) is not None for name in SIDECARS):
            raise MigrationRefused("orphan SQLite sidecar without ledger")
        return
    # Every entry was validated under the exclusive startup lock. SQLite needs
    # a pathname to manage its sidecars; the pinned parent survives renames.
    path = f"/proc/self/fd/{parent}/{LEDGER}"
    connection = sqlite3.connect(f"file:{path}?mode=rw", uri=True, timeout=0)
    try:
        connection.execute("PRAGMA synchronous=FULL")
        result = connection.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if result[0] != 0:
            raise MigrationRefused("busy WAL; another writer has not stopped")
        if connection.execute("PRAGMA quick_check").fetchone() != ("ok",):
            raise MigrationRefused("ledger integrity check failed")
    finally:
        connection.close()
    for name in (LEDGER, *SIDECARS):
        if _regular(parent, name) is not None:
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    os.fsync(parent)


def relocate(data_root: Path, *, reverse=False, dry_run=False, after_step=None):
    """Return a plan or complete the egress substep; no service is started.

    after_step is an in-process fault-injection seam, not a CLI/RPC operation.
    Dry-run requires an existing layout lock and reads without opening SQLite.
    The full startup migration owns initialization and final layout admission.
    """
    import fcntl

    direction = "reverse" if reverse else "forward"
    with ExitStack() as stack:
        root = stack.enter_context(_directory(data_root))
        if _regular(root, ".layout.lock") is None:
            raise MigrationRefused("missing .layout.lock; initialize the data layout first")
        lock = os.open(".layout.lock", os.O_RDONLY | os.O_NOFOLLOW, dir_fd=root)
        stack.callback(os.close, lock)
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        document = _read_marker(root)
        progress = document.get("roles", {}).get("egress", {})
        # storage_layout can resume the consents move and mark the whole
        # volume stable. Do not overlap it: that would admit an old daemon
        # after relocation, which would create an empty ledger at the old path.
        if (document.get("layout") != 2
                or document.get("moves", {}).get("consents_outside_command_centers") != "done"):
            raise MigrationRefused("complete the consents layout migration before role migration")
        own_migration = (document.get("roles", {}).get("state") == "migrating"
                         and progress.get("direction") in ("forward", "reverse")
                         and progress.get("state") in ("migrating", "stable"))
        if document.get("state") != "stable" and not (
                document.get("state") == "migrating" and own_migration):
            raise MigrationRefused("unrelated layout migration is not complete")
        if progress.get("state") == "migrating" and progress.get("direction") != direction:
            raise MigrationRefused("finish interrupted egress direction before reversing")
        broker_info = _stat(root, BROKER_DIR)
        broker = None
        if broker_info is not None:
            broker = stack.enter_context(_directory(BROKER_DIR, parent=root))
            if os.fstat(broker).st_dev != os.fstat(root).st_dev:
                raise MigrationRefused("broker directory must share the data filesystem")
        names = (*SIDECARS, LEDGER, PROXY)
        plan = []
        for name in names:
            old, new = _stat(root, name), _stat(broker, name) if broker is not None else None
            if old is not None and new is not None:
                raise MigrationRefused(f"conflicting source and destination: {name}")
            if old is not None:
                _tree(root, name)
            if new is not None:
                _tree(broker, name)
            if (new if reverse else old) is not None:
                plan.append(f"{direction}: {name}")
        if dry_run:
            return plan
        # Durable marker precedes even mkdir/chown. A completed repeat makes no
        # metadata changes; normal broker writes retain these declared modes.
        if (progress.get("direction") == direction
                and progress.get("state") == "stable" and not plan):
            return []
        resumed = progress.get("direction") == direction and progress.get("state") == "migrating"
        progress = {"direction": direction, "state": "migrating",
                    "checkpointed": bool(resumed and progress.get("checkpointed"))}
        document = _mark(root, document, progress)
        if broker is None:
            os.mkdir(BROKER_DIR, 0o700, dir_fd=root)
            os.fsync(root)
            broker = stack.enter_context(_directory(BROKER_DIR, parent=root))
        _permissions(broker, 1002, 1101, PRIVATE_DIR_MODE)
        os.fsync(broker)
        source, destination = (broker, root) if reverse else (root, broker)
        if not progress["checkpointed"]:
            _checkpoint(source)
            progress["checkpointed"] = True
            document = _mark(root, document, progress)
            if after_step:
                after_step("checkpoint")
        uid, gid = (1001, 1001) if reverse else (1002, 1101)
        for name in names:
            if _stat(source, name) is not None:
                _tree(source, name, uid=uid, gid=gid)
                _rename(source, destination, name)
                if after_step:
                    after_step(name)
            elif _stat(destination, name) is not None:
                _tree(destination, name, uid=uid, gid=gid)
        _mark(root, document, {**progress, "state": "stable"})
        return plan
