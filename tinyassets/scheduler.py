"""Event-triggered branch invocation, and the cron parser automations use.

One primitive: ``subscribe_branch`` -- an event subscription that fires a branch
run when a matching event is emitted (a Source node's ``source:<id>`` event).

Persistence: ``branch_subscriptions`` in the universe's .runs.db (managed by
tinyassets.runs.initialize_runs_db). The ``Scheduler`` singleton drains the
in-process event queue on one loop and fires matching subscriptions. Events fire
exactly once per event_id (idempotency via ``scheduler_delivered_events``).

Schedules are retired. Cadences are user-owned automations
(``tinyassets.automations``), which reuse the cron parser below. The
``branch_schedules`` table is still created, and the migration stamps every row
with the retirement reason, so production rows keep a recorded disposition
until a host action drops the table.
"""

from __future__ import annotations

import inspect
import json
import logging
import queue
import sqlite3
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from tinyassets.sqlite_connection import ClosingConnection

logger = logging.getLogger(__name__)

# ─── Cron parser ──────────────────────────────────────────────────────────────

_FIELD_RANGES = [
    (0, 59),   # minute
    (0, 23),   # hour
    (1, 31),   # day-of-month
    (1, 12),   # month
    (0, 6),    # day-of-week (0=Sunday)
]

_MONTH_NAMES = {
    "jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6,
    "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12,
}
_DOW_NAMES = {
    "sun": 0, "mon": 1, "tue": 2, "wed": 3, "thu": 4, "fri": 5, "sat": 6,
}


class CronParseError(ValueError):
    pass


def _expand_field(token: str, lo: int, hi: int) -> frozenset[int]:
    """Expand one cron field token into a frozenset of matching ints."""
    token = token.lower()

    # Named substitutions (month / dow)
    for name, val in {**_MONTH_NAMES, **_DOW_NAMES}.items():
        token = token.replace(name, str(val))

    result: set[int] = set()
    for part in token.split(","):
        step = 1
        if "/" in part:
            part, step_s = part.split("/", 1)
            try:
                step = int(step_s)
            except ValueError:
                raise CronParseError(f"bad step: {step_s!r}")
            if step < 1:
                raise CronParseError(f"step must be ≥ 1, got {step}")

        if part == "*":
            result.update(range(lo, hi + 1, step))
        elif "-" in part:
            a, b = part.split("-", 1)
            try:
                start, end = int(a), int(b)
            except ValueError:
                raise CronParseError(f"bad range: {part!r}")
            if not (lo <= start <= end <= hi):
                raise CronParseError(
                    f"range {start}-{end} out of [{lo},{hi}]"
                )
            result.update(range(start, end + 1, step))
        else:
            try:
                v = int(part)
            except ValueError:
                raise CronParseError(f"bad value: {part!r}")
            if not (lo <= v <= hi):
                raise CronParseError(f"{v} out of [{lo},{hi}]")
            result.add(v)
    return frozenset(result)


@dataclass(frozen=True)
class CronSchedule:
    """Parsed cron expression (5-field standard format)."""
    minutes: frozenset[int]
    hours: frozenset[int]
    days_of_month: frozenset[int]
    months: frozenset[int]
    days_of_week: frozenset[int]
    expr: str

    @classmethod
    def parse(cls, expr: str) -> "CronSchedule":
        parts = expr.strip().split()
        if len(parts) != 5:
            raise CronParseError(
                f"cron must have 5 fields (minute hour dom month dow), got {len(parts)}: {expr!r}"
            )
        fields = [
            _expand_field(tok, lo, hi)
            for tok, (lo, hi) in zip(parts, _FIELD_RANGES)
        ]
        return cls(
            minutes=fields[0],
            hours=fields[1],
            days_of_month=fields[2],
            months=fields[3],
            days_of_week=fields[4],
            expr=expr,
        )

    def matches(self, t: time.struct_time) -> bool:
        cron_dow = (t.tm_wday + 1) % 7  # Python Mon=0…Sun=6 → cron Sun=0…Sat=6
        return (
            t.tm_min in self.minutes
            and t.tm_hour in self.hours
            and t.tm_mday in self.days_of_month
            and t.tm_mon in self.months
            and cron_dow in self.days_of_week
        )


