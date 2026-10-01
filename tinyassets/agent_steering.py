"""Owner messages that steer a running turn (harness S2).

Founder, 2026-10-01: the agent should work like Claude Code, where a message
typed while it works reaches it at the next tool boundary instead of waiting for
the whole turn to end (design #4172, Appendix A.3). Before this, the app held a
message sent mid-turn in the browser, marked "Queued -- your universe sees this
when its current turn ends", and the agent never heard it until it was done.

How a steering message travels, and why through this store:

* The owner's app posts it while a turn of their thread is running. The daemon
  checks the turn is live and records it here, keyed by the session it steers
  (``thread:principal:<owner>``).
* The universe's engine tools run in a separate per-universe process
  (``engine_mcp_http``) that serves the chat turn and the universe's own agent
  nodes alike. Each launch names its session in the engine route, so the engine
  hands a pending message to the next tool result of THAT session only. A
  background agent never takes a message meant for the owner's chat.
* When the turn ends, the daemon settles the queue: messages the agent
  received are recorded in the conversation in the order they were sent, and
  any it never received go back to the app to send as the next message. None is
  lost and none is said twice.

The store sits beside the session records in the data root's
``.agent-sessions/<universe>/``, outside every universe folder, so no process
the universe runs can plant or read a message here.
"""

from __future__ import annotations

import sqlite3
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from tinyassets import agent_sessions

#: One message, at most. Longer text is refused, not cut.
MAX_STEER_CHARS = 16_000
#: Messages waiting for one session, at most. More is refused, not dropped.
MAX_PENDING = 20

_FILE = "steering.db"
_SCHEMA = """CREATE TABLE IF NOT EXISTS steer (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    text TEXT NOT NULL,
    created_at REAL NOT NULL,
    delivered_at REAL)"""


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
    conn.execute(_SCHEMA)
    return conn


def _key(session_key: str) -> str:
    key = str(session_key or "").strip()
    if not key:
        raise ValueError("a steering message needs a session")
    return key


def enqueue(universe_dir: Path, session_key: str, text: str) -> Steer:
    """Queue ``text`` for the next tool result of ``session_key``."""
    key = _key(session_key)
    body = str(text or "").strip()
    if not body:
        raise SteeringRefused("the message is empty")
    if len(body) > MAX_STEER_CHARS:
        raise SteeringRefused(f"the message is over {MAX_STEER_CHARS} characters")
    now = time.time()
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        waiting = conn.execute(
            "SELECT COUNT(*) FROM steer WHERE session_key = ?", (key,),
        ).fetchone()[0]
        if waiting >= MAX_PENDING:
            conn.execute("ROLLBACK")
            raise SteeringRefused(f"{MAX_PENDING} messages are already waiting")
        cursor = conn.execute(
            "INSERT INTO steer (session_key, text, created_at) VALUES (?, ?, ?)",
            (key, body, now),
        )
        conn.execute("COMMIT")
    return Steer(int(cursor.lastrowid), body, now)


def take(universe_dir: Path, session_key: str) -> list[Steer]:
    """The messages not yet delivered to ``session_key``, now marked delivered.

    Called by the engine for a tool result it is about to return, so the agent
    receives each message exactly once, in the order the owner sent them.
    """
    key = _key(session_key)
    now = time.time()
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, text, created_at FROM steer "
            "WHERE session_key = ? AND delivered_at IS NULL ORDER BY id",
            (key,),
        ).fetchall()
        if rows:
            conn.executemany(
                "UPDATE steer SET delivered_at = ? WHERE id = ?",
                [(now, row[0]) for row in rows],
            )
        conn.execute("COMMIT")
    return [Steer(int(row[0]), row[1], float(row[2]), now) for row in rows]


def settle(universe_dir: Path, session_key: str) -> tuple[list[Steer], list[Steer]]:
    """End of turn: ``(delivered, undelivered)``, and the queue emptied.

    The caller records ``delivered`` in the conversation and hands
    ``undelivered`` back to the owner's app to send as the next message.
    """
    key = _key(session_key)
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT id, text, created_at, delivered_at FROM steer "
            "WHERE session_key = ? ORDER BY id",
            (key,),
        ).fetchall()
        conn.execute("DELETE FROM steer WHERE session_key = ?", (key,))
        conn.execute("COMMIT")
    delivered, undelivered = [], []
    for row in rows:
        item = Steer(int(row[0]), row[1], float(row[2]),
                     float(row[3]) if row[3] is not None else None)
        (delivered if item.delivered_at is not None else undelivered).append(item)
    return delivered, undelivered


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
