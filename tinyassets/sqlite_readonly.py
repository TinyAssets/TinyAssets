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
_Copy = tuple[Path, dict[Path, _Signature]]
_SNAPSHOT: ContextVar[tuple[ExitStack, dict[Path, _Copy]] | None] = ContextVar(
    "tinyassets_sqlite_readonly_snapshot", default=None,
)


@contextmanager
def readonly_snapshot() -> Iterator[None]:
    """Reuse stable private copies across one preflight's repeated DB reads."""
    with ExitStack() as stack:
        token = _SNAPSHOT.set((stack, {}))
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
    """Read a live WAL directly or a verified private copy when -shm is absent.

    SQLite can read a live read-only WAL when -shm exists. Without it, copy the
    database and WAL to a writable temporary directory, then verify that the
    source files did not change during the copy. A writer arriving during this
    window creates -shm or changes a source digest, so the read fails closed.
    """
    path = Path(path).resolve()
    shm = path.with_name(path.name + "-shm")
    wal = path.with_name(path.name + "-wal")
    with ExitStack() as local_stack:
        snapshot = _SNAPSHOT.get()
        stack, copies = snapshot if snapshot is not None else (local_stack, {})
        if path in copies:
            copied, signatures = copies[path]
            if shm.exists() or wal.is_file() != (wal in signatures) or any(
                _signature(item) != signature for item, signature in signatures.items()
            ):
                raise RuntimeError(f"SQLite store changed during read-only snapshot: {path}")
            database = copied
        elif shm.exists():
            database = path
        else:
            scratch = Path(stack.enter_context(
                tempfile.TemporaryDirectory(prefix="tinyassets-sqlite-read-")))
            copied = scratch / path.name
            source = [path]
            if wal.is_file():
                source.append(wal)
            signatures = {item: _signature(item) for item in source}
            digests = {}
            for item in source:
                digest = hashlib.sha256()
                with item.open("rb") as original, (scratch / item.name).open("wb") as target:
                    for block in iter(lambda: original.read(1024 * 1024), b""):
                        target.write(block)
                        digest.update(block)
                digests[item] = digest.digest()
            if shm.exists() or wal.is_file() != (wal in source) or any(
                _signature(item) != signatures[item] or _digest(item) != digests[item]
                for item in source
            ):
                raise RuntimeError(f"SQLite store changed during read-only snapshot: {path}")
            copies[path] = (copied, signatures)
            database = copied
        conn = sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True, timeout=30.0)
        try:
            yield conn
        finally:
            conn.close()
