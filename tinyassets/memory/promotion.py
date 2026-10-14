"""Promotion gates -- lifecycle transitions across memory tiers.

Facts:     3+ scene evidence (consistent) -> promote to canonical
Rules:     persistent constraint violations (3+ scenes) -> ASP rule candidate
Style:     3+ clustered judge observations -> active style rule

Promotions run in the consolidate node at chapter boundaries.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

FACT_PROMOTION_THRESHOLD = 3
STYLE_PROMOTION_THRESHOLD = 3
VIOLATION_PROMOTION_THRESHOLD = 3


@dataclass
class PromotionResult:
    """Outcome of running promotion gates on a chapter's data."""

    promoted_facts: list[dict[str, Any]] = field(default_factory=list)
    promoted_style_rules: list[dict[str, Any]] = field(default_factory=list)
    asp_rule_candidates: list[dict[str, Any]] = field(default_factory=list)


class PromotionGates:
    """Evaluates facts, observations, and violations for tier promotion."""

    def __init__(
        self,
        fact_threshold: int = FACT_PROMOTION_THRESHOLD,
        style_threshold: int = STYLE_PROMOTION_THRESHOLD,
        violation_threshold: int = VIOLATION_PROMOTION_THRESHOLD,
    ) -> None:
        self._fact_threshold = fact_threshold
        self._style_threshold = style_threshold
        self._violation_threshold = violation_threshold

    def run(
        self,
        episodic: Any,
        violations: list[dict[str, Any]] | None = None,
    ) -> PromotionResult:
        """Evaluate all promotion gates and return the result.

        Parameters
        ----------
        episodic : EpisodicMemory
            The episodic memory store to scan for promotable items.
        violations : list[dict] | None
            Constraint violations accumulated during the chapter.
        """
        result = PromotionResult()

        # -- Fact promotion --
        promotable = episodic.get_promotable_facts(
            threshold=self._fact_threshold
        )
        for fact in promotable:
            episodic.mark_promoted(fact["fact_id"])
            result.promoted_facts.append(fact)
            logger.info(
                "Promoted fact %s (entity=%s, evidence=%d)",
                fact["fact_id"], fact["entity"], fact["evidence_count"],
            )

        # -- Style rule promotion --
        # Scan observation dimensions for those exceeding threshold.
        dimensions = self._get_observation_dimensions(episodic)
        for dim in dimensions:
            count = episodic.count_observations(dim)
            if count >= self._style_threshold:
                observations = episodic.get_observations_by_dimension(dim)
                rule = {
                    "dimension": dim,
                    "observation_count": count,
                    "summary": observations[0]["observation"] if observations else "",
                    "source_observations": observations[:5],
                }
                result.promoted_style_rules.append(rule)
                logger.info(
                    "Promoted style rule for dimension '%s' (%d observations)",
                    dim, count,
                )

        # -- ASP rule candidates from violations --
        if violations:
            violation_counts: dict[str, list[dict[str, Any]]] = {}
            for v in violations:
                key = v.get("rule", v.get("type", "unknown"))
                violation_counts.setdefault(key, []).append(v)

            for rule_key, instances in violation_counts.items():
                if len(instances) >= self._violation_threshold:
                    result.asp_rule_candidates.append({
                        "rule": rule_key,
                        "violation_count": len(instances),
                        "instances": instances[:5],
                    })
                    logger.info(
                        "ASP rule candidate: '%s' (%d violations)",
                        rule_key, len(instances),
                    )

        return result

    @staticmethod
    def _get_observation_dimensions(episodic: Any) -> list[str]:
        """Extract distinct style dimensions from the episodic store."""
        return episodic.observation_dimensions()
