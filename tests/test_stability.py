"""Long-run stability and cross-module integration tests.

Verifies:
- Full book simulation (multi-chapter, multi-scene)
- State dict size doesn't grow unboundedly over many iterations
- Health degradation triggers diagnose and recovery
- Cross-book promise promotion via SeriesPromiseTracker
- Output versioning captures all drafts including reverted ones
- Select_task routing under different health conditions
"""

from __future__ import annotations

import importlib
from typing import Any

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from domains.fantasy_daemon.graphs.scene import build_scene_graph  # noqa: E402
from domains.fantasy_daemon.phases.book_close import book_close  # noqa: E402
from domains.fantasy_daemon.phases.commit import commit  # noqa: E402
from domains.fantasy_daemon.phases.diagnose import diagnose  # noqa: E402
from domains.fantasy_daemon.phases.select_task import select_task  # noqa: E402
from domains.fantasy_daemon.phases.universe_cycle import universe_cycle  # noqa: E402
from tests.support.fantasy_model import answer as fantasy_answer
from tinyassets.memory.promises import SeriesPromiseTracker  # noqa: E402
from tinyassets.memory.versioning import OutputVersionStore  # noqa: E402


@pytest.fixture(autouse=True)
def fantasy_model_transport(monkeypatch):
    from domains.fantasy_daemon.phases import _provider_stub

    monkeypatch.setattr(_provider_stub, "text_sync", fantasy_answer)
    monkeypatch.setattr(importlib.import_module("domains.fantasy_daemon.phases.commit"),
                        "text_sync", fantasy_answer)
    monkeypatch.setattr(importlib.import_module("domains.fantasy_daemon.phases.consolidate"),
                        "text_sync", fantasy_answer)


