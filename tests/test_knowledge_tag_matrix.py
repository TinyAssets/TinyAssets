from __future__ import annotations

import json
import logging

from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
from tinyassets.knowledge.models import FactWithContext, RetrievalResult, SourceType
from tinyassets.knowledge.tag_matrix import (
    CommonsPromotionRecord,
    KnowledgeTags,
    TagMatrixQuery,
    filter_rows_by_tag_matrix,
    row_visible_for_tag_matrix,
)
from tinyassets.memory.scoping import MemoryScope
from tinyassets.retrieval.router import _drop_cross_universe_rows, _filter_by_tag_matrix


def _promotion_record() -> CommonsPromotionRecord:
    return CommonsPromotionRecord(
        source_universe="u-private",
        shape_tag="protocol",
        promoter_identity="user-123",
        declassification_reason="Reusable protocol shape, no private canon.",
        resolver_decision="resolved",
        timestamp="2026-05-27T22:16:00Z",
    )


def test_tag_matrix_includes_same_scope_and_promoted_commons(caplog):
    scope = MemoryScope(universe_id="u1")
    query = TagMatrixQuery(domain_tags=("research",), shape_tags=("protocol",))
    promoted = _promotion_record()

    rows = [
        {
            "id": "same-universe",
            "tag_universes": json.dumps(["u1"]),
            "tag_domains": json.dumps(["research"]),
            "tag_shapes": json.dumps(["protocol"]),
        },
        {
            "id": "other-universe",
            "tag_universes": json.dumps(["u2"]),
            "tag_domains": json.dumps(["research"]),
            "tag_shapes": json.dumps(["protocol"]),
        },
        {
            "id": "promoted-commons",
            "tag_commons": 1,
            "tag_private_canon": 1,
            "tag_domains": json.dumps(["research"]),
            "tag_shapes": json.dumps(["protocol"]),
            "promotion_record": json.dumps(promoted.__dict__),
        },
        {
            "id": "wrong-domain-commons",
            "tag_commons": 1,
            "tag_domains": json.dumps(["fiction"]),
            "tag_shapes": json.dumps(["protocol"]),
        },
        {
            "id": "unpromoted-private",
            "tag_commons": 1,
            "tag_private_canon": 1,
            "tag_domains": json.dumps(["research"]),
            "tag_shapes": json.dumps(["protocol"]),
        },
    ]

    with caplog.at_level(logging.WARNING, logger="tinyassets.knowledge.tag_matrix"):
        filtered = filter_rows_by_tag_matrix(rows, scope=scope, query=query)

    assert [row["id"] for row in filtered] == [
        "same-universe",
        "promoted-commons",
    ]
    assert any("tag_matrix.inv5_block" in rec.message for rec in caplog.records)


def test_tag_matrix_does_not_override_hard_memory_scope():
    scope = MemoryScope(universe_id="u1")
    tag_query = TagMatrixQuery(domain_tags=("research",))
    result = RetrievalResult(
        relationships=[
            {
                "source": "bad",
                "target": "leak",
                "universe_id": "u2",
                "tag_universes": json.dumps(["u1"]),
                "tag_domains": json.dumps(["research"]),
            },
            {
                "source": "ok",
                "target": "local",
                "universe_id": "u1",
                "tag_universes": json.dumps(["u1"]),
                "tag_domains": json.dumps(["research"]),
            },
        ],
    )

    scoped = _drop_cross_universe_rows(result, scope)
    filtered = _filter_by_tag_matrix(scoped, scope, tag_query)

    assert filtered.relationships == [
        {
            "source": "ok",
            "target": "local",
            "universe_id": "u1",
            "tag_universes": json.dumps(["u1"]),
            "tag_domains": json.dumps(["research"]),
        },
    ]


def test_unpromoted_private_commons_fails_closed():
    scope = MemoryScope(universe_id="u1")
    row = {
        "tag_commons": 1,
        "tag_private_canon": 1,
        "tag_shapes": json.dumps(["protocol"]),
    }

    assert not row_visible_for_tag_matrix(
        row,
        scope=scope,
        query=TagMatrixQuery(shape_tags=("protocol",)),
    )


def test_manual_tag_construction_accepts_single_string():
    tags = KnowledgeTags(universes="u1", domains="research", shapes="protocol")  # type: ignore[arg-type]
    query = TagMatrixQuery(domain_tags="research", shape_tags="protocol")  # type: ignore[arg-type]

    assert tags.universes == ("u1",)
    assert tags.domains == ("research",)
    assert tags.shapes == ("protocol",)
    assert query.domain_tags == ("research",)
    assert query.shape_tags == ("protocol",)


def test_knowledge_graph_persists_fact_tags(canonical_source, tmp_path):
    kg = KnowledgeGraph(
        canonical_source(
            "knowledge",
            key=tmp_path / "kg.db",
            namespaces=[None, "world", "u1", "test", "test-universe", "universe-A"],
        )
    )
    try:
        scope = MemoryScope(universe_id="u1")
        tags = KnowledgeTags(
            universes=("u1",),
            domains=("research",),
            shapes=("protocol",),
        )
        kg.add_facts(
            [
                FactWithContext(
                    fact_id="f1",
                    text="Ryn keeps a cited research protocol.",
                    source_type=SourceType.AUTHOR_FACT,
                )
            ],
            scope=scope,
            tags=tags,
        )

        facts = kg.query_facts()
        assert len(facts) == 1
        assert facts[0].tag_universes == ["u1"]
        assert facts[0].tag_domains == ["research"]
        assert facts[0].tag_shapes == ["protocol"]
    finally:
        kg.close()
