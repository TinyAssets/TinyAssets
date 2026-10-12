"""Tests for graph topology -- compilation, execution, and conditional edges.

Verifies:
- All 4 graphs compile without errors.
- Scene graph runs end-to-end with mock nodes.
- Conditional edges route correctly:
  - accept -> END
  - second_draft -> draft loop (once)
  - revert -> END
- Chapter graph loops scenes correctly.
- Book graph handles stuck recovery routing.
- Universe graph routes tasks and stops after one cycle.
- compile_all_graphs helper works.
- Fallback paths propagate word counts correctly.
"""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest
from langgraph.graph import END

from domains.fantasy_daemon.graphs import (
    build_book_graph,
    build_chapter_graph,
    build_scene_graph,
    build_universe_graph,
)
from domains.fantasy_daemon.graphs.book import route_after_diagnose, should_continue_book
from domains.fantasy_daemon.graphs.chapter import should_continue_chapter
from domains.fantasy_daemon.graphs.scene import route_after_commit
from domains.fantasy_daemon.graphs.universe import (
    route_after_foundation_review,
    route_dispatched_task,
    should_continue_universe,
)
from tinyassets.checkpointing import compile_all_graphs

pytestmark = pytest.mark.usefixtures("connected_fantasy_model")

# -----------------------------------------------------------------------
# Graph compilation tests
# -----------------------------------------------------------------------


class TestGraphCompilation:
    """Verify all 4 graphs compile successfully."""

    def test_scene_graph_compiles(self, checkpointer):
        graph = build_scene_graph()
        compiled = graph.compile(checkpointer=checkpointer)
        nodes = list(compiled.get_graph().nodes)
        assert "orient" in nodes
        assert "plan" in nodes
        assert "draft" in nodes
        assert "commit" in nodes

    def test_chapter_graph_compiles(self, checkpointer):
        graph = build_chapter_graph()
        compiled = graph.compile(checkpointer=checkpointer)
        nodes = list(compiled.get_graph().nodes)
        assert "run_scene" in nodes
        assert "consolidate" in nodes
        assert "learn" in nodes

    def test_book_graph_compiles(self, checkpointer):
        graph = build_book_graph()
        compiled = graph.compile(checkpointer=checkpointer)
        nodes = list(compiled.get_graph().nodes)
        assert "run_chapter" in nodes
        assert "diagnose" in nodes
        assert "book_close" in nodes

    def test_universe_graph_compiles(self, checkpointer):
        graph = build_universe_graph()
        compiled = graph.compile(checkpointer=checkpointer)
        nodes = list(compiled.get_graph().nodes)
        assert "foundation_priority_review" in nodes
        assert "authorial_priority_review" in nodes
        assert "dispatch_execution" in nodes
        assert "run_book" in nodes
        assert "worldbuild" in nodes
        assert "reflect" in nodes
        assert "universe_cycle" in nodes

    def test_compile_all_graphs(self, checkpointer):
        graphs = compile_all_graphs(checkpointer)
        assert set(graphs.keys()) == {"scene", "chapter", "book", "universe"}
        for name, compiled in graphs.items():
            nodes = list(compiled.get_graph().nodes)
            # Every compiled graph has __start__ and __end__
            assert "__start__" in nodes, f"{name} missing __start__"
            assert "__end__" in nodes, f"{name} missing __end__"


# -----------------------------------------------------------------------
# Scene graph end-to-end execution
# -----------------------------------------------------------------------


