"""Cost-driver receipts (W10, row N12): measured, never enforced.

Phase 1 records what each owner's box, relay traffic and storage cost the
platform, one receipt per owner per day. Nothing here limits anything and no
admission module imports it (``check_shapes.py`` N12), so S26 stays exactly
two account limits.

Each trusted process keeps its own store in its private state directory
(boxhostd: ``/var/lib/boxhostd/state``; the broker: beside its owner
identities), never on an owner volume or a box mount. A write failure never
stops a box from starting or stopping: it is logged as an error and counted in
``failures``, which every receipt read reports. A read failure raises.

Receipts are CloudEvents, so an export to Stripe, Lago or OpenMeter is a field
mapping: ``id`` is the idempotency key, ``subject`` the owner, ``type`` the
meter and ``data.quantity`` the value in ``data.unit``.
"""
from __future__ import annotations

import datetime as dt
import functools
import hashlib
import json
import logging
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

log = logging.getLogger(__name__)

#: driver -> (unit, base units per unit). Stored quantities are seconds or bytes.
DRIVERS = {
    "box_seconds": ("box-seconds", 1),
    "relay": ("GB", 10**9),
    "storage": ("GB-days", 10**9),
    # Thin-loop wait. Its producer is the loop-worker pool (ADR-023, slice S2a);
    # nothing inside a box records it.
    "loop_seconds": ("loop-seconds", 1),
}
#: ADR-026: every wake reserves the thin class. A wake cannot know a CLI or
#: Chromium will start, so no other class has a source yet.
RESERVED_CLASS = "thin"
_DERIVED = ("box_seconds", "storage")  # rebuilt from intervals and samples by rollup
_SEEN_EVERY = 60

