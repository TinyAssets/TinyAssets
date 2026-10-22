"""D60 broker-owned, append-only machine identities; never recycle a label.

Only the broker opens this database, inside its private state directory. A
missing database is an error except at explicit first-volume initialization.
Account erasure and rollback must retain it: files can outlive either action.

"""
from __future__ import annotations

import os
import secrets
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
    def __init__(self, path: Path, *, initialize: bool = False, plans=None) -> None:
        from tinyassets.broker.owner_reads import Plans

        self.path = Path(path)
        self.work_ready = True
        self.plans = plans if plans is not None else Plans(broker_dir=self.path.parent)
        self.plans.admission_path = self.path
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
            db.execute("CREATE TABLE IF NOT EXISTS owner_box_secrets ("
                       "principal TEXT PRIMARY KEY NOT NULL, secret TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS routing_sequence (revision INTEGER NOT NULL)")
            db.execute("INSERT INTO routing_sequence SELECT 0 WHERE NOT EXISTS "
                       "(SELECT 1 FROM routing_sequence)")
            from tinyassets.broker.owner_volumes import schema

            schema(db)
            from tinyassets.broker.deletion_jobs import schema as job_schema

            job_schema(db)
            if initialize:
                from tinyassets.broker.admission import schema as admission_schema

                admission_schema(db)
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

    async def admit(self, principal: str, *, routing) -> OwnerIdentity:
        if routing is None:
            raise RuntimeError("admission mutation requires routing")
        from tinyassets.broker.admission import thread_work

        identity = await thread_work(self._admit, principal)
        await routing.changed(principal)
        return identity

    def _admit(self, principal: str) -> OwnerIdentity:
        """Admit one owner, reserving a new one's permanent identity first.

        Centers never enter the shared map (C11). A first sign-in is the only
        way an owner arrives after cutover, so admission reserves the label;
        an erased owner is refused before any label is spent, and recovery
        (C3) keeps admission closed.
        """
        from tinyassets.broker.admission import OwnerErased, OwnerUnadmitted, admission, mutate

        if not self.work_ready:
            raise OwnerUnadmitted("platform recovery admission is closed")
        with closing(self._connect(write=False)) as db:
            if admission(db, principal).state == "erased":
                raise OwnerErased("owner account is deleted (erased)")
        identity = self.resolve(principal, allocate=True)
        with closing(self._connect(write=True)) as db, db:
            self.plans.attach_admission(db)
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            first = admission(db, principal).state == "absent"
            mutate(db, principal, "admit")
            self.plans.admit_free(db, principal, first_admission=first)
            db.execute("INSERT OR IGNORE INTO owner_box_secrets VALUES (?, ?)",
                       (principal, secrets.token_urlsafe(32)))
        return identity

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

    def box_route(self, principal: str) -> tuple[int, str]:
        """An admitted owner's socket identity and secret; never allocate a machine."""
        validate_principal(principal)
        with closing(self._connect(write=True)) as db, db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            from tinyassets.broker.admission import admission

            admission(db, principal).require_work()
            row = db.execute("SELECT machine_id FROM owner_identities WHERE principal=?",
                             (principal,)).fetchone()
            if row is None:
                raise LookupError("owner identity is missing")
            secret = db.execute("SELECT secret FROM owner_box_secrets WHERE principal=?",
                                (principal,)).fetchone()
            if secret is None:
                raise LookupError("owner ingress credential is missing")
            return row[0], secret[0]


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
        channel.settimeout(70 if request["op"] == "OWNER_BOX_WAKE" else 30)
        channel.connect(os.fspath(supervisor.socket_path))
        supervisor.verify_broker(channel)
        channel.sendall(rf.control(rf.CONNECTION, request))
        frame = rf.read_frame_blocking(channel)
        if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
            raise RuntimeError("invalid owner identity reply")
        return frame.control()


def owner_admission(data_root: Path, owner: str):
    """Read the sole admission record through authenticated broker IPC (C6)."""
    from tinyassets.broker.admission import OwnerAdmissionRecord

    answer = _owner_request(data_root, {"op": "OWNER_ADMISSION", "principal": owner})
    if (set(answer) != {"op", "state", "reasons"}
            or answer["op"] != "OWNER_ADMISSION_IS"
            or answer["state"] not in {"absent", "admitted", "suspended", "erased"}):
        raise PermissionError("owner admission unavailable")
    return OwnerAdmissionRecord(answer["state"], frozenset(answer["reasons"]))


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


def wake_owner(data_root: Path, *, principal: str) -> None:
    """Cold wake only. Awake forwarding never calls the control plane."""
    validate_principal(principal)
    answer = _owner_request(data_root, {"op": "OWNER_BOX_WAKE", "principal": principal})
    if answer == {"op": "OWNER_BOX_UNADMITTED"}:
        raise LookupError("owner has no live admission")
    if answer != {"op": "OWNER_LIFECYCLE_DONE"}:
        raise RuntimeError("owner wake refused")


def admit_owner(data_root: Path, *, principal: str) -> int:
    answer = _owner_request(data_root, {"op": "ADMIT_OWNER", "principal": principal})
    if set(answer) != {"op", "machine"} or answer["op"] != "OWNER_ADMIT_IS":
        raise PermissionError("owner admission refused")
    return answer["machine"]