def _make_scene_state(
    *,
    chapter: int = 1,
    scene: int = 1,
    db_path: str = ":memory:",
    **overrides: Any,
) -> dict[str, Any]:
    """Create a minimal scene state for testing."""
    state = {
        "universe_id": "stability-test",
        "book_number": 1,
        "chapter_number": chapter,
        "scene_number": scene,
        "orient_result": {},
        "retrieved_context": {},
        "recent_prose": "",
        "workflow_instructions": {},
        "memory_context": {},
        "plan_output": None,
        "draft_output": None,
        "commit_result": None,
        "second_draft_used": False,
        "verdict": "",
        "extracted_facts": [],
        "extracted_promises": [],
        "style_observations": [],
        "quality_trace": [],
        "quality_debt": [],
        "_db_path": db_path,
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# 1. Full book simulation (multi-chapter, multi-scene)
# ---------------------------------------------------------------------------


class TestFullBookSimulation:
    """Simulate a mini-book: 3 chapters x 2 scenes each."""

    def test_multi_scene_execution(self, tmp_path):
        """Run 6 scenes through the scene graph, all should complete."""
        db_path = str(tmp_path / "book_sim.db")
        graph = build_scene_graph()

        with SqliteSaver.from_conn_string(":memory:") as cp:
            compiled = graph.compile(checkpointer=cp)

            for ch in range(1, 4):
                for sc in range(1, 3):
                    state = _make_scene_state(
                        chapter=ch, scene=sc, db_path=db_path,
                    )
                    thread_id = f"book-sim-c{ch}s{sc}"
                    config = {"configurable": {"thread_id": thread_id}}

                    final = compiled.invoke(state, config=config)

                    assert final["verdict"] in ("accept", "second_draft", "revert")
                    assert final["plan_output"] is not None
                    assert final["draft_output"] is not None
                    assert len(final["quality_trace"]) >= 4

    def test_book_close_after_chapters(self):
        """Book close should run after chapters complete."""
        result = book_close({
            "book_number": 1,
            "chapters_completed": 3,
            "extracted_promises": [
                {"id": "p1", "description": "Hero returns", "chapter": 2},
            ],
        })
        assert "book_summary" in result


# ---------------------------------------------------------------------------
# 2. State size stability over many iterations
# ---------------------------------------------------------------------------


class TestStateSizeStability:
    """Verify state dict size doesn't grow without bound."""

    def test_accumulated_lists_grow_linearly(self, tmp_path):
        """quality_trace and extracted_facts should grow linearly,
        not exponentially, across scene iterations."""
        db_path = str(tmp_path / "size_test.db")
        graph = build_scene_graph()

        sizes = []
        with SqliteSaver.from_conn_string(":memory:") as cp:
            compiled = graph.compile(checkpointer=cp)

            for i in range(1, 11):
                state = _make_scene_state(
                    chapter=1, scene=i, db_path=db_path,
                )
                config = {"configurable": {"thread_id": f"size-{i}"}}
                final = compiled.invoke(state, config=config)

                # Measure the size of accumulated lists
                trace_size = len(final["quality_trace"])
                facts_size = len(final["extracted_facts"])
                sizes.append(trace_size + facts_size)

        # Each scene adds a fixed number of trace entries (4 nodes)
        # and a small number of facts. Size should be roughly constant
        # per scene, not growing.
        avg_size = sum(sizes) / len(sizes)
        max_size = max(sizes)
        # Max should be within 3x of average (no exponential blowup)
        assert max_size <= avg_size * 3, (
            f"State size growing too fast: avg={avg_size}, max={max_size}"
        )

    def test_state_keys_bounded(self, tmp_path):
        """The number of keys in the state should not grow."""
        db_path = str(tmp_path / "keys_test.db")
        graph = build_scene_graph()

        key_counts = []
        with SqliteSaver.from_conn_string(":memory:") as cp:
            compiled = graph.compile(checkpointer=cp)

            for i in range(1, 6):
                state = _make_scene_state(
                    chapter=1, scene=i, db_path=db_path,
                )
                config = {"configurable": {"thread_id": f"keys-{i}"}}
                final = compiled.invoke(state, config=config)
                key_counts.append(len(final))

        # Key count should be constant across iterations
        assert max(key_counts) == min(key_counts), (
            f"Key count varies: {key_counts}"
        )


# ---------------------------------------------------------------------------
# 3. Health degradation and diagnose trigger
# ---------------------------------------------------------------------------


class TestHealthDegradation:
    """Verify diagnose detects stuck states and recovers."""

    def test_consecutive_reverts_detected(self):
        """Diagnose should detect 3+ consecutive reverts."""
        trace = [
            {"node": "commit", "verdict": "accept"},
            {"node": "commit", "verdict": "revert"},
            {"node": "commit", "verdict": "revert"},
            {"node": "commit", "verdict": "revert"},
        ]
        result = diagnose({
            "health": {"stuck_level": 5},
            "quality_trace": trace,
        })
        health = result["health"]
        assert health["recent_reverts"] == 3
        assert len(health["recovery_suggestions"]) >= 1
        assert any(
            s["type"] == "revert_pattern"
            for s in health["recovery_suggestions"]
        )
        # stuck_level should be reduced, not fully reset
        assert health["stuck_level"] == 3  # 5 - 2

    def test_recurring_failures_detected(self):
        """Diagnose should find recurring structural check failures."""
        trace = [
            {
                "node": "commit",
                "verdict": "accept",
                "structural_checks": [
                    {"name": "pacing", "passed": False},
                    {"name": "readability", "passed": True},
                ],
            },
            {
                "node": "commit",
                "verdict": "accept",
                "structural_checks": [
                    {"name": "pacing", "passed": False},
                ],
            },
        ]
        result = diagnose({
            "health": {"stuck_level": 2},
            "quality_trace": trace,
        })
        health = result["health"]
        assert "pacing" in health["recurring_failures"]
        assert health["recurring_failures"]["pacing"] == 2

    def test_general_stuck_suggestion(self):
        """With no specific pattern, diagnose should suggest general recovery."""
        result = diagnose({
            "health": {"stuck_level": 4},
            "quality_trace": [],
        })
        health = result["health"]
        assert len(health["recovery_suggestions"]) == 1
        assert health["recovery_suggestions"][0]["type"] == "general_stuck"

    def test_select_task_routes_to_diagnose_when_stuck(self):
        """select_task should route to diagnose when stuck_level > 3."""
        result = select_task({
            "task_queue": ["write"],
            "health": {"stuck_level": 5},
        })
        assert result["task_queue"][0] == "diagnose"

    def test_select_task_routes_to_worldbuild_when_stale(self):
        """select_task should route to worldbuild when world state is stale."""
        result = select_task({
            "task_queue": ["write"],
            "health": {"stuck_level": 0},
            "world_state_version": 0,
            "total_chapters": 10,
        })
        assert result["task_queue"][0] == "worldbuild"

    def test_diagnose_then_write_recovery_flow(self):
        """Full flow: stuck -> diagnose -> reduced stuck_level -> write resumes."""
        # Step 1: select_task detects stuck
        st_result = select_task({
            "task_queue": ["write"],
            "health": {"stuck_level": 5},
        })
        assert st_result["task_queue"][0] == "diagnose"

        # Step 2: diagnose generates recovery
        diag_result = diagnose({
            "health": {"stuck_level": 5},
            "quality_trace": [],
        })
        assert diag_result["health"]["stuck_level"] == 3  # reduced

        # Step 3: next select_task with user-directed write should resume
        recovered_health = diag_result["health"]
        recovered_health["cycles_completed"] = 1
        st_result2 = select_task({
            "task_queue": ["write"],
            "health": recovered_health,
            "canon_facts_count": 10,  # Established universe
            "world_state_version": 1,
            "workflow_instructions": {"next_task": "write"},
        })
        assert st_result2["task_queue"][0] == "write"


# ---------------------------------------------------------------------------
# 4. Cross-book promise promotion
# ---------------------------------------------------------------------------


class TestCrossBookPromises:
    """Verify promises promote from book to series level."""

    def test_promises_promote_across_books(self, canonical_source):
        """Promises from book 1 should be visible in book 2."""
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )

        # Book 1 close: promote promises
        book1_promises = [
            {"id": "p1", "description": "Hero finds the sword", "chapter": 5},
            {"id": "p2", "description": "Villain escapes", "chapter": 8},
        ]
        created = tracker.promote_from_book(book1_promises, book=1)
        assert created == 2

        # Book 2 start: check open promises
        open_p = tracker.get_open_promises()
        assert len(open_p) == 2

        # Resolve one in book 2
        tracker.resolve_promise("p1", book=2, chapter=3)
        open_p = tracker.get_open_promises()
        assert len(open_p) == 1
        assert open_p[0].promise_id == "p2"

    def test_overdue_promise_detection(self, canonical_source):
        """Promises unresolved for too long should be flagged."""
        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        tracker.create_promise("old_p", "Ancient prophecy", book=1, chapter=1)

        overdue = tracker.get_overdue_promises(
            current_book=3, current_chapter=10, max_age_chapters=20,
        )
        assert len(overdue) == 1
        assert overdue[0].promise_id == "old_p"

    def test_book_close_wires_promise_promotion(self, canonical_source):
        """book_close node should call promote_from_book."""
        import tinyassets.runtime_singletons as runtime

        tracker = SeriesPromiseTracker(
            canonical_source("promises", key=":memory:", namespaces=["test"]), "test"
        )
        runtime.promise_tracker = tracker
        try:
            result = book_close({
                "book_number": 1,
                "chapters_completed": 10,
                "extracted_promises": [
                    {"id": "cross1", "description": "Foreshadowed event", "chapter": 7},
                ],
            })
        finally:
            runtime.promise_tracker = None
        assert "book_summary" in result
        assert len(tracker.get_open_promises()) == 1


