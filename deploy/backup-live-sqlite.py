#!/usr/bin/env python3
"""Replace every SQLite file in a live tree copy with an online-backup copy.

backup-owner-box.sh copies the data volume and owner trees while every
service keeps serving (24/7: the nightly backup never pauses the app). A plain
or reflink copy of a database that is being written can be torn, so each
SQLite file in COPY is re-taken from its LIVE source through sqlite3's backup
API, which reads one transactionally consistent snapshot without blocking WAL
writers. The copy's stale -wal/-shm/-journal siblings are dropped: the backup
already contains every committed page.

Usage: backup-live-sqlite.py LIVE_ROOT COPY_ROOT
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

MAGIC = b"SQLite format 3\x00"
SIDECARS = ("-wal", "-shm", "-journal")


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _drop_sidecars(path: Path) -> None:
    for suffix in SIDECARS:
        Path(str(path) + suffix).unlink(missing_ok=True)


def retake(live: Path, copy: Path) -> None:
    if live.is_symlink() or not live.is_file():
        # Deleted (or replaced by a non-file) since the copy: the live tree
        # no longer holds this database, so neither does the backup.
        copy.unlink()
        _drop_sidecars(copy)
        return
    stat = live.stat()
    temporary = copy.with_name(copy.name + ".backup-tmp")
    temporary.unlink(missing_ok=True)
    source = sqlite3.connect(live.as_uri() + "?mode=ro", uri=True)
    try:
        target = sqlite3.connect(temporary)
        try:
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()
    os.chown(temporary, stat.st_uid, stat.st_gid)
    os.chmod(temporary, stat.st_mode & 0o7777)
    os.replace(temporary, copy)
    _drop_sidecars(copy)


def main(live_root: Path, copy_root: Path) -> int:
    taken = 0
    for directory, _dirs, files in os.walk(copy_root):
        for name in files:
            copy = Path(directory) / name
            if copy.is_symlink() or not _is_sqlite(copy):
                continue
            retake(live_root / copy.relative_to(copy_root), copy)
            taken += 1
    print(f"online-backup copies: {taken} SQLite file(s) under {copy_root}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    sys.exit(main(Path(sys.argv[1]), Path(sys.argv[2])))
