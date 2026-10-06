"""Activities: the agent's background work, several at once, with no chat open
(harness D2; change ``universe-agent-activities``).

ChatGPT dots keep working on several things while the owner is away and show
them under *In progress / Scheduled / Completed*. Here an activity is a child
session of the universe's agent, keyed ``activity:<id>``, with a durable record:

* **Where.** ``.agent-sessions/<universe>/agent-activities.db`` in the data
  root, beside ``rules.db`` and outside every universe folder, so nothing the
  agent runs can forge a status, a runner claim or an effect intent (design D1).
* **One live run.** An activity executes as runs of the universe's
  *Activities* branch (design D3). The dispatcher claims it (the next
  generation), reserves a run, binds that run's id to the record, and only
  then releases it; the run executes only if the record still names it. A run
  is replaced only once it ended or was interrupted, and every runner write
  names its generation, so a superseded run's writes change nothing.
* **Reads never truncate.** Listing is keyset-paged by ``(updated_at, id)``;
  every field is bounded when it is written, so a page cannot grow without
  limit and nothing is cut to fit.

Every transition runs under ``BEGIN IMMEDIATE`` and is checked against the
allowed moves below; an owner edit can also pin the record's ``revision``.
"""

from __future__ import annotations

import base64
import functools
import json
import re
import secrets
import sqlite3
import time
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

from tinyassets import agent_sessions

_FILE = "agent-activities.db"

SCHEDULED = "scheduled"
IN_PROGRESS = "in_progress"
WAITING_ON_YOU = "waiting_on_you"
PAUSED = "paused"
COMPLETED = "completed"
FAILED = "failed"
STATUSES = (SCHEDULED, IN_PROGRESS, WAITING_ON_YOU, PAUSED, COMPLETED, FAILED)
RESTING = frozenset({WAITING_ON_YOU, PAUSED, COMPLETED, FAILED})
TERMINAL = frozenset({COMPLETED, FAILED})

ORIGINS = ("ask", "proposal", "schedule")

#: Allowed moves. ``waiting_on_you`` and ``paused`` go back to ``scheduled``
#: (queued for a seat), never straight to running: a runner claims it from there.
_MOVES = {
    SCHEDULED: {PAUSED, COMPLETED, FAILED},
    IN_PROGRESS: {WAITING_ON_YOU, PAUSED, COMPLETED, FAILED, SCHEDULED},
    WAITING_ON_YOU: {SCHEDULED, PAUSED, COMPLETED, FAILED},
    PAUSED: {SCHEDULED, COMPLETED},
    COMPLETED: set(),
    FAILED: set(),
}

MAX_TITLE = 200
MAX_BRIEF = 16 * 1024
MAX_SUMMARY = 4_000
MAX_REASON = 300
MAX_EVENTS = 200
PAGE = 50
#: A claim that never bound a run is taken over only after this long, so a
#: second dispatcher never starts a duplicate run for a claim still binding.
UNBOUND_GRACE_S = 120.0
#: A run that will not start is retried with backoff, then the activity fails.
MAX_START_FAILURES = 3
START_BACKOFF_S = 30.0

_ID = re.compile(r"^act_[0-9a-f]{16}$")
_CONTROL = re.compile(r"[\x00-\x1f\x7f]+")

