"""The owner's live view of their command center: projects and activities, for
the screens they build (harness D2; the village: buildings are projects,
villagers are agents walking to what they are doing).

Read-only and owner-scoped: the caller has already proved write access to this
command center. Every field is picked; nothing here carries a prompt, an input,
an output or a principal id. Each list is complete -- the projects are the
command center's own workflows, few by nature, and activities are listed in
full except completed ones, of which the newest are kept for the view.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from tinyassets import agent_activities as activities

#: Completed or failed activities shown in a live view, newest first. The full
#: history is read through the activity contract, paged.
RECENT_FINISHED = 20


def projects(base_path: Path, universe_id: str) -> list[dict[str, Any]]:
    """One row per workflow that has run here: its progress and its state."""
    from tinyassets.daemon_server import get_branch_definition
    from tinyassets.runs import run_counts_by_branch

    rows = []
    for counts in run_counts_by_branch(base_path, universe_id=universe_id):
        branch_id = counts["branch_def_id"]
        try:
            name = str(get_branch_definition(base_path, branch_def_id=branch_id).get("name")
                       or "")
        except KeyError:
            name = ""
        if counts["running"]:
            state = "running"
        elif counts["last_status"] == "failed":
            state = "failed"
        else:
            state = "idle"
        rows.append({
            "project_id": branch_id, "name": name or "Workflow",
            "runs": counts["total"], "completed": counts["completed"],
            "failed": counts["failed"], "running": counts["running"],
            "last_activity_at": counts["last_at"], "state": state,
        })
    return rows


def activity_rows(universe_dir: Path) -> list[dict[str, Any]]:
    """Every unfinished activity and the newest finished ones, newest first."""
    rows: list[dict[str, Any]] = []
    finished = 0
    cursor = None
    while True:
        page = activities.list_page(universe_dir, cursor=cursor)
        for record in page["activities"]:
            done = record["status"] in activities.TERMINAL
            if done:
                finished += 1
                if finished > RECENT_FINISHED:
                    continue
            rows.append({
                "activity_id": record["activity_id"], "agent_id": record["agent_id"],
                "title": record["title"], "status": record["status"],
                "waiting_reason": record["waiting_reason"],
                "result_summary": record["result_summary"][:300] if done else "",
                "updated_at": record["updated_at"],
            })
        cursor = page["next_cursor"]
        if cursor is None:
            return rows


def agent_states(activity_list: list[dict[str, Any]]) -> dict[str, str]:
    """Each agent's state from its activities: waiting on the owner beats working."""
    states: dict[str, str] = {}
    for row in activity_list:
        if row["status"] == activities.WAITING_ON_YOU:
            states[row["agent_id"]] = "waiting_on_you"
        elif row["status"] == activities.IN_PROGRESS and states.get(row["agent_id"]) is None:
            states[row["agent_id"]] = "working"
    return states
