"""Tests for the explicit writer tool surface."""

from __future__ import annotations

import pytest

from domains.fantasy_daemon.phases.orient import orient
from domains.fantasy_daemon.phases.writer_tools import select_and_run_writer_tools
from tinyassets.notes import add_note, list_notes
from tinyassets.universe_soul import write_universe_soul


def _state_with_context(tmp_path) -> dict:
    canon_dir = tmp_path / "canon"
    canon_dir.mkdir()
    (canon_dir / "world.md").write_text(
        "Ashwater stands beneath a ring of stormglass towers.",
        encoding="utf-8",
    )
    return {
        "universe_id": "test",
        "book_number": 1,
        "chapter_number": 1,
        "scene_number": 1,
        "_universe_path": str(tmp_path),
        "orient_result": {
            "scene_id": "test-B1-C1-S1",
            "world_state": {
                "chapter_number": 1,
                "scene_number": 1,
                "chapter_avg_words": 900,
                "active_promises": [{"text": "The glass gate must open."}],
                "characters": [
                    {"name": "Ryn", "location": "Ashwater", "emotional_state": "wary"}
                ],
                "recent_scenes": [{"scene_id": "s0", "summary": "Ryn returned to the city."}],
            },
        },
        "retrieved_context": {
            "facts": [{"text": "Ryn once served the harbor watch."}],
            "relationships": [{"source": "Ryn", "relation_type": "trusts", "target": "Kael"}],
            "prose_chunks": ["The harbor bells rang through the fog."],
            "community_summaries": ["Ashwater politics center on the stormglass towers."],
        },
        "memory_context": {
            "recent_summaries": [{"ch": 1, "sc": 1, "summary": "Ryn crossed the lower ward."}],
            "facts": [{"content": "Kael owes Ryn a favor."}],
            "style_rules": [{"rule": "Keep the tone intimate and tense."}],
        },
        "recent_prose": "Ryn traced the harbor wall with her fingertips.",
    }


class TestWriterTools:
    def test_plan_tools_render_context_and_mark_notes_read(self, tmp_path):
        state = _state_with_context(tmp_path)
        write_universe_soul(
            tmp_path,
            purpose="A civic-memory lens checks every source.",
            hard_lines=("Never invent citations.",),
        )
        add_note(
            tmp_path,
            source="user",
            text="Keep the pressure on the harbor gate.",
            category="direction",
        )

        context, used = select_and_run_writer_tools("plan", state)

        assert "Context Tools" in context
        assert "Story Search" in context
        assert "Routed sources" in context
        assert "Unread Notes" in context
        assert "Universe soul lens pinned to soul_versions/0001.md" in context
        assert "A civic-memory lens checks every source." in context
        assert "Never invent citations." in context
        assert "Keep the pressure on the harbor gate." in context
        assert "stormglass towers" in context
        assert "harbor watch" in context
        assert used == ["story_search"]
        assert len(list_notes(tmp_path, status="read")) == 1

    def test_draft_tools_include_recent_prose_and_revision_feedback(self, tmp_path):
        state = _state_with_context(tmp_path)
        state["_revision_feedback"] = {
            "warnings": ["Scene turn arrives too late."],
            "editorial_notes": {
                "concerns": [{"text": "Ryn speaks too formally.", "clearly_wrong": False}],
                "protect": ["Strong physical atmosphere"],
            },
            "style_observations": [
                {
                    "dimension": "voice",
                    "observation": "Sharpen the interiority.",
                }
            ],
        }

        context, used = select_and_run_writer_tools("draft", state)

        assert "Story Search" in context
        assert "Recent Prose" in context
        assert "Revision Feedback" in context
        assert "Ryn traced the harbor wall" in context
        assert "Scene turn arrives too late." in context
        assert "Sharpen the interiority." in context
        assert "story_search" in used
        assert "recent_prose" in used
        assert "revision_feedback" in used


class TestOrientNotesConsumption:
    def test_orient_leaves_notes_unread_until_writer_tools(self, tmp_path, tmp_story_db):
        add_note(
            tmp_path,
            source="user",
            text="Do not lose sight of the treaty bells.",
            category="direction",
        )
        state = {
            "universe_id": "test",
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
            "_universe_path": str(tmp_path),
            "_db_path": tmp_story_db,
            "_kg_path": "",
        }

        result = orient(state)

        instructions = result.get("workflow_instructions", {})
        assert "workflow_instructions" not in result or "notes" not in instructions
        assert len(list_notes(tmp_path, status="unread")) == 1


pytestmark = pytest.mark.usefixtures("canonical_runtime")