_SCHEMA = """
CREATE TABLE IF NOT EXISTS meter_intervals (
    id INTEGER PRIMARY KEY, owner TEXT NOT NULL, uid INTEGER NOT NULL,
    size_class TEXT NOT NULL, started_at REAL NOT NULL, seen_at REAL NOT NULL,
    ended_at REAL, end_reason TEXT, peak_memory_bytes INTEGER, cpu_usec INTEGER);
CREATE UNIQUE INDEX IF NOT EXISTS meter_one_open ON meter_intervals (uid)
    WHERE ended_at IS NULL;
CREATE TABLE IF NOT EXISTS meter_storage (
    day TEXT NOT NULL, uid INTEGER NOT NULL, owner TEXT,
    allocated_bytes INTEGER NOT NULL, provisioned_bytes INTEGER NOT NULL,
    PRIMARY KEY (day, uid));
CREATE TABLE IF NOT EXISTS meter_daily (
    day TEXT NOT NULL, owner TEXT NOT NULL, center TEXT NOT NULL DEFAULT '',
    driver TEXT NOT NULL, size_class TEXT NOT NULL DEFAULT '',
    quantity REAL NOT NULL, detail TEXT NOT NULL DEFAULT '{}',
    PRIMARY KEY (day, owner, center, driver, size_class));
CREATE TABLE IF NOT EXISTS meter_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def day_of(at: float) -> str:
    return dt.datetime.fromtimestamp(at, dt.UTC).date().isoformat()


def _bounds(day: str) -> tuple[float, float]:
    start = dt.datetime.fromisoformat(day).replace(tzinfo=dt.UTC).timestamp()
    return start, start + 86400


def _recorded(method):
    """A metering write logs and counts its failure; it never raises into lifecycle."""
    @functools.wraps(method)
    def write(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except Exception:
            with self._lock:
                self.failures += 1
            log.exception("metering %s failed; receipts are incomplete (%d failures)",
                          method.__name__, self.failures)
            try:  # the day's receipts then say they are incomplete
                with closing(self._connect()) as db, db:
                    db.execute("INSERT INTO meter_meta VALUES (?, '1') ON CONFLICT (key) "
                               "DO UPDATE SET value=value+1", (f"failures:{day_of(time.time())}",))
            except Exception:
                log.exception("metering could not record its own failure")
            return None
    return write


class Meter:
    def __init__(self, path: Path, *, source: str) -> None:
        self.path, self.source = Path(path), source
        self.failures = 0
        self._lock = threading.Lock()
        self._ready = False
        self._seen: dict[int, float] = {}
        self._daily = ""

    def _connect(self):
        db = sqlite3.connect(self.path, timeout=5)
        if not self._ready:
            db.executescript(_SCHEMA)
            self._ready = True
        return db

    @_recorded
    def measure(self, read, *args):
        """A runtime fact (cgroup usage) whose read failure must not block a stop."""
        return read(*args)

    @_recorded
    def open_interval(self, owner: str, uid: int, at: float, size_class=RESERVED_CLASS):
        with closing(self._connect()) as db, db:
            began = db.execute("SELECT MIN(started_at) FROM meter_intervals "
                               "WHERE uid=? AND ended_at IS NULL", (uid,)).fetchone()[0]
            db.execute("UPDATE meter_intervals SET ended_at=seen_at, end_reason='superseded' "
                       "WHERE uid=? AND ended_at IS NULL", (uid,))
            db.execute("INSERT INTO meter_intervals (owner, uid, size_class, started_at, "
                       "seen_at) VALUES (?,?,?,?,?)", (owner, uid, size_class, at, at))
        self._seen[uid] = at
        if began is not None:  # A superseded interval's earlier days drop its open tail.
            self._reroll(began, at)

    @_recorded
    def seen(self, uid: int, at: float):
        """The last time the box was observed running: a crash's best known end."""
        if at - self._seen.get(uid, 0) < _SEEN_EVERY:
            return
        with closing(self._connect()) as db, db:
            db.execute("UPDATE meter_intervals SET seen_at=? WHERE uid=? AND ended_at IS NULL",
                       (at, uid))
        self._seen[uid] = at

    @_recorded
    def close_interval(self, uid: int, reason: str, at: float, usage=None):
        usage = usage or {}
        with closing(self._connect()) as db, db:
            began = db.execute("SELECT MIN(started_at) FROM meter_intervals "
                               "WHERE uid=? AND ended_at IS NULL", (uid,)).fetchone()[0]
            db.execute("UPDATE meter_intervals SET ended_at=MAX(?, started_at), end_reason=?, "
                       "peak_memory_bytes=?, cpu_usec=? WHERE uid=? AND ended_at IS NULL",
                       (at, reason, usage.get("peak_memory_bytes"), usage.get("cpu_usec"), uid))
        self._seen.pop(uid, None)
        self._reroll(began, at)

    @_recorded
    def recover(self, at: float, running: dict[int, dict]):
        """After a host restart: a box still running ends now, any other at its last sighting."""
        with closing(self._connect()) as db, db:
            began = db.execute("SELECT MIN(started_at) FROM meter_intervals "
                               "WHERE ended_at IS NULL").fetchone()[0]
            for uid, usage in running.items():
                db.execute("UPDATE meter_intervals SET ended_at=MAX(?, started_at), "
                           "end_reason='recovered', peak_memory_bytes=?, cpu_usec=? "
                           "WHERE uid=? AND ended_at IS NULL",
                           (at, usage.get("peak_memory_bytes"), usage.get("cpu_usec"), uid))
            db.execute("UPDATE meter_intervals SET ended_at=seen_at, end_reason='lost' "
                       "WHERE ended_at IS NULL")
        self._reroll(began, at)

    def _reroll(self, began, at: float):
        """CPU is known only at close: each earlier day of the interval takes its share again."""
        day = dt.date.fromisoformat(day_of(at if began is None else began))
        while day.isoformat() < day_of(at):
            self._rollup(day.isoformat(), at)
            day += dt.timedelta(days=1)

    @_recorded
    def add(self, owner: str, driver: str, quantity: float, at: float, center: str = ""):
        if driver not in DRIVERS or driver in _DERIVED:
            raise ValueError(f"{driver!r} is not a counted driver")
        with closing(self._connect()) as db, db:
            db.execute("INSERT INTO meter_daily (day, owner, center, driver, quantity) "
                       "VALUES (?,?,?,?,?) ON CONFLICT (day, owner, center, driver, size_class) "
                       "DO UPDATE SET quantity=quantity+excluded.quantity",
                       (day_of(at), owner, center, driver, quantity))

    @_recorded
    def daily(self, at: float, storage):
        """The daily job: one storage sample per owner image, then roll up finished days."""
        today = day_of(at)
        if self._daily == today:
            return
        self._daily = today  # a failed pass is retried by tomorrow's, from rolled_through
        with closing(self._connect()) as db, db:
            for uid, (owner, allocated, provisioned) in storage().items():
                db.execute("INSERT OR REPLACE INTO meter_storage VALUES (?,?,?,?,?)",
                           (today, uid, owner, allocated, provisioned))
            row = db.execute("SELECT value FROM meter_meta WHERE key='rolled_through'").fetchone()
        first = dt.date.fromisoformat(today) - dt.timedelta(days=1)
        if row is not None:
            first = max(dt.date.fromisoformat(row[0]) + dt.timedelta(days=1),
                        first - dt.timedelta(days=31))
        day = first
        while day.isoformat() < today:
            self._rollup(day.isoformat(), at)
            day += dt.timedelta(days=1)
        with closing(self._connect()) as db, db:
            db.execute("INSERT OR REPLACE INTO meter_meta VALUES ('rolled_through', ?)",
                       ((dt.date.fromisoformat(today) - dt.timedelta(days=1)).isoformat(),))

    def _rollup(self, day: str, now: float):
        start, end = _bounds(day)
        boxes: dict[tuple[str, str], dict] = {}
        with closing(self._connect()) as db, db:
            db.execute("BEGIN IMMEDIATE")  # a close commits before or after, never in between
            for owner, size_class, began, ended, peak, cpu in db.execute(
                    "SELECT owner, size_class, started_at, COALESCE(ended_at, ?), "
                    "peak_memory_bytes, cpu_usec FROM meter_intervals "
                    "WHERE started_at < ? AND COALESCE(ended_at, ?) > ?",
                    (now, end, now, start)):
                overlap = max(0.0, min(ended, end) - max(began, start))
                row = boxes.setdefault((owner, size_class), {
                    "seconds": 0.0, "intervals": 0, "peak_memory_bytes": None, "cpu_seconds": 0.0})
                row["seconds"] += overlap
                row["intervals"] += 1
                if peak is not None:
                    row["peak_memory_bytes"] = max(peak, row["peak_memory_bytes"] or 0)
                if cpu is not None and ended > began:
                    row["cpu_seconds"] += cpu / 1e6 * overlap / (ended - began)
            db.execute("DELETE FROM meter_daily WHERE day=? AND driver IN (?, ?)",
                       (day, *_DERIVED))
            for (owner, size_class), row in boxes.items():
                seconds = row.pop("seconds")
                db.execute("INSERT INTO meter_daily (day, owner, driver, size_class, quantity, "
                           "detail) VALUES (?,?,?,?,?,?)", (day, owner, "box_seconds",
                           size_class, seconds, json.dumps(row, sort_keys=True)))
            for uid, owner, allocated, provisioned in db.execute(
                    "SELECT s.uid, COALESCE(s.owner, (SELECT owner FROM meter_intervals i "
                    "WHERE i.uid=s.uid ORDER BY started_at DESC LIMIT 1)), "
                    "s.allocated_bytes, s.provisioned_bytes FROM meter_storage s WHERE day=?",
                    (day,)).fetchall():
                db.execute("INSERT OR REPLACE INTO meter_daily (day, owner, driver, quantity, "
                           "detail) VALUES (?,?,?,?,?)", (day, owner or f"box:{uid}", "storage",
                           allocated, json.dumps({"provisioned_bytes": provisioned})))

    def receipts(self, since: str, until: str) -> dict:
        """Finished UTC days in [since, until] as CloudEvents. Opens the store read-only.

        Today is refused: its rows still move, under the final rows' ``id``.
        """
        dt.date.fromisoformat(since)
        if dt.date.fromisoformat(until).isoformat() >= day_of(time.time()):
            raise ValueError(f"{until} is not a finished UTC day")
        if not self.path.is_file():
            raise FileNotFoundError(f"no {self.source} receipt store at {self.path}")
        with closing(sqlite3.connect(self.path.as_uri() + "?mode=ro", uri=True)) as db:
            rows = db.execute("SELECT day, owner, center, driver, size_class, quantity, detail "
                              "FROM meter_daily WHERE day BETWEEN ? AND ? "
                              "ORDER BY day, owner, driver, size_class", (since, until)).fetchall()
            failures = dict(db.execute(
                "SELECT substr(key, 10), CAST(value AS INTEGER) FROM meter_meta "
                "WHERE key BETWEEN ? AND ?", (f"failures:{since}", f"failures:{until}")))
        return {"source": self.source, "write_failures": failures,
                "events": [self._event(*row) for row in rows]}

    def _event(self, day, owner, center, driver, size_class, quantity, detail):
        unit, scale = DRIVERS[driver]
        key = "|".join((self.source, day, owner, center, driver, size_class))
        data = {"day": day, "owner": owner, "center": center or None, "driver": driver,
                "unit": unit, "quantity": quantity / scale, **json.loads(detail)}
        if size_class:
            data["size_class"] = size_class
        return {"specversion": "1.0", "id": hashlib.sha256(key.encode()).hexdigest()[:32],
                "source": f"tinyassets/{self.source}", "type": f"io.tinyassets.meter.{driver}",
                "subject": owner, "time": f"{day}T00:00:00Z",
                "datacontenttype": "application/json", "data": data}
