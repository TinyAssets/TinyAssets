"""Per-universe goal subscriptions (Phase F).

A subscription is an opt-in on a Goal slug. The daemon reads
`<universe>/subscriptions.json` at each cycle to decide which
pool directories to scan (``<repo_root>/goal_pool/<goal_slug>/``).

File-locked via a **separate** sidecar
`<universe>/.subscriptions.json.lock` — deliberately distinct from
`.branch_tasks.json.lock` so subscription mutations don't contend
with dispatcher-cycle queue writes.

Fresh-install default: if the file is missing, the daemon behaves
as if ``["maintenance"]`` is subscribed (preflight §4.1 #5 +
invariant 10).
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

logger = logging.getLogger(__name__)

SUBSCRIPTIONS_FILENAME = "subscriptions.json"
# Dotted: a platform coordination file, never owner content
# (command_center_layout.PLATFORM_LOCK_NAMES).
LOCK_FILENAME = ".subscriptions.json.lock"

DEFAULT_GOALS: tuple[str, ...] = ("maintenance",)


def validate_goal_id(goal_id: str) -> None:
    """A goal is one portable path segment, never a relative or absolute path."""
    if not isinstance(goal_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", goal_id):
        raise ValueError("goal_id must be a single safe path segment")


def _subscriptions_path(universe_path: Path) -> Path:
    return Path(universe_path) / SUBSCRIPTIONS_FILENAME


@contextlib.contextmanager
def _file_lock(universe_path: Path) -> Iterator[None]:
    """Mirrors ``tinyassets.branch_tasks._file_lock`` but on a separate
    sidecar file. Cross-platform exclusive lock, opened link-free through
    ``universe_files.open_lock_file``.
    """
    from tinyassets.universe_files import open_lock_file

    Path(universe_path).mkdir(parents=True, exist_ok=True)
    fd = open_lock_file(universe_path, LOCK_FILENAME)
    try:
        if sys.platform == "win32":
            import msvcrt
            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
        else:
            import fcntl
            fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            yield
        finally:
            if sys.platform == "win32":
                import msvcrt
                try:
                    os.lseek(fd, 0, 0)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                except OSError:
                    pass
            else:
                import fcntl
                try:
                    fcntl.flock(fd, fcntl.LOCK_UN)
                except OSError:
                    pass
    finally:
        os.close(fd)


def _read_raw(universe_path: Path) -> list[str]:
    sp = _subscriptions_path(universe_path)
    if not sp.exists():
        # Fresh install: default maintenance subscription (invariant 10).
        return list(DEFAULT_GOALS)
    try:
        raw = sp.read_text(encoding="utf-8")
    except OSError as exc:
        raise RuntimeError(f"Failed to read {sp}: {exc}") from exc
    if not raw.strip():
        return list(DEFAULT_GOALS)
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Corrupt subscriptions at {sp}: {exc}") from exc
    if isinstance(data, dict):
        goals = data.get("goals", [])
    else:
        goals = data if isinstance(data, list) else []
    safe_goals = []
    for goal in goals:
        try:
            validate_goal_id(goal)
        except ValueError as exc:
            logger.warning("Ignoring unsafe subscribed goal %r: %s", goal, exc)
            continue
        safe_goals.append(goal)
    return safe_goals


def _write_raw(universe_path: Path, goals: list[str]) -> None:
    from tinyassets.universe_files import write_universe_file

    payload = {
        "goals": goals,
        "updated_at": datetime.now(timezone.utc).isoformat(),
    }
    write_universe_file(Path(universe_path), SUBSCRIPTIONS_FILENAME,
                        json.dumps(payload, indent=2, default=str).encode(), mode="replace")


def list_subscriptions(universe_path: Path) -> list[str]:
    """File-locked read. Returns the deduped goal list preserving
    insertion order (first occurrence wins).

    Missing file → ``["maintenance"]`` default (fresh-install behavior).
    """
    with _file_lock(universe_path):
        goals = _read_raw(universe_path)
    # Preserve order, dedupe.
    seen: set[str] = set()
    out: list[str] = []
    for g in goals:
        if g not in seen:
            seen.add(g)
            out.append(g)
    return out


def subscribe(universe_path: Path, goal_id: str) -> list[str]:
    """Append goal_id if absent. Idempotent. Returns updated list."""
    validate_goal_id(goal_id)
    with _file_lock(universe_path):
        goals = _read_raw(universe_path)
        if goal_id not in goals:
            goals.append(goal_id)
            _write_raw(universe_path, goals)
    return goals


def unsubscribe(universe_path: Path, goal_id: str) -> list[str]:
    """Remove goal_id if present. Silent on not-present. Returns updated list."""
    if not goal_id:
        raise ValueError("goal_id must be a non-empty string")
    with _file_lock(universe_path):
        goals = _read_raw(universe_path)
        if goal_id in goals:
            goals = [g for g in goals if g != goal_id]
            _write_raw(universe_path, goals)
    return goals
