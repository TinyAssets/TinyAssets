"""Authorial-priority-review node -- universe graph; selects work target after foundation review."""

from __future__ import annotations

from typing import Any

from tinyassets.work_targets import (
    ROLE_NOTES,
    choose_authorial_targets,
    ensure_seed_targets,
    hard_priorities_path,
    materialize_pending_requests,
    work_targets_path,
    write_review_artifact,
)


def authorial_priority_review(state: dict[str, Any]) -> dict[str, Any]:
    """Choose the next authorial work target once hard blockers are clear."""
    universe_path = state.get("_universe_path", state.get("universe_path", ""))
    instructions = dict(state.get("workflow_instructions", {}))
    premise = str(
        instructions.get("premise") or state.get("premise_kernel") or ""
    )

    if not universe_path:
        return {
            "review_stage": "authorial",
            "selected_target_id": None,
            "selected_intent": None,
            "alternate_target_ids": [],
            "current_task": "idle",
            "task_queue": ["idle"],
            "quality_trace": [{
                "node": "authorial_priority_review",
                "action": "authorial_review_no_universe_path",
            }],
        }

    ensure_seed_targets(universe_path, premise=premise)
    materialized_requests = materialize_pending_requests(universe_path)
    ranked = choose_authorial_targets(universe_path, premise=premise)
    workflow_next = str(instructions.get("next_task", "") or "").strip().lower()

    selected = _choose_target(ranked, workflow_next, state.get("selected_target_id"))
    selected_intent = _choose_intent(selected, workflow_next)
    alternates = [
        target.target_id for target in ranked
        if target.target_id != (selected.target_id if selected else None)
    ][:2]

    payload = {
        "stage": "authorial",
        "workflow_next": workflow_next,
        "selected_target_id": selected.target_id if selected else None,
        "selected_intent": selected_intent,
        "alternate_target_ids": alternates,
        "candidate_target_ids": [target.target_id for target in ranked[:8]],
    }
    artifact_ref = write_review_artifact(
        universe_path, "authorial-priority-review", payload,
    )

    return {
        "review_stage": "authorial",
        "selected_target_id": selected.target_id if selected else None,
        "selected_intent": selected_intent,
        "alternate_target_ids": alternates,
        "current_task": "idle" if selected is None else None,
        "task_queue": ["idle"] if selected is None else [],
        "work_targets_ref": work_targets_path(universe_path).name,
        "hard_priorities_ref": hard_priorities_path(universe_path).name,
        "last_review_artifact_ref": artifact_ref,
        "quality_trace": [{
            "node": "authorial_priority_review",
            "action": "authorial_review",
            "selected_target_id": selected.target_id if selected else None,
            "selected_intent": selected_intent,
            "alternate_target_ids": alternates,
            "review_artifact_ref": artifact_ref,
            "materialized_request_count": len(materialized_requests),
        }],
    }


def _choose_target(
    ranked: list[Any],
    workflow_next: str,
    previously_selected: str | None,
) -> Any | None:
    if not ranked:
        return None

    if workflow_next == "reflect":
        for target in ranked:
            if target.role == ROLE_NOTES:
                return target

    if workflow_next == "worldbuild":
        for target in ranked:
            if target.role == ROLE_NOTES:
                return target

    if previously_selected:
        for target in ranked:
            if target.target_id == previously_selected:
                return target

    return ranked[0]


def _choose_intent(selected: Any | None, workflow_next: str) -> str | None:
    if selected is None:
        return None
    if workflow_next == "reflect":
        return "reflect on canon and notes"
    if workflow_next == "worldbuild":
        return "worldbuild and reconcile notes"
    if workflow_next == "write":
        return selected.current_intent or "continue authorial work"
    return selected.current_intent or "continue authorial work"
