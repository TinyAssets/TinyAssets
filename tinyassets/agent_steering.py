"""Active-turn observation and draining of pre-cutover steering records.

New owner messages enter only through owner_door.send. No producer or destructive
browser claim remains here. Existing steering records are retained for the in-box
turn's legacy drain; active-turn observations serve the page's working indicator.
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
    # The message a running turn is answering, for a page reloaded mid-turn.
    if "message" not in {row[1] for row in conn.execute("PRAGMA table_info(open_turns)")}:
        try:
            conn.execute("ALTER TABLE open_turns ADD COLUMN message TEXT NOT NULL DEFAULT ''")
        except sqlite3.OperationalError as exc:  # another process added it first
            if "duplicate column" not in str(exc):
                raise
    if "client_send_id" not in {r[1] for r in conn.execute("PRAGMA table_info(open_turns)")}:
        try:
            conn.execute(
                "ALTER TABLE open_turns ADD COLUMN client_send_id TEXT NOT NULL DEFAULT ''"
            )
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc):
                raise
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
              *, live_ids: Iterable[str] = (), message: str = "", client_send_id: str = "") -> None:
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
        conn.execute("INSERT OR REPLACE INTO open_turns (session_key, live_id, opened_at, "
                     "message, client_send_id) VALUES (?, ?, ?, ?, ?)",
                     (key, live, time.time(), str(message or "")[:MAX_STEER_CHARS], client_send_id))
        conn.execute("COMMIT")


@dataclass(frozen=True, slots=True)
class Pending:
    """A line the owner sent that no turn has handled yet, as the page shows it."""
    id: int
    text: str
    created_at: float
    state: str  # "steered": bound to the running turn; "held": the next turn takes it


def pending(universe_dir: Path, session_key: str) -> list[Pending]:
    """Every line of ``session_key`` no turn has handled yet, oldest first."""
    key = _key(session_key)
    with closing(_connect(universe_dir)) as conn:
        rows = conn.execute(
            "SELECT id, text, created_at, live_id FROM steer WHERE session_key = ? "
            "AND delivered_at IS NULL ORDER BY id", (key,),
        ).fetchall()
    return [Pending(int(r[0]), r[1], float(r[2]), "steered" if r[3] else "held")
            for r in rows]


def active(universe_dir: Path, session_key: str) -> dict | None:
    """The message the newest open turn of ``session_key`` is answering, so a
    page reloaded mid-turn can show it with the turn's working state."""
    key = _key(session_key)
    with closing(_connect(universe_dir)) as conn:
        row = conn.execute(
            "SELECT message, opened_at, client_send_id FROM open_turns WHERE session_key = ? "
            "ORDER BY opened_at DESC LIMIT 1", (key,),
        ).fetchone()
    if row is None or not row[0]:
        return None
    return {"text": row[0], "started_at": float(row[1]),
            **({"client_send_id": row[2]} if row[2] else {})}


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
