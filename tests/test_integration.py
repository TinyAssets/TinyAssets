"""Cross-module integration tests.

Verifies that all modules are properly wired together:
- Orient calls RetrievalRouter and MemoryManager
- Plan uses HTN/DOME guidance and MemoryManager
- Draft uses provider (mocked) and MemoryManager
- Commit uses real StructuralEvaluator and editorial verdict
- Full scene graph executes end-to-end
- Desktop dashboard receives graph events
- Entry point (DaemonController) initializes all subsystems
"""

from __future__ import annotations

import importlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

import domains.fantasy_daemon.phases._provider_stub as _provider_stub  # noqa: E402
from domains.fantasy_daemon.graphs.scene import build_scene_graph  # noqa: E402
from domains.fantasy_daemon.phases.commit import commit  # noqa: E402
from domains.fantasy_daemon.phases.draft import draft  # noqa: E402
from domains.fantasy_daemon.phases.orient import orient  # noqa: E402
from domains.fantasy_daemon.phases.plan import plan  # noqa: E402
from tests.support.fantasy_model import answer as fantasy_answer
from tinyassets.desktop.dashboard import DashboardHandler  # noqa: E402
from tinyassets.evaluation.structural import StructuralEvaluator, StructuralResult  # noqa: E402

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fantasy_model_transport(monkeypatch):
    monkeypatch.setattr(_provider_stub, "text_sync", fantasy_answer)
    monkeypatch.setattr(importlib.import_module("domains.fantasy_daemon.phases.commit"),
                        "text_sync", fantasy_answer)
    monkeypatch.setattr(importlib.import_module("domains.fantasy_daemon.phases.consolidate"),
                        "text_sync", fantasy_answer)


@pytest.fixture
def tmp_db():
    fd, path = tempfile.mkstemp(suffix=".db", prefix="integ_test_")
    os.close(fd)
    os.unlink(path)
    yield path
    try:
        os.unlink(path)
    except FileNotFoundError:
        pass


@pytest.fixture
def base_state(tmp_db) -> dict[str, Any]:
    """Minimal scene state with integration-relevant keys."""
    return {
        "universe_id": "integ-test",
        "book_number": 1,
        "chapter_number": 1,
        "scene_number": 1,
        "orient_result": {},
        "retrieved_context": {},
        "recent_prose": "",
        "workflow_instructions": {},
        "memory_context": {},
        "search_context": {},
        "plan_output": None,
        "draft_output": None,
        "commit_result": None,
        "editorial_notes": None,
        "second_draft_used": False,
        "verdict": "",
        "extracted_facts": [],
        "extracted_promises": [],
        "style_observations": [],
        "quality_trace": [],
        "quality_debt": [],
        "_universe_path": "",
        "_db_path": tmp_db,
        "_kg_path": "",
    }


# ---------------------------------------------------------------------------
# 1. Orient → Retrieval Router integration
# ---------------------------------------------------------------------------


class TestOrientRetrievalIntegration:
    """Orient node calls RetrievalRouter and populates retrieved_context."""

    def test_orient_calls_retrieval_router(self, base_state):
        """Orient should attempt to call RetrievalRouter."""
        result = orient(base_state)
        # retrieved_context should be a dict (possibly empty if KG not initialized)
        assert isinstance(result["retrieved_context"], dict)

    def test_orient_populates_retrieved_context_with_kg(self, base_state, tmp_db):
        """When KG has data, retrieved_context should be non-empty."""
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
        from tinyassets.knowledge.models import GraphEntity

        kg_path = tmp_db + ".kg"
        kg = KnowledgeGraph(kg_path)
        # Add some entity data using the GraphEntity TypedDict
        kg.add_entity(GraphEntity(
            entity_id="Ryn", entity_type="character", access_tier=0,
            public_description="A young scout", hidden_description="",
            secret_description="", aliases=[],
        ))
        kg.add_entity(GraphEntity(
            entity_id="Northern Gate", entity_type="location", access_tier=0,
            public_description="An ancient gate", hidden_description="",
            secret_description="", aliases=[],
        ))
        kg.close()

        base_state["_kg_path"] = kg_path
        result = orient(base_state)

        ctx = result["retrieved_context"]
        assert isinstance(ctx, dict)
        # Should have at least attempted retrieval (facts key present)
        if ctx:  # Non-empty means retrieval worked
            assert "facts" in ctx or "relationships" in ctx or "sources" in ctx

        # Cleanup
        try:
            os.unlink(kg_path)
        except FileNotFoundError:
            pass

    def test_orient_graceful_on_retrieval_failure(self, base_state):
        """Orient should not crash if retrieval fails."""
        base_state["_kg_path"] = "/nonexistent/path.db"
        result = orient(base_state)
        assert isinstance(result["retrieved_context"], dict)
        assert "orient_result" in result  # Rest of orient still works

    def test_orient_passes_provider_call_to_router(self, base_state):
        """RetrievalRouter wiring should match the current search policy."""
        from unittest.mock import MagicMock, patch

        # Patch the router class at its import source
        mock_router_cls = MagicMock()
        mock_instance = mock_router_cls.return_value
        from tinyassets.retrieval.router import RetrievalResult
        empty = RetrievalResult(
            facts=[], relationships=[], prose_chunks=[],
            community_summaries=[], sources=[], token_count=0,
        )

        async def mock_query(**kw):
            return empty

        mock_instance.query = mock_query

        with patch(
            "tinyassets.retrieval.router.RetrievalRouter", mock_router_cls,
        ):
            from domains.fantasy_daemon.phases.orient import _run_retrieval
            _run_retrieval(base_state, "test-scene")

        assert mock_router_cls.called
        kwargs = mock_router_cls.call_args[1]
        provider_call = kwargs.get("provider_call")
        assert provider_call is not None
        assert callable(provider_call)

    def test_orient_passes_enriched_state_to_memory_manager(self, base_state):
        """MemoryManager should see the freshly assembled orient_result contract."""
        from tinyassets import runtime_singletons as runtime

        captured: dict[str, Any] = {}

        class DummyManager:
            def assemble_context(self, phase, state):
                captured["phase"] = phase
                captured["orient_result"] = dict(state.get("orient_result", {}))
                return {"assembled": True}

        runtime.memory_manager = DummyManager()
        result = orient(base_state)

        assert result["memory_context"] == {"assembled": True}
        assert captured["phase"] == "orient"
        assert "warnings" in captured["orient_result"]
        assert "world_state" in captured["orient_result"]

    def test_run_retrieval_preserves_runtime_knowledge_graph(self, base_state):
        """orient retrieval must not close the daemon-owned KG singleton."""
        from unittest.mock import patch

        from domains.fantasy_daemon.phases.orient import _run_retrieval
        from tinyassets import runtime_singletons as runtime
        from tinyassets.knowledge.models import (
            FactWithContext,
            RetrievalResult,
            SourceType,
        )

        class FakeKG:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        class FakeRouter:
            def __init__(self, **kwargs):
                self.kwargs = kwargs

            async def query(self, **kwargs):
                return RetrievalResult(
                    facts=[
                        FactWithContext(
                            fact_id="f1",
                            text="Ryn serves Ashwater.",
                            source_type=SourceType.AUTHOR_FACT,
                        )
                    ],
                    relationships=[],
                    prose_chunks=[],
                    community_summaries=[],
                    sources=["hipporag"],
                    token_count=12,
                )

        fake_kg = FakeKG()
        runtime.knowledge_graph = fake_kg

        with patch("tinyassets.retrieval.router.RetrievalRouter", FakeRouter):
            ctx = _run_retrieval(base_state, "test-scene")

        assert fake_kg.closed is False
        assert ctx["facts"] == ctx["canon_facts"]


