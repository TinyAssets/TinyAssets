"""Read SQLite stores on a read-only volume, including quiescent WAL stores."""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from contextlib import ExitStack, contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator

_Signature = tuple[int, int, int, int, int]
_Fingerprint = tuple[dict[Path, _Signature], dict[Path, bytes]]
_SNAPSHOT: ContextVar[dict[Path, _Fingerprint] | None] = ContextVar(
    "tinyassets_sqlite_readonly_snapshot", default=None,
)


@contextmanager
def readonly_snapshot() -> Iterator[None]:
    """Reject changed sources across reads without retaining private DB copies."""
    token = _SNAPSHOT.set({})
    try:
        yield
    finally:
        _SNAPSHOT.reset(token)


def _digest(path: Path) -> bytes:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.digest()


def _signature(path: Path) -> _Signature:
    info = path.stat()
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def readonly_db(path: Path) -> Iterator[sqlite3.Connection]:
    """Read a verified private copy, including when a live WAL index exists.

    SQLite's mode=ro may still update read marks in an existing -shm file.
    Root can do that even when its mode is read-only. Never open the source
    through SQLite: copy the database and WAL and verify their bytes before
    opening the private copy. A concurrent writer makes the copy refuse.
    """
    path = Path(path).resolve()
    shm = path.with_name(path.name + "-shm")
    wal = path.with_name(path.name + "-wal")
    journal = path.with_name(path.name + "-journal")
    if journal.exists() or journal.is_symlink():
        raise RuntimeError(f"SQLite rollback journal present: {journal}")
    with ExitStack() as local_stack:
        snapshot = _SNAPSHOT.get()
        had_shm = shm.exists()
        scratch = Path(local_stack.enter_context(
            tempfile.TemporaryDirectory(prefix="tinyassets-sqlite-read-")))
        database = scratch / path.name
        source = [path]
        if wal.is_file():
            source.append(wal)
        signatures = {item: _signature(item) for item in source}
        if snapshot is not None and path in snapshot:
            prior_signatures, prior_digests = snapshot[path]
            if (signatures != prior_signatures or
                    any(_digest(item) != prior_digests[item] for item in source)):
                raise RuntimeError(f"SQLite store changed during read-only snapshot: {path}")
        digests = {}
        for item in source:
            digest = hashlib.sha256()
            with item.open("rb") as original, (scratch / item.name).open("wb") as target:
                for block in iter(lambda: original.read(1024 * 1024), b""):
                    target.write(block)
                    digest.update(block)
            digests[item] = digest.digest()
        if (shm.exists() != had_shm or journal.exists() or journal.is_symlink()
                or wal.is_file() != (wal in source) or any(
            _signature(item) != signatures[item] or _digest(item) != digests[item]
            for item in source
        )):
            raise RuntimeError(f"SQLite store changed during read-only snapshot: {path}")
        if snapshot is not None:
            snapshot[path] = (signatures, digests)
        conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=30.0)
        try:
            yield conn
        finally:
            conn.close()
            if journal.exists() or journal.is_symlink():
                raise RuntimeError(f"SQLite rollback journal present: {journal}")
