"""Bounded owner-door harness history, outside every agent environment."""
from __future__ import annotations

import hashlib
import os
import sqlite3
import time
from contextlib import closing, contextmanager
from pathlib import Path

from tinyassets import agent_sessions, universe_files
from tinyassets import workspace_fs as fs

MAX_PRIOR_BYTES = 256 * 1024
_FILE = "history.db"
_SCHEMA = """CREATE TABLE IF NOT EXISTS changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    path TEXT NOT NULL, prior_content BLOB, prior_state TEXT NOT NULL,
    new_digest TEXT NOT NULL, who TEXT NOT NULL, changed_at REAL NOT NULL)"""


class HistoryConflict(ValueError):
    """The file no longer matches the change being undone."""


def digest(content: bytes | None) -> str:
    return "absent" if content is None else hashlib.sha256(content).hexdigest()


def _path(path: str) -> str:
    from tinyassets.universe_tools import AGENT_BRAIN_FILES

    parts = path.split("/")
    if (any(p in ("", ".", "..") for p in parts) or "\\" in path or "\x00" in path
            or not (path == "soul.md" or path in AGENT_BRAIN_FILES
                    or (parts[0] == "skills" and len(parts) > 1))):
        raise ValueError("not a harness file")
    return path


@contextmanager
def transaction(universe_dir: Path):
    """Serialize the owner door's read/modify/write operations per universe."""
    path = agent_sessions._records_dir(Path(universe_dir)) / _FILE
    with closing(sqlite3.connect(path, timeout=10)) as conn:
        conn.row_factory = sqlite3.Row
        conn.execute(_SCHEMA)
        with conn:
            conn.execute("BEGIN IMMEDIATE")
            yield conn


def _prior(universe_dir: Path, path: str) -> tuple[bytes | None, str]:
    # Only a bound refusal is skippable; links and other I/O errors fail closed.
    try:
        data = universe_files.read_universe_file(universe_dir, path, max_bytes=MAX_PRIOR_BYTES)
        return data, "present"
    except FileNotFoundError:
        return None, "absent"
    except OSError as exc:
        if (f"over the {MAX_PRIOR_BYTES}-byte bound" in str(exc)
                or f"over the {MAX_PRIOR_BYTES} bound" in str(exc)
                or f"grew past the {MAX_PRIOR_BYTES} bound" in str(exc)):
            return None, "too large"
        raise


def _replace(universe_dir: Path, path: str, content: bytes | None) -> None:
    """Publish one harness file through the daemon's only universe writer.

    ``universe_files`` already is fresh-inode-plus-rename inside a parent whose
    every component was opened following no link, so the owner door gets the
    same guarantee as every other write into a universe folder without a second
    implementation of it: a link planted at the name is replaced rather than
    written through, and a linked parent refuses loudly.

    Parents are never created -- a harness path writes only into a directory
    that already exists (``tests/test_harness_history.py``).

    The root is checked here, not there. ``universe_files`` resolves the
    universe dir before its no-follow walk, so that walk is link-free only
    BELOW the root and cannot refuse a link at or above the universe dir
    itself (``docs/concerns/2026-10-02-universe-files-resolves-its-root.md``).
    Checking it keeps the refusal this function had before it delegated.
    """
    if fs._POSIX:
        os.close(fs.open_dir_nofollow(universe_dir))
    if content is None:
        universe_files.unlink_universe_file(universe_dir, path)
        return
    universe_files.write_universe_file(universe_dir, path, content, make_parents=False)


def _write(conn, universe_dir: Path, path: str, content: bytes | None, who: str) -> int:
    path = _path(path)
    if who not in ("owner", "agent"):
        raise ValueError("unknown history actor")
    if path == "soul.md" and who != "owner":
        raise ValueError("soul.md requires the owner")
    prior, state = _prior(universe_dir, path)
    cursor = conn.execute(
        "INSERT INTO changes (path, prior_content, prior_state, new_digest, who, changed_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (path, prior, state, digest(content), who, time.time()),
    )
    _replace(Path(universe_dir), path, content)
    return int(cursor.lastrowid)


def write_file(universe_dir: Path, path: str, content: str, *, who: str = "owner") -> int:
    """Record and replace one harness file; return its change ID."""
    with transaction(universe_dir) as conn:
        return _write(conn, universe_dir, path, content.encode("utf-8"), who)


def list_history(universe_dir: Path, limit: int = 20) -> list[dict]:
    """Recent metadata only; never send prior file contents to the page."""
    with transaction(universe_dir) as conn:
        rows = conn.execute(
            "SELECT id, path, prior_state, new_digest, who, changed_at FROM changes "
            "ORDER BY id DESC LIMIT ?", (max(1, min(int(limit), 100)),),
        ).fetchall()
    return [dict(row) for row in rows]


def undo(universe_dir: Path, change_id: int) -> int:
    with transaction(universe_dir) as conn:
        row = conn.execute("SELECT * FROM changes WHERE id = ?", (change_id,)).fetchone()
        if row is None:
            raise ValueError("unknown history entry")
        path = _path(row["path"])
        if row["prior_state"] == "too large":
            raise HistoryConflict(f"{path}: prior content was too large to save")
        try:
            current = universe_files.read_universe_file(universe_dir, path)
        except FileNotFoundError:
            current = None
        except OSError as exc:
            raise HistoryConflict(f"{path}: conflict; current file cannot be read safely") from exc
        if digest(current) != row["new_digest"]:
            raise HistoryConflict(f"{path}: conflict; the file has changed since this entry")
        return _write(conn, universe_dir, path, row["prior_content"], "owner")