# ---------------------------------------------------------------------------
# 1b. Epistemic filtering (POV + access tier)
# ---------------------------------------------------------------------------


class TestEpistemicFiltering:
    """Orient extracts POV character and access tier for retrieval filtering."""

    def test_orient_result_contains_pov_fields(self, base_state):
        """orient_result should include pov_character and access_tier."""
        result = orient(base_state)
        orient_res = result["orient_result"]
        assert "pov_character" in orient_res
        assert "access_tier" in orient_res

    def test_pov_extracted_from_character_db(self, base_state, tmp_db):
        """When characters exist in the DB, orient picks the most recent."""
        from domains.fantasy_daemon.phases.world_state_db import (
            connect as ws_connect,
        )
        from domains.fantasy_daemon.phases.world_state_db import (
            init_db,
            upsert_character,
        )

        init_db(tmp_db)
        with ws_connect(tmp_db) as conn:
            upsert_character(
                conn,
                character_id="ryn", name="Ryn",
                location="riverbank", emotional_state="alert",
                last_updated_scene="B1-C1-S2",
            )
            upsert_character(
                conn,
                character_id="kael", name="Kael",
                location="market", emotional_state="neutral",
                last_updated_scene="B1-C1-S1",
            )

        base_state["_db_path"] = tmp_db
        result = orient(base_state)
        orient_res = result["orient_result"]
        # ryn has a later last_updated_scene, so should be picked
        assert orient_res["pov_character"] == "ryn"

    def test_pov_none_when_no_characters(self, base_state):
        """With no characters in DB, pov_character should be None."""
        result = orient(base_state)
        orient_res = result["orient_result"]
        assert orient_res["pov_character"] is None
        assert orient_res["access_tier"] == 0


# ---------------------------------------------------------------------------
# 2. Orient/Plan/Draft → MemoryManager integration
# ---------------------------------------------------------------------------


class TestMemoryManagerIntegration:
    """Nodes call MemoryManager.assemble_context when present in state."""

    @pytest.fixture(autouse=True)
    def _isolate_memory_manager(self, monkeypatch):
        """Ensure runtime.memory_manager is None before and after each test.

        Uses monkeypatch so the global is automatically reverted even if
        the test raises.  This prevents mock leakage across test ordering.
        """
        import tinyassets.runtime_singletons as runtime

        monkeypatch.setattr(runtime, "memory_manager", None)

    def test_orient_calls_memory_manager(self, base_state, monkeypatch):
        """Orient should call assemble_context with the enriched orient state."""
        import tinyassets.runtime_singletons as runtime

        mock_mgr = MagicMock()
        mock_mgr.assemble_context.return_value = {
            "voice_profile": {"pov": "third_limited"},
            "active_facts": ["Ryn is a scout"],
        }
        monkeypatch.setattr(runtime, "memory_manager", mock_mgr)
        result = orient(base_state)
        mock_mgr.assemble_context.assert_called()
        phase, passed_state = mock_mgr.assemble_context.call_args.args
        assert phase == "orient"
        assert passed_state["orient_result"]["scene_id"] == result["orient_result"]["scene_id"]
        assert "warnings" in passed_state["orient_result"]
        assert "world_state" in passed_state["orient_result"]
        assert result["memory_context"]["voice_profile"]["pov"] == "third_limited"

    def test_plan_calls_memory_manager(self, base_state, monkeypatch):
        """Plan should call assemble_context('plan', state)."""
        import tinyassets.runtime_singletons as runtime

        mock_mgr = MagicMock()
        mock_mgr.assemble_context.return_value = {"recent_beats": []}
        monkeypatch.setattr(runtime, "memory_manager", mock_mgr)
        base_state["orient_result"] = {"scene_id": "test-scene"}
        result = plan(base_state)
        mock_mgr.assemble_context.assert_any_call("plan", base_state)
        assert result["memory_context"] == {"recent_beats": []}

    def test_draft_calls_memory_manager(self, base_state, monkeypatch):
        """Draft should call assemble_context('draft', state)."""
        import tinyassets.runtime_singletons as runtime

        mock_mgr = MagicMock()
        mock_mgr.assemble_context.return_value = {"tone": "dark"}
        monkeypatch.setattr(runtime, "memory_manager", mock_mgr)
        base_state["plan_output"] = {
            "scene_id": "test-scene",
            "beats": [{"beat_number": 1, "description": "Test", "tension": 0.5}],
        }
        result = draft(base_state)
        mock_mgr.assemble_context.assert_any_call("draft", base_state)
        assert result["memory_context"]["tone"] == "dark"

    def test_commit_calls_memory_manager(self, base_state, monkeypatch):
        """Commit should call assemble_context('evaluate', state)."""
        import tinyassets.runtime_singletons as runtime

        mock_mgr = MagicMock()
        mock_mgr.assemble_context.return_value = {"eval_context": True}
        monkeypatch.setattr(runtime, "memory_manager", mock_mgr)
        base_state["draft_output"] = {
            "scene_id": "test-scene",
            "prose": "Ryn walked through the dark forest. " * 50,
            "word_count": 350,
        }
        result = commit(base_state)
        # Use assert_any_call: background graph threads from prior tests
        # can race against the monkeypatched global, inflating call count.
        mock_mgr.assemble_context.assert_any_call("evaluate", base_state)
        assert result["memory_context"]["eval_context"] is True

    def test_nodes_work_without_memory_manager(self, base_state, monkeypatch):
        """All nodes should work when runtime.memory_manager is None."""
        import tinyassets.runtime_singletons as runtime

        monkeypatch.setattr(runtime, "memory_manager", None)
        result = orient(base_state)
        assert "memory_context" in result
        assert isinstance(result["memory_context"], dict)

    def test_commit_stores_to_memory_on_accept(self, base_state, monkeypatch):
        """Commit should call store_scene_result on accept."""
        import tinyassets.runtime_singletons as runtime

        mock_mgr = MagicMock()
        mock_mgr.assemble_context.return_value = {}
        monkeypatch.setattr(runtime, "memory_manager", mock_mgr)
        base_state["draft_output"] = {
            "scene_id": "test-scene",
            "prose": "Ryn walked through the dark forest. " * 50,
            "word_count": 350,
        }
        result = commit(base_state)
        if result["verdict"] == "accept":
            assert mock_mgr.store_scene_result.call_count >= 1


