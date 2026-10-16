"""Integration test — worldbuild no-op streak guardrail routes graph to END.

Task #6 / Task B. The per-node unit tests in `test_universe_nodes.py` pin
the streak-counter math in `worldbuild()` in isolation. This test wires
worldbuild into a compiled LangGraph and verifies the end-to-end
contract: after `_MAX_WORLDBUILD_NOOP_STREAK` consecutive no-op cycles,
worldbuild sets `health["stopped"]`, and `should_continue_universe`
routes the graph to END.

Why not run the full `build_universe_graph()`?
The real graph starts at `foundation_priority_review`, which reads disk
artifacts and mutates the state far beyond what this test needs. We
build a minimal topology exercising the *exact* wiring under test:

    START -> worldbuild -> universe_cycle -> should_continue_universe
                                               |
                                    cycle -> worldbuild  (loop)
                                      end -> END         (guardrail fired)

`worldbuild` is called with a monkeypatched body so each cycle is a
deterministic no-op (signals_acted == 0, generated_files == []).
`universe_cycle` is the real node — the test needs to prove that
`health` set by worldbuild survives the cycle node and drives the
routing decision, which is precisely the plumbing question.
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from domains.fantasy_daemon.graphs.universe import should_continue_universe
from domains.fantasy_daemon.phases.universe_cycle import universe_cycle
from domains.fantasy_daemon.state.universe_state import UniverseState

pytestmark = pytest.mark.usefixtures("canonical_runtime")


# The parent package's __init__ re-exports the `worldbuild` function
# under the same name, shadowing the submodule. `importlib` gets us
# the actual module object so we can read `_MAX_WORLDBUILD_NOOP_STREAK`.
worldbuild_mod = importlib.import_module(
    "domains.fantasy_daemon.phases.worldbuild"
)


def _minimal_state() -> dict[str, Any]:
    """Thin UniverseState; only fields universe_cycle and worldbuild read."""
    return {
        "universe_id": "test-noop",
        "universe_path": "/tmp/noop-test-universe",
        "review_stage": "foundation",
        "active_series": None,
        "series_completed": [],
        "selected_target_id": None,
        "selected_intent": None,
        "alternate_target_ids": [],
        "current_task": "worldbuild",
        "current_execution_id": None,
        "current_execution_ref": None,
        "last_review_artifact_ref": None,
        "work_targets_ref": "work_targets.json",
        "hard_priorities_ref": "hard_priorities.json",
        "timeline_ref": None,
        "soft_conflicts": [],
        "world_state_version": 0,
        "canon_facts_count": 0,
        "total_words": 0,
        "total_chapters": 0,
        "health": {},
        "task_queue": [],
        "universal_style_rules": [],
        "cross_series_facts": [],
        "quality_trace": [],
    }


def _stub_noop_worldbuild(state: dict[str, Any]) -> dict[str, Any]:
    """Stand-in that mirrors worldbuild's output shape for a no-op cycle.

    Mirrors the real `worldbuild()` tail: increments the noop streak via
    the same arithmetic, sets `health["stopped"]` at threshold. This is
    a faithful stub — it intentionally duplicates the guardrail logic
    instead of calling the real node, because the real node hits disk,
    SQLite, and the memory manager. The assertion we care about is
    whether `health["stopped"]` propagates through the compiled graph
    to `should_continue_universe`, not whether worldbuild's own
    internals work (pinned by unit tests).
    """
    health = dict(state.get("health", {}))
    streak = int(health.get("worldbuild_noop_streak", 0)) + 1
    health["worldbuild_noop_streak"] = streak
    if streak >= worldbuild_mod._MAX_WORLDBUILD_NOOP_STREAK:
        health["stopped"] = True
        health["idle_reason"] = "worldbuild_stuck"
    return {
        "health": health,
        "world_state_version": state.get("world_state_version", 0) + 1,
        "quality_trace": [
            {
                "node": "worldbuild",
                "action": "stub_noop",
                "signals_acted": 0,
                "generated_files": [],
                "noop_streak": streak,
                "self_paused": health.get("stopped", False),
            }
        ],
    }


def _stub_productive_worldbuild(state: dict[str, Any]) -> dict[str, Any]:
    """Stand-in that mirrors a productive cycle — streak resets to 0."""
    health = dict(state.get("health", {}))
    health["worldbuild_noop_streak"] = 0
    return {
        "health": health,
        "world_state_version": state.get("world_state_version", 0) + 1,
        "quality_trace": [
            {
                "node": "worldbuild",
                "action": "stub_productive",
                "signals_acted": 2,
                "generated_files": ["doc1.md", "doc2.md"],
                "noop_streak": 0,
                "self_paused": False,
            }
        ],
    }


def _build_minimal_graph(worldbuild_fn):
    """worldbuild -> universe_cycle -> cond(should_continue_universe)."""
    graph = StateGraph(UniverseState)
    graph.add_node("worldbuild", worldbuild_fn)
    graph.add_node("universe_cycle", universe_cycle)
    graph.set_entry_point("worldbuild")
    graph.add_edge("worldbuild", "universe_cycle")
    graph.add_conditional_edges(
        "universe_cycle",
        should_continue_universe,
        {"cycle": "worldbuild", "end": END},
    )
    return graph


def test_three_consecutive_noops_halt_graph() -> None:
    """After _MAX_WORLDBUILD_NOOP_STREAK (3) cycles, graph routes to END.

    Drives the compiled graph and collects the streaming events. The
    graph naturally terminates when should_continue_universe returns
    "end"; that's the integration assertion — no recursion limit trick.
    """
    with SqliteSaver.from_conn_string(":memory:") as cp:
        compiled = _build_minimal_graph(_stub_noop_worldbuild).compile(
            checkpointer=cp,
        )
        config = {"configurable": {"thread_id": "noop-three"}}
        events = list(
            compiled.stream(_minimal_state(), config, stream_mode="values")
        )

    # Graph should have halted naturally, not hit recursion limit.
    final = events[-1]
    assert final["health"]["stopped"] is True
    assert final["health"]["idle_reason"] == "worldbuild_stuck"
    # The streak must have reached exactly the guardrail threshold.
    assert (
        final["health"]["worldbuild_noop_streak"]
        == worldbuild_mod._MAX_WORLDBUILD_NOOP_STREAK
    )

    # Count how many times worldbuild actually ran.
    worldbuild_runs = [
        t for t in final.get("quality_trace", [])
        if t.get("node") == "worldbuild"
    ]
    assert len(worldbuild_runs) == worldbuild_mod._MAX_WORLDBUILD_NOOP_STREAK
    # Self-pause flag must fire on the threshold cycle, not before.
    streaks = [t["noop_streak"] for t in worldbuild_runs]
    assert streaks == [1, 2, 3]
    assert [t["self_paused"] for t in worldbuild_runs] == [False, False, True]


def test_two_noops_then_productive_resets_streak() -> None:
    """A productive cycle mid-run resets the counter; guardrail doesn't fire.

    This uses a stub that flips between no-op and productive behavior
    across cycles. The integration claim: streak reset is visible in
    state after universe_cycle runs, so a future no-op starts fresh.
    """
    call_count = {"n": 0}

    def _hybrid_worldbuild(state: dict[str, Any]) -> dict[str, Any]:
        call_count["n"] += 1
        # Cycles 1, 2: no-op. Cycle 3: productive (resets). Cycle 4: no-op.
        # Then end manually via health["stopped"] on cycle 4 to terminate.
        if call_count["n"] == 3:
            return _stub_productive_worldbuild(state)
        out = _stub_noop_worldbuild(state)
        # Force halt after cycle 4 so the test doesn't loop forever.
        if call_count["n"] >= 4:
            out["health"]["stopped"] = True
            out["health"]["idle_reason"] = "test_bound"
        return out

    with SqliteSaver.from_conn_string(":memory:") as cp:
        compiled = _build_minimal_graph(_hybrid_worldbuild).compile(
            checkpointer=cp,
        )
        config = {"configurable": {"thread_id": "noop-reset"}}
        events = list(
            compiled.stream(_minimal_state(), config, stream_mode="values")
        )

    final = events[-1]
    worldbuild_runs = [
        t for t in final.get("quality_trace", [])
        if t.get("node") == "worldbuild"
    ]
    streaks = [t["noop_streak"] for t in worldbuild_runs]
    # Cycles 1, 2 no-op → 1, 2. Cycle 3 productive → 0. Cycle 4 no-op → 1.
    assert streaks == [1, 2, 0, 1]
    # Guardrail did NOT fire via the worldbuild_stuck path — streak
    # never reached threshold.
    paused_cycles = [
        t for t in worldbuild_runs if t.get("self_paused")
    ]
    assert paused_cycles == [], (
        "productive cycle should have reset streak, preventing self-pause"
    )
    # Test bound halted the loop instead (sanity — loop really did exit).
    assert final["health"]["idle_reason"] == "test_bound"


def test_productive_first_cycle_does_not_halt() -> None:
    """Sanity check: a single productive cycle leaves streak at 0 and the
    graph continues. Forces termination via test-bound halt on cycle 2.
    """
    call_count = {"n": 0}

    def _productive_then_halt(state: dict[str, Any]) -> dict[str, Any]:
        call_count["n"] += 1
        if call_count["n"] == 1:
            return _stub_productive_worldbuild(state)
        # Cycle 2: halt the loop.
        out = _stub_noop_worldbuild(state)
        out["health"]["stopped"] = True
        out["health"]["idle_reason"] = "test_bound"
        return out

    with SqliteSaver.from_conn_string(":memory:") as cp:
        compiled = _build_minimal_graph(_productive_then_halt).compile(
            checkpointer=cp,
        )
        config = {"configurable": {"thread_id": "productive-first"}}
        events = list(
            compiled.stream(_minimal_state(), config, stream_mode="values")
        )

    final = events[-1]
    worldbuild_runs = [
        t for t in final.get("quality_trace", [])
        if t.get("node") == "worldbuild"
    ]
    # Cycle 1 productive (streak 0), cycle 2 no-op (streak 1) + test halt.
    assert [t["noop_streak"] for t in worldbuild_runs] == [0, 1]
    # worldbuild_stuck never triggered.
    assert final["health"]["idle_reason"] == "test_bound"


def test_guardrail_threshold_matches_worldbuild_module_constant() -> None:
    """Regression guard: if `_MAX_WORLDBUILD_NOOP_STREAK` is retuned, the
    integration test above needs to know. Pin the current value here so
    a silent constant change surfaces as a visible test failure rather
    than a confusing integration flake.
    """
    assert worldbuild_mod._MAX_WORLDBUILD_NOOP_STREAK == 3
