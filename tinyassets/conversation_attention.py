"""Read receipts and the unread count for one owner's retained conversation.

Receipts are observational, never consent. The caller verifies the universe and
principal before using this module.

**One reader per thread: the universe.** Receipts are keyed by the owner's thread
(``principal:<owner>``) in the owner's universe, and by nothing narrower. The
universe's background wakes and its chat turns are one self. They share no
narrower durable identity either: an agent node opens a fresh MCP session for
every served call, and a chat turn opens one per turn, so a per-session reader
reset to "all history unread" on every call. A turn's own prompt is not a
receipt. Only a served conversation read is, so answering a message in chat does
not take it out of the count.

**What counts.** The owner's own messages (speaker ``founder``) in that thread,
recorded at or after ``UNREAD_EPOCH``. The universe's replies and platform
notices are not messages to it. History from before the counter shipped is not
news; without the epoch every existing universe would open at hundreds unread.

**What marks a message read.** Only an exact message chunk that the tool
actually returned. A message is read once its returned chunks cover the whole
text. Catalogues, errors, capped results and partial chunks never clear one, and
a message that arrives during a read is not in that payload.

**Cost.** ``unread_count`` is cached against both stores' file signatures (size
and mtime of each database and its WAL), so a tool call that changes nothing
costs four ``stat`` calls. Only a call that returned a message body writes.
A signature is only trusted once every file in it is older than
``_RACY_GRACE_S``: the kernel stamps mtime from a coarse clock, so two writes in
one tick at the same size look unchanged, and a count cached between them stayed
stale (the unread badge stuck at 1 after the message was read).
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from contextlib import closing
from pathlib import Path

OWNER_SPEAKER = "founder"

#: 2026-09-30T00:00:00Z. Owner messages recorded before the counter shipped are
#: treated as seen.
UNREAD_EPOCH = 1_790_726_400.0

_TRANSCRIPT = ".conversation_memory.db"
_RECEIPTS = ".conversation_attention.db"
_SCHEMA = """CREATE TABLE IF NOT EXISTS reads (
    session_id TEXT NOT NULL, message_id INTEGER NOT NULL,
    total INTEGER NOT NULL, ranges TEXT NOT NULL, complete INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY(session_id, message_id))"""

#: A file modified this recently may be modified again within the same mtime
#: tick (or a coarse filesystem's 1-2 s granularity) without its signature
#: changing, so a count computed now is not cached.
_RACY_GRACE_S = 2.0

_cache_lock = threading.Lock()
_cache: dict[tuple[str, str], tuple[tuple, int]] = {}


def _path(root, name):
    root = Path(root).resolve()
    path = root / name
    if path.resolve() != path:
        raise PermissionError("conversation_store_outside_universe")
    return path


def _signature(path: Path):
    parts = []
    for candidate in (str(path), str(path) + "-wal"):
        try:
            stat = os.stat(candidate)
        except OSError:
            parts.append(None)
        else:
            parts.append((stat.st_size, stat.st_mtime_ns))
    return tuple(parts)


def _settled(signature, now_ns: int) -> bool:
    """Every file in ``signature`` is older than the racy-write grace window."""
    cutoff = now_ns - int(_RACY_GRACE_S * 1e9)
    return all(
        part is None or part[1] < cutoff
        for file_signature in signature[:2]
        for part in file_signature
    )


def _receipt(payload):
    """Only an exact, successful message chunk is evidence of a read."""
    if not isinstance(payload, dict) or payload.get("available") is not True:
        return None
    if payload.get("error") or payload.get("truncated"):
        return None
    ident, offset, total, chunk = (
        payload.get("field_name"), payload.get("offset"),
        payload.get("total_chars"), payload.get("chunk"),
    )
    if (not isinstance(ident, str) or not ident.isascii() or not ident.isdecimal()
            or len(ident) > 18 or int(ident) <= 0
            or type(offset) is not int or type(total) is not int
            or offset < 0 or total < 0 or not isinstance(chunk, str)):
        return None
    end = offset + len(chunk)
    if end > total or payload.get("offset_unit") != "unicode_code_points":
        return None
    if payload.get("next_offset") != (end if end < total else None):
        return None
    return int(ident), offset, end, total


def returned_page(text):
    """The conversation read payload inside a returned tool text, or None."""
    if not isinstance(text, str):
        return None
    try:
        document = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if isinstance(document, dict) and document.get("untrusted") is True:
        if document.get("source") != "conversation":
            return None
        document = document.get("content")
    return document if isinstance(document, dict) else None


def _owner_ids(transcript: Path, session_id: str, epoch: float) -> set[int]:
    # The transcript is read-only, including for old databases with no migrations.
    with closing(sqlite3.connect(
        transcript.as_uri() + "?mode=ro", uri=True, timeout=5.0,
    )) as history:
        rows = history.execute(
            "SELECT id FROM conversation_turns WHERE session_id = ? AND speaker = ? "
            "AND ts >= ?",
            (session_id, OWNER_SPEAKER, epoch),
        ).fetchall()
    return {row[0] for row in rows}


def acknowledge(root, session_id, page, *, epoch: float = UNREAD_EPOCH) -> None:
    """Record the coverage a returned message chunk proves. Writes nothing otherwise."""
    if not session_id:
        raise ValueError("conversation_session_required")
    got = _receipt(page)
    if got is None:
        return
    transcript = _path(root, _TRANSCRIPT)
    if not transcript.exists():
        return
    ident, start, end, total = got
    # Only the owner's own message in THIS thread can be acknowledged.
    if ident not in _owner_ids(transcript, session_id, epoch):
        return
    with closing(sqlite3.connect(_path(root, _RECEIPTS), timeout=5.0)) as conn:
        conn.execute(_SCHEMA)
        conn.execute("BEGIN IMMEDIATE")
        previous = conn.execute(
            "SELECT total, ranges FROM reads WHERE session_id=? AND message_id=?",
            (session_id, ident),
        ).fetchone()
        ranges = json.loads(previous[1]) if previous and previous[0] == total else []
        ranges.append([start, end])
        merged = []
        for lo, hi in sorted(ranges):
            if merged and lo <= merged[-1][1]:
                merged[-1][1] = max(merged[-1][1], hi)
            else:
                merged.append([lo, hi])
        conn.execute(
            "INSERT OR REPLACE INTO reads VALUES (?, ?, ?, ?, ?)",
            (session_id, ident, total, json.dumps(merged),
             int(merged == [[0, total]])),
        )
        conn.commit()


def unread_count(root, session_id, *, epoch: float = UNREAD_EPOCH) -> int | None:
    """The owner's messages in ``session_id`` not yet fully read, or None if unknown."""
    if not session_id:
        raise ValueError("conversation_session_required")
    transcript = _path(root, _TRANSCRIPT)
    receipts = _path(root, _RECEIPTS)
    if not transcript.exists():
        return 0  # no conversation yet: nothing was sent
    key = (str(transcript), session_id)
    now_ns = time.time_ns()
    signature = (_signature(transcript), _signature(receipts), epoch)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None and cached[0] == signature:
        return cached[1]
    owner = _owner_ids(transcript, session_id, epoch)
    read: set[int] = set()
    if owner and receipts.exists():
        with closing(sqlite3.connect(
            receipts.as_uri() + "?mode=ro", uri=True, timeout=5.0,
        )) as conn:
            try:
                read = {row[0] for row in conn.execute(
                    "SELECT message_id FROM reads WHERE session_id=? AND complete=1",
                    (session_id,),
                )}
            except sqlite3.OperationalError as exc:
                if "no such table" not in str(exc).lower():
                    raise
    count = len(owner - read)
    with _cache_lock:
        if _settled(signature, now_ns):
            _cache[key] = (signature, count)
        else:
            _cache.pop(key, None)
    return count
