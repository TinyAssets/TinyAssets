"""Tests for the Tier 1 structural evaluation system.

Covers all 10 deterministic checks in StructuralEvaluator.
"""

from __future__ import annotations

import json

import pytest

from tinyassets.evaluation import EvalResult, EvidenceRef, PatchNotes
from tinyassets.evaluation.structural import (
    CheckResult,
    StructuralEvaluator,
    StructuralResult,
    _check_canon_breach,
    _check_chekhov,
    _check_pacing,
    _check_premise_grounding,
    _check_readability,
    _check_taaco_coherence,
    _check_timeline,
    _extract_premise_terms,
    _facts_contradict,
)


def _patch_notes(**overrides) -> PatchNotes:
    values = {
        "summary": "Repair the rejected artifact",
        "rationale": "The gate found a deterministic mismatch.",
        "author_actor_id": "alice",
    }
    values.update(overrides)
    return PatchNotes(**values)


def test_patch_notes_round_trip_preserves_typed_evidence_and_identity():
    notes = _patch_notes(
        evidence_refs=[
            EvidenceRef(kind="run_artifact", id="artifact-1", cited_by="gate-1")
        ],
        extra={"target": "draft"},
    )

    restored = PatchNotes.from_dict(notes.to_dict())

    assert restored == notes
    assert restored.patch_notes_id == notes.patch_notes_id


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"summary": ""}, "summary must be 1-200 characters"),
        ({"summary": "x" * 201}, "summary must be 1-200 characters"),
        ({"author_actor_id": ""}, "author_actor_id is required"),
    ],
)
def test_patch_notes_reject_malformed_required_fields(changes, message):
    with pytest.raises(ValueError, match=message):
        _patch_notes(**changes)


@pytest.mark.parametrize(
    "payload_update",
    [
        {"summary": 42},
        {"rationale": None},
        {"affected_files": "tinyassets/runs.py"},
        {"tests_added": ["ok", 7]},
        {"evidence_run_id": {"run": "r1"}},
    ],
)
def test_patch_notes_from_dict_rejects_malformed_field_types(payload_update):
    payload = _patch_notes().to_dict()
    payload.update(payload_update)

    with pytest.raises(TypeError):
        PatchNotes.from_dict(payload)


def test_route_back_evaluation_requires_goal_and_typed_patch_notes():
    notes = _patch_notes()

    decision = EvalResult(
        score=0.0,
        verdict="route_back",
        kind="structural",
        goal_id="goal-1",
        patch_notes=notes,
    )

    assert decision.goal_id == "goal-1"
    assert decision.patch_notes is notes
    with pytest.raises(ValueError, match="goal_id"):
        EvalResult(
            score=0.0,
            verdict="route_back",
            kind="structural",
            patch_notes=notes,
        )
    with pytest.raises(TypeError, match="PatchNotes"):
        EvalResult(
            score=0.0,
            verdict="route_back",
            kind="structural",
            goal_id="goal-1",
            patch_notes={"summary": "untyped"},
        )

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_scene_state(
    prose: str = "The knight rode through the forest.",
    orient_result: dict | None = None,
    retrieved_context: dict | None = None,
    extracted_facts: list | None = None,
    extracted_promises: list | None = None,
    scene_number: int = 1,
) -> dict:
    """Build a minimal SceneState dict for testing."""
    return {
        "universe_id": "test",
        "book_number": 1,
        "chapter_number": 1,
        "scene_number": scene_number,
        "orient_result": orient_result or {},
        "retrieved_context": retrieved_context or {},
        "recent_prose": "",
        "workflow_instructions": {},
        "plan_output": None,
        "draft_output": {"prose": prose, "scene_id": "1-1-1", "word_count": len(prose.split())},
        "commit_result": None,
        "second_draft_used": False,
        "verdict": "",
        "extracted_facts": extracted_facts or [],
        "extracted_promises": extracted_promises or [],
        "style_observations": [],
        "quality_trace": [],
        "quality_debt": [],
    }


COHERENT_PROSE = (
    "The knight rode through the dark forest, his armor glinting in the moonlight. "
    "The forest was thick with ancient trees and tangled roots. "
    "He gripped the reins tighter as the path narrowed ahead. "
    "The narrow path led deeper into the heart of the woods."
)

INCOHERENT_PROSE = (
    "Quantum entanglement produces Bell state violations. "
    "The recipe calls for two cups of flour and three eggs. "
    "Jupiter's magnetosphere extends millions of kilometers. "
    "The stock market closed at record highs yesterday."
)

FANTASY_GRADE_PROSE = (
    "Beneath the obsidian spires of Ashwater, the erstwhile conjuror "
    "contemplated the inexorable dissolution of his arcane patrimony. "
    "The crystalline formations, once resplendent with ethereal luminescence, "
    "now languished in melancholic quiescence. "
    "His erstwhile companions had relinquished their ambitions, "
    "succumbing to the inevitable entropy of their diminishing powers."
)