def _cron_matches(expr: str, t: time.struct_time) -> bool:
    """Return True if cron expression matches the given UTC time struct.

    Cron expressions are evaluated in **UTC**, never host-local time: wall-clock
    minute/hour arithmetic is only exact in a zone with no DST transitions.
    """
    try:
        return CronSchedule.parse(expr).matches(t)
    except CronParseError:
        return False


#: Closed event types a subscription may name besides ``source:<id>``. Empty:
#: the four once listed here (canon_change, branch_run_completed, canon_upload,
#: pr_open) had no emitter, so a subscription to one was stored and never
#: fired. Engine events are automation triggers (``tinyassets.automation_events``).
VALID_EVENT_TYPES: frozenset[str] = frozenset()

#: A Source node emits a namespaced ``source:<source_id>`` event. These are open-ended
#: (one per user-created Source), so they are admitted by PREFIX past the closed
#: ``VALID_EVENT_TYPES`` allowlist rather than enumerated (design Floor 3).
_SOURCE_EVENT_PREFIX = "source:"
_MAX_EVENT_TYPE_LEN = 320


def _is_valid_event_type(event_type: str) -> bool:
    if event_type in VALID_EVENT_TYPES:
        return True
    if not event_type.startswith(_SOURCE_EVENT_PREFIX):
        return False
    source_id = event_type[len(_SOURCE_EVENT_PREFIX):]
    return bool(source_id) and len(event_type) <= _MAX_EVENT_TYPE_LEN

# ─── Schema helpers (called from runs.initialize_runs_db) ────────────────────

SCHEDULER_SCHEMA = """
CREATE TABLE IF NOT EXISTS branch_schedules (
    schedule_id          TEXT PRIMARY KEY,
    branch_def_id        TEXT NOT NULL,
    owner_actor          TEXT NOT NULL,
    universe_id          TEXT NOT NULL DEFAULT '',
    owner_principal_id   TEXT NOT NULL DEFAULT '',
    cron_expr            TEXT NOT NULL DEFAULT '',
    interval_seconds     REAL NOT NULL DEFAULT 0,
    inputs_template_json TEXT NOT NULL DEFAULT '{}',
    skip_if_running      INTEGER NOT NULL DEFAULT 0,
    active               INTEGER NOT NULL DEFAULT 1,
    paused               INTEGER NOT NULL DEFAULT 0,
    pause_reason         TEXT NOT NULL DEFAULT '',
    revision             INTEGER NOT NULL DEFAULT 0,
    created_at           REAL NOT NULL,
    last_fired_at        REAL
);

CREATE INDEX IF NOT EXISTS idx_schedules_owner
    ON branch_schedules(owner_actor);
CREATE INDEX IF NOT EXISTS idx_schedules_active
    ON branch_schedules(active);
CREATE INDEX IF NOT EXISTS idx_schedules_universe
    ON branch_schedules(universe_id);

CREATE TABLE IF NOT EXISTS branch_subscriptions (
    subscription_id      TEXT PRIMARY KEY,
    branch_def_id        TEXT NOT NULL,
    owner_actor          TEXT NOT NULL,
    event_type           TEXT NOT NULL,
    filter_json          TEXT NOT NULL DEFAULT '{}',
    inputs_mapping_json  TEXT NOT NULL DEFAULT '{}',
    active               INTEGER NOT NULL DEFAULT 1,
    created_at           REAL NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_subscriptions_owner
    ON branch_subscriptions(owner_actor);
CREATE INDEX IF NOT EXISTS idx_subscriptions_event
    ON branch_subscriptions(event_type, active);

CREATE TABLE IF NOT EXISTS scheduler_delivered_events (
    event_id             TEXT PRIMARY KEY,
    subscription_id      TEXT NOT NULL,
    delivered_at         REAL NOT NULL
);
"""


# ─── Schedule / subscription CRUD ─────────────────────────────────────────────

