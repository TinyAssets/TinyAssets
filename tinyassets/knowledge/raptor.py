"""RAPTOR tree synthesis -- recursive abstractive summarization.

Builds a tree from: chunks -> clusters -> summaries -> meta-summaries.
Provides 72%+ accuracy on multi-hop reasoning by enabling traversal at
multiple abstraction levels.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np

# ---------------------------------------------------------------------------
# Tree structure
# ---------------------------------------------------------------------------


@dataclass
class RaptorNode:
    """A node in the RAPTOR tree (leaf = chunk, internal = summary)."""

    node_id: str
    text: str
    level: int  # 0 = leaf (raw chunks), higher = more abstract
    children: list[str] = field(default_factory=list)
    embedding: list[float] | None = None


@dataclass
class RaptorTree:
    """Multi-level abstraction tree for global/thematic queries."""

    nodes: dict[str, RaptorNode] = field(default_factory=dict)
    levels: dict[int, list[str]] = field(default_factory=dict)

    def add_node(self, node: RaptorNode) -> None:
        self.nodes[node.node_id] = node
        self.levels.setdefault(node.level, []).append(node.node_id)

    @property
    def depth(self) -> int:
        return max(self.levels.keys()) + 1 if self.levels else 0

    def get_level(self, level: int) -> list[RaptorNode]:
        """Get all nodes at a given level."""
        return [self.nodes[nid] for nid in self.levels.get(level, [])]

    def query_top_level(self) -> list[str]:
        """Return summaries from the highest abstraction level."""
        if not self.levels:
            return []
        top = max(self.levels.keys())
        return [self.nodes[nid].text for nid in self.levels[top]]


# ---------------------------------------------------------------------------
# Clustering (GMM-based, using simple k-means as MVP fallback)
# ---------------------------------------------------------------------------


def _cluster_embeddings(
    embeddings: np.ndarray,
    n_clusters: int | None = None,
) -> list[list[int]]:
    """Cluster embedding vectors into groups.

    Uses a simple distance-based approach for MVP.  For production,
    replace with GMM clustering.

    Returns list of clusters, each containing indices into the
    embeddings array.
    """
    n = len(embeddings)
    if n == 0:
        return []
    if n_clusters is None:
        n_clusters = max(1, n // 3)
    n_clusters = min(n_clusters, n)

    if n_clusters <= 1:
        return [list(range(n))]

    # Simple k-means-style clustering
    rng = np.random.default_rng(42)
    center_indices = rng.choice(n, size=n_clusters, replace=False)
    centers = embeddings[center_indices].copy()

    for _ in range(10):
        # Assign to nearest center
        clusters: list[list[int]] = [[] for _ in range(n_clusters)]
        for i in range(n):
            dists = np.linalg.norm(centers - embeddings[i], axis=1)
            clusters[int(np.argmin(dists))].append(i)

        # Update centers
        for c in range(n_clusters):
            if clusters[c]:
                centers[c] = embeddings[clusters[c]].mean(axis=0)

    # Remove empty clusters
    return [c for c in clusters if c]


# ---------------------------------------------------------------------------
# Tree construction
# ---------------------------------------------------------------------------


async def build_raptor_tree(
    chunks: list[str],
    embeddings: np.ndarray,
    provider_call: Callable,
    max_depth: int = 4,
    tree_id: str = "default",
) -> RaptorTree:
    """Build a RAPTOR tree from text chunks.

    Parameters
    ----------
    chunks
        Raw text chunks (leaf level).
    embeddings
        Pre-computed embeddings for the chunks (shape: [n, dim]).
    provider_call
        Async callable for summarization:
        ``provider_call(prompt, system, role) -> str``.
    max_depth
        Maximum tree depth (4 is typical).
    tree_id
        Identifier prefix for node IDs.

    Returns
    -------
    RaptorTree
        Multi-level abstraction tree.
    """
    tree = RaptorTree()

    # Level 0: raw chunks
    current_texts = []
    current_embeddings = embeddings.copy()
    for i, chunk in enumerate(chunks):
        node = RaptorNode(
            node_id=f"{tree_id}_L0_{i}",
            text=chunk,
            level=0,
            embedding=embeddings[i].tolist() if i < len(embeddings) else None,
        )
        tree.add_node(node)
        current_texts.append(chunk)

    # Build higher levels
    system = (
        "You are a summarization engine. Given a cluster of related text "
        "passages, write a concise summary that captures the key information. "
        "Output only the summary text."
    )

    for level in range(1, max_depth):
        if len(current_texts) <= 1:
            break

        clusters = _cluster_embeddings(current_embeddings)
        if len(clusters) <= 1 and level > 1:
            break

        next_texts = []
        next_embeddings_list = []
        child_id_base = tree.levels.get(level - 1, [])

        for c_idx, cluster_indices in enumerate(clusters):
            cluster_texts = [current_texts[i] for i in cluster_indices]

            prompt = (
                "Summarize the following passages into a concise summary:\n\n"
                + "\n---\n".join(cluster_texts)
            )
            summary = await provider_call(prompt, system, "extract")

            # Compute summary embedding as mean of cluster embeddings
            cluster_embs = current_embeddings[cluster_indices]
            mean_emb = cluster_embs.mean(axis=0)

            children = [child_id_base[i] for i in cluster_indices
                        if i < len(child_id_base)]

            node = RaptorNode(
                node_id=f"{tree_id}_L{level}_{c_idx}",
                text=summary,
                level=level,
                children=children,
                embedding=mean_emb.tolist(),
            )
            tree.add_node(node)
            next_texts.append(summary)
            next_embeddings_list.append(mean_emb)

        current_texts = next_texts
        if next_embeddings_list:
            current_embeddings = np.array(next_embeddings_list)
        else:
            current_embeddings = np.array([])

    return tree


def query_raptor_tree(
    tree: RaptorTree,
    query_embedding: np.ndarray,
    level: int | None = None,
    top_k: int = 3,
) -> list[str]:
    """Query the RAPTOR tree for relevant summaries.

    Parameters
    ----------
    tree
        A built RAPTOR tree.
    query_embedding
        Pre-computed query embedding.
    level
        Specific level to search. None = search top level.
    top_k
        Number of results to return.

    Returns
    -------
    list[str]
        Most relevant summaries/chunks.
    """
    if not tree.nodes:
        return []

    if level is None:
        level = max(tree.levels.keys()) if tree.levels else 0

    nodes = tree.get_level(level)
    if not nodes:
        return []

    # Rank by cosine similarity to query embedding
    scored = []
    query_norm = np.linalg.norm(query_embedding)
    if query_norm == 0:
        return [n.text for n in nodes[:top_k]]

    for node in nodes:
        if node.embedding is None:
            continue
        node_emb = np.array(node.embedding)
        node_norm = np.linalg.norm(node_emb)
        if node_norm == 0:
            continue
        similarity = np.dot(query_embedding, node_emb) / (query_norm * node_norm)
        scored.append((similarity, node.text))

    scored.sort(key=lambda x: x[0], reverse=True)
    return [text for _, text in scored[:top_k]]


# ---------------------------------------------------------------------------
# Canon-based tree rebuild (shared helper)
# ---------------------------------------------------------------------------

_MIN_PARAGRAPH_LEN = 50


def _read_canon_paragraphs(canon_dir: str) -> list[str]:
    """Read canon/*.md files and split into paragraphs (>50 chars).

    Returns a flat list of non-trivial paragraphs suitable for RAPTOR
    leaf nodes.

    Enumeration is routed through :func:`iter_canon_files`, which resolves +
    contains each entry before read; a symlinked ``.md`` whose target lives
    outside ``canon_dir`` is skipped, so a poisoned canon entry cannot pull
    external content into the RAPTOR tree.
    """
    from pathlib import Path

    from tinyassets.ingestion.canon_io import iter_canon_files

    paragraphs: list[str] = []
    canon = Path(canon_dir)
    if not canon.exists():
        return paragraphs
    for md_file in iter_canon_files(canon, suffix=".md"):
        try:
            text = md_file.read_text(encoding="utf-8")
        except OSError:
            continue
        for para in text.split("\n\n"):
            stripped = para.strip()
            if len(stripped) >= _MIN_PARAGRAPH_LEN:
                paragraphs.append(stripped)
    return paragraphs


def rebuild_raptor_from_canon(
    canon_dir: str,
    embed_fn: Callable | None,
    universe_id: str = "default",
) -> RaptorTree | None:
    """Build a RAPTOR tree from canon files and assign to runtime.

    This is the shared entry point called at daemon startup and after
    worldbuild.  Reads canon/*.md, splits into paragraphs, embeds, and
    builds the tree using the provider stub for summarization.

    Returns the built tree (also assigned to ``runtime.raptor_tree``),
    or None if the build was skipped.
    """
    import asyncio
    import logging

    from tinyassets.exceptions import ProviderAuthorityHeldError

    logger = logging.getLogger(__name__)

    paragraphs = _read_canon_paragraphs(canon_dir)
    if len(paragraphs) < 2:
        logger.debug("RAPTOR: <2 canon paragraphs, skipping tree build")
        return None

    if embed_fn is None:
        logger.debug("RAPTOR: no embed_fn, skipping tree build")
        return None

    # Embed all paragraphs
    try:
        embeddings = np.array(
            [embed_fn(p) for p in paragraphs], dtype=np.float32,
        )
    except Exception as e:
        logger.debug("RAPTOR: embedding failed: %s", e)
        return None

    # Async wrapper for the sync provider call. No ``fallback_response``: an
    # empty string would become an empty summary node, i.e. a tree that looks
    # built but says nothing. A failed call fails the build instead.
    async def _summarize(prompt: str, system: str, role: str) -> str:
        from tinyassets.providers.model_call import text_sync
        return text_sync(prompt, system, role=role)

    # Build tree
    try:
        loop = asyncio.new_event_loop()
        try:
            tree = loop.run_until_complete(
                build_raptor_tree(
                    chunks=paragraphs,
                    embeddings=embeddings,
                    provider_call=_summarize,
                    max_depth=3,
                    tree_id=universe_id,
                )
            )
        finally:
            loop.close()

        import tinyassets.runtime_singletons as rt
        rt.raptor_tree = tree
        logger.info(
            "RAPTOR tree built: %d nodes, depth %d from %d canon paragraphs",
            len(tree.nodes), tree.depth, len(paragraphs),
        )
        return tree
    except ProviderAuthorityHeldError:
        # Includes PlatformLLMCallRefusedError (Hard Rule 15). A refused
        # summarization call propagates to the caller, loudly; it is never
        # reported as a skipped build.
        raise
    except Exception as e:
        logger.warning("RAPTOR tree build failed: %s", e)
        return None