SIMPLE_PROSE = "The cat sat. The dog ran. I am big. He is sad."


# ---------------------------------------------------------------------------
# StructuralEvaluator: smoke test
# ---------------------------------------------------------------------------


class TestStructuralEvaluator:
    """Tests for the full StructuralEvaluator.evaluate() flow."""

    def test_returns_structural_result(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose=COHERENT_PROSE)
        result = evaluator.evaluate(state)

        assert isinstance(result, StructuralResult)
        assert isinstance(result.checks, list)
        assert len(result.checks) == 9
        assert isinstance(result.aggregate_score, float)
        assert 0.0 <= result.aggregate_score <= 1.0
        assert isinstance(result.hard_failure, bool)

    def test_all_check_names_present(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose=COHERENT_PROSE)
        result = evaluator.evaluate(state)
        names = {c.name for c in result.checks}
        expected = {
            "taaco_coherence",
            "readability",
            "pacing",
            "chekhov_tracking",
            "timeline_consistency",
            "character_voice",
            "canon_breach",
            "premise_grounding",
            "asp_constraint",
        }
        assert names == expected

    def test_each_check_is_check_result(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose=COHERENT_PROSE)
        result = evaluator.evaluate(state)
        for check in result.checks:
            assert isinstance(check, CheckResult)
            assert isinstance(check.name, str)
            assert isinstance(check.passed, bool)
            assert isinstance(check.score, float)
            assert 0.0 <= check.score <= 1.0
            assert isinstance(check.violations, list)

    def test_aggregate_score_within_bounds(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose=COHERENT_PROSE)
        result = evaluator.evaluate(state)
        assert 0.0 <= result.aggregate_score <= 1.0

    def test_empty_prose_does_not_crash(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose="")
        result = evaluator.evaluate(state)
        assert isinstance(result, StructuralResult)

    def test_missing_draft_output_does_not_crash(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state()
        state["draft_output"] = None
        result = evaluator.evaluate(state)
        assert isinstance(result, StructuralResult)


# ---------------------------------------------------------------------------
# TAACO coherence
# ---------------------------------------------------------------------------


class TestTaacCoherence:
    def test_high_overlap_scores_well(self):
        result = _check_taaco_coherence(COHERENT_PROSE)
        assert result.passed is True
        assert result.score >= 0.5

    def test_low_overlap_scores_poorly(self):
        result = _check_taaco_coherence(INCOHERENT_PROSE)
        # Sentences about completely different topics should have low overlap
        assert result.score <= 0.8  # may still pass but score should be lower

    def test_single_sentence(self):
        result = _check_taaco_coherence("Just one sentence here.")
        assert result.passed is True
        assert result.score == 1.0

    def test_empty_text(self):
        result = _check_taaco_coherence("")
        assert result.passed is True


# ---------------------------------------------------------------------------
# Readability
# ---------------------------------------------------------------------------


class TestReadability:
    def test_fantasy_range_passes(self):
        result = _check_readability(FANTASY_GRADE_PROSE)
        assert result.passed is True
        assert "flesch_kincaid_grade" in result.details

    def test_too_simple_flags(self):
        result = _check_readability(SIMPLE_PROSE)
        # Grade ~1-3 should be flagged as too simple
        grade = result.details["flesch_kincaid_grade"]
        if grade < 4:
            assert result.passed is False

    def test_empty_text(self):
        result = _check_readability("")
        assert result.passed is True

    def test_details_populated(self):
        result = _check_readability(COHERENT_PROSE)
        assert "word_count" in result.details
        assert "sentence_count" in result.details
        assert "avg_syllables_per_word" in result.details


# ---------------------------------------------------------------------------
# Pacing
# ---------------------------------------------------------------------------


class TestPacing:
    def test_normal_scene_passes(self):
        # ~40 words, chapter average ~50 -- within bounds
        result = _check_pacing(COHERENT_PROSE, chapter_avg_words=50)
        assert result.passed is True

    def test_too_long_flags(self):
        long_prose = COHERENT_PROSE * 20  # ~800 words
        result = _check_pacing(long_prose, chapter_avg_words=100)
        assert result.passed is False
        assert any("chapter average" in v for v in result.violations)

    def test_no_chapter_avg_still_works(self):
        result = _check_pacing(COHERENT_PROSE, chapter_avg_words=None)
        assert isinstance(result.score, float)

    def test_dialogue_ratio_detected(self):
        dialogue_heavy = (
            '"Hello there," said the knight. '
            '"How are you?" asked the squire. '
            '"Fine," replied the knight. '
            '"Good," answered the squire.'
        )
        result = _check_pacing(dialogue_heavy)
        assert result.details["dialogue_to_narration_ratio"] > 0

    def test_details_populated(self):
        result = _check_pacing(COHERENT_PROSE, chapter_avg_words=50)
        assert "word_count" in result.details
        assert "dialogue_to_narration_ratio" in result.details
        assert "paragraph_count" in result.details


# ---------------------------------------------------------------------------
# Chekhov's Gun tracking
# ---------------------------------------------------------------------------


class TestChekhovTracking:
    def test_no_promises_passes(self):
        state = _make_scene_state()
        result = _check_chekhov(state)
        assert result.passed is True
        assert result.score == 1.0

    def test_unresolved_long_active_flags(self):
        state = _make_scene_state(
            scene_number=8,
            orient_result={
                "active_promises": [
                    {"description": "The mysterious letter", "introduced_scene": 2},
                    {"description": "The locked door", "introduced_scene": 1},
                ]
            },
        )
        result = _check_chekhov(state)
        assert result.passed is False
        assert result.details["overdue_count"] == 2

    def test_recently_introduced_passes(self):
        state = _make_scene_state(
            scene_number=3,
            orient_result={
                "active_promises": [
                    {"description": "The sword's glow", "introduced_scene": 2},
                ]
            },
        )
        result = _check_chekhov(state)
        assert result.passed is True

    def test_mixed_overdue_and_recent(self):
        state = _make_scene_state(
            scene_number=10,
            orient_result={
                "active_promises": [
                    {"description": "Old promise", "introduced_scene": 1},
                    {"description": "New promise", "introduced_scene": 9},
                ]
            },
        )
        result = _check_chekhov(state)
        assert result.passed is False
        assert result.details["overdue_count"] == 1
        assert 0.0 < result.score < 1.0


# ---------------------------------------------------------------------------
# Timeline consistency
# ---------------------------------------------------------------------------


class TestTimelineConsistency:
    def test_no_conflicts_passes(self):
        state = _make_scene_state(orient_result={"warnings": []})
        result = _check_timeline(state)
        assert result.passed is True
        assert result.score == 1.0

    def test_location_conflict_is_hard_failure(self):
        state = _make_scene_state(
            orient_result={
                "warnings": [
                    {
                        "type": "location_conflict",
                        "character": "Ryn",
                        "location_a": "Ashwater",
                        "location_b": "Northern Pass",
                    }
                ]
            }
        )
        result = _check_timeline(state)
        assert result.passed is False
        assert result.details["is_hard_failure"] is True
        assert len(result.violations) == 1

    def test_knowledge_boundary_violation(self):
        state = _make_scene_state(
            orient_result={
                "warnings": [
                    {
                        "type": "knowledge_boundary",
                        "character": "Sera",
                        "fact": "the king is poisoned",
                    }
                ]
            }
        )
        result = _check_timeline(state)
        assert result.passed is False
        assert result.details["knowledge_violations"] == 1

    def test_empty_orient_result(self):
        state = _make_scene_state(orient_result={})
        result = _check_timeline(state)
        assert result.passed is True


# ---------------------------------------------------------------------------
# Fact contradiction detection
# ---------------------------------------------------------------------------


class TestFactsContradict:
    def test_negation_asymmetry(self):
        assert _facts_contradict(
            "Ryn is a member of the Compact",
            "Ryn is not a member of the Compact",
        ) is True

    def test_attribute_conflict(self):
        assert _facts_contradict(
            "Ryn has brown eyes",
            "Ryn has blue eyes",
        ) is True

    def test_no_contradiction(self):
        assert _facts_contradict(
            "Ryn walked to the market",
            "The market opens at dawn",
        ) is False

    def test_same_fact_no_contradiction(self):
        assert _facts_contradict(
            "Ryn has brown eyes",
            "Ryn has brown eyes",
        ) is False

    def test_different_subjects_no_conflict(self):
        assert _facts_contradict(
            "Ryn has brown eyes",
            "Sera has blue eyes",
        ) is False

    def test_never_vs_positive(self):
        assert _facts_contradict(
            "Corin never left Ashwater",
            "Corin left Ashwater at dawn",
        ) is True

    def test_unrelated_negation_no_false_positive(self):
        """Negation in one fact about unrelated topic must not trigger."""
        assert _facts_contradict(
            "Corin has no siblings",
            "Corin lives in Ashwater",
        ) is False

    def test_unrelated_never_no_false_positive(self):
        """'never' about a different predicate must not trigger."""
        assert _facts_contradict(
            "The city was never conquered",
            "The city has tall walls",
        ) is False

    def test_shared_subject_different_predicates_no_false_positive(self):
        """Two facts sharing character + location but different predicates."""
        assert _facts_contradict(
            "Corin was not ready for the journey",
            "Corin was exhausted from the journey",
        ) is False

    def test_negation_with_shared_predicate_still_fires(self):
        """True contradiction with shared content words must still fire."""
        assert _facts_contradict(
            "Corin never left the village of Ashwater",
            "Corin left the village of Ashwater at dawn",
        ) is True


# ---------------------------------------------------------------------------
# Canon breach detection
# ---------------------------------------------------------------------------


class TestCanonBreach:
    def test_no_canon_data_passes(self):
        state = _make_scene_state()
        result = _check_canon_breach(state)
        assert result.passed is True
        assert result.score == 1.0

    def test_attribute_contradiction_detected(self):
        """Different attribute values for same entity triggers breach."""
        state = _make_scene_state(
            extracted_facts=[
                {"fact": "Ryn has brown eyes and dark hair"},
            ],
            retrieved_context={
                "canon_facts": [
                    {"fact": "Ryn has blue eyes and light hair"},
                ]
            },
        )
        result = _check_canon_breach(state)
        assert result.passed is False
        assert len(result.violations) >= 1

    def test_negation_contradiction_detected(self):
        """Negation asymmetry triggers breach."""
        state = _make_scene_state(
            extracted_facts=[
                {"fact": "Ryn is not a member of the Compact"},
            ],
            retrieved_context={
                "canon_facts": [
                    {"fact": "Ryn is a member of the Compact"},
                ]
            },
        )
        result = _check_canon_breach(state)
        assert result.passed is False
        assert len(result.violations) >= 1

    def test_no_contradiction_passes(self):
        state = _make_scene_state(
            extracted_facts=[
                {"fact": "Ryn walked to the market"},
            ],
            retrieved_context={
                "canon_facts": [
                    {"fact": "The market opens at dawn"},
                ]
            },
        )
        result = _check_canon_breach(state)
        assert result.passed is True

    def test_compatible_facts_pass(self):
        """Facts about same entity with no conflict should pass."""
        state = _make_scene_state(
            extracted_facts=[
                {"fact": "Ryn has brown eyes"},
            ],
            retrieved_context={
                "canon_facts": [
                    {"fact": "Ryn has brown eyes and dark hair"},
                ]
            },
        )
        result = _check_canon_breach(state)
        assert result.passed is True


# (TestNameConsistency and TestExtractDeprecatedNames removed --
# name consistency now handled by editorial reader via notes system.)


# ---------------------------------------------------------------------------
# ASP constraint (stub)
# ---------------------------------------------------------------------------


class TestASPConstraint:
    def test_stub_passes_when_no_engine(self):
        """Without the constraints module installed, ASP check should pass."""
        from tinyassets.evaluation.structural import _HAS_ASP

        if not _HAS_ASP:
            state = _make_scene_state()
            evaluator = StructuralEvaluator()
            result = evaluator.evaluate(state)
            asp_check = next(c for c in result.checks if c.name == "asp_constraint")
            assert asp_check.passed is True
            assert asp_check.score == 1.0
        else:
            pytest.skip("ASP engine is available; stub test not applicable")


# ---------------------------------------------------------------------------
# Hard failure propagation
# ---------------------------------------------------------------------------


class TestHardFailure:
    def test_timeline_conflict_sets_hard_failure(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(
            prose=COHERENT_PROSE,
            orient_result={
                "warnings": [
                    {
                        "type": "location_conflict",
                        "character": "Ryn",
                        "location_a": "Ashwater",
                        "location_b": "Northern Pass",
                    }
                ]
            },
        )
        result = evaluator.evaluate(state)
        assert result.hard_failure is True

    def test_canon_breach_is_observation_not_hard_failure(self):
        """Canon breach is demoted to observation -- not a hard failure.

        The editorial reader handles canon nuance better than lexical
        contradiction detection (high false-positive rate).
        """
        evaluator = StructuralEvaluator()
        state = _make_scene_state(
            prose=COHERENT_PROSE,
            extracted_facts=[{"fact": "Ryn has brown eyes"}],
            retrieved_context={
                "canon_facts": [
                    {"fact": "Ryn has blue eyes"},
                ]
            },
        )
        result = evaluator.evaluate(state)
        assert result.hard_failure is False

        # Should produce an observation instead
        canon_check = next(c for c in result.checks if c.name == "canon_breach")
        assert not canon_check.passed
        assert canon_check.observation  # non-empty observation

    def test_no_hard_failure_for_pacing(self):
        evaluator = StructuralEvaluator()
        long_prose = COHERENT_PROSE * 20
        state = _make_scene_state(
            prose=long_prose,
            orient_result={"chapter_avg_words": 50},
        )
        result = evaluator.evaluate(state)
        # Pacing violations are soft, not hard failures
        assert result.hard_failure is False


# ---------------------------------------------------------------------------
# Aggregate score
# ---------------------------------------------------------------------------


class TestAggregateScore:
    def test_perfect_state_high_score(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose=COHERENT_PROSE)
        result = evaluator.evaluate(state)
        assert result.aggregate_score >= 0.5

    def test_score_is_weighted_average(self):
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose=COHERENT_PROSE)
        result = evaluator.evaluate(state)

        # Manually compute expected weighted average
        from tinyassets.evaluation.structural import _CHECK_WEIGHTS

        weighted_sum = 0.0
        weight_total = 0.0
        for check in result.checks:
            w = _CHECK_WEIGHTS.get(check.name, 0.1)
            weighted_sum += check.score * w
            weight_total += w
        expected = weighted_sum / weight_total if weight_total > 0 else 0.0
        assert abs(result.aggregate_score - round(expected, 3)) < 0.01


# ---------------------------------------------------------------------------
# Commit node integration: uses real StructuralEvaluator
# ---------------------------------------------------------------------------


@pytest.mark.usefixtures("canonical_runtime")
class TestCommitUsesRealEvaluator:
    """Verify the commit node imports and uses the real StructuralEvaluator."""

    def test_commit_imports_real_evaluator(self):
        """commit.py should import from evaluation.structural."""
        import importlib

        commit_mod = importlib.import_module("domains.fantasy_daemon.phases.commit")

        # The module should reference the real StructuralEvaluator
        assert hasattr(commit_mod, "_structural_evaluator")
        assert hasattr(commit_mod, "StructuralEvaluator")
        assert commit_mod.StructuralEvaluator is StructuralEvaluator

    def test_commit_returns_structural_checks_as_dicts(self):
        """commit_result should contain serialized CheckResult data."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _make_scene_state(prose=COHERENT_PROSE)
        state["_db_path"] = ":memory:"
        result = commit(state)

        checks = result["commit_result"]["structural_checks"]
        assert len(checks) == 9
        for check in checks:
            assert "name" in check
            assert "passed" in check
            assert "score" in check
            assert "violations" in check

    def test_commit_verdict_without_editorial(self):
        """Without editorial reader, verdict should be 'accept'."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _make_scene_state(prose=COHERENT_PROSE)
        state["_db_path"] = ":memory:"
        result = commit(state)

        # No editorial reader in mock mode -> accept
        assert result["verdict"] == "accept"
        # editorial_notes should be None when reader is unavailable
        assert result["commit_result"]["editorial_notes"] is None

    def test_commit_hard_failure_reverts(self):
        """Hard structural failure should produce 'revert' verdict."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _make_scene_state(
            prose=COHERENT_PROSE,
            orient_result={
                "warnings": [
                    {
                        "type": "location_conflict",
                        "character": "Ryn",
                        "location_a": "A",
                        "location_b": "B",
                    }
                ]
            },
        )
        state["_db_path"] = ":memory:"
        result = commit(state)

        assert result["verdict"] == "revert"
        assert result["commit_result"]["hard_failure"] is True


# ---------------------------------------------------------------------------
# Enriched judge context
# ---------------------------------------------------------------------------


class TestBuildEditorialContext:
    """Tests for _build_editorial_context in commit.py."""

    def test_extracts_previous_scene(self):
        from domains.fantasy_daemon.phases.commit import _build_editorial_context

        state = {"recent_prose": "The wolf howled at the moon."}
        ctx = _build_editorial_context(state)
        assert ctx["previous_scene"] == "The wolf howled at the moon."

    def test_extracts_canon_from_orient_result(self):
        from domains.fantasy_daemon.phases.commit import _build_editorial_context

        state = {
            "orient_result": {
                "canon_context": "### Characters\n\nCorin is a young healer.",
            },
        }
        ctx = _build_editorial_context(state)
        assert "Corin is a young healer" in ctx["canon_facts"]

    def test_extracts_direction_notes_from_notes_store(self, tmp_path):
        from domains.fantasy_daemon.phases.commit import _build_editorial_context
        from tinyassets.notes import add_note

        add_note(
            tmp_path,
            source="user",
            text="Focus on the antagonist's backstory.",
            category="direction",
        )
        state = {"_universe_path": str(tmp_path)}
        ctx = _build_editorial_context(state)
        assert "antagonist" in ctx["direction_notes"]

    def test_empty_state_returns_empty_strings(self):
        from domains.fantasy_daemon.phases.commit import _build_editorial_context

        ctx = _build_editorial_context({})
        assert ctx["previous_scene"] == ""
        assert ctx["canon_facts"] == ""
        assert ctx["direction_notes"] == ""


class TestEditorialReaderEnrichedContext:
    """Tests that the editorial reader receives enriched context."""

    def test_editorial_prompt_includes_context(self):
        """Verify the editorial reader receives enriched context."""
        from tinyassets.evaluation.editorial import read_editorial

        captured_prompts = []

        def mock_call(prompt, system, role="judge"):
            captured_prompts.append(prompt)
            return json.dumps({
                "protect": ["vivid imagery"],
                "concerns": [],
                "next_scene": "Continue the tension.",
            })

        read_editorial(
            "Test prose here.",
            previous_scene="The wolf howled.",
            canon_facts="Corin is a healer.",
            direction_notes="Focus on tension.",
            provider_call=mock_call,
        )

        assert len(captured_prompts) == 1
        prompt = captured_prompts[0]
        assert "## Previous Scene" in prompt
        assert "The wolf howled." in prompt
        assert "## Canon Facts" in prompt
        assert "Corin is a healer." in prompt
        assert "## Active Direction Notes" in prompt
        assert "Focus on tension." in prompt


class TestConsistencyAuditNotes:
    def test_builds_notes_from_structural_and_process_failures(self):
        from domains.fantasy_daemon.phases.commit import _build_consistency_audit_notes
        from tinyassets.evaluation.process import ProcessCheck, ProcessEvaluation
        from tinyassets.evaluation.structural import CheckResult, StructuralResult

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

        notes = _build_consistency_audit_notes(
            "test-universe-B1-C1-S1", structural, process_eval
        )

        assert len(notes) == 2
        assert notes[0].source == "structural"
        assert notes[0].category == "error"
        assert "canon name Corin" in notes[0].text
        assert notes[0].target == "test-universe-B1-C1-S1"
        assert notes[1].source == "system"
        assert notes[1].category == "concern"
        assert "Writer tool usage" in notes[1].text


# ---------------------------------------------------------------------------
# Observation-based output and editorial concerns
# ---------------------------------------------------------------------------


class TestObservationNotes:
    """Structural checks produce reader-style observations, not just violations."""

    def test_coherence_observation_low_overlap(self):
        """Low coherence produces a disconnected-sentences observation."""
        # Single-word sentences guarantee zero content-word overlap
        prose = "Cats. Dogs. Frogs. Birds. Wolves. Snakes. Hawks. Deer."
        result = _check_taaco_coherence(prose)
        assert not result.passed, "Expected coherence check to fail on zero-overlap prose"
        assert "disconnected" in result.observation.lower()

    def test_readability_observation_dense_prose(self):
        """Very dense prose produces a complexity observation."""
        # One extremely long sentence with multisyllabic words
        dense = (
            "The quintessentially multifaceted juxtaposition of the "
            "antidisestablishmentarian philosophies permeated the "
            "extraordinarily circumnavigated architectural structures "
            "of the incomprehensibly magnificent metropolis."
        )
        result = _check_readability(dense)
        assert not result.passed, "Expected readability check to fail on dense prose"
        assert "dense" in result.observation.lower()

    def test_pacing_observation_no_dialogue(self):
        """Scene with no dialogue produces a dialogue observation."""
        # Long prose with zero dialogue triggers the "very little dialogue" path
        prose = (
            "The knight walked through the forest. Trees towered above him. "
            "Wind rustled the ancient leaves overhead. Birds sang softly. "
            "Shadows lengthened across the mossy ground beneath the canopy. "
        ) * 15
        result = _check_pacing(prose)
        assert result.observation, "Expected pacing observation for dialogue-free scene"
        assert "dialogue" in result.observation.lower()

    def test_chekhov_observation(self):
        """Overdue promises produce an observation about forgotten threads."""
        state = _make_scene_state(
            scene_number=10,
            orient_result={
                "active_promises": [
                    {"description": "the locked chest", "introduced_scene": 2},
                ],
            },
        )
        result = _check_chekhov(state)
        assert result.observation
        assert "promise" in result.observation.lower()

    def test_canon_breach_observation(self):
        """Canon contradiction produces observation, not hard failure."""
        state = _make_scene_state(
            extracted_facts=[{"fact": "Ryn has brown eyes"}],
            retrieved_context={
                "canon_facts": [{"fact": "Ryn has blue eyes"}],
            },
        )
        result = _check_canon_breach(state)
        assert not result.passed
        assert result.observation
        assert "contradiction" in result.observation.lower()

    def test_passing_check_has_no_observation(self):
        """Checks that pass should not produce observations."""
        result = _check_readability(COHERENT_PROSE)
        assert result.passed
        assert result.observation == ""

    def test_check_result_observation_field_defaults_empty(self):
        """CheckResult.observation defaults to empty string."""
        cr = CheckResult(name="test", passed=True, score=1.0)
        assert cr.observation == ""


class TestToEditorialConcerns:
    """StructuralResult.to_editorial_concerns produces EditorialConcern objects."""

    def test_hard_failure_becomes_clearly_wrong(self):
        """Hard failure checks produce clearly_wrong=True concerns."""
        evaluator = StructuralEvaluator()
        state = _make_scene_state(
            prose=COHERENT_PROSE,
            orient_result={
                "warnings": [{
                    "type": "location_conflict",
                    "character": "Ryn",
                    "location_a": "Ashwater",
                    "location_b": "Northern Pass",
                }],
            },
        )
        result = evaluator.evaluate(state)
        concerns = result.to_editorial_concerns()

        clearly_wrong = [c for c in concerns if c.clearly_wrong]
        assert len(clearly_wrong) > 0
        assert any("location" in c.text.lower() for c in clearly_wrong)

    def test_soft_observation_becomes_not_clearly_wrong(self):
        """Soft observations produce clearly_wrong=False concerns."""
        evaluator = StructuralEvaluator()
        state = _make_scene_state(
            prose=COHERENT_PROSE,
            extracted_facts=[{"fact": "Ryn has brown eyes"}],
            retrieved_context={
                "canon_facts": [{"fact": "Ryn has blue eyes"}],
            },
        )
        result = evaluator.evaluate(state)
        concerns = result.to_editorial_concerns()

        soft = [c for c in concerns if not c.clearly_wrong]
        assert len(soft) > 0
        assert any("contradiction" in c.text.lower() for c in soft)

    def test_no_issues_produces_no_concerns(self):
        """Clean prose produces zero editorial concerns."""
        evaluator = StructuralEvaluator()
        state = _make_scene_state(prose=COHERENT_PROSE)
        result = evaluator.evaluate(state)
        concerns = result.to_editorial_concerns()
        assert len(concerns) == 0

    def test_concerns_are_editorial_concern_instances(self):
        """Returned objects are proper EditorialConcern instances."""
        from tinyassets.evaluation.editorial import EditorialConcern

        evaluator = StructuralEvaluator()
        state = _make_scene_state(
            prose=COHERENT_PROSE,
            orient_result={
                "warnings": [{
                    "type": "location_conflict",
                    "character": "Ryn",
                    "location_a": "here",
                    "location_b": "there",
                }],
            },
        )
        result = evaluator.evaluate(state)
        concerns = result.to_editorial_concerns()
        for c in concerns:
            assert isinstance(c, EditorialConcern)


# ---------------------------------------------------------------------------
# Premise grounding
# ---------------------------------------------------------------------------


class TestExtractPremiseTerms:
    """Tests for _extract_premise_terms helper."""

    def test_extracts_proper_nouns(self):
        premise = "Loral Duskspore is a young mycologist in the Underhallow."
        protag, terms = _extract_premise_terms(premise)
        assert "Loral" in terms
        assert "Duskspore" in terms
        assert "Underhallow" in terms

    def test_protagonist_is_noun_before_verb(self):
        premise = "Loral Duskspore explores the Mycelia network."
        protag, _ = _extract_premise_terms(premise)
        # "Duskspore" is followed by "explores" (verb), so it's the protagonist
        assert protag == ["Duskspore"]

    def test_protagonist_after_location(self):
        """Protagonist should be identified even when a location comes first."""
        premise = "In the Underhallow, Loral Duskspore studies Mycoturgy."
        protag, terms = _extract_premise_terms(premise)
        # "Loral" is followed by "Duskspore" which ends in consonant,
        # but "Duskspore" is followed by "studies" which is a verb.
        # The heuristic should find "Duskspore" as protagonist since
        # it's followed by verb "studies". Either Loral or Duskspore
        # is acceptable — what matters is it's not "Underhallow".
        assert protag[0] in ("Loral", "Duskspore")
        assert protag[0] != "Underhallow"

    def test_empty_premise(self):
        protag, terms = _extract_premise_terms("")
        assert protag == []
        assert terms == []

    def test_skips_common_words(self):
        premise = "The story is about Ryn in Ashwater."
        _, terms = _extract_premise_terms(premise)
        # "The" should be excluded
        assert "The" not in terms
        assert "Ryn" in terms
        assert "Ashwater" in terms


class TestPremiseGrounding:
    """Tests for _check_premise_grounding structural check."""

    def test_correct_universe_passes(self):
        state = _make_scene_state(
            prose=(
                "Loral Duskspore knelt beside the germination pool in the "
                "Underhallow, watching the Mycelia threads pulse with light."
            ),
        )
        state["workflow_instructions"] = {
            "premise": (
                "Loral Duskspore is a young mycologist in the Underhallow, "
                "studying the Mycelia network."
            ),
        }
        result = _check_premise_grounding(state)
        assert result.passed is True
        assert result.score > 0.5

    def test_wrong_protagonist_degrades_score(self):
        """Missing protagonist lowers score but doesn't add a violation
        (protagonist heuristic is best-effort, not a hard signal)."""
        state = _make_scene_state(
            prose=(
                "The Underhallow caverns stretched deep beneath the surface. "
                "Mycoturgy threads pulsed with ancient light."
            ),
        )
        state["workflow_instructions"] = {
            "premise": (
                "Loral Duskspore is a young mycologist in the Underhallow."
            ),
        }
        result = _check_premise_grounding(state)
        # World terms present (Underhallow) so no zero-terms hard fail,
        # but protagonist "Duskspore" (identified by verb "is") absent
        # -> score penalty + observation
        assert result.score < 1.0
        assert result.details["protagonist_found"] is False
        assert result.observation  # should note missing protagonist

    def test_total_departure_fails(self):
        """Completely wrong universe prose triggers hard failure."""
        state = _make_scene_state(
            prose=(
                "Caro walked through the village of Durnhollow, her dog Ashka "
                "padding along beside her through the Vael Reach."
            ),
        )
        state["workflow_instructions"] = {
            "premise": (
                "Loral Duskspore is a young mycologist in the Underhallow."
            ),
        }
        result = _check_premise_grounding(state)
        assert result.passed is False
        assert any("Zero premise world terms" in v for v in result.violations)

    def test_zero_world_terms_fails(self):
        state = _make_scene_state(
            prose=(
                "Wren crept through the ruins of Durnhollow, watching "
                "for signs of the Torben raiders from the east."
            ),
        )
        state["workflow_instructions"] = {
            "premise": (
                "Loral Duskspore studies Mycoturgy in the Underhallow, "
                "navigating the Mycelia and the Germination chambers."
            ),
        }
        result = _check_premise_grounding(state)
        assert result.passed is False
        assert any("Zero premise world terms" in v for v in result.violations)

    def test_no_premise_passes(self):
        """No premise available means we can't check -- pass by default."""
        state = _make_scene_state(prose="Ryn walked through the forest.")
        result = _check_premise_grounding(state)
        assert result.passed is True

    def test_premise_kernel_used_as_fallback(self):
        state = _make_scene_state(
            prose="Loral explored the Underhallow depths.",
        )
        state["premise_kernel"] = (
            "Loral Duskspore in the Underhallow."
        )
        result = _check_premise_grounding(state)
        assert result.passed is True

    def test_program_md_disk_fallback(self, tmp_path):
        """Read premise from PROGRAM.md on disk when state has none."""
        program = tmp_path / "PROGRAM.md"
        program.write_text(
            "Loral Duskspore is a mycologist in the Underhallow.",
            encoding="utf-8",
        )
        state = _make_scene_state(
            prose="Loral explored the Underhallow caverns.",
        )
        state["_universe_path"] = str(tmp_path)
        # No premise in workflow_instructions or premise_kernel
        result = _check_premise_grounding(state)
        assert result.passed is True
        assert result.details["world_terms_found"] > 0

    def test_cross_universe_contamination(self):
        """Characters from another universe should trigger failure."""
        state = _make_scene_state(
            prose=(
                "Daeren and Wren rode through Ashwater while Torben "
                "watched from the hills of Vael Reach."
            ),
        )
        state["workflow_instructions"] = {
            "premise": (
                "Loral Duskspore navigates the Mycelia in the Underhallow."
            ),
        }
        result = _check_premise_grounding(state)
        assert result.passed is False

    def test_evaluator_includes_premise_check(self):
        """StructuralEvaluator should include premise_grounding in its checks."""
        evaluator = StructuralEvaluator()
        state = _make_scene_state(
            prose=(
                "Loral Duskspore knelt beside the germination pool in the "
                "Underhallow, watching the Mycelia threads pulse with light. "
                "The spores drifted upward through the cavern. "
                "She traced the familiar patterns of Mycoturgy."
            ),
        )
        state["workflow_instructions"] = {
            "premise": (
                "Loral Duskspore is a young mycologist in the Underhallow."
            ),
        }
        result = evaluator.evaluate(state)
        check_names = [c.name for c in result.checks]
        assert "premise_grounding" in check_names

    def test_hard_failure_on_total_departure(self):
        """Total premise departure should trigger hard_failure."""
        evaluator = StructuralEvaluator()
        state = _make_scene_state(
            prose=(
                "Wren crept through the ruins of Durnhollow while Daeren "
                "watched from the hills of Vael Reach. The dog Ashka "
                "padded silently through the cobblestones ahead."
            ),
        )
        state["workflow_instructions"] = {
            "premise": (
                "Loral Duskspore studies Mycoturgy in the Underhallow, "
                "navigating the Mycelia and the Germination chambers."
            ),
        }
        result = evaluator.evaluate(state)
        assert result.hard_failure is True