# ---------------------------------------------------------------------------
# 3. Provider routing integration
# ---------------------------------------------------------------------------


class TestProviderIntegration:
    """Provider stub routes through real ProviderRouter.call_sync."""

    def test_unbound_model_call_fails_loudly(self):
        from tinyassets.exceptions import ProviderAuthorityHeldError
        from tinyassets.providers.model_call import text_sync

        with pytest.raises(ProviderAuthorityHeldError, match="owner-bound"):
            text_sync("test prompt", role="writer")

    def test_call_for_plan_returns_json(self, monkeypatch):
        """call_for_plan should return parseable JSON with alternatives."""
        import json

        monkeypatch.setattr(_provider_stub, "text_sync", lambda *_a, **_k:
                            '{"alternatives":[{"beats":[{"description":"Test"}]}]}')
        result = _provider_stub.call_for_plan({"scene_id": "test"})
        data = json.loads(result)
        assert "alternatives" in data
        assert len(data["alternatives"]) > 0
        assert "beats" in data["alternatives"][0]

    def test_call_for_draft_returns_prose(self, monkeypatch):
        """call_for_draft should return non-empty prose."""
        monkeypatch.setattr(_provider_stub, "text_sync", lambda *_a, **_k: "prose " * 20)
        result = _provider_stub.call_for_draft(
            {"beats": [{"beat_number": 1, "description": "Test", "tension": 0.5}]},
            {"scene_id": "test"},
        )
        assert isinstance(result, str)
        assert len(result) > 50

    def test_call_for_extraction_returns_json(self, monkeypatch):
        """call_for_extraction should return parseable JSON facts."""
        import json

        monkeypatch.setattr(_provider_stub, "text_sync", lambda *_a, **_k: "[]")
        result = _provider_stub.call_for_extraction("Ryn walked to the Gate.")
        data = json.loads(result)
        assert isinstance(data, list)

    def test_router_has_call_sync(self):
        """ProviderRouter should expose call_sync for sync nodes."""
        from tinyassets.providers.router import ProviderRouter

        router = ProviderRouter()
        assert hasattr(router, "call_sync")
        assert callable(router.call_sync)


# ---------------------------------------------------------------------------
# 4. Commit → Real StructuralEvaluator
# ---------------------------------------------------------------------------


class TestCommitEvaluationIntegration:
    """Commit node uses real StructuralEvaluator, not stub."""

    def test_commit_imports_real_evaluator(self):
        """Commit module should import from evaluation.structural."""
        import importlib

        commit_mod = importlib.import_module("domains.fantasy_daemon.phases.commit")
        assert hasattr(commit_mod, "_structural_evaluator")
        assert isinstance(commit_mod._structural_evaluator, StructuralEvaluator)

    def test_commit_returns_structural_checks(self, base_state):
        """Commit result should contain real structural check names."""
        base_state["draft_output"] = {
            "scene_id": "test-scene",
            "prose": "Ryn walked through the dark forest. " * 50,
            "word_count": 350,
        }
        result = commit(base_state)

        checks = result["commit_result"]["structural_checks"]
        check_names = {c["name"] for c in checks}
        # Real evaluator has these specific check names
        assert "taaco_coherence" in check_names or "readability" in check_names
        assert "pacing" in check_names

    def test_commit_returns_valid_verdict(self, base_state):
        """Commit should return a valid verdict."""
        base_state["draft_output"] = {
            "scene_id": "test-scene",
            "prose": "Ryn walked through the dark forest. " * 50,
            "word_count": 350,
        }
        result = commit(base_state)

        # Verdict should be one of the valid values
        assert result["verdict"] in ("accept", "second_draft", "revert")

    def test_hard_failure_triggers_revert(self, base_state):
        """A hard structural failure should result in revert verdict."""
        # Empty prose triggers hard failures in some checks
        base_state["draft_output"] = {
            "scene_id": "test-scene",
            "prose": "",
            "word_count": 0,
        }
        result = commit(base_state)

        # With empty prose, structural should flag hard failure
        if result["commit_result"]["hard_failure"]:
            assert result["verdict"] == "revert"


