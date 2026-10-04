"""What the universe agent's tools are doing, for its owner to watch (harness S4).

Founder, 2026-10-01: the app should let him see the agent work, the way Claude
Code shows each tool as it runs. Production on 2026-10-01 had 109 tool rows in
``agent_turn_tools``, all from the HTTP loop and none from a native CLI turn, so
nobody (the founder or the agent) could see which tools a turn had called
(design #4172 §3.2).

Every adapter's tools run through the one per-universe engine process, so that
is where each call is recorded: once when it starts and once when it ends, with
a one-line summary (the command, the path, the target) and, on failure, the
first line of the real cause. This is an observation log, never authority: no
code decides anything from it, and losing a row loses nothing but the view.

The store sits beside the session records in the data root's
``.agent-sessions/<universe>/``, outside every universe folder. Each session
keeps its latest :data:`KEEP_PER_SESSION` calls.
"""

from __future__ import annotations

import re
import shlex
import sqlite3
import time
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

from tinyassets import agent_sessions

#: Calls kept per session; older ones are dropped as new ones start.
KEEP_PER_SESSION = 200
#: Longest summary or error line stored.
MAX_LINE = 240

_FILE = "activity.db"
_SCHEMA = (
    """CREATE TABLE IF NOT EXISTS tool_calls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_key TEXT NOT NULL,
    tool TEXT NOT NULL,
    summary TEXT NOT NULL,
    started_at REAL NOT NULL,
    finished_at REAL,
    ok INTEGER,
    error TEXT)""",
    "CREATE INDEX IF NOT EXISTS tool_calls_session ON tool_calls(session_key, id)",
)


def _connect(universe_dir: Path) -> sqlite3.Connection:
    path = agent_sessions._records_dir(Path(universe_dir)) / _FILE
    conn = sqlite3.connect(path, timeout=5.0, isolation_level=None)
    conn.execute("PRAGMA busy_timeout = 5000")
    for statement in _SCHEMA:
        conn.execute(statement)
    return conn


def _line(text: object) -> str:
    one = " ".join(str(text or "").split())
    return one if len(one) <= MAX_LINE else one[: MAX_LINE - 1] + "…"


# What a summary may keep, parsed token by token rather than pattern-matched
# for secrets (a redactor that misses one shape leaks it): plain words, plain
# flags, and plain relative paths. A URL keeps only its scheme and host.
# Anything else -- a value after "=", userinfo, a long opaque string -- is
# shown as an ellipsis, never stored.
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9._-]{0,31}")
_FLAG = re.compile(r"--?[A-Za-z][A-Za-z0-9-]{0,31}")
_PATH = re.compile(r"[A-Za-z0-9._/-]{1,80}")
_OPAQUE = re.compile(r"[A-Za-z0-9_-]{24,}")
_HIDDEN = "\u2026"


def _safe_token(token: str) -> str:
    # Punctuation around a word ("refused:", "(403)") is kept around its verdict.
    core = token.strip("():,;!?\"'")
    if core and core != token:
        safe = _safe_token(core)
        if safe == _HIDDEN:
            return _HIDDEN
        start = token.index(core)
        return token[:start] + safe + token[start + len(core):]
    if "://" in token:
        try:
            parts = urlsplit(token)
        except ValueError:
            return _HIDDEN
        host = parts.hostname or ""
        return f"{parts.scheme}://{host}" if parts.scheme and _PATH.fullmatch(host) else _HIDDEN
    if _FLAG.fullmatch(token) or _WORD.fullmatch(token):
        return token
    if (_PATH.fullmatch(token) and ("/" in token or "." in token)
            and not any(_OPAQUE.fullmatch(part) for part in token.split("/"))):
        return token
    return _HIDDEN


def _safe_words(text: str, *, limit: int = 12) -> str:
    try:
        tokens = shlex.split(str(text or ""), posix=True)
    except ValueError:
        tokens = str(text or "").split()
    shown: list[str] = []
    for token in tokens[:limit]:
        safe = _safe_token(token)
        if not (safe == _HIDDEN and shown and shown[-1] == _HIDDEN):
            shown.append(safe)
    if len(tokens) > limit and shown[-1:] != [_HIDDEN]:
        shown.append(_HIDDEN)
    return _line(" ".join(shown))