def list_bound_to_branch(base_path: str | Path, *, branch_def_id: str) -> dict[str, list[str]]:
    """Active schedule ids and subscription ids that fire this branch."""
    db = _runs_db(base_path)
    if not db.exists():
        return {"schedules": [], "subscriptions": []}
    with _connect(db) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if not {"branch_schedules", "branch_subscriptions"} <= tables:
            # No scheduler tables yet means nothing was ever registered here.
            return {"schedules": [], "subscriptions": []}
        migrate_scheduler_schema(conn)
        schedules = [
            str(r[0]) for r in conn.execute(
                "SELECT schedule_id FROM branch_schedules WHERE branch_def_id = ? AND active = 1",
                (branch_def_id,),
            )
        ]
        subscriptions = [
            str(r[0]) for r in conn.execute(
                "SELECT subscription_id FROM branch_subscriptions WHERE branch_def_id = ? AND active = 1",
                (branch_def_id,),
            )
        ]
    return {"schedules": schedules, "subscriptions": subscriptions}


def list_scheduler_subscriptions(
    base_path: str | Path,
    *,
    owner_actor: str = "",
    event_type: str = "",
    active_only: bool = True,
) -> list[dict[str, Any]]:
    """List event subscriptions, optionally filtered by owner and/or event_type."""
    db = _runs_db(base_path)
    with _connect(db) as conn:
        q = "SELECT * FROM branch_subscriptions"
        params: list[Any] = []
        clauses: list[str] = []
        if active_only:
            clauses.append("active=1")
        if owner_actor:
            clauses.append("owner_actor=?")
            params.append(owner_actor)
        if event_type:
            clauses.append("event_type=?")
            params.append(event_type)
        if clauses:
            q += " WHERE " + " AND ".join(clauses)
        rows = conn.execute(q, params).fetchall()
    return [dict(r) for r in rows]


def register_subscription(
    base_path: str | Path,
    *,
    branch_def_id: str,
    owner_actor: str,
    event_type: str,
    filter_json: dict[str, Any] | None = None,
    inputs_mapping: dict[str, Any] | None = None,
) -> str:
    """Register an event subscription. Returns subscription_id."""
    if not _is_valid_event_type(event_type):
        raise ValueError(
            f"unknown event_type {event_type!r}; only a "
            f"'{_SOURCE_EVENT_PREFIX}<id>' source event is subscribable here"
        )
    db = _runs_db(base_path)
    with _connect(db) as conn:
        # Not counted per owner: each fire is charged as a run (plan item 6).
        sub_id = str(uuid.uuid4())
        conn.execute(
            """
            INSERT INTO branch_subscriptions
                (subscription_id, branch_def_id, owner_actor, event_type,
                 filter_json, inputs_mapping_json, active, created_at)
            VALUES (?,?,?,?,?,?,1,?)
            """,
            (
                sub_id,
                branch_def_id,
                owner_actor,
                event_type,
                json.dumps(filter_json or {}),
                json.dumps(inputs_mapping or {}),
                time.time(),
            ),
        )
    return sub_id


def unregister_subscription(
    base_path: str | Path,
    subscription_id: str,
    *,
    requesting_actor: str,
    admin: bool = False,
) -> bool:
    """Deactivate a subscription. Owner or admin only."""
    db = _runs_db(base_path)
    with _connect(db) as conn:
        row = conn.execute(
            "SELECT owner_actor FROM branch_subscriptions WHERE subscription_id=?",
            (subscription_id,),
        ).fetchone()
        if not row:
            return False
        if not admin and row["owner_actor"] != requesting_actor:
            raise PermissionError(
                f"{requesting_actor!r} is not the owner of subscription {subscription_id!r}"
            )
        conn.execute(
            "UPDATE branch_subscriptions SET active=0 WHERE subscription_id=?",
            (subscription_id,),
        )
    return True


# ─── Event emission ───────────────────────────────────────────────────────────

@dataclass
class SchedulerEvent:
    event_type: str
    event_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    payload: dict[str, Any] = field(default_factory=dict)
    #: The principal the fired run acts for. Stamped by the emitter from the
    #: hook's owner (authenticated-owner boundary D2); the event thread has no
    #: request identity to fall back to, and there is no synthetic one.
    owner_principal_id: str = ""


def emit_event(event: SchedulerEvent) -> None:
    """Emit an event into the global scheduler's queue (if running)."""
    s = _SINGLETON
    if s is not None:
        s._event_queue.put(event)


def is_running() -> bool:
    """Whether the global scheduler is up and its loops are alive.

    A Source delivery may only be published onto a live event queue. That needs
    thread liveness, not merely the presence of a singleton object: a Scheduler
    that was constructed but never started, or whose loop died, would otherwise
    read as available.
    """
    s = _SINGLETON
    return s is not None and s.is_alive()


