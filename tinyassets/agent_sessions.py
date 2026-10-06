"""Long-lived agent sessions: one continuing native session per thread or agent node.

Founder, 2026-10-01: the universe agent should keep working like Claude Code,
not start over on every message. Before this, every served turn was a fresh
``--ephemeral`` process that saw a 20-message, 7,000-character text summary of
the conversation and none of its own earlier tool calls or results, and every
background wake rebuilt that same context from scratch (change
``universe-agent-harness``, design §2).

A *session* is keyed by what it continues: the founder's conversation thread
(``thread:<principal>``) or one agent node (``node:<node key>``). An adapter
that can resume declares ``native_resume`` and keeps its native session files
under :func:`native_store`; this module only remembers which native session a
key is on, so the next turn of the same key resumes it and sends just the new
input. No vendor is named here: the handle is opaque to the platform.

Two stores, kept apart on purpose:

* The **record** (which native session a key is on) and its lock live in the
  data root's ``.agent-sessions/<universe>/``, outside every universe folder.
  No jail of any kind binds it, so no process the universe runs can plant a link
  or a file the daemon then writes through or trusts (gpt-6-astra refute of
  S1: a workflow provider jail binds the universe read-write, ``.runtime``
  included).
* The **native session files** must be visible to the adapter, so they live in
  the universe's ``.runtime/agent-sessions/native/<adapter>/``. Every component
  is created and opened without following a link, the daemon only ever
  checks whether a file exists there (never following a link while walking),
  and the provider jail refuses a bind whose source resolves outside the
  universe.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import re
import tempfile
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path

from tinyassets.providers.provider_jail import PLATFORM_RUNTIME_DIR

logger = logging.getLogger(__name__)

#: Native session files, inside the universe's ``.runtime``.
SESSIONS_DIR = Path(PLATFORM_RUNTIME_DIR) / "agent-sessions"

#: Records and locks, in the data root beside the universes, never inside one.
RECORDS_DIR = ".agent-sessions"

_ADAPTER = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class AgentSessionRef:
    """Which session a turn continues, and what to send if it can.

    ``fresh_prompt_digest`` names the exact prompt the turn was built with; an
    adapter resumes only when the prompt it was handed is still that one, so a
    caller that rewrote the input (a continuation after a capacity retry, for
    instance) never has its rewrite silently replaced. ``resume_prompt`` is what
    a resumed native session receives instead: only what it has not seen.
    ``built_at`` is when the turn read the conversation, recorded so the next
    turn of this key knows which messages arrived after it.
    """

    universe_dir: Path
    key: str
    fresh_prompt_digest: str
    resume_prompt: str = field(repr=False)
    built_at: float = 0.0


def _records_dir(universe_dir: Path) -> Path:
    root = Path(universe_dir)
    path = root.parent / RECORDS_DIR / root.name
    path.mkdir(parents=True, exist_ok=True)
    return path


def _record_path(universe_dir: Path, key: str) -> Path:
    return _records_dir(universe_dir) / f"{digest(key)[:32]}.json"


def _nofollow_dirs(root: Path, parts: tuple[str, ...]) -> Path:
    """Create and walk ``root/parts`` one component at a time, never following a link.

    A component that already exists as a link (or as anything but a directory)
    refuses, so a process that can write the universe cannot redirect where the
    daemon creates directories. POSIX only: ``dir_fd`` and ``O_NOFOLLOW``.
    """
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    fd = os.open(root, flags)
    try:
        for part in parts:
            try:
                os.mkdir(part, 0o700, dir_fd=fd)
            except FileExistsError:
                pass
            child = os.open(part, flags, dir_fd=fd)
            os.close(fd)
            fd = child
    finally:
        os.close(fd)
    return root.joinpath(*parts)


def native_store(universe_dir: Path, adapter: str) -> Path:
    """The persistent directory an adapter keeps its native session files in."""
    if not _ADAPTER.match(adapter or ""):
        raise ValueError(f"invalid adapter name for a session store: {adapter!r}")
    return _nofollow_dirs(Path(universe_dir), (*SESSIONS_DIR.parts, "native", adapter))


def native_file_exists(store: Path, name_suffix: str) -> bool:
    """Whether a regular file ending in ``name_suffix`` is under ``store``.

    Walks without following links and never opens what it finds.
    """
    for _dirpath, _dirs, files in os.walk(store, followlinks=False):
        if any(name.endswith(name_suffix) for name in files):
            return True
    return False


def load(universe_dir: Path, key: str) -> dict | None:
    """The stored record for ``key``, or ``None`` when there is none or it is unreadable."""
    path = _record_path(universe_dir, key)
    try:
        if path.is_symlink():
            return None
        record = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        logger.warning("agent session record for %s is unreadable; starting fresh", key)
        return None
    if not isinstance(record, dict) or record.get("key") != key:
        return None
    return record


def save(ref: AgentSessionRef, *, adapter: str, model: str, handle: str, system: str) -> None:
    """Remember that ``ref.key`` now continues native session ``handle``."""
    if not handle:
        return
    path = _record_path(ref.universe_dir, ref.key)
    record = {
        "version": 1,
        "key": ref.key,
        "adapter": adapter,
        "model": model or "",
        "handle": handle,
        "system_digest": digest(system or ""),
        "consumed_at": ref.built_at or time.time(),
        "updated_at": time.time(),
    }
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=".record-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(record))
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def clear(ref: AgentSessionRef) -> None:
    with contextlib.suppress(FileNotFoundError):
        _record_path(ref.universe_dir, ref.key).unlink()


def consumed_at(universe_dir: Path, key: str) -> float | None:
    """When the session for ``key`` last read the conversation, if it exists."""
    record = load(universe_dir, key)
    if record is None:
        return None
    try:
        return float(record.get("consumed_at"))
    except (TypeError, ValueError):
        return None


def resumable(ref: AgentSessionRef | None, *, adapter: str, model: str,
              prompt: str) -> dict | None:
    """The record to resume for this launch, or ``None`` to start a new session.

    A record resumes only on the same adapter and model it was made on, and only
    when the launch is still sending the prompt the turn was built with.
    """
    if ref is None or digest(prompt) != ref.fresh_prompt_digest:
        return None
    record = load(ref.universe_dir, ref.key)
    if record is None or not record.get("handle"):
        return None
    if record.get("adapter") != adapter or (record.get("model") or "") != (model or ""):
        return None
    return record


@contextlib.contextmanager
def exclusive(ref: AgentSessionRef | None) -> Iterator[bool]:
    """Hold the session for one launch; yields ``False`` when another launch has it.

    Two processes resuming one native session at once would interleave its
    history, so the second one runs as a fresh, unrecorded session instead.
    """
    if ref is None:
        yield False
        return
    try:
        import fcntl
    except ImportError:  # Windows tray: native sessions are not resumed there.
        yield False
        return
    lock_path = _record_path(ref.universe_dir, ref.key).with_suffix(".lock")
    fd = os.open(lock_path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            logger.info("agent session %s is busy; this turn runs unrecorded", ref.key)
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        os.close(fd)


def unseen(history, since: float | None, *, speakers: frozenset[str] | None = None) -> list:
    """Messages from ``history`` after ``since`` (all of them when ``since`` is None).

    ``speakers`` narrows to those speakers; items are ``conversation_memory.Msg``
    or anything with ``speaker`` and ``ts``.
    """
    out = []
    for item in history or ():
        speaker = getattr(item, "speaker", None)
        ts = getattr(item, "ts", None)
        if isinstance(item, dict):
            speaker, ts = item.get("speaker"), item.get("ts")
        if speakers is not None and speaker not in speakers:
            continue
        try:
            when = float(ts) if ts is not None else None
        except (TypeError, ValueError):
            when = None
        if since is not None and (when is None or when <= since):
            continue
        out.append(item)
    return out