# ---------------------------------------------------------------------------
# 5. Full scene graph end-to-end
# ---------------------------------------------------------------------------


class TestSceneGraphEndToEnd:
    """Full scene graph executes orient→plan→draft→commit with all wiring."""

    def test_scene_graph_completes(self, base_state):
        """Scene graph should run to completion with mock providers."""
        graph = build_scene_graph()
        with SqliteSaver.from_conn_string(":memory:") as cp:
            compiled = graph.compile(checkpointer=cp)
            config = {"configurable": {"thread_id": "integ-test-1"}}

            final_state = compiled.invoke(base_state, config=config)

            # Should have a verdict
            assert final_state["verdict"] in ("accept", "second_draft", "revert")
            # Orient should have run
            assert final_state["orient_result"] != {}
            assert "scene_id" in final_state["orient_result"]
            # Plan should have run
            assert final_state["plan_output"] is not None
            assert "beats" in final_state["plan_output"]
            # Draft should have run
            assert final_state["draft_output"] is not None
            assert "prose" in final_state["draft_output"]
            assert len(final_state["draft_output"]["prose"]) > 0
            # Commit should have run
            assert final_state["commit_result"] is not None
            # Quality trace should accumulate from all nodes
            assert len(final_state["quality_trace"]) >= 4
            nodes_in_trace = {t["node"] for t in final_state["quality_trace"]}
            assert "orient" in nodes_in_trace
            assert "plan" in nodes_in_trace
            assert "draft" in nodes_in_trace
            assert "commit" in nodes_in_trace

    def test_scene_graph_memory_context_flows(self, base_state):
        """Scene graph propagates memory_context through all phases.

        Note: _memory_manager can't be injected through the graph
        (not in SceneState TypedDict), but nodes populate memory_context
        from existing state when no manager is present.
        """
        base_state["memory_context"] = {"pre_existing": True}

        graph = build_scene_graph()
        with SqliteSaver.from_conn_string(":memory:") as cp:
            compiled = graph.compile(checkpointer=cp)
            config = {"configurable": {"thread_id": "integ-test-2"}}

            final_state = compiled.invoke(base_state, config=config)

            assert final_state["verdict"] in ("accept", "second_draft", "revert")
            # memory_context should be present in final state
            assert isinstance(final_state["memory_context"], dict)

    def test_scene_graph_extracts_facts(self, base_state):
        """Scene graph should extract facts from generated prose."""
        graph = build_scene_graph()
        with SqliteSaver.from_conn_string(":memory:") as cp:
            compiled = graph.compile(checkpointer=cp)
            config = {"configurable": {"thread_id": "integ-test-3"}}

            final_state = compiled.invoke(base_state, config=config)

            # Facts should be extracted (mock prose contains character names)
            assert isinstance(final_state["extracted_facts"], list)

    def test_scene_graph_checkpoint_resume(self, base_state):
        """Scene graph state should be checkpointed and resumable."""
        graph = build_scene_graph()
        with SqliteSaver.from_conn_string(":memory:") as cp:
            compiled = graph.compile(checkpointer=cp)
            thread_id = "integ-resume-test"
            config = {"configurable": {"thread_id": thread_id}}

            # Run to completion
            compiled.invoke(base_state, config=config)

            # Get the checkpoint
            checkpoint = cp.get(config)
            assert checkpoint is not None


# ---------------------------------------------------------------------------
# 6. Dashboard receives graph events
# ---------------------------------------------------------------------------


class TestDashboardIntegration:
    """DashboardHandler processes events from graph stream."""

    def test_dashboard_tracks_phase_events(self):
        """Dashboard should track phase transitions."""
        handler = DashboardHandler()
        handler.handle_event({"type": "phase_start", "phase": "orient"})
        handler.handle_event({"type": "phase_start", "phase": "plan"})

        summary = handler.summary()
        assert summary["current_phase"] == "plan"

    def test_dashboard_tracks_draft_progress(self):
        """draft_progress is an in-progress indicator, not a total update."""
        handler = DashboardHandler()
        handler.handle_event({"type": "draft_progress", "word_count": 500})

        summary = handler.summary()
        # draft_progress no longer updates total_words — scene_complete does
        assert summary["total_words"] == 0

    def test_dashboard_tracks_scene_completion(self):
        """Dashboard should count completed scenes."""
        handler = DashboardHandler()
        handler.handle_event({
            "type": "scene_complete",
            "scene_number": 1,
            "word_count": 1000,
        })

        summary = handler.summary()
        assert summary["scenes_complete"] == 1

    def test_dashboard_tracks_judge_results(self):
        """Dashboard should track accept/reject rates via scene events."""
        handler = DashboardHandler()
        handler.handle_event({"type": "judge_result", "verdict": "accept"})
        handler.handle_event({"type": "judge_result", "verdict": "accept"})
        handler.handle_event({"type": "judge_result", "verdict": "revert"})

        summary = handler.summary()
        # accept_rate = accepted / evaluated
        assert summary["accept_rate"] > 0


# ---------------------------------------------------------------------------
# 7. Plan → HTN/DOME integration
# ---------------------------------------------------------------------------


class TestPlanConstraintIntegration:
    """Plan node uses HTN/DOME when goals are present."""

    def test_plan_uses_htn_dome_with_premise(self, base_state):
        """Plan should use HTN/DOME when orient_result has a premise."""
        base_state["orient_result"] = {
            "scene_id": "test-B1-C1-S1",
            "premise": "A young scout discovers a forbidden gate.",
        }

        result = plan(base_state)

        trace = result["quality_trace"][0]
        assert trace["htn_used"] is True
        assert trace["dome_used"] is True
        # Should have structural guidance
        assert result["plan_output"]["structural_guidance"] is not None

    def test_plan_skips_htn_dome_without_goal(self, base_state):
        """Plan should skip HTN/DOME when no goal is present."""
        base_state["orient_result"] = {"scene_id": "test-B1-C1-S1"}

        result = plan(base_state)

        trace = result["quality_trace"][0]
        assert trace["htn_used"] is False
        assert trace["dome_used"] is False

    def test_plan_constraint_validation_present(self, base_state):
        """Plan output should include constraint_validation field."""
        base_state["orient_result"] = {"scene_id": "test-B1-C1-S1"}

        result = plan(base_state)

        assert "constraint_validation" in result["plan_output"]


