"""Foundation-priority-review node -- universe graph; cycle entry point, gates hard blockers."""

from __future__ import annotations

from typing import Any

from tinyassets.work_targets import (
    HARD_PRIORITY_ACTIVE,
    collect_soft_conflicts,
    finalize_eligible_discards,
    hard_priorities_path,
    sync_source_synthesis_priorities,
    work_targets_path,
    write_review_artifact,
)


def foundation_priority_review(state: dict[str, Any]) -> dict[str, Any]:
    """Review hard blockers before any authorial work.

    v1 hard-block policy is intentionally narrow: unsynthesized uploads are
    the only must-do-now blocker. Other conflicts remain visible as soft
    conflicts for later daemon judgment.
    """
    universe_path = state.get("_universe_path", state.get("universe_path", ""))
    if not universe_path:
        return {
            "review_stage": "authorial",
            "soft_conflicts": [],
            "quality_trace": [{
                "node": "foundation_priority_review",
                "action": "foundation_review_no_universe_path",
            }],
        }

    review_cycle = int(state.get("health", {}).get("review_cycles_completed", 0))
    try:
        # Inside the handler: this loads work targets, so a refused read here is
        # the same class of refusal as the signal sync below and must idle the
        # cycle rather than escape the phase.
        finalized_discards = finalize_eligible_discards(
            universe_path,
            review_cycle=review_cycle,
        )
        priorities, synth_signals = sync_source_synthesis_priorities(universe_path)
    except (RuntimeError, OSError) as exc:
        # The signal queue or the work targets are unreadable or refused (a
        # link). Never rebuild from empty, and never read that as "no
        # blockers": stay in foundation and idle this cycle, leaving it
        # untouched.
        return {
            "review_stage": "foundation",
            "current_task": "idle",
            "soft_conflicts": [],
            "quality_trace": [{
                "node": "foundation_priority_review",
                "action": "foundation_review_signals_unreadable",
                "error": str(exc),
            }],
        }
    active_hard = [
        priority for priority in priorities
        if priority.status == HARD_PRIORITY_ACTIVE and priority.hard_block
    ]
    soft_conflicts = collect_soft_conflicts(universe_path)

    selected_target_id = active_hard[0].target_id if active_hard else None
    selected_intent = "synthesize source upload" if active_hard else None
    stage = "foundation" if active_hard else "authorial"
    current_task = "worldbuild" if active_hard else None

    payload = {
        "stage": "foundation",
        "hard_blocked": bool(active_hard),
        "active_hard_priorities": [priority.to_dict() for priority in active_hard],
        "soft_conflicts": soft_conflicts,
        "selected_target_id": selected_target_id,
        "selected_intent": selected_intent,
        "synthesis_signals": synth_signals,
        "finalized_discards": [target.target_id for target in finalized_discards],
    }
    artifact_ref = write_review_artifact(
        universe_path, "foundation-priority-review", payload,
    )

    return {
        "review_stage": stage,
        "selected_target_id": selected_target_id,
        "selected_intent": selected_intent,
        "alternate_target_ids": [],
        "current_task": current_task,
        "task_queue": ["worldbuild"] if current_task else [],
        "work_targets_ref": work_targets_path(universe_path).name,
        "hard_priorities_ref": hard_priorities_path(universe_path).name,
        "soft_conflicts": soft_conflicts,
        "last_review_artifact_ref": artifact_ref,
        "quality_trace": [{
            "node": "foundation_priority_review",
            "action": "foundation_review",
            "hard_blocked": bool(active_hard),
            "active_hard_priorities": len(active_hard),
            "soft_conflicts": len(soft_conflicts),
            "selected_target_id": selected_target_id,
            "selected_intent": selected_intent,
            "review_artifact_ref": artifact_ref,
            "synthesis_signals": len(synth_signals),
            "finalized_discards": len(finalized_discards),
        }],
    }
