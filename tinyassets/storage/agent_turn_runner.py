"""Task-lifetime OS claims. Tokens are unique and never reclaimed for execution."""
from __future__ import annotations

import os
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

from tinyassets import process_liveness

_HELD = {}
_LOCK = threading.Lock()


def _root(base):
    return Path(base) / ".agent-turn-runners"


@contextmanager
def claim(base, turn_id):
    token = uuid.uuid4().hex
    held = process_liveness.hold_liveness(_root(base), token)
    key = (str(Path(base).resolve()), turn_id)
    with _LOCK:
        _HELD[key] = held
    try:
        yield token
    except BaseException:
        release(base, turn_id)
        raise
    # Ownership passes to the coordinator after the row commits.


def release(base, turn_id):
    with _LOCK:
        held = _HELD.pop((str(Path(base).resolve()), turn_id), None)
    if held is not None:
        # Keep the file: an unlocked file is durable proof of runner death.
        os.close(held.fd)


def alive(base, token):
    return (bool(token) and
            process_liveness.owner_state(_root(base), token) == process_liveness.ALIVE)


def orphan(base, token):
    return not token or process_liveness.owner_state(_root(base), token) == process_liveness.DEAD


def _after_fork():
    global _LOCK
    for held in _HELD.values():
        os.close(held.fd)
    _HELD.clear()
    _LOCK = threading.Lock()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)
