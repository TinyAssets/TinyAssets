"""Tests for the memory hierarchy: core, episodic, promotion, reflexion, manager."""

from __future__ import annotations

import json

import pytest

from tinyassets.memory.core import CoreMemory
from tinyassets.memory.episodic import EpisodicMemory
from tinyassets.memory.manager import DRAFT, EVALUATE, ORIENT, PLAN, MemoryManager
from tinyassets.memory.promotion import PromotionGates
from tinyassets.memory.reflexion import ReflexionEngine, ReflexionResult

pytestmark = pytest.mark.usefixtures("connected_fantasy_model")

# =====================================================================
# CoreMemory
# =====================================================================


class TestCoreMemory:
    def test_put_and_get(self):
        mem = CoreMemory("test-universe")
        mem.put("characters", "ryn", {"name": "Ryn", "class": "scout"})
        assert mem.get("characters", "ryn") == {"name": "Ryn", "class": "scout"}

    def test_get_missing_returns_default(self):
        mem = CoreMemory("test-universe")
        assert mem.get("characters", "missing") is None
        assert mem.get("characters", "missing", "fallback") == "fallback"

    def test_get_all(self):
        mem = CoreMemory("test-universe")
        mem.put("characters", "a", {"name": "A"})
        mem.put("characters", "b", {"name": "B"})
        result = mem.get_all("characters")
        assert len(result) == 2
        assert "a" in result
        assert "b" in result

    def test_delete(self):
        mem = CoreMemory("test-universe")
        mem.put("characters", "ryn", {"name": "Ryn"})
        mem.delete("characters", "ryn")
        assert mem.get("characters", "ryn") is None

    def test_clear_category(self):
        mem = CoreMemory("test-universe")
        mem.put("characters", "a", {})
        mem.put("promises", "p1", {})
        mem.clear("characters")
        assert mem.get_all("characters") == {}
        assert mem.get("promises", "p1") == {}

    def test_clear_all(self):
        mem = CoreMemory("test-universe")
        mem.put("characters", "a", {})
        mem.put("promises", "p1", {})
        mem.clear()
        assert mem.get_all("characters") == {}
        assert mem.get_all("promises") == {}

    def test_load_characters(self):
        mem = CoreMemory("test-universe")
        mem.load_characters([
            {"id": "ryn", "name": "Ryn"},
            {"id": "kael", "name": "Kael"},
        ])
        assert mem.get("characters", "ryn")["name"] == "Ryn"
        assert mem.get("characters", "kael")["name"] == "Kael"

    def test_load_world_state(self):
        mem = CoreMemory("test-universe")
        mem.load_world_state({"time": "dawn", "weather": "rain"})
        ws = mem.get("world_state", "current")
        assert ws["time"] == "dawn"

    def test_estimated_tokens(self):
        mem = CoreMemory("test-universe")
        mem.put("characters", "a", {"name": "A character with some data"})
        assert mem.estimated_tokens() > 0


# =====================================================================
# EpisodicMemory
# =====================================================================


