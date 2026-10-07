"""Tests for WorkTarget persistence, review gates, and read-only surfaces."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from domains.fantasy_daemon.graphs.universe import run_book
from domains.fantasy_daemon.phases.authorial_priority_review import authorial_priority_review
from domains.fantasy_daemon.phases.dispatch_execution import dispatch_execution
from domains.fantasy_daemon.phases.foundation_priority_review import foundation_priority_review
from domains.fantasy_daemon.phases.target_actions import (
    create_provisional_target_from_execution,
    mark_target_for_discard_from_execution,
)
from fantasy_daemon.api import app, configure
from tinyassets.work_targets import (
    LIFECYCLE_DISCARDED,
    LIFECYCLE_MARKED_FOR_DISCARD,
    PUBLISH_STAGE_COMMITTED,
    PUBLISH_STAGE_PROVISIONAL,
    ROLE_NOTES,
    ROLE_PUBLISHABLE,
    commit_publishable_target,
    create_target,
    get_target,
    load_hard_priorities,
    load_work_targets,
    mark_target_for_discard,
    reclassify_target_role,
)


@pytest.fixture
def universe_dir(tmp_path):
    universe_dir = tmp_path / "test-universe"
    universe_dir.mkdir()
    configure(base_path=str(tmp_path), api_key="", daemon=None)
    yield universe_dir
    configure(base_path="", api_key="", daemon=None)


@pytest.fixture
def client(universe_dir):
    return TestClient(app)


def test_notes_to_publishable_requires_provisional_before_committed(universe_dir):
    target = create_target(
        universe_dir,
        title="Old exploratory outline",
        role=ROLE_NOTES,
    )
    promoted = reclassify_target_role(
        universe_dir,
        target.target_id,
        new_role=ROLE_PUBLISHABLE,
        reason="Promote into story candidate",
    )
    assert promoted is not None
    assert promoted.role == ROLE_PUBLISHABLE
    assert promoted.publish_stage == PUBLISH_STAGE_PROVISIONAL

    committed = commit_publishable_target(universe_dir, target.target_id)
    assert committed is not None
    assert committed.publish_stage == PUBLISH_STAGE_COMMITTED


def test_publishable_to_notes_emits_reconciliation_note(universe_dir):
    target = create_target(
        universe_dir,
        title="Candidate novella",
        role=ROLE_PUBLISHABLE,
        publish_stage=PUBLISH_STAGE_COMMITTED,
    )
    demoted = reclassify_target_role(
        universe_dir,
        target.target_id,
        new_role=ROLE_NOTES,
        reason="Better as reference notes",
        create_reconciliation_note=True,
    )
    assert demoted is not None
    assert demoted.role == ROLE_NOTES
    assert demoted.publish_stage != PUBLISH_STAGE_COMMITTED

    notes = json.loads((universe_dir / "notes.json").read_text(encoding="utf-8"))
    assert any(
        note.get("metadata", {}).get("target_id") == target.target_id
        and "reconciliation" in note.get("tags", [])
        for note in notes
    )


def test_marked_for_discard_needs_20_review_cycles(universe_dir):
    target = create_target(universe_dir, title="Bad branch", role=ROLE_NOTES)
    mark_target_for_discard(
        universe_dir,
        target.target_id,
        review_cycle=1,
        reason="No longer wanted",
    )

    foundation_priority_review({
        "_universe_path": str(universe_dir),
        "health": {"review_cycles_completed": 10},
    })
    assert get_target(universe_dir, target.target_id).lifecycle == LIFECYCLE_MARKED_FOR_DISCARD

    foundation_priority_review({
        "_universe_path": str(universe_dir),
        "health": {"review_cycles_completed": 21},
    })
    assert get_target(universe_dir, target.target_id).lifecycle == LIFECYCLE_DISCARDED


def test_foundation_review_hard_blocks_on_unsynthesized_upload(universe_dir):
    (universe_dir / "worldbuild_signals.json").write_text(
        json.dumps([
            {
                "type": "synthesize_source",
                "source_file": "draft-notes.md",
                "detail": "New source file: draft-notes.md",
            }
        ], indent=2),
        encoding="utf-8",
    )

    result = foundation_priority_review({
        "_universe_path": str(universe_dir),
        "health": {"review_cycles_completed": 0},
    })

    assert result["review_stage"] == "foundation"
    assert result["current_task"] == "worldbuild"
    assert result["selected_target_id"]
    priorities = load_hard_priorities(universe_dir)
    assert any(priority.kind == "synthesize_source" for priority in priorities)


def test_authorial_review_returns_one_target_and_alternates(universe_dir):
    create_target(
        universe_dir,
        title="World Notes",
        role=ROLE_NOTES,
        current_intent="compare canon notes",
    )
    create_target(
        universe_dir,
        title="Book One",
        role=ROLE_PUBLISHABLE,
        publish_stage=PUBLISH_STAGE_PROVISIONAL,
        current_intent="continue book one",
    )
    create_target(
        universe_dir,
        title="Short Story Seed",
        role=ROLE_PUBLISHABLE,
        publish_stage=PUBLISH_STAGE_PROVISIONAL,
        current_intent="expand side story",
    )

    result = authorial_priority_review({
        "_universe_path": str(universe_dir),
        "workflow_instructions": {"premise": "A glass kingdom in decline."},
    })

    assert result["selected_target_id"]
    assert result["selected_intent"]
    assert len(result["alternate_target_ids"]) <= 2


def test_dispatch_execution_routes_notes_target_to_worldbuild(universe_dir):
    target = create_target(
        universe_dir,
        title="Canon Comparison",
        role=ROLE_NOTES,
        current_intent="compare source and canon",
    )
    result = dispatch_execution({
        "_universe_path": str(universe_dir),
        "selected_target_id": target.target_id,
        "selected_intent": target.current_intent,
        "alternate_target_ids": [],
    })
    assert result["current_task"] == "worldbuild"
    assert result["current_execution_id"]
    assert result["current_execution_ref"]


def test_execution_can_create_provisional_targets_directly(universe_dir):
    result = create_provisional_target_from_execution(
        {"_universe_path": str(universe_dir)},
        title="The Lantern Road",
        current_intent="spin into a short story",
        tags=["story", "seed"],
    )
    assert len(result["created_target_ids"]) == 1
    targets = load_work_targets(universe_dir)
    created = [target for target in targets if target.target_id in result["created_target_ids"]]
    assert len(created) == 1
    assert created[0].role == ROLE_PUBLISHABLE
    assert created[0].publish_stage == PUBLISH_STAGE_PROVISIONAL


def test_execution_can_only_mark_for_discard(universe_dir):
    target = create_target(universe_dir, title="Throwaway", role=ROLE_NOTES)
    result = mark_target_for_discard_from_execution(
        {
            "_universe_path": str(universe_dir),
            "health": {"review_cycles_completed": 0},
        },
        target_id=target.target_id,
        reason="Dead branch",
    )
    assert result["marked_for_discard"] == [target.target_id]
    assert get_target(universe_dir, target.target_id).lifecycle == LIFECYCLE_MARKED_FOR_DISCARD


def test_api_note_tags_and_metadata_round_trip(client, universe_dir):
    response = client.post(
        "/v1/universes/test-universe/notes",
        json={
            "text": "Track this for reconciliation.",
            "category": "concern",
            "tags": ["reconciliation", "target"],
            "metadata": {"target_id": "book-1"},
        },
    )
    assert response.status_code == 201
    note = response.json()["note"]
    assert "reconciliation" in note["tags"]
    assert note["metadata"]["target_id"] == "book-1"


# ---------------------------------------------------------------------------
# TOMBSTONE — orphan API tests deleted 2026-04-16
# ---------------------------------------------------------------------------
# test_api_work_targets_endpoint asserted GET /v1/universes/{id}/work-targets;
# test_api_review_state_endpoint asserted GET /v1/universes/{id}/review-state.
# Neither HTTP route ever existed on main — `git log -S "/work-targets"` and
# `git log -S "/review-state"` return zero add-commits, and a 50+ route audit
# of tinyassets/api.py + tinyassets/universe_server.py confirms. By design,
# work-targets access is MCP-side. The
# review_state test was additionally malformed (nested-def, mixed indent —
# pytest could not collect it). Same orphan-test pattern as commit d8a4757
# (test_integration.py process-evaluation cleanup). Resurrection would
# require fresh API design.
# ---------------------------------------------------------------------------


def test_run_book_uses_chapter_target_execution_scope(universe_dir):
    target = create_target(
        universe_dir,
        title="Chapter 3",
        role=ROLE_PUBLISHABLE,
        tags=["chapter"],
        metadata={
            "execution_kind": "chapter",
            "book_number": 2,
            "chapter_number": 3,
        },
    )
    captured: dict[str, object] = {}

    class _CompiledGraph:
        def invoke(self, state):
            captured.update(state)
            return {
                "chapters_completed": state["chapters_completed"] + 1,
                "chapter_word_count": 250,
            }

    class _Graph:
        def compile(self):
            return _CompiledGraph()

    with patch("domains.fantasy_daemon.graphs.book.build_book_graph", return_value=_Graph()):
        result = run_book({
            "universe_id": "test",
            "_universe_path": str(universe_dir),
            "_db_path": str(universe_dir / "story.db"),
            "selected_target_id": target.target_id,
            "selected_intent": "continue chapter three",
            "total_chapters": 0,
            "total_words": 0,
            "workflow_instructions": {},
        })

    assert result["total_chapters"] == 1
    assert captured["book_number"] == 2
    assert captured["chapters_completed"] == 2
    assert captured["chapters_target"] == 3
    scope = captured["workflow_instructions"]["execution_scope"]
    assert scope["execution_kind"] == "chapter"
    assert scope["chapter_number"] == 3
    assert scope["scenes_completed"] == 0


def test_run_book_resumes_open_book_target_from_existing_output(universe_dir):
    book_target = create_target(
        universe_dir,
        title="Book One",
        role=ROLE_PUBLISHABLE,
        tags=["book"],
        metadata={"execution_kind": "book", "book_number": 1},
    )
    chapter_dir = universe_dir / "output" / "book-1" / "chapter-02"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "scene-01.md").write_text("First scene.", encoding="utf-8")
    (chapter_dir / "scene-02.md").write_text("Second scene.", encoding="utf-8")
    captured: dict[str, object] = {}

    class _CompiledGraph:
        def invoke(self, state):
            captured.update(state)
            return {
                "chapters_completed": state["chapters_completed"] + 1,
                "chapter_word_count": 300,
            }

    class _Graph:
        def compile(self):
            return _CompiledGraph()

    with patch("domains.fantasy_daemon.graphs.book.build_book_graph", return_value=_Graph()):
        run_book({
            "universe_id": "test",
            "_universe_path": str(universe_dir),
            "_db_path": str(universe_dir / "story.db"),
            "selected_target_id": book_target.target_id,
            "selected_intent": "continue the book",
            "total_chapters": 0,
            "total_words": 0,
            "workflow_instructions": {},
        })

    assert captured["book_number"] == 1
    assert captured["chapters_completed"] == 1
    assert captured["chapters_target"] == 2
    scope = captured["workflow_instructions"]["execution_scope"]
    assert scope["execution_kind"] == "book"
    assert scope["chapter_number"] == 2
    assert scope["scenes_completed"] == 2
    assert scope["last_scene_prose"] == "Second scene."


def test_run_book_uses_scene_target_exact_coordinates(universe_dir):
    scene_target = create_target(
        universe_dir,
        title="Scene 3",
        role=ROLE_PUBLISHABLE,
        tags=["scene"],
        metadata={
            "execution_kind": "scene",
            "book_number": 1,
            "chapter_number": 4,
            "scene_number": 3,
        },
    )
    chapter_dir = universe_dir / "output" / "book-1" / "chapter-04"
    chapter_dir.mkdir(parents=True)
    (chapter_dir / "scene-02.md").write_text("Prior scene.", encoding="utf-8")
    captured: dict[str, object] = {}

    class _CompiledGraph:
        def invoke(self, state):
            captured.update(state)
            return {
                "chapters_completed": state["chapters_completed"] + 1,
                "chapter_word_count": 200,
            }

    class _Graph:
        def compile(self):
            return _CompiledGraph()

    with patch("domains.fantasy_daemon.graphs.book.build_book_graph", return_value=_Graph()):
        run_book({
            "universe_id": "test",
            "_universe_path": str(universe_dir),
            "_db_path": str(universe_dir / "story.db"),
            "selected_target_id": scene_target.target_id,
            "selected_intent": "rewrite this scene",
            "total_chapters": 0,
            "total_words": 0,
            "workflow_instructions": {},
        })

    assert captured["chapters_completed"] == 3
    assert captured["chapters_target"] == 4
    scope = captured["workflow_instructions"]["execution_scope"]
    assert scope["execution_kind"] == "scene"
    assert scope["chapter_number"] == 4
    assert scope["scene_number"] == 3
    assert scope["scenes_completed"] == 2
    assert scope["scenes_target"] == 3
    assert scope["last_scene_prose"] == "Prior scene."
