"""SQLite verification confined to the one-shot old-layout cutover."""
import os
import sqlite3
import stat
from contextlib import closing

from tinyassets.restore_recovery import _safe


def _repair_staged_databases(staged):
    from tinyassets.box_layout_migration import _claim_sqlite_sidecars
    from tinyassets.sqlite_integrity import check

    verified = set()
    for path in sorted(staged.rglob("*")):
        # A sidecar of a database verified above was refused-or-claimed with
        # it, and closing that database may remove it; sorted() visits the
        # database first. Any other file, whatever its name, is inspected.
        if any(path.name.endswith(suffix) and path.with_name(path.name[:-len(suffix)]) in verified
               for suffix in ("-wal", "-shm", "-journal")):
            continue
        # Owner content includes links. Discover SQLite without following any
        # symlink; only database candidates need private-inode custody.
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode):
            continue
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, "rb") as stream:
            opened = os.fstat(stream.fileno())
            if (not stat.S_ISREG(opened.st_mode)
                    or (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino)):
                raise ValueError("staged file changed during database discovery")
            if stream.read(16) != b"SQLite format 3\x00":
                continue
        _safe(staged, path.relative_to(staged))
        for suffix in ("-wal", "-shm", "-journal"):
            _safe(staged, str(path.relative_to(staged)) + suffix)
        _claim_sqlite_sidecars(path)
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=rw", uri=True)) as db, db:
            check(db, repair=True)
        verified.add(path)


