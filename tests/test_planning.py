"""Tests for HTN planner, DOME expansion, and constraint synthesis.

Validates:
- HTN decomposition produces structured outlines with preconditions/effects
- DOME expansion deepens outlines with beats and tension curves
- Constraint synthesis EXTRACT/GENERATE modes produce valid ConstraintSurfaces
"""

from __future__ import annotations

import pytest

from tinyassets.constraints.constraint_surface import (
    empty_constraint_surface,
    score_constraint_surface,
)
from tinyassets.constraints.constraint_synthesis import ConstraintSynthesis
from tinyassets.planning.dome_expansion import DOMEExpander
from tinyassets.planning.htn_planner import HTNPlanner

pytestmark = pytest.mark.usefixtures("connected_fantasy_model", "canonical_runtime")

# ------------------------------------------------------------------
# HTN Planner
# ------------------------------------------------------------------


def test_htn_decompose_goal():
    """HTN decomposes a goal into acts/chapters/scenes."""
    planner = HTNPlanner()
    outline = planner.decompose("A young mage discovers a forbidden power")

    assert len(outline["acts"]) == 3  # Three-act structure
    assert outline["premise_kernel"] == "A young mage discovers a forbidden power"
    assert len(outline["thematic_core"]) > 0
    assert len(outline["characters_extracted"]) >= 3

    # Each act has chapters
    for act in outline["acts"]:
        assert len(act["chapters"]) >= 3
        assert len(act["name"]) > 0
        assert len(act["summary"]) > 0

        # Each chapter has scenes
        for chapter in act["chapters"]:
            assert len(chapter["scenes"]) >= 3
            assert len(chapter["title"]) > 0


def test_htn_preconditions_tracked():
    """Each decomposition level has preconditions and effects."""
    planner = HTNPlanner()
    outline = planner.decompose("War breaks out between two kingdoms")

    # Act I has no preconditions (it's the first act)
    assert outline["acts"][0]["preconditions"] == []
    # Act I has effects that become preconditions for Act II
    act1_effects = outline["acts"][0]["effects"]
    assert len(act1_effects) > 0

    act2_preconditions = outline["acts"][1]["preconditions"]
    # Act II depends on Act I's effects
    assert len(act2_preconditions) > 0
    for pre in act2_preconditions:
        assert pre in act1_effects

    # Chapters within an act track cumulative preconditions
    act = outline["acts"][0]
    for i, chapter in enumerate(act["chapters"]):
        if i == 0:
            assert chapter["preconditions"] == []
        else:
            assert len(chapter["preconditions"]) > 0

    # Scenes within a chapter track cumulative preconditions
    chapter = outline["acts"][0]["chapters"][0]
    for i, scene in enumerate(chapter["scenes"]):
        if i == 0:
            assert scene["preconditions"] == []
        else:
            assert len(scene["preconditions"]) > 0


def test_htn_total_scene_count():
    """Decomposition produces a reasonable total scene count."""
    planner = HTNPlanner()
    outline = planner.decompose("Hero's journey to reclaim a lost artifact")

    total_scenes = sum(
        len(chapter["scenes"])
        for act in outline["acts"]
        for chapter in act["chapters"]
    )
    # 3 acts * ~3-5 chapters * ~3-5 scenes = roughly 27-75
    assert 20 <= total_scenes <= 100


# ------------------------------------------------------------------
# DOME Expansion
# ------------------------------------------------------------------


def test_dome_expand_outline():
    """DOME expands a sparse outline into detailed scenes with beats."""
    planner = HTNPlanner()
    outline = planner.decompose("A rebellion against an unjust ruler")

    expander = DOMEExpander(max_depth=1)
    detailed = expander.expand(outline)

    assert detailed["premise_kernel"] == outline["premise_kernel"]
    assert detailed["expansion_depth"] == 1
    assert detailed["kg_enriched"] is False

    # Every scene should now have beats
    for act in detailed["acts"]:
        for chapter in act["chapters"]:
            for scene in chapter["scenes"]:
                assert "beats" in scene
                assert len(scene["beats"]) > 0
                assert "tension_curve" in scene
                assert len(scene["tension_curve"]) == len(scene["beats"])


def test_dome_respects_max_depth():
    """Expansion depth matches the configured max_depth."""
    planner = HTNPlanner()
    outline = planner.decompose("A quest for a hidden treasure")

    expander_d1 = DOMEExpander(max_depth=1)
    result_d1 = expander_d1.expand(outline)
    assert result_d1["expansion_depth"] == 1

    expander_d3 = DOMEExpander(max_depth=3)
    result_d3 = expander_d3.expand(outline)
    assert result_d3["expansion_depth"] == 3


def test_dome_tension_curves_valid():
    """Tension values are in [0.0, 1.0] range."""
    planner = HTNPlanner()
    outline = planner.decompose("Survival in a harsh wasteland")

    expander = DOMEExpander(max_depth=2)
    detailed = expander.expand(outline)

    for act in detailed["acts"]:
        for chapter in act["chapters"]:
            for scene in chapter["scenes"]:
                for t in scene["tension_curve"]:
                    assert 0.0 <= t <= 1.0, f"Tension {t} out of range"