# ─── Scheduler singleton ──────────────────────────────────────────────────────

def _accepts_principal_id(run_fn: Callable[..., None]) -> bool:
    """Whether ``run_fn`` accepts the ``principal_id`` keyword.

    The run_fn contract is ``run_fn(branch_def_id, actor, inputs, run_name, *,
    principal_id="")``. An event carries the hook owner as its principal; a
    four-argument callable still serves, and is simply not given one.
    """
    try:
        params = inspect.signature(run_fn).parameters.values()
    except (TypeError, ValueError):  # builtins / C callables expose no signature
        return False
    if any(p.kind is inspect.Parameter.VAR_KEYWORD for p in params):
        return True
    return any(
        p.name == "principal_id"
        and p.kind
        in (inspect.Parameter.KEYWORD_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
        for p in params
    )


class Scheduler:
    """Drives event subscriptions against a universe DB."""

    def __init__(
        self,
        base_path: str | Path,
        run_fn: Callable[..., None],
    ) -> None:
        """
        Args:
            base_path: universe directory (contains .runs.db).
            run_fn:    callable(branch_def_id, actor, inputs, run_name, *,
                       principal_id="") — fires a branch run. Called in a separate
                       thread; must be thread-safe. ``principal_id`` is passed only
                       when the callable accepts it.
        """
        self._base_path = Path(base_path)
        self._run_fn = run_fn
        self._run_fn_takes_principal = _accepts_principal_id(run_fn)
        self._event_queue: queue.Queue[SchedulerEvent] = queue.Queue()
        self._stop = threading.Event()
        self._event_thread: threading.Thread | None = None

    # ── Lifecycle ──

    def start(self) -> None:
        """Start the event loop. Idempotent if already running."""
        if self._event_thread and self._event_thread.is_alive():
            return
        self._stop.clear()
        self._event_thread = threading.Thread(
            target=self._event_loop, daemon=True, name="scheduler-event"
        )
        self._event_thread.start()
        logger.info("Scheduler started (base=%s)", self._base_path)

    def is_alive(self) -> bool:
        """Whether the event loop is running right now."""
        return bool(self._event_thread and self._event_thread.is_alive())

    def stop(self, timeout: float = 5.0) -> None:
        """Signal the loop to stop and wait for it to exit."""
        self._stop.set()
        self._event_queue.put(_STOP_SENTINEL)  # unblock event loop
        if self._event_thread:
            self._event_thread.join(timeout=timeout)
        logger.info("Scheduler stopped")

    # ── Event loop ──

    def _event_loop(self) -> None:
        while not self._stop.is_set():
            try:
                event = self._event_queue.get(timeout=1.0)
            except queue.Empty:
                continue
            if event is _STOP_SENTINEL:
                break
            try:
                self._dispatch_event(event)
            except Exception:
                logger.exception("scheduler: event dispatch error for %s", event.event_id)

    def _subscription_active(self, subscription_id: str) -> bool:
        """Whether a subscription is still active RIGHT NOW (Codex #3 revocation re-check)."""
        db = _runs_db(self._base_path)
        try:
            with _connect(db) as conn:
                row = conn.execute(
                    "SELECT 1 FROM branch_subscriptions "
                    "WHERE subscription_id=? AND active=1",
                    (subscription_id,),
                ).fetchone()
            return row is not None
        except sqlite3.Error:
            # Fail closed: if we cannot confirm it is active, do not fire.
            logger.exception("scheduler: active re-check failed for %s", subscription_id)
            return False

    def _dispatch_event(self, event: SchedulerEvent) -> None:
        db = _runs_db(self._base_path)
        try:
            with _connect(db) as conn:
                subs = conn.execute(
                    "SELECT * FROM branch_subscriptions WHERE event_type=? AND active=1",
                    (event.event_type,),
                ).fetchall()
        except sqlite3.Error:
            logger.exception("scheduler: DB read failed for event dispatch")
            return

        for sub in subs:
            sub_id = sub["subscription_id"]
            # Idempotency: skip if already delivered.
            delivery_key = f"{event.event_id}:{sub_id}"
            try:
                with _connect(db) as conn:
                    already = conn.execute(
                        "SELECT 1 FROM scheduler_delivered_events WHERE event_id=?",
                        (delivery_key,),
                    ).fetchone()
                    if already:
                        continue
                    # Mark delivered before firing to prevent double-fire on crash.
                    _SQL_MARK_DELIVERED = (
                        "INSERT INTO scheduler_delivered_events "
                        "(event_id, subscription_id, delivered_at) VALUES (?,?,?)"
                    )
                    conn.execute(
                        _SQL_MARK_DELIVERED,
                        (delivery_key, sub_id, time.time()),
                    )
            except sqlite3.Error:
                logger.exception("scheduler: idempotency write failed for %s", delivery_key)
                continue

            # Apply event-type filter if any.
            event_filter = json.loads(sub["filter_json"] or "{}")
            if event_filter:
                if not _matches_filter(event.payload, event_filter):
                    continue

            inputs_mapping = json.loads(sub["inputs_mapping_json"] or "{}")
            if inputs_mapping:
                inputs = {k: event.payload.get(v, v) for k, v in inputs_mapping.items()}
            elif event.event_type.startswith(_SOURCE_EVENT_PREFIX):
                # A Source event carries the branch inputs verbatim under "inputs".
                raw_inputs = event.payload.get("inputs")
                inputs = raw_inputs if isinstance(raw_inputs, dict) else {}
            else:
                inputs = {}
            # A subscription owned by a universe fires its branch AS that universe (the
            # correct branch_run actor), never wrapped as a "subscriber:" identity. Legacy
            # (non-universe) owners keep the subscriber prefix.
            owner = str(sub["owner_actor"])
            actor = owner if owner.startswith("universe:") else f"subscriber:{owner}"
            run_name = f"event:{event.event_type}:{sub_id[:8]}"
            # Codex #3 revocation race: re-check the subscription is STILL active
            # immediately before firing. A revoke (create_source→revoke_source, or
            # unsubscribe) that landed after the snapshot above now takes effect, so an
            # in-flight event for a revoked source does not run.
            if not self._subscription_active(sub_id):
                logger.info("scheduler: subscription %s revoked before fire; skipping", sub_id)
                continue
            try:
                if self._run_fn_takes_principal:
                    self._run_fn(
                        sub["branch_def_id"], actor, inputs, run_name,
                        principal_id=str(event.owner_principal_id or "").strip(),
                    )
                else:
                    self._run_fn(sub["branch_def_id"], actor, inputs, run_name)
                logger.info(
                    "scheduler: fired subscription %s on event %s",
                    sub_id,
                    event.event_type,
                )
            except Exception:
                logger.exception(
                    "scheduler: run_fn failed for subscription %s on event %s",
                    sub_id,
                    event.event_type,
                )


def _matches_filter(payload: dict[str, Any], filter_json: dict[str, Any]) -> bool:
    """Simple equality filter: each key in filter_json must match payload."""
    for k, v in filter_json.items():
        if payload.get(k) != v:
            return False
    return True


# ─── Internal helpers ─────────────────────────────────────────────────────────

class _StopSentinel:
    pass


_STOP_SENTINEL = _StopSentinel()
_SINGLETON: Scheduler | None = None
_SINGLETON_LOCK = threading.Lock()


def _runs_db(base_path: str | Path) -> Path:
    return Path(base_path) / ".runs.db"


#: Columns added to ``branch_schedules`` after its initial schema, with the DDL
#: fragment that adds each one. SQLite has no ``ADD COLUMN IF NOT EXISTS``, so
#: the migration probes ``PRAGMA table_info`` and adds whatever is missing.
#:
#: ``universe_id`` / ``owner_principal_id`` carry the two identities a run needs
#: (user-owned-automations 2.1): which universe executes the branch, and which
#: authenticated principal authorised it. A row predating them keeps '' for both
#: and is LEGACY — it never fires, because a run with no owner would have to fall
#: back to an ambient identity, which is exactly what the founder principle
#: forbids. ``pause_reason`` carries why the tick auto-paused a row (D3/D5), so
#: the owner can read the cause on their own surface. ``revision`` is the
#: optimistic-concurrency counter every state-changing write compares and bumps,
#: so a tick acting on a stale observation loses instead of clobbering.
_SCHEDULE_COLUMN_MIGRATIONS: tuple[tuple[str, str], ...] = (
    ("paused", "INTEGER NOT NULL DEFAULT 0"),
    ("universe_id", "TEXT NOT NULL DEFAULT ''"),
    ("owner_principal_id", "TEXT NOT NULL DEFAULT ''"),
    ("pause_reason", "TEXT NOT NULL DEFAULT ''"),
    ("revision", "INTEGER NOT NULL DEFAULT 0"),
)


#: The recorded disposition of every ``branch_schedules`` row once schedules were
#: retired (dark-code deletion, 2026-09-28). Production held 2 rows, both inactive.
RETIRED_SCHEDULE_REASON = (
    "retired_scheduler: schedules were retired with the scheduler's tick loop; "
    "recreate this as an automation with write_graph target=automation"
)


def migrate_scheduler_schema(conn: sqlite3.Connection) -> None:
    """Add every post-initial ``branch_schedules`` column, then stamp every row retired.

    Idempotent, concurrency-safe.

    **This MUST run before ``SCHEDULER_SCHEMA`` is executed**, not after.
    ``SCHEDULER_SCHEMA`` contains ``CREATE INDEX ... branch_schedules(universe_id)``;
    on an existing install that index names a column the old table does not have
    yet, so ``initialize_runs_db`` died with ``OperationalError: no such column:
    universe_id`` before any migration could run. Ordering the migration first is
    the whole fix — a migration that runs after the thing it enables is not a
    migration (Codex ADAPT on 44caf369, reproduced against an old-schema DB).

    The probe and the ALTERs run inside one ``BEGIN IMMEDIATE`` so two connections
    opening the same DB cannot both observe a missing column and both try to add
    it; the duplicate-column error is still caught, because the write lock is
    only held per connection and a process that lost a race must treat the column
    as already present rather than crash.
    """
    if not {row[1] for row in conn.execute("PRAGMA table_info(branch_schedules)")}:
        # Table not laid down yet: CREATE TABLE brings every column with it, and
        # there are no rows to stamp.
        return
    conn.commit()  # close any implicit transaction so BEGIN IMMEDIATE can take the lock
    conn.execute("BEGIN IMMEDIATE")
    try:
        # Re-probe INSIDE the write lock: the check and the ALTER have to be one
        # atomic step, or the check is just a guess made before the lock.
        cols = {row[1] for row in conn.execute("PRAGMA table_info(branch_schedules)")}
        for col, ddl in _SCHEDULE_COLUMN_MIGRATIONS:
            if col in cols:
                continue
            try:
                conn.execute(f"ALTER TABLE branch_schedules ADD COLUMN {col} {ddl}")
            except sqlite3.OperationalError as exc:
                if "duplicate column" not in str(exc).lower():
                    raise
        # Schedules are retired (no tick loop fires them). Each row keeps a
        # recorded disposition instead of being dropped: inactive, paused, and
        # the reason on the row the owner can read.
        conn.execute(
            "UPDATE branch_schedules SET active = 0, paused = 1, pause_reason = ? "
            "WHERE pause_reason != ?",
            (RETIRED_SCHEDULE_REASON, RETIRED_SCHEDULE_REASON),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def _connect(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path), timeout=30.0, factory=ClosingConnection)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    migrate_scheduler_schema(conn)
    return conn


def get_or_create_scheduler(
    base_path: str | Path,
    run_fn: Callable[..., None],
) -> Scheduler:
    """Return the process-global Scheduler, creating it if needed."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is None:
            _SINGLETON = Scheduler(base_path, run_fn)
            _SINGLETON.start()
        return _SINGLETON


def shutdown_scheduler(timeout: float = 5.0) -> None:
    """Stop the global scheduler (used in tests and daemon shutdown)."""
    global _SINGLETON
    with _SINGLETON_LOCK:
        if _SINGLETON is not None:
            _SINGLETON.stop(timeout=timeout)
            _SINGLETON = None


__all__ = [
    "CronParseError",
    "CronSchedule",
    "Scheduler",
    "SchedulerEvent",
    "SCHEDULER_SCHEMA",
    "RETIRED_SCHEDULE_REASON",
    "VALID_EVENT_TYPES",
    "emit_event",
    "get_or_create_scheduler",
    "is_running",
    "migrate_scheduler_schema",
    "list_scheduler_subscriptions",
    "register_subscription",
    "shutdown_scheduler",
    "unregister_subscription",
]
