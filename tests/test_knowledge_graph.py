"""Tests for knowledge graph: models, entity extraction, KG, Leiden, HippoRAG."""

from __future__ import annotations

import json

import igraph as ig
import numpy as np
import pytest

from tinyassets.knowledge.entity_extraction import (
    AliasRegistry,
    extract_from_prose,
)
from tinyassets.knowledge.hipporag import (
    HippoRAG,
    personalized_pagerank_query,
)
from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
from tinyassets.knowledge.leiden import (
    detect_communities,
    detect_communities_from_kg,
)
from tinyassets.knowledge.models import (
    FactWithContext,
    GraphEdge,
    GraphEntity,
    LanguageType,
    SourceType,
)
from tinyassets.knowledge.raptor import (
    RaptorTree,
    build_raptor_tree,
    query_raptor_tree,
)

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def tmp_db(canonical_source, tmp_path):
    """Create a KnowledgeGraph backed by a temp DB."""
    kg = KnowledgeGraph(
        canonical_source(
            "knowledge",
            key=str(tmp_path / "test.db"),
            namespaces=[None, "world", "u1", "test", "test-universe", "universe-A"],
        )
    )
    yield kg
    kg.close()


@pytest.fixture
def populated_kg(tmp_db):
    """KG with a small character graph and facts."""
    kg = tmp_db

    # Characters
    for name, etype in [
        ("ryn", "character"),
        ("ashwater_council", "faction"),
        ("northern_pass", "location"),
        ("glass_treaty", "artifact"),
        ("kael", "character"),
    ]:
        kg.add_entity(
            GraphEntity(
                entity_id=name,
                entity_type=etype,
                access_tier=0,
                public_description=f"Description of {name}",
                hidden_description="",
                secret_description="",
                aliases=[],
            )
        )

    # Edges
    edges = [
        ("ryn", "ashwater_council", "membership", 0, 1.0),
        ("ryn", "kael", "alliance", 0, 0.8),
        ("kael", "ashwater_council", "membership", 0, 0.9),
        ("ryn", "northern_pass", "knowledge", 0, 0.7),
        ("ashwater_council", "glass_treaty", "possession", 1, 0.6),
    ]
    for src, tgt, rel, tier, w in edges:
        kg.add_edge(
            GraphEdge(
                source=src,
                target=tgt,
                relation_type=rel,
                access_tier=tier,
                temporal_scope="always",
                pov_characters=[],
                weight=w,
                valid_from_chapter=1,
                valid_to_chapter=None,
            )
        )

    # Facts
    kg.add_facts(
        [
            FactWithContext(
                fact_id="f1",
                text="Ryn is a scout of the Ashwater Council",
                source_type=SourceType.AUTHOR_FACT,
                valid_from_chapter=1,
                access_tier=0,
                importance=0.8,
            ),
            FactWithContext(
                fact_id="f2",
                text="The Glass Treaty was forged in secret",
                source_type=SourceType.WORLD_TRUTH,
                valid_from_chapter=1,
                access_tier=1,
                importance=0.9,
                pov_characters=["ashwater_council"],
            ),
            FactWithContext(
                fact_id="f3",
                text="Kael suspects the treaty is a lie",
                source_type=SourceType.CHARACTER_BELIEF,
                narrator="kael",
                narrator_reliability=0.6,
                valid_from_chapter=3,
                access_tier=0,
            ),
        ]
    )

    return kg


# ---------------------------------------------------------------------------
# FactWithContext tests
# ---------------------------------------------------------------------------