_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS activities (
        activity_id        TEXT PRIMARY KEY,
        agent_id           TEXT NOT NULL DEFAULT 'main',
        parent_activity_id TEXT NOT NULL DEFAULT '',
        session_key        TEXT NOT NULL UNIQUE,
        owner_principal    TEXT NOT NULL,
        title              TEXT NOT NULL,
        brief              TEXT NOT NULL,
        origin_kind        TEXT NOT NULL CHECK (origin_kind IN ('ask','proposal','schedule')),
        origin_ref         TEXT NOT NULL DEFAULT '',
        approval_id        TEXT NOT NULL DEFAULT '',
        status             TEXT NOT NULL CHECK (status IN ('scheduled','in_progress',
                             'waiting_on_you','paused','completed','failed')),
        outcome            TEXT NOT NULL DEFAULT '',
        waiting_reason     TEXT NOT NULL DEFAULT '',
        waiting_request_id TEXT NOT NULL DEFAULT '',
        result_summary     TEXT NOT NULL DEFAULT '',
        result_path        TEXT NOT NULL DEFAULT '',
        last_tool_seq      INTEGER NOT NULL DEFAULT 0,
        runner_token       TEXT NOT NULL DEFAULT '',
        runner_generation  INTEGER NOT NULL DEFAULT 0,
        claimed_at         REAL NOT NULL DEFAULT 0,
        retiring_token     TEXT NOT NULL DEFAULT '',
        start_failures     INTEGER NOT NULL DEFAULT 0,
        revision           INTEGER NOT NULL DEFAULT 1,
        created_at         REAL NOT NULL,
        updated_at         REAL NOT NULL,
        finished_at        REAL NOT NULL DEFAULT 0)""",
    """CREATE INDEX IF NOT EXISTS activities_by_creation
        ON activities(created_at DESC, activity_id DESC)""",
    """CREATE UNIQUE INDEX IF NOT EXISTS activities_by_schedule
        ON activities(origin_ref) WHERE origin_kind = 'schedule'""",
    """CREATE TABLE IF NOT EXISTS effect_intents (
        intent_key    TEXT PRIMARY KEY,
        activity_id   TEXT NOT NULL,
        run_id        TEXT NOT NULL,
        node_key      TEXT NOT NULL,
        effect_index  INTEGER NOT NULL,
        wire_digest   TEXT NOT NULL,
        connection_id TEXT NOT NULL,
        operation     TEXT NOT NULL,
        path          TEXT NOT NULL,
        state         TEXT NOT NULL CHECK (state IN ('planned','sent','confirmed','failed',
                        'unknown','owner_resolved')),
        resolution    TEXT NOT NULL DEFAULT '',
        receipt_json  TEXT NOT NULL DEFAULT '',
        created_at    REAL NOT NULL,
        updated_at    REAL NOT NULL)""",
    """CREATE INDEX IF NOT EXISTS effect_intents_by_activity
        ON effect_intents(activity_id, created_at)""",
    """CREATE TABLE IF NOT EXISTS activity_events (
        activity_id TEXT NOT NULL,
        seq         INTEGER NOT NULL,
        ts          REAL NOT NULL,
        kind        TEXT NOT NULL,
        line        TEXT NOT NULL,
        delivered   INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY (activity_id, seq))""",
)

_COLUMNS = (
    "activity_id", "agent_id", "parent_activity_id", "session_key", "owner_principal",
    "title", "brief", "origin_kind", "origin_ref", "approval_id", "status", "outcome",
    "waiting_reason", "waiting_request_id", "result_summary", "result_path",
    "last_tool_seq", "runner_token", "runner_generation", "claimed_at", "retiring_token",
    "start_failures", "revision", "created_at",
    "updated_at", "finished_at",
)


class ActivityRefused(ValueError):
    """The request cannot be applied as asked; the message says why."""

    def __init__(self, message: str, *, kind: str = "activity_refused") -> None:
        super().__init__(message)
        self.kind = kind


def store_path(universe_dir: Path) -> Path:
    root = Path(universe_dir)
    return root.parent / agent_sessions.RECORDS_DIR / root.name / _FILE


class _NoStore(Exception):
    """The store does not exist (never created, or removed with the account)."""


def _connect(universe_dir: Path, *, create: bool = False) -> sqlite3.Connection:
    """Open the store. Only ``create`` may bring it into existence -- and only for
    a universe that exists -- so a runner outliving an account deletion can
    never write the store back."""
    path = store_path(universe_dir)
    if create:
        if not Path(universe_dir).is_dir():
            raise ActivityRefused("That universe does not exist.", kind="not_found")
        agent_sessions._records_dir(Path(universe_dir))
        conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    else:
        if not path.is_file():
            raise _NoStore()
        conn = sqlite3.connect(f"{path.as_uri()}?mode=rw", uri=True, timeout=10.0,
                               isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 10000")
    conn.execute("PRAGMA journal_mode = WAL")
    for statement in _SCHEMA:
        conn.execute(statement)
    for table, name, declaration in (
        ("activities", "task_generation", "INTEGER NOT NULL DEFAULT 1"),
        ("activities", "task_expires_at", "REAL NOT NULL DEFAULT 0"),
        ("activities", "continuation_only", "INTEGER NOT NULL DEFAULT 0"),
        ("activity_events", "dedupe_key", "TEXT"),
    ):
        if name not in {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {declaration}")
    return conn


@contextmanager
def _txn(universe_dir: Path, *, create: bool = False) -> Iterator[sqlite3.Connection]:
    with closing(_connect(universe_dir, create=create)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")


def _when_absent(default: Callable[[], object]):
    """With no store there is nothing to read or move: answer ``default()``."""
    def wrap(fn):
        @functools.wraps(fn)
        def inner(*args, **kwargs):
            try:
                return fn(*args, **kwargs)
            except _NoStore:
                return default()
        return inner
    return wrap


def _not_found():
    raise ActivityRefused("No such activity.", kind="not_found")


def _one_line(text: object, limit: int) -> str:
    return _CONTROL.sub(" ", str(text or "")).strip()[:limit]


def _row(row) -> dict:
    return dict(zip(_COLUMNS, row, strict=True))


def _get(conn: sqlite3.Connection, activity_id: str) -> dict | None:
    row = conn.execute(
        f"SELECT {', '.join(_COLUMNS)} FROM activities WHERE activity_id = ?", (activity_id,)
    ).fetchone()
    return _row(row) if row else None


def _quoted(title: str) -> str:
    """The only agent-supplied text a status line carries: one line, bounded."""
    return json.dumps(_one_line(title, 80), ensure_ascii=False)


_LINES = {
    "created": "is queued.",
    IN_PROGRESS: "is running.",
    "resumed": "picked up where it left off after a restart.",
    WAITING_ON_YOU: "is waiting on your owner.",
    PAUSED: "is paused.",
    SCHEDULED: "is queued again.",
    COMPLETED: "finished.",
    FAILED: "failed.",
    "stopped": "was stopped; its result so far is kept.",
    "waiting_for_seat": "is waiting for a free agent seat.",
}


def _event(conn: sqlite3.Connection, record: dict, kind: str, reason: str = "") -> None:
    activity_id = record["activity_id"]
    line = f"Activity {activity_id} {_quoted(record['title'])} {_LINES[kind]}"
    if reason:
        line += f" ({reason})"
    seq = conn.execute(
        "SELECT COALESCE(MAX(seq), 0) + 1 FROM activity_events WHERE activity_id = ?",
        (activity_id,),
    ).fetchone()[0]
    conn.execute(
        "INSERT INTO activity_events (activity_id, seq, ts, kind, line) VALUES (?, ?, ?, ?, ?)",
        (activity_id, seq, time.time(), kind, _one_line(line, MAX_REASON)),
    )
    # Bounded to the newest MAX_EVENTS lines, delivered or not: status lines are
    # a recent window onto the record, which stays the truth, so an activity that
    # loops through waits cannot grow its history without limit.
    conn.execute(
        "DELETE FROM activity_events WHERE activity_id = ? AND seq <= ? AND dedupe_key IS NULL",
        (activity_id, seq - MAX_EVENTS),
    )


def _bump(record: dict) -> float:
    """A strictly later ``updated_at``, so keyset paging always moves."""
    return max(time.time(), record["updated_at"] + 1e-6)


@contextmanager
def _agent_creation_admission(universe_dir: Path, owner: str, agent_id: str):
    if not agent_id or agent_id == "main":
        yield
        return
    from tinyassets.addressed_agents import resolve
    from tinyassets.provider_assignment import provider_assignment_admission

    # Retirement holds exclusive admission through its activity fence. The
    # validation and insert must be on the same side of that fence.
    with provider_assignment_admission().shared(universe_dir):
        resolve(universe_dir.parent, universe_id=universe_dir.name,
                owner=owner, agent_id=agent_id)
        yield


def create(universe_dir: Path, *, owner_principal: str, title: str, brief: str,
           origin_kind: str, origin_ref: str = "", agent_id: str = "main",
           approval_id: str = "", continuation_only: bool = False) -> dict:
    """A new activity, queued (``scheduled``). The id is minted here, never taken.

    ``owner_principal`` must be derived server-side from the authenticated
    creator (a served turn's verified actor, the owner door's identity, an
    automation row's owner), never from tool arguments. A ``schedule`` origin
    is idempotent on ``origin_ref``: re-firing the same attempt returns the
    activity it already made.
    """
    owner = str(owner_principal or "").strip()
    if not owner:
        raise ActivityRefused("An activity needs its authenticated owner.",
                              kind="authentication_required")
    if origin_kind not in ORIGINS:
        raise ActivityRefused(f"Unknown origin {origin_kind!r}.")
    title = _one_line(title, MAX_TITLE)
    if not title:
        raise ActivityRefused("Give the activity a short title.")
    brief = str(brief or "").strip()
    if not brief:
        raise ActivityRefused("Say what the activity should do.")
    if len(brief.encode("utf-8")) > MAX_BRIEF:
        raise ActivityRefused(
            f"The task text is over {MAX_BRIEF // 1024} KiB; put the detail in a file "
            "in your workspace and name the file in the task.")
    origin_ref = _one_line(origin_ref, MAX_REASON)
    if origin_kind == "schedule" and not origin_ref:
        raise ActivityRefused("A scheduled activity names the firing it came from.")
    activity_id = "act_" + secrets.token_hex(8)
    now = time.time()
    record = {
        "activity_id": activity_id, "agent_id": agent_id or "main",
        "parent_activity_id": "", "session_key": f"activity:{activity_id}",
        "owner_principal": owner, "title": title, "brief": brief,
        "origin_kind": origin_kind, "origin_ref": origin_ref, "approval_id": approval_id,
        "status": SCHEDULED, "outcome": "", "waiting_reason": "", "waiting_request_id": "",
        "result_summary": "", "result_path": "", "last_tool_seq": 0, "runner_token": "",
        "runner_generation": 0, "claimed_at": 0.0, "retiring_token": "",
        "start_failures": 0, "revision": 1, "created_at": now, "updated_at": now,
        "finished_at": 0.0,
    }
    with (_agent_creation_admission(universe_dir, owner, agent_id),
          _txn(universe_dir, create=True) as conn):
        if origin_kind == "schedule":
            row = conn.execute(
                f"SELECT {', '.join(_COLUMNS)} FROM activities "
                "WHERE origin_kind = 'schedule' AND origin_ref = ?", (origin_ref,),
            ).fetchone()
            if row:
                return _row(row)
        conn.execute(
            f"INSERT INTO activities ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join('?' for _ in _COLUMNS)})",
            tuple(record[c] for c in _COLUMNS),
        )
        if continuation_only:
            conn.execute("UPDATE activities SET continuation_only=1,task_expires_at=?,"
                         "status='waiting_on_you' WHERE activity_id=?",
                         (now + 86400, activity_id))
        _event(conn, record, "created")
    return record


@_when_absent(lambda: None)
def get(universe_dir: Path, activity_id: str) -> dict | None:
    if not _ID.match(str(activity_id or "")):
        return None
    with closing(_connect(universe_dir)) as conn:
        return _get(conn, activity_id)


def _encode_cursor(created_at: float, activity_id: str) -> str:
    raw = json.dumps([created_at, activity_id]).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _decode_cursor(cursor: str) -> tuple[float, str]:
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        updated_at, activity_id = json.loads(base64.urlsafe_b64decode(padded))
        return float(updated_at), str(activity_id)
    except (ValueError, TypeError) as exc:
        raise ActivityRefused("That page cursor is not one this list gave out.",
                              kind="invalid_cursor") from exc


@_when_absent(lambda: {"activities": [], "next_cursor": None})
def list_page(universe_dir: Path, *, status: str | None = None, cursor: str | None = None,
              limit: int = PAGE) -> dict:
    """One page of activities, newest first, and the cursor to the next.

    Ordered by creation, which never changes, so following ``next_cursor``
    reaches every activity that existed when the walk began even while others
    change status; nothing is cut for size.
    """
    if status is not None and status not in STATUSES:
        raise ActivityRefused(f"Unknown status {status!r}.")
    limit = max(1, min(int(limit or PAGE), PAGE))
    where, params = [], []
    if status is not None:
        where.append("status = ?")
        params.append(status)
    if cursor:
        updated_at, last_id = _decode_cursor(cursor)
        where.append("(created_at < ? OR (created_at = ? AND activity_id < ?))")
        params += [updated_at, updated_at, last_id]
    sql = f"SELECT {', '.join(_COLUMNS)} FROM activities"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY created_at DESC, activity_id DESC LIMIT ?"
    with closing(_connect(universe_dir)) as conn:
        rows = [_row(r) for r in conn.execute(sql, (*params, limit + 1))]
    more = len(rows) > limit
    rows = rows[:limit]
    next_cursor = (_encode_cursor(rows[-1]["created_at"], rows[-1]["activity_id"])
                   if more else None)
    return {"activities": rows, "next_cursor": next_cursor}


@_when_absent(_not_found)
def transition(universe_dir: Path, activity_id: str, to: str, *,
               generation: int | None = None, expect_revision: int | None = None,
               outcome: str = "", waiting_reason: str = "", waiting_request_id: str = "",
               result_summary: str | None = None, event: str | None = None) -> dict:
    """Move an activity to ``to`` if that move is allowed; the record after.

    A runner passes its ``generation`` (refused once superseded); the owner
    door passes ``expect_revision``. ``event`` overrides the status line's
    kind (``stopped`` for a stop, which lands in ``completed``). Leaving
    ``in_progress`` clears the runner claim.
    """
    if to not in STATUSES or to == IN_PROGRESS:
        raise ActivityRefused(f"An activity cannot be moved to {to!r}; a runner claims it.",
                              kind="invalid_transition")
    with _txn(universe_dir) as conn:
        record = _get(conn, activity_id)
        if record is None:
            raise ActivityRefused("No such activity.", kind="not_found")
        if generation is not None and (record["status"] != IN_PROGRESS
                                       or record["runner_generation"] != generation):
            raise ActivityRefused("This runner no longer holds the activity.",
                                  kind="superseded")
        if expect_revision is not None and record["revision"] != expect_revision:
            raise ActivityRefused("The activity changed since you loaded it; "
                                  "reload and try again.", kind="revision_conflict")
        if to not in _MOVES[record["status"]]:
            raise ActivityRefused(
                f"An activity that is {record['status']} cannot become {to}.",
                kind="invalid_transition")
        waiting = to == WAITING_ON_YOU
        changes = {
            "status": to, "updated_at": _bump(record), "revision": record["revision"] + 1,
            "waiting_reason": _one_line(waiting_reason, MAX_REASON) if waiting else "",
            "waiting_request_id": _one_line(waiting_request_id, 80) if waiting else "",
            "runner_token": "",
            # The run leaving is kept as retiring until it has actually ended,
            # so a resume never starts a second run beside a cancelled one.
            "retiring_token": record["runner_token"] or record["retiring_token"],
        }
        if outcome:
            changes["outcome"] = _one_line(outcome, 80)
        if result_summary is not None:
            changes["result_summary"] = str(result_summary)[:MAX_SUMMARY]
        if to in TERMINAL:
            changes["finished_at"] = time.time()
        conn.execute(
            f"UPDATE activities SET {', '.join(f'{k} = ?' for k in changes)} "
            "WHERE activity_id = ?",
            (*changes.values(), activity_id),
        )
        record.update(changes)
        _event(conn, record, event or to,
               record["result_summary"] if to == FAILED else changes["waiting_reason"])
    return record


@_when_absent(lambda: False)
def answered(universe_dir: Path, activity_id: str, request_id: str) -> bool:
    """The owner answered the request an activity waits on: queue it again.

    Only the request it is waiting on moves it; a stale or repeated answer
    changes nothing.
    """
    with _txn(universe_dir) as conn:
        record = _get(conn, activity_id)
        if (record is None or record["status"] != WAITING_ON_YOU
                or not request_id or record["waiting_request_id"] != request_id):
            return False
        changes = {"status": SCHEDULED, "waiting_reason": "", "waiting_request_id": "",
                   "updated_at": _bump(record), "revision": record["revision"] + 1}
        conn.execute(
            f"UPDATE activities SET {', '.join(f'{k} = ?' for k in changes)} "
            "WHERE activity_id = ?", (*changes.values(), activity_id),
        )
        record.update(changes)
        _event(conn, record, SCHEDULED)
    return True


def _claimable(record: dict, replaceable: Callable[[str], bool], now: float) -> bool:
    status = record["status"]
    retiring = record["retiring_token"]
    if retiring and not replaceable(retiring):
        return False  # its previous run has not ended yet
    if status == SCHEDULED:
        failures = record["start_failures"]
        return not failures or now >= record["updated_at"] + START_BACKOFF_S * 2 ** (failures - 1)
    if status != IN_PROGRESS:
        return False
    token = record["runner_token"]
    if token:
        return replaceable(token)
    return now >= record["claimed_at"] + UNBOUND_GRACE_S


@_when_absent(lambda: None)
def claim(universe_dir: Path, activity_id: str, *,
          replaceable: Callable[[str], bool], now: float | None = None) -> int | None:
    """Claim an activity for a new run; the new generation, or None.

    Step 1 of reserve, bind, release (design D4). Claimable: a queued activity
    (after its start backoff) whose previous run has ended; an ``in_progress``
    one whose run ended or was interrupted (``replaceable``); or one whose
    claim never bound a run within ``UNBOUND_GRACE_S`` (its dispatcher died).
    A live run is never replaced, and a claim still binding is never raced.
    """
    now = time.time() if now is None else now
    with _txn(universe_dir) as conn:
        record = _get(conn, activity_id)
        if record is None or not _claimable(record, replaceable, now):
            return None
        resumed = record["status"] == IN_PROGRESS
        generation = record["runner_generation"] + 1
        conn.execute(
            "UPDATE activities SET status = ?, runner_token = '', retiring_token = '', "
            "runner_generation = ?, claimed_at = ?, revision = revision + 1, updated_at = ? "
            "WHERE activity_id = ?",
            (IN_PROGRESS, generation, now, _bump(record), activity_id),
        )
        _event(conn, record, "resumed" if resumed and record["last_tool_seq"] else IN_PROGRESS)
    return generation


@_when_absent(lambda: None)
def note_start_failure(universe_dir: Path, activity_id: str, generation: int,
                       reason: str) -> str | None:
    """A claimed run would not start: queue again with backoff, or fail after
    ``MAX_START_FAILURES``. The status it landed in, or None if superseded."""
    with _txn(universe_dir) as conn:
        record = _get(conn, activity_id)
        if (record is None or record["status"] != IN_PROGRESS
                or record["runner_generation"] != generation or record["runner_token"]):
            return None
        failures = record["start_failures"] + 1
        to = FAILED if failures >= MAX_START_FAILURES else SCHEDULED
        changes = {"status": to, "start_failures": failures, "updated_at": _bump(record),
                   "revision": record["revision"] + 1}
        if to == FAILED:
            changes.update(outcome="failed:run_refused", finished_at=time.time())
        conn.execute(
            f"UPDATE activities SET {', '.join(f'{k} = ?' for k in changes)} "
            "WHERE activity_id = ?", (*changes.values(), activity_id),
        )
        record.update(changes)
        _event(conn, record, to, _one_line(reason, 120))
    return to


@_when_absent(lambda: False)
def bind_run(universe_dir: Path, activity_id: str, generation: int, run_id: str) -> bool:
    """Step 3: name the reserved run on the record, under the claim's generation."""
    if not run_id:
        raise ValueError("bind a reserved run id")
    with closing(_connect(universe_dir)) as conn:
        cur = conn.execute(
            "UPDATE activities SET runner_token = ? WHERE activity_id = ? AND status = ? "
            "AND runner_generation = ? AND runner_token = ''",
            (run_id, activity_id, IN_PROGRESS, int(generation)),
        )
        return cur.rowcount == 1


@_when_absent(lambda: None)
def linked_generation(universe_dir: Path, activity_id: str, run_id: str) -> int | None:
    """The start barrier (step 4): the generation under which the record names
    ``run_id``, or None -- an unlinked run must not execute."""
    if not _ID.match(str(activity_id or "")) or not run_id:
        return None
    with closing(_connect(universe_dir)) as conn:
        row = conn.execute(
            "SELECT runner_generation FROM activities WHERE activity_id = ? AND status = ? "
            "AND runner_token = ?", (activity_id, IN_PROGRESS, run_id),
        ).fetchone()
    return int(row[0]) if row else None


@_when_absent(lambda: False)
def note_progress(universe_dir: Path, activity_id: str, generation: int, *,
                  last_tool_seq: int | None = None, result_summary: str | None = None) -> bool:
    """Record a completed tool call or the result so far; False once superseded."""
    sets, params = [], []
    if last_tool_seq is not None:
        sets.append("last_tool_seq = MAX(last_tool_seq, ?)")
        params.append(int(last_tool_seq))
    if result_summary is not None:
        sets.append("result_summary = ?")
        params.append(str(result_summary)[:MAX_SUMMARY])
    with closing(_connect(universe_dir)) as conn:
        cur = conn.execute(
            f"UPDATE activities SET {', '.join(sets) or 'activity_id = activity_id'} "
            "WHERE activity_id = ? AND status = ? AND runner_generation = ?",
            (*params, activity_id, IN_PROGRESS, int(generation)),
        )
        return cur.rowcount == 1


def holds(universe_dir: Path, activity_id: str, generation: int, *, run_id: str = "") -> bool:
    """Whether this generation still runs the activity (checked at tool boundaries)."""
    record = get(universe_dir, activity_id)
    return bool(record and record["status"] == IN_PROGRESS
                and record["runner_generation"] == generation
                and (not run_id or record["runner_token"] == run_id))


@_when_absent(lambda: None)
def note_waiting_for_seat(universe_dir: Path, activity_id: str) -> None:
    """One line per wait: repeated polls while still queued add nothing."""
    with _txn(universe_dir) as conn:
        record = _get(conn, activity_id)
        if record is None or record["status"] != SCHEDULED:
            return
        last = conn.execute(
            "SELECT kind FROM activity_events WHERE activity_id = ? ORDER BY seq DESC LIMIT 1",
            (activity_id,),
        ).fetchone()
        if last is None or last[0] != "waiting_for_seat":
            _event(conn, record, "waiting_for_seat")


@_when_absent(lambda: None)
def activity_for_run(universe_dir: Path, run_id: str) -> dict | None:
    """The in-progress activity whose record names ``run_id``, or None.

    Linkage comes from the record alone, never from the run's inputs: a run the
    agent starts itself, whatever it claims to be, finds nothing here.
    """
    if not run_id:
        return None
    with closing(_connect(universe_dir)) as conn:
        row = conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM activities WHERE status = ? "
            "AND runner_token = ?", (IN_PROGRESS, run_id),
        ).fetchone()
    return _row(row) if row else None


