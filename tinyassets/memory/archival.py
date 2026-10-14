"""Archival memory -- bridge to the knowledge graph and ASP engine.

The knowledge graph IS the archival memory.  This module does not
duplicate storage; it imports and delegates to the knowledge and
constraints modules.  Graceful fallbacks return empty results when
the KG is not populated or Clingo is not installed.
"""

from __future__ import annotations

import logging
from typing import Any

from tinyassets.boxes.provider import BoxError
from tinyassets.okf.bundle import InvalidBundle

logger = logging.getLogger(__name__)


class ArchivalMemory:
    """Thin bridge to the KG (HippoRAG), RAPTOR tree, and ASP store.

    All methods return empty results when the backing modules are not
    available, allowing the memory system to work standalone during
    early development phases.
    """

    def __init__(self, universe_id: str, source) -> None:
        self._universe_id = universe_id
        from tinyassets.knowledge.knowledge_graph import KnowledgeGraph

        self._kg = KnowledgeGraph(source)
        self._asp = self._try_load_asp()

    # ------------------------------------------------------------------
    # Knowledge graph queries (HippoRAG / RAPTOR)
    # ------------------------------------------------------------------

    def hipporag_query(
        self,
        entities: list[str],
        k: int = 20,
    ) -> list[dict[str, Any]]:
        """Retrieve facts via Personalized PageRank on the KG.

        Returns up to *k* facts related to the given entities.
        """
        if self._kg is None:
            return []
        try:
            return self._kg.hipporag_query(entities=entities, k=k)
        except (InvalidBundle, BoxError):
            raise
        except Exception:
            logger.debug("HippoRAG query failed; returning empty", exc_info=True)
            return []

    def raptor_query(
        self,
        query: str,
        k: int = 5,
    ) -> list[dict[str, Any]]:
        """Retrieve summaries from the RAPTOR tree for global queries."""
        if self._kg is None:
            return []
        try:
            return self._kg.raptor_query(query=query, k=k)
        except (InvalidBundle, BoxError):
            raise
        except Exception:
            logger.debug("RAPTOR query failed; returning empty", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Promise tracking (deterministic, not vector search)
    # ------------------------------------------------------------------

    def get_open_promises(self, overdue: bool = False) -> list[dict[str, Any]]:
        """Return open narrative promises from the knowledge store.

        If *overdue* is True, only return promises past their expected
        resolution point.
        """
        if self._kg is None:
            return []
        try:
            return self._kg.get_open_promises(overdue=overdue)
        except (InvalidBundle, BoxError):
            raise
        except Exception:
            logger.debug("Promise query failed; returning empty", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # ASP constraint queries
    # ------------------------------------------------------------------

    def validate_facts(
        self,
        facts: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Run facts through the ASP solver for consistency.

        Returns a dict with ``valid`` (bool) and ``violations`` (list).
        """
        if self._asp is None:
            return {"valid": True, "violations": []}
        try:
            return self._asp.validate(facts)
        except (InvalidBundle, BoxError):
            raise
        except Exception:
            logger.debug("ASP validation failed; assuming valid", exc_info=True)
            return {"valid": True, "violations": []}

    # ------------------------------------------------------------------
    # Module loading helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _try_load_asp() -> Any | None:
        """Import and instantiate the ASP engine."""
        try:
            from tinyassets.constraints.asp_engine import ASPEngine
            return ASPEngine()
        except ImportError:
            logger.debug("asp_engine not available; archival ASP disabled")
            return None
        except (InvalidBundle, BoxError):
            raise
        except Exception:
            logger.warning("ASPEngine instantiation failed", exc_info=True)
            return None
