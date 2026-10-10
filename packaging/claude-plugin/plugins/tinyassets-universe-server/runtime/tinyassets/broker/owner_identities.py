"""D60 broker-owned, append-only machine identities; never recycle a label.

Only the broker opens this database, inside its private state directory. A
missing database is an error except at explicit first-volume initialization.
Account erasure and rollback must retain it: files can outlive either action.

DA1 (owner-dynamic-admission): the same database holds the append-only,
generation-numbered ``center_admissions`` log, the only record of which command
centers are admitted. The broker derives each row's machine from the
principal's reservation; a caller never supplies it.
"""
from __future__ import annotations

import os
import re
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


@dataclass(frozen=True)
class AdmissionRow:
    generation: int
    event: str
    principal: str
    center: str
    machine: int


ADMISSION_EVENTS = ("admit", "retire")


class CenterUnadmitted(LookupError):
    """A retire named a center the log never admitted; nothing was appended."""
# The bounded bootstrap's center character set (deploy/role_owner_launcher.py).
_CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")


def validate_center(center: str) -> None:
    if not isinstance(center, str) or not _CENTER.fullmatch(center):
        raise ValueError("invalid command center name")


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
            db.execute("CREATE TABLE IF NOT EXISTS center_admissions ("
                       "generation INTEGER PRIMARY KEY AUTOINCREMENT, "
                       "event TEXT NOT NULL CHECK (event IN ('admit','retire')), "
                       "principal TEXT NOT NULL, center TEXT NOT NULL, "
                       "machine INTEGER NOT NULL CHECK(machine BETWEEN 300001 AND 399999), "
                       "UNIQUE(center, event))")
            for operation in ("DELETE", "UPDATE"):
                db.execute(f"CREATE TRIGGER IF NOT EXISTS no_admission_{operation.lower()} "
                           f"BEFORE {operation} ON center_admissions BEGIN "
                           "SELECT RAISE(ABORT, 'center admissions are append-only'); END")
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

    def _connect(self, *, write: bool) -> sqlite3.Connection:
        # mode=rw/ro: a lost database never becomes a fresh, empty log.
        return sqlite3.connect(self.path.as_uri() + ("?mode=rw" if write else "?mode=ro"),
                               uri=True, timeout=30)

    def admission(self, event: str, principal: str, center: str) -> AdmissionRow:
        """Append (or return the identical existing) admit/retire row; DA1."""
        if event not in ADMISSION_EVENTS:
            raise ValueError("invalid admission event")
        validate_principal(principal)
        validate_center(center)
        with closing(self._connect(write=True)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            rows = {row[1]: row for row in db.execute(
                "SELECT generation, event, principal, center, machine FROM center_admissions "
                "WHERE center=?", (center,))}
            admitted = rows.get("admit")
            if admitted is not None and admitted[2] != principal:
                raise PermissionError("center is admitted to another principal")
            if event == "admit":
                if "retire" in rows:
                    raise PermissionError("a retired center is never admitted again")
                if admitted is not None:
                    return _row(admitted)
                found = db.execute("SELECT machine_id FROM owner_identities WHERE principal=?",
                                   (principal,)).fetchone()
                if found is None:
                    raise LookupError("owner identity is not allocated")
                machine = found[0]
            else:
                if admitted is None:
                    raise CenterUnadmitted("retire requires a prior admit")
                if "retire" in rows:
                    return _row(rows["retire"])
                machine = admitted[4]
            cursor = db.execute("INSERT INTO center_admissions "
                                "(event, principal, center, machine) VALUES (?, ?, ?, ?)",
                                (event, principal, center, machine))
            return _row((cursor.lastrowid, event, principal, center, machine))

    def owner_machine(self, principal: str) -> int | None:
        """Read-only reservation lookup; never allocates."""
        validate_principal(principal)
        with closing(self._connect(write=False)) as db:
            found = db.execute("SELECT machine_id FROM owner_identities WHERE principal=?",
                               (principal,)).fetchone()
        if found is None:
            return None
        if type(found[0]) is not int or not OWNER_ID_FIRST <= found[0] <= OWNER_ID_LAST:
            raise RuntimeError("invalid durable owner identity")
        return found[0]

    def center_state(self, center: str) -> str:
        validate_center(center)
        with closing(self._connect(write=False)) as db:
            events = {row[0] for row in db.execute(
                "SELECT event FROM center_admissions WHERE center=?", (center,))}
        return "retired" if "retire" in events else "admitted" if events else "unadmitted"

    def admitted_owner(self, center: str) -> str:
        """The mapper's live owner binding; no allocation or inferred grants."""
        validate_center(center)
        with closing(self._connect(write=False)) as db:
            rows = dict(db.execute(
                "SELECT event, principal FROM center_admissions WHERE center=?", (center,)))
        if 'admit' not in rows or 'retire' in rows:
            raise PermissionError('center has no live owner admission')
        validate_principal(rows['admit'])
        return rows['admit']

    def admission_row(self, generation: int) -> AdmissionRow | None:
        if type(generation) is not int or generation < 1:
            raise ValueError("invalid admission generation")
        with closing(self._connect(write=False)) as db:
            found = db.execute("SELECT generation, event, principal, center, machine "
                               "FROM center_admissions WHERE generation=?",
                               (generation,)).fetchone()
        return None if found is None else _row(found)

    def admissions_after(self, generation: int) -> list[AdmissionRow]:
        """Ordered log delta for the restart contract (DA7)."""
        if type(generation) is not int or generation < 0:
            raise ValueError("invalid admission generation")
        with closing(self._connect(write=False)) as db:
            rows = db.execute("SELECT generation, event, principal, center, machine "
                              "FROM center_admissions WHERE generation>? ORDER BY generation",
                              (generation,)).fetchall()
        return [_row(row) for row in rows]


def _row(values) -> AdmissionRow:
    generation, event, principal, center, machine = values
    if (type(generation) is not int or generation < 1 or event not in ADMISSION_EVENTS
            or type(machine) is not int or not OWNER_ID_FIRST <= machine <= OWNER_ID_LAST):
        raise RuntimeError("invalid durable admission row")
    return AdmissionRow(generation, event, principal, center, machine)


def _owner_request(data_root: Path, request: dict) -> dict:
    import socket

    from tinyassets import rpc_frames as rf
    from tinyassets.broker.supervisor import get_supervisor

    supervisor = get_supervisor(data_root)
    if supervisor is None:
        raise RuntimeError("owner identity broker is unavailable")
    generation, token = supervisor.fence()
    request = {**request, "generation": generation, "token": token}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(30)
        channel.connect(os.fspath(supervisor.socket_path))
        supervisor.verify_broker(channel)
        channel.sendall(rf.control(rf.CONNECTION, request))
        frame = rf.read_frame_blocking(channel)
        if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
            raise RuntimeError("invalid owner identity reply")
        return frame.control()


def center_admission(data_root: Path, *, event: str, principal: str,
                     center: str) -> tuple[int, int]:
    """Append an admit/retire row through the broker; returns (generation, machine)."""
    if event not in ADMISSION_EVENTS:
        raise ValueError("invalid admission event")
    validate_principal(principal)
    validate_center(center)
    answer = _owner_request(data_root, {"op": "CENTER_ADMISSION", "event": event,
                                        "principal": principal, "center": center})
    if answer == {"op": "CENTER_ADMISSION_UNADMITTED"}:
        raise CenterUnadmitted("the admission log never admitted this center")
    if (set(answer) != {"op", "generation", "machine"}
            or answer["op"] != "CENTER_ADMISSION_IS"
            or type(answer["generation"]) is not int or answer["generation"] < 1
            or type(answer["machine"]) is not int
            or not OWNER_ID_FIRST <= answer["machine"] <= OWNER_ID_LAST):
        raise RuntimeError("center admission refused")
    return answer["generation"], answer["machine"]


def owner_identity(data_root: Path, *, principal: str, allocate: bool = False) -> OwnerIdentity:
    """Resolve through the authenticated broker; never open its database locally."""
    validate_principal(principal)
    if type(allocate) is not bool:
        raise ValueError("allocate must be boolean")
    answer = _owner_request(data_root, {"op": "OWNER_IDENTITY", "principal": principal,
                                        "allocate": allocate})
    if (set(answer) != {"op", "uid", "gid"} or answer["op"] != "OWNER_IDENTITY_IS"
            or type(answer["uid"]) is not int or type(answer["gid"]) is not int
            or answer["uid"] != answer["gid"]
            or not OWNER_ID_FIRST <= answer["uid"] <= OWNER_ID_LAST):
        raise RuntimeError("owner identity refused")
    return OwnerIdentity(answer["uid"], answer["gid"])


def admitted_owner(data_root: Path, *, center: str) -> str:
    """Read the mapper's owner binding through authenticated broker IPC."""
    validate_center(center)
    answer = _owner_request(data_root, {'op': 'ADMITTED_OWNER', 'center': center})
    if set(answer) != {'op', 'principal'} or answer['op'] != 'ADMITTED_OWNER_IS':
        raise PermissionError('center owner admission refused')
    validate_principal(answer['principal'])
    return answer['principal']
