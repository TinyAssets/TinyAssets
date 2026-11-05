"""Catch a sqlite connection a call opens and never closes.

A ``sqlite3.Connection`` is in a reference cycle with its statement cache, so one
left open is freed only by the cyclic collector, at an allocation nobody chose.
With collection off for the call, a leaked connection is still on the heap and
still open afterwards, so the check is deterministic.
"""
from __future__ import annotations

import gc
import sqlite3
import tracemalloc
from contextlib import contextmanager


def _closed(item):
    try:
        item.total_changes
    except sqlite3.ProgrammingError as exc:
        # A thread check raises this type too, for a connection that is open.
        return "closed database" in str(exc)
    return False


def _open():
    return [item for item in gc.get_objects()
            if isinstance(item, sqlite3.Connection) and not _closed(item)]


def _close(item):
    try:
        item.close()
    except sqlite3.ProgrammingError:
        pass  # Opened in another thread, which alone may close it; still reported.


def _opened_at(item):
    trace = tracemalloc.get_object_traceback(item)
    if trace is None:
        return "?"
    # The opener and its caller: a helper such as _open_ro names nothing alone.
    frames = [f for f in reversed(trace) if "/tinyassets/" in f.filename][:2] or [trace[-1]]
    return " <- ".join(f"{f.filename.rsplit('/', 1)[-1]}:{f.lineno}" for f in frames)


@contextmanager
def no_connection_left_open():
    """Fail when the body leaves a sqlite connection open."""
    enabled = gc.isenabled()
    gc.disable()
    before = _open()
    try:
        yield
        left = [item for item in _open() if not any(item is old for old in before)]
        # Run with PYTHONTRACEMALLOC=N to name the line that opened each one.
        opened = [_opened_at(item) for item in left]
        for item in left:
            _close(item)
        del left
        assert not opened, f"{len(opened)} sqlite connection(s) left open: {opened}"
    finally:
        del before
        if enabled:
            gc.enable()