@_when_absent(list)
def needing_a_runner(universe_dir: Path, *, replaceable: Callable[[str], bool],
                     now: float | None = None) -> list[str]:
    """Activities a dispatcher should act on now, oldest first: claimable ones
    and running ones whose bound run ended (to settle)."""
    now = time.time() if now is None else now
    with closing(_connect(universe_dir)) as conn:
        rows = [_row(r) for r in conn.execute(
            f"SELECT {', '.join(_COLUMNS)} FROM activities WHERE status IN (?, ?) "
            "ORDER BY created_at, activity_id", (SCHEDULED, IN_PROGRESS))]
    return [r["activity_id"] for r in rows if _claimable(r, replaceable, now)]


@_when_absent(lambda: False)
def wait_on(universe_dir: Path, activity_id: str, request_id: str, reason: str = "") -> bool:
    """The yield (design D4): the running activity raised an owner request and
    now waits on it, holding no run. False when it is not running."""
    if not request_id:
        return False
    with _txn(universe_dir) as conn:
        record = _get(conn, activity_id)
        if record is None or record["status"] != IN_PROGRESS:
            return False
        changes = {"status": WAITING_ON_YOU, "waiting_request_id": _one_line(request_id, 80),
                   "waiting_reason": _one_line(reason, MAX_REASON),
                   "retiring_token": record["runner_token"] or record["retiring_token"],
                   "runner_token": "", "updated_at": _bump(record),
                   "revision": record["revision"] + 1}
        conn.execute(
            f"UPDATE activities SET {', '.join(f'{k} = ?' for k in changes)} "
            "WHERE activity_id = ?", (*changes.values(), activity_id),
        )
        record.update(changes)
        _event(conn, record, WAITING_ON_YOU, changes["waiting_reason"])
    return True