class TestSceneExecution:
    """Verify the scene graph runs end-to-end with real nodes."""

    def test_scene_runs_to_completion(self, checkpointer, scene_input):
        compiled = build_scene_graph().compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-scene-e2e"}}

        result = compiled.invoke(scene_input, config)

        assert result["verdict"] in ("accept", "second_draft", "revert")
        assert result["orient_result"]["scene_id"] is not None
        assert result["plan_output"]["beats"] is not None
        assert result["draft_output"]["prose"] is not None
        assert result["commit_result"]["overall_score"] >= 0.0

    def test_scene_accumulates_quality_trace(self, checkpointer, scene_input):
        compiled = build_scene_graph().compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-scene-trace"}}

        result = compiled.invoke(scene_input, config)

        # 4 nodes each append one trace entry
        assert len(result["quality_trace"]) >= 4
        trace_nodes = [t["node"] for t in result["quality_trace"]]
        assert trace_nodes[:4] == ["orient", "plan", "draft", "commit"]

    def test_scene_extracts_facts(self, checkpointer, scene_input):
        compiled = build_scene_graph().compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-scene-facts"}}

        result = compiled.invoke(scene_input, config)

        # Real nodes extract facts from mock prose
        assert len(result["extracted_facts"]) >= 1

    def test_scene_identity_fields_preserved(self, checkpointer, scene_input):
        compiled = build_scene_graph().compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-scene-id"}}

        result = compiled.invoke(scene_input, config)

        assert result["universe_id"] == "test-universe"
        assert result["book_number"] == 1
        assert result["chapter_number"] == 1
        assert result["scene_number"] == 1


# -----------------------------------------------------------------------
# Conditional edge routing tests
# -----------------------------------------------------------------------


