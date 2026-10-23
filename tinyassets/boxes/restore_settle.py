"""In-box settlement after a rewind. Shared services do not queue the calls."""
from __future__ import annotations

import json
from pathlib import Path


def result_path(root: Path) -> Path:
    return Path(root) / ".owner-boxd" / "restore-result.json"


def rewind_paused(root: Path) -> bool:
    """True while a rewind has been recorded and the owner has not resumed."""
    path = result_path(root)
    if not path.is_file():
        return False
    document = json.loads(path.read_text(encoding="utf-8"))
    outcome = document.get("outcome")
    if outcome == "paused":
        return True
    if outcome == "resumed":
        return False
    raise RuntimeError("restore result is unreadable")


def settle_rewind(root: Path, generation: int) -> dict:
    """Validate happened before this. Pause restored work and record the result."""
    from tinyassets.control_plane.triggers import TriggerStore
    from tinyassets.owner_centers import Centers
    from tinyassets.runs import pause_queued
    from tinyassets.storage.pending_requests import invalidate_pending

    root = Path(root)
    if type(generation) is not int or generation < 1:
        raise ValueError("positive restored volume generation required")
    try:
        centers = list(Centers(root).active())
    except Exception:  # noqa: BLE001 - a box with no center table has nothing queued
        centers = []
    invalidated = invalidate_pending(root)
    paused_runs = pause_queued(root)
    for center in centers:
        invalidated += invalidate_pending(root / center)
        paused_runs += pause_queued(root / center)
    document = {
        "generation": generation,
        "outcome": "paused",
        "invalidated_approvals": invalidated,
        "paused_triggers": TriggerStore(root).pause_all(),
        "paused_runs": paused_runs,
    }
    path = result_path(root)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


def resume(root: Path, generation: int, operation_id: str) -> dict:
    """Explicit resumption. The id must belong to this volume generation."""
    if type(generation) is not int or not isinstance(operation_id, str):
        raise PermissionError("explicit resumption requires a fresh generation id")
    if not operation_id.startswith(f"{generation}:"):
        raise PermissionError("explicit resumption requires a fresh generation id")
    path = result_path(root)
    document = json.loads(path.read_text(encoding="utf-8"))
    if document.get("generation") != generation or document.get("outcome") != "paused":
        raise PermissionError("restore generation changed")
    document["outcome"] = "resumed"
    document["resumed_by"] = operation_id
    path.write_text(json.dumps(document), encoding="utf-8")
    return document


def propose_restore(universe_dir: Path, snapshot_id: str) -> dict:
    """The agent may ask. It cannot authorize a restore."""
    from tinyassets.storage.pending_requests import create_request

    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise ValueError("snapshot reference is required")
    row = create_request(
        Path(universe_dir), kind="restore", title="Restore the box?",
        body="Restore needs your confirmation. The agent cannot restore the box.",
        fields=[], action={"type": "restore_proposal", "snapshot_id": snapshot_id},
        dedupe_key=f"restore:{snapshot_id}")
    if not isinstance(row, dict) or row.get("error") or not row.get("request_id"):
        detail = row.get("detail") if isinstance(row, dict) else None
        raise RuntimeError("restore request could not be recorded"
                           + (f": {detail}" if isinstance(detail, str) and detail else ""))
    return row
