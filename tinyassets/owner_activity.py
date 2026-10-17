"""Process-local admission gate shared by HTTP and every agent turn."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from functools import wraps


class Activity:
    def __init__(self):
        self.lock = threading.RLock()
        self.active = 0
        self.last = time.monotonic()
        self.draining = False
        self.revoked = False

    @contextmanager
    def enter(self):
        with self.lock:
            if self.draining:
                raise RuntimeError("owner box is draining")
            self.active += 1
            self.last = time.monotonic()
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1
                self.last = time.monotonic()

    def status(self, *, drain=False, idle=0.0, revoke=False):
        with self.lock:
            age = time.monotonic() - self.last
            if revoke:
                self.revoked = self.draining = True
            elif drain and self.active == 0 and age >= idle:
                self.draining = True
            return {"active": self.active, "idle_seconds": age,
                    "draining": self.draining, "revoked": self.revoked}


activity = Activity()


def owner_work(function):
    """Hold the gate for scheduled work as well as disconnected HTTP turns."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        from tinyassets.owner_runtime import current

        if current() is None:
            return function(*args, **kwargs)
        with activity.enter():
            return function(*args, **kwargs)
    return wrapped