class TestConditionalEdges:
    """Verify conditional edge functions route correctly."""

    # -- Scene: route_after_commit --

    def test_route_accept_ends(self):
        state = {"verdict": "accept", "second_draft_used": False}
        assert route_after_commit(state) == END

    def test_route_second_draft_loops(self):
        state = {"verdict": "second_draft", "second_draft_used": False}
        assert route_after_commit(state) == "draft"

    def test_route_second_draft_already_used_ends(self):
        state = {"verdict": "second_draft", "second_draft_used": True}
        assert route_after_commit(state) == END

    def test_route_revert_ends(self):
        state = {"verdict": "revert", "second_draft_used": False}
        assert route_after_commit(state) == END

    def test_route_revert_with_second_draft_used_ends(self):
        state = {"verdict": "revert", "second_draft_used": True}
        assert route_after_commit(state) == END

    # -- Chapter: should_continue_chapter --

    def test_chapter_continues_when_scenes_remain(self):
        state = {"scenes_completed": 1, "scenes_target": 3}
        assert should_continue_chapter(state) == "next_scene"

    def test_chapter_consolidates_when_target_reached(self):
        state = {"scenes_completed": 3, "scenes_target": 3}
        assert should_continue_chapter(state) == "consolidate"

    def test_chapter_consolidates_when_target_exceeded(self):
        state = {"scenes_completed": 5, "scenes_target": 3}
        assert should_continue_chapter(state) == "consolidate"

    def test_chapter_adaptive_end_on_high_word_count(self):
        """Chapter should end early if 2+ scenes and 3000+ words."""
        state = {
            "scenes_completed": 2,
            "scenes_target": 5,
            "chapter_word_count": 3500,
        }
        assert should_continue_chapter(state) == "consolidate"

    def test_chapter_no_adaptive_end_below_minimum_scenes(self):
        """Chapter should NOT end early with only 1 scene."""
        state = {
            "scenes_completed": 1,
            "scenes_target": 5,
            "chapter_word_count": 5000,
        }
        assert should_continue_chapter(state) == "next_scene"

    def test_chapter_no_adaptive_end_low_word_count(self):
        """Chapter with 2 scenes but low words should continue."""
        state = {
            "scenes_completed": 2,
            "scenes_target": 5,
            "chapter_word_count": 500,
        }
        assert should_continue_chapter(state) == "next_scene"

    def test_chapter_halts_after_three_consecutive_reverts(self):
        """RC-3 gate: 3 reverts in a row forces consolidate, not next_scene."""
        state = {
            "scenes_completed": 3,
            "scenes_target": 10,
            "chapter_word_count": 500,
            "consecutive_revert_count": 3,
        }
        assert should_continue_chapter(state) == "consolidate"

    def test_chapter_continues_with_two_reverts(self):
        """RC-3 gate: 2 reverts is under threshold, loop continues."""
        state = {
            "scenes_completed": 2,
            "scenes_target": 10,
            "chapter_word_count": 500,
            "consecutive_revert_count": 2,
        }
        assert should_continue_chapter(state) == "next_scene"

    # -- Book: should_continue_book --

    def test_book_continues_when_chapters_remain(self):
        state = {
            "chapters_completed": 1,
            "chapters_target": 10,
            "health": {"stuck_level": 0},
        }
        assert should_continue_book(state) == "next_chapter"

    def test_book_closes_when_target_reached(self):
        state = {
            "chapters_completed": 10,
            "chapters_target": 10,
            "health": {"stuck_level": 0},
        }
        assert should_continue_book(state) == "book_close"

    def test_book_diagnoses_when_stuck(self):
        state = {
            "chapters_completed": 3,
            "chapters_target": 10,
            "health": {"stuck_level": 3},
        }
        assert should_continue_book(state) == "diagnose"

    def test_book_adaptive_end_on_high_word_count(self):
        """Book should end early with 3+ chapters and 15000+ words."""
        state = {
            "chapters_completed": 3,
            "chapters_target": 20,
            "chapter_word_count": 16000,
            "health": {"stuck_level": 0},
        }
        assert should_continue_book(state) == "book_close"

    def test_book_no_adaptive_end_below_minimum_chapters(self):
        """Book should NOT end early with only 2 chapters."""
        state = {
            "chapters_completed": 2,
            "chapters_target": 20,
            "chapter_word_count": 20000,
            "health": {"stuck_level": 0},
        }
        assert should_continue_book(state) == "next_chapter"

    def test_book_no_adaptive_end_low_word_count(self):
        """Book with 3+ chapters but low words should continue."""
        state = {
            "chapters_completed": 3,
            "chapters_target": 20,
            "chapter_word_count": 5000,
            "health": {"stuck_level": 0},
        }
        assert should_continue_book(state) == "next_chapter"

    def test_book_diagnose_routes_recovery(self):
        state = {"health": {"stuck_level": 0}}
        assert route_after_diagnose(state) == "next_chapter"

    def test_book_diagnose_routes_still_stuck(self):
        state = {"health": {"stuck_level": 4}}
        assert route_after_diagnose(state) == "book_close"

    # -- Universe: review routing --

    def test_foundation_routes_hard_block_to_dispatch(self):
        state = {"review_stage": "foundation"}
        assert route_after_foundation_review(state) == "dispatch_execution"

    def test_foundation_routes_clear_to_authorial(self):
        state = {"review_stage": "authorial"}
        assert route_after_foundation_review(state) == "authorial_priority_review"

    def test_dispatch_routes_run_book(self):
        state = {"current_task": "run_book"}
        assert route_dispatched_task(state) == "run_book"

    def test_dispatch_routes_worldbuild(self):
        state = {"current_task": "worldbuild"}
        assert route_dispatched_task(state) == "worldbuild"

    def test_dispatch_routes_reflect(self):
        state = {"current_task": "reflect"}
        assert route_dispatched_task(state) == "reflect"

    def test_dispatch_defaults_to_idle(self):
        state = {}
        assert route_dispatched_task(state) == "idle"

    # -- Universe: should_continue_universe --

    def test_universe_continues_when_not_stopped(self):
        state = {"health": {"stopped": False}}
        assert should_continue_universe(state) == "cycle"

    def test_universe_ends_when_stopped(self):
        state = {"health": {"stopped": True}}
        assert should_continue_universe(state) == "end"

    def test_universe_continues_when_health_missing(self):
        state = {}
        assert should_continue_universe(state) == "cycle"


