"""Persistent conversation progress for each thread or agent node."""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

#: Progress records, in the data root beside the universes, never inside one.
RECORDS_DIR = ".agent-sessions"


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


def consumed_at(universe_dir: Path, key: str) -> float | None:
    """When the session for ``key`` last read the conversation, if it exists."""
    record = load(universe_dir, key)
    if record is None:
        return None
    try:
        return float(record.get("consumed_at"))
    except (TypeError, ValueError):
        return None


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