class TestEpisodicMemory:
    def _make_episodic(self, canonical_source) -> EpisodicMemory:
        return EpisodicMemory(
            source=canonical_source("episodic", key=":memory:", namespaces=["test"]),
            universe_id="test",
            window_chapters=3,
        )

    def test_store_and_get_summary(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_summary(1, 1, 1, "Ryn enters the forest.", 50)
        recent = ep.get_recent(chapter=1, k=5)
        assert len(recent) == 1
        assert recent[0].summary == "Ryn enters the forest."
        assert recent[0].word_count == 50

    def test_get_recent_ordering(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_summary(1, 1, 1, "Scene 1", 10)
        ep.store_summary(1, 1, 2, "Scene 2", 20)
        ep.store_summary(1, 2, 1, "Scene 3", 30)
        recent = ep.get_recent(chapter=2, k=5)
        # Most recent first.
        assert recent[0].chapter_number == 2
        assert recent[1].chapter_number == 1

    def test_get_recent_limit(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        for i in range(10):
            ep.store_summary(1, i + 1, 1, f"Scene {i}", 10)
        recent = ep.get_recent(chapter=10, k=3)
        assert len(recent) == 3

    def test_store_fact_and_increment(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_fact("f1", "Ryn", "has blue eyes", "b1c1s1")
        ep.store_fact("f1", "Ryn", "has blue eyes", "b1c2s1")
        facts = ep.get_facts_for_entity("Ryn")
        assert len(facts) == 1
        assert facts[0]["evidence_count"] == 2
        assert len(facts[0]["source_scenes"]) == 2

    def test_get_promotable_facts(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_fact("f1", "Ryn", "has blue eyes", "s1")
        ep.store_fact("f1", "Ryn", "has blue eyes", "s2")
        ep.store_fact("f1", "Ryn", "has blue eyes", "s3")

        promotable = ep.get_promotable_facts(threshold=3)
        assert len(promotable) == 1
        assert promotable[0]["fact_id"] == "f1"

    def test_mark_promoted(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_fact("f1", "Ryn", "has blue eyes", "s1")
        ep.store_fact("f1", "Ryn", "has blue eyes", "s2")
        ep.store_fact("f1", "Ryn", "has blue eyes", "s3")

        ep.mark_promoted("f1")
        promotable = ep.get_promotable_facts(threshold=3)
        assert len(promotable) == 0

    def test_store_and_query_observations(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_observation("pacing", "too slow", "b1c1s1")
        ep.store_observation("pacing", "needs tension", "b1c1s2")
        obs = ep.get_observations_by_dimension("pacing")
        assert len(obs) == 2
        assert ep.count_observations("pacing") == 2

    def test_store_and_query_reflections(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_reflection(1, 2, "voice was off", "focus on voice next time")
        refs = ep.get_recent_reflections(k=5)
        assert len(refs) == 1
        assert refs[0]["critique"] == "voice was off"

    def test_evict_old_summaries(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        for ch in range(1, 11):
            ep.store_summary(1, ch, 1, f"Chapter {ch}", 100)
        # Window is 3, current chapter 10 => evict chapters < 7.
        evicted = ep.evict_old_summaries(current_chapter=10)
        assert evicted == 6  # chapters 1-6
        remaining = ep.get_recent(chapter=10, k=100)
        assert len(remaining) == 4  # chapters 7-10

    def test_upsert_summary(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_summary(1, 1, 1, "Version 1", 50)
        ep.store_summary(1, 1, 1, "Version 2", 60)
        recent = ep.get_recent(chapter=1, k=5)
        assert len(recent) == 1
        assert recent[0].summary == "Version 2"

    def test_store_episode_summary_keeps_generic_rows_domain_neutral(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_episode_summary(
            episode_id="research-session-1",
            sequence_number=7,
            summary="Synthesized current standards evidence.",
            word_count=120,
            domain_id="research_probe",
            domain_payload={"query": "data rules"},
        )

        recent = ep.get_recent_episodes(max_sequence=7, k=5, domain_id="research_probe")
        assert len(recent) == 1
        assert recent[0].episode_id == "research-session-1"
        assert recent[0].sequence_number == 7
        assert recent[0].domain_payload == {"query": "data rules"}

        row = ep._conn.execute(
            "SELECT book_number, chapter_number, scene_number "
            "FROM scene_summaries WHERE domain_id = ?",
            ("research_probe",),
        ).fetchone()
        assert row == (None, None, None)

    def test_legacy_summary_call_records_fantasy_domain_payload(self, canonical_source):
        ep = self._make_episodic(canonical_source)
        ep.store_summary(1, 2, 3, "Fantasy scene summary.", 90)

        row = ep._conn.execute(
            "SELECT domain_id, episode_id, sequence_number, domain_payload "
            "FROM scene_summaries WHERE universe_id = ?",
            ("test",),
        ).fetchone()
        assert row[0] == "fantasy_author"
        assert row[1] == "book:1/chapter:2/scene:3"
        assert row[2] == 2
        assert json.loads(row[3]) == {
            "book_number": 1,
            "chapter_number": 2,
            "scene_number": 3,
        }


# =====================================================================
# PromotionGates
# =====================================================================


class TestPromotionGates:
    def test_fact_promotion(self, canonical_source):
        ep = EpisodicMemory(
            canonical_source("episodic", key=":memory:", namespaces=["test"]), "test"
        )
        ep.store_fact("f1", "Ryn", "blue eyes", "s1")
        ep.store_fact("f1", "Ryn", "blue eyes", "s2")
        ep.store_fact("f1", "Ryn", "blue eyes", "s3")

        gates = PromotionGates(fact_threshold=3)
        result = gates.run(ep)
        assert len(result.promoted_facts) == 1
        assert result.promoted_facts[0]["fact_id"] == "f1"

    def test_no_promotion_below_threshold(self, canonical_source):
        ep = EpisodicMemory(
            canonical_source("episodic", key=":memory:", namespaces=["test"]), "test"
        )
        ep.store_fact("f1", "Ryn", "blue eyes", "s1")
        ep.store_fact("f1", "Ryn", "blue eyes", "s2")

        gates = PromotionGates(fact_threshold=3)
        result = gates.run(ep)
        assert len(result.promoted_facts) == 0

    def test_style_rule_promotion(self, canonical_source):
        ep = EpisodicMemory(
            canonical_source("episodic", key=":memory:", namespaces=["test"]), "test"
        )
        ep.store_observation("pacing", "too slow", "s1")
        ep.store_observation("pacing", "needs momentum", "s2")
        ep.store_observation("pacing", "flat", "s3")

        gates = PromotionGates(style_threshold=3)
        result = gates.run(ep)
        assert len(result.promoted_style_rules) == 1
        assert result.promoted_style_rules[0]["dimension"] == "pacing"

    def test_asp_rule_candidate(self, canonical_source):
        violations = [
            {"rule": "no_teleport", "details": "scene 1"},
            {"rule": "no_teleport", "details": "scene 2"},
            {"rule": "no_teleport", "details": "scene 3"},
        ]
        ep = EpisodicMemory(
            canonical_source("episodic", key=":memory:", namespaces=["test"]), "test"
        )
        gates = PromotionGates(violation_threshold=3)
        result = gates.run(ep, violations=violations)
        assert len(result.asp_rule_candidates) == 1
        assert result.asp_rule_candidates[0]["rule"] == "no_teleport"


# =====================================================================
# ReflexionEngine
# =====================================================================


class TestReflexionEngine:
    def test_reflect_generates_result(self, canonical_source):
        ep = EpisodicMemory(
            canonical_source("episodic", key=":memory:", namespaces=["test"]), "test"
        )
        engine = ReflexionEngine(episodic=ep)

        state = {
            "book_number": 1,
            "chapter_number": 2,
            "scene_number": 3,
            "quality_trace": [],
        }
        feedback = [
            {"verdict": "revert", "rationale": "voice inconsistent", "provider": "codex"}
        ]

        result = engine.reflect(state, judge_feedback=feedback)
        assert isinstance(result, ReflexionResult)
        assert "voice inconsistent" in result.critique
        assert "Reflection on" in result.reflection

    def test_reflect_stores_in_episodic(self, canonical_source):
        ep = EpisodicMemory(
            canonical_source("episodic", key=":memory:", namespaces=["test"]), "test"
        )
        engine = ReflexionEngine(episodic=ep)

        state = {"book_number": 1, "chapter_number": 1, "scene_number": 1, "quality_trace": []}
        engine.reflect(state, judge_feedback=[])

        refs = ep.get_recent_reflections(k=5)
        assert len(refs) == 1

    def test_weight_update_on_continuity_issue(self):
        engine = ReflexionEngine()
        state = {"chapter_number": 1, "scene_number": 1, "quality_trace": []}
        feedback = [{"verdict": "revert", "rationale": "continuity error"}]
        result = engine.reflect(state, judge_feedback=feedback)
        assert "continuity_check" in result.updated_weights

    def test_reflect_without_episodic(self):
        engine = ReflexionEngine(episodic=None)
        state = {"chapter_number": 1, "scene_number": 1, "quality_trace": []}
        result = engine.reflect(state)
        assert isinstance(result, ReflexionResult)


# =====================================================================
# MemoryManager
# =====================================================================


class TestMemoryManager:
    def _make_manager(self, canonical_source) -> MemoryManager:
        return MemoryManager(
            universe_id="test",
            source=canonical_source("episodic", key=":memory:", namespaces=["test"]),
        )

    def _make_state(self, **overrides) -> dict:
        base = {
            "universe_id": "test",
            "book_number": 1,
            "chapter_number": 1,
            "scene_number": 1,
            "orient_result": {
                "characters": [{"id": "ryn", "name": "Ryn"}],
                "world_state": {"time": "dawn"},
                "warnings": [],
            },
            "retrieved_context": {},
            "recent_prose": "The wind howled.",
            "workflow_instructions": {},
            "plan_output": {
                "scene_id": "s1",
                "beats": ["arrive", "discover"],
                "done_when": ["Ryn reaches the gate"],
            },
            "draft_output": {
                "prose": "Ryn walked through the forest.",
                "word_count": 5,
            },
            "commit_result": None,
            "second_draft_used": False,
            "verdict": "",
            "extracted_facts": [],
            "extracted_promises": [],
            "style_observations": [],
            "quality_trace": [],
            "quality_debt": [],
        }
        base.update(overrides)
        return base

    def test_assemble_orient(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        state = self._make_state()
        ctx = mgr.assemble_context(ORIENT, state)
        assert ctx.phase == ORIENT
        assert "world_state" in ctx
        assert "active_promises" in ctx
        assert "recent_reflections" in ctx

    def test_assemble_plan(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        state = self._make_state()
        ctx = mgr.assemble_context(PLAN, state)
        assert ctx.phase == PLAN
        assert "facts" in ctx
        assert "promises" in ctx
        assert "orient_warnings" in ctx
        assert "style_rules" in ctx

    def test_assemble_draft(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        state = self._make_state()
        ctx = mgr.assemble_context(DRAFT, state)
        assert ctx.phase == DRAFT
        assert "recent_prose" in ctx
        assert "beat_sheet" in ctx

    def test_assemble_evaluate(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        state = self._make_state()
        ctx = mgr.assemble_context(EVALUATE, state)
        assert ctx.phase == EVALUATE
        assert "draft_text" in ctx
        assert "canon_facts" in ctx

    def test_store_scene_result(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        state = self._make_state(
            extracted_facts=[
                {"fact_id": "f1", "entity": "Ryn", "text": "has blue eyes"}
            ],
            style_observations=[
                {"dimension": "pacing", "observation": "good flow"}
            ],
        )
        mgr.store_scene_result(state)

        # Verify summary stored.
        recent = mgr.episodic.get_recent(chapter=1, k=5)
        assert len(recent) == 1

        # Verify fact stored.
        facts = mgr.episodic.get_facts_for_entity("Ryn")
        assert len(facts) == 1

        # Verify observation stored.
        obs = mgr.episodic.get_observations_by_dimension("pacing")
        assert len(obs) == 1

    def test_run_promotion_gates(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        # Build up evidence.
        for i in range(3):
            mgr.episodic.store_fact("f1", "Ryn", "blue eyes", f"s{i}")

        result = mgr.run_promotion_gates()
        assert len(result.promoted_facts) == 1

    def test_run_reflexion(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        state = self._make_state()
        feedback = [{"verdict": "revert", "rationale": "bad pacing"}]
        result = mgr.run_reflexion(state, judge_feedback=feedback)
        assert isinstance(result, ReflexionResult)
        assert "pacing" in result.critique

    def test_evict_old_data(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        for ch in range(1, 11):
            mgr.episodic.store_summary(1, ch, 1, f"ch{ch}", 100)
        evicted = mgr.evict_old_data(current_chapter=10)
        assert evicted > 0

    def test_close(self, canonical_source):
        mgr = self._make_manager(canonical_source)
        mgr.close()
        # Should not raise on double close.
