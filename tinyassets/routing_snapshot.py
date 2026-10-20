"""C5/T1: the router's atomic, process-local projection of broker admission."""
from __future__ import annotations

import hashlib
import secrets
import threading
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Route:
    state: str
    socket: Path | None
    secret: str | None


class RoutingSnapshot:
    def __init__(self):
        self.instance = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._installed = None

    def install(self, instance, revision, records):
        # C5: an old process's delivery cannot initialize a restarted router.
        if instance != self.instance or type(revision) is not int or revision < 1:
            raise ValueError("routing snapshot incarnation refused")
        routes = {}
        for owner, row in records.items():
            if (not isinstance(owner, str) or not owner
                    or set(row) != {"state", "socket", "secret"}
                    or row["state"] not in {"admitted", "suspended"}):
                raise ValueError("invalid routing snapshot record")
            if row["state"] == "admitted":
                if (not isinstance(row["socket"], str) or not row["socket"]
                        or not isinstance(row["secret"], str) or len(row["secret"]) != 43):
                    raise ValueError("admitted routing snapshot lacks its credential")
            routes[owner] = Route(row["state"], Path(row["socket"]) if row["socket"] else None,
                                  row["secret"])
        with self._lock:
            if self._installed is None or revision > self._installed[0]:
                self._installed = revision, routes
            return self._installed[0]

    def record(self, owner):
        with self._lock:
            self.require_current()
            return self._installed[1].get(hashlib.sha256(owner.encode()).hexdigest())

    def require_current(self):
        if self._installed is None:
            raise RuntimeError("router needs a current routing snapshot")

    def route(self, owner):
        record = self.record(owner)
        if record is not None and record.state == "suspended":
            raise PermissionError("owner account is suspended")
        if record is None or record.state != "admitted":
            raise LookupError("owner has no admitted route")
        return record


_snapshots = {}
_lock = threading.Lock()


def snapshot(root):
    with _lock:
        return _snapshots.setdefault(Path(root), RoutingSnapshot())
