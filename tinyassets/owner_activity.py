"""Process-local admission gate shared by HTTP and every agent turn."""
from __future__ import annotations

import contextvars
import threading
import time
from contextlib import contextmanager
from functools import wraps

# Per admitted turn, not per OS thread. The owner server runs every request
# as its own task on one event-loop thread; a thread-local depth would treat
# the next request as a continuation of the one already inside `enter`.
_ADMISSION_DEPTH: contextvars.ContextVar[dict[int, int] | None] = contextvars.ContextVar(
    "owner_admission_depth", default=None,
)


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
        self.snapshot_stop = False
        self.waiters = []

    @contextmanager
    def pending_login(self, cancelled):
        """Hold the idle clock while sign-in waits; a drain still cancels it.

        A box sleeps as soon as no work runs (ADR-026), so a pending sign-in
        reads as active until it ends; the login's own timeout bounds it.
        """
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
                self.last = time.monotonic()  # The lease restarts when sign-in ends.

    def _cancel_logins(self):
        for cancelled in self.pending_logins:
            cancelled.set()

    def admit_deletion_only(self):
        with self.lock:
            self.deletion_only = self.deleting = self.draining = True
            self._cancel_logins()

    def _depth(self) -> int:
        depths = _ADMISSION_DEPTH.get()
        if not depths:
            return 0
        return depths.get(id(self), 0)

    @contextmanager
    def enter(self, *, keep_awake=True):
        with self.lock:
            # Nested work of a turn already admitted in this task may finish.
            # A different task, including another request on the same thread, is the next turn.
            if self.draining and not self._depth():
                raise RuntimeError("owner box is draining")
            self.active += 1
            depths = dict(_ADMISSION_DEPTH.get() or {})
            depths[id(self)] = self._depth() + 1
            token = _ADMISSION_DEPTH.set(depths)
            if keep_awake:
                self.last = time.monotonic()
        try:
            yield
        finally:
            with self.lock:
                self.active -= 1
                _ADMISSION_DEPTH.reset(token)
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
            if self.pending_logins and not self.draining:
                self.last = time.monotonic()
            age = time.monotonic() - self.last
            if revoke:
                self.revoked = self.draining = True
            elif deleting:
                self.deleting = self.draining = True
            elif snapshot:
                self.draining = True  # Finish admitted turns; refuse the next turn.
                self.snapshot_stop = True
                self._interrupt_waiters()
            elif drain and self.active == 0 and age >= idle:
                self.draining = True
            if self.draining or (drain and age >= idle):
                self._cancel_logins()
            return {"active": self.active, "idle_seconds": age,
                    "draining": self.draining, "revoked": self.revoked,
                    "deleting": self.deleting}

    def waiting(self, operation_id, generation):
        """A call parked on a turn. Snapshot drain settles it before shutdown."""
        slot = {"operation_id": operation_id, "volume_generation": generation, "result": None}
        with self.lock:
            self.waiters.append(slot)
            if self.snapshot_stop:
                slot["result"] = _interrupted(slot)
        return slot

    def _interrupt_waiters(self):
        for slot in self.waiters:
            if slot["result"] is None:
                slot["result"] = _interrupted(slot)

    def deletion_refused(self):
        """Shared deletion is already ledger-fenced before local cleanup runs."""
        self.admit_deletion_only()


def _interrupted(slot):
    return {"error": "interrupted_by_restore", "operation_id": slot["operation_id"],
            "volume_generation": slot["volume_generation"]}


activity = Activity()


def owner_work(function):
    """Count background work for drain without renewing the user-idle clock."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        with activity.enter(keep_awake=False):
            return function(*args, **kwargs)
    return wrapped
