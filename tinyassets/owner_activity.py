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
        self.deletion_only = False
        self.pending_logins = set()

    @contextmanager
    def pending_login(self, cancelled):
        """Admit sign-in without keeping the box awake or delaying a drain."""
        with self.lock:
            if self.draining:
                cancelled.set()
                raise RuntimeError("owner box is draining")
            self.pending_logins.add(cancelled)
        try:
            yield
        finally:
            with self.lock:
                self.pending_logins.discard(cancelled)

    def _cancel_logins(self):
        for cancelled in self.pending_logins:
            cancelled.set()

    def admit_deletion_only(self):
        with self.lock:
            self.deletion_only = self.deleting = self.draining = True
            self._cancel_logins()

    @contextmanager
    def enter(self, *, keep_awake=True):
        with self.lock:
            if self.draining:
                raise RuntimeError("owner box is draining")
            self.active += 1
            if keep_awake:
                self.last = time.monotonic()
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1
                if keep_awake:
                    self.last = time.monotonic()
                self.lock.notify_all()

    def quiesce(self, timeout):
        with self.lock:
            self.deleting = self.draining = True
            self._cancel_logins()
            return self.lock.wait_for(lambda: self.active == 0, timeout=timeout)

    def status(self, *, drain=False, idle=0.0, revoke=False, deleting=False, snapshot=False):
        with self.lock:
            age = time.monotonic() - self.last
            if revoke:
                self.revoked = self.draining = True
            elif deleting:
                self.deleting = self.draining = True
            elif snapshot:
                self.draining = True  # Finish admitted turns; refuse the next turn.
            elif drain and self.active == 0 and age >= idle:
                self.draining = True
            if self.draining or (drain and age >= idle):
                self._cancel_logins()
            return {"active": self.active, "idle_seconds": age,
                    "draining": self.draining, "revoked": self.revoked,
                    "deleting": self.deleting}

    def deletion_refused(self):
        """Shared deletion is already ledger-fenced before local cleanup runs."""
        self.admit_deletion_only()


activity = Activity()


def owner_work(function):
    """Count background work for drain without renewing the user-idle clock."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with activity.enter(keep_awake=False):
            return function(*args, **kwargs)
    return wrapped
