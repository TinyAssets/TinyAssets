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
code grants authority from it. Failed turns retain these observations for the next agent.

The store sits beside the session records in the data root's
``.agent-sessions/<universe>/``, outside every universe folder. Reads are bounded;
recorded evidence is not pruned.
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

#: Longest summary or error line stored.
MAX_LINE = 240
# Transcript projections are bounded; the underlying observations stay verbatim.
EVIDENCE_CHARS = 2000
EVIDENCE_CALLS = 8
RESULT_PREVIEW_CHARS = 512

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
    columns = {row[1] for row in conn.execute('PRAGMA table_info(tool_calls)')}
    for name in ('turn_id', 'result_text'):
        if name not in columns:
            try:
                conn.execute(f"ALTER TABLE tool_calls ADD COLUMN {name} TEXT NOT NULL DEFAULT ''")
            except sqlite3.OperationalError as exc:
                if 'duplicate column' not in str(exc).lower():
                    raise
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


def started(universe_dir: Path, session_key: str, tool: str, summary: str, *, turn_id="") -> int:
    """Record a call starting; returns its id."""
    now = time.time()
    with closing(_connect(universe_dir)) as conn:
        conn.execute("BEGIN IMMEDIATE")
        cursor = conn.execute(
            "INSERT INTO tool_calls (session_key, tool, summary, started_at, turn_id) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_key, _line(tool), _line(summary), now, turn_id),
        )
        conn.execute("COMMIT")
    return int(cursor.lastrowid)


def finished(universe_dir: Path, call_id: int, *, ok: bool, error: str = "",
             result_text="") -> None:
    """Record how a call ended: ``ok``, or the first line of its real cause."""
    with closing(_connect(universe_dir)) as conn:
        conn.execute(
            "UPDATE tool_calls SET finished_at = ?, ok = ?, error = ?, result_text=? WHERE id = ?",
            (time.time(), 1 if ok else 0, safe_error(error), result_text, call_id),
        )


def turn_evidence(universe_dir: Path, session_key: str, turn_id: str) -> str:
    """Owner-scoped tool observations, not proof of upstream effect completion."""
    path = agent_sessions._records_dir(Path(universe_dir)) / _FILE
    if not path.exists():
        return ''
    with closing(sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)) as conn:
        if 'turn_id' not in {row[1] for row in conn.execute('PRAGMA table_info(tool_calls)')}:
            return ''
        count = conn.execute('SELECT count(*) FROM tool_calls WHERE session_key=? AND turn_id=?',
                             (session_key, turn_id)).fetchone()[0]
        rows = conn.execute(
            'SELECT id, tool, summary, finished_at, ok, error, '
            'substr(result_text,1,?), length(result_text) FROM tool_calls '
            'WHERE session_key=? AND turn_id=? ORDER BY id DESC LIMIT ?',
            (RESULT_PREVIEW_CHARS, session_key, turn_id, EVIDENCE_CALLS),
        ).fetchall()
    lines = [f'{count} stored tool observations. Latest observations (bounded excerpts):']
    for call_id, tool, summary, ended, ok, error, result, size in rows:
        state = 'outcome unknown' if ended is None else 'returned' if ok else 'reported an error'
        excerpt = f'Tool {call_id}: {tool} {summary}: {state}\n{result or error or ""}'
        if size > RESULT_PREVIEW_CHARS:
            excerpt += '\n[result excerpt; full observation retained]'
        lines.append(excerpt)
    text = '\n'.join(lines)
    return (text[:EVIDENCE_CHARS] + '\n[more observations retained]' if len(text) > EVIDENCE_CHARS
            else text) if count else ''


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
