"""Owner messages that steer a running turn (harness S2).

Founder, 2026-10-01: the agent should work like Claude Code, where a message
typed while it works reaches it at the next tool boundary instead of waiting for
the whole turn to end (design #4172, Appendix A.3). Before this, the app held a
message sent mid-turn in the browser, marked "Queued -- your universe sees this
when its current turn ends", and the agent never heard it until it was done.

How a steering message travels:

* A served turn of the owner's thread OPENS itself here under its live id
  (``turn_interrupt.LiveTurn.live_id``) and every launch names that id on its
  engine route, beside the session (``engine_steering.route_with_session``).
* The owner's app posts a line while the turn runs. It is admitted only while a
  turn of that thread is open, and it is bound to that turn, in one transaction,
  so a line can never land after the turn has settled and wait unseen.
* The universe's engine tools run in a separate per-universe process that also
  serves the universe's own agent nodes. It hands a bound line only to a tool
  result of the same session AND the same turn, and within a per-result budget.
* When the turn ends it settles: lines its agent received are returned to be
  recorded between the message and the reply; lines it never received become
  CARRYOVER, kept here until a later turn takes them, so a page that is closed
  or reloaded before it re-sends them loses nothing. The next served turn folds
  any carryover its own message does not already repeat into that message.

The store sits beside the session records in the data root's
``.agent-sessions/<universe>/``, outside every universe folder, so no process
the universe runs can plant or read a message here.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Iterable
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from tinyassets import agent_sessions

#: One message, at most. Longer text is refused, not cut.
MAX_STEER_CHARS = 16_000
#: Messages waiting for one session (bound and carried over), at most.
MAX_PENDING = 20
#: Characters handed to one tool result. More waits for the next result; a
#: single message larger than this still goes alone, never cut.
DELIVERY_BUDGET_CHARS = 6_000

_FILE = "steering.db"
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS steer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    live_id TEXT,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL)""",
    """CREATE TABLE IF NOT EXISTS open_turns (
    session_key TEXT NOT NULL,
    live_id TEXT NOT NULL,
    opened_at REAL NOT NULL,
    PRIMARY KEY(session_key, live_id))""",
)


class SteeringRefused(ValueError):
    """The message cannot be queued: empty, too long, or too many waiting."""


@dataclass(frozen=True, slots=True)
class Steer:
    id: int
    text: str
    created_at: float
    delivered_at: float | None = None


def _connect(universe_dir: Path) -> sqlite3.Connection:
    path = agent_sessions._records_dir(Path(universe_dir)) / _FILE
    conn = sqlite3.connect(path, timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 10000")
    for statement in _SCHEMA:
        conn.execute(statement)
    return conn


def _key(session_key: str) -> str:
    key = str(session_key or "").strip()
    if not key:
        raise ValueError("a steering message needs a session")
    return key


def _row(row) -> Steer:
    return Steer(int(row[0]), row[1], float(row[2]),
                 float(row[3]) if row[3] is not None else None)


def open_turn(universe_dir: Path, session_key: str, live_id: str,
              *, live_ids: Iterable[str] = ()) -> None:
    """A served turn of ``session_key`` starts and may be steered.

    ``live_ids`` are the turns of this session still running in this process.
    Any other turn left open (a process that died mid-turn) is closed, and its
    unreceived lines become carryover rather than waiting for a settle that
    can never come.
    """
    key, live = _key(session_key), str(live_id or "").strip()
    if not live:
        raise ValueError("a steered turn needs its live id")
    keep = {str(item) for item in live_ids} | {live}
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        stale = [row[0] for row in conn.execute(
            "SELECT live_id FROM open_turns WHERE session_key = ?", (key,),
        ) if row[0] not in keep]
        for dead in stale:
            conn.execute("DELETE FROM open_turns WHERE session_key = ? AND live_id = ?",
                         (key, dead))
            conn.execute("DELETE FROM steer WHERE session_key = ? AND live_id = ? "
                         "AND delivered_at IS NOT NULL", (key, dead))
            conn.execute("UPDATE steer SET live_id = NULL WHERE session_key = ? "
                         "AND live_id = ?", (key, dead))
        conn.execute("INSERT OR REPLACE INTO open_turns VALUES (?, ?, ?)",
                     (key, live, time.time()))
        conn.execute("COMMIT")


def enqueue(universe_dir: Path, session_key: str, text: str) -> Steer | None:
    """Queue ``text`` for the open turn of ``session_key``; ``None`` if none is open.

    Admission and binding happen in one transaction with the turn's settle, so
    a line is either bound to a turn that will settle it or refused.
    """
    key = _key(session_key)
    body = str(text or "").strip()
    if not body:
        raise SteeringRefused("the message is empty")
    if len(body) > MAX_STEER_CHARS:
        raise SteeringRefused(f"the message is over {MAX_STEER_CHARS} characters")
    now = time.time()
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        turn = conn.execute(
            "SELECT live_id FROM open_turns WHERE session_key = ? "
            "ORDER BY opened_at DESC LIMIT 1", (key,),
        ).fetchone()
        if turn is None:
            conn.execute("ROLLBACK")
            return None
        waiting = conn.execute(
            "SELECT COUNT(*) FROM steer WHERE session_key = ?", (key,),
        ).fetchone()[0]
        if waiting >= MAX_PENDING:
            conn.execute("ROLLBACK")
            raise SteeringRefused(f"{MAX_PENDING} messages are already waiting")
        cursor = conn.execute(
            "INSERT INTO steer (session_key, live_id, text, created_at) VALUES (?, ?, ?, ?)",
            (key, turn[0], body, now),
        )
        conn.execute("COMMIT")
    return Steer(int(cursor.lastrowid), body, now)


def take(universe_dir: Path, session_key: str, live_id: str,
         *, budget: int = DELIVERY_BUDGET_CHARS) -> list[Steer]:
    """Lines bound to turn ``live_id`` not yet delivered, now marked delivered.

    Called by the engine for a tool result it is about to return: each line is
    handed over exactly once, in the order sent, at most ``budget`` characters
    per result (one larger line goes alone).
    """
    key, live = _key(session_key), str(live_id or "").strip()
    if not live:
        return []
    now = time.time()
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, text, created_at FROM steer WHERE session_key = ? "
            "AND live_id = ? AND delivered_at IS NULL ORDER BY id",
            (key, live),
        ).fetchall()
        chosen, used = [], 0
        for row in rows:
            if chosen and used + len(row[1]) > budget:
                break
            chosen.append(row)
            used += len(row[1])
        if chosen:
            conn.executemany("UPDATE steer SET delivered_at = ? WHERE id = ?",
                             [(now, row[0]) for row in chosen])
        conn.execute("COMMIT")
    return [Steer(int(row[0]), row[1], float(row[2]), now) for row in chosen]


