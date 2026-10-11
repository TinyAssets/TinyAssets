"""Activity logging and phase tracking for scene nodes.

Writes timestamped lines to ``{universe_path}/activity.log`` and
updates ``current_phase`` in ``status.json`` so that external tools
(API, GPT, dashboard) can track what the daemon is doing inside
long-running subgraphs.
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.universe_files import write_data_path

logger = logging.getLogger(__name__)

# Guards all reads/writes to status.json across threads (heartbeat + nodes).
_status_lock = threading.Lock()


def activity_log(
    state: dict[str, Any], message: str, tag: str = "",
) -> None:
    """Append a timestamped line to the universe's activity.log.

    Safe to call from any node -- silently does nothing if
    ``_universe_path`` is missing or empty.

    Parameters
    ----------
    state : dict
        Universe state — must carry ``_universe_path``.
    message : str
        Free-form message body.
    tag : str, optional
        Machine-filterable category (e.g. ``"dispatch_guard"``,
        ``"overshoot_detected"``, ``"revert_gate"``). When provided, the
        line format becomes ``[TS] [TAG] MESSAGE`` so the
        ``get_recent_events(tag=...)`` MCP verb can grep it out. Empty
        string preserves the legacy ``[TS] MESSAGE`` shape (backward
        compat for un-tagged callers).
    """
    universe_path = state.get("_universe_path", "")
    if not universe_path:
        return
    try:
        log_path = Path(universe_path) / "activity.log"
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        body = f"[{tag}] {message}" if tag else message
        write_data_path(log_path, f"[{ts}] {body}\n", mode="append")
    except OSError as exc:
        logger.warning("Failed to append activity at %s: %s", log_path, exc)


def update_phase(state: dict[str, Any], phase: str) -> None:
    """Update current_phase in status.json so external tools see live progress.

    Called at node entry so status shows "orient", "plan", "draft", "commit",
    "worldbuild" during long-running LLM calls instead of stale "select_task".
    """
    universe_path = state.get("_universe_path", "")
    if not universe_path:
        return
    status_path = Path(universe_path) / "status.json"
    with _status_lock:
        try:
            data: dict[str, Any] = {}
            if status_path.exists():
                data = json.loads(status_path.read_text(encoding="utf-8"))
            data["current_phase"] = phase
            data["last_updated"] = datetime.now(timezone.utc).isoformat()
            write_data_path(status_path, json.dumps(data, indent=2) + "\n")
        except (OSError, json.JSONDecodeError):
            logger.debug("Failed to update phase in status.json")