def _command_summary(text: object) -> str:
    """A command as its program and, when it is a plain word, its subcommand
    ("git status", "npm test"), then an ellipsis for anything further.

    Nothing past those two is kept, however safe it looks: arguments are where
    paths, hosts, tokens and file contents go, and this line is shown live --
    in the owner's command center and to the UIs they build. The program is
    shown by its own name, never the path it was run from.
    """
    try:
        tokens = shlex.split(str(text or ""), posix=True)
    except ValueError:
        tokens = str(text or "").split()
    if not tokens:
        return ""
    def plain(word: str) -> bool:
        return bool(_WORD.fullmatch(word)) and not _OPAQUE.fullmatch(word)

    program = tokens[0].rsplit("/", 1)[-1]
    shown = [program if plain(program) else _HIDDEN]
    rest = tokens[1:]
    if rest and plain(rest[0]) and shown[0] != _HIDDEN:
        shown.append(rest[0])
        rest = rest[1:]
    if rest and shown[-1] != _HIDDEN:
        shown.append(_HIDDEN)
    return _line(" ".join(shown))


def summarize(tool: str, arguments: dict | None) -> str:
    """One safe line saying what a call does, from its own arguments."""
    args = arguments if isinstance(arguments, dict) else {}
    if tool == "bash":
        return _command_summary(args.get("command"))
    if tool in ("read", "write", "edit"):
        return _safe_words(args.get("path"), limit=1)
    for key in ("target", "action", "operation", "name"):
        value = args.get(key)
        if isinstance(value, str) and _WORD.fullmatch(value):
            return f"{key}={value}"
    return ""


def safe_error(text: str) -> str:
    """The first line of a failure's cause, made safe the same way."""
    first = str(text or "").strip().splitlines()
    return _safe_words(first[0], limit=24) if first else ""


def started(universe_dir: Path, session_key: str, tool: str, summary: str) -> int:
    """Record a call starting; returns its id."""
    now = time.time()
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "INSERT INTO tool_calls (session_key, tool, summary, started_at) "
            "VALUES (?, ?, ?, ?)",
            (session_key, _line(tool), _line(summary), now),
        )
        conn.execute(
            "DELETE FROM tool_calls WHERE session_key = ? AND id NOT IN ("
            "SELECT id FROM tool_calls WHERE session_key = ? ORDER BY id DESC LIMIT ?)",
            (session_key, session_key, KEEP_PER_SESSION),
        )
        conn.execute("COMMIT")
    return int(cursor.lastrowid)


def finished(universe_dir: Path, call_id: int, *, ok: bool, error: str = "") -> None:
    """Record how a call ended: ``ok``, or the first line of its real cause."""
    with closing(_connect(universe_dir)) as conn:
        conn.execute(
            "UPDATE tool_calls SET finished_at = ?, ok = ?, error = ? WHERE id = ?",
            (time.time(), 1 if ok else 0, safe_error(error), call_id),
        )


def recent(universe_dir: Path, session_key: str, *, limit: int = 5,
           since: float | None = None) -> list[dict]:
    """The latest calls of ``session_key``, newest first, for the owner's view."""
    now = time.time()
    query = ("SELECT tool, summary, started_at, finished_at, ok, error FROM tool_calls "
             "WHERE session_key = ?")
    params: list = [session_key]
    if since is not None:
        query += " AND started_at >= ?"
        params.append(float(since))
    query += " ORDER BY id DESC LIMIT ?"
    params.append(max(1, min(int(limit), 50)))
    path = agent_sessions._records_dir(Path(universe_dir)) / _FILE
    if not path.exists():
        return []
    with closing(_connect(universe_dir)) as conn:
        rows = conn.execute(query, params).fetchall()
    out = []
    for tool, summary, began, ended, ok, error in rows:
        item = {
            "tool": tool,
            "summary": summary,
            "state": "running" if ended is None else ("done" if ok else "failed"),
            "age_s": round(max(0.0, now - began), 1),
        }
        if ended is not None:
            item["took_s"] = round(max(0.0, ended - began), 1)
        if error:
            item["error"] = error
        out.append(item)
    return out