# -----------------------------------------------------------------------
# Second-draft loop integration test
# -----------------------------------------------------------------------


class TestSecondDraftLoop:
    """Verify the scene graph handles second_draft verdict correctly."""

    def test_second_draft_loops_back_to_draft(self, checkpointer, scene_input):
        """When commit returns second_draft, scene loops back to draft once."""
        call_count = {"commit": 0}

        def mock_commit_second_then_accept(state: dict[str, Any]) -> dict[str, Any]:
            call_count["commit"] += 1
            scene_id = state.get("draft_output", {}).get("scene_id", "unknown")

            if call_count["commit"] == 1:
                # First call: request second draft
                return {
                    "verdict": "second_draft",
                    "commit_result": {
                        "scene_id": scene_id,
                        "structural_pass": False,
                        "editorial_notes": None,
                        "overall_score": 0.4,
                    },
                    "second_draft_used": False,
                    "extracted_facts": [],
                    "extracted_promises": [],
                    "style_observations": [],
                    "quality_trace": [
                        {
                            "node": "commit",
                            "scene_id": scene_id,
                            "action": "request_second_draft",
                            "verdict": "second_draft",
                        }
                    ],
                    "quality_debt": [],
                }
            else:
                # Second call: accept
                return {
                    "verdict": "accept",
                    "commit_result": {
                        "scene_id": scene_id,
                        "structural_pass": True,
                        "editorial_notes": None,
                        "overall_score": 0.8,
                    },
                    "second_draft_used": True,
                    "extracted_facts": [
                        {"fact": "revised fact", "source_scene": scene_id, "confidence": 1.0}
                    ],
                    "extracted_promises": [],
                    "style_observations": [],
                    "quality_trace": [
                        {
                            "node": "commit",
                            "scene_id": scene_id,
                            "action": "accept_after_revision",
                            "verdict": "accept",
                        }
                    ],
                    "quality_debt": [],
                }

        with patch("domains.fantasy_daemon.graphs.scene.commit", mock_commit_second_then_accept):
            compiled = build_scene_graph().compile(checkpointer=checkpointer)
            config = {"configurable": {"thread_id": "test-second-draft"}}
            result = compiled.invoke(scene_input, config)

        assert result["verdict"] == "accept"
        assert result["second_draft_used"] is True
        assert call_count["commit"] == 2
        # Quality trace should have: orient + plan + draft + commit(1st) + draft(2nd) + commit(2nd)
        assert len(result["quality_trace"]) == 6


# -----------------------------------------------------------------------
# Full graph execution tests (higher level)
# -----------------------------------------------------------------------


class TestChapterExecution:
    """Verify the chapter graph runs end-to-end."""

    def test_chapter_runs_to_completion(self, checkpointer, chapter_input):
        compiled = build_chapter_graph().compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-chapter-e2e"}}

        result = compiled.invoke(chapter_input, config)

        assert result["scenes_completed"] == result["scenes_target"]
        assert result["chapter_summary"] is not None
        assert "completed" in result["chapter_summary"].lower()


class TestBookExecution:
    """Verify the book graph runs end-to-end."""

    def test_book_runs_to_completion(self, checkpointer, book_input):
        compiled = build_book_graph().compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-book-e2e"}}

        result = compiled.invoke(book_input, config)

        assert result["chapters_completed"] == result["chapters_target"]
        assert result["book_summary"] is not None


