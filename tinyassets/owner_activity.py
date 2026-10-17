"""Process-local admission gate shared by HTTP and every agent turn."""
from __future__ import annotations

import threading
import time
from contextlib import contextmanager
from functools import wraps


class Activity:
    def __init__(self):
        self.lock = threading.Condition(threading.RLock())
        self.active = 0
        self.last = time.monotonic()
        self.draining = False
        self.revoked = False
        self.deleting = False

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
                self.lock.notify_all()

    def quiesce(self, timeout):
        with self.lock:
            self.deleting = self.draining = True
            return self.lock.wait_for(lambda: self.active == 0, timeout=timeout)

    def status(self, *, drain=False, idle=0.0, revoke=False, deleting=False):
        with self.lock:
            age = time.monotonic() - self.last
            if revoke:
                self.revoked = self.draining = True
            elif deleting:
                self.deleting = self.draining = True
            elif drain and self.active == 0 and age >= idle:
                self.draining = True
            return {"active": self.active, "idle_seconds": age,
                    "draining": self.draining, "revoked": self.revoked,
                    "deleting": self.deleting}

    def deletion_refused(self):
        """Only a pre-mutation refusal may reopen a deletion drain."""
        with self.lock:
            if not self.revoked:
                self.deleting = self.draining = False


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