class TestFactWithContext:
    def test_access_tier_public(self):
        f = FactWithContext(
            fact_id="t1", text="Public fact", source_type=SourceType.AUTHOR_FACT
        )
        assert f.is_accessible_to("anyone", 0)

    def test_access_tier_restricted(self):
        f = FactWithContext(
            fact_id="t2",
            text="Secret fact",
            source_type=SourceType.WORLD_TRUTH,
            access_tier=2,
        )
        assert not f.is_accessible_to("commoner", 1)
        assert f.is_accessible_to("insider", 2)

    def test_pov_restriction(self):
        f = FactWithContext(
            fact_id="t3",
            text="Only Ryn knows",
            source_type=SourceType.NARRATOR_CLAIM,
            pov_characters=["ryn"],
        )
        assert f.is_accessible_to("ryn", 0)
        assert not f.is_accessible_to("kael", 0)

    def test_temporal_validity(self):
        f = FactWithContext(
            fact_id="t4",
            text="Treaty active",
            source_type=SourceType.WORLD_TRUTH,
            valid_from_chapter=3,
            valid_to_chapter=7,
        )
        assert not f.is_valid_at_chapter(2)
        assert f.is_valid_at_chapter(3)
        assert f.is_valid_at_chapter(5)
        assert f.is_valid_at_chapter(7)
        assert not f.is_valid_at_chapter(8)

    def test_temporal_open_ended(self):
        f = FactWithContext(
            fact_id="t5",
            text="Always true",
            source_type=SourceType.AUTHOR_FACT,
            valid_from_chapter=1,
        )
        assert f.is_valid_at_chapter(100)

    def test_metaphorical_language_type(self):
        f = FactWithContext(
            fact_id="t6",
            text="Her heart was stone",
            source_type=SourceType.NARRATOR_CLAIM,
            language_type=LanguageType.METAPHORICAL,
        )
        assert f.language_type == LanguageType.METAPHORICAL


# ---------------------------------------------------------------------------
# KnowledgeGraph tests
# ---------------------------------------------------------------------------