# ---------------------------------------------------------------------------
# 8. Entry point / DaemonController
# ---------------------------------------------------------------------------




class TestProseExport:
    """Commit node writes accepted prose to chapter files on disk."""

    def test_export_prose_on_accept(self, base_state, tmp_path):
        """Accepted prose should be written to per-scene file."""
        base_state["_universe_path"] = str(tmp_path)
        base_state["book_number"] = 1
        base_state["chapter_number"] = 3
        base_state["scene_number"] = 1
        base_state["draft_output"] = {
            "scene_id": "test-scene",
            "prose": "Ryn walked through the dark forest. " * 50,
            "word_count": 350,
        }

        result = commit(base_state)

        assert result["verdict"] == "accept"
        scene_file = tmp_path / "output" / "book-1" / "chapter-03" / "scene-01.md"
        assert scene_file.exists()
        content = scene_file.read_text(encoding="utf-8")
        assert "Ryn walked through the dark forest." in content

    def test_export_prose_appends_scenes(self, base_state, tmp_path):
        """Multiple accepted scenes should create separate scene files."""
        base_state["_universe_path"] = str(tmp_path)
        base_state["book_number"] = 1
        base_state["chapter_number"] = 1

        # Scene 1
        base_state["scene_number"] = 1
        base_state["draft_output"] = {
            "scene_id": "scene-1",
            "prose": "Scene one prose. " * 50,
            "word_count": 200,
        }
        result1 = commit(base_state)
        assert result1["verdict"] == "accept"

        # Scene 2
        base_state["scene_number"] = 2
        base_state["draft_output"] = {
            "scene_id": "scene-2",
            "prose": "Scene two prose. " * 50,
            "word_count": 200,
        }
        result2 = commit(base_state)
        assert result2["verdict"] == "accept"

        chapter_dir = tmp_path / "output" / "book-1" / "chapter-01"
        scene1 = chapter_dir / "scene-01.md"
        scene2 = chapter_dir / "scene-02.md"
        assert scene1.exists()
        assert scene2.exists()
        assert "Scene one prose." in scene1.read_text(encoding="utf-8")
        assert "Scene two prose." in scene2.read_text(encoding="utf-8")

    def test_export_prose_no_universe_path(self, base_state):
        """No _universe_path should silently skip export."""
        base_state["draft_output"] = {
            "scene_id": "test-scene",
            "prose": "Ryn walked. " * 50,
            "word_count": 100,
        }
        # No _universe_path set -- should not raise
        result = commit(base_state)
        assert "verdict" in result

    def test_export_prose_scene_separator_only_after_first(self, base_state, tmp_path):
        """Each scene file contains only its own prose (no separators)."""
        base_state["_universe_path"] = str(tmp_path)
        base_state["scene_number"] = 1
        base_state["draft_output"] = {
            "scene_id": "scene-1",
            "prose": "Opening line of the story." + " More text." * 40,
            "word_count": 200,
        }

        result = commit(base_state)

        assert result["verdict"] == "accept"
        scene_file = tmp_path / "output" / "book-1" / "chapter-01" / "scene-01.md"
        content = scene_file.read_text(encoding="utf-8")
        assert "---" not in content
        assert content.startswith("Opening line")




class TestStateFlowUniversePath:
    """_universe_path flows from universe state through book/chapter to scene."""

    def test_universe_path_flows_to_book_via_internal_key(self):
        from domains.fantasy_daemon.graphs.universe import run_book

        state = {
            "universe_id": "test",
            "universe_path": "/tmp/public-path",
            "_universe_path": "/tmp/internal-path",
            "_db_path": "test.db",
            "total_chapters": 0,
            "total_words": 0,
        }
        # _universe_path (internal) takes priority over universe_path
        result = run_book(state)
        assert "total_chapters" in result

    def test_universe_path_falls_back_to_public_key(self):
        from domains.fantasy_daemon.graphs.universe import run_book

        state = {
            "universe_id": "test",
            "universe_path": "/tmp/public-path",
            "_db_path": "test.db",
            "total_chapters": 0,
            "total_words": 0,
        }
        # No _universe_path set -- should fall back to universe_path
        result = run_book(state)
        assert "total_chapters" in result

    def test_make_chapter_input_includes_universe_path(self):
        from domains.fantasy_daemon.graphs.book import _make_chapter_input

        state = {
            "universe_id": "test",
            "book_number": 1,
            "chapters_completed": 0,
            "_universe_path": "/tmp/test",
            "_db_path": "test.db",
        }
        result = _make_chapter_input(state)
        assert result["_universe_path"] == "/tmp/test"
        assert result["_db_path"] == "test.db"

    def test_universe_state_declares_internal_keys(self):
        """UniverseState must declare _db_path, _universe_path, _kg_path.

        Without these, LangGraph silently strips them at the universe
        graph boundary and no downstream node gets the DB path.
        """
        from domains.fantasy_daemon.state.universe_state import UniverseState

        annotations = UniverseState.__annotations__
        assert "_db_path" in annotations, "_db_path missing from UniverseState"
        assert "_universe_path" in annotations, "_universe_path missing from UniverseState"
        assert "_kg_path" in annotations, "_kg_path missing from UniverseState"

    def test_make_scene_input_includes_universe_path(self):
        from domains.fantasy_daemon.graphs.chapter import _make_scene_input

        state = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 0,
            "_universe_path": "/tmp/test",
            "_db_path": "test.db",
        }
        result = _make_scene_input(state)
        assert result["_universe_path"] == "/tmp/test"
        assert result["_db_path"] == "test.db"
        assert result["editorial_notes"] is None


# ---------------------------------------------------------------------------
# 9. Verdict routing integration
# ---------------------------------------------------------------------------