def settle(universe_dir: Path, session_key: str, live_id: str
           ) -> tuple[list[Steer], list[Steer]]:
    """Turn ``live_id`` ends: ``(delivered, undelivered)``.

    The turn is closed to new lines in the same transaction. Delivered lines
    are removed (the caller records them); undelivered ones stay as carryover
    for a later turn and are also returned so the page can send them now.
    """
    key, live = _key(session_key), str(live_id or "").strip()
    if not live:
        return [], []
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("DELETE FROM open_turns WHERE session_key = ? AND live_id = ?",
                     (key, live))
        rows = conn.execute(
            "SELECT id, text, created_at, delivered_at FROM steer "
            "WHERE session_key = ? AND live_id = ? ORDER BY id", (key, live),
        ).fetchall()
        conn.execute("DELETE FROM steer WHERE session_key = ? AND live_id = ? "
                     "AND delivered_at IS NOT NULL", (key, live))
        conn.execute("UPDATE steer SET live_id = NULL WHERE session_key = ? "
                     "AND live_id = ?", (key, live))
        conn.execute("COMMIT")
    items = [_row(row) for row in rows]
    return ([i for i in items if i.delivered_at is not None],
            [i for i in items if i.delivered_at is None])


def take_carryover(universe_dir: Path, session_key: str, message: str) -> list[Steer]:
    """A new turn starts: carryover lines its own ``message`` does not repeat.

    Every carryover line is removed: the ones the page already re-sent are in
    ``message``, the rest are returned to be folded into it.
    """
    key = _key(session_key)
    paragraphs = {part.strip() for part in str(message or "").split("\n\n")}
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, text, created_at, delivered_at FROM steer "
            "WHERE session_key = ? AND live_id IS NULL ORDER BY id", (key,),
        ).fetchall()
        conn.execute("DELETE FROM steer WHERE session_key = ? AND live_id IS NULL", (key,))
        conn.execute("COMMIT")
    return [_row(row) for row in rows if row[1].strip() not in paragraphs]


def render(messages: list[Steer]) -> str:
    """The block appended to a tool result: mechanical, platform-authored."""
    lines = [
        f"[{len(messages)} new message{'s' if len(messages) != 1 else ''} from "
        "your founder, sent while you were working. Take it into account now; "
        "you do not need to stop unless it asks you to.]"
    ]
    for item in messages:
        stamp = time.strftime("%H:%M UTC", time.gmtime(item.created_at))
        lines.append(f"[{stamp}] {item.text}")
    return "\n".join(lines)