class TestKnowledgeGraph:
    def test_add_and_get_entity(self, tmp_db):
        tmp_db.add_entity(
            GraphEntity(
                entity_id="ryn",
                entity_type="character",
                access_tier=0,
                public_description="A scout",
                hidden_description="A spy",
                secret_description="The heir",
                aliases=["the scout", "she"],
            )
        )
        entity = tmp_db.get_entity("ryn")
        assert entity is not None
        assert entity["entity_type"] == "character"
        assert "the scout" in entity["aliases"]

    def test_get_nonexistent_entity(self, tmp_db):
        assert tmp_db.get_entity("nobody") is None

    def test_query_entities_by_type(self, populated_kg):
        chars = populated_kg.query_entities(entity_type="character")
        assert len(chars) == 2
        names = {c["entity_id"] for c in chars}
        assert names == {"ryn", "kael"}

    def test_query_entities_by_access_tier(self, populated_kg):
        all_entities = populated_kg.query_entities(access_tier=0)
        assert len(all_entities) == 5

    def test_add_and_query_edges(self, populated_kg):
        edges = populated_kg.get_edges(entity_id="ryn")
        assert len(edges) >= 3  # membership, alliance, knowledge

    def test_edge_temporal_filter(self, tmp_db):
        for eid in ["a", "b"]:
            tmp_db.add_entity(
                GraphEntity(
                    entity_id=eid,
                    entity_type="character",
                    access_tier=0,
                    public_description="",
                    hidden_description="",
                    secret_description="",
                    aliases=[],
                )
            )
        tmp_db.add_edge(
            GraphEdge(
                source="a",
                target="b",
                relation_type="alliance",
                access_tier=0,
                temporal_scope="limited",
                pov_characters=[],
                weight=1.0,
                valid_from_chapter=3,
                valid_to_chapter=5,
            )
        )
        assert len(tmp_db.get_edges(entity_id="a", chapter_number=4)) == 1
        assert len(tmp_db.get_edges(entity_id="a", chapter_number=1)) == 0
        assert len(tmp_db.get_edges(entity_id="a", chapter_number=6)) == 0

    def test_edge_access_tier_filter(self, populated_kg):
        # Glass treaty edge has access_tier=1
        public_edges = populated_kg.get_edges(
            entity_id="glass_treaty", access_tier=0
        )
        assert len(public_edges) == 0
        insider_edges = populated_kg.get_edges(
            entity_id="glass_treaty", access_tier=1
        )
        assert len(insider_edges) == 1

    def test_add_and_query_facts(self, populated_kg):
        facts = populated_kg.query_facts(chapter_number=1)
        # f1 and f2 are valid from ch1, f3 from ch3
        assert len(facts) == 2

    def test_fact_access_tier_filter(self, populated_kg):
        public_facts = populated_kg.query_facts(
            chapter_number=1, access_tier=0
        )
        assert len(public_facts) == 1  # Only f1

    def test_fact_character_filter(self, populated_kg):
        # f2 is restricted to ashwater_council
        facts = populated_kg.query_facts(
            chapter_number=1, access_tier=1, character_id="ryn"
        )
        # f1 is public (no pov restriction), f2 is restricted to ashwater_council
        assert len(facts) == 1
        assert facts[0].fact_id == "f1"

    def test_build_igraph(self, populated_kg):
        g = populated_kg.build_igraph()
        assert g.vcount() >= 4  # At least 4 entities with edges
        assert g.ecount() >= 4

    def test_build_igraph_access_filter(self, populated_kg):
        g_public = populated_kg.build_igraph(access_tier=0)
        g_insider = populated_kg.build_igraph(access_tier=1)
        # Insider graph should have the glass_treaty edge too
        assert g_insider.ecount() >= g_public.ecount()

    def test_epistemic_access(self, populated_kg):
        access = populated_kg.get_epistemic_access("ryn", chapter=1)
        assert access["character"] == "ryn"
        assert len(access["accessible_entities"]) == 5

    def test_hipporag_query(self, populated_kg):
        results = populated_kg.hipporag_query(entities=["ryn"], k=5)
        assert isinstance(results, list)
        assert len(results) >= 1
        # Each result should be a dict with expected keys
        for r in results:
            assert "fact_id" in r
            assert "text" in r
            assert "importance" in r

    def test_hipporag_query_empty_graph(self, tmp_db):
        results = tmp_db.hipporag_query(entities=["nobody"], k=5)
        assert results == []

    def test_raptor_query_returns_empty(self, populated_kg):
        # raptor_query is a stub until RAPTOR tree is integrated
        results = populated_kg.raptor_query(query="some query", k=5)
        assert results == []

    def test_get_open_promises_none(self, populated_kg):
        # Default populated_kg has no promise/foreshadowing facts
        results = populated_kg.get_open_promises()
        assert results == []

    def test_get_open_promises_with_foreshadowing(self, populated_kg):
        from tinyassets.knowledge.models import NarrativeFunction
        # Add a foreshadowing fact with no truth_value_final
        populated_kg.add_facts([
            FactWithContext(
                fact_id="promise1",
                text="A dark power stirs beneath the mountain",
                source_type=SourceType.NARRATOR_CLAIM,
                narrative_function=NarrativeFunction.FORESHADOWING,
                valid_from_chapter=1,
                importance=0.9,
            ),
        ])
        results = populated_kg.get_open_promises()
        assert len(results) == 1
        assert results[0]["fact_id"] == "promise1"

    def test_get_open_promises_resolved_excluded(self, populated_kg):
        from tinyassets.knowledge.models import NarrativeFunction
        # Add a resolved foreshadowing fact
        populated_kg.add_facts([
            FactWithContext(
                fact_id="resolved_promise",
                text="The heir will return",
                source_type=SourceType.NARRATOR_CLAIM,
                narrative_function=NarrativeFunction.FORESHADOWING,
                truth_value_final="true",
                valid_from_chapter=1,
                importance=0.8,
            ),
        ])
        results = populated_kg.get_open_promises()
        assert all(r["fact_id"] != "resolved_promise" for r in results)

    def test_get_open_promises_overdue(self, populated_kg):
        from tinyassets.knowledge.models import NarrativeFunction
        # Add an old promise (chapter 1) and a recent one (chapter 50)
        populated_kg.add_facts([
            FactWithContext(
                fact_id="old_promise",
                text="An ancient prophecy unfolds",
                source_type=SourceType.NARRATOR_CLAIM,
                narrative_function=NarrativeFunction.PROMISE,
                valid_from_chapter=1,
                importance=0.7,
            ),
            FactWithContext(
                fact_id="recent_promise",
                text="A new threat emerges",
                source_type=SourceType.NARRATOR_CLAIM,
                narrative_function=NarrativeFunction.PROMISE,
                valid_from_chapter=50,
                importance=0.6,
            ),
        ])
        overdue = populated_kg.get_open_promises(overdue=True)
        overdue_ids = {r["fact_id"] for r in overdue}
        assert "old_promise" in overdue_ids
        assert "recent_promise" not in overdue_ids


