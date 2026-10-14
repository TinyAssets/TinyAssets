"""Tests for semantic result clustering in the retrieval router."""

from __future__ import annotations

import numpy as np

from tinyassets.knowledge.models import (
    FactWithContext,
    SourceType,
)
from tinyassets.retrieval.router import (
    _cluster_facts,
    _cluster_texts,
    _cosine_similarity,
    _greedy_cluster,
    _text_overlap,
)


class TestCosineSimility:
    def test_identical_vectors(self):
        a = np.array([1.0, 0.0, 0.0])
        assert _cosine_similarity(a, a) > 0.99

    def test_orthogonal_vectors(self):
        a = np.array([1.0, 0.0])
        b = np.array([0.0, 1.0])
        assert abs(_cosine_similarity(a, b)) < 0.01

    def test_zero_vector(self):
        a = np.array([0.0, 0.0])
        b = np.array([1.0, 1.0])
        assert _cosine_similarity(a, b) == 0.0


class TestTextOverlap:
    def test_identical_text(self):
        assert _text_overlap("hello world", "hello world") == 1.0

    def test_no_overlap(self):
        assert _text_overlap("hello world", "foo bar") == 0.0

    def test_partial_overlap(self):
        score = _text_overlap("the cat sat", "the dog sat")
        assert 0.3 < score < 0.8

    def test_empty_text(self):
        assert _text_overlap("", "hello") == 0.0
        assert _text_overlap("", "") == 0.0


class TestGreedyCluster:
    def test_single_item(self):
        clusters = _greedy_cluster(["hello"], None)
        assert clusters == [[0]]

    def test_identical_items_cluster_together(self):
        texts = ["Ryn is a scout", "Ryn is a scout", "Kael rules Ashwater"]
        clusters = _greedy_cluster(texts, None)
        # First two should cluster, third separate
        assert len(clusters) == 2
        assert len(clusters[0]) == 2
        assert len(clusters[1]) == 1

    def test_distinct_items_stay_separate(self):
        texts = [
            "The mountain was covered in snow",
            "Ryn swung her sword at the beast",
            "The marketplace was loud and colorful",
        ]
        clusters = _greedy_cluster(texts, None)
        # All distinct enough to be separate
        assert len(clusters) == 3

    def test_with_embed_fn(self):
        # Use similar embeddings to force clustering
        emb_map = {
            "A": np.array([1.0, 0.0, 0.0]),
            "B": np.array([0.99, 0.1, 0.0]),  # Very similar to A
            "C": np.array([0.0, 0.0, 1.0]),   # Orthogonal
        }

        def embed_fn(text):
            return emb_map.get(text, np.zeros(3))

        clusters = _greedy_cluster(["A", "B", "C"], embed_fn)
        assert len(clusters) == 2
        # A and B should cluster
        assert 0 in clusters[0] and 1 in clusters[0]


class TestClusterFacts:
    def _make_fact(self, text: str, importance: float = 0.5) -> FactWithContext:
        return FactWithContext(
            fact_id=f"f-{text[:10]}",
            text=text,
            source_type=SourceType.AUTHOR_FACT,
            importance=importance,
        )

    def test_keeps_highest_importance(self):
        facts = [
            self._make_fact("Ryn is a scout in the northern watch", importance=0.3),
            self._make_fact("Ryn is a scout in the northern watch", importance=0.9),
            self._make_fact("Kael rules from the tower", importance=0.5),
        ]
        result = _cluster_facts(facts, None)
        assert len(result) == 2
        # The high-importance duplicate should be the representative
        ryn_fact = [f for f in result if "Ryn" in f.text][0]
        assert ryn_fact.importance == 0.9

    def test_single_fact_unchanged(self):
        facts = [self._make_fact("Only one fact")]
        result = _cluster_facts(facts, None)
        assert len(result) == 1
        assert result[0].text == "Only one fact"

    def test_empty_list(self):
        assert _cluster_facts([], None) == []


class TestClusterTexts:
    def test_deduplicates_identical(self):
        texts = ["The wind howled", "The wind howled", "Stars shone above"]
        result = _cluster_texts(texts, None)
        assert len(result) == 2

    def test_keeps_longest_representative(self):
        # These share high word overlap (Jaccard > 0.85) to trigger clustering
        texts = [
            "Ryn walked through the northern gate at dawn",
            "Ryn walked through the northern gate at dawn and paused",
            "completely different topic about Ashwater markets",
        ]
        result = _cluster_texts(texts, None)
        assert len(result) == 2
        # The longer near-duplicate should be kept
        assert any("paused" in t for t in result)

    def test_single_text(self):
        result = _cluster_texts(["only one"], None)
        assert result == ["only one"]

    def test_empty_list(self):
        assert _cluster_texts([], None) == []


class TestRetrievalResultClustering:
    """Integration test: verify clustering works through the RetrievalRouter."""

    def test_router_clusters_during_query(self, canonical_source, tmp_path):
        """Verify the router applies clustering to its results."""
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
        from tinyassets.knowledge.models import GraphEntity
        from tinyassets.retrieval.router import RetrievalRouter

        kg = KnowledgeGraph(
            canonical_source(
                "knowledge",
                key=str(tmp_path / "test.db"),
                namespaces=[None, "world", "u1", "test", "test-universe", "universe-A"],
            )
        )
        kg.add_entity(
            GraphEntity(
                entity_id="ryn",
                entity_type="character",
                access_tier=0,
                public_description="a scout",
                hidden_description="",
                secret_description="",
                aliases=[],
            )
        )
        # Add duplicate facts
        for i in range(3):
            kg.add_facts([
                FactWithContext(
                    fact_id=f"dup-{i}",
                    text="Ryn is a scout in the northern watch",
                    source_type=SourceType.AUTHOR_FACT,
                    importance=0.5 + i * 0.1,
                )
            ])
        kg.add_facts([
            FactWithContext(
                fact_id="unique",
                text="Ashwater is a coastal city",
                source_type=SourceType.WORLD_TRUTH,
                importance=0.6,
            )
        ])

        router = RetrievalRouter(kg=kg)

        import asyncio

        from tinyassets.memory.scoping import MemoryScope
        result = asyncio.run(router.query(
            "What are Ryn's relationships?",
            phase="orient",
            scope=MemoryScope(universe_id="test-universe"),
            chapter_number=1,
        ))

        # After clustering, duplicates should be reduced
        ryn_facts = [f for f in result.facts if "Ryn" in f.text]
        assert len(ryn_facts) <= 1  # Duplicates clustered into one

        kg.close()