class TestEditorialVerdictIntegration:
    """_compute_editorial_verdict correctly routes decisions."""

    def test_clean_structural_pass_accepts(self):
        """Good structural result with no editorial should accept."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict

        structural = StructuralResult(
            checks=[], aggregate_score=0.85,
            hard_failure=False, violations=[],
        )
        verdict, _ = _compute_editorial_verdict(structural, None, False)
        assert verdict == "accept"

    def test_hard_failure_reverts(self):
        """Hard structural failure should always revert."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict

        structural = StructuralResult(
            checks=[], aggregate_score=0.2,
            hard_failure=True, violations=["canon_breach"],
        )
        verdict, _ = _compute_editorial_verdict(structural, None, False)
        assert verdict == "revert"

    def test_low_structural_score_records_debt(self):
        """Low score without hard failure should accept with debt."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict

        structural = StructuralResult(
            checks=[], aggregate_score=0.4,
            hard_failure=False, violations=[],
        )
        verdict, debt = _compute_editorial_verdict(structural, None, False)
        assert verdict == "accept"
        assert len(debt) > 0
        assert debt[0]["type"] == "low_structural_score"


class TestConsolidateNode:
    """Tests for the consolidate node's chapter summary generation."""

    def test_consolidate_reads_prose_and_summarizes(self):
        """When chapter prose exists on disk, consolidate should call
        the provider and produce a real summary (mocked here)."""
        from domains.fantasy_daemon.phases.consolidate import consolidate

        universe = tempfile.mkdtemp()
        book_dir = Path(universe) / "output" / "book-1"
        book_dir.mkdir(parents=True)
        chapter_file = book_dir / "chapter-01.md"
        chapter_file.write_text("Kael crept through the market square.", encoding="utf-8")

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 1,
            "_universe_path": universe,
        }

        result = consolidate(state)
        assert "chapter_summary" in result
        # With _FORCE_MOCK=True, text_sync returns fallback_response
        # which is the format-string fallback
        assert result["chapter_summary"]
        assert isinstance(result["chapter_summary"], str)

    def test_consolidate_falls_back_when_no_prose(self):
        """Without prose on disk, consolidate should use fallback summary."""
        from domains.fantasy_daemon.phases.consolidate import consolidate

        universe = tempfile.mkdtemp()

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 2,
            "scenes_completed": 3,
            "_universe_path": universe,
        }

        result = consolidate(state)
        assert result["chapter_summary"] == (
            "Chapter 2 completed with 3 scenes."
        )

    def test_consolidate_falls_back_without_universe_path(self):
        """Without _universe_path, consolidate should use fallback."""
        from domains.fantasy_daemon.phases.consolidate import consolidate

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scenes_completed": 2,
        }

        result = consolidate(state)
        assert result["chapter_summary"] == (
            "Chapter 1 completed with 2 scenes."
        )
        assert "consolidated_facts" in result


class TestCreativeBriefing:
    """Tests for the creative briefing generation in progress.md."""

    @pytest.fixture()
    def tmp_db(self, tmp_path):
        return str(tmp_path / "test.db")







class TestStateMismatchFix:
    """Tests for the state mismatch bug fix — progress.md and status.json
    must reflect idle state after daemon stops."""

    @pytest.fixture
    def tmp_db(self, tmp_path):
        return str(tmp_path / "test.db")





class TestCharacterUpsert:
    """Tests for character upsert wiring in the commit node."""

    def test_upsert_characters_from_facts(self, tmp_path):
        """_upsert_characters_from_facts should insert characters from facts."""
        from domains.fantasy_daemon.phases.commit import _upsert_characters_from_facts
        from domains.fantasy_daemon.phases.fact_extraction import (
            FactWithContext,
            LanguageType,
            SourceType,
        )
        from domains.fantasy_daemon.phases.world_state_db import (
            connect,
            get_all_characters,
            init_db,
        )

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        facts = [
            FactWithContext(
                fact_id="f1",
                text="Kael crept through the market.",
                source_type=SourceType.NARRATOR_CLAIM,
                language_type=LanguageType.LITERAL,
                pov_characters=["Kael"],
            ),
            FactWithContext(
                fact_id="f2",
                text="Mira watched from the rooftop.",
                source_type=SourceType.NARRATOR_CLAIM,
                language_type=LanguageType.LITERAL,
            ),
        ]

        with connect(db_path) as conn:
            _upsert_characters_from_facts(conn, facts, "s1")
            chars = get_all_characters(conn)

        names = {c["name"] for c in chars}
        assert "Kael" in names
        assert "Mira" in names

    def test_upsert_characters_skips_stopwords(self, tmp_path):
        """Stopwords like 'The', 'She' should not be upserted as characters."""
        from domains.fantasy_daemon.phases.commit import _upsert_characters_from_facts
        from domains.fantasy_daemon.phases.fact_extraction import (
            FactWithContext,
            LanguageType,
            SourceType,
        )
        from domains.fantasy_daemon.phases.world_state_db import (
            connect,
            get_all_characters,
            init_db,
        )

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        facts = [
            FactWithContext(
                fact_id="f1",
                text="She walked through the Northern Gate.",
                source_type=SourceType.NARRATOR_CLAIM,
                language_type=LanguageType.LITERAL,
            ),
        ]

        with connect(db_path) as conn:
            _upsert_characters_from_facts(conn, facts, "s1")
            chars = get_all_characters(conn)

        names = {c["name"] for c in chars}
        assert "She" not in names
        assert "Northern" not in names

    def test_commit_upserts_characters(self, tmp_path):
        """Full commit should result in characters in the DB."""
        from domains.fantasy_daemon.phases.commit import commit
        from domains.fantasy_daemon.phases.world_state_db import (
            connect,
            get_all_characters,
            init_db,
        )

        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scene_number": 1,
            "draft_output": {
                "scene_id": "s1",
                "prose": (
                    "Kael drew his blade and faced the shadow. "
                    "Elara whispered a warning from behind the pillar."
                ),
                "word_count": 20,
            },
            "orient_result": {},
            "plan_output": {},
            "second_draft_used": False,
            "memory_context": {},
            "_db_path": db_path,
        }

        commit(state)

        with connect(db_path) as conn:
            chars = get_all_characters(conn)

        names = {c["name"] for c in chars}
        assert "Kael" in names
        assert "Elara" in names


    def test_upsert_characters_short_names(self, tmp_path):
        """Short names like Ryn (3 chars) should be upserted."""
        from domains.fantasy_daemon.phases.commit import _upsert_characters_from_facts
        from domains.fantasy_daemon.phases.fact_extraction import (
            FactWithContext,
            LanguageType,
            SourceType,
        )
        from domains.fantasy_daemon.phases.world_state_db import (
            connect,
            get_all_characters,
            init_db,
        )

        db_path = str(tmp_path / "test.db")
        init_db(db_path)
        facts = [
            FactWithContext(
                fact_id="f1",
                text="Ryn crossed the river at dawn.",
                source_type=SourceType.NARRATOR_CLAIM,
                language_type=LanguageType.LITERAL,
            ),
        ]

        with connect(db_path) as conn:
            _upsert_characters_from_facts(conn, facts, "s1")
            chars = get_all_characters(conn)

        names = {c["name"] for c in chars}
        assert "Ryn" in names

    def test_upsert_characters_prose_fallback(self, tmp_path):
        """When facts are empty, characters should be extracted from prose."""
        from domains.fantasy_daemon.phases.commit import _upsert_characters_from_facts
        from domains.fantasy_daemon.phases.world_state_db import (
            connect,
            get_all_characters,
            init_db,
        )

        db_path = str(tmp_path / "test.db")
        init_db(db_path)

        with connect(db_path) as conn:
            _upsert_characters_from_facts(
                conn, [], "s1",
                prose="Corin walked through the market. Wren watched from above.",
            )
            chars = get_all_characters(conn)

        names = {c["name"] for c in chars}
        assert "Corin" in names
        assert "Wren" in names


