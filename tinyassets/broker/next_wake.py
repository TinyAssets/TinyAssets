"""Broker-owned wake metadata. Schedule definitions never leave an owner box."""
from __future__ import annotations

import math
import sqlite3
from contextlib import closing

FLOOR_SECONDS = 24 * 60 * 60


class NextWakeIndex:
    def __init__(self, identities):
        self.identities = identities
        self.path = identities.path.parent / "next-wake.db"
        with closing(self._connect()) as db, db:
            db.execute("CREATE TABLE IF NOT EXISTS next_wakes ("
                       "owner TEXT PRIMARY KEY, next_wake_at REAL, reported_at REAL, "
                       "last_woken_at REAL)")

    def _connect(self):
        return sqlite3.connect(self.path, timeout=30, uri=True)

    def report(self, owner, at, *, now):
        if at is not None and (type(at) not in (int, float) or not math.isfinite(at)):
            raise ValueError("next wake must be finite epoch seconds or null")
        # Every report rechecks live admission, including on an established relay.
        self.identities.box_route(owner)
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO next_wakes (owner, next_wake_at, reported_at) "
                       "VALUES (?, ?, ?) ON CONFLICT(owner) DO UPDATE SET "
                       "next_wake_at=excluded.next_wake_at, reported_at=excluded.reported_at",
                       (owner, at, now))

    def woken(self, owner, *, now):
        # An acknowledgement never consumes or overwrites a concurrent report.
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO next_wakes (owner, last_woken_at) VALUES (?, ?) "
                       "ON CONFLICT(owner) DO UPDATE SET last_woken_at=excluded.last_woken_at",
                       (owner, now))

    def due(self, *, now):
        with closing(self._connect()) as db:
            db.execute("ATTACH DATABASE ? AS identities",
                       (self.identities.path.as_uri() + "?mode=ro",))
            return [row[0] for row in db.execute(
                "SELECT i.principal FROM identities.owner_identities i "
                "LEFT JOIN next_wakes w ON w.owner=i.principal "
                "WHERE EXISTS (SELECT 1 FROM identities.center_admissions a "
                "WHERE a.principal=i.principal AND a.event='admit' AND NOT EXISTS ("
                "SELECT 1 FROM identities.center_admissions r "
                "WHERE r.center=a.center AND r.event='retire')) "
                "AND NOT EXISTS (SELECT 1 FROM identities.owner_box_revocations v "
                "WHERE v.principal=i.principal AND v.deleted=1) "
                "AND (w.next_wake_at<=? OR w.last_woken_at IS NULL OR w.last_woken_at<=?) "
                "ORDER BY w.last_woken_at, i.principal", (now, now - FLOOR_SECONDS))]
