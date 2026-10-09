"""Center-admission helpers for the bootstrap and the one-time migration (stdlib only).

The canonical root label every admitted center carries, the admission staging
sweep the migration runs with writers stopped, and the broker's append-only
admission log (DA1), read or appended only by a child that has fully retired
to the broker identity. Root never opens the broker-private database.
"""

from __future__ import annotations

import errno
import json
import os
import re
import signal
import stat
import struct
import time

STAGING = ".role-admission"
ACCESS, DEFAULT = "system.posix_acl_access", "system.posix_acl_default"
_CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")


class ContractRefused(RuntimeError):
    """The admission log or staging area is not in a shape this code accepts."""


# Canonical root label: 1001:<owner gid> 0750, the owner r-x and the broker --x.
def canonical_root_acl(machine):
    undefined = 0xFFFFFFFF
    entries = [(1, 7, undefined), (2, 1, 1002), (2, 5, machine), (4, 0, undefined),
               (16, 5, undefined), (32, 0, undefined)]
    return struct.pack("<I", 2) + b"".join(struct.pack("<HHI", *e) for e in entries)


def canonical_label(machine):
    return (1001, machine, 0o750, canonical_root_acl(machine), None)


def _xattr(fd, name):
    try:
        return os.getxattr(fd, name)
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise
        return None


def read_label(fd):
    info = os.fstat(fd)
    return (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
            _xattr(fd, ACCESS), _xattr(fd, DEFAULT))


def clear_staging(root):
    """Remove ``.role-admission/`` with writers stopped: every entry in it is an
    unpublished admission remnant (DA4). Returns the number of names removed."""
    try:
        info = os.stat(STAGING, dir_fd=root, follow_symlinks=False)
    except FileNotFoundError:
        return 0
    if not stat.S_ISDIR(info.st_mode):
        raise ContractRefused("admission staging is not a directory")
    return _remove(root, STAGING)


def _remove(parent, name):
    removed = 0
    fd = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                 dir_fd=parent)
    try:
        for entry in os.listdir(fd):
            info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                removed += _remove(fd, entry)
            else:
                os.unlink(entry, dir_fd=fd)
                removed += 1
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)
    os.fsync(parent)
    return removed + 1


def _rows(rows, after):
    """The log rows above ``after``, in generation order, or a refusal."""
    ordered = sorted(rows, key=lambda row: row["generation"])
    for row in ordered:
        if (set(row) != {"generation", "event", "principal", "center", "machine"}
                or type(row["generation"]) is not int or row["generation"] <= after
                or row["event"] not in ("admit", "retire")
                or not isinstance(row["center"], str) or not _CENTER.fullmatch(row["center"])):
            raise ContractRefused("invalid admission log delta")
    return ordered


# The image's interpreter and application; a root oracle outside it substitutes
# its own (the child still retires to the broker before exec).
PYTHON, APP = "/opt/venv/bin/python", "/app"
_BROKER_LOG = (
    "import sys,json; from pathlib import Path; sys.path.insert(0,sys.argv[3]); "
    "from tinyassets.broker.owner_identities import OwnerIdentities; "
    "db=OwnerIdentities(Path(sys.argv[1])/'.broker/state/owner-identities.db'); "
    "req=json.loads(sys.argv[2]); "
    "[db.admission('admit',p,c) for p,c in req['append']]; "
    "rows=[r.__dict__ for r in db.admissions_after(req['after'])]; "
    "owners={p:db.owner_machine(p) for p in req['owners']}; "
    "sys.stdout.write(json.dumps({'rows':rows,'owners':owners}))"
)


def _broker_child(data_root, launch, request, timeout):
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(reader)
            os.dup2(writer, 1)
            launch["close_descriptors"]({0, 1, 2})
            launch["retire_child"]("broker")
            os.execve(PYTHON,
                      ["python", "-I", "-B", "-c", _BROKER_LOG, str(data_root),
                       json.dumps(request), APP],
                      {"PATH": "/opt/venv/bin:/usr/bin:/bin", "PYTHONDONTWRITEBYTECODE": "1"})
        except BaseException:
            os._exit(126)
    os.close(writer)
    output = b""
    deadline = time.monotonic() + timeout
    try:
        while True:
            chunk = os.read(reader, 65536)
            if not chunk:
                break
            output += chunk
            if time.monotonic() >= deadline or len(output) > 64 * 1024 * 1024:
                raise ContractRefused("retired broker log read exceeded its bound")
    finally:
        os.close(reader)
        if time.monotonic() >= deadline:
            os.kill(child, signal.SIGKILL)
        _, status = os.waitpid(child, 0)
    if status:
        raise ContractRefused("retired broker admission log access failed")
    return json.loads(output)


def broker_log(data_root, launch, *, after=0, append=(), timeout=60):
    """Append admit rows, then read the delta, as a fully retired broker child.

    Appends are idempotent (DA1): an identical row is returned and a conflicting
    one refuses. Returns the rows above ``after``.
    """
    answer = _broker_child(data_root, launch, {"after": after, "owners": [],
                                               "append": [list(p) for p in append]}, timeout)
    return _rows(answer["rows"], after)


def reservations(data_root, launch, principals, *, timeout=60):
    """Durable reservations, never allocating."""
    answer = _broker_child(data_root, launch, {"after": 0, "append": [],
                                               "owners": sorted(set(principals))}, timeout)
    return answer["owners"]
