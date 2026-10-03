"""Tests for retrieval: vector store, router, phase-aware context."""

from __future__ import annotations

import json

import numpy as np
import pytest

from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
from tinyassets.knowledge.models import (
    FactWithContext,
    GraphEdge,
    GraphEntity,
    QueryType,
    RetrievalResult,
    SourceType,
)
from tinyassets.memory.scoping import MemoryScope
from tinyassets.retrieval.agentic_search import (
    assemble_phase_search_context,
    build_phase_query,
)
from tinyassets.retrieval.phase_context import (
    get_phase_config,
    get_token_budget,
    should_use_backend,
)
from tinyassets.retrieval.router import (
    RetrievalRouter,
    _parse_decomposition,
    _simple_decompose,
)
from tinyassets.retrieval.vector_store import VectorStore, reset_db

_SCOPE = MemoryScope(universe_id="test-universe")

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def clean_lancedb():
    """Reset LanceDB singleton between tests."""
    reset_db()
    yield
    reset_db()


@pytest.fixture
def tmp_kg(tmp_path):
    kg = KnowledgeGraph(str(tmp_path / "test.db"))
    # Add some entities and edges
    for eid in ["ryn", "kael", "ashwater"]:
        kg.add_entity(
            GraphEntity(
                entity_id=eid,
                entity_type="character",
                access_tier=0,
                public_description=f"{eid} description",
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
    yield kg
    kg.close()


@pytest.fixture
def tmp_vs(tmp_path):
    return VectorStore(
        db_path=str(tmp_path / "lance"),
        table_name="test_chunks",
        embedding_dim=8,
    )


# ---------------------------------------------------------------------------
# VectorStore tests
# ---------------------------------------------------------------------------


class TestVectorStore:
    def test_singleton_connection(self, tmp_path):
        from tinyassets.retrieval.vector_store import get_db

        db1 = get_db(str(tmp_path / "lance"))
        db2 = get_db(str(tmp_path / "lance"))
        assert db1 is db2

    def test_get_db_rejects_empty_path(self):
        import pytest

        from tinyassets.retrieval.vector_store import get_db

        with pytest.raises(ValueError, match="cross-universe contamination"):
            get_db("")

    def test_vectorstore_rejects_empty_db_path(self):
        import pytest

        with pytest.raises(ValueError, match="cross-universe contamination"):
            VectorStore(db_path="")

    def test_index_and_search(self, tmp_vs):
        chunks = [
            {
                "chunk_id": "c1",
                "text": "The winter wind howled.",
                "scene_id": "1_1_1",
                "chapter_number": 1,
                "character": "ryn",
                "location": "northern_pass",
                "embedding": np.random.randn(8).astype(np.float32),
            },
            {
                "chunk_id": "c2",
                "text": "Flames danced in the hearth.",
                "scene_id": "1_1_2",
                "chapter_number": 1,
                "character": "kael",
                "location": "tavern",
                "embedding": np.random.randn(8).astype(np.float32),
            },
        ]
        count = tmp_vs.index(chunks)
        assert count == 2

        query_emb = np.random.randn(8).astype(np.float32)
        results = tmp_vs.search(query_emb, limit=5)
        assert len(results) == 2
        assert all(r["chunk_id"] != "__seed__" for r in results)

    def test_search_with_filter(self, tmp_vs):
        chunks = [
            {
                "chunk_id": "c1",
                "text": "Winter scene.",
                "scene_id": "1_1_1",
                "chapter_number": 1,
                "embedding": np.random.randn(8).astype(np.float32),
            },
            {
                "chunk_id": "c2",
                "text": "Summer scene.",
                "scene_id": "2_1_1",
                "chapter_number": 2,
                "embedding": np.random.randn(8).astype(np.float32),
            },
        ]
        tmp_vs.index(chunks)

        query_emb = np.random.randn(8).astype(np.float32)
        results = tmp_vs.search(query_emb, limit=5, where="chapter_number = 1")
        assert all(r["chapter_number"] == 1 for r in results)

    def test_empty_index(self, tmp_vs):
        assert tmp_vs.index([]) == 0

    def test_count(self, tmp_vs):
        assert tmp_vs.count() == 0
        tmp_vs.index(
            [
                {
                    "chunk_id": "c1",
                    "text": "Test.",
                    "embedding": np.random.randn(8).astype(np.float32),
                }
            ]
        )
        assert tmp_vs.count() == 1

    def test_numpy_embedding(self, tmp_vs):
        emb = np.array([1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0], dtype=np.float32)
        tmp_vs.index(
            [{"chunk_id": "c1", "text": "Numpy test.", "embedding": emb}]
        )
        results = tmp_vs.search(emb, limit=1)
        assert len(results) == 1


# ---------------------------------------------------------------------------
# Phase context tests
# ---------------------------------------------------------------------------


class TestPhaseContext:
    def test_orient_config(self):
        config = get_phase_config("orient")
        assert "kg_relationships" in config.primary
        assert "prose_voice" in config.exclude

    def test_plan_config(self):
        config = get_phase_config("plan")
        assert "outline_position" in config.primary
        assert "raw_prose" in config.exclude

    def test_draft_config(self):
        config = get_phase_config("draft")
        assert "voice_examples" in config.primary
        assert "world_rules" in config.exclude

    def test_evaluate_config(self):
        config = get_phase_config("evaluate")
        assert "canon_facts" in config.primary
        assert "tone_examples" in config.exclude

    def test_unknown_phase_fallback(self):
        config = get_phase_config("unknown_phase")
        # Falls back to orient
        assert config.primary == get_phase_config("orient").primary

    def test_should_use_backend(self):
        assert should_use_backend("orient", "kg_relationships")
        assert not should_use_backend("orient", "prose_voice")
        assert should_use_backend("draft", "voice_examples")
        assert not should_use_backend("draft", "world_rules")

    def test_token_budget(self):
        budget = get_token_budget("orient", total_budget=4000)
        assert budget["primary"] == 2800  # 0.7 * 4000
        assert budget["secondary"] == 1200  # 0.3 * 4000


# ---------------------------------------------------------------------------
# Query decomposition tests
# ---------------------------------------------------------------------------


class TestQueryDecomposition:
    def test_parse_valid_json(self):
        raw = json.dumps(
            [
                {
                    "text": "Ryn's relationships",
                    "query_type": "entity_relationship",
                    "entity_hints": ["ryn"],
                }
            ]
        )
        sqs = _parse_decomposition(raw)
        assert len(sqs) == 1
        assert sqs[0].query_type == QueryType.ENTITY_RELATIONSHIP
        assert sqs[0].entity_hints == ["ryn"]

    def test_parse_markdown_fenced(self):
        raw = '```json\n[{"text": "test", "query_type": "tone_similarity"}]\n```'
        sqs = _parse_decomposition(raw)
        assert len(sqs) == 1
        assert sqs[0].query_type == QueryType.TONE_SIMILARITY

    def test_parse_invalid_json_fallback(self):
        sqs = _parse_decomposition("not json at all")
        assert len(sqs) == 1
        assert sqs[0].query_type == QueryType.ENTITY_RELATIONSHIP

    def test_simple_decompose_relationship(self):
        sqs = _simple_decompose("What relationships does Ryn have?")
        assert sqs[0].query_type == QueryType.ENTITY_RELATIONSHIP

    def test_simple_decompose_thematic(self):
        sqs = _simple_decompose("What is the overall theme of the story?")
        assert sqs[0].query_type == QueryType.THEMATIC_GLOBAL

    def test_simple_decompose_tone(self):
        sqs = _simple_decompose("Find prose with a dark mood")
        assert sqs[0].query_type == QueryType.TONE_SIMILARITY


# ---------------------------------------------------------------------------
# RetrievalRouter tests
# ---------------------------------------------------------------------------


class TestRetrievalRouter:
    @pytest.mark.asyncio
    async def test_orient_routes_to_hipporag(self, tmp_kg):
        router = RetrievalRouter(kg=tmp_kg)
        result = await router.query(
            "What are Ryn's relationships?",
            phase="orient",
            scope=_SCOPE,
            access_tier=0,
            chapter_number=1,
        )
        assert "hipporag" in result.sources

    @pytest.mark.asyncio
    async def test_draft_routes_to_vector(self, tmp_kg, tmp_vs):
        # Index a chunk
        tmp_vs.index(
            [
                {
                    "chunk_id": "c1",
                    "text": "Cold wind blew.",
                    "embedding": np.random.randn(8).astype(np.float32).tolist(),
                }
            ]
        )

        def embed_fn(text):
            return np.random.randn(8).astype(np.float32)

        router = RetrievalRouter(
            kg=tmp_kg, vector_store=tmp_vs, embed_fn=embed_fn
        )
        result = await router.query(
            "Find prose with cold atmosphere",
            phase="draft",
            scope=_SCOPE,
        )
        assert "vector" in result.sources
        assert len(result.prose_chunks) >= 1

    @pytest.mark.asyncio
    async def test_draft_excludes_kg(self, tmp_kg):
        router = RetrievalRouter(kg=tmp_kg)
        result = await router.query(
            "What does Ryn know?",
            phase="draft",
            scope=_SCOPE,
        )
        # Draft phase excludes KG relationships
        assert "hipporag" not in result.sources

    @pytest.mark.asyncio
    async def test_with_llm_decomposition(self, tmp_kg):
        async def mock_decomposer(prompt, system, role):
            return json.dumps(
                [
                    {
                        "text": "Ryn's alliances",
                        "query_type": "entity_relationship",
                        "entity_hints": ["ryn"],
                    }
                ]
            )

        router = RetrievalRouter(kg=tmp_kg, provider_call=mock_decomposer)
        result = await router.query(
            "Tell me about Ryn's alliances",
            phase="orient",
            scope=_SCOPE,
            chapter_number=1,
        )
        assert "hipporag" in result.sources
        assert len(result.facts) >= 1

    @pytest.mark.asyncio
    async def test_token_estimation(self, tmp_kg):
        router = RetrievalRouter(kg=tmp_kg)
        result = await router.query(
            "What relationships exist?",
            phase="orient",
            scope=_SCOPE,
            chapter_number=1,
        )
        assert result.token_count >= 0

    @pytest.mark.asyncio
    async def test_result_structure(self, tmp_kg):
        router = RetrievalRouter(kg=tmp_kg)
        result = await router.query("test", phase="orient", scope=_SCOPE)
        assert isinstance(result, RetrievalResult)
        assert isinstance(result.facts, list)
        assert isinstance(result.relationships, list)
        assert isinstance(result.prose_chunks, list)
        assert isinstance(result.community_summaries, list)
        assert isinstance(result.sources, list)


class TestScopeAssertion:
    """Stage 1 defense-in-depth: post-query universe_id check."""

    @pytest.mark.asyncio
    async def test_rows_without_universe_id_pass_through(self, tmp_kg):
        """KG rows don't carry universe_id today — they should all pass."""
        router = RetrievalRouter(kg=tmp_kg)
        result = await router.query(
            "What relationships exist?",
            phase="orient",
            scope=MemoryScope(universe_id="universe-A"),
            chapter_number=1,
        )
        # tmp_kg seeded one fact; it has no universe_id column, so the
        # assertion must NOT drop it.
        assert len(result.facts) >= 1

    @pytest.mark.asyncio
    async def test_cross_universe_dict_rows_dropped(self, tmp_kg, caplog):
        """Rows that declare a mismatched universe_id are dropped loudly."""
        import logging

        scope = MemoryScope(universe_id="universe-A")

        good_fact = FactWithContext(
            fact_id="ok",
            text="From universe A.",
            source_type=SourceType.WORLD_TRUTH,
        )
        # Simulate a singleton-bleed: a row tagged with the wrong
        # universe_id sneaks in. The router must drop it and warn.
        bad_fact = FactWithContext(
            fact_id="leak",
            text="From universe B.",
            source_type=SourceType.WORLD_TRUTH,
        )
        bad_fact.universe_id = "universe-B"  # row-level tag (future Stage 2)

        from tinyassets.retrieval.router import _drop_cross_universe_rows

        result = RetrievalResult(facts=[good_fact, bad_fact])
        with caplog.at_level(logging.WARNING, logger="tinyassets.retrieval.router"):
            filtered = _drop_cross_universe_rows(result, scope)

        kept_ids = [f.fact_id for f in filtered.facts]
        assert "ok" in kept_ids
        assert "leak" not in kept_ids
        assert any(
            "scope_mismatch" in rec.message for rec in caplog.records
        ), "expected scope_mismatch warning"

    @pytest.mark.asyncio
    async def test_cross_universe_relationships_dropped(self, tmp_kg):
        """Dict-shaped relationship rows are also filtered by universe_id."""
        from tinyassets.retrieval.router import _drop_cross_universe_rows

        scope = MemoryScope(universe_id="universe-A")
        result = RetrievalResult(
            relationships=[
                {"source": "ryn", "target": "kael", "universe_id": "universe-A"},
                {"source": "mira", "target": "jorn", "universe_id": "universe-B"},
                {"source": "loose", "target": "unknown"},  # no tag: passes
            ],
        )
        filtered = _drop_cross_universe_rows(result, scope)
        keys = [(r["source"], r["target"]) for r in filtered.relationships]
        assert ("ryn", "kael") in keys
        assert ("loose", "unknown") in keys
        assert ("mira", "jorn") not in keys


class TestAgenticSearchPolicy:
    def test_build_phase_query_varies_by_phase(self):
        state = {
            "orient_result": {
                "scene_id": "s1",
                "characters": [{"name": "Ryn"}],
                "warnings": [{"text": "A gate promise is overdue."}],
            },
            "plan_output": {
                "scene_id": "s1",
                "beats": [{"description": "Ryn reaches the gate."}],
                "done_when": "Ryn opens the gate",
            },
        }

        plan_query = build_phase_query(state, "plan")
        draft_query = build_phase_query(state, "draft")

        assert "Overall theme" in plan_query
        assert "Voice, atmosphere" in draft_query

    def test_run_phase_retrieval_skips_when_no_scope(self, monkeypatch, caplog):
        """Missing universe_id + missing scope → skip retrieval, don't crash."""
        import logging

        from tinyassets.retrieval.agentic_search import run_phase_retrieval

        state = {
            "orient_result": {
                "scene_id": "s1",
                "characters": [{"name": "Ryn"}],
            },
        }
        with caplog.at_level(
            logging.WARNING, logger="tinyassets.retrieval.agentic_search",
        ):
            result = run_phase_retrieval(state, "orient")
        assert result == {}
        assert any(
            "no scope in state" in rec.message for rec in caplog.records
        )

    def test_run_phase_retrieval_threads_scope_to_router(self, monkeypatch):
        """State with universe_id → router.query receives a MemoryScope."""
        from tinyassets.retrieval import agentic_search

        captured: dict[str, object] = {}

        class _FakeRouter:
            def __init__(self, **_):
                pass

            def query(self, *, query, phase, scope, **_):
                captured["scope"] = scope

                async def _empty():
                    return RetrievalResult()

                return _empty()

        monkeypatch.setattr(
            "tinyassets.retrieval.router.RetrievalRouter", _FakeRouter,
        )

        class _FakeKG:
            def close(self):
                pass

        monkeypatch.setattr(
            "tinyassets.knowledge.knowledge_graph.KnowledgeGraph",
            lambda *a, **kw: _FakeKG(),
        )
        monkeypatch.setattr(
            "domains.fantasy_daemon.phases._paths.resolve_kg_path",
            lambda state: "/tmp/fake.db",
        )
        # Clear the runtime singletons so run_phase_retrieval falls
        # through to construct a fresh KG/router.
        from tinyassets import runtime_singletons as runtime
        monkeypatch.setattr(runtime, "knowledge_graph", None)

        state = {
            "universe_id": "universe-A",
            "orient_result": {"scene_id": "s1"},
        }
        agentic_search.run_phase_retrieval(state, "orient")
        scope = captured.get("scope")
        assert isinstance(scope, MemoryScope)
        assert scope.universe_id == "universe-A"

    def test_assemble_phase_search_context_merges_prior_retrieval(self, monkeypatch):
        state = {
            "chapter_number": 1,
            "scene_number": 1,
            "orient_result": {
                "scene_id": "s1",
                "world_state": {"chapter_number": 1, "scene_number": 1},
                "warnings": [],
                "characters": [{"name": "Ryn"}],
            },
            "retrieved_context": {
                "facts": [{"fact_id": "f1", "text": "Ryn is a scout."}],
                "canon_facts": [{"fact_id": "f1", "text": "Ryn is a scout."}],
                "relationships": [],
                "prose_chunks": [],
                "community_summaries": [],
                "warnings": [],
                "sources": ["hipporag"],
                "token_count": 12,
            },
            "memory_context": {},
            "recent_prose": "",
        }

        monkeypatch.setattr(
            "tinyassets.retrieval.agentic_search.assemble_memory_context",
            lambda state, phase: {"style_rules": [{"rule": "Stay intimate."}]},
        )
        monkeypatch.setattr(
            "tinyassets.retrieval.agentic_search.run_phase_retrieval",
            lambda state, phase, memory_context=None: {
                "facts": [],
                "canon_facts": [],
                "relationships": [],
                "prose_chunks": ["Cold rain needled the harbor."],
                "community_summaries": ["Ashwater's towers rule the bay."],
                "warnings": [],
                "sources": ["vector", "raptor"],
                "token_count": 20,
            },
        )

        search = assemble_phase_search_context(state, "draft")

        assert search["phase"] == "draft"
        assert search["facts"][0]["text"] == "Ryn is a scout."
        assert "hipporag" in search["sources"]
        assert "vector" in search["sources"]
        assert search["retrieved_context"]["prose_chunks"] == [
            "Cold rain needled the harbor."
        ]

    def test_assemble_phase_search_context_adds_pinned_soul_lens(
        self, tmp_path, monkeypatch,
    ):
        from tinyassets.universe_soul import write_universe_soul

        write_universe_soul(
            tmp_path,
            purpose="A lab studies civic memory with auditable sources.",
            why="Keep research claims reusable.",
            hard_lines=("Cite uncertainty.",),
            soft_preferences=("Prefer compact steps.",),
        )
        monkeypatch.setattr(
            "tinyassets.retrieval.agentic_search.assemble_memory_context",
            lambda state, phase: {},
        )
        monkeypatch.setattr(
            "tinyassets.retrieval.agentic_search.run_phase_retrieval",
            lambda state, phase, memory_context=None: {},
        )

        search = assemble_phase_search_context({
            "universe_id": "u1",
            "_universe_path": str(tmp_path),
        }, "plan")

        soul = search["universe_soul"]
        assert soul["version_id"] == "soul_versions/0001.md"
        assert soul["purpose"] == "A lab studies civic memory with auditable sources."
        assert soul["hard_lines"] == ["Cite uncertainty."]
        assert soul["soft_preferences"] == ["Prefer compact steps."]
        assert soul["identity_boundary"].startswith("Command center soul guides")
        assert "soul:soul_versions/0001.md" in search["sources"]
