"""D60 broker-owned, append-only machine identities; never recycle a label.

Only the broker opens this database, inside its private state directory. A
missing database is an error except at explicit first-volume initialization.
Account erasure and rollback must retain it: files can outlive either action.
"""
from __future__ import annotations

import os
import sqlite3
import stat
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

# D62: 300000 is the bounded launcher; owners never share its identity.
# The entire range is disjoint from D1's per-box 200000..299999 reservation.
OWNER_ID_FIRST = 300001
OWNER_ID_LAST = 399999


@dataclass(frozen=True)
class OwnerIdentity:
    uid: int
    gid: int


def validate_principal(principal: str) -> None:
    if (not isinstance(principal, str) or not principal.strip()
            or principal != principal.strip() or len(principal) > 512
            or not principal.isprintable()):
        raise ValueError("invalid owner principal")


class OwnerIdentities:
    def __init__(self, path: Path, *, initialize: bool = False) -> None:
        self.path = Path(path)
        parent = self.path.parent.lstat()
        if (not stat.S_ISDIR(parent.st_mode) or parent.st_uid != os.geteuid()
                or stat.S_IMODE(parent.st_mode) & 0o077):
            raise PermissionError("owner identities require a private broker directory")
        flags = os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK
        if initialize:
            try:
                fd = os.open(self.path, flags | os.O_CREAT | os.O_EXCL, 0o600)
            except FileExistsError:
                fd = os.open(self.path, flags)
        else:
            fd = os.open(self.path, flags)
        try:
            info = os.fstat(fd)
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
                raise PermissionError("unsafe owner identity database")
            os.fsync(fd)
        finally:
            os.close(fd)
        with closing(sqlite3.connect(self.path)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE IF NOT EXISTS owner_identities ("
                       "principal TEXT PRIMARY KEY NOT NULL, "
                       "machine_id INTEGER NOT NULL UNIQUE "
                       "CHECK(machine_id BETWEEN 300001 AND 399999))")
            for operation in ("DELETE", "UPDATE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS no_identity_{operation.lower()} "
                           f"BEFORE {operation} ON owner_identities BEGIN "
                           "SELECT RAISE(ABORT, 'owner identities are permanent'); END")
        directory = os.open(self.path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)

    def resolve(self, principal: str, *, allocate: bool = False) -> OwnerIdentity:
        validate_principal(principal)
        # mode=rw prevents a lost database from becoming a fresh allocation map.
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=rw", uri=True,
                                     timeout=30)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE" if allocate else "BEGIN")
            row = db.execute("SELECT machine_id FROM owner_identities WHERE principal=?",
                             (principal,)).fetchone()
            if row is None:
                if not allocate:
                    raise LookupError("owner identity is not allocated")
                maximum = db.execute("SELECT MAX(machine_id) FROM owner_identities").fetchone()[0]
                machine_id = OWNER_ID_FIRST if maximum is None else maximum + 1
                if machine_id > OWNER_ID_LAST:
                    raise RuntimeError("owner identity range exhausted")
                db.execute("INSERT INTO owner_identities VALUES (?, ?)", (principal, machine_id))
            else:
                machine_id = row[0]
            if type(machine_id) is not int or not OWNER_ID_FIRST <= machine_id <= OWNER_ID_LAST:
                raise RuntimeError("invalid durable owner identity")
            return OwnerIdentity(machine_id, machine_id)


def owner_identity(data_root: Path, *, principal: str, allocate: bool = False) -> OwnerIdentity:
    """Resolve through the authenticated broker; never open its database locally."""
    import socket

    from tinyassets import rpc_frames as rf
    from tinyassets.broker.supervisor import get_supervisor

    validate_principal(principal)
    if type(allocate) is not bool:
        raise ValueError("allocate must be boolean")
    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise RuntimeError("owner identity broker is unavailable")
    generation, token = supervisor.fence()
    request = {"op": "OWNER_IDENTITY", "principal": principal, "allocate": allocate,
               "generation": generation, "token": token}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        channel.connect(os.fspath(supervisor.socket_path))
        supervisor.verify_broker(channel)
        channel.sendall(rf.control(rf.CONNECTION, request))
        frame = rf.read_frame_blocking(channel)
        if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
            raise RuntimeError("invalid owner identity reply")
        answer = frame.control()
    if (set(answer) != {"op", "uid", "gid"} or answer["op"] != "OWNER_IDENTITY_IS"
            or type(answer["uid"]) is not int or type(answer["gid"]) is not int
            or answer["uid"] != answer["gid"]
            or not OWNER_ID_FIRST <= answer["uid"] <= OWNER_ID_LAST):
        raise RuntimeError("owner identity refused")
    return OwnerIdentity(answer["uid"], answer["gid"])