class TestUniverseExecution:
    """Verify the universe graph runs at least one cycle.

    The daemon now runs indefinitely (no auto-stop on empty queue),
    so we use ``recursion_limit`` to cap execution for testing.
    """

    def test_universe_runs_at_least_one_cycle(
        self, checkpointer, universe_input
    ):
        compiled = build_universe_graph().compile(
            checkpointer=checkpointer
        )
        config = {
            "configurable": {"thread_id": "test-universe-e2e"},
            "recursion_limit": 25,
        }

        from langgraph.errors import GraphRecursionError

        # Use stream to capture intermediate state even if recursion
        # limit is hit before the graph finishes.
        last_state = None
        try:
            for event in compiled.stream(universe_input, config):
                last_state = event
        except GraphRecursionError:
            pass  # Expected: daemon loops until recursion limit

        # We must have received at least one streaming event
        assert last_state is not None, "No state events emitted"

        # Check that universe_cycle actually ran by verifying health
        # was updated (cycles_completed > 0).  The event is keyed by
        # node name; look for universe_cycle or inspect the last event.
        if "universe_cycle" in last_state:
            cycle_health = last_state["universe_cycle"].get("health", {})
            assert cycle_health.get("cycles_completed", 0) >= 1
        else:
            # If the last event was a different node, verify that at
            # least some work happened -- total_chapters should have
            # been incremented by the run_book node.
            for node_name, node_output in last_state.items():
                if "total_chapters" in node_output:
                    assert node_output["total_chapters"] >= 1
                    break


# -----------------------------------------------------------------------
# Orient retrieval integration tests
# -----------------------------------------------------------------------


class TestOrientRetrieval:
    """Verify that orient calls the RetrievalRouter and populates
    retrieved_context with real results."""

    def test_orient_returns_retrieval_context_with_kg(self, tmp_path, tmp_story_db):
        """Orient should populate retrieved_context when a KG is available."""
        from domains.fantasy_daemon.phases.orient import orient
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
        from tinyassets.knowledge.models import (
            FactWithContext,
            GraphEntity,
            SourceType,
        )

        kg_path = str(tmp_path / "orient_test.db")
        kg = KnowledgeGraph(kg_path)
        kg.add_entity(
            GraphEntity(
                entity_id="ryn",
                entity_type="character",
                access_tier=0,
                public_description="A young warrior",
                hidden_description="",
                secret_description="",
                aliases=[],
            )
        )
        kg.add_facts(
            [
                FactWithContext(
                    fact_id="f1",
                    text="Ryn is a warrior",
                    source_type=SourceType.AUTHOR_FACT,
                )
            ]
        )
        kg.close()

        state = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scene_number": 1,
            "_db_path": tmp_story_db,
            "_kg_path": kg_path,
        }

        result = orient(state)
        ctx = result["retrieved_context"]

        # Should have populated dict with retrieval keys
        assert isinstance(ctx, dict)
        assert "sources" in ctx
        assert "facts" in ctx

    def test_orient_graceful_fallback_on_missing_kg(self, tmp_story_db, tmp_path):
        """Orient returns empty context when KG path doesn't exist yet."""
        from domains.fantasy_daemon.phases.orient import orient

        state = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scene_number": 1,
            "_db_path": tmp_story_db,
            # Point to a non-existent, but createable path.
            # KnowledgeGraph will create the DB, but it'll be empty.
            "_kg_path": str(tmp_path / "empty_kg.db"),
        }

        result = orient(state)
        # Should not crash -- returns either empty dict or populated dict
        assert isinstance(result["retrieved_context"], dict)

    def test_orient_retrieved_context_has_hipporag_source(self, tmp_path, tmp_story_db):
        """Orient queries about relationships should route through hipporag."""
        from domains.fantasy_daemon.phases.orient import orient
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
        from tinyassets.knowledge.models import (
            FactWithContext,
            GraphEdge,
            GraphEntity,
            SourceType,
        )

        kg_path = str(tmp_path / "hippo_test.db")
        kg = KnowledgeGraph(kg_path)
        for eid in ["ryn", "kael"]:
            kg.add_entity(
                GraphEntity(
                    entity_id=eid,
                    entity_type="character",
                    access_tier=0,
                    public_description=f"{eid} desc",
                    hidden_description="",
                    secret_description="",
                    aliases=[],
                )
            )
        kg.add_edge(
            GraphEdge(
                source="ryn",
                target="kael",
                relation_type="alliance",
                access_tier=0,
                temporal_scope="always",
                pov_characters=[],
                weight=1.0,
                valid_from_chapter=1,
                valid_to_chapter=None,
            )
        )
        kg.add_facts(
            [
                FactWithContext(
                    fact_id="f1",
                    text="Ryn and Kael are allies",
                    source_type=SourceType.AUTHOR_FACT,
                )
            ]
        )
        kg.close()

        state = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scene_number": 1,
            "_db_path": tmp_story_db,
            "_kg_path": kg_path,
        }

        result = orient(state)
        ctx = result["retrieved_context"]

        assert isinstance(ctx, dict)
        # The orient phase allows kg_relationships, so hipporag should appear
        if ctx.get("sources"):
            assert "hipporag" in ctx["sources"]

    def test_scene_graph_populates_retrieved_context(self, checkpointer, scene_input, tmp_path):
        """Full scene graph run should populate retrieved_context."""
        kg_path = str(tmp_path / "scene_kg.db")
        scene_input["_kg_path"] = kg_path

        compiled = build_scene_graph().compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-orient-retrieval"}}

        result = compiled.invoke(scene_input, config)

        # retrieved_context should be a dict (possibly empty if no KG data)
        assert isinstance(result["retrieved_context"], dict)