# ---------------------------------------------------------------------------
# Leiden community detection tests
# ---------------------------------------------------------------------------


class TestLeidenCommunities:
    def test_two_clear_clusters(self):
        edges = [
            ("A", "B", 1.0),
            ("B", "C", 1.0),
            ("C", "A", 1.0),
            ("D", "E", 1.0),
            ("E", "F", 1.0),
            ("F", "D", 1.0),
            ("C", "D", 0.1),
        ]
        g = ig.Graph.TupleList(
            [(s, t, w) for s, t, w in edges], directed=False, weights=True
        )
        communities = detect_communities(g)
        assert len(communities) >= 2
        all_entities = set()
        for c in communities:
            all_entities.update(c.entities)
        assert all_entities == {"A", "B", "C", "D", "E", "F"}

    def test_empty_graph(self):
        g = ig.Graph(directed=False)
        communities = detect_communities(g)
        assert communities == []

    def test_single_component(self):
        edges = [("A", "B", 1.0), ("B", "C", 1.0), ("C", "A", 1.0)]
        g = ig.Graph.TupleList(
            [(s, t, w) for s, t, w in edges], directed=False, weights=True
        )
        communities = detect_communities(g)
        assert len(communities) >= 1

    def test_detect_from_kg(self, populated_kg):
        communities = detect_communities_from_kg(populated_kg)
        assert len(communities) >= 1
        total_entities = sum(len(c.entities) for c in communities)
        assert total_entities >= 3


# ---------------------------------------------------------------------------
# HippoRAG tests
# ---------------------------------------------------------------------------


class TestHippoRAG:
    def test_ppr_basic(self):
        edges = [("A", "B", 1.0), ("B", "C", 1.0), ("C", "D", 1.0)]
        g = ig.Graph.TupleList(
            [(s, t, w) for s, t, w in edges], directed=False, weights=True
        )
        ranked = personalized_pagerank_query(g, ["A"], top_k=4)
        assert len(ranked) == 4
        # A should be in the top results (seed gets high score,
        # but B may rank higher in an undirected path due to centrality)
        top_ids = {r["entity_id"] for r in ranked[:2]}
        assert "A" in top_ids
        # Scores should be monotonically non-increasing
        assert ranked[0]["ppr_score"] >= ranked[1]["ppr_score"]

    def test_ppr_no_seeds(self):
        edges = [("A", "B", 1.0)]
        g = ig.Graph.TupleList(
            [(s, t, w) for s, t, w in edges], directed=False, weights=True
        )
        # No matching seeds -- should fall back to uniform
        ranked = personalized_pagerank_query(g, ["Z"], top_k=2)
        assert len(ranked) == 2

    def test_ppr_empty_graph(self):
        g = ig.Graph(directed=False)
        ranked = personalized_pagerank_query(g, ["A"])
        assert ranked == []

    def test_hipporag_query(self, populated_kg):
        hippo = HippoRAG(populated_kg)
        result = hippo.query(
            entity_mentions=["ryn"],
            chapter_number=1,
            access_tier=0,
        )
        assert len(result["ranked_entities"]) > 0
        top_entity = result["ranked_entities"][0]["entity_id"]
        assert top_entity == "ryn"
        assert len(result["related_facts"]) >= 1
        assert len(result["related_edges"]) >= 1

    def test_hipporag_access_filter(self, populated_kg):
        hippo = HippoRAG(populated_kg)
        # With access_tier=0, shouldn't see glass_treaty edge
        result = hippo.query(
            entity_mentions=["ashwater_council"],
            chapter_number=1,
            access_tier=0,
        )
        edge_targets = {e["target"] for e in result["related_edges"]}
        edge_sources = {e["source"] for e in result["related_edges"]}
        all_endpoints = edge_targets | edge_sources
        assert "glass_treaty" not in all_endpoints