# ---------------------------------------------------------------------------
# 5. Output versioning captures all drafts
# ---------------------------------------------------------------------------


class TestOutputVersioning:
    """Verify versioning captures accepts and reverts."""

    def test_versioning_saves_accepted_draft(self, canonical_source):
        """Accepted drafts should be saved to version store."""
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "Good prose", verdict="accept", quality_score=0.9)

        current = store.get_current(1, 1, 1)
        assert current is not None
        assert current.verdict == "accept"
        assert current.quality_score == 0.9

    def test_versioning_saves_reverted_draft(self, canonical_source):
        """Reverted drafts should also be saved for history."""
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "Bad prose", verdict="revert", quality_score=0.2)
        store.save_draft(1, 1, 1, "Better prose", verdict="accept", quality_score=0.8)

        all_versions = store.get_all_versions(1, 1, 1)
        assert len(all_versions) == 2
        verdicts = {v.verdict for v in all_versions}
        assert "revert" in verdicts
        assert "accept" in verdicts

    def test_rollback_restores_earlier_version(self, canonical_source):
        """Rollback should restore an earlier draft as current."""
        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        store.save_draft(1, 1, 1, "Version 1")
        store.save_draft(1, 1, 1, "Version 2")
        store.save_draft(1, 1, 1, "Version 3")

        rolled = store.rollback(1, 1, 1, to_version=1)
        assert rolled.prose == "Version 1"

        current = store.get_current(1, 1, 1)
        assert current.version == 1

    def test_commit_node_saves_to_version_store(self, canonical_source, tmp_path):
        """Commit node should save draft to version store when wired."""
        import tinyassets.runtime_singletons as runtime

        store = OutputVersionStore(
            canonical_source("versions", key=":memory:", namespaces=["test"]), "test"
        )
        db_path = str(tmp_path / "version_commit.db")

        state = _make_scene_state(db_path=db_path)
        state["draft_output"] = {
            "scene_id": "test-B1-C1-S1",
            "prose": "The hero walked through the forest. " * 30,
            "word_count": 210,
        }

        runtime.version_store = store
        try:
            result = commit(state)
        finally:
            runtime.version_store = None

        # Version store should have saved the draft
        current = store.get_current(1, 1, 1)
        assert current is not None
        assert current.verdict == result["verdict"]


