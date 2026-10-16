"""Cycle-level no-op guardrail (#6 Task A).

The worldbuild-local streak only fires when worldbuild runs. A universe
stuck in review → idle → cycle (never reaching worldbuild) can loop
forever below that radar. `universe_cycle` watches the whole cycle:
if five consecutive cycles move none of the progress signals, self-pause.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from domains.fantasy_daemon.phases.universe_cycle import (
    _MAX_CYCLE_NOOP_STREAK,
    universe_cycle,
)


def _base_state(universe_path: Path, **overrides: object) -> dict:
    state = {
        "universe_id": "test",
        "universe_path": str(universe_path),
        "_universe_path": str(universe_path),
        "review_stage": "foundation",
        "health": {},
        "task_queue": [],
        "current_task": None,
        "total_words": 0,
        "total_chapters": 0,
        "world_state_version": 1,
        "canon_facts_count": 0,
        "quality_trace": [],
        "cycle_noop_streak": 0,
        "_prev_cycle_totals": {},
    }
    state.update(overrides)
    return state


def _run_cycle(state: dict) -> dict:
    """Apply one cycle's output back onto state (LangGraph merge semantics)."""
    delta = universe_cycle(state)
    merged = dict(state)
    for key, value in delta.items():
        if key == "quality_trace":
            merged["quality_trace"] = (state.get("quality_trace") or []) + list(value)
        else:
            merged[key] = value
    return merged


def test_streak_trips_after_max_noop_cycles(tmp_path: Path) -> None:
    """Five consecutive no-op cycles set stopped=True + idle_reason."""
    state = _base_state(tmp_path)

    for i in range(_MAX_CYCLE_NOOP_STREAK - 1):
        state = _run_cycle(state)
        assert not state["health"].get("stopped"), (
            f"premature stop at cycle {i + 1}"
        )
        assert state["cycle_noop_streak"] == i + 1

    # Cycle that tips over the threshold
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == _MAX_CYCLE_NOOP_STREAK
    assert state["health"]["stopped"] is True
    assert state["health"]["idle_reason"] == "universe_cycle_noop_streak"


def test_forward_progress_resets_streak(tmp_path: Path) -> None:
    """total_words increase resets the streak counter."""
    state = _base_state(tmp_path)

    # Accumulate some streak
    state = _run_cycle(state)
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 2

    # Next cycle: word count jumps → progress
    state["total_words"] = 500
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 0
    assert not state["health"].get("stopped")


def test_canon_facts_progress_resets_streak(tmp_path: Path) -> None:
    state = _base_state(tmp_path)
    state = _run_cycle(state)
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 2

    state["canon_facts_count"] = 7
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 0


def test_notes_mtime_progress_resets_streak(tmp_path: Path) -> None:
    """A new note landing mid-stream counts as forward progress."""
    notes_path = tmp_path / "notes.json"
    state = _base_state(tmp_path)
    state = _run_cycle(state)
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 2

    # Simulate a user/editor note landing between cycles
    notes_path.write_text(
        json.dumps([{
            "id": "n1", "source": "user", "text": "keep going",
            "category": "direction", "status": "unread",
        }]) + "\n",
        encoding="utf-8",
    )

    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 0


def test_signals_acted_in_quality_trace_counts_as_progress(tmp_path: Path) -> None:
    """If the last quality_trace entry reports signals_acted > 0, progress."""
    state = _base_state(tmp_path)
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 1

    # Simulate worldbuild having run and acted on signals before this cycle
    state["quality_trace"].append({
        "node": "worldbuild",
        "action": "worldbuild_real",
        "signals_acted": 3,
        "generated_files": [],
    })
    state = _run_cycle(state)
    assert state["cycle_noop_streak"] == 0


def test_self_pause_note_emitted_on_trip(tmp_path: Path) -> None:
    """When the streak trips, a system-attributed note lands in notes.json."""
    state = _base_state(tmp_path)
    for _ in range(_MAX_CYCLE_NOOP_STREAK):
        state = _run_cycle(state)

    assert state["health"]["stopped"] is True
    notes_file = tmp_path / "notes.json"
    assert notes_file.exists(), "self-pause note should have been written"
    notes = json.loads(notes_file.read_text(encoding="utf-8"))
    assert any(
        n.get("source") == "system"
        and "self_pause" in (n.get("tags") or [])
        and n.get("metadata", {}).get("idle_reason") == "universe_cycle_noop_streak"
        for n in notes
    )


def test_streak_appears_in_state_each_cycle(tmp_path: Path) -> None:
    """cycle_noop_streak is visible in returned state every cycle, not just on trip."""
    state = _base_state(tmp_path)
    for expected in (1, 2, 3):
        state = _run_cycle(state)
        assert "cycle_noop_streak" in state
        assert state["cycle_noop_streak"] == expected
        assert state["health"]["cycle_noop_streak"] == expected


def test_already_stopped_cycle_does_not_clobber_idle_reason(tmp_path: Path) -> None:
    """External stop (API/SIGINT/.pause) stays the authoritative reason."""
    state = _base_state(tmp_path, health={"stopped": True, "idle_reason": "api_stop"})
    for _ in range(_MAX_CYCLE_NOOP_STREAK):
        state = _run_cycle(state)

    # Still stopped, but the reason wasn't overwritten
    assert state["health"]["stopped"] is True
    assert state["health"]["idle_reason"] == "api_stop"


pytestmark = pytest.mark.usefixtures("canonical_runtime")
