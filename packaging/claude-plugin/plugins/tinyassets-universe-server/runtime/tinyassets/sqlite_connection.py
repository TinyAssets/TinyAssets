"""A sqlite3 connection whose ``with`` block is its whole life.

``sqlite3.Connection.__exit__`` commits or rolls back and leaves the connection
OPEN. The storage helpers here return a fresh connection per call, and their
callers write ``with _connect(...) as conn:`` -- so every call left a handle
open until a garbage-collector finalizer closed it, at some later moment,
inside whatever was running then. That held file handles (and, on Windows, file
locks) on the 2 GB host, and it is what made an unrelated test's fd count move
(#4204). Python 3.11 does not warn about it; 3.13+ reports each one as a
ResourceWarning, and a shard-1 probe found thousands.

Pass ``factory=ClosingConnection`` to ``sqlite3.connect`` in a helper whose
connections are per call. ``with`` then commits on success, rolls back on error,
and always closes. A caller that keeps using the connection after its ``with``
block gets ``ProgrammingError: Cannot operate on a closed database`` -- loudly,
not a silent leak. Never use it for a long-lived connection that runs several
``with conn:`` transactions.
"""
from __future__ import annotations

import sqlite3


class ClosingConnection(sqlite3.Connection):
    """``sqlite3.Connection`` that closes when its ``with`` block exits."""

    def __exit__(self, exc_type, exc, tb):
        try:
            return super().__exit__(exc_type, exc, tb)
        finally:
            self.close()
