#!/usr/bin/env python3
"""Claude Code SessionStart hook: run dev-box disk hygiene, cheaply.

Founder directive 2026-09-26, after C: reached 0 bytes free and broke every
lane: *disk cleaning should be an automatic part of the architecture, not
something I'm asked about every so often.*

Why a SessionStart hook is the mechanism:

* It already exists and already runs here (``session_sync_gate_hook.py``), so
  there is no new install step and no elevation.
* It is version-controlled with the repo, so every agent lane on this box gets
  it the moment the change lands — nothing to remember, nothing to configure.
* Sessions are what *create* the garbage, so "every session start" tracks the
  rate the garbage appears at.

Its one weakness is that it only fires when a session starts, which is why
``scripts/install_dev_hygiene_task.ps1`` registers an hourly Task Scheduler job
for the full pass. Session start also inventories all classes so cleanup does not
depend on the task having been installed. Unknown or busy resources are kept.

Advisory only. Never blocks, never fails a session, never exits non-zero.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

# Inventory all local disk consumers at session start.
SESSION_CLASSES = "basetemp,scratch,docker,worktree,toolcache"
DEFAULT_FLOOR_GB = 40.0
HOOK_TIMEOUT = 300
# A stale escalation is noise; the scheduled pass runs hourly.
FULL_SUMMARY_MAX_AGE_HOURS = 12.0


def _truthy(value: str) -> bool:
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _project_dir(payload: dict) -> Path:
    raw = payload.get("cwd") or payload.get("project_dir")
    return Path(raw) if raw else Path.cwd()


def _floor_gb() -> float:
    try:
        return float(os.environ.get("TINYASSETS_DEV_HYGIENE_FLOOR_GB", "") or DEFAULT_FLOOR_GB)
    except ValueError:
        return DEFAULT_FLOOR_GB


def _full_pass_escalation(project: Path) -> str:
    """The hourly full pass's escalation, if it is recent enough to still be true."""
    path = project / ".claude" / "logs" / "dev-hygiene-full.json"
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError):
        return ""
    text = str(payload.get("escalation") or "")
    if not text:
        return ""
    finished = payload.get("finished_epoch")
    if not isinstance(finished, (int, float)):
        return ""
    if (time.time() - float(finished)) / 3600.0 > FULL_SUMMARY_MAX_AGE_HOURS:
        return ""
    return f"Full hygiene pass ({payload.get('finished_at', 'unknown time')}):\n{text}"


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}
    if str(payload.get("hook_event_name") or "") != "SessionStart":
        return 0
    if _truthy(os.environ.get("TINYASSETS_DEV_HYGIENE_DISABLE", "")):
        return 0

    project = _project_dir(payload)
    script = project / "scripts" / "dev_hygiene.py"
    if not script.exists():
        return 0

    floor = _floor_gb()
    logs = project / ".claude" / "logs"
    command = [
        sys.executable,
        str(script),
        "--apply",
        "--classes",
        SESSION_CLASSES,
        "--escalate-below",
        f"{floor:g}",
        "--budget-seconds",
        "180",
        "--quiet",
        "--repo",
        str(project),
        "--log",
        str(logs / "dev-hygiene.log"),
        "--summary-out",
        str(logs / "dev-hygiene-session.json"),
    ]
    try:
        proc = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=HOOK_TIMEOUT,
            cwd=str(project),
        )
        session_escalation = (proc.stdout or "").strip()
    except (subprocess.SubprocessError, OSError):
        # A hygiene pass is never worth a failed session start.
        session_escalation = ""

    blocks = [b for b in (session_escalation, _full_pass_escalation(project)) if b]
    if not blocks:
        return 0
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "SessionStart",
                    "additionalContext": "Disk hygiene (session start):\n" + "\n\n".join(blocks),
                }
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
