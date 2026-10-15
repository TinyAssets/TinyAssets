#!/usr/bin/env python3
"""Replace every SQLite file in a live tree copy with an online-backup copy.

backup-owner-box.sh copies the data volume and owner trees while every
service keeps serving (24/7: the nightly backup never pauses the app). A plain
or reflink copy of a database that is being written can be torn, so each
SQLite file in COPY is re-taken from its LIVE source through sqlite3's backup
API, which reads one transactionally consistent snapshot without blocking WAL
writers. The copy's stale -wal/-shm/-journal siblings are dropped: the backup
already contains every committed page. A database created after the tree copy
passed its directory is taken from the live tree too.

``--as-owner`` (owner trees) drops to the live root's uid/gid first, so a
symlink the owner plants can never make root read another owner's data, and an
owner file that cannot be read cleanly is left out with a warning instead of
failing everyone's backup. The data volume is not owner-writable and runs as
root; there only a file that vanished mid-backup is skipped.

Usage: backup-live-sqlite.py [--as-owner] LIVE_ROOT COPY_ROOT
"""

from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path

MAGIC = b"SQLite format 3\x00"
SIDECARS = ("-wal", "-shm", "-journal")


class Skip(Exception):
    """This file is left out of the backup; the message says why."""


def _is_sqlite(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(len(MAGIC)) == MAGIC
    except OSError:
        return False


def _drop(copy: Path) -> None:
    for name in ("", *SIDECARS, ".backup-tmp"):
        Path(str(copy) + name).unlink(missing_ok=True)


def _plain(live_root: Path, live: Path) -> bool:
    """No component of ``live`` below the root is a symlink."""
    return os.path.realpath(live) == str(live) and live.is_relative_to(live_root)


def retake(live_root: Path, live: Path, copy: Path) -> None:
    if not live.exists() or live.is_symlink() or not live.is_file():
        # Deleted (or replaced by a non-file) since the copy: the live tree
        # no longer holds this database, so neither does the backup.
        _drop(copy)
        return
    if not _plain(live_root, live):
        raise Skip("a parent directory is a symlink")
    try:
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
    except FileNotFoundError as exc:
        raise Skip("vanished during the backup") from exc
    except sqlite3.OperationalError as exc:
        if not live.exists():
            raise Skip("vanished during the backup") from exc
        raise
    os.chown(temporary, stat.st_uid, stat.st_gid)
    os.chmod(temporary, stat.st_mode & 0o7777)
    os.replace(temporary, copy)
    for suffix in SIDECARS:
        Path(str(copy) + suffix).unlink(missing_ok=True)


def _make_parents(live_root: Path, copy_root: Path, relative: Path) -> None:
    """Create missing copy directories with their live owner and mode."""
    for part in reversed(relative.parents[:-1]):
        target = copy_root / part
        if target.is_dir():
            continue
        try:
            stat = (live_root / part).lstat()
        except FileNotFoundError as exc:
            raise Skip("vanished during the backup") from exc
        target.mkdir()
        os.chown(target, stat.st_uid, stat.st_gid)
        os.chmod(target, stat.st_mode & 0o7777)


def _take(live_root: Path, live: Path, copy: Path, *, owner: bool) -> bool:
    try:
        retake(live_root, live, copy)
        return True
    except Skip as exc:
        reason = str(exc)
    except (OSError, sqlite3.Error) as exc:
        if not owner:
            raise
        reason = type(exc).__name__
    _drop(copy)
    print(f"WARN: left out of the backup ({reason}): {live}", file=sys.stderr)
    return False


def _drop_privileges(live_root: Path) -> None:
    stat = live_root.lstat()
    if os.geteuid() == 0 and stat.st_uid != 0:
        os.setgroups([])
        os.setgid(stat.st_gid)
        os.setuid(stat.st_uid)


def main(live_root: Path, copy_root: Path, *, owner: bool = False) -> int:
    live_root = Path(os.path.realpath(live_root))
    if owner:
        _drop_privileges(live_root)
    taken = 0
    for directory, _dirs, files in os.walk(copy_root):
        for name in files:
            copy = Path(directory) / name
            if copy.is_symlink() or not _is_sqlite(copy):
                continue
            live = live_root / copy.relative_to(copy_root)
            taken += _take(live_root, live, copy, owner=owner)
    for directory, _dirs, files in os.walk(live_root):
        for name in files:
            live = Path(directory) / name
            relative = live.relative_to(live_root)
            copy = copy_root / relative
            if live.is_symlink() or copy.exists() or copy.is_symlink() or not _is_sqlite(live):
                continue
            try:
                _make_parents(live_root, copy_root, relative)
            except Skip:
                continue
            taken += _take(live_root, live, copy, owner=owner)
    print(f"online-backup copies: {taken} SQLite file(s) under {copy_root}")
    return 0


if __name__ == "__main__":
    args = sys.argv[1:]
    as_owner = args[:1] == ["--as-owner"]
    args = args[1:] if as_owner else args
    if len(args) != 2:
        raise SystemExit(__doc__)
    sys.exit(main(Path(args[0]), Path(args[1]), owner=as_owner))