# ---------------------------------------------------------------------------
# Entity extraction tests
# ---------------------------------------------------------------------------


class TestEntityExtraction:
    @pytest.mark.asyncio
    async def test_extract_with_mock(self):
        async def mock_provider(prompt, system, role):
            return json.dumps(
                {
                    "entities": [
                        {
                            "entity_id": "ryn",
                            "entity_type": "character",
                            "aliases": ["the scout"],
                            "description": "A young scout",
                        }
                    ],
                    "relationships": [
                        {
                            "source": "ryn",
                            "target": "ashwater",
                            "relation_type": "membership",
                            "weight": 0.8,
                        }
                    ],
                    "facts": [
                        {
                            "text": "Ryn serves Ashwater",
                            "source_type": "author_fact",
                            "importance": 0.7,
                            "confidence": 0.9,
                        }
                    ],
                }
            )

        result = await extract_from_prose(
            "Ryn the scout crossed into Ashwater territory.",
            "1_1_1",
            "ryn",
            mock_provider,
        )
        assert len(result["entities"]) == 1
        assert len(result["edges"]) == 1
        assert len(result["facts"]) == 1
        assert result["facts"][0].source_type == SourceType.AUTHOR_FACT

    def test_alias_registry(self):
        reg = AliasRegistry()
        reg.register("ryn", ["the scout", "she", "Ryn"])
        assert reg.resolve("the scout") == "ryn"
        assert reg.resolve("RYN") == "ryn"
        assert reg.resolve("she") == "ryn"
        assert reg.resolve("unknown") is None

    @pytest.mark.asyncio
    async def test_alias_deduplication(self):
        async def mock_provider(prompt, system, role):
            return json.dumps(
                {
                    "entities": [
                        {
                            "entity_id": "ryn",
                            "entity_type": "character",
                            "aliases": ["the scout"],
                        }
                    ],
                    "relationships": [
                        {
                            "source": "the scout",
                            "target": "ashwater",
                            "relation_type": "membership",
                            "weight": 0.5,
                        }
                    ],
                    "facts": [],
                }
            )

        registry = AliasRegistry()
        result = await extract_from_prose(
            "The scout joined Ashwater.",
            "1_1_1",
            "ryn",
            mock_provider,
            alias_registry=registry,
        )
        # Edge source should be resolved from "the scout" to "ryn"
        assert result["edges"][0]["source"] == "ryn"

    @pytest.mark.asyncio
    async def test_access_tier_parsed_from_response(self):
        """access_tier values from the LLM response flow into typed objects."""
        async def mock_provider(prompt, system, role):
            return json.dumps({
                "entities": [{
                    "entity_id": "glass_lattice",
                    "entity_type": "magic_system",
                    "aliases": [],
                    "description": "A hidden magical network",
                    "access_tier": 2,
                }],
                "relationships": [{
                    "source": "glass_lattice",
                    "target": "ashwater",
                    "relation_type": "knowledge",
                    "weight": 0.9,
                    "access_tier": 1,
                }],
                "facts": [{
                    "text": "The Glass Lattice connects all ley lines",
                    "source_type": "world_truth",
                    "importance": 0.9,
                    "confidence": 0.95,
                    "access_tier": 3,
                }],
            })

        result = await extract_from_prose(
            "The Glass Lattice hummed beneath the earth.",
            "1_1_1", "narrator", mock_provider,
        )
        assert result["entities"][0]["access_tier"] == 2
        assert result["edges"][0]["access_tier"] == 1
        assert result["facts"][0].access_tier == 3

    def test_prompt_includes_access_tier_instructions(self):
        """FICTION_EXTRACTION_PROMPT should describe access_tier classification."""
        from tinyassets.knowledge.entity_extraction import FICTION_EXTRACTION_PROMPT
        assert "access_tier" in FICTION_EXTRACTION_PROMPT
        assert "common knowledge" in FICTION_EXTRACTION_PROMPT
        assert "cosmic" in FICTION_EXTRACTION_PROMPT

    def test_fact_extraction_prompt_includes_access_tier(self):
        """FACT_EXTRACTION_SYSTEM should describe access_tier classification."""
        from domains.fantasy_daemon.phases.fact_extraction import FACT_EXTRACTION_SYSTEM
        assert "access_tier" in FACT_EXTRACTION_SYSTEM
        assert "common knowledge" in FACT_EXTRACTION_SYSTEM


