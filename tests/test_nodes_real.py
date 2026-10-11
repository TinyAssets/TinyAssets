"""Tests for the real Phase 1 node implementations (orient, plan, draft, commit).

Model calls use an explicit test transport. Tests verify the deterministic logic, state threading,
and graceful fallback behavior of each node.
"""

from __future__ import annotations

import importlib
import tempfile
from pathlib import Path
from typing import Any

import pytest

from domains.fantasy_daemon.phases.commit import commit
from domains.fantasy_daemon.phases.draft import draft
from domains.fantasy_daemon.phases.orient import _estimate_arc_position, orient
from domains.fantasy_daemon.phases.plan import (
    _default_plan,
    _parse_plan_response,
    _parse_tension,
    _score_alternative,
    plan,
)
from tests.support.fantasy_model import answer as fantasy_answer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fantasy_model_transport(monkeypatch):
    from domains.fantasy_daemon.phases import _provider_stub

    monkeypatch.setattr(_provider_stub, "text_sync", fantasy_answer)
    monkeypatch.setattr(importlib.import_module("domains.fantasy_daemon.phases.commit"),
                        "text_sync", fantasy_answer)
    monkeypatch.setattr(importlib.import_module("domains.fantasy_daemon.phases.consolidate"),
                        "text_sync", fantasy_answer)


def _base_state(tmp_story_db: str, **overrides: Any) -> dict[str, Any]:
    """Build a minimal valid SceneState for testing."""
    state: dict[str, Any] = {
        "universe_id": "test-universe",
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
        "_db_path": tmp_story_db,
        "_kg_path": "",
    }
    state.update(overrides)
    return state


# ---------------------------------------------------------------------------
# Orient node
# ---------------------------------------------------------------------------