# -----------------------------------------------------------------------
# Fallback path tests -- verify word counts propagate when subgraphs fail
# -----------------------------------------------------------------------


class TestChapterFallbackWordCount:
    """Verify _run_chapter_fallback propagates word counts from scenes."""

    def test_chapter_fallback_accumulates_word_count(self):
        """When chapter subgraph fails, fallback path should still
        accumulate chapter_word_count from run_scene results."""
        from domains.fantasy_daemon.graphs.book import _run_chapter_fallback

        call_count = {"n": 0}

        def mock_run_scene(state: dict[str, Any]) -> dict[str, Any]:
            call_count["n"] += 1
            return {
                "scenes_completed": state["scenes_completed"] + 1,
                "consolidated_facts": [{"fact": f"fact-{call_count['n']}"}],
                "chapter_word_count": state.get("chapter_word_count", 0)
                + 150,
            }

        chapter_state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 0,
            "scenes_target": 3,
            "chapter_summary": None,
            "consolidated_facts": [],
            "quality_trend": {},
            "chapter_arc": {},
            "style_rules_observed": [],
            "craft_cards_generated": [],
            "chapter_word_count": 0,
        }

        with (
            patch(
                "domains.fantasy_daemon.graphs.chapter.run_scene",
                mock_run_scene,
            ),
            patch(
                "domains.fantasy_daemon.graphs.book.consolidate",
                lambda s: {"chapter_summary": "done"},
            ),
            patch(
                "domains.fantasy_daemon.graphs.book.learn",
                lambda s: {},
            ),
        ):
            result = _run_chapter_fallback(chapter_state)

        assert call_count["n"] == 3
        assert result["chapter_word_count"] == 450  # 150 * 3 scenes

    def test_run_chapter_uses_fallback_on_compile_failure(self):
        """run_chapter should use fallback when build_chapter_graph raises,
        and still return correct word count."""
        from domains.fantasy_daemon.graphs.book import run_chapter

        def mock_build_chapter_graph():
            raise RuntimeError("Simulated compilation failure")

        def mock_run_scene(state: dict[str, Any]) -> dict[str, Any]:
            return {
                "scenes_completed": state["scenes_completed"] + 1,
                "consolidated_facts": [],
                "chapter_word_count": state.get("chapter_word_count", 0)
                + 200,
            }

        book_state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapters_completed": 0,
            "chapters_target": 1,
            "book_summary": None,
            "book_arc": {},
            "health": {"stuck_level": 0},
            "cross_book_promises_active": [],
            "quality_trace": [],
            "chapter_word_count": 0,
        }

        with (
            patch(
                "domains.fantasy_daemon.graphs.chapter.build_chapter_graph",
                mock_build_chapter_graph,
            ),
            patch(
                "domains.fantasy_daemon.graphs.chapter.run_scene",
                mock_run_scene,
            ),
            patch(
                "domains.fantasy_daemon.graphs.book.consolidate",
                lambda s: {"chapter_summary": "done"},
            ),
            patch(
                "domains.fantasy_daemon.graphs.book.learn",
                lambda s: {},
            ),
        ):
            result = run_chapter(book_state)

        assert result["chapters_completed"] == 1
        # Default scenes_target is 3 -> 200 * 3 = 600
        assert result["chapter_word_count"] == 600