@_when_absent(lambda: None)
def answered_request(universe_dir: Path, request_id: str) -> str | None:
    """The owner resolved ``request_id``: re-queue the activity waiting on it.
    Returns that activity's id, or None when none waits on it."""
    if not request_id:
        return None
    with closing(_connect(universe_dir)) as conn:
        row = conn.execute(
            "SELECT activity_id FROM activities WHERE status = ? AND waiting_request_id = ?",
            (WAITING_ON_YOU, request_id)).fetchone()
    if row is None:
        return None
    return row[0] if answered(universe_dir, row[0], request_id) else None


@_when_absent(lambda: 0)
def reconcile_answers(universe_dir: Path) -> int:
    """Repair missed answer wakes from the two durable stores, idempotently.

    The answer can commit before the activity starts waiting, or its immediate
    notification can fail. Read requests outside the activity transaction;
    ``answered`` then compares the exact waiting request again under the lock.
    A read failure leaves the activity waiting for the next dispatcher tick.
    """
    from tinyassets.storage.pending_requests import get_request

    with closing(_connect(universe_dir)) as conn:
        waiting = conn.execute(
            "SELECT activity_id, waiting_request_id FROM activities WHERE status = ?",
            (WAITING_ON_YOU,),
        ).fetchall()
    requeued = 0
    for activity_id, request_id in waiting:
        request = get_request(universe_dir, request_id) if request_id else None
        if request is not None and request.get("status") in {"answered", "dismissed"}:
            requeued += int(answered(universe_dir, activity_id, request_id))
    return requeued


