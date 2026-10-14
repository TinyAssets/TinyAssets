"""Integration test: commit() -> entity extraction -> KG population.

Verifies that _index_prose_entities correctly wires through to the
KnowledgeGraph when runtime.knowledge_graph is set. This closes the
test gap where all prior tests hit the early-return (kg is None).

NOTE: commit() has two separate extraction paths:
  - Fact extraction (_extract_facts) uses call_for_extraction, which
    hits _mock_extraction_response under _FORCE_MOCK=True (conftest).
  - KG indexing (_index_prose_entities) passes commit.text_sync to
    index_text as provider_call, which we patch to return EXTRACTION_JSON.
These tests target the KG indexing path specifically.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from tinyassets.knowledge.knowledge_graph import KnowledgeGraph


@pytest.fixture
def temp_kg(canonical_source, tmp_path):
    """Create a temporary KnowledgeGraph backed by a temp SQLite file."""
    db_path = tmp_path / "test_kg.db"
    kg = KnowledgeGraph(
        source=canonical_source(
            "knowledge",
            key=str(db_path),
            namespaces=[None, "world", "u1", "test", "test-universe", "universe-A"],
        )
    )
    yield kg
    kg.close()


@pytest.fixture
def _set_runtime_kg(temp_kg):
    """Set runtime.knowledge_graph to a real KG for the duration of the test."""
    from tinyassets import runtime_singletons as runtime

    runtime.knowledge_graph = temp_kg
    yield
    runtime.knowledge_graph = None


# Realistic extraction JSON that a provider would return
EXTRACTION_JSON = json.dumps({
    "entities": [
        {
            "entity_id": "corin",
            "entity_type": "character",
            "aliases": ["Corin", "the boy"],
            "description": "A young shepherd standing on the ridge",
            "access_tier": 0,
        },
        {
            "entity_id": "ashka",
            "entity_type": "character",
            "aliases": ["Ashka", "the wolf"],
            "description": "A silver-grey wolf bonded to Corin",
            "access_tier": 0,
        },
        {
            "entity_id": "thornwall",
            "entity_type": "location",
            "aliases": ["Thornwall", "the village"],
            "description": "A small mountain village below the ridge",
            "access_tier": 0,
        },
    ],
    "relationships": [
        {
            "source": "corin",
            "target": "ashka",
            "relation_type": "alliance",
            "weight": 0.9,
            "access_tier": 0,
        },
        {
            "source": "corin",
            "target": "thornwall",
            "relation_type": "membership",
            "weight": 0.7,
            "access_tier": 0,
        },
    ],
    "facts": [
        {
            "text": "Corin has been bonded to Ashka since childhood",
            "source_type": "narrator_claim",
            "language_type": "literal",
            "narrative_function": "world_fact",
            "importance": 0.8,
            "confidence": 0.9,
            "access_tier": 0,
        },
        {
            "text": "Thornwall sits in the shadow of the northern peaks",
            "source_type": "narrator_claim",
            "language_type": "literal",
            "narrative_function": "world_fact",
            "importance": 0.5,
            "confidence": 0.9,
            "access_tier": 0,
        },
    ],
})

SAMPLE_PROSE = """\
Corin stood on the ridge, one hand resting on Ashka's silver-grey fur.
The wolf pressed against his leg, warm and steady as always. Below them,
Thornwall's thatched roofs caught the last of the evening light. Smoke
curled from the smithy chimney, carrying the scent of iron and pine.

He had been bonded to Ashka since childhood -- seven winters now, since
the day the silver pup had walked out of the Mistwood and refused to leave
his side. The village elders said it was a sign. Corin wasn't sure what it
signified, but he was glad of her company on nights like this, when the
wind came down from the northern peaks and the world felt too large.
"""


def _commit_patches():
    """Return context managers that patch commit.py's provider and editorial reader."""
    return (
        patch(
            "domains.fantasy_daemon.phases.commit.text_sync",
            return_value=EXTRACTION_JSON,
        ),
        patch(
            "domains.fantasy_daemon.phases.commit._run_editorial",
            return_value=None,
        ),
    )