class TestBookFallbackWordCount:
    """Verify _run_book_fallback propagates word counts from chapters."""

    def test_book_fallback_copies_word_count(self):
        """When book subgraph fails, fallback path should copy
        chapter_word_count from run_chapter result."""
        from domains.fantasy_daemon.graphs.universe import _run_book_fallback

        def mock_run_chapter(state: dict[str, Any]) -> dict[str, Any]:
            return {
                "chapters_completed": state["chapters_completed"] + 1,
                "chapter_word_count": 500,
            }

        book_state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapters_completed": 0,
            "chapters_target": 1,
            "book_summary": None,
            "book_arc": {},
            "health": {"stuck_level": 0},
            "cross_book_promises_active": [],
            "quality_trace": [],
            "chapter_word_count": 0,
        }

        with patch(
            "domains.fantasy_daemon.graphs.book.run_chapter",
            mock_run_chapter,
        ):
            result = _run_book_fallback(book_state)

        assert result["chapters_completed"] == 1
        assert result["chapter_word_count"] == 500

    def test_run_book_uses_fallback_on_compile_failure(self):
        """run_book should use fallback when build_book_graph raises,
        and still return word count in total_words."""
        from domains.fantasy_daemon.graphs.universe import run_book

        def mock_build_book_graph():
            raise RuntimeError("Simulated compilation failure")

        def mock_run_chapter(state: dict[str, Any]) -> dict[str, Any]:
            return {
                "chapters_completed": state["chapters_completed"] + 1,
                "chapter_word_count": 750,
            }

        universe_state: dict[str, Any] = {
            "universe_id": "test",
            "universe_path": "/tmp/test",
            "total_words": 100,
            "total_chapters": 2,
            "health": {},
            "task_queue": ["write"],
        }

        with (
            patch(
                "domains.fantasy_daemon.graphs.book.build_book_graph",
                mock_build_book_graph,
            ),
            patch(
                "domains.fantasy_daemon.graphs.book.run_chapter",
                mock_run_chapter,
            ),
        ):
            result = run_book(universe_state)

        assert result["total_chapters"] == 3  # 2 existing + 1 new
        assert result["total_words"] == 850  # 100 existing + 750 new


# =====================================================================
# Scene-to-scene prose continuity
# =====================================================================


