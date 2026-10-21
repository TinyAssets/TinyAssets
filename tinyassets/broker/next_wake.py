"""Broker-owned wake metadata. Schedule definitions never leave an owner box."""
from __future__ import annotations

import math
import sqlite3
from contextlib import closing


class NextWakeIndex:
    def __init__(self, identities):
        self.identities = identities
        self.path = identities.path.parent / "next-wake.db"
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS next_wakes ("
                       "owner TEXT PRIMARY KEY, next_wake_at REAL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30, uri=True)

    def report(self, owner, at):
        if at is not None and (type(at) not in (int, float) or not math.isfinite(at)):
            raise ValueError("next wake must be finite epoch seconds or null")
        # Every report rechecks live admission, including on an established relay.
        from tinyassets.broker.admission import require_admitted

        require_admitted(self.identities, owner)
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO next_wakes (owner, next_wake_at) "
                       "VALUES (?, ?) ON CONFLICT(owner) DO UPDATE SET "
                       "next_wake_at=excluded.next_wake_at", (owner, at))

    def due(self, *, now):
        with closing(self._connect()) as db:
            db.execute("ATTACH DATABASE ? AS identities",
                       (self.identities.path.as_uri() + "?mode=ro",))
            candidates = [row[0] for row in db.execute(
                "SELECT i.principal FROM identities.owner_identities i "
                "LEFT JOIN next_wakes w ON w.owner=i.principal "
                "WHERE w.next_wake_at<=? OR w.owner IS NULL "
                "ORDER BY i.principal", (now,))]
            from tinyassets.broker.admission import admission

            return [owner for owner in candidates if admission(db, owner).state == "admitted"]