class TestCommitKGIntegration:
    """Test that commit() populates the KG via _index_prose_entities."""

    @pytest.mark.usefixtures("_set_runtime_kg")
    def test_commit_populates_kg_with_entities(self, temp_kg, tmp_story_db):
        """Full integration: commit() with real prose -> KG has >0 entities."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _build_commit_state(tmp_story_db)

        # Mock provider to return extraction JSON, stub editorial reader
        p1, p2 = _commit_patches()
        with p1, p2:
            result = commit(state)

        # Verify entities landed in the KG
        entities = temp_kg.query_entities()
        assert len(entities) > 0, (
            f"Expected >0 entities in KG, got {len(entities)}. "
            f"Commit result: {result.get('commit_result', {})}"
        )

        # Verify specific entities we know should be there
        entity_ids = {e["entity_id"] for e in entities}
        assert "corin" in entity_ids
        assert "ashka" in entity_ids

    @pytest.mark.usefixtures("_set_runtime_kg")
    def test_commit_populates_kg_edges(self, temp_kg, tmp_story_db):
        """Commit should also index relationships as edges."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _build_commit_state(tmp_story_db)

        p1, p2 = _commit_patches()
        with p1, p2:
            commit(state)

        edges = temp_kg.get_edges(entity_id="corin")
        assert len(edges) > 0, "Expected edges for 'corin' in KG"
        relation_types = {e["relation_type"] for e in edges}
        assert "alliance" in relation_types

    @pytest.mark.usefixtures("_set_runtime_kg")
    def test_commit_populates_kg_facts(self, temp_kg, tmp_story_db):
        """Commit should index extracted facts into the KG."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _build_commit_state(tmp_story_db)

        p1, p2 = _commit_patches()
        with p1, p2:
            commit(state)

        facts = temp_kg.query_facts()
        assert len(facts) > 0, "Expected >0 facts in KG"
        fact_texts = [f.text for f in facts]
        assert any("bonded" in t for t in fact_texts)

    @pytest.mark.usefixtures("_set_runtime_kg")
    def test_regex_fallback_populates_kg(self, temp_kg, tmp_story_db):
        """When LLM extraction returns bad JSON, regex fallback still populates KG."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _build_commit_state(tmp_story_db)

        # Return non-JSON to trigger regex fallback
        with (
            patch(
                "domains.fantasy_daemon.phases.commit.text_sync",
                return_value="This is not valid JSON at all",
            ),
            patch(
                "domains.fantasy_daemon.phases.commit._run_editorial",
                return_value=None,
            ),
        ):
            commit(state)

        entities = temp_kg.query_entities()
        assert len(entities) > 0, (
            "Regex fallback should have extracted character names from prose"
        )
        entity_ids = {e["entity_id"] for e in entities}
        # Regex extracts capitalized names -- Corin and Ashka should be found
        assert "corin" in entity_ids or "ashka" in entity_ids, (
            f"Expected 'corin' or 'ashka' in entity_ids, got: {entity_ids}"
        )

    def test_kg_none_skips_extraction(self, tmp_story_db):
        """When runtime.knowledge_graph is None, entity indexing is skipped."""
        from domains.fantasy_daemon.phases.commit import commit
        from tinyassets import runtime_singletons as runtime

        runtime.knowledge_graph = None  # Explicit disabled-index unit scenario.
        assert runtime.knowledge_graph is None

        state = _build_commit_state(tmp_story_db)

        p1, p2 = _commit_patches()
        with p1, p2:
            # Should not raise -- just skips extraction
            result = commit(state)

        assert result["verdict"] in ("accept", "second_draft", "revert")

    @pytest.mark.usefixtures("_set_runtime_kg")
    def test_query_entities_after_commit(self, temp_kg, tmp_story_db):
        """Verify query_entities with type filter works after commit."""
        from domains.fantasy_daemon.phases.commit import commit

        state = _build_commit_state(tmp_story_db)

        p1, p2 = _commit_patches()
        with p1, p2:
            commit(state)

        characters = temp_kg.query_entities(entity_type="character")
        locations = temp_kg.query_entities(entity_type="location")

        assert len(characters) >= 2, f"Expected >=2 characters, got {len(characters)}"
        assert len(locations) >= 1, f"Expected >=1 location, got {len(locations)}"


def _build_commit_state(db_path: str) -> dict:
    """Build a minimal state dict that commit() can process."""
    from domains.fantasy_daemon.phases.world_state_db import init_db

    init_db(db_path)

    return {
        "universe_id": "test-universe",
        "book_number": 1,
        "chapter_number": 1,
        "scene_number": 1,
        "_db_path": db_path,
        "_universe_path": "",
        "draft_output": {
            "scene_id": "b1_c1_s1",
            "prose": SAMPLE_PROSE,
            "word_count": len(SAMPLE_PROSE.split()),
            "is_revision": False,
        },
        "second_draft_used": False,
        "orient_result": {},
        "plan_output": {"beats": []},
        "recent_prose": "",
        "retrieved_context": {},
        "memory_context": {},
        "extracted_facts": [],
        "extracted_promises": [],
        "style_observations": [],
        "quality_trace": [],
        "quality_debt": [],
        "verdict": "",
    }


pytestmark = pytest.mark.usefixtures("canonical_runtime")