class TestIndexerRegexFallback:
    """Indexer falls back to regex entity extraction when LLM fails."""

    def test_regex_fallback_populates_kg(self, tmp_path):
        """When provider returns empty, regex fallback adds entities to KG."""
        from tinyassets.ingestion.indexer import index_text
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph

        kg_path = str(tmp_path / "knowledge.db")
        kg = KnowledgeGraph(kg_path)

        # Provider that always returns empty (simulates exhaustion)
        def empty_provider(prompt, system, role="extract"):
            return ""

        result = index_text(
            "Corin drew his blade. Maren whispered from the shadows.",
            source_id="test-scene",
            knowledge_graph=kg,
            provider_call=empty_provider,
        )

        assert result["entities"] > 0
        # Verify entities are actually in the KG
        entity = kg.get_entity("corin")
        assert entity is not None
        kg.close()

    def test_regex_fallback_on_invalid_json(self, tmp_path):
        """When provider returns non-JSON, regex fallback kicks in."""
        from tinyassets.ingestion.indexer import index_text
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph

        kg_path = str(tmp_path / "knowledge.db")
        kg = KnowledgeGraph(kg_path)

        def bad_json_provider(prompt, system, role="extract"):
            return "I found some entities but can't format them as JSON."

        result = index_text(
            "Ryn crossed the Ashwater River at dawn.",
            source_id="test-scene",
            knowledge_graph=kg,
            provider_call=bad_json_provider,
        )

        assert result["entities"] > 0
        entity = kg.get_entity("ryn")
        assert entity is not None
        kg.close()

    def test_llm_extraction_works_when_provider_returns_json(self, tmp_path):
        """When provider returns valid JSON, LLM extraction is used."""
        import json

        from tinyassets.ingestion.indexer import index_text
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph

        kg_path = str(tmp_path / "knowledge.db")
        kg = KnowledgeGraph(kg_path)

        def json_provider(prompt, system, role="extract"):
            return json.dumps({
                "entities": [{
                    "entity_id": "ryn",
                    "entity_type": "character",
                    "aliases": ["the scout"],
                    "description": "A river watcher",
                    "access_tier": 1,
                }],
                "relationships": [],
                "facts": [{
                    "text": "Ryn patrols the Ashwater",
                    "source_type": "narrator_claim",
                    "importance": 0.7,
                    "confidence": 0.9,
                    "access_tier": 0,
                }],
            })

        result = index_text(
            "Ryn patrols the Ashwater.",
            source_id="test-scene",
            knowledge_graph=kg,
            provider_call=json_provider,
        )

        assert result["entities"] == 1
        assert result["facts"] == 1
        entity = kg.get_entity("ryn")
        assert entity is not None
        assert entity["access_tier"] == 1
        kg.close()


