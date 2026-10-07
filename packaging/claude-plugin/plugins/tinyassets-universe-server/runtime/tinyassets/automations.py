"""User-owned automations: the row, the owner's controls, and the due-run path.

One universe, one owner, one recurring Branch run. This module is the whole
storage + logic half of ``openspec/changes/user-owned-automations`` task 3.1/3.2;
the MCP surface and the consumer pump call into it and add nothing of their own.

What is deliberately NOT here (design D1, PLAN.md 2026-08-29 "nothing runs
outside a user's universe"):

* **No provider.** A row records what to run and when, never which provider or
  credential runs it. Every due run resolves the universe's CURRENT
  ``provider_assignments`` row through the same foreground session a live turn
  uses, so rebinding the universe to another provider needs no re-preparation.
* **No executor identity.** No ``worker_id``, ``daemon_id`` or
  ``runtime_instance_id`` is stored or compared. The fleet-era activation layer
  wedged on exactly those: a boot-unique consumer id could never match the id
  recorded when the automation was prepared.
* **No host actor.** The run actor is ``universe:<id>`` and the provider
  principal is the automation's owner, checked at registration AND at each run
  (D3). An owner who lost admin gets a recorded refusal and an auto-paused
  automation, never a run.

Fences and failure:

* ``(automation_id, due_at)`` is the run fence (D2) -- a ``BEGIN IMMEDIATE``
  count-and-insert, the same TOCTOU-safe shape as ``universe_seats.acquire``. A
  restart recomputes the same ``due_at`` and finds the row, so a due run
  launches exactly once across restarts.
* Registration fails loud (D4): a row that cannot fire right now is refused with
  a named reason rather than stored. Hard Rule 8.
* Every skip lands in ``assigned_queue_refusals`` keyed ``automation:<id>``
  (D5), and one automation's failure never propagates into the pump.

Due-time note (deviation from the build brief, deliberate): the interval trigger
floors ``now`` onto the automation's own period grid rather than always emitting
``anchor + one interval``. Both are deterministic across a restart -- the fence
requirement -- but the naive form replays every interval missed while the daemon
was down, one per poll, which spends the owner's subscription on a backlog burst
(the exact risk design.md lists). Flooring fires once for a missed window, which
is also what the cron branch does with its minute bucket.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import uuid
from dataclasses import dataclass
from datetime import date as _date
from datetime import datetime, timedelta, timezone
from datetime import time as _time
from pathlib import Path
from typing import Any

from tinyassets.principals import named_principal
from tinyassets.schedule_timezone import (
    DEFAULT_TIMEZONE,
    UnknownTimezone,
    local_now,
    normalize_timezone,
    resolve_zone,
    slot_instant,
    slot_key,
)

logger = logging.getLogger(__name__)

DB_FILENAME = ".automations.db"

#: The shortest cadence: one second. There is no policy floor and no count
#: ceiling (plan item 6, founder 2026-08-30 "limit USAGE, not shape"). What
#: bounds a tight or numerous cadence is the account's seats: a run waits for one
#: before it claims its attempt (``run_due_automation``). A cadence shorter than
#: the pump's poll simply fires once per poll.
MIN_INTERVAL_SECONDS = 1

TRIGGER_INTERVAL = "interval"
TRIGGER_CRON = "cron"
#: A one-shot run, due at ``not_before``. What a node's ``enqueue_branch_run``
#: stores: "wake this branch now, or not before then" -- any wake behaviour a
#: graph wants is built from this plus its own logic.
TRIGGER_ONCE = "once"

#: A subscription: never due on a clock. ``tinyassets.automation_events``
#: stores a ``once`` wake for it each time its event is emitted, so the fired
#: run takes the same pump, fence, admission and authority checks as any other.
TRIGGER_EVENT = "event"
#: An event subscription's ``last_reason`` after it fired: ``woke:<wake id>``.
EVENT_WOKE_PREFIX = "woke:"

#: The events the engine emits (``automation_events.EVENT_FILTER_KEYS``). A
#: subscription to anything else would be stored and never fire, so it is
#: refused at registration.
EVENT_RUN_COMPLETED = "run_completed"
EVENT_PENDING_REQUEST_ANSWERED = "pending_request_answered"
#: Emitted by the owner's own session -- a click in a UI they built, through
#: ``run_graph operation="emit_event"`` -- to wake the agent subscribed to that
#: NAME. The name is required in every subscription: a UI can wake only what
#: its owner (or their universe) subscribed to that name, never "any".
EVENT_APP = "app_event"
#: The owner sent their universe a message (``automation_events.
#: emit_owner_message``, once the message is stored in their conversation). A
#: burst coalesces into the one wake still waiting to start; the universe's own
#: replies never emit it.
EVENT_OWNER_MESSAGE = "owner_message"
EVENT_TYPES = frozenset({
    EVENT_RUN_COMPLETED, EVENT_PENDING_REQUEST_ANSWERED, EVENT_APP, EVENT_OWNER_MESSAGE,
})

#: Payload keys each event carries, which are also the keys a subscription may
#: filter on (equality). ``run_completed`` must name the branch it follows: an
#: unfiltered "any run finished" pair of subscriptions wakes each other forever
#: without either owner having asked for a loop. A loop stays expressible -- it
#: just has to be named.
EVENT_FILTER_KEYS: dict[str, frozenset[str]] = {
    EVENT_RUN_COMPLETED: frozenset({"branch_def_id", "outcome", "run_id"}),
    EVENT_PENDING_REQUEST_ANSWERED: frozenset(
        {"request_id", "kind", "status", "item_id"}
    ),
    EVENT_APP: frozenset({"name"}),
    EVENT_OWNER_MESSAGE: frozenset(),
}
EVENT_REQUIRED_FILTER_KEYS: dict[str, frozenset[str]] = {
    EVENT_RUN_COMPLETED: frozenset({"branch_def_id"}),
    EVENT_PENDING_REQUEST_ANSWERED: frozenset(),
    EVENT_APP: frozenset({"name"}),
    EVENT_OWNER_MESSAGE: frozenset(),
}

#: A ``once`` row whose attempt never reached a run is retried this much later
#: per attempt made, and retires after ``MAX_ONCE_ATTEMPTS``.
ONCE_RETRY_SECONDS = 60
MAX_ONCE_ATTEMPTS = 5

#: How far ahead a ``once`` row may be parked: as far as the author likes.
#: ``MAX_NOT_BEFORE = timedelta(days=366)`` refused a wake scheduled for two
#: years out with ``trigger_invalid``, which is a structural cap on what someone
#: may build, not a limit on what they use (founder, 2026-09-30). A parked row
#: costs one row of storage until it fires.
STATE_ACTIVE = "active"
STATE_PAUSED = "paused"

#: What a due run does when its agent -- the same branch in the same universe --
#: is already running. Runs of one agent never overlap; different agents in
#: one universe run side by side.
#:
#: * ``queue`` (default): wait, and start when the running one ends. Instants a
#:   cadence passes meanwhile collapse into that one owed run. This is the
#:   behaviour every automation had under the per-universe lease.
#: * ``skip``: drop this due run, and a cadence moves on to its next instant.
#:   A one-shot wake has no next instant, so it is never dropped: it waits, as
#:   under ``queue``, with ``waiting_for_previous_run`` on the owner's surface.
#:   Live 2026-09-28: a ``run_completed`` subscription's wake fell due while the
#:   run that fired it still held the agent, and retiring it ended the owner's
#:   self-built loop with nothing to tell them.
#: * ``cancel_previous``: ask the running one to cancel, then start once it
#:   has stopped.
OVERLAP_QUEUE = "queue"
OVERLAP_SKIP = "skip"
OVERLAP_CANCEL_PREVIOUS = "cancel_previous"
OVERLAP_POLICIES = frozenset({OVERLAP_QUEUE, OVERLAP_SKIP, OVERLAP_CANCEL_PREVIOUS})

#: The owner-visible reason a due run records while its agent is busy and it
#: waits for the lease: every ``queue`` row, and a ``skip`` one-shot wake.
WAITING_FOR_PREVIOUS_RUN = "waiting_for_previous_run"

#: An agent's lease key is ``agent:<len(universe)>:<universe>:<branch>``. The
#: length prefix makes it unambiguous whatever the ids contain, so no key and
#: no universe prefix can reach into another universe (Codex refute
#: 2026-09-28, P2: `(U, B::C)` and `(U::B, C)` collided under a bare `::`).
LEASE_KEY_PREFIX = "agent:"

#: Refusal-ledger key convention. Shared with the consumer and the owner's
#: surface, which read the reason back out of ``assigned_queue_refusals``.
REFUSAL_KEY_PREFIX = "automation:"

#: Consecutive failed attempts before the automation pauses itself. A cadence
#: that fails every period is an endless spend loop on the owner's subscription
#: (Codex ADAPT 2026-08-29 §6: a foreign-authored branch registered, then failed
#: forever while staying active). Reset by any successful run.
MAX_CONSECUTIVE_FAILURES = 3

#: There is NO ceiling on one automation run. ``DEFAULT_RUN_TIMEOUT_SECONDS =
#: 10800`` cancelled a run at three hours; its own comment already conceded that
#: a turn runs until it is finished, and justified itself as a bound on a run
#: "nothing will ever finish" -- which is a LIVENESS question a clock cannot
#: answer. Founder, 2026-09-30: a turn runs until it is finished, and a dead run
#: is caught by liveness, not by duration.
#:
#: What catches a dead run instead: the seat lease
#: (``universe_seats.SEAT_LEASE_SECONDS``, 120s) expires on a holder that stops
#: stamping, and run-owner proof terminalizes a run whose owner is gone. A LIVE
#: long run keeps stamping and keeps its seat, which is the whole point.
#:
#: An operator may still set ``AUTOMATION_RUN_TIMEOUT_SECONDS`` for a host they
#: are nursing; it defaults to none.

#: How long a cancelled run is given to actually stop before the pump gives
#: up on it. The lease is held for the whole of it -- releasing sooner would
#: let a second process start while the first is still calling the provider.
DEFAULT_CANCEL_GRACE_SECONDS = 300

#: How often a held universe lease is re-stamped while its run is in flight.
LEASE_REFRESH_SECONDS = 60


#: The automations table, parameterised on its name so the CHECK rebuild in
#: ``_rebuild_trigger_check`` creates its successor from the same text.
_AUTOMATIONS_TABLE = """
CREATE TABLE IF NOT EXISTS __TABLE__ (
    automation_id      TEXT PRIMARY KEY,
    universe_id        TEXT NOT NULL,
    owner_principal_id TEXT NOT NULL,
    name               TEXT NOT NULL,
    branch_def_id      TEXT NOT NULL,
    trigger_kind       TEXT NOT NULL
                       CHECK(trigger_kind IN ('interval','cron','once','event')),
    interval_seconds   INTEGER NOT NULL DEFAULT 0,
    cron_expr          TEXT NOT NULL DEFAULT '',
    inputs_json        TEXT NOT NULL DEFAULT '{}',
    desired_state      TEXT NOT NULL CHECK(desired_state IN ('active','paused')),
    pause_reason       TEXT NOT NULL DEFAULT '',
    revision           INTEGER NOT NULL DEFAULT 1,
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    retired_at         TEXT NOT NULL DEFAULT '',
    last_due_at        TEXT NOT NULL DEFAULT '',
    last_run_id        TEXT NOT NULL DEFAULT '',
    last_reason        TEXT NOT NULL DEFAULT '',
    last_finished_at   TEXT NOT NULL DEFAULT ''
);
"""

_SCHEMA = _AUTOMATIONS_TABLE.replace("__TABLE__", "automations") + """
CREATE INDEX IF NOT EXISTS idx_automations_universe
    ON automations(universe_id, retired_at, created_at);

