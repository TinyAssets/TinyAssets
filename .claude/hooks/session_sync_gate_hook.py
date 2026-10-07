#!/usr/bin/env python3
"""Claude Code SessionStart hook: surface the branch sync gate (Layer 3).

Runs scripts/session_sync_gate.py and, if the primary checkout is off-main or
behind origin/main, injects the warning into session context so the drift is
seen at the top of the session instead of discovered as a "1,209 behind" mess.

Heals the PRIMARY checkout (session_sync_gate --heal): a non-main branch or
uncommitted edits are saved to a local backup branch, then main fast-forwards,
so no session ever loads a stale AGENTS.md. Never touches linked worktrees;
never blocks the session.
See docs/design-notes/2026-06-24-branch-lifecycle-automation.md.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


def _project_dir(payload: dict) -> Path:
    raw = payload.get("cwd") or payload.get("project_dir")
    if raw:
        return Path(raw)
    return Path.cwd()


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        payload = {}

    if str(payload.get("hook_event_name") or "") != "SessionStart":
        return 0

    project = _project_dir(payload)
    script = project / "scripts" / "session_sync_gate.py"
    if not script.exists():
        return 0

    try:
        proc = subprocess.run(
            [sys.executable, str(script), "--heal"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=22,
            cwd=str(project),
        )
    except (subprocess.SubprocessError, OSError):
        return 0

    # Exit 0 + "clean" => nothing to inject. Warnings print on stdout (rc 0 here).
    out = (proc.stdout or "").strip()
    if not out or out.startswith("✓"):  # ✓ clean
        return 0

    context = "Branch sync gate (session start):\n" + out
    print(
        json.dumps(
            {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": context}}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
