"""N10 one-shot: boxhostd lifts a box's old authority files before the box starts.

Before N10 a box held its owner's action rules (``POLICY_FILE`` at the volume
root) and effector consents (``CONSENTS_FILE`` in each center's sidecar
folder). The broker holds both now. On a wake the broker marks pending,
boxhostd reads those files from the attached volume BEFORE the sandbox starts
and returns the extracted rows; the broker validates and imports them under
the wake's owner lock. The box never touches them again.

Everything on the volume is the owner's, so it is hostile input to root code:
no link is followed at any level, each file must be one regular file with one
link and a bounded size, and SQLite parses a private copy opened immutable and
read-only, with a WAL folded in first, under hard limits (value length, SQL
shape), one step budget and one wall-clock deadline for the whole lift (all
files together), and a row bound. Both names must be one plain table (never a
view or a virtual table). Any error yields no rows and an error count, which
the broker turns into the defaults, an ALARM and a notice to the owner.
The files are removed only by ``remove``, after a lift with no error was held.

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
import time
from contextlib import closing
from pathlib import Path

POLICY_FILE = ".action-policy.db"
CONSENTS_FILE = ".effector_consents.db"
SIDECARS = ".universe-sidecars"
MAX_BYTES = 8 * 1024 * 1024
MAX_CENTERS = 256
MAX_ROWS = 10_000
MAX_VALUE_BYTES = 1024 * 1024
STEP_BUDGET = 20_000_000
DEADLINE_S = 5.0
_STEPS = 1000
_SUFFIXES = ("", "-wal", "-shm", "-journal")
_LIMITS = (
    (sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES),
    (sqlite3.SQLITE_LIMIT_SQL_LENGTH, 100_000),
    (sqlite3.SQLITE_LIMIT_COLUMN, 64),
    (sqlite3.SQLITE_LIMIT_EXPR_DEPTH, 64),
    (sqlite3.SQLITE_LIMIT_COMPOUND_SELECT, 2),
    (sqlite3.SQLITE_LIMIT_VDBE_OP, 25_000),
    (sqlite3.SQLITE_LIMIT_FUNCTION_ARG, 8),
    (sqlite3.SQLITE_LIMIT_ATTACHED, 0),
    (sqlite3.SQLITE_LIMIT_LIKE_PATTERN_LENGTH, 256),
    (sqlite3.SQLITE_LIMIT_TRIGGER_DEPTH, 1),
)


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


class _Budget:
    """One wall-clock deadline and one step budget for the whole lift, all files."""

    def __init__(self) -> None:
        self.deadline = time.monotonic() + DEADLINE_S
        self.spent = 0

    def exhausted(self) -> bool:
        return self.spent > STEP_BUDGET or time.monotonic() > self.deadline


def _harden(db: sqlite3.Connection, budget: _Budget) -> None:
    for limit, value in _LIMITS:
        db.setlimit(limit, value)

    def progress() -> int:
        budget.spent += _STEPS
        return int(budget.exhausted())

    db.set_progress_handler(progress, _STEPS)
    db.execute("PRAGMA trusted_schema=OFF")
    db.execute("PRAGMA cell_size_check=ON")


def _rows(name: str, parent: int, private: Path, table: str, query: str,
          budget: _Budget) -> list | None:
    """The query's rows from a private immutable copy, or None when there is no file."""
    if budget.exhausted():
        raise ValueError("authority lift budget exhausted")
    data = _read(name, parent)
    if data is None:
        return None
    wal = _read(name + "-wal", parent)
    work = Path(tempfile.mkdtemp(dir=private))
    try:
        copy = work / "copy.db"
        copy.write_bytes(data)
        if wal:
            # Fold the WAL into the private copy once, then never read it again.
            (work / "copy.db-wal").write_bytes(wal)
            with closing(sqlite3.connect(copy, timeout=0, isolation_level=None)) as db:
                _harden(db, budget)
                busy, logged, folded = db.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
                if busy or logged != folded:
                    raise ValueError("authority WAL did not fold")
            for suffix in ("-wal", "-shm"):
                (work / ("copy.db" + suffix)).unlink(missing_ok=True)
        uri = copy.as_uri() + "?mode=ro&immutable=1"
        with closing(sqlite3.connect(uri, uri=True, timeout=0)) as db:
            _harden(db, budget)
            db.execute("PRAGMA query_only=ON")
            schema = db.execute("SELECT type, sql FROM sqlite_master "
                                "WHERE lower(name)=lower(?)", (table,)).fetchall()
            if (len(schema) != 1 or schema[0][0] != "table"
                    or not str(schema[0][1] or "").lstrip().upper().startswith("CREATE TABLE")):
                raise ValueError(f"{table} is not one plain table")
            rows = db.execute(query).fetchmany(MAX_ROWS + 1)
        if len(rows) > MAX_ROWS:
            raise ValueError("too many authority rows")
        return rows
    finally:
        shutil.rmtree(work, ignore_errors=True)


def _targets(root: int, opened: list[int]) -> list[tuple[str, int]]:
    targets = [(POLICY_FILE, root)]
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
    return targets


def _policy_entries(rows: list) -> list:
    found = json.loads(rows[0][0]).get("entries", []) if rows else []
    if not isinstance(found, list) or len(found) > MAX_ROWS:
        raise ValueError("policy entries are not a bounded list")
    return found


def extract(mountpoint: Path, private: Path) -> dict:
    """Lift the old rows, touching nothing; ``errors`` counts what could not be read."""
    entries, consents, errors, found = [], [], 0, 0
    budget = _Budget()
    private.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    opened = [root]
    try:
        try:
            targets = _targets(root, opened)
        except (OSError, ValueError):
            targets, errors = [(POLICY_FILE, root)], errors + 1
        for name, parent in targets:
            try:
                if name == POLICY_FILE:
                    rows = _rows(name, parent, private, "policy",
                                 "SELECT document FROM policy WHERE singleton=1", budget)
                    if rows is not None:
                        found += 1
                        entries += _policy_entries(rows)
                else:
                    rows = _rows(name, parent, private, "effector_consents",
                                 "SELECT sink,destination,granted_at,granted_by "
                                 "FROM effector_consents WHERE revoked_at IS NULL", budget)
                    if rows is not None:
                        found += 1
                        consents += [
                            {"sink": sink, "destination": destination,
                             "granted_at": granted_at, "granted_by": granted_by}
                            for sink, destination, granted_at, granted_by in rows]
                if len(entries) > MAX_ROWS or len(consents) > MAX_ROWS:
                    raise ValueError("too many authority rows")
            except Exception:  # noqa: BLE001 - hostile input: every failure is an error count
                errors += 1
    finally:
        for fd in opened:
            os.close(fd)
    if errors:
        entries, consents = [], []
    canonical = json.dumps({"entries": entries, "consents": consents}, sort_keys=True,
                           separators=(",", ":"), default=str)
    return {"source_digest": "sha256:" + hashlib.sha256(canonical.encode()).hexdigest(),
            "entries": entries, "consents": consents, "errors": errors, "found": found}


def remove(mountpoint: Path) -> None:
    """Delete the old files (unlinkat never follows), once their rows are held."""
    root = os.open(mountpoint, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    opened = [root]
    try:
        for name, parent in _targets(root, opened):
            for suffix in _SUFFIXES:
                try:
                    os.unlink(name + suffix, dir_fd=parent)
                except FileNotFoundError:
                    pass
    finally:
        for fd in opened:
            os.close(fd)
