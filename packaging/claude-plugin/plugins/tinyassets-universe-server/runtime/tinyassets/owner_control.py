"""Cross-worker owner-control serialization, outside agent-writable storage.

The OS lock is never stolen on a timer. A dead worker loses its descriptor;
another worker can then recover under the same lock. Nested synchronous calls
reuse the lock on their thread, never across async tasks.
"""

from __future__ import annotations

import functools
import os
import threading
from contextlib import contextmanager
from pathlib import Path

from tinyassets import agent_sessions
from tinyassets.singleton_lock import _lock_fd, _unlock_fd
from tinyassets.universe_files import open_lock_file

_held = threading.local()


class ControlUnavailable(RuntimeError):
    """Retryable refusal; the operation has not been queued or applied."""

    kind = "owner_control_unavailable"


@contextmanager
def control(universe_dir: Path):
    root = Path(universe_dir).resolve()
    key = (os.getpid(), str(root))
    held = getattr(_held, "locks", {})
    if key in held:
        os.fstat(held[key])  # A closed descriptor cannot authorize a nested write.
        yield
        return
    relpath = f"{agent_sessions.RECORDS_DIR}/{root.name}/owner-control.lock"
    fd = open_lock_file(root.parent, relpath, mode=0o600)
    if not _lock_fd(fd):
        os.close(fd)
        raise ControlUnavailable("Owner controls are busy; retry the operation.")
    _held.locks = {**held, key: fd}
    try:
        yield
        os.fstat(fd)
    finally:
        _held.locks = held
        _unlock_fd(fd)
        os.close(fd)


def serialized(fn):
    @functools.wraps(fn)
    def wrapped(universe_dir, *args, **kwargs):
        with control(universe_dir):
            return fn(universe_dir, *args, **kwargs)

    return wrapped