class TestSceneProsesContinuity:
    """Verify _last_scene_prose is declared and propagated."""

    def test_last_scene_prose_declared_in_chapter_state(self):
        """_last_scene_prose must be in ChapterState TypedDict to avoid
        silent stripping at LangGraph subgraph boundaries."""
        from domains.fantasy_daemon.state.chapter_state import ChapterState

        annotations = ChapterState.__annotations__
        assert "_last_scene_prose" in annotations, (
            "_last_scene_prose not declared in ChapterState -- "
            "LangGraph will silently strip it at subgraph boundaries"
        )

    def test_run_scene_stores_last_scene_prose(self):
        """run_scene should set _last_scene_prose from draft output."""
        from domains.fantasy_daemon.graphs.chapter import run_scene

        fake_prose = "The wind howled through the canyon."

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 0,
            "scenes_target": 3,
            "chapter_word_count": 0,
            "workflow_instructions": {},
            "_universe_path": "",
            "_db_path": "",
            "_kg_path": "",
            "_last_scene_prose": "",
        }

        def mock_scene_invoke(input_state, *a, **kw):
            return {
                "draft_output": {
                    "prose": fake_prose,
                    "word_count": 7,
                },
                "extracted_facts": [],
                "verdict": "accept",
            }

        with patch(
            "domains.fantasy_daemon.graphs.scene.build_scene_graph",
        ) as mock_build:
            mock_compiled = mock_build.return_value.compile.return_value
            mock_compiled.invoke = mock_scene_invoke

            result = run_scene(state)

        assert result.get("_last_scene_prose") == fake_prose

    def test_run_scene_passes_previous_prose(self):
        """run_scene should pass _last_scene_prose as recent_prose
        to the next scene."""
        from domains.fantasy_daemon.graphs.chapter import run_scene

        previous_prose = "She opened the iron gate."

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 1,
            "scenes_target": 3,
            "chapter_word_count": 500,
            "workflow_instructions": {},
            "_universe_path": "",
            "_db_path": "",
            "_kg_path": "",
            "_last_scene_prose": previous_prose,
        }

        captured_input = {}

        def mock_scene_invoke(input_state, *a, **kw):
            captured_input.update(input_state)
            return {
                "draft_output": {"prose": "Next scene.", "word_count": 2},
                "extracted_facts": [],
                "verdict": "accept",
            }

        with patch(
            "domains.fantasy_daemon.graphs.scene.build_scene_graph",
        ) as mock_build:
            mock_compiled = mock_build.return_value.compile.return_value
            mock_compiled.invoke = mock_scene_invoke

            run_scene(state)

        assert captured_input.get("recent_prose") == previous_prose

    def test_run_scene_increments_revert_streak(self):
        """RC-3 gate: run_scene increments consecutive_revert_count on revert."""
        from domains.fantasy_daemon.graphs.chapter import run_scene

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 1,
            "scenes_target": 5,
            "chapter_word_count": 150,
            "consecutive_revert_count": 2,
            "workflow_instructions": {},
            "_universe_path": "",
            "_db_path": "",
            "_kg_path": "",
            "_last_scene_prose": "",
        }

        def mock_scene_invoke(input_state, *a, **kw):
            return {
                "draft_output": {"prose": "stuck prose", "word_count": 2},
                "extracted_facts": [],
                "verdict": "revert",
            }

        with patch(
            "domains.fantasy_daemon.graphs.scene.build_scene_graph",
        ) as mock_build:
            mock_compiled = mock_build.return_value.compile.return_value
            mock_compiled.invoke = mock_scene_invoke

            result = run_scene(state)

        assert result.get("consecutive_revert_count") == 3

    def test_run_scene_resets_revert_streak_on_accept(self):
        """RC-3 gate: non-revert verdict resets consecutive_revert_count to 0."""
        from domains.fantasy_daemon.graphs.chapter import run_scene

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 1,
            "scenes_target": 5,
            "chapter_word_count": 150,
            "consecutive_revert_count": 2,
            "workflow_instructions": {},
            "_universe_path": "",
            "_db_path": "",
            "_kg_path": "",
            "_last_scene_prose": "",
        }

        def mock_scene_invoke(input_state, *a, **kw):
            return {
                "draft_output": {"prose": "good prose", "word_count": 2},
                "extracted_facts": [],
                "verdict": "accept",
            }

        with patch(
            "domains.fantasy_daemon.graphs.scene.build_scene_graph",
        ) as mock_build:
            mock_compiled = mock_build.return_value.compile.return_value
            mock_compiled.invoke = mock_scene_invoke

            result = run_scene(state)

        assert result.get("consecutive_revert_count") == 0
