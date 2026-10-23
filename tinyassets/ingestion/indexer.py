"""Indexer -- ingests text into KG + vector store after extraction.

Bridges the ingestion pipeline to the retrieval backends:
1. Extract entities/relationships/facts from text via entity_extraction
2. Add entities and edges to KnowledgeGraph
3. Compute embeddings and index chunks into VectorStore

All operations are optional -- if a backend is unavailable, that
step is skipped. Never blocks the pipeline.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:
    from tinyassets.memory.scoping import MemoryScope

logger = logging.getLogger(__name__)

# Max text length per extraction call (avoid overwhelming the LLM).
_MAX_EXTRACT_CHARS = 4000


def index_text(
    text: str,
    source_id: str,
    *,
    knowledge_graph: Any | None = None,
    vector_store: Any | None = None,
    embed_fn: Callable[[str], list[float]] | None = None,
    provider_call: Callable | None = None,
    chapter_number: int = 0,
    scope: "MemoryScope | None" = None,
) -> dict[str, int]:
    """Extract entities/facts from text and index into retrieval backends.

    Parameters
    ----------
    text : str
        Text content to index.
    source_id : str
        Identifier for the source (e.g., filename or scene_id).
    knowledge_graph : KnowledgeGraph or None
        If provided, entities/edges/facts are added.
    vector_store : VectorStore or None
        If provided, text chunks are embedded and indexed.
    embed_fn : callable or None
        Synchronous embedding function: text -> list[float].
    provider_call : callable or None
        Sync callable for entity extraction: (prompt, system, role) -> str.
    chapter_number : int
        Chapter number for vector store metadata.
    scope : MemoryScope or None
        Memory-scope Stage 2b: tags every KG and vector row with the
        caller's tier values. When ``None``, rows inherit NULL (=
        legacy/universe-public). Read-side enforcement waits on Stage
        2c's ``TINYASSETS_TIERED_SCOPE`` flip.

    Returns
    -------
    dict with counts: entities, edges, facts, chunks_indexed.
    """
    stats: dict[str, int] = {
        "entities": 0,
        "edges": 0,
        "facts": 0,
        "chunks_indexed": 0,
    }

    # Split text into manageable chunks
    chunks = _split_into_chunks(text)

    # Entity extraction + KG indexing
    if knowledge_graph is not None and provider_call is not None:
        for i, chunk in enumerate(chunks):
            try:
                result = _extract_and_index_kg(
                    chunk, f"{source_id}_chunk_{i}",
                    knowledge_graph, provider_call,
                    scope=scope,
                )
                stats["entities"] += result.get("entities", 0)
                stats["edges"] += result.get("edges", 0)
                stats["facts"] += result.get("facts", 0)
            except Exception as e:
                logger.warning(
                    "KG indexing failed for %s chunk %d: %s",
                    source_id, i, e,
                )

    # Vector store indexing
    if vector_store is not None and embed_fn is not None:
        for i, chunk in enumerate(chunks):
            try:
                _index_vector_chunk(
                    chunk, f"{source_id}_chunk_{i}",
                    vector_store, embed_fn,
                    source_id=source_id,
                    chapter_number=chapter_number,
                    scope=scope,
                )
                stats["chunks_indexed"] += 1
            except Exception as e:
                logger.warning(
                    "Vector indexing failed for %s chunk %d: %s",
                    source_id, i, e,
                )

    logger.info(
        "Indexed %s: %d entities, %d edges, %d facts, %d vector chunks",
        source_id, stats["entities"], stats["edges"],
        stats["facts"], stats["chunks_indexed"],
    )
    return stats


def _split_into_chunks(text: str) -> list[str]:
    """Split text into chunks suitable for extraction/embedding.

    Splits on paragraph boundaries, keeping chunks under _MAX_EXTRACT_CHARS.
    """
    if len(text) <= _MAX_EXTRACT_CHARS:
        return [text] if text.strip() else []

    paragraphs = text.split("\n\n")
    chunks: list[str] = []
    current: list[str] = []
    current_len = 0

    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        if current_len + len(para) > _MAX_EXTRACT_CHARS and current:
            chunks.append("\n\n".join(current))
            current = [para]
            current_len = len(para)
        else:
            current.append(para)
            current_len += len(para)

    if current:
        chunks.append("\n\n".join(current))

    return chunks


def _extract_and_index_kg(
    text: str,
    chunk_id: str,
    kg: Any,
    provider_call: Callable,
    *,
    scope: "MemoryScope | None" = None,
) -> dict[str, int]:
    """Extract entities/edges/facts and add to KnowledgeGraph."""
    from tinyassets.knowledge.entity_extraction import (
        FICTION_EXTRACTION_PROMPT,
        FICTION_EXTRACTION_SYSTEM,
        _build_edge,
        _build_entity,
        _build_fact,
    )

    prompt = FICTION_EXTRACTION_PROMPT.format(
        pov_character="narrator",
        prose=text[:_MAX_EXTRACT_CHARS],
    )

    raw = provider_call(prompt, FICTION_EXTRACTION_SYSTEM, role="extract")
    if not raw:
        logger.warning("Entity extraction returned empty for %s — using regex fallback", chunk_id)
        return _regex_fallback_kg(text, chunk_id, kg)

    # Parse response
    import json
    import re

    response_text = raw.strip()
    if response_text.startswith("```"):
        response_text = re.sub(r"^```(?:json)?\s*", "", response_text)
        response_text = re.sub(r"\s*```$", "", response_text)

    try:
        parsed = json.loads(response_text)
    except json.JSONDecodeError:
        logger.warning(
            "Failed to parse extraction JSON for %s (first 200 chars: %s) — using regex fallback",
            chunk_id, response_text[:200],
        )
        return _regex_fallback_kg(text, chunk_id, kg)

    # Build and index entities
    entities = [_build_entity(e) for e in parsed.get("entities", [])]
    for entity in entities:
        try:
            _write_entity_file(kg, entity, scope)
        except Exception as e:
            logger.warning("Failed to add entity %s: %s", entity.get("entity_id", "?"), e)

    # Build and index edges
    edges = [_build_edge(r) for r in parsed.get("relationships", [])]
    for edge in edges:
        try:
            _write_edge_file(kg, edge, scope)
        except Exception as e:
            logger.warning(
                "Failed to add edge %s->%s: %s",
                edge.get("source", "?"), edge.get("target", "?"), e,
            )

    # Build and index facts
    facts = [
        _build_fact(f, chunk_id, i, "narrator")
        for i, f in enumerate(parsed.get("facts", []))
    ]
    if facts:
        try:
            _write_fact_files(kg, facts, scope)
        except Exception as e:
            logger.warning("Failed to add %d facts: %s", len(facts), e)

    if not entities and not edges and not facts:
        logger.warning(
            "LLM extraction returned valid JSON but no data for %s",
            chunk_id,
        )
        return _regex_fallback_kg(text, chunk_id, kg, scope=scope)

    return {
        "entities": len(entities),
        "edges": len(edges),
        "facts": len(facts),
    }


def _regex_fallback_kg(
    text: str,
    chunk_id: str,
    kg: Any,
    *,
    scope: "MemoryScope | None" = None,
) -> dict[str, int]:
    """Fallback: extract character entities from prose using regex.

    When LLM extraction fails (provider exhaustion, non-JSON response),
    this ensures the KG still gets populated with at least the character
    names found in the text.
    """
    import re

    name_pattern = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")
    stopwords = {
        "The", "She", "Her", "His", "They", "This", "That", "But", "And",
        "Not", "Scene", "Chapter", "Book", "Northern", "Southern", "Eastern",
        "Western", "Ancient", "Great", "Old", "New", "Dark", "Bright",
        "When", "Then", "What", "Where", "How", "Who", "Why", "Now",
        "Once", "Here", "There", "Some", "From", "Into", "With",
    }

    names: set[str] = set()
    for match in name_pattern.finditer(text):
        name = match.group(1)
        first_word = name.split()[0]
        if first_word not in stopwords and len(name) > 2:
            names.add(name)

    from tinyassets.knowledge.entity_extraction import _build_entity

    entities_added = 0
    for name in names:
        entity_id = name.lower().replace(" ", "_")
        entity = _build_entity({
            "entity_id": entity_id,
            "entity_type": "character",
            "aliases": [name],
            "description": f"Character mentioned in {chunk_id}",
            "access_tier": 0,
        })
        try:
            _write_entity_file(kg, entity, scope)
            entities_added += 1
        except Exception as e:
            logger.warning("Regex fallback: failed to add entity %s: %s", entity_id, e)

    if entities_added:
        logger.info("Regex fallback indexed %d character entities for %s", entities_added, chunk_id)

    return {"entities": entities_added, "edges": 0, "facts": 0}


def _graph_owner(kg: Any):
    from pathlib import Path

    database = str(getattr(kg, "_db_path", "") or "")
    if not database or database == ":memory:":
        raise ValueError("knowledge files require an owner database path")
    return Path(database).parent


def _write_entity_file(kg: Any, entity: Any, scope: Any) -> None:
    from tinyassets.knowledge_authority import write_graph_entity

    write_graph_entity(_graph_owner(kg), entity, scope=scope)


def _write_edge_file(kg: Any, edge: Any, scope: Any) -> None:
    from tinyassets.knowledge_authority import write_graph_edge

    write_graph_edge(_graph_owner(kg), edge, scope=scope)


def _write_fact_files(kg: Any, facts: Any, scope: Any) -> None:
    from tinyassets.knowledge_authority import write_graph_facts

    write_graph_facts(_graph_owner(kg), facts, scope=scope)


def _index_vector_chunk(
    text: str,
    chunk_id: str,
    vector_store: Any,
    embed_fn: Callable[[str], list[float]],
    *,
    source_id: str = "",
    chapter_number: int = 0,
    scope: "MemoryScope | None" = None,
) -> None:
    """Compute embedding and index a single chunk into VectorStore."""
    embedding = embed_fn(text)
    if not embedding:
        return

    chunk_data = {
        "chunk_id": chunk_id,
        "text": text,
        "embedding": embedding,
        "scene_id": source_id,
        "chapter_number": chapter_number,
        "character": "",
        "location": "",
    }
    vector_store.index([chunk_data], scope=scope)