@_when_absent(lambda: False)
def has_in_progress(universe_dir: Path, agent_id: str = "main") -> bool:
    """Whether one of ``agent_id``'s activities is running now (platform state
    only; the proactive scheduler's "no in-progress activity" rule)."""
    with closing(_connect(universe_dir)) as conn:
        return conn.execute(
            "SELECT 1 FROM activities WHERE agent_id = ? AND status = ? LIMIT 1",
            (agent_id, IN_PROGRESS)).fetchone() is not None


@_when_absent(set)
def runner_tokens(universe_dir: Path) -> set[str]:
    """Every run a running activity names."""
    with closing(_connect(universe_dir)) as conn:
        return {row[0] for row in conn.execute(
            "SELECT runner_token FROM activities WHERE status = ? AND runner_token != ''",
            (IN_PROGRESS,))}


@_when_absent(list)
def undelivered_lines(universe_dir: Path, *, agent_id: str = "main",
                      limit: int = PAGE) -> list[dict]:
    """Status lines not yet shown to the agent's main session, oldest first."""
    with closing(_connect(universe_dir)) as conn:
        rows = conn.execute(
            "SELECT e.activity_id, e.seq, e.ts, e.kind, e.line FROM activity_events e "
            "JOIN activities a ON a.activity_id = e.activity_id "
            "WHERE e.delivered = 0 AND a.agent_id = ? ORDER BY e.ts, e.activity_id, e.seq "
            "LIMIT ?",
            (agent_id, max(1, int(limit))),
        ).fetchall()
    return [dict(zip(("activity_id", "seq", "ts", "kind", "line"), r, strict=True))
            for r in rows]