# ---------------------------------------------------------------------------
# 6. Universe cycle lifecycle
# ---------------------------------------------------------------------------


class TestUniverseCycleLifecycle:
    """Verify universe_cycle updates health and manages queue."""

    def test_cycle_updates_health_metrics(self):
        """universe_cycle should update total_words and total_chapters."""
        result = universe_cycle({
            "task_queue": ["write", "worldbuild"],
            "health": {},
            "total_words": 5000,
            "total_chapters": 3,
        })
        health = result["health"]
        assert health["total_words"] == 5000
        assert health["total_chapters"] == 3

    def test_cycle_pops_completed_task(self):
        """universe_cycle should pop the front task from queue."""
        result = universe_cycle({
            "task_queue": ["write", "worldbuild"],
            "health": {},
        })
        # The first task should be removed
        assert "write" not in result["task_queue"]
        assert "worldbuild" in result["task_queue"]

    def test_cycle_continues_when_queue_empty(self):
        """universe_cycle should NOT stop when queue empties.

        The daemon runs indefinitely -- select_task picks the next
        task.  Only an explicit health.stopped signal stops it.
        """
        result = universe_cycle({
            "task_queue": ["write"],
            "health": {},
        })
        health = result["health"]
        assert health.get("stopped", False) is False

    def test_cycle_continues_with_remaining_tasks(self):
        """universe_cycle should continue when queue has items."""
        result = universe_cycle({
            "task_queue": ["write", "worldbuild", "write"],
            "health": {},
        })
        health = result["health"]
        assert health.get("stopped", False) is False


pytestmark = pytest.mark.usefixtures("canonical_runtime")