-- One row per lease key with work in flight: `<universe>::<branch>` for an
-- automation (`automation_lease_key`), the bare universe id for legacy queue
-- work. The consumer's `_active` map is process-local, so a restarted process
-- (empty map) could launch work an OLD process is still doing (Codex ADAPT
-- 2026-08-29 §8). This lease is the shared fence: it lives in the database
-- both processes read. The table keeps its first name; the column holds a key.
CREATE TABLE IF NOT EXISTS universe_leases (
    universe_id TEXT PRIMARY KEY,
    holder      TEXT NOT NULL,
    expires_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_attempts (
    automation_id TEXT NOT NULL,
    due_at        TEXT NOT NULL,
    claimed_at    TEXT NOT NULL,
    run_id        TEXT NOT NULL DEFAULT '',
    status        TEXT NOT NULL DEFAULT 'claimed',
    reason        TEXT NOT NULL DEFAULT '',
    finished_at   TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (automation_id, due_at)
);
"""

#: Columns added after the first shipped schema. Applied by probe on every
#: connect so a database written by the previous build keeps working.
_MIGRATIONS: tuple[tuple[str, str, str], ...] = (
    ("automations", "outside_origin_json", "TEXT"),
    ("automations", "consecutive_failures", "INTEGER NOT NULL DEFAULT 0"),
    ("automations", "not_before", "TEXT NOT NULL DEFAULT ''"),
    ("automations", "event_type", "TEXT NOT NULL DEFAULT ''"),
    ("automations", "event_filter_json", "TEXT NOT NULL DEFAULT '{}'"),
    ("automations", "timezone", "TEXT NOT NULL DEFAULT ''"),
    ("automations", "last_due_local", "TEXT NOT NULL DEFAULT ''"),
    ("automations", "overlap", "TEXT NOT NULL DEFAULT 'queue'"),
    ("universe_leases", "run_id", "TEXT NOT NULL DEFAULT ''"),
    ("automations", "event_key", "TEXT NOT NULL DEFAULT ''"),
)

#: One wake per (subscription, event): a terminal event is delivered at least
#: once, so its wake must be stored at most once (run-owner-proof D4). Created
#: after the migrations, which is when the column is known to exist.
_EVENT_KEY_INDEX = (
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_automations_event_key "
    "ON automations(event_key) WHERE event_key != ''"
)

#: A row read with how many attempts it has had -- the ``once`` due key.
#: Only for ``once`` rows: a recurring automation's attempt history grows
#: forever, and counting it on every poll made the scan cost grow with it
#: (Codex refute 2026-09-27, P2).
_SELECT_ROWS = (
    "SELECT automations.*, "
    "CASE WHEN trigger_kind = 'once' THEN (SELECT COUNT(*) FROM automation_attempts a "
    "WHERE a.automation_id = automations.automation_id) ELSE 0 END AS attempt_count, "
    "CASE WHEN trigger_kind = 'once' THEN (SELECT MAX(claimed_at) FROM "
    "automation_attempts a WHERE a.automation_id = automations.automation_id) "
    "ELSE '' END AS last_claimed_at "
    "FROM automations"
)


def _rebuild_trigger_check(conn: sqlite3.Connection) -> None:
    """Widen a stored ``trigger_kind`` CHECK that predates ``once`` or ``event``.

    SQLite cannot alter a CHECK, so a database written by an earlier build is
    rebuilt: successor table, copy every shared column, drop, rename, re-index.
    One ``BEGIN IMMEDIATE`` with a re-read inside it, so two processes
    connecting at once rebuild at most once and never lose a row.
    """

    def stale() -> bool:
        row = conn.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' "
            "AND name = 'automations'"
        ).fetchone()
        text = str(row[0] or "") if row is not None else ""
        return row is not None and not ("'once'" in text and "'event'" in text)

    if not stale():
        return
    conn.execute("BEGIN IMMEDIATE")
    try:
        if not stale():
            conn.execute("ROLLBACK")
            return
        conn.execute("DROP TABLE IF EXISTS automations__rebuild")
        conn.execute(_AUTOMATIONS_TABLE.replace("__TABLE__", "automations__rebuild"))
        for table, column, decl in _MIGRATIONS:
            if table == "automations":
                conn.execute(
                    f"ALTER TABLE automations__rebuild ADD COLUMN {column} {decl}"
                )
        old = [str(r[1]) for r in conn.execute("PRAGMA table_info(automations)")]
        new = {
            str(r[1])
            for r in conn.execute("PRAGMA table_info(automations__rebuild)")
        }
        shared = ", ".join(column for column in old if column in new)
        conn.execute(
            f"INSERT INTO automations__rebuild ({shared}) "
            f"SELECT {shared} FROM automations"
        )
        conn.execute("DROP TABLE automations")
        conn.execute("ALTER TABLE automations__rebuild RENAME TO automations")
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_automations_universe "
            "ON automations(universe_id, retired_at, created_at)"
        )
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise


def run_timeout_seconds() -> float | None:
    """The operator's ceiling on one automation run, or ``None`` for no ceiling.

    ``None`` is the default and the normal case: a run finishes when it is
    finished. ``AUTOMATION_RUN_TIMEOUT_SECONDS`` exists for a host being nursed
    through something, not as a product limit.
    """
    raw = os.environ.get("AUTOMATION_RUN_TIMEOUT_SECONDS", "").strip()
    if not raw:
        return None
    value = float(raw)
    if value <= 0:
        raise ValueError("AUTOMATION_RUN_TIMEOUT_SECONDS must be positive")
    return value


class AutomationRunTimeout(Exception):
    """The run outlived an OPERATOR-set ``run_timeout_seconds()``, was cancelled,
    and stopped. Unreachable unless a host sets one: there is no default."""


class AutomationRunUnstopped(AutomationRunTimeout):
    """Cancelled at timeout but STILL RUNNING after the grace period.

    A subclass so every timeout handler catches both, but the caller can tell
    the two apart where it matters: the universe lease must NOT be released
    while a provider call the owner is paying for is still in flight.
    """


def cancel_grace_seconds() -> float:
    """How long to wait for a cancelled run to actually stop."""
    raw = os.environ.get("AUTOMATION_CANCEL_GRACE_SECONDS", "").strip()
    if not raw:
        return float(DEFAULT_CANCEL_GRACE_SECONDS)
    value = float(raw)
    if value <= 0:
        raise ValueError("AUTOMATION_CANCEL_GRACE_SECONDS must be positive")
    return value


def _await_cancelled_run(run_id: str) -> bool:
    """True when the run's worker ended within the grace period.

    Cancellation is checked between nodes, so a run inside a long provider call
    keeps going after the flag is set. The caller needs to know which happened:
    a stopped run frees its universe, one still running does not.
    """
    from tinyassets.runs import wait_for

    try:
        wait_for(run_id, timeout=cancel_grace_seconds())
    except TimeoutError:
        return False
    except Exception:  # noqa: BLE001 - the worker raised, but it HAS ended
        return True
    return True


def cron_min_gap_seconds(expr: str) -> int:
    """Smallest gap, in seconds, between two minutes this cron expression fires.

    Walks one week of minute buckets, which is enough to see every
    minute/hour/day-of-month/month/day-of-week interaction the parser supports,
    and measures the smallest distance between consecutive matches INCLUDING
    the wrap from the last match back to the first. A single match in the whole
    week has no gap to measure and is reported as a week.

    The schedules lane imports this after merge -- keep the signature stable.
    """
    from tinyassets.scheduler import CronSchedule

    schedule = CronSchedule.parse(expr)
    week_minutes = 7 * 24 * 60
    # A Monday 00:00 origin, so every day-of-week is visited exactly once.
    origin = datetime(2026, 1, 5, tzinfo=timezone.utc)
    matches = [
        minute
        for minute in range(week_minutes)
        if schedule.matches((origin + timedelta(minutes=minute)).timetuple())
    ]
    if len(matches) < 2:
        return week_minutes * 60
    gaps = [
        (later - earlier) * 60
        for earlier, later in zip(matches, matches[1:])
    ]
    gaps.append((matches[0] + week_minutes - matches[-1]) * 60)
    return min(gaps)


class AutomationUnavailable(Exception):
    """A registration that cannot fire, refused instead of stored (D4).

    ``reason`` is a short snake_case token the surface maps straight onto its
    error payload, so the owner reads a cause rather than a stack trace.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Automation:
    """One stored automation row (``inputs_json`` decoded into ``inputs``)."""

    automation_id: str
    universe_id: str
    owner_principal_id: str
    name: str
    branch_def_id: str
    trigger_kind: str
    interval_seconds: int
    cron_expr: str
    inputs: dict[str, Any]
    desired_state: str
    pause_reason: str
    revision: int
    created_at: str
    updated_at: str
    retired_at: str
    last_due_at: str
    last_run_id: str
    last_reason: str
    last_finished_at: str
    consecutive_failures: int = 0
    not_before: str = ""
    #: ``event`` rows only: what they subscribe to, and the equality filter
    #: an emitted payload must match.
    event_type: str = ""
    event_filter: dict[str, Any] | None = None
    overlap: str = "queue"
    #: An event wake's identity, ``<subscription>:<event>:<id>``; '' otherwise.
    event_key: str = ""
    #: ``cron`` rows only. The IANA zone the expression is written in, resolved
    #: ONCE at create (passed -> owner's account -> UTC) and authoritative
    #: thereafter, so changing the account zone later cannot silently move an
    #: existing schedule. Empty reads as UTC, which is what every row written
    #: before 2026-09-30 already did.
    timezone: str = ""
    #: The local slot that last fired, ``YYYY-MM-DDTHH:MM`` in ``timezone``.
    #: Separate from ``last_due_at`` (a UTC instant, and the run-claim fence
    #: key) because a UTC key fires an AMBIGUOUS slot twice: the two 01:00s on a
    #: fall-back day are different UTC minutes but one slot the owner asked for.
    last_due_local: str = ""
    #: ``once`` rows only, read from ``automation_attempts``: how many attempts
    #: were claimed, and when the latest was.
    attempt_count: int = 0
    last_claimed_at: str = ""


# -- Time helpers -------------------------------------------------------------


def _as_utc(moment: datetime) -> datetime:
    """Normalize to an aware UTC datetime; a naive input is read as UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(timezone.utc)


def _iso(moment: datetime) -> str:
    """Second-resolution UTC ISO.

    Sub-second precision would make an interval's computed ``due_at``
    un-reproducible across a restart, and that reproducibility IS the fence.
    """
    return _as_utc(moment).replace(microsecond=0).isoformat()


def _parse(stamp: str) -> datetime | None:
    text = (stamp or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


# -- Store --------------------------------------------------------------------


def automations_db_path(base_path: str | Path) -> Path:
    return Path(base_path) / DB_FILENAME


def _decoded_filter(raw: Any) -> dict[str, Any]:
    try:
        value = json.loads(raw or "{}")
    except (TypeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def _from_row(row: sqlite3.Row) -> Automation:
    try:
        inputs = json.loads(row["inputs_json"] or "{}")
    except (TypeError, ValueError):
        inputs = {}
    return Automation(
        automation_id=str(row["automation_id"]),
        universe_id=str(row["universe_id"]),
        owner_principal_id=str(row["owner_principal_id"]),
        name=str(row["name"]),
        branch_def_id=str(row["branch_def_id"]),
        trigger_kind=str(row["trigger_kind"]),
        interval_seconds=int(row["interval_seconds"] or 0),
        cron_expr=str(row["cron_expr"] or ""),
        inputs=inputs if isinstance(inputs, dict) else {},
        desired_state=str(row["desired_state"]),
        pause_reason=str(row["pause_reason"] or ""),
        revision=int(row["revision"] or 1),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        retired_at=str(row["retired_at"] or ""),
        last_due_at=str(row["last_due_at"] or ""),
        last_run_id=str(row["last_run_id"] or ""),
        last_reason=str(row["last_reason"] or ""),
        last_finished_at=str(row["last_finished_at"] or ""),
        consecutive_failures=int(row["consecutive_failures"] or 0),
        not_before=str(row["not_before"] or ""),
        event_type=str(row["event_type"] or ""),
        event_filter=_decoded_filter(row["event_filter_json"]),
        overlap=str(row["overlap"] or OVERLAP_QUEUE),
        event_key=str(row["event_key"] or ""),
        # Guarded by `.keys()` like the two below: a row read through a SELECT
        # that predates the column, or a test fixture built from an older
        # schema, reads as unset rather than raising.
        timezone=str(
            (row["timezone"] if "timezone" in row.keys() else "") or ""
        ),
        last_due_local=str(
            (row["last_due_local"] if "last_due_local" in row.keys() else "") or ""
        ),
        attempt_count=int(
            (row["attempt_count"] if "attempt_count" in row.keys() else 0) or 0
        ),
        last_claimed_at=str(
            (row["last_claimed_at"] if "last_claimed_at" in row.keys() else "") or ""
        ),
    )


class AutomationStore:
    """SQLite rows for one data root. Reads never create the database.

    A read that created the file would give a flag-off daemon a visible side
    effect, and the consumer scans every serving universe on every poll.
    """

    def __init__(self, base_path: str | Path) -> None:
        self.base_path = Path(base_path)

    @property
    def db_path(self) -> Path:
        return automations_db_path(self.base_path)

    def _connect(self, *, create: bool) -> sqlite3.Connection | None:
        path = self.db_path
        if not create and not path.is_file():
            return None
        path.parent.mkdir(parents=True, exist_ok=True)
        # isolation_level=None: the fence needs an explicit BEGIN IMMEDIATE, not
        # Python's implicit deferred-transaction wrapper.
        conn = sqlite3.connect(path, timeout=30.0, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 30000")
        conn.executescript(_SCHEMA)
        _rebuild_trigger_check(conn)
        for table, column, decl in _MIGRATIONS:
            existing = {
                str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})")
            }
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
        conn.execute(_EVENT_KEY_INDEX)
        return conn

    def get_by_event_key(self, event_key: str) -> Automation | None:
        """The wake already stored for this event, or None."""
        if not event_key:
            return None
        conn = self._connect(create=False)
        if conn is None:
            return None
        try:
            row = conn.execute(
                f"{_SELECT_ROWS} WHERE event_key = ?", (event_key,),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _from_row(row)

    def get(self, automation_id: str) -> Automation | None:
        conn = self._connect(create=False)
        if conn is None:
            return None
        try:
            row = conn.execute(
                f"{_SELECT_ROWS} WHERE automation_id = ?",
                (automation_id,),
            ).fetchone()
        finally:
            conn.close()
        return None if row is None else _from_row(row)

    def list(
        self,
        *,
        universe_id: str,
        include_retired: bool = False,
    ) -> list[Automation]:
        conn = self._connect(create=False)
        if conn is None:
            return []
        query = f"{_SELECT_ROWS} WHERE universe_id = ?"
        if not include_retired:
            query += " AND retired_at = ''"
        query += " ORDER BY created_at ASC, automation_id ASC"
        try:
            rows = conn.execute(query, (universe_id,)).fetchall()
        finally:
            conn.close()
        return [_from_row(row) for row in rows]

    def list_for_branch(
        self,
        branch_def_id: str,
        *,
        include_retired: bool = False,
    ) -> list[Automation]:
        """Every automation, in ANY universe, bound to one branch. Used by the
        branch delete guard: registration promises not to store an automation
        that cannot fire, and deleting its branch would create exactly that."""
        conn = self._connect(create=False)
        if conn is None:
            return []
        query = "SELECT * FROM automations WHERE branch_def_id = ?"
        if not include_retired:
            query += " AND retired_at = ''"
        query += " ORDER BY created_at ASC, automation_id ASC"
        try:
            rows = conn.execute(query, (branch_def_id,)).fetchall()
        finally:
            conn.close()
        return [_from_row(row) for row in rows]

    def insert(self, automation: Automation) -> Automation:
        """Store a row. What bounds how many is usage, charged by the caller
        (``register_automation``), never a count of rows here."""
        conn = self._connect(create=True)
        if conn is None:  # pragma: no cover - create=True always connects
            raise RuntimeError("automation store connection is unavailable")
        try:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO automations (
                    automation_id, universe_id, owner_principal_id, name,
                    branch_def_id, trigger_kind, interval_seconds, cron_expr,
                    inputs_json, desired_state, pause_reason, revision,
                    created_at, updated_at, retired_at, last_due_at,
                    last_run_id, last_reason, last_finished_at, not_before,
                    event_type, event_filter_json, overlap, event_key, timezone,
                    last_due_local
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                          ?, ?, ?, ?, ?, ?)
                """,
                (
                    automation.automation_id,
                    automation.universe_id,
                    automation.owner_principal_id,
                    automation.name,
                    automation.branch_def_id,
                    automation.trigger_kind,
                    automation.interval_seconds,
                    automation.cron_expr,
                    json.dumps(automation.inputs, sort_keys=True),
                    automation.desired_state,
                    automation.pause_reason,
                    automation.revision,
                    automation.created_at,
                    automation.updated_at,
                    automation.retired_at,
                    automation.last_due_at,
                    automation.last_run_id,
                    automation.last_reason,
                    automation.last_finished_at,
                    automation.not_before,
                    automation.event_type,
                    json.dumps(automation.event_filter or {}, sort_keys=True),
                    automation.overlap,
                    automation.event_key,
                    automation.timezone,
                    automation.last_due_local,
                ),
            )
            from tinyassets.outside_authority import captured_identity

            conn.execute("UPDATE automations SET outside_origin_json=? WHERE automation_id=?",
                         (captured_identity(automation.owner_principal_id),
                          automation.automation_id))
            conn.execute("COMMIT")
        except AutomationUnavailable:
            raise
        except BaseException:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return automation

    def claim_attempt(self, automation_id: str, due_at: str, *, now: datetime) -> bool:
        """Claim ``(automation_id, due_at)`` for exactly one caller (D2).

        The existence check and the insert run inside one ``BEGIN IMMEDIATE`` so
        two pollers -- or one poller either side of a restart -- cannot both
        pass. Returns False when the pair is already claimed.
        """
        conn = self._connect(create=True)
        if conn is None:  # pragma: no cover - create=True always connects
            raise RuntimeError("automation store connection is unavailable")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                existing = conn.execute(
                    "SELECT 1 FROM automation_attempts "
                    "WHERE automation_id = ? AND due_at = ?",
                    (automation_id, due_at),
                ).fetchone()
                if existing is not None:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "INSERT INTO automation_attempts "
                    "(automation_id, due_at, claimed_at) VALUES (?, ?, ?)",
                    (automation_id, due_at, _iso(now)),
                )
                conn.execute("COMMIT")
                return True
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def finish_attempt(
        self,
        automation_id: str,
        due_at: str,
        *,
        run_id: str,
        status: str,
        reason: str,
        now: datetime,
        row_reason: str | None = None,
        succeeded: bool | None = None,
    ) -> None:
        """Close the attempt and roll its outcome onto the automation row.

        Both writes share one transaction: an attempt whose outcome never
        reached its automation would recompute the same ``due_at`` forever,
        claim it, and skip -- an automation stuck silent with no reason.

        ``row_reason`` lets the owner-facing row differ from the refusal-ledger
        text: the ledger keeps the consumer's ``ok:ran:<run_id>`` convention
        while the row reads a plain ``ok`` (Codex ADAPT §6 -- a success written
        only into a table named "refusals" is not an owner-legible receipt).

        ``succeeded`` drives the consecutive-failure counter: True resets it,
        False increments it, None leaves it (a skip that never ran is neither a
        success nor a failure of the work).
        """
        stamp = _iso(now)
        conn = self._connect(create=True)
        if conn is None:  # pragma: no cover - create=True always connects
            raise RuntimeError("automation store connection is unavailable")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "UPDATE automation_attempts SET run_id = ?, status = ?, "
                    "reason = ?, finished_at = ? "
                    "WHERE automation_id = ? AND due_at = ?",
                    (run_id, status, reason, stamp, automation_id, due_at),
                )
                if succeeded is True:
                    failures_sql = "consecutive_failures = 0, "
                elif succeeded is False:
                    failures_sql = (
                        "consecutive_failures = consecutive_failures + 1, "
                    )
                else:
                    failures_sql = ""
                conn.execute(
                    "UPDATE automations SET last_due_at = ?, last_run_id = ?, "
                    f"last_reason = ?, last_finished_at = ?, {failures_sql}"
                    "last_due_local = ?, updated_at = ? WHERE automation_id = ?",
                    (
                        due_at,
                        run_id,
                        reason if row_reason is None else row_reason,
                        stamp,
                        self._slot_key(conn, automation_id, due_at),
                        stamp,
                        automation_id,
                    ),
                )
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def record_event_fire(
        self, automation_id: str, *, reason: str, now: datetime,
    ) -> None:
        """Roll an event subscription's latest fire onto its own row.

        A subscription never runs itself: each matching event stores a one-shot
        wake, so without this its ``last_*`` stayed empty however often it fired
        and its owner could not tell a live subscription from a dead one.
        ``last_due_at`` is when it fired; ``last_reason`` is ``woke:<wake id>``
        or why the wake was refused. The wake's own run stays on the wake's row.
        No ``revision`` bump: this is the runtime's record, not an owner edit.
        An older fire that lands late never replaces a newer one, and never
        moves ``updated_at`` back (Codex refute 2026-09-30, P2).
        """
        stamp = _iso(now)
        conn = self._connect(create=True)
        if conn is None:  # pragma: no cover - create=True always connects
            raise RuntimeError("automation store connection is unavailable")
        try:
            with conn:
                conn.execute(
                    "UPDATE automations SET last_due_at = ?, last_reason = ?, "
                    "updated_at = MAX(updated_at, ?) "
                    "WHERE automation_id = ? AND trigger_kind = ? AND last_due_at <= ?",
                    (stamp, reason, stamp, automation_id, TRIGGER_EVENT, stamp),
                )
        finally:
            conn.close()

    # -- Cross-process per-universe lease ----------------------------------

    @staticmethod
    def _slot_key(
        conn: sqlite3.Connection, automation_id: str, due_at: str,
    ) -> str:
        """The local slot a recorded cron ``due_at`` belongs to, or ``""``.

        Read inside the caller's own transaction, so the expression and zone the
        key is derived from are the ones the row holds at the moment it is
        written. Non-cron rows have no wall-clock slot and store ``""``.
        """
        row = conn.execute(
            "SELECT trigger_kind, cron_expr, timezone FROM automations "
            "WHERE automation_id = ?",
            (automation_id,),
        ).fetchone()
        if row is None or str(row["trigger_kind"]) != TRIGGER_CRON:
            return ""
        return slot_key_for_due(
            str(row["cron_expr"] or ""), str(row["timezone"] or ""), due_at,
        )

    def _lease_blocks(self, row: sqlite3.Row, holder: str, moment: datetime,
                      *, same_key: bool) -> bool:
        """Whether this lease row keeps ``holder`` out.

        Unexpired: it blocks unless its holder is proven dead. Expired: it
        blocks only while its holder is proven ALIVE -- a live holder that
        missed its refreshes may still be calling a provider (Codex round 2,
        sequence 1 after TTL). On the SAME key a holder never blocks itself; a
        sibling key it holds does, because that is other work in flight.
        """
        current = str(row["holder"])
        if same_key and current == holder:
            return False
        expires = _parse(str(row["expires_at"]))
        unexpired = expires is not None and expires > moment
        from tinyassets.process_liveness import ALIVE, DEAD, owner_state

        state = owner_state(self.base_path, current)
        if unexpired:
            return state != DEAD
        return state == ALIVE

    def acquire_universe_lease(
        self,
        universe_id: str,
        *,
        holder: str,
        now: datetime,
        ttl_seconds: float,
        excluded_by: str = "",
        excluded_by_prefix: str = "",
    ) -> bool:
        """Take the lease on key ``universe_id``, or return False if it is held.

        `_active` in the consumer is process-local: a restarted process starts
        with an EMPTY map and would happily launch work the old process is
        still doing (Codex ADAPT §8). This lease is shared state, so both
        processes see it.

        An EXPIRED lease is stealable -- a process that died mid-run must not
        wedge its work forever. So is one whose holder is PROVABLY dead
        (``process_liveness.owner_state``): a deploy kills the process mid-run, and
        waiting out a TTL as long as the run timeout froze automations for
        hours. A holder merely late to refresh is not dead. The TTL only has to
        outlive the gap to the next re-stamp, and the holder re-stamps while it
        works, so expiry means "nobody is refreshing" -- which is the whole
        property, and it never needed a run duration behind it.

        ``excluded_by`` / ``excluded_by_prefix`` name OTHER keys whose live
        lease also keeps this one out, checked in the same transaction: an
        agent's key and its universe's legacy key exclude each other (Codex
        round 2 §3a), while two agents' keys do not.
        """
        deadline = _iso(now + timedelta(seconds=ttl_seconds))
        moment = _as_utc(now)
        conn = self._connect(create=True)
        if conn is None:  # pragma: no cover - create=True always connects
            raise RuntimeError("automation store connection is unavailable")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT holder, expires_at FROM universe_leases "
                    "WHERE universe_id = ?",
                    (universe_id,),
                ).fetchone()
                if row is not None and self._lease_blocks(
                    row, holder, moment, same_key=True
                ):
                    conn.execute("ROLLBACK")
                    return False
                others: list[sqlite3.Row] = []
                if excluded_by:
                    others += conn.execute(
                        "SELECT holder, expires_at FROM universe_leases "
                        "WHERE universe_id = ?",
                        (excluded_by,),
                    ).fetchall()
                if excluded_by_prefix:
                    # substr, not LIKE: `_` is a LIKE wildcard and universe ids
                    # are full of it.
                    others += conn.execute(
                        "SELECT holder, expires_at FROM universe_leases "
                        "WHERE substr(universe_id, 1, ?) = ?",
                        (len(excluded_by_prefix), excluded_by_prefix),
                    ).fetchall()
                if any(
                    self._lease_blocks(other, holder, moment, same_key=False)
                    for other in others
                ):
                    conn.execute("ROLLBACK")
                    return False
                conn.execute(
                    "INSERT INTO universe_leases "
                    "(universe_id, holder, expires_at, run_id) "
                    "VALUES (?, ?, ?, '') ON CONFLICT(universe_id) DO UPDATE SET "
                    "holder = excluded.holder, expires_at = excluded.expires_at, "
                    "run_id = CASE WHEN universe_leases.holder = excluded.holder "
                    "THEN universe_leases.run_id ELSE '' END",
                    (universe_id, holder, deadline),
                )
                conn.execute("COMMIT")
                return True
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()

    def set_lease_run(self, key: str, *, holder: str, run_id: str) -> None:
        """Record the run a held lease is working, for ``cancel_previous``."""
        conn = self._connect(create=True)
        if conn is None:  # pragma: no cover - create=True always connects
            raise RuntimeError("automation store connection is unavailable")
        try:
            conn.execute(
                "UPDATE universe_leases SET run_id = ? "
                "WHERE universe_id = ? AND holder = ?",
                (run_id, key, holder),
            )
        finally:
            conn.close()

    def attempt_claimed(self, automation_id: str, due_at: str) -> bool:
        """Whether ``(automation_id, due_at)`` has already been claimed."""
        conn = self._connect(create=False)
        if conn is None:
            return False
        try:
            return conn.execute(
                "SELECT 1 FROM automation_attempts "
                "WHERE automation_id = ? AND due_at = ?",
                (automation_id, due_at),
            ).fetchone() is not None
        finally:
            conn.close()

    def lease_run_id(self, key: str, *, now: datetime) -> str:
        """The run the live lease on ``key`` is working, or ''."""
        conn = self._connect(create=False)
        if conn is None:
            return ""
        try:
            row = conn.execute(
                "SELECT expires_at, run_id FROM universe_leases "
                "WHERE universe_id = ?",
                (key,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return ""
        expires = _parse(str(row["expires_at"]))
        if expires is None or expires <= _as_utc(now):
            return ""
        return str(row["run_id"] or "")

    def refresh_universe_lease(
        self,
        universe_id: str,
        *,
        holder: str,
        now: datetime,
        ttl_seconds: float,
    ) -> bool:
        """Re-stamp a lease this holder still owns. False once it has been lost."""
        deadline = _iso(now + timedelta(seconds=ttl_seconds))
        conn = self._connect(create=True)
        if conn is None:  # pragma: no cover
            raise RuntimeError("automation store connection is unavailable")
        try:
            cursor = conn.execute(
                "UPDATE universe_leases SET expires_at = ? "
                "WHERE universe_id = ? AND holder = ?",
                (deadline, universe_id, holder),
            )
            return cursor.rowcount > 0
        finally:
            conn.close()

    def release_universe_lease(self, universe_id: str, *, holder: str) -> None:
        """Drop a lease this holder owns. Never steals another holder's row."""
        conn = self._connect(create=False)
        if conn is None:
            return
        try:
            conn.execute(
                "DELETE FROM universe_leases WHERE universe_id = ? AND holder = ?",
                (universe_id, holder),
            )
        finally:
            conn.close()

    def lease_holders(self) -> set[str]:
        """Every holder named by any lease row, expired or not."""
        conn = self._connect(create=False)
        if conn is None:
            return set()
        try:
            return {
                str(row[0])
                for row in conn.execute("SELECT DISTINCT holder FROM universe_leases")
            }
        finally:
            conn.close()

    def universe_lease_holder(self, universe_id: str, *, now: datetime) -> str:
        """The live holder of this universe's lease, or '' if unheld/expired."""
        conn = self._connect(create=False)
        if conn is None:
            return ""
        try:
            row = conn.execute(
                "SELECT holder, expires_at FROM universe_leases WHERE universe_id = ?",
                (universe_id,),
            ).fetchone()
        finally:
            conn.close()
        if row is None:
            return ""
        expires = _parse(str(row["expires_at"]))
        if expires is None or expires <= _as_utc(now):
            return ""
        return str(row["holder"])

    def set_desired_state(
        self,
        automation_id: str,
        desired: str,
        *,
        expected_revision: int,
        reason: str = "",
        now: datetime,
    ) -> Automation:
        """Owner-driven pause/resume under optimistic concurrency."""
        if desired not in {STATE_ACTIVE, STATE_PAUSED}:
            raise ValueError(f"unknown desired state {desired!r}")
        return self._write_state(
            automation_id,
            expected_revision=expected_revision,
            now=now,
            desired_state=desired,
            pause_reason=reason if desired == STATE_PAUSED else "",
        )

    def retire(
        self,
        automation_id: str,
        *,
        expected_revision: int,
        now: datetime,
    ) -> Automation:
        """The owner's delete. The row stays as the record of what ran."""
        return self._write_state(
            automation_id,
            expected_revision=expected_revision,
            now=now,
            desired_state=STATE_PAUSED,
            pause_reason="retired",
            retire=True,
        )

    def retire_for_reason(
        self,
        automation_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> Automation:
        """Revision-agnostic retire for the run path: a spent ``once`` row."""
        return self._write_state(
            automation_id,
            expected_revision=None,
            now=now,
            desired_state=STATE_PAUSED,
            pause_reason=reason,
            retire=True,
        )

    def pause_for_reason(
        self,
        automation_id: str,
        *,
        reason: str,
        now: datetime,
    ) -> Automation:
        """Revision-agnostic pause for the run path (D3).

        The run path is not the owner and holds no revision expectation: an
        owner edit racing a refused run must not leave the automation active
        with authority it no longer has.
        """
        return self._write_state(
            automation_id,
            expected_revision=None,
            now=now,
            desired_state=STATE_PAUSED,
            pause_reason=reason,
        )

    def _write_state(
        self,
        automation_id: str,
        *,
        expected_revision: int | None,
        now: datetime,
        desired_state: str,
        pause_reason: str,
        retire: bool = False,
    ) -> Automation:
        stamp = _iso(now)
        conn = self._connect(create=False)
        if conn is None:
            raise ValueError(f"automation {automation_id!r} does not exist")
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM automations WHERE automation_id = ?",
                    (automation_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"automation {automation_id!r} does not exist")
                current = _from_row(row)
                if current.retired_at:
                    raise ValueError(f"automation {automation_id!r} is retired")
                if (
                    expected_revision is not None
                    and current.revision != expected_revision
                ):
                    raise ValueError(
                        f"automation {automation_id!r} is at revision "
                        f"{current.revision}, not {expected_revision}"
                    )
                conn.execute(
                    "UPDATE automations SET desired_state = ?, pause_reason = ?, "
                    "retired_at = ?, revision = ?, updated_at = ? "
                    "WHERE automation_id = ?",
                    (
                        desired_state,
                        pause_reason,
                        stamp if retire else current.retired_at,
                        current.revision + 1,
                        stamp,
                        automation_id,
                    ),
                )
                updated = conn.execute(
                    "SELECT * FROM automations WHERE automation_id = ?",
                    (automation_id,),
                ).fetchone()
                conn.execute("COMMIT")
            except BaseException:
                conn.execute("ROLLBACK")
                raise
        finally:
            conn.close()
        return _from_row(updated)


# -- Registration -------------------------------------------------------------


def _validated_not_before(not_before: Any, now: datetime) -> str:
    """A stored ``once`` instant: a past time means now; a far one is refused."""
    moment = _as_utc(now)
    parsed = _parse(str(not_before or ""))
    if parsed is None:
        raise AutomationUnavailable("trigger_invalid")
    return _iso(max(parsed, moment))


def _validated_event(event_type: Any, event_filter: Any) -> tuple[str, dict[str, Any]]:
    """An emitted event type and a flat equality filter over its payload keys."""
    kind = str(event_type or "").strip()
    if kind not in EVENT_TYPES:
        raise AutomationUnavailable("event_type_unknown")
    if event_filter is None:
        event_filter = {}
    if not isinstance(event_filter, dict):
        raise AutomationUnavailable("event_filter_invalid")
    allowed = EVENT_FILTER_KEYS[kind]
    cleaned: dict[str, Any] = {}
    for key, value in event_filter.items():
        if key not in allowed or not isinstance(value, str) or not value.strip():
            raise AutomationUnavailable("event_filter_invalid")
        cleaned[key] = value.strip()
    if not EVENT_REQUIRED_FILTER_KEYS[kind] <= set(cleaned):
        raise AutomationUnavailable("event_filter_invalid")
    # A cancelled run announces nothing (``automation_events``): a filter for
    # it would be stored and never fire.
    if kind == EVENT_RUN_COMPLETED and cleaned.get("outcome") == "cancelled":
        raise AutomationUnavailable("event_filter_invalid")
    return kind, cleaned


def _validated_trigger(interval_seconds: Any, cron_expr: Any) -> tuple[str, int, str]:
    from tinyassets.scheduler import CronParseError, CronSchedule

    try:
        seconds = int(interval_seconds or 0)
    except (TypeError, ValueError) as exc:
        raise AutomationUnavailable("trigger_invalid") from exc
    expr = str(cron_expr or "").strip()
    if seconds < 0:
        raise AutomationUnavailable("trigger_invalid")
    # Exactly one trigger: neither leaves nothing to fire on, both leaves two
    # answers to "when is this next due".
    if (seconds > 0) == bool(expr):
        raise AutomationUnavailable("trigger_invalid")
    if seconds > 0:
        if seconds < MIN_INTERVAL_SECONDS:
            raise AutomationUnavailable("trigger_invalid")
        return TRIGGER_INTERVAL, seconds, ""
    try:
        CronSchedule.parse(expr)
    except CronParseError as exc:
        raise AutomationUnavailable("trigger_invalid") from exc
    # No gap floor: `* * * * *` is a real cadence, and what it may spend is
    # the universe's run admission, charged on every fire (plan item 6).
    return TRIGGER_CRON, 0, expr


def register_automation(
    base_path: str | Path,
    *,
    universe_id: str,
    owner_principal_id: str,
    name: str,
    branch_def_id: str,
    interval_seconds: int = 0,
    cron_expr: str = "",
    not_before: str = "",
    event_type: str = "",
    event_filter: dict[str, Any] | None = None,
    overlap: str = "",
    timezone_name: str = "",
    inputs: dict[str, Any] | None = None,
    now: datetime | None = None,
    event_key: str = "",
    paused_reason: str = "",
) -> Automation:
    """Store one automation, or refuse with a named reason (D4).

    Active registration checks execution readiness in the owner's request.
    Explicitly paused copies need no connected provider: they are dormant
    metadata, and the run path still rechecks authority before every launch.

    ``event_key`` names an event wake: the wake already stored for that key is
    returned instead of a second one, before anything is charged. A package
    install uses it the same way, as its per-component idempotency key.

    ``paused_reason`` stores the row PAUSED in the same insert, so it is never
    runnable before its owner resumes it (an installed package's automations).
    """
    existing = AutomationStore(base_path).get_by_event_key(event_key)
    if existing is not None:
        return existing
    from tinyassets.api.branches import _resolve_readable_branch
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.runtime.assigned_queue_consumer import (
        assigned_queue_consumer_enabled,
    )

    base = Path(base_path)
    uid = str(universe_id or "").strip()
    owner = named_principal(owner_principal_id)

    if not paused_reason and not assigned_queue_consumer_enabled():
        raise AutomationUnavailable("consumer_disabled")
    if not owner:
        raise AutomationUnavailable("authentication_required")
    if universe_access_permission(base, universe_id=uid, actor_id=owner) != "admin":
        raise AutomationUnavailable("owner_not_admin")
    if get_founder_home(base, owner) != uid:
        raise AutomationUnavailable("not_owner_home")
    if not paused_reason:
        assignment = load_provider_assignment(base, universe_id=uid)
        if assignment is None or assignment.state != "ready":
            raise AutomationUnavailable("no_serving_assignment")
    # An OPEN (api_key_http) assignment is the owner's own source like any
    # other: foreground admission stopped refusing open providers on 2026-09-03
    # (0f96c04d), and the runtime check (_runtime_authority_reason) never did.
    # Refusing it here left free accounts with no automation at all.
    resolved = _resolve_readable_branch(str(branch_def_id or "").strip(), str(base))
    if resolved is None:
        raise AutomationUnavailable("branch_not_readable")
    # READABLE is not RUNNABLE. `_resolve_readable_branch` admits any public
    # branch, but foreground admission requires `branch.author == principal`
    # (foreground_run_provider.py:211-213). Codex registered a public
    # Bob-authored branch for Alice and every run then failed before reaching a
    # provider, leaving the automation active -- an endless cadence-driven
    # failure loop. Refuse it where the owner can read the reason.
    if str(resolved[1].get("author") or "").strip() != owner:
        raise AutomationUnavailable("branch_not_owned")
    policy = str(overlap or "").strip() or OVERLAP_QUEUE
    if policy not in OVERLAP_POLICIES:
        raise AutomationUnavailable("overlap_invalid")
    moment = _as_utc(now or datetime.now(timezone.utc))
    kind_event, event_match = "", {}
    if str(event_type or "").strip() or event_filter:
        # A subscription. Exactly one trigger still: an event has no clock.
        if (
            int(interval_seconds or 0)
            or str(cron_expr or "").strip()
            or str(not_before or "").strip()
        ):
            raise AutomationUnavailable("trigger_invalid")
        kind_event, event_match = _validated_event(event_type, event_filter)
        trigger_kind, seconds, expr, once_at = TRIGGER_EVENT, 0, "", ""
    elif str(not_before or "").strip():
        # A one-shot. Exactly one trigger still: a wake has no cadence.
        if int(interval_seconds or 0) or str(cron_expr or "").strip():
            raise AutomationUnavailable("trigger_invalid")
        trigger_kind, seconds, expr = TRIGGER_ONCE, 0, ""
        once_at = _validated_not_before(not_before, moment)
    else:
        trigger_kind, seconds, expr = _validated_trigger(interval_seconds, cron_expr)
        once_at = ""

    # The zone the expression is written in, resolved ONCE and stored. Passed
    # value wins; else the owner's own clock as their client reported it; else
    # UTC, which is stated rather than guessed. Only `cron` has a wall-clock
    # slot, so the other kinds store nothing and cannot be read as scheduled in
    # a zone they do not use.
    zone_stored = ""
    if trigger_kind == TRIGGER_CRON:
        requested = str(timezone_name or "").strip()
        if requested:
            try:
                zone_stored = normalize_timezone(requested)
            except UnknownTimezone:
                raise AutomationUnavailable("timezone_invalid") from None
        else:
            from tinyassets.storage.account_timezone import get_account_timezone

            zone_stored = (
                get_account_timezone(base, owner_user_id=owner) or DEFAULT_TIMEZONE
            )

    store = AutomationStore(base)
    stamp = _iso(moment)
    candidate = Automation(
        automation_id=uuid.uuid4().hex,
        universe_id=uid,
        owner_principal_id=owner,
        name=str(name or "").strip(),
        branch_def_id=resolved[0],
        trigger_kind=trigger_kind,
        interval_seconds=seconds,
        cron_expr=expr,
        inputs=dict(inputs or {}),
        desired_state=STATE_PAUSED if paused_reason else STATE_ACTIVE,
        pause_reason=str(paused_reason or ""),
        revision=1,
        created_at=stamp,
        updated_at=stamp,
        retired_at="",
        last_due_at="",
        last_run_id="",
        last_reason="",
        last_finished_at="",
        not_before=once_at,
        event_type=kind_event,
        event_filter=event_match,
        overlap=policy,
        event_key=event_key,
        timezone=zone_stored,
    )
    try:
        return store.insert(candidate)
    except sqlite3.IntegrityError:
        # A concurrent delivery of the same event stored it first.
        stored = store.get_by_event_key(event_key)
        if stored is None:
            raise
        return stored


# -- Cron in a timezone -------------------------------------------------------
#
# A cron expression is a WALL-CLOCK statement, so it cannot be evaluated by
# matching UTC fields (which is what this module did until 2026-09-30, via
# `time.localtime` on a UTC container -- hence a Pacific owner being promised
# 7am and scheduled for midnight). Slots are enumerated per LOCAL DATE and each
# one resolved to an instant through the zone, which is also what makes the
# decided DST policy expressible at all; `tinyassets.schedule_timezone` owns
# that resolution and states the policy.

#: How many local days back to enumerate when looking for the most recent slot.
#: One is enough: a slot just after local midnight has to be findable from the
#: minute before it, and nothing older than `_CRON_GRACE` is owed anyway.
_CRON_LOOKBACK_DAYS = 1

#: How late a slot may be claimed. This is the difference between "the poller
#: was a few minutes late" and "the daemon was off all morning", and it must
#: stay small for a reason the owner cares about: the pre-timezone code matched
#: only the CURRENT minute, so a slot the daemon slept through was skipped. A
#: generous window would turn that into a surprise run hours late -- a "morning
#: note" arriving at lunchtime, or, worse, a schedule created at noon
#: immediately firing for a slot that passed before it existed. An hour covers
#: a restart and a deploy; past that the slot is missed, which is the behaviour
#: this replaces and the honest one.
_CRON_GRACE = timedelta(hours=1)


def cron_zone_name(automation: Automation) -> str:
    """The zone this automation's expression is written in.

    Empty means UTC, which is exactly what every row written before the column
    existed already did -- so an unmigrated row keeps its current behaviour
    instead of silently moving.
    """
    return (automation.timezone or "").strip() or DEFAULT_TIMEZONE


def _cron_slots_on(schedule: Any, local_day: _date) -> list[_time]:
    """Wall-clock slots this expression matches on ``local_day``, ascending.

    Day-of-month / month / day-of-week are matched on the LOCAL date, which is
    the point: "every Monday at 7am" means the owner's Monday.
    """
    dow = (local_day.weekday() + 1) % 7
    if not (
        local_day.day in schedule.days_of_month
        and local_day.month in schedule.months
        and dow in schedule.days_of_week
    ):
        return []
    return [
        _time(hour, minute)
        for hour in sorted(schedule.hours)
        for minute in sorted(schedule.minutes)
    ]


def _latest_cron_slot(
    automation: Automation,
    moment: datetime,
    *,
    grace: timedelta | None = _CRON_GRACE,
) -> tuple[_date, _time] | None:
    """The most recent (local date, slot) whose instant is at or before ``moment``.

    Candidates are ordered by INSTANT, not by wall-clock label. Across a
    spring-forward gap the two orders differ -- several absent labels clamp to
    the moment the gap closes, so a later label can hold an earlier or equal
    instant -- and a label-ordered walk then picks a slot whose instant is not
    the latest (Codex refute, PR #4128: Lord Howe ``15,30 2 * * *`` promised
    15:45Z while selection owed 15:30Z).

    ``grace`` bounds how late a slot may be claimed; ``None`` removes the bound,
    which is what `slot_key_for_due` needs when it asks "which slot IS this
    recorded instant" rather than "what is owed now".

    A slot before the automation existed is never owed: the grace window is for
    a poller that is late, not a licence to run history. Without that floor,
    creating a 7am schedule at 07:59 local immediately owed 07:00 -- before
    there was an automation (same review, claim 3).
    """
    from tinyassets.scheduler import CronParseError, CronSchedule

    try:
        schedule = CronSchedule.parse(automation.cron_expr)
        # A stored zone can stop resolving -- the tz database drops and renames
        # names, and a host-specific one may not exist on the next host at all
        # (`docs/concerns/automation-timezone-host-aliases.md`). One such row
        # must leave itself un-runnable, not raise out of the poll that is
        # scanning EVERY automation for this universe. Same shape as the
        # unparseable expression beside it.
        zone = resolve_zone(cron_zone_name(automation))
    except (CronParseError, UnknownTimezone):
        logger.warning(
            "automation %s cannot be scheduled: unusable cron expression or "
            "timezone", automation.automation_id or "<unsaved>",
        )
        return None
    born = _parse(automation.created_at)
    today = local_now(moment, zone).date()
    candidates: list[tuple[datetime, _date, _time]] = []
    for back in range(_CRON_LOOKBACK_DAYS + 1):
        local_day = today - timedelta(days=back)
        for slot in _cron_slots_on(schedule, local_day):
            instant = slot_instant(local_day, slot, zone)
            if instant > moment:
                continue
            if grace is not None and moment - instant > grace:
                continue
            if born is not None and instant < born:
                continue
            candidates.append((instant, local_day, slot))
    if not candidates:
        return None
    # Latest instant; the local slot breaks a tie deterministically, so two
    # pollers reading the same row at the same wall-clock agree.
    instant, local_day, slot = max(candidates)
    return local_day, slot


def slot_key_for_due(
    cron_expr: str, zone_name: str, due_at: str,
) -> str:
    """The local slot key a recorded ``due_at`` belongs to, or ``""``.

    ONE definition of "which slot", shared by selection and persistence.
    `_due_instant` asks `_latest_cron_slot` for the slot owed at a moment; this
    asks the SAME function at the recorded instant, so the key stored is by
    construction the key that selection will later compare against.

    The first version inverted instead -- re-deriving the slot by matching
    instants -- and UTC to local is not injective across a spring-forward gap,
    where every absent wall time clamps to the moment the gap closes and a real
    slot can sit there too. Selection chose one of them and inversion chose
    another (the earliest), so the recorded key never matched and the slot
    stayed owed for the rest of the day (Codex refute, PR #4128, claim 2: Los
    Angeles hourly at 10:00Z selected local 03:00 and stored 02:00). Deriving
    both from one function removes the disagreement instead of trying to keep
    two derivations in step.

    Returns ``""`` for a non-cron row or an unparseable expression, which leaves
    the UTC-instant bridge in `_already_fired` as the guard.
    """
    instant = _parse(due_at)
    if instant is None or not (cron_expr or "").strip():
        return ""
    probe = Automation(
        automation_id="", universe_id="", owner_principal_id="", name="",
        branch_def_id="", trigger_kind=TRIGGER_CRON, interval_seconds=0,
        cron_expr=cron_expr, inputs={}, desired_state=STATE_ACTIVE,
        pause_reason="", revision=1, created_at="", updated_at="", retired_at="",
        last_due_at="", last_run_id="", last_reason="", last_finished_at="",
        timezone=zone_name or DEFAULT_TIMEZONE,
    )
    found = _latest_cron_slot(probe, instant, grace=None)
    if found is None:
        return ""
    # The slot selection picks at this moment must actually BE this moment. It
    # normally is -- `due_at` came from selection -- and demanding it means an
    # instant no slot produces reports "" instead of quietly claiming the
    # preceding slot, which would suppress that slot's real fire.
    try:
        zone = resolve_zone(cron_zone_name(probe))
    except UnknownTimezone:
        return ""
    if slot_instant(found[0], found[1], zone) != instant:
        return ""
    return slot_key(*found)


def _already_fired(automation: Automation, local_day: _date, slot: _time) -> bool:
    """Has this LOCAL slot already fired?

    Keyed on the local slot, not the UTC instant, because an ambiguous slot has
    two instants and is one slot: 01:00 on a Los Angeles fall-back day is both
    08:00Z and 09:00Z, and the owner asked for one 1am run.

    Bridge for a row written before `last_due_local` existed: fall back to
    comparing the stored UTC instant, so an upgrade cannot re-fire a slot that
    already ran.
    """
    key = slot_key(local_day, slot)
    if automation.last_due_local:
        return key <= automation.last_due_local
    previous = _parse(automation.last_due_at)
    if previous is None:
        return False
    zone = resolve_zone(cron_zone_name(automation))
    return slot_instant(local_day, slot, zone) <= previous


# -- Due selection ------------------------------------------------------------


def _once_due(automation: Automation) -> datetime | None:
    """``not_before``; after an attempt, one retry step past its claim.

    An attempt that never reached a run -- a refused admission, or a process
    killed before the run started -- is retried under a NEW
    ``(automation_id, due_at)`` fence key. The key is strictly later than every
    earlier one (each earlier key was claimed at or after it came due), and it
    is measured from the latest CLAIM, not from ``not_before``, so a wake
    resumed late does not find all its retries already due at once (Codex
    refute 2026-09-27, P2). Past ``MAX_ONCE_ATTEMPTS`` it stays due so the run
    path can retire it rather than leave it pending forever.
    """
    base = _parse(automation.not_before)
    if base is None:
        return None
    if automation.attempt_count <= 0:
        return base
    last = _parse(automation.last_claimed_at)
    if last is None:
        return None
    return max(base, last) + timedelta(seconds=ONCE_RETRY_SECONDS)


def _due_instant(automation: Automation, now: datetime) -> str:
    """The ``due_at`` this automation is currently owed, or '' if none.

    Deterministic in ``now``: two pollers, or one either side of a restart, at
    the same wall-clock derive the same string, so the fence holds.
    """
    moment = _as_utc(now)
    last = _parse(automation.last_due_at)
    if automation.trigger_kind == TRIGGER_INTERVAL:
        anchor = last or _parse(automation.created_at)
        if anchor is None or automation.interval_seconds <= 0:
            return ""
        elapsed = (moment - anchor).total_seconds()
        periods = int(elapsed // automation.interval_seconds)
        if periods < 1:
            return ""
        # Floor onto the period grid: a daemon down for ten intervals owes ONE
        # run, not ten. The naive anchor+interval form replays the backlog.
        return _iso(anchor + timedelta(seconds=periods * automation.interval_seconds))
    if automation.trigger_kind == TRIGGER_CRON:
        # The owner's clock, from the row -- not the process's. See
        # `cron_zone_name` and `tinyassets.schedule_timezone`.
        found = _latest_cron_slot(automation, moment)
        if found is None:
            return ""
        local_day, slot = found
        if _already_fired(automation, local_day, slot):
            return ""
        zone = resolve_zone(cron_zone_name(automation))
        return _iso(slot_instant(local_day, slot, zone))
    if automation.trigger_kind == TRIGGER_ONCE:
        due = _once_due(automation)
        if due is None or due > moment:
            return ""
        return _iso(due)
    return ""


#: How far ahead ``next_due_at`` looks for a cron match before reporting none.
NEXT_DUE_HORIZON = timedelta(days=366)


def next_due_at(automation: Automation, now: datetime) -> str:
    """When the pump will next fire this automation, or '' if it will not.

    Computed from the same trigger rules as ``_due_instant``, so the owner reads
    the time the run will actually be owed rather than a second estimate. An
    instant at or before ``now`` means the run is owed and starts on the next
    poll. Paused and retired rows never fire and report ''.
    """
    if automation.desired_state != STATE_ACTIVE or automation.retired_at:
        return ""
    owed = _due_instant(automation, now)
    if owed:
        return owed
    moment = _as_utc(now)
    if automation.trigger_kind == TRIGGER_INTERVAL:
        # Not owed means less than one period has elapsed since the anchor.
        anchor = _parse(automation.last_due_at) or _parse(automation.created_at)
        if anchor is None or automation.interval_seconds <= 0:
            return ""
        return _iso(anchor + timedelta(seconds=automation.interval_seconds))
    if automation.trigger_kind == TRIGGER_ONCE:
        due = _once_due(automation)
        return "" if due is None else _iso(due)
    if automation.trigger_kind == TRIGGER_CRON:
        from tinyassets.scheduler import CronParseError, CronSchedule

        try:
            schedule = CronSchedule.parse(automation.cron_expr)
            # Guarded together with the expression, and for the same reason: a
            # stored zone that no longer resolves makes a row un-runnable, and
            # this function is read by the projection every time an automation
            # is listed. "No next fire" is the truthful answer for a row that
            # cannot be scheduled.
            zone = resolve_zone(cron_zone_name(automation))
        except (CronParseError, UnknownTimezone):
            return ""
        # Walks LOCAL DATES and resolves each day's slots through the zone, so
        # this agrees with `_due_instant` by construction rather than by two
        # implementations happening to match. The previous form walked UTC
        # minutes and skipped by arithmetic derived from local fields; that
        # survived DST (measured), but it read the PROCESS's clock, which is the
        # defect this replaces.
        today = local_now(moment, zone).date()
        limit = (moment + NEXT_DUE_HORIZON).astimezone(zone).date()
        local_day = today
        while local_day <= limit:
            # Sorted by INSTANT within the day: wall-label order is not instant
            # order across a spring-forward gap, where absent labels clamp to
            # the gap's end. Promising the first label past `moment` returned an
            # instant LATER than the one selection would owe, so the promised
            # slot was never the one that ran (Codex refute, PR #4128).
            ahead = sorted(
                instant
                for instant in (
                    slot_instant(local_day, slot, zone)
                    for slot in _cron_slots_on(schedule, local_day)
                )
                if instant > moment
            )
            if ahead:
                return _iso(ahead[0])
            local_day += timedelta(days=1)
    return ""


def owed_since(automation: Automation, due_at: str) -> str:
    """When the run ``due_at`` stands for first became owed: the order an
    agent's due rows are taken in, longest-owed first.

    Not ``due_at`` itself. An interval collapses missed instants onto the
    LATEST one, so its ``due_at`` moves forward every poll it is not served and
    a steady stream of one-shot wakes would always look older (Codex refute
    2026-09-29, P2). Its first unserved instant does not move. A cron row is
    owed only inside its minute and forgets the last one, so it counts from
    its last run: a minute it loses is gone, and a wake can wait (round 3).
    """
    anchor = _parse(automation.last_due_at) or _parse(automation.created_at)
    if anchor is None:
        return due_at
    if automation.trigger_kind == TRIGGER_INTERVAL and automation.interval_seconds > 0:
        return _iso(anchor + timedelta(seconds=automation.interval_seconds))
    if automation.trigger_kind == TRIGGER_CRON:
        return min(_iso(anchor), due_at)
    return due_at


def due_automations(
    base_path: str | Path,
    *,
    universe_id: str,
    now: datetime,
) -> list[tuple[Automation, str]]:
    """Active, non-retired automations owed a run at ``now``, with their due_at."""
    due: list[tuple[Automation, str]] = []
    for automation in AutomationStore(base_path).list(universe_id=universe_id):
        if automation.desired_state != STATE_ACTIVE:
            continue
        due_at = _due_instant(automation, now)
        if due_at:
            due.append((automation, due_at))
    return due


def automation_lease_key(automation: Automation) -> str:
    """The lease an automation's run holds: its agent, not its row.

    One agent is one branch in one universe. A cadence, the one-shot wakes a
    run enqueues for itself, and the wakes an event stores are separate rows of
    the same agent; keyed by row, a branch that re-wakes itself would run
    beside its own previous run. Keyed by branch, it never overlaps itself,
    and two different agents in one universe run side by side.
    """
    return f"{agent_lease_prefix(automation.universe_id)}{automation.branch_def_id}"


def agent_lease_prefix(universe_id: str) -> str:
    """The prefix every agent lease key of one universe starts with, and only
    that universe's: the length field pins where the universe id ends."""
    return f"{LEASE_KEY_PREFIX}{len(universe_id)}:{universe_id}:"


def lease_key_universe(key: str) -> str:
    """The universe a lease key belongs to (a bare key is a universe id)."""
    if not key.startswith(LEASE_KEY_PREFIX):
        return key
    length, _, rest = key[len(LEASE_KEY_PREFIX):].partition(":")
    try:
        return rest[: int(length)]
    except ValueError:
        return key


def skip_overlapping(
    base_path: str | Path,
    automation: Automation,
    due_at: str,
    *,
    now: datetime,
    consumer_id: str = "",
) -> str:
    """Spend a due run whose agent is busy, under the ``skip`` policy.

    The instant is claimed and closed as skipped, so a cadence moves on to its
    next instant rather than owing this one. A one-shot wake is left untouched
    and :data:`WAITING_FOR_PREVIOUS_RUN` is returned: its one fire is all it
    has, so it waits for the agent instead. Never raises.
    """
    if automation.trigger_kind == TRIGGER_ONCE:
        return WAITING_FOR_PREVIOUS_RUN
    base = Path(base_path)
    moment = _as_utc(now)
    store = AutomationStore(base)
    reason = "skipped_overlap"
    try:
        if not store.claim_attempt(automation.automation_id, due_at, now=moment):
            return "attempt_exists"
        store.finish_attempt(
            automation.automation_id, due_at, run_id="", status="skipped",
            reason=reason, now=moment, succeeded=None,
        )
    except Exception:  # noqa: BLE001 - one owner's row cannot stop the pump
        logger.exception("overlap skip failed automation=%s", automation.automation_id)
        return "skip_error"
    _record_refusal(base, automation, reason, moment, consumer_id)
    return reason


# -- Run path -----------------------------------------------------------------


def _runtime_authority_reason(base_path: Path, automation: Automation) -> str:
    """'' when this automation may run right now, else the refusal token (D3).

    Re-derived from live state on every run. Active registration proved all
    three; a paused copy may never have had an assignment. Between two ticks
    any authority can be revoked, and the RUN has to notice, not the row.
    """
    from tinyassets.daemon_server import get_founder_home, universe_access_permission
    from tinyassets.outside_authority import automation_identity
    from tinyassets.provider_assignment import load_provider_assignment

    try:
        with automation_identity(base_path, automation):
            pass
    except (PermissionError, ValueError, OSError, sqlite3.Error):
        return "outside_client_authority_refused"
    owner = automation.owner_principal_id
    uid = automation.universe_id
    if universe_access_permission(base_path, universe_id=uid, actor_id=owner) != "admin":
        return "owner_lost_admin"
    if get_founder_home(base_path, owner) != uid:
        return "not_owner_home"
    assignment = load_provider_assignment(base_path, universe_id=uid)
    if assignment is None or assignment.state != "ready":
        return "no_serving_assignment"
    return ""


def _bind_automation_provider_call(base_path: Path, automation: Automation) -> Any:
    """The foreground run recipe, with the owner supplied explicitly.

    ``tinyassets.api.runs._bind_run_provider_call`` reads the principal off the
    request; a consumer thread has no request, so the principal comes off the
    automation row instead. Everything downstream -- founder-home validation,
    the CURRENT assignment, custody, admission, budget -- is the foreground path
    unchanged, which is what lets a provider switch need no re-preparation.
    """
    from tinyassets.config import load_universe_config
    from tinyassets.foreground_run_provider import new_foreground_run_provider_session
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.call import bind_universe_provider_call, call_provider

    universe_dir = (base_path / automation.universe_id).resolve()
    if not universe_dir.is_relative_to(base_path.resolve()):
        raise ValueError(f"invalid universe_id: {automation.universe_id!r}")
    session = new_foreground_run_provider_session(
        base_path,
        universe_id=automation.universe_id,
        principal_id=automation.owner_principal_id,
        provider_call=call_provider,
    )
    return bind_universe_provider_call(
        session,
        UniverseContext(
            universe_dir=universe_dir,
            config=load_universe_config(universe_dir),
        ),
        operation="run_graph",
    )


def _load_branch(base_path: Path, automation: Automation) -> Any:
    from tinyassets.branches import BranchDefinition
    from tinyassets.daemon_server import get_branch_definition

    branch = BranchDefinition.from_dict(
        get_branch_definition(base_path, branch_def_id=automation.branch_def_id)
    )
    errors = branch.validate()
    if errors:
        raise ValueError(
            f"branch {automation.branch_def_id} failed validation: {errors}"
        )
    return branch


#: Text the graph records when a run died because the owner's authority went
#: away mid-run. Matched by `_failure_pause_reason` to pause rather than retry.
AUTHORITY_LOST_MARKER = "automation_owner_lost_admin"


def _authority_guard(base_path: Path, automation: Automation):
    """A per-node re-check of the owner's live authority (Codex ADAPT §5).

    Codex's repro: revoke the owner's admin AFTER `_runtime_authority_reason`
    returns and BEFORE the launch, and the run still reaches the provider once.
    The window is real because the foreground session re-checks founder home
    and branch authorship on every attempt but NOT the admin ACL
    (foreground_run_provider.py:114-145, 206-246).

    This closes it from the caller's side, without editing that authority
    module. The compiler emits ``phase="starting"`` BEFORE a prompt node's
    provider call (graph_compiler.py:1187-1199), which reaches this callback via
    `runs._emit_node_status`.

    It must raise a CANCEL-shaped exception, and that is not a stylistic choice.
    The compiler wraps the seam in ``except Exception`` and re-raises only what
    `_is_cancel_exception` matches -- a NAME match on ``RunCancelledError``
    (graph_compiler.py:296-304). A `RunExecutionAuthorityLost` raised here is
    logged and SWALLOWED, and the provider is then called anyway: measured, one
    real provider call still reached the fake in Codex's repro. So the guard
    raises the runner's own cancel error, the graph unwinds before the call, and
    `run_due_automation` re-reads live authority when the run comes back
    non-completed.

    Two shapes were considered and rejected, both for evidence rather than
    taste:
      * Wrapping the BOUND provider_call: `_locate_session` requires the session
        at chain depth exactly 1, so any outer wrapper makes
        `prepare_foreground_run_provider` raise and every run fail
        (foreground_run_provider.py:666-676, 730-742).
      * Wrapping the INNER `call_provider` handed to the session: the session
        routes any callable whose `__module__` is not
        `tinyassets.providers.call` down its unarmed STUB path, skipping
        `_ensure_admitted()` and `_authorize_attempt` entirely
        (foreground_run_provider.py:537-557). That would have silently disabled
        foreground admission for every automation -- a far worse hole than the
        one being closed.
    """
    from tinyassets.runs import NODE_STATUS_RUNNING, RunCancelledError

    def guard(node_id: str, status: str) -> None:
        if status != NODE_STATUS_RUNNING:
            return
        lost = _runtime_authority_reason(base_path, automation)
        if lost:
            # The message IS the diagnosis. Measured 2026-08-29: the run row
            # ends up carrying this text verbatim (status `cancelled`, error
            # `automation_owner_lost_admin:...`), so an owner can tell an
            # authority cancel from a user pressing stop by reading the run.
            # Codex round 2 §2 named `_invoke_graph`'s generic-cancel branch as
            # the writer; it is not the one that writes this row -- reverting a
            # marker-preserving patch there changed no observed output, so no
            # change was made to that authority-adjacent file.
            raise RunCancelledError(f"{AUTHORITY_LOST_MARKER}:{lost}")

    return guard


def _execute(
    base_path: Path,
    automation: Automation,
    provider_call: Any,
    branch: Any,
    inputs: dict[str, Any],
    on_run_started: Any = None,
) -> Any:
    """The one seam a test may replace. Everything authority-bearing is above it.

    Substituting this fakes the graph, never the session: the provider call
    handed in has already resolved the universe's live assignment.

    Async-then-block, NOT the synchronous ``execute_branch``: only the async
    entry points call ``prepare_foreground_run_provider``
    (``runs.py:3205``/``3634``), and an unprepared foreground session refuses
    every provider call with ``ProviderAuthorityHeldError`` -- its receipt is
    minted against a run id it would never have been given. So a run started
    through the sync path with a foreground session bound would fail at its
    first prompt node. We start it the way ``enqueue_universe_branch_run``
    does, then block this consumer thread until the run is terminal so the
    attempt records the outcome rather than "queued", and the universe's one
    active slot stays held for as long as its automation is really running.

    The wait is UNBOUNDED unless an operator set ``run_timeout_seconds()``: a run
    finishes when it is finished (founder, 2026-09-30). A dead run is caught by
    the seat lease expiring on a holder that stopped stamping, not by a clock on
    the work. If an operator did set one, expiry CANCELS the run through the runs
    API rather than abandoning it, so the worker and its provider authority claim
    unwind instead of leaking (Codex ADAPT §1).
    """
    from dataclasses import replace as _replace

    from tinyassets.api.permissions import owner_run_identity
    from tinyassets.runs import (
        RUN_STATUS_FAILED,
        execute_branch_async,
        get_run,
        request_cancel,
        wait_for,
    )

    # The run is the owner's own work on a thread no request bound. Its worker
    # copies THIS context, so bind the owner here: its nodes read the owner's
    # private universe as the owner, and a node that wakes another branch
    # registers it as the owner (#4060's `owner_run_identity`).
    with owner_run_identity(
        base_path, automation.universe_id, automation.owner_principal_id
    ):
        outcome = execute_branch_async(
            base_path,
            branch=branch,
            inputs=inputs,
            run_name=f"automation:{automation.automation_id[:8]}",
            actor=f"universe:{automation.universe_id}",
            # The persisted owner, as every other universe:<id> run records it
            # (direct input, conversation turns, deliveries). Without it the
            # run's own children -- the owner's private branch, or an
            # unpublished pinned version -- had no owner to be authorized
            # against, and a co-admin's definition could not be told apart.
            owner_user_id=automation.owner_principal_id,
            provider_call=provider_call,
            on_node_status=_authority_guard(base_path, automation),
            _enqueue_universe_id=automation.universe_id,
        )
    run_id = str(getattr(outcome, "run_id", "") or "")
    # Publish the run id BEFORE blocking on it. Announcing it after `wait_for`
    # returned meant `stop()` could never see an active run -- the only moment
    # it needed one was while the wait was still in progress (Codex round 2 §3c).
    if callable(on_run_started) and run_id:
        on_run_started(run_id)
    if not run_id or outcome.status == RUN_STATUS_FAILED:
        # Admission already refused this run; there is no worker to wait on.
        return outcome
    try:
        wait_for(run_id, timeout=run_timeout_seconds())
    except TimeoutError as exc:
        # Cancellation is COOPERATIVE and deliberately does not interrupt an
        # active provider call. Releasing the universe now would let another
        # process start work while this one is still talking to the provider on
        # the owner's subscription (Codex round 2 §3b), so wait out a bounded
        # grace and report whether the worker actually ended.
        request_cancel(base_path, run_id)
        if _await_cancelled_run(run_id):
            raise AutomationRunTimeout(
                f"automation run {run_id} exceeded {run_timeout_seconds()}s; "
                "cancelled and stopped"
            ) from exc
        raise AutomationRunUnstopped(
            f"automation run {run_id} ignored cancellation for "
            f"{cancel_grace_seconds()}s; command center stays leased"
        ) from exc
    record = get_run(base_path, run_id) or {}
    return _replace(
        outcome,
        status=str(record.get("status") or outcome.status),
        error=str(record.get("error") or outcome.error or ""),
    )


def _record_refusal(
    base_path: Path,
    automation: Automation,
    reason: str,
    now: datetime,
    consumer_id: str,
) -> None:
    from tinyassets.storage.assigned_queue_refusals import AssignedQueueRefusalStore

    try:
        AssignedQueueRefusalStore(base_path).record(
            branch_task_id=f"{REFUSAL_KEY_PREFIX}{automation.automation_id}",
            universe_id=automation.universe_id,
            reason=reason,
            observed_at=_iso(now),
            consumer_id=consumer_id,
        )
    except Exception:  # noqa: BLE001 - the ledger must never take the pump down
        logger.exception(
            "automation refusal record failed automation=%s",
            automation.automation_id,
        )


def _append_run_ledger(automation: Automation, run_id: str, due_at: str) -> None:
    from tinyassets.api.branches import _append_global_ledger

    try:
        _append_global_ledger(
            "run_branch",
            actor=f"universe:{automation.universe_id}",
            target=run_id,
            summary=(
                f"automation={automation.automation_id} due_at={due_at} "
                f"branch={automation.branch_def_id}"
            ),
            payload=None,
        )
    except Exception as exc:  # noqa: BLE001 - ledger loss must not fail the run
        logger.warning("automation run ledger write failed: %s", exc)


#: Error text that means the run never got past admission, so retrying it next
#: period would fail identically. Pausing beats looping (Codex ADAPT §6).
_ADMISSION_REFUSED_MARKERS = (
    "ProviderAuthorityHeldError",
    "PermissionError",
    "Provider authority admission failed",
    "provider authority",
)


def _failure_pause_reason(error_text: str) -> str:
    """Why a failed run should pause the automation, or '' to retry next period."""
    text = error_text or ""
    if AUTHORITY_LOST_MARKER in text:
        return "owner_lost_admin"
    lowered = text.lower()
    if any(marker.lower() in lowered for marker in _ADMISSION_REFUSED_MARKERS):
        return "run_admission_refused"
    return ""


#: The reason recorded while an automation waits for its account's seat. Not a
#: failure and not an attempt: the row stays due and is re-offered next poll.
WAITING_FOR_SEAT = "waiting_for_seat"


def run_due_automation(
    base_path: str | Path,
    automation: Automation,
    due_at: str,
    **kwargs: Any,
) -> str:
    """Wait for the account's seat BEFORE claiming, then run one due automation.

    The seat check comes first, before any attempt is claimed, and without
    blocking: over the seat count this returns `WAITING_FOR_SEAT` with the
    automation still due and its queue position kept for the next poll
    (`universe_seats.try_acquire`). So a wait never spends a wake's
    `MAX_ONCE_ATTEMPTS`, never counts toward `MAX_CONSECUTIVE_FAILURES`, and never
    parks a consumer thread while the account is full.

    The seat it gets is given straight back, not held across the run: the run
    goes to the shared run pool, and a seat held by a run still queued for a pool
    worker, behind workers that are themselves waiting for that account's seats,
    is a deadlock (gpt-6-astra refute, 2026-09-30, round 1). The run's agent
    calls take their own seats at the executor (`graph_compiler`), and a run
    that finds the account busy again simply waits there -- no run timeout ends
    it (`run_timeout_seconds` is None), so a wait inside a claimed run is still
    neither a failure nor a second attempt.
    """
    from tinyassets import universe_seats

    base = Path(base_path)
    moment = _as_utc(kwargs.get("now") or datetime.now(timezone.utc))
    key = universe_seats.account_key(automation.universe_id, root=base)
    db = universe_seats.ledger_path(base)
    outcome = universe_seats.try_acquire(
        f"automation:{automation.automation_id}",
        key,
        kind=(universe_seats.KIND_WAKE if automation.trigger_kind == TRIGGER_ONCE
              else universe_seats.KIND_AUTOMATION),
        universe_id=automation.universe_id,
        db=db,
    )
    if isinstance(outcome, universe_seats.Waiting):
        _record_refusal(
            base, automation, WAITING_FOR_SEAT, moment, str(kwargs.get("consumer_id") or ""),
        )
        return WAITING_FOR_SEAT
    universe_seats.release(outcome.seat_id, db=db)
    from tinyassets.outside_authority import automation_identity

    try:
        with automation_identity(base_path, automation):
            return _run_due_automation(base_path, automation, due_at, **kwargs)
    except (PermissionError, ValueError, OSError, sqlite3.Error):
        reason = "outside_client_authority_refused"
        _record_refusal(base, automation, reason, moment, str(kwargs.get("consumer_id") or ""))
        return reason


def _run_due_automation(
    base_path: str | Path,
    automation: Automation,
    due_at: str,
    *,
    now: datetime | None = None,
    consumer_id: str = "",
    on_run_started: Any = None,
) -> str:
    """Run one due automation and return the reason recorded for it.

    Never raises: the caller is a pump across every universe, and one owner's
    broken automation must not stop another owner's working one. Every exit
    records a reason -- there is no silent return (Codex ADAPT §6).

    ``on_run_started`` receives the run id as soon as one exists, so the
    consumer can cancel an in-flight automation on shutdown.
    """
    # _error_reason is the consumer's bounded, path/secret-stripped formatter.
    # Imported rather than duplicated so both halves of the pump sanitise
    # identically; the function-local import keeps the two modules acyclic.
    from tinyassets.runtime.assigned_queue_consumer import _error_reason

    base = Path(base_path)
    moment = _as_utc(now or datetime.now(timezone.utc))
    store = AutomationStore(base)

    if (
        automation.trigger_kind == TRIGGER_ONCE
        and automation.attempt_count >= MAX_ONCE_ATTEMPTS
    ):
        # Out of attempts, however they ended -- including five processes killed
        # before their runs started, which never reach `_retire_once`.
        _retire_once(store, automation, ran=False, now=moment)
        _record_refusal(base, automation, "gave_up", moment, consumer_id)
        return "gave_up"

    # The claim is INSIDE the guarded region: a SQLite failure here used to
    # escape with no attempt row and no refusal, so the owner saw nothing at all.
    try:
        claimed = store.claim_attempt(automation.automation_id, due_at, now=moment)
    except Exception as exc:  # noqa: BLE001 - a fence failure is still an outcome
        reason = _error_reason("claim_error", exc)
        logger.exception(
            "automation claim failed automation=%s due_at=%s",
            automation.automation_id,
            due_at,
        )
        _record_refusal(base, automation, reason, moment, consumer_id)
        return reason
    if not claimed:
        # Only reachable when a restart re-derives an instant an older process
        # already owns. Rare, and previously invisible -- record it.
        _record_refusal(base, automation, "attempt_exists", moment, consumer_id)
        return "attempt_exists"

    # The due scan's row is a snapshot: another process may have retired or
    # paused it since -- a spent wake, or an owner's pause. Re-read under the
    # claim and run only what is still active (Codex refute 2026-09-27, P1).
    try:
        live = store.get(automation.automation_id)
    except Exception:  # noqa: BLE001 - unreadable is not active
        logger.exception("automation re-read failed automation=%s",
                         automation.automation_id)
        live = None
    if live is None or live.retired_at or live.desired_state != STATE_ACTIVE:
        _close_attempt_quietly(
            store, automation, due_at, status="skipped", reason="not_active",
            now=moment, succeeded=None,
        )
        _record_refusal(base, automation, "not_active", moment, consumer_id)
        return "not_active"

    try:
        blocked = _runtime_authority_reason(base, automation)
        if blocked:
            store.finish_attempt(
                automation.automation_id,
                due_at,
                run_id="",
                status="refused",
                reason=blocked,
                now=moment,
            )
            store.pause_for_reason(
                automation.automation_id,
                reason=blocked,
                now=moment,
            )
            _record_refusal(base, automation, blocked, moment, consumer_id)
            return blocked

        # Resolve before admission: a failed read has not started any graph
        # and must remain retryable after its source is repaired.
        try:
            from tinyassets.automation_context import resolve_automation_inputs
            current = store.get(automation.automation_id)
            if current is None:
                raise ValueError('automation_disappeared_before_context_read')
            inputs = resolve_automation_inputs(
                base, current, observed_at=_iso(datetime.now(timezone.utc))
            )
        except Exception as exc:  # noqa: BLE001 - explicit pre-execution refusal
            logger.exception("automation context unavailable")
            reason = "context_unavailable"
            store.finish_attempt(
                automation.automation_id, due_at, run_id="", status="refused",
                reason=reason, now=moment, succeeded=False,
            )
            _record_refusal(base, automation, reason, moment, consumer_id)
            _pause_if_hopeless(base, store, automation, str(exc), moment, consumer_id)
            _retire_once(store, automation, ran=False, now=moment)
            return reason

        # Settlement identity only, never a refusal: this run already holds its
        # account seat (`run_due_automation`), which is the only bound.
        from tinyassets.engine_mcp_server import _engine_run_admit

        ticket = _engine_run_admit(universe_id=automation.universe_id)

        branch = _load_branch(base, automation)
        provider_call = _bind_automation_provider_call(base, automation)

        def _started(run_id: str) -> None:
            # Bind the settlement ticket to the run the moment it exists
            # (tinyassets.engine_admissions); _execute publishes the id before
            # it blocks on completion.
            from tinyassets.engine_admissions import attach_run

            attach_run(ticket, str(run_id or ""))
            if run_id:
                # A wake is delivered when its run exists, not when the run
                # returns: a process killed mid-run must not re-run the work
                # after its lease frees (Codex refute 2026-09-27, P1).
                _retire_once(store, automation, ran=True, now=moment)
            if callable(on_run_started):
                on_run_started(run_id)

        outcome = _execute(
            base,
            automation,
            provider_call,
            branch,
            inputs,
            _started,
        )
        from tinyassets.runs import RUN_STATUS_COMPLETED

        run_id = str(getattr(outcome, "run_id", "") or "")
        status = str(getattr(outcome, "status", "") or "unknown")
        succeeded = status == RUN_STATUS_COMPLETED
        reason = f"ok:ran:{run_id}" if succeeded else f"run_failed:{status}"
        store.finish_attempt(
            automation.automation_id,
            due_at,
            run_id=run_id,
            status=status,
            reason=reason,
            now=moment,
            # The ledger keeps the consumer's `ok:ran:<id>` convention; the ROW
            # reads a plain `ok`, so an owner surface reads success from the
            # automation rather than from a table called "refusals".
            row_reason="ok" if succeeded else reason,
            succeeded=succeeded,
        )
        _record_refusal(base, automation, reason, moment, consumer_id)
        _append_run_ledger(automation, run_id, due_at)
        if not succeeded:
            _pause_if_hopeless(
                base,
                store,
                automation,
                str(getattr(outcome, "error", "") or ""),
                moment,
                consumer_id,
            )
        _retire_once(store, automation, ran=bool(run_id), now=moment)
        return reason
    except AutomationRunTimeout as exc:
        # The fence row STAYS: this instant was attempted and must not be
        # re-launched by the next poll. The run itself has been cancelled.
        # `run_timeout_unreleased` additionally tells the caller NOT to give
        # the universe back yet -- the worker ignored the cancel and is still
        # spending the owner's subscription (Codex round 2 §3b).
        reason = (
            "run_timeout_unreleased"
            if isinstance(exc, AutomationRunUnstopped)
            else "run_timeout"
        )
        logger.warning("automation run timed out: %s", exc)
        _close_attempt_quietly(
            store, automation, due_at, status="timeout", reason=reason, now=moment
        )
        _record_refusal(base, automation, reason, moment, consumer_id)
        _pause_if_hopeless(base, store, automation, "", moment, consumer_id)
        _retire_once(store, automation, ran=True, now=moment)
        return reason
    except Exception as exc:  # noqa: BLE001 - the pump continues; the row says why
        reason = _error_reason("automation_error", exc)
        logger.exception(
            "automation run failed automation=%s due_at=%s",
            automation.automation_id,
            due_at,
        )
        _close_attempt_quietly(
            store, automation, due_at, status="error", reason=reason, now=moment
        )
        _record_refusal(base, automation, reason, moment, consumer_id)
        _pause_if_hopeless(base, store, automation, str(exc), moment, consumer_id)
        _retire_once(store, automation, ran=False, now=moment)
        return reason


def _retire_once(
    store: AutomationStore,
    automation: Automation,
    *,
    ran: bool,
    now: datetime,
) -> None:
    """Spend a ``once`` row: after its run started, or out of attempts.

    A run that started is the wake delivered -- whatever the graph then did is
    its own outcome, and the graph can wake itself again. An attempt that never
    reached a run leaves the row for the next attempt key (``_once_due``).
    """
    if automation.trigger_kind != TRIGGER_ONCE:
        return
    try:
        current = store.get(automation.automation_id)
        if current is None or current.retired_at:
            return
        if not ran and current.attempt_count < MAX_ONCE_ATTEMPTS:
            return
        store.retire_for_reason(
            automation.automation_id, reason="ran" if ran else "gave_up", now=now,
        )
    except Exception:  # noqa: BLE001 - the attempt outcome is already recorded
        logger.exception(
            "once automation retire failed automation=%s", automation.automation_id
        )


def _close_attempt_quietly(
    store: AutomationStore,
    automation: Automation,
    due_at: str,
    *,
    status: str,
    reason: str,
    now: datetime,
    succeeded: bool | None = False,
) -> None:
    try:
        store.finish_attempt(
            automation.automation_id,
            due_at,
            run_id="",
            status=status,
            reason=reason,
            now=now,
            succeeded=succeeded,
        )
    except Exception:  # noqa: BLE001 - the refusal record is still owed
        logger.exception(
            "automation attempt close failed automation=%s",
            automation.automation_id,
        )


def _pause_if_hopeless(
    base_path: Path,
    store: AutomationStore,
    automation: Automation,
    error_text: str,
    now: datetime,
    consumer_id: str,
) -> None:
    """Stop a cadence that cannot succeed, instead of paying for it hourly.

    Three triggers, in order. Live authority loss wins: whatever ended the run,
    an owner who no longer holds admin over their own home must not be retried
    next period -- and the per-node guard cancels the run rather than failing
    it, so the reason is not in the error text to read. Then a deterministic
    admission/authority failure, which pauses on the first occurrence because
    the next period fails identically. Everything else gets
    `MAX_CONSECUTIVE_FAILURES` tries, because a transient provider or network
    failure should not retire an owner's automation.
    """
    reason = _runtime_authority_reason(base_path, automation)
    if not reason:
        reason = _failure_pause_reason(error_text)
    if not reason:
        current = store.get(automation.automation_id)
        failures = 0 if current is None else current.consecutive_failures
        if failures < MAX_CONSECUTIVE_FAILURES:
            return
        reason = "repeated_failures"
    try:
        store.pause_for_reason(automation.automation_id, reason=reason, now=now)
    except Exception:  # noqa: BLE001 - the run outcome is already recorded
        logger.exception(
            "automation auto-pause failed automation=%s", automation.automation_id
        )
        return
    _record_refusal(base_path, automation, reason, now, consumer_id)


__all__ = [
    "LEASE_REFRESH_SECONDS",
    "MAX_CONSECUTIVE_FAILURES",
    "MAX_ONCE_ATTEMPTS",
    "MIN_INTERVAL_SECONDS",
    "OVERLAP_CANCEL_PREVIOUS",
    "OVERLAP_POLICIES",
    "OVERLAP_QUEUE",
    "OVERLAP_SKIP",
    "ONCE_RETRY_SECONDS",
    "EVENT_FILTER_KEYS",
    "EVENT_OWNER_MESSAGE",
    "EVENT_PENDING_REQUEST_ANSWERED",
    "EVENT_RUN_COMPLETED",
    "EVENT_TYPES",
    "TRIGGER_EVENT",
    "EVENT_WOKE_PREFIX",
    "TRIGGER_ONCE",
    "REFUSAL_KEY_PREFIX",
    "WAITING_FOR_PREVIOUS_RUN",
    "Automation",
    "AutomationRunTimeout",
    "AutomationRunUnstopped",
    "AutomationStore",
    "AutomationUnavailable",
    "agent_lease_prefix",
    "automation_lease_key",
    "lease_key_universe",
    "automations_db_path",
    "cancel_grace_seconds",
    "cron_min_gap_seconds",
    "due_automations",
    "register_automation",
    "run_due_automation",
    "run_timeout_seconds",
    "owed_since",
    "skip_overlapping",
]