# ---------------------------------------------------------------------------
# RAPTOR tests
# ---------------------------------------------------------------------------


class TestRAPTOR:
    @pytest.mark.asyncio
    async def test_build_tree(self):
        async def mock_provider(prompt, system, role):
            return "Summary of the cluster."

        chunks = ["Chunk A.", "Chunk B.", "Chunk C.", "Chunk D.", "Chunk E.", "Chunk F."]
        embeddings = np.random.randn(6, 8).astype(np.float32)

        tree = await build_raptor_tree(chunks, embeddings, mock_provider, max_depth=3)
        assert tree.depth >= 2
        assert len(tree.get_level(0)) == 6

    def test_query_tree(self):
        tree = RaptorTree()
        for i in range(3):
            tree.add_node(
                __import__(
                    "tinyassets.knowledge.raptor", fromlist=["RaptorNode"]
                ).RaptorNode(
                    node_id=f"n{i}",
                    text=f"Summary {i}",
                    level=1,
                    embedding=np.random.randn(8).tolist(),
                )
            )
        query_emb = np.random.randn(8).astype(np.float32)
        results = query_raptor_tree(tree, query_emb, level=1, top_k=2)
        assert len(results) == 2

    def test_empty_tree_query(self):
        tree = RaptorTree()
        results = query_raptor_tree(tree, np.zeros(8), top_k=3)
        assert results == []

    def test_read_canon_paragraphs(self, tmp_path):
        """_read_canon_paragraphs splits canon/*.md into >50 char paragraphs."""
        from tinyassets.knowledge.raptor import _read_canon_paragraphs

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "test.md").write_text(
            "Short.\n\n"  # <50 chars, should be filtered
            "This is a long paragraph about the fantasy world that exceeds fifty characters.\n\n"
            "Another substantial paragraph with enough content for the test.",
            encoding="utf-8",
        )
        paras = _read_canon_paragraphs(str(canon_dir))
        assert len(paras) == 2
        assert all(len(p) >= 50 for p in paras)

    def test_read_canon_paragraphs_no_dir(self, tmp_path):
        """_read_canon_paragraphs returns empty list when dir doesn't exist."""
        from tinyassets.knowledge.raptor import _read_canon_paragraphs

        paras = _read_canon_paragraphs(str(tmp_path / "nonexistent"))
        assert paras == []

    def test_rebuild_raptor_from_canon(self, tmp_path):
        """rebuild_raptor_from_canon builds tree from canon/*.md paragraphs."""
        from unittest.mock import patch

        import tinyassets.runtime_singletons as rt
        from tinyassets.knowledge.raptor import rebuild_raptor_from_canon

        # Create canon files with >50 char paragraphs
        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "characters.md").write_text(
            "Ryn is a river-watcher who patrols the banks of the Ashwater.\n\n"
            "She carries a silver compass that was her grandmother's heirloom.\n\n"
            "The Ashwater River flows through the heart of the Silent Valley.\n\n"
            "The Glass Treaty bound the river clans to never cross at night.",
            encoding="utf-8",
        )

        # Mock embed_fn to return deterministic embeddings
        dim = 8

        def mock_embed(text: str) -> list[float]:
            rng = np.random.default_rng(hash(text) % (2**31))
            return rng.standard_normal(dim).tolist()

        old_rt = rt.raptor_tree
        rt.raptor_tree = None

        try:
            with patch(
                "tinyassets.providers.model_call.text_sync",
                return_value="Summary of the cluster.",
            ):
                tree = rebuild_raptor_from_canon(
                    canon_dir=str(canon_dir),
                    embed_fn=mock_embed,
                    universe_id="test",
                )

            assert tree is not None
            assert rt.raptor_tree is tree
            assert tree.depth >= 1
            assert len(tree.get_level(0)) == 4
        finally:
            rt.raptor_tree = old_rt

    def test_daemon_build_raptor_from_canon(self, tmp_path):
        """DaemonController._build_raptor_tree reads canon files."""
        from unittest.mock import patch

        import tinyassets.runtime_singletons as rt
        from tinyassets.__main__ import DaemonController

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "world.md").write_text(
            "The Silent Valley stretches between two mountain ranges.\n\n"
            "Ancient ruins dot the landscape, remnants of the First Age.\n\n"
            "The river clans have traded along these routes for centuries.",
            encoding="utf-8",
        )

        dim = 8

        def mock_embed(text: str) -> list[float]:
            rng = np.random.default_rng(hash(text) % (2**31))
            return rng.standard_normal(dim).tolist()

        old_ef = rt.embed_fn
        old_rt = rt.raptor_tree
        rt.embed_fn = mock_embed
        rt.raptor_tree = None

        try:
            controller = DaemonController(
                universe_path=str(tmp_path),
                no_tray=True,
            )
            with patch(
                "tinyassets.providers.model_call.text_sync",
                return_value="Summary of cluster.",
            ):
                controller._build_raptor_tree()

            assert rt.raptor_tree is not None
            assert rt.raptor_tree.depth >= 1
            assert len(rt.raptor_tree.get_level(0)) == 3
        finally:
            rt.embed_fn = old_ef
            rt.raptor_tree = old_rt

    def test_daemon_build_raptor_skips_without_embed_fn(self, tmp_path):
        """_build_raptor_tree skips when no embed_fn is available."""
        import tinyassets.runtime_singletons as rt
        from tinyassets.__main__ import DaemonController

        canon_dir = tmp_path / "canon"
        canon_dir.mkdir()
        (canon_dir / "test.md").write_text(
            "Some paragraph that is long enough for RAPTOR processing.\n\n"
            "Another paragraph with enough content for the test to work.",
            encoding="utf-8",
        )

        old_ef = rt.embed_fn
        old_rt = rt.raptor_tree
        rt.embed_fn = None
        rt.raptor_tree = None

        try:
            controller = DaemonController(
                universe_path=str(tmp_path),
                no_tray=True,
            )
            controller._build_raptor_tree()
            assert rt.raptor_tree is None
        finally:
            rt.embed_fn = old_ef
            rt.raptor_tree = old_rt

    def test_daemon_build_raptor_skips_empty_canon(self, tmp_path):
        """_build_raptor_tree skips when canon dir has no content."""
        import tinyassets.runtime_singletons as rt
        from tinyassets.__main__ import DaemonController

        old_rt = rt.raptor_tree
        rt.raptor_tree = None

        try:
            controller = DaemonController(
                universe_path=str(tmp_path),
                no_tray=True,
            )
            controller._build_raptor_tree()
            assert rt.raptor_tree is None
        finally:
            rt.raptor_tree = old_rt