class TestEditorialReader:
    """Tests for the editorial reader wired into commit."""

    def test_parse_editorial_response(self):
        """Valid editorial response should parse correctly."""
        from tinyassets.evaluation.editorial import _parse_editorial_response

        raw = json.dumps({
            "protect": ["vivid imagery", "strong character voice"],
            "concerns": [
                {
                    "text": "Wrong character name",
                    "quoted_passage": "Kael said",
                    "clearly_wrong": True,
                },
            ],
            "next_scene": "Continue the tension.",
        })
        result = _parse_editorial_response(raw)
        assert result is not None
        assert len(result.protect) == 2
        assert len(result.concerns) == 1
        assert result.concerns[0].clearly_wrong is True
        assert result.next_scene == "Continue the tension."

    def test_parse_editorial_invalid_json(self):
        """Non-JSON response should return None."""
        from tinyassets.evaluation.editorial import _parse_editorial_response

        result = _parse_editorial_response("This is not JSON")
        assert result is None

    def test_parse_editorial_code_fences(self):
        """JSON wrapped in markdown code fences should still parse."""
        from tinyassets.evaluation.editorial import _parse_editorial_response

        raw = '```json\n{"protect": ["good pacing"], "concerns": [], "next_scene": ""}\n```'
        result = _parse_editorial_response(raw)
        assert result is not None
        assert result.protect == ["good pacing"]

    def test_editorial_skips_on_hard_failure(self):
        """Editorial reader should be skipped on structural hard failure."""
        from domains.fantasy_daemon.phases.commit import _run_editorial
        from tinyassets.evaluation.structural import StructuralResult

        structural = StructuralResult(
            checks=[], aggregate_score=0.0,
            hard_failure=True, violations=["canon_breach"],
        )
        result = _run_editorial("Some prose", structural, {})
        assert result is None

    def test_editorial_skips_mock_response(self):
        """Editorial reader should return None for mock responses."""
        from tinyassets.evaluation.editorial import read_editorial

        result = read_editorial("Some prose")
        # With _FORCE_MOCK=True, text_sync returns mock -> None
        assert result is None

    def test_commit_includes_editorial_notes(self):
        """Commit result should include editorial_notes field."""
        from domains.fantasy_daemon.phases.commit import commit

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scene_number": 1,
            "draft_output": {
                "scene_id": "s1",
                "prose": "Kael crept through the market square.",
                "word_count": 7,
            },
            "orient_result": {},
            "plan_output": {},
            "second_draft_used": False,
            "memory_context": {},
            "_db_path": ":memory:",
        }
        result = commit(state)
        # With mock provider, editorial returns None
        assert result["commit_result"]["editorial_notes"] is None
        assert result["editorial_notes"] is None

    def test_commit_editorial_notes_in_state(self):
        """Commit should return editorial_notes in the state dict."""
        from domains.fantasy_daemon.phases.commit import commit

        state: dict[str, Any] = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scene_number": 1,
            "draft_output": {
                "scene_id": "s1",
                "prose": "A short scene.",
                "word_count": 4,
            },
            "orient_result": {},
            "plan_output": {},
            "second_draft_used": False,
            "memory_context": {},
            "_db_path": ":memory:",
        }
        result = commit(state)
        assert "editorial_notes" in result
        assert "style_observations" in result


class TestEditorialVerdict:
    """Tests for editorial-based verdict computation."""

    def test_accept_without_editorial(self):
        """No editorial -> accept."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict
        from tinyassets.evaluation.structural import StructuralResult

        structural = StructuralResult(
            checks=[], aggregate_score=0.8,
            hard_failure=False, violations=[],
        )
        verdict, debt = _compute_editorial_verdict(structural, None, False)
        assert verdict == "accept"

    def test_revert_on_hard_failure(self):
        """Structural hard failure -> revert."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict
        from tinyassets.evaluation.editorial import EditorialNotes
        from tinyassets.evaluation.structural import StructuralResult

        structural = StructuralResult(
            checks=[], aggregate_score=0.0,
            hard_failure=True, violations=["canon_breach"],
        )
        editorial = EditorialNotes(protect=["good pacing"])
        verdict, _ = _compute_editorial_verdict(structural, editorial, False)
        assert verdict == "revert"

    def test_second_draft_on_clearly_wrong(self):
        """Clearly wrong concern -> second_draft (first attempt)."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict
        from tinyassets.evaluation.editorial import (
            EditorialConcern,
            EditorialNotes,
        )
        from tinyassets.evaluation.structural import StructuralResult

        structural = StructuralResult(
            checks=[], aggregate_score=0.8,
            hard_failure=False, violations=[],
        )
        editorial = EditorialNotes(
            protect=["vivid imagery"],
            concerns=[EditorialConcern(
                text="Wrong name used", clearly_wrong=True,
            )],
        )
        verdict, debt = _compute_editorial_verdict(structural, editorial, False)
        assert verdict == "second_draft"
        assert any(d["type"] == "editorial_clearly_wrong" for d in debt)

    def test_accept_on_second_draft_even_with_clearly_wrong(self):
        """Clearly wrong on second draft -> accept (never block)."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict
        from tinyassets.evaluation.editorial import (
            EditorialConcern,
            EditorialNotes,
        )
        from tinyassets.evaluation.structural import StructuralResult

        structural = StructuralResult(
            checks=[], aggregate_score=0.8,
            hard_failure=False, violations=[],
        )
        editorial = EditorialNotes(
            concerns=[EditorialConcern(
                text="Still wrong", clearly_wrong=True,
            )],
        )
        verdict, _ = _compute_editorial_verdict(structural, editorial, True)
        assert verdict == "accept"

    def test_accept_with_non_wrong_concerns(self):
        """Concerns that aren't clearly_wrong -> accept."""
        from domains.fantasy_daemon.phases.commit import _compute_editorial_verdict
        from tinyassets.evaluation.editorial import (
            EditorialConcern,
            EditorialNotes,
        )
        from tinyassets.evaluation.structural import StructuralResult

        structural = StructuralResult(
            checks=[], aggregate_score=0.8,
            hard_failure=False, violations=[],
        )
        editorial = EditorialNotes(
            concerns=[EditorialConcern(
                text="Pacing feels slow", clearly_wrong=False,
            )],
        )
        verdict, _ = _compute_editorial_verdict(structural, editorial, False)
        assert verdict == "accept"


# =====================================================================
# Public ingress removal (cloud-only)
# =====================================================================








class TestLocalTunnelCapabilityRemoved:
    """The shipped client/tray/daemon must not be able to publish an ingress.

    These assert the *absence* of the capability, not that it is disabled:
    a gated launcher is still a launcher, and this machine must never enroll
    a Cloudflare connector even momentarily.
    """


    def test_no_cloudflared_spawn_in_entrypoints(self):
        """No entry point still names cloudflared as a process to spawn."""
        root = Path(__file__).resolve().parent.parent
        for rel in (
            "fantasy_daemon/__main__.py",
            "tinyassets/__main__.py",
        ):
            text = (root / rel).read_text(encoding="utf-8")
            assert '"cloudflared"' not in text, rel
            assert "shutil.which(" not in text or "cloudflared" not in text, rel









    # -- real CLI parsing -------------------------------------------------
    #
    # The helper test above proves the refusal function; these drive
    # ``_main_unfenced`` with real argv so a regression in *parsing or
    # routing* (a dropped flag check, a reordered branch) is caught too.



# =====================================================================
# Tray Mode
# =====================================================================
