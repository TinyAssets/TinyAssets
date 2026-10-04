"""Research wake admission; scheduling and the S7 turn loop live elsewhere."""
from __future__ import annotations

from pathlib import Path


def _paused(universe_dir: Path, agent_id: str) -> bool:
    return (universe_dir / ".pause").exists()


def _busy(universe_dir: Path, agent_id: str) -> bool:
    # TODO(#4221): agent_activities.has_in_progress(universe_dir, agent_id).
    # The activities subsystem is absent on this branch.
    return False


def _has_compute(base_path: Path, command_center_id: str) -> bool:
    from tinyassets.provider_assignment import load_provider_assignment

    # Same no_serving_assignment check as automations._runtime_authority_reason.
    assignment = load_provider_assignment(base_path, universe_id=command_center_id)
    return assignment is not None and assignment.state == "ready"


def handle_research_wake(base_path: Path, req: dict) -> dict:
    root = Path(base_path)
    uid, agent_id = req["command_center_id"], req["agent_id"]
    universe_dir = root / uid
    if _paused(universe_dir, agent_id):
        return {"declined": "paused"}
    if _busy(universe_dir, agent_id):
        return {"declined": "busy"}
    if not _has_compute(root, uid):
        return {"declined": "no_compute"}
    # TODO: launch the S7 thin loop / activity runner with the platform-routed
    # research:<agent_id>:<turn_id> key. No turn runner or scheduler in D3a.
    return {"declined": "not_wired"}