def test_dome_with_kg_feedback():
    """DOME marks kg_enriched=True when feedback is provided."""
    planner = HTNPlanner()
    outline = planner.decompose("Dragons return to a world that forgot them")

    expander = DOMEExpander(max_depth=1)
    kg_feedback = {"entities": ["dragon", "elder"], "relationships": []}
    detailed = expander.expand(outline, kg_feedback=kg_feedback)

    assert detailed["kg_enriched"] is True


# ------------------------------------------------------------------
# Constraint Synthesis
# ------------------------------------------------------------------


def test_constraint_synthesis_classify_sparse():
    """Sparse input is classified as GENERATE."""
    mode = ConstraintSynthesis.classify_input(
        "A world where magic has a price", None
    )
    assert mode == "GENERATE"


def test_constraint_synthesis_classify_rich():
    """Rich source documents are classified as EXTRACT."""
    long_doc = "word " * 600  # 600 words
    mode = ConstraintSynthesis.classify_input(
        "A world where magic has a price", [long_doc]
    )
    assert mode == "EXTRACT"


def test_constraint_synthesis_classify_short_docs():
    """Short source documents still classify as GENERATE."""
    short_doc = "A brief note about the world."
    mode = ConstraintSynthesis.classify_input(
        "A world where magic has a price", [short_doc]
    )
    assert mode == "GENERATE"


def test_constraint_synthesis_generate():
    """GENERATE mode produces a valid ConstraintSurface."""
    # Use base_rules_path="" to skip loading world_rules.lp
    from tinyassets.constraints.asp_engine import ASPEngine

    engine = ASPEngine(base_rules_path="")
    synth = ConstraintSynthesis(asp_engine=engine)

    surface = synth.process("A rebellion against a corrupt theocracy")

    assert surface["premise_kernel"] == "A rebellion against a corrupt theocracy"
    assert surface["constraint_depth_score"] > 0.0
    assert surface["ready_to_write"] is True  # Should reach readiness after iterations
    assert len(surface.get("characters", [])) >= 3


def test_constraint_synthesis_extract():
    """EXTRACT mode processes rich source into a ConstraintSurface."""
    from tinyassets.constraints.asp_engine import ASPEngine

    engine = ASPEngine(base_rules_path="")
    synth = ConstraintSynthesis(asp_engine=engine)

    source = """
    The Kingdom of Aldara is ruled by the Church of the Eternal Flame.
    Magic users must never use power without a tithe of blood.
    The Guild of Artificers controls all trade in magical artifacts.
    Ryn is a young apprentice who discovers forbidden knowledge.
    Kael is the guild master who hides a dark secret.
    The Northern Pass connects the mountain city to the lowlands.
    The Great War, 200 years ago, destroyed the old empire.
    Writers must avoid info dumps and purple prose.
    The tone should be dark and grounded, never whimsical.
    Water is scarce in the southern desert, controlled by the Church.
    """ * 60  # Repeat to exceed word threshold

    surface = synth.process(
        "A world where magic demands blood sacrifice",
        source_documents=[source],
    )

    assert surface["premise_kernel"] == "A world where magic demands blood sacrifice"
    assert surface["ready_to_write"] is True


def test_constraint_surface_scoring_incremental():
    """Score increases as fields are populated."""
    surface = empty_constraint_surface()
    score_empty = score_constraint_surface(surface)
    assert score_empty == 0.0

    surface["premise_kernel"] = "Test premise"
    score_with_premise = score_constraint_surface(surface)
    assert score_with_premise > score_empty

    surface["characters"] = [{"name": "A"}, {"name": "B"}, {"name": "C"}]
    score_with_chars = score_constraint_surface(surface)
    assert score_with_chars > score_with_premise

    surface["forcing_constraints"] = ["Rule 1", "Rule 2", "Rule 3"]
    score_with_constraints = score_constraint_surface(surface)
    assert score_with_constraints > score_with_chars


def test_constraint_synthesis_never_blocks():
    """Synthesis always returns (never-block rule), even with max iterations."""
    from tinyassets.constraints.asp_engine import ASPEngine

    engine = ASPEngine(base_rules_path="")
    synth = ConstraintSynthesis(asp_engine=engine)

    # Even a minimal premise should produce a result
    surface = synth.process("Elves.")
    assert surface["ready_to_write"] is True
    assert surface["constraint_depth_score"] >= 0.0


# ------------------------------------------------------------------
# Plan node integration with HTN / DOME / Constraints
# ------------------------------------------------------------------