class TestOrientNode:
    def test_basic_orient(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        result = orient(state)

        assert "orient_result" in result
        assert "quality_trace" in result
        orient_result = result["orient_result"]
        assert orient_result["scene_id"] == "test-universe-B1-C1-S1"
        assert isinstance(orient_result["overdue_promises"], list)
        assert isinstance(orient_result["pacing_flags"], list)
        assert isinstance(orient_result["character_gaps"], list)

    def test_orient_includes_arc_position(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        result = orient(state)
        assert "arc_position" in result["orient_result"]

    def test_orient_populates_downstream_contract_fields(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        result = orient(state)
        orient_result = result["orient_result"]
        assert "warnings" in orient_result
        assert "characters" in orient_result
        assert "world_state" in orient_result
        assert "chapter_avg_words" in orient_result
        assert orient_result["world_state"]["chapter_number"] == 1
        assert result["search_context"]["phase"] == "orient"

    def test_orient_reads_recent_prose(self, tmp_story_db):
        state = _base_state(tmp_story_db, recent_prose="The wind howled.")
        result = orient(state)
        assert result["orient_result"]["recent_prose"] == "The wind howled."

    def test_orient_quality_trace_format(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        result = orient(state)
        trace = result["quality_trace"]
        assert len(trace) == 1
        assert trace[0]["node"] == "orient"
        assert trace[0]["action"] == "orient_real"
        assert "warnings_count" in trace[0]

    def test_orient_with_promises_in_db(self, tmp_story_db):
        from domains.fantasy_daemon.phases.world_state_db import add_promise, connect, init_db

        init_db(tmp_story_db)
        with connect(tmp_story_db) as conn:
            add_promise(
                conn, promise_id="p1", text="The sword will shatter.",
                created_scene="s0", created_chapter=1, importance=0.9,
            )
        state = _base_state(tmp_story_db, chapter_number=10)
        result = orient(state)
        assert len(result["orient_result"]["overdue_promises"]) >= 1

    def test_orient_with_character_gaps(self, tmp_story_db):
        from domains.fantasy_daemon.phases.world_state_db import connect, init_db, upsert_character

        init_db(tmp_story_db)
        with connect(tmp_story_db) as conn:
            upsert_character(
                conn, character_id="ryn", name="Ryn",
                location="unknown",
            )
        state = _base_state(tmp_story_db)
        result = orient(state)
        assert len(result["orient_result"]["character_gaps"]) >= 1

    def test_orient_canon_context_with_dir(self, tmp_story_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            canon_dir = Path(tmpdir) / "canon"
            canon_dir.mkdir()
            (canon_dir / "world.md").write_text("The world of Ashwater.", encoding="utf-8")
            state = _base_state(tmp_story_db, _universe_path=tmpdir)
            result = orient(state)
            assert "Ashwater" in result["orient_result"]["canon_context"]

    def test_orient_no_canon_dir(self, tmp_story_db):
        with tempfile.TemporaryDirectory() as tmpdir:
            state = _base_state(tmp_story_db, _universe_path=tmpdir)
            result = orient(state)
            assert result["orient_result"]["canon_context"] == ""



# ---------------------------------------------------------------------------
# Arc position estimation
# ---------------------------------------------------------------------------


class TestArcPosition:
    def test_setup_early(self):
        assert _estimate_arc_position(1, 1, 20) == "setup"

    def test_rising_action(self):
        assert _estimate_arc_position(4, 1, 20) == "rising_action"

    def test_midpoint(self):
        assert _estimate_arc_position(7, 1, 20) == "midpoint"

    def test_climax(self):
        assert _estimate_arc_position(14, 1, 20) == "climax"

    def test_resolution(self):
        assert _estimate_arc_position(19, 1, 20) == "resolution"

    def test_default_target(self):
        # No target -> defaults to 20
        assert _estimate_arc_position(1, 1) == "setup"


# ---------------------------------------------------------------------------
# Plan node
# ---------------------------------------------------------------------------


class TestPlanNode:
    def test_basic_plan(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        # Run orient first
        orient_result = orient(state)
        state.update(orient_result)

        result = plan(state)
        assert "plan_output" in result
        assert "quality_trace" in result
        plan_output = result["plan_output"]
        assert "beats" in plan_output
        assert len(plan_output["beats"]) >= 1
        assert "done_when" in plan_output

    def test_plan_quality_trace(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        result = plan(state)
        trace = result["quality_trace"]
        assert len(trace) == 1
        assert trace[0]["node"] == "plan"
        assert trace[0]["action"] == "plan_real"
        assert result["search_context"]["phase"] == "plan"
        assert "search_sources" in trace[0]

    def test_plan_scores_alternatives(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        result = plan(state)
        assert result["plan_output"]["best_score"] > 0.0


class TestPlanParsing:
    def test_parse_json_alternatives(self):
        raw = (
            '{"alternatives": [{"beats": [{"beat_number": 1,'
            ' "description": "X", "tension": 0.5}]}]}'
        )
        alts = _parse_plan_response(raw, {})
        assert len(alts) == 1

    def test_parse_json_array(self):
        raw = '[{"beats": [{"beat_number": 1}]}]'
        alts = _parse_plan_response(raw, {})
        assert len(alts) == 1

    def test_parse_garbage_returns_default(self):
        alts = _parse_plan_response("total garbage text", {"scene_id": "s1"})
        assert len(alts) == 1
        assert alts[0]["beats"]  # Default plan has beats


class TestTensionParsing:
    def test_numeric(self):
        assert _parse_tension(0.8) == 0.8
        assert _parse_tension(1) == 1.0

    def test_string_number(self):
        assert _parse_tension("0.75") == 0.75

    def test_word(self):
        assert _parse_tension("high") == 0.8
        assert _parse_tension("climax") == 0.9
        assert _parse_tension("low") == 0.2

    def test_unknown_word(self):
        assert _parse_tension("bizarre") == 0.5

    def test_none(self):
        assert _parse_tension(None) == 0.5


class TestScoring:
    def test_ideal_plan_scores_high(self):
        alt = {
            "beats": [
                {"beat_number": 1, "tension": 0.3},
                {"beat_number": 2, "tension": 0.6},
                {"beat_number": 3, "tension": 0.9},
            ],
            "promise_resolutions": [],
        }
        orient_result = {"overdue_promises": []}
        score = _score_alternative(alt, orient_result)
        assert score >= 0.8

    def test_flat_tension_scores_lower(self):
        alt = {
            "beats": [
                {"beat_number": 1, "tension": 0.5},
                {"beat_number": 2, "tension": 0.5},
            ],
            "promise_resolutions": [],
        }
        orient_result = {"overdue_promises": []}
        score = _score_alternative(alt, orient_result)
        # Flat tension = no rise, possibly no peak
        assert score < 0.9

    def test_default_plan_valid(self):
        p = _default_plan("s1")
        assert "beats" in p
        assert "done_when" in p
        assert len(p["beats"]) == 3


# ---------------------------------------------------------------------------
# Draft node
# ---------------------------------------------------------------------------


class TestDraftNode:
    def test_basic_draft(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))

        result = draft(state)
        assert "draft_output" in result
        assert "quality_trace" in result
        assert result["search_context"]["phase"] == "draft"
        draft_output = result["draft_output"]
        assert "prose" in draft_output
        assert draft_output["word_count"] > 0
        assert draft_output["is_revision"] is False

    def test_draft_voice_decisions(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        result = draft(state)
        voice = result["draft_output"]["voice_decisions"]
        assert "pov" in voice
        assert "tense" in voice
        assert "tone" in voice

    def test_draft_revision_mode(self, tmp_story_db):
        state = _base_state(tmp_story_db, second_draft_used=True)
        state.update(orient(state))
        state.update(plan(state))
        state["commit_result"] = {
            "structural_checks": [],
            "warnings": ["Too short"],
            "overall_score": 0.4,
            "editorial_notes": {
                "protect": [],
                "concerns": [
                    {"text": "Needs more depth", "clearly_wrong": True, "quoted_passage": ""},
                ],
                "next_scene": "",
            },
        }
        result = draft(state)
        assert result["draft_output"]["is_revision"] is True

    def test_draft_quality_trace(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        result = draft(state)
        trace = result["quality_trace"]
        assert trace[0]["node"] == "draft"
        assert trace[0]["action"] == "draft_real"
        assert trace[0]["word_count"] > 0
        assert "writer_tools" in trace[0]
        assert "search_sources" in trace[0]

    def test_draft_empty_prose_short_circuits(self, tmp_story_db):
        """When provider returns empty string, draft flags provider_failed."""
        from unittest.mock import patch

        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        with patch(
            "domains.fantasy_daemon.phases._provider_stub.call_for_draft",
            return_value="",
        ):
            result = draft(state)
        assert result["draft_output"]["provider_failed"] is True
        assert result["draft_output"]["word_count"] == 0
        assert result["quality_trace"][0]["action"] == "draft_provider_exhausted"


# ---------------------------------------------------------------------------
# Commit node
# ---------------------------------------------------------------------------


class TestCommitNode:
    def test_basic_commit(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        state.update(draft(state))

        result = commit(state)
        assert "verdict" in result
        assert result["verdict"] in ("accept", "second_draft", "revert")
        assert "commit_result" in result
        assert "extracted_facts" in result
        assert "extracted_promises" in result

    def test_commit_quality_trace(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        state.update(draft(state))
        result = commit(state)
        trace = result["quality_trace"]
        assert trace[0]["node"] == "commit"
        assert trace[0]["action"] == "commit_real"
        assert "facts_extracted" in trace[0]
        assert "promises_detected" in trace[0]
        assert "process_score" in trace[0]
        assert "process_failures" in trace[0]

    def test_commit_updates_world_state(self, tmp_story_db):
        from domains.fantasy_daemon.phases.world_state_db import connect, get_recent_scenes, init_db

        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        state.update(draft(state))
        commit(state)

        init_db(tmp_story_db)
        with connect(tmp_story_db) as conn:
            scenes = get_recent_scenes(conn, 1)
        assert len(scenes) >= 1

    def test_commit_second_draft_flag(self, tmp_story_db):
        state = _base_state(tmp_story_db, second_draft_used=True)
        state.update(orient(state))
        state.update(plan(state))
        state.update(draft(state))
        result = commit(state)
        assert result["second_draft_used"] is True

    def test_commit_structural_result(self, tmp_story_db):
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        state.update(draft(state))
        result = commit(state)
        cr = result["commit_result"]
        assert "structural_score" in cr
        assert "structural_checks" in cr
        assert isinstance(cr["structural_checks"], list)
        assert "process_evaluation" in cr
        assert "aggregate_score" in cr["process_evaluation"]

    def test_commit_reverts_on_provider_failed(self, tmp_story_db):
        """When draft flags provider_failed, commit reverts immediately."""
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        state["draft_output"] = {
            "scene_id": "test-universe-B1-C1-S1",
            "prose": "",
            "word_count": 0,
            "is_revision": False,
            "voice_decisions": {},
            "provider_failed": True,
        }
        result = commit(state)
        assert result["verdict"] == "revert"
        assert result["commit_result"]["provider_failed"] is True
        assert "process_evaluation" in result["commit_result"]
        assert "aggregate_score" in result["commit_result"]["process_evaluation"]
        assert result["extracted_facts"] == []
        assert "process_score" in result["quality_trace"][0]
        assert "process_failures" in result["quality_trace"][0]

    def test_commit_persists_consistency_audit_notes(self, tmp_story_db, tmp_path):
        from unittest.mock import patch

        from tinyassets.evaluation.process import ProcessCheck, ProcessEvaluation
        from tinyassets.evaluation.structural import CheckResult, StructuralResult
        from tinyassets.notes import list_notes

        state = _base_state(tmp_story_db, _universe_path=str(tmp_path))
        state.update(orient(state))
        state.update(plan(state))
        state.update(draft(state))

        structural = StructuralResult(
            checks=[
                CheckResult(
                    name="canon_breach",
                    passed=False,
                    score=0.0,
                    violations=["Marcus contradicts the canon name Corin."],
                ),
            ],
            aggregate_score=0.2,
            hard_failure=False,
            violations=["Marcus contradicts the canon name Corin."],
        )
        process_eval = ProcessEvaluation(
            checks=[
                ProcessCheck(
                    name="tool_use",
                    passed=False,
                    score=0.25,
                    observation=(
                        "Writer tool usage did not cover both plan and draft phases."
                    ),
                ),
            ],
            aggregate_score=0.25,
            failing_checks=["tool_use"],
        )

        with (
            patch(
                "domains.fantasy_daemon.phases.commit._structural_evaluator.evaluate",
                return_value=structural,
            ),
            patch("domains.fantasy_daemon.phases.commit._run_editorial", return_value=None),
            patch(
                "domains.fantasy_daemon.phases.commit.evaluate_scene_process",
                return_value=process_eval,
            ),
        ):
            commit(state)

        notes = list_notes(tmp_path)
        assert any(
            note.source == "structural"
            and "canon name Corin" in note.text
            for note in notes
        )
        assert any(
            note.source == "system"
            and "Writer tool usage" in note.text
            for note in notes
        )


# ---------------------------------------------------------------------------
# Full orient -> plan -> draft -> commit pipeline
# ---------------------------------------------------------------------------


class TestFullPipeline:
    def test_pipeline_produces_verdict(self, tmp_story_db):
        """Run the full pipeline (mock providers) and verify we get a verdict."""
        state = _base_state(tmp_story_db)
        state.update(orient(state))
        state.update(plan(state))
        state.update(draft(state))
        result = commit(state)

        assert result["verdict"] in ("accept", "second_draft", "revert")
        assert result["commit_result"]["scene_id"] == "test-universe-B1-C1-S1"

    def test_pipeline_accumulates_quality_trace(self, tmp_story_db):
        """Quality trace should accumulate entries from all nodes."""
        state = _base_state(tmp_story_db)

        result = orient(state)
        state.update(result)
        trace = list(result["quality_trace"])

        result = plan(state)
        state.update(result)
        trace.extend(result["quality_trace"])

        result = draft(state)
        state.update(result)
        trace.extend(result["quality_trace"])

        result = commit(state)
        trace.extend(result["quality_trace"])

        nodes = [t["node"] for t in trace]
        assert "orient" in nodes
        assert "plan" in nodes
        assert "draft" in nodes
        assert "commit" in nodes

    def test_pipeline_at_different_chapters(self, tmp_story_db):
        """Verify the pipeline works at chapter 5 scene 3."""
        state = _base_state(
            tmp_story_db,
            chapter_number=5,
            scene_number=3,
        )
        state.update(orient(state))
        assert "B1-C5-S3" in state["orient_result"]["scene_id"]
        state.update(plan(state))
        state.update(draft(state))
        result = commit(state)
        assert result["verdict"] in ("accept", "second_draft", "revert")