@_when_absent(lambda: None)
def mark_delivered(universe_dir: Path, lines: list[dict]) -> None:
    if not lines:
        return
    with closing(_connect(universe_dir)) as conn:
        conn.executemany(
            "UPDATE activity_events SET delivered = 1 WHERE activity_id = ? AND seq = ?",
            [(line["activity_id"], line["seq"]) for line in lines],
        )


@_when_absent(lambda: {"events": [], "next_after": None})
def events_page(universe_dir: Path, activity_id: str, *, after: int = 0,
                limit: int = PAGE) -> dict:
    """One page of an activity's status lines, oldest first, and the next cursor."""
    limit = max(1, min(int(limit or PAGE), PAGE))
    with closing(_connect(universe_dir)) as conn:
        rows = conn.execute(
            "SELECT seq, ts, kind, line FROM activity_events WHERE activity_id = ? "
            "AND seq > ? ORDER BY seq LIMIT ?", (activity_id, int(after), limit + 1),
        ).fetchall()
    events = [dict(zip(("seq", "ts", "kind", "line"), r, strict=True)) for r in rows[:limit]]
    return {"events": events,
            "next_after": events[-1]["seq"] if len(rows) > limit else None}


@_when_absent(list)
def fence_agent(universe_dir: Path, *, owner: str, agent_id: str) -> list[str]:
    """Stop only this owner's retired agent, retaining its activity evidence."""
    now = time.time()
    with _txn(universe_dir) as conn:
        cursor = conn.execute(
            "SELECT * FROM activities "
            "WHERE owner_principal=? AND agent_id=? AND status NOT IN (?, ?)",
            (owner, agent_id, COMPLETED, FAILED),
        )
        columns = [column[0] for column in cursor.description]
        records = [dict(zip(columns, row)) for row in cursor.fetchall()]
        runs = [row[key] for row in records for key in ("runner_token", "retiring_token")
                if row[key]]
        conn.execute(
            "UPDATE activities SET status=?, outcome='agent retired', "
            "retiring_token=CASE WHEN runner_token!='' THEN runner_token ELSE retiring_token END, "
            "runner_token='', runner_generation=runner_generation+1, revision=revision+1, "
            "updated_at=?, finished_at=? WHERE owner_principal=? AND agent_id=? "
            "AND status NOT IN (?, ?)",
            (COMPLETED, now, now, owner, agent_id, COMPLETED, FAILED),
        )
        for row in records:
            _event(conn, dict(row), "stopped", "agent retired")
    return runs


@_when_absent(list)
def fence_all(universe_dir: Path, *, outcome: str) -> list[str]:
    """Fail every unfinished activity and supersede its runner (account deletion).

    Runs before the store is removed, so a runner still alive cannot act
    through a stale generation. Returns the runs still named, current or
    retiring, for the caller to cancel.
    """
    now = time.time()
    with _txn(universe_dir) as conn:
        runs = [token for row in conn.execute(
            "SELECT runner_token, retiring_token FROM activities") for token in row if token]
        conn.execute(
            "UPDATE activities SET status = ?, outcome = ?, "
            "retiring_token = CASE WHEN runner_token != '' THEN runner_token "
            "ELSE retiring_token END, runner_token = '', "
            "runner_generation = runner_generation + 1, revision = revision + 1, "
            "updated_at = ?, finished_at = ? WHERE status NOT IN (?, ?)",
            (FAILED, _one_line(outcome, 80), now, now, COMPLETED, FAILED),
        )
    return runs