def test_plan_node_uses_htn_dome_when_goal_present():
    """Plan node runs HTN/DOME when a book-level goal is in orient_result."""
    from domains.fantasy_daemon.phases.plan import plan

    state = {
        "orient_result": {
            "scene_id": "s1",
            "premise": "A young mage discovers a forbidden power",
            "overdue_promises": [],
            "pacing_flags": [],
            "arc_position": "rising_action",
        },
        "chapter_number": 1,
        "scene_number": 1,
        "retrieved_context": {},
        "workflow_instructions": {},
    }

    result = plan(state)

    assert "plan_output" in result
    assert "quality_trace" in result

    plan_output = result["plan_output"]
    assert plan_output["scene_id"] == "s1"
    assert len(plan_output["beats"]) >= 1

    # Structural guidance should be present
    assert plan_output["structural_guidance"] is not None
    assert len(plan_output["structural_guidance"]) > 0

    # Quality trace should record HTN/DOME usage
    trace = result["quality_trace"][0]
    assert trace["htn_used"] is True
    assert trace["dome_used"] is True
    assert trace["goal"] == "A young mage discovers a forbidden power"


def test_plan_node_skips_htn_dome_without_goal():
    """Plan node works normally when no book-level goal is available."""
    from domains.fantasy_daemon.phases.plan import plan

    state = {
        "orient_result": {
            "scene_id": "s2",
            "overdue_promises": [],
            "pacing_flags": [],
            "arc_position": "rising_action",
        },
        "chapter_number": 1,
        "scene_number": 1,
        "retrieved_context": {},
        "workflow_instructions": {},
    }

    result = plan(state)

    plan_output = result["plan_output"]
    assert plan_output["scene_id"] == "s2"
    assert len(plan_output["beats"]) >= 1

    # No structural guidance without a goal
    assert plan_output["structural_guidance"] is None

    trace = result["quality_trace"][0]
    assert trace["htn_used"] is False
    assert trace["dome_used"] is False


def test_plan_node_goal_from_workflow_instructions():
    """Plan node finds a goal in workflow_instructions if not in orient_result."""
    from domains.fantasy_daemon.phases.plan import plan

    state = {
        "orient_result": {
            "scene_id": "s3",
            "overdue_promises": [],
            "pacing_flags": [],
        },
        "chapter_number": 2,
        "scene_number": 3,
        "retrieved_context": {},
        "workflow_instructions": {
            "premise": "War between two rival kingdoms",
        },
    }

    result = plan(state)

    plan_output = result["plan_output"]
    assert plan_output["structural_guidance"] is not None

    trace = result["quality_trace"][0]
    assert trace["htn_used"] is True
    assert trace["goal"] == "War between two rival kingdoms"


def test_plan_node_goal_from_book_arc():
    """Plan node finds a goal in book_arc state."""
    from domains.fantasy_daemon.phases.plan import plan

    state = {
        "orient_result": {
            "scene_id": "s4",
            "overdue_promises": [],
            "pacing_flags": [],
        },
        "chapter_number": 1,
        "scene_number": 1,
        "retrieved_context": {},
        "workflow_instructions": {},
        "book_arc": {
            "premise": "A heist gone wrong in a magical city",
        },
    }

    result = plan(state)

    trace = result["quality_trace"][0]
    assert trace["htn_used"] is True
    assert trace["goal"] == "A heist gone wrong in a magical city"


def test_plan_node_structural_alignment_scoring():
    """Structural alignment bonus is applied when DOME beats exist."""
    from domains.fantasy_daemon.phases.plan import _score_alternative, _structural_alignment_score

    # Plan beats that match DOME tension pattern
    plan_beats = [
        {"beat_number": 1, "tension": 0.3},
        {"beat_number": 2, "tension": 0.6},
        {"beat_number": 3, "tension": 0.9},
        {"beat_number": 4, "tension": 0.7},
    ]
    dome_beats = [
        {"tension_level": 0.3},
        {"tension_level": 0.6},
        {"tension_level": 0.9},
        {"tension_level": 0.6},
    ]

    # Score with structural guidance
    orient = {"overdue_promises": []}
    score_with = _score_alternative(
        {"beats": plan_beats, "promise_resolutions": []},
        orient,
        structural_beats=dome_beats,
    )

    # Score without structural guidance
    score_without = _score_alternative(
        {"beats": plan_beats, "promise_resolutions": []},
        orient,
        structural_beats=None,
    )

    # Both should be valid scores
    assert 0.0 <= score_with <= 1.0
    assert 0.0 <= score_without <= 1.0

    # Alignment score helper should work
    alignment = _structural_alignment_score(plan_beats, dome_beats)
    assert 0.0 <= alignment <= 1.0
    # Same beat count = at least 0.5 for count match
    assert alignment >= 0.5


def test_plan_node_constraint_validation_in_output():
    """Plan output includes constraint validation results when available."""
    from domains.fantasy_daemon.phases.plan import plan

    state = {
        "orient_result": {
            "scene_id": "s5",
            "premise": "Dragons awaken in a frozen kingdom",
            "overdue_promises": [],
            "pacing_flags": [],
        },
        "chapter_number": 1,
        "scene_number": 1,
        "retrieved_context": {},
        "workflow_instructions": {},
    }

    result = plan(state)
    plan_output = result["plan_output"]

    # constraint_validation may be None if clingo is not installed,
    # but the key should always be present
    assert "constraint_validation" in plan_output
