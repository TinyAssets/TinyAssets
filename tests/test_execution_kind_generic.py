"""Phase C.2 — execution_kind genericization.

Per taskproducer_phase_c.md (archived in git) §2 + §7.

Contract:
- `tinyassets.work_targets` exports ONLY `EXECUTION_KIND_NOTES` among
  the execution-kind constants. BOOK/CHAPTER/SCENE and
  `infer_execution_scope` live at `domains.fantasy_daemon.work_kinds`.
- `FANTASY_EXECUTION_KINDS` reflects the full fantasy set.
- `infer_fantasy_execution_scope` produces the same scope dict the
  previous generic `infer_execution_scope` did for the same fantasy
  inputs — no behavior change in C.2.
"""

from __future__ import annotations


def test_workflow_work_targets_only_exports_notes():
    """Generic engine must not re-export fantasy execution_kind constants.

    Tests the barrier: if a non-fantasy domain imports
    `tinyassets.work_targets`, they must not see BOOK/CHAPTER/SCENE.
    """
    import tinyassets.work_targets as wt

    assert hasattr(wt, "EXECUTION_KIND_NOTES")
    assert not hasattr(wt, "EXECUTION_KIND_BOOK")
    assert not hasattr(wt, "EXECUTION_KIND_CHAPTER")
    assert not hasattr(wt, "EXECUTION_KIND_SCENE")
    assert not hasattr(wt, "infer_execution_scope")


def test_fantasy_work_kinds_exposes_four_values():
    from domains.fantasy_daemon.work_kinds import (
        EXECUTION_KIND_BOOK,
        EXECUTION_KIND_CHAPTER,
        EXECUTION_KIND_NOTES,
        EXECUTION_KIND_SCENE,
        FANTASY_EXECUTION_KINDS,
    )

    assert EXECUTION_KIND_NOTES == "notes"
    assert EXECUTION_KIND_BOOK == "book"
    assert EXECUTION_KIND_CHAPTER == "chapter"
    assert EXECUTION_KIND_SCENE == "scene"
    assert FANTASY_EXECUTION_KINDS == (
        "notes", "book", "chapter", "scene",
    )


def test_infer_fantasy_execution_scope_handles_notes(tmp_path):
    """ROLE_NOTES target → execution_kind=notes + just target_id."""
    from domains.fantasy_daemon.work_kinds import (
        infer_fantasy_execution_scope,
    )
    from tinyassets.work_targets import ROLE_NOTES, WorkTarget

    target = WorkTarget(
        target_id="universe-notes",
        title="Universe Notes",
        role=ROLE_NOTES,
    )
    scope = infer_fantasy_execution_scope(target)
    assert scope == {
        "execution_kind": "notes",
        "target_id": "universe-notes",
    }


def test_infer_fantasy_execution_scope_infers_book_from_default():
    """Publishable target with no kind/scene/chapter hint → BOOK default."""
    from domains.fantasy_daemon.work_kinds import (
        infer_fantasy_execution_scope,
    )
    from tinyassets.work_targets import (
        PUBLISH_STAGE_PROVISIONAL,
        ROLE_PUBLISHABLE,
        WorkTarget,
    )

    target = WorkTarget(
        target_id="book-1",
        title="Book One",
        role=ROLE_PUBLISHABLE,
        publish_stage=PUBLISH_STAGE_PROVISIONAL,
        metadata={"book_number": 1},
    )
    scope = infer_fantasy_execution_scope(target)
    assert scope["execution_kind"] == "book"
    assert scope["book_number"] == 1


def test_infer_fantasy_execution_scope_infers_scene_from_tags():
    """tags=['scene'] + metadata drives SCENE classification."""
    from domains.fantasy_daemon.work_kinds import (
        infer_fantasy_execution_scope,
    )
    from tinyassets.work_targets import (
        PUBLISH_STAGE_PROVISIONAL,
        ROLE_PUBLISHABLE,
        WorkTarget,
    )

    target = WorkTarget(
        target_id="book-1-ch-2-scene-3",
        title="Scene 3",
        role=ROLE_PUBLISHABLE,
        publish_stage=PUBLISH_STAGE_PROVISIONAL,
        tags=["scene"],
        metadata={
            "book_number": 1,
            "chapter_number": 2,
            "scene_number": 3,
        },
    )
    scope = infer_fantasy_execution_scope(target)
    assert scope["execution_kind"] == "scene"
    assert scope["book_number"] == 1
    assert scope["chapter_number"] == 2
    assert scope["scene_number"] == 3


def test_infer_fantasy_execution_scope_none_target():
    from domains.fantasy_daemon.work_kinds import (
        infer_fantasy_execution_scope,
    )

    assert infer_fantasy_execution_scope(None) == {}
