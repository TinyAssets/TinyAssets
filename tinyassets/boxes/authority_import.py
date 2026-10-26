"""N10 one-shot: boxhostd lifts a box's old authority files before the box starts.

Before N10 a box held its owner's action rules (``POLICY_FILE`` at the volume
root) and effector consents (``CONSENTS_FILE`` in each center's sidecar
folder). The broker holds both now. On a wake the broker marks pending,
boxhostd reads those files from the attached volume BEFORE the sandbox starts,
deletes them, and returns the extracted rows; the broker validates and imports
them under the wake's owner lock. The box never touches them again.

Everything on the volume is the owner's, so it is untrusted here: no link is
followed at any level, each file must be one regular file with one link and a
bounded size, and SQLite parses a private copy. Any error yields no rows and an
error count, which the broker turns into defaults and an ALARM receipt.

Delete this module once every owner's import has settled.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import stat
import tempfile
from contextlib import closing
from pathlib import Path

POLICY_FILE = ".action-policy.db"
CONSENTS_FILE = ".effector_consents.db"
SIDECARS = ".universe-sidecars"
MAX_BYTES = 8 * 1024 * 1024
MAX_CENTERS = 256
_SUFFIXES = ("", "-wal", "-shm", "-journal")


def _open_dir(name: str, parent: int) -> int | None:
    try:
        return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                       dir_fd=parent)
    except FileNotFoundError:
        return None


def _read(name: str, parent: int) -> bytes | None:
    """One regular, singly linked, bounded file; never a link at any level."""
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=parent)
    except FileNotFoundError:
        return None
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_size > MAX_BYTES):
            raise ValueError("authority file is not one bounded regular file")
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("authority file is oversized")
    return data


def _rows(name: str, parent: int, private: Path, query: str) -> list:
    data = _read(name, parent)
    if data is None:
        return []
    wal = _read(name + "-wal", parent)
    work = Path(tempfile.mkdtemp(dir=private))
    try:
        copy = work / "copy.db"
        copy.write_bytes(data)
        if wal is not None:
            (work / "copy.db-wal").write_bytes(wal)
        with closing(sqlite3.connect(copy, timeout=1)) as db:
            db.execute("PRAGMA trusted_schema=OFF")
            db.execute("PRAGMA cell_size_check=ON")
            return db.execute(query).fetchall()
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _unlink(name: str, parent: int) -> None:
    for suffix in _SUFFIXES:
        try:
            os.unlink(name + suffix, dir_fd=parent)  # unlinkat never follows
        except FileNotFoundError:
            pass


def extract(mountpoint: Path, private: Path) -> dict:
    """Lift and delete the old files; ``errors`` counts what could not be read."""
    entries, consents, errors = [], [], 0
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    opened = [root]
    try:
        targets = [(POLICY_FILE, root)]
        try:
            sidecars = _open_dir(SIDECARS, root)
            if sidecars is not None:
                opened.append(sidecars)
                names = sorted(os.listdir(sidecars))
                if len(names) > MAX_CENTERS:
                    raise ValueError("too many center sidecars")
                for name in names:
                    center = _open_dir(name, sidecars)
                    if center is not None:
                        opened.append(center)
                        targets.append((CONSENTS_FILE, center))
        except (OSError, ValueError):
            errors += 1
        for name, parent in targets:
            try:
                if name == POLICY_FILE:
                    rows = _rows(name, parent, private,
                                 "SELECT document FROM policy WHERE singleton=1")
                    found = json.loads(rows[0][0]).get("entries", []) if rows else []
                    if not isinstance(found, list):
                        raise ValueError("policy entries are not a list")
                    entries += found
                else:
                    consents += [
                        {"sink": sink, "destination": destination,
                         "granted_at": granted_at, "granted_by": granted_by}
                        for sink, destination, granted_at, granted_by in _rows(
                            name, parent, private,
                            "SELECT sink,destination,granted_at,granted_by "
                            "FROM effector_consents WHERE revoked_at IS NULL")]
            except (OSError, ValueError, TypeError, AttributeError, sqlite3.Error):
                errors += 1
            finally:
                _unlink(name, parent)
    finally:
        for fd in opened:
            os.close(fd)
    if errors:
        entries, consents = [], []
    canonical = json.dumps({"entries": entries, "consents": consents}, sort_keys=True,
                           separators=(",", ":"), default=str)
    return {"source_digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
            "entries": entries, "consents": consents, "errors": errors}
