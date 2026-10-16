"""Memory manager -- central interface for the three-tier hierarchy.

Consumed by graph-core nodes via:
    ``MemoryManager.assemble_context(phase, state) -> ContextBundle``

Coordinates core, episodic, and archival memory to build phase-specific
context bundles that fit within the ~8-15K token budget.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from tinyassets.exceptions import ContextBundleOverflowError
from tinyassets.memory.archival import ArchivalMemory
from tinyassets.memory.core import CoreMemory
from tinyassets.memory.episodic import EpisodicMemory
from tinyassets.memory.promotion import PromotionGates, PromotionResult
from tinyassets.memory.reflexion import ReflexionEngine, ReflexionResult
from tinyassets.okf.records import SourceRecords

logger = logging.getLogger(__name__)

# Phase names matching SceneState flow.
ORIENT = "orient"
PLAN = "plan"
DRAFT = "draft"
EVALUATE = "evaluate"

# Maximum token budget for assembled context.
MAX_CONTEXT_TOKENS = 15_000


def _debug_context_enabled() -> bool:
    """Return True when TINYASSETS_DEBUG_CONTEXT is set to a truthy value."""
    return os.environ.get("TINYASSETS_DEBUG_CONTEXT", "").lower() in {
        "1", "on", "true", "yes",
    }


def _estimate_bundle_tokens(bundle: dict[str, Any]) -> int:
    """Rough token count (4 chars ≈ 1 token) for a bundle dict.

    Matches ``CoreMemory.estimated_tokens`` shape so trim decisions use
    the same yardstick throughout the budget pipeline. BUG-024: the
    overflow check previously measured CoreMemory only; the trim then
    operated against that stale number rather than remeasuring the
    bundle it was mutating.
    """
    return sum(len(str(v)) for v in bundle.values()) // 4


class ContextBundle(dict):
    """Phase-specific context assembled from the memory hierarchy.

    Extends dict so nodes can access fields with bracket notation.
    """

    @property
    def phase(self) -> str:
        return self.get("phase", "")


class MemoryManager:
    """Central memory coordinator for all four graph levels.

    Parameters
    ----------
    universe_id : str
        Universe namespace.
    source : SourceRecords
        Explicitly admitted, reviewed canonical owner-box records.
    window_chapters : int
        Sliding window size for episodic memory.
    """

    def __init__(
        self,
        universe_id: str,
        source: SourceRecords,
        window_chapters: int = 5,
    ) -> None:
        self.core = CoreMemory(universe_id)
        self.episodic = EpisodicMemory(
            source=source,
            universe_id=universe_id,
            window_chapters=window_chapters,
        )
        self.archival = ArchivalMemory(
            universe_id, source=SourceRecords(source.bundle, "knowledge")
        )
        self._promotion = PromotionGates()
        self._reflexion = ReflexionEngine(episodic=self.episodic)
        self._universe_id = universe_id

    def close(self) -> None:
        """Release resources."""
        self.episodic.close()
        self.archival._kg.close()

    # ------------------------------------------------------------------
    # Primary interface: assemble context for a given phase
    # ------------------------------------------------------------------

    def assemble_context(
        self,
        phase: str,
        state: dict[str, Any],
    ) -> ContextBundle:
        """Build a phase-specific context bundle from all memory tiers.

        Parameters
        ----------
        phase : str
            One of ``'orient'``, ``'plan'``, ``'draft'``, ``'evaluate'``.
        state : dict
            Current scene/chapter state.

        Returns
        -------
        ContextBundle
            Dict-like object with phase-relevant fields.
        """
        builder = {
            ORIENT: self._assemble_orient,
            PLAN: self._assemble_plan,
            DRAFT: self._assemble_draft,
            EVALUATE: self._assemble_evaluate,
        }

        build_fn = builder.get(phase, self._assemble_orient)
        bundle = build_fn(state)
        bundle["phase"] = phase

        bundle_tokens = _estimate_bundle_tokens(bundle)
        if bundle_tokens > MAX_CONTEXT_TOKENS:
            logger.warning(
                "Context bundle exceeds budget: ~%d tokens (max %d) — trimming",
                bundle_tokens, MAX_CONTEXT_TOKENS,
            )
            if _debug_context_enabled():
                self._log_core_field_breakdown(
                    phase, self.core.estimated_tokens(),
                )
                self._log_bundle_field_breakdown(phase, bundle)
            bundle = self._trim_to_budget(bundle, bundle_tokens)

        return ContextBundle(bundle)

    # ------------------------------------------------------------------
    # Diagnostic instrumentation (TINYASSETS_DEBUG_CONTEXT=1)
    # ------------------------------------------------------------------

    def _log_core_field_breakdown(self, phase: str, total_tokens: int) -> None:
        """Log per-category + per-key token cost within CoreMemory.

        Gated on TINYASSETS_DEBUG_CONTEXT=1. Covers the 48535-token overflow
        diagnosis: CoreMemory is the ONLY field counted by
        ``core.estimated_tokens()`` so this breakdown pinpoints which
        category (characters / world_state / promises / style_rules) and
        which key is dominating.
        """
        store = self.core._store
        parts: list[str] = []
        for category, items in store.items():
            cat_chars = sum(len(str(v)) for v in items.values())
            cat_tokens = cat_chars // 4
            parts.append(
                f"  {category}: ~{cat_tokens} tokens across {len(items)} keys"
            )
            for key, value in items.items():
                key_tokens = len(str(value)) // 4
                if key_tokens > 500:
                    parts.append(
                        f"    -> key={key!r} ~{key_tokens} tokens "
                        f"(type={type(value).__name__})"
                    )
        logger.warning(
            "CONTEXT-DEBUG core breakdown (phase=%s, total=~%d tokens):\n%s",
            phase, total_tokens, "\n".join(parts) or "  (empty)",
        )

    def _log_bundle_field_breakdown(
        self, phase: str, bundle: dict[str, Any]
    ) -> None:
        """Log per-field token cost within the returned bundle dict.

        The bundle is what actually flows into prompts downstream, so
        this is complementary to the CoreMemory breakdown — useful when
        the overflow driver is a duplicated field (e.g. characters
        embedded inside world_state AND loaded separately).
        """
        parts: list[str] = []
        for key, value in bundle.items():
            tok = len(str(value)) // 4
            if isinstance(value, list):
                parts.append(
                    f"  {key}: ~{tok} tokens, list len={len(value)}"
                )
            elif isinstance(value, dict):
                parts.append(
                    f"  {key}: ~{tok} tokens, dict keys={len(value)}"
                )
            else:
                parts.append(f"  {key}: ~{tok} tokens ({type(value).__name__})")
        logger.warning(
            "CONTEXT-DEBUG bundle breakdown (phase=%s):\n%s",
            phase, "\n".join(parts) or "  (empty)",
        )

    # ------------------------------------------------------------------
    # Budget enforcement
    # ------------------------------------------------------------------

    def _trim_to_budget(
        self, bundle: dict[str, Any], current_tokens: int
    ) -> dict[str, Any]:
        """Iteratively trim the bundle until it fits the token budget.

        BUG-024 fix: the previous implementation trimmed list lengths
        once against the CoreMemory-derived input token count and
        accepted whatever remained — so an over-budget bundle stayed
        over-budget. Contract now: returns ≤ MAX_CONTEXT_TOKENS or
        raises ``ContextBundleOverflowError``.

        Priority order (trimmed first to last):
          1. recent_reflections — least critical, easily regenerated
          2. recent_summaries / recent_scenes — reduce window
          3. facts / canon_facts — keep first N
          4. active_characters — summarise instead of full state
          5. world_state — keep as-is (highest priority)
        """
        _MAX_ITERATIONS = 3
        tokens = current_tokens

        for attempt in range(1, _MAX_ITERATIONS + 1):
            if tokens <= MAX_CONTEXT_TOKENS:
                return bundle

            ratio = MAX_CONTEXT_TOKENS / max(tokens, 1)
            # Each successive pass trims harder: attempt 1 = proportional
            # to the overage, attempt 2 halves, attempt 3 quarters.
            aggression = 0.5 ** (attempt - 1)
            effective_ratio = ratio * aggression

            bundle = self._trim_lists(bundle, effective_ratio)
            bundle = self._trim_character_dict(bundle, aggressive=attempt >= 2)
            if attempt >= 2:
                bundle = self._truncate_string_bodies(
                    bundle, target_tokens=MAX_CONTEXT_TOKENS,
                )

            tokens = _estimate_bundle_tokens(bundle)
            logger.debug(
                "_trim_to_budget attempt %d: %d tokens (target %d)",
                attempt, tokens, MAX_CONTEXT_TOKENS,
            )

        if tokens > MAX_CONTEXT_TOKENS:
            raise ContextBundleOverflowError(
                f"Context bundle still at ~{tokens} tokens after "
                f"{_MAX_ITERATIONS} trim passes (budget {MAX_CONTEXT_TOKENS}). "
                "CoreMemory or retrieval layer is producing irreducibly "
                "large content. Enable TINYASSETS_DEBUG_CONTEXT=1 to inspect "
                "per-field sizes."
            )

        return bundle

    def _trim_lists(
        self, bundle: dict[str, Any], ratio: float
    ) -> dict[str, Any]:
        """Proportionally shrink list-valued bundle fields."""
        _list_fields = (
            "recent_reflections",
            "recent_summaries",
            "recent_scenes",
            "facts",
            "canon_facts",
            "promises",
            "orient_warnings",
            "character_goals",
        )
        for key in _list_fields:
            items = bundle.get(key)
            if isinstance(items, list) and len(items) > 1:
                keep = max(1, int(len(items) * ratio))
                bundle[key] = items[:keep]
        return bundle

    def _trim_character_dict(
        self, bundle: dict[str, Any], *, aggressive: bool
    ) -> dict[str, Any]:
        """Strip verbose fields from active_characters.

        First pass keeps a wider set of fields; aggressive pass also drops
        `goals` and caps remaining string values at 200 chars.
        """
        chars = bundle.get("active_characters")
        if not isinstance(chars, dict):
            return bundle

        if aggressive:
            _keep_keys = {"name", "id", "role", "status"}
            _value_cap = 200
        else:
            _keep_keys = {"name", "id", "role", "goals", "status"}
            _value_cap = 1_000

        trimmed: dict[str, Any] = {}
        for cid, cdata in chars.items():
            if isinstance(cdata, dict):
                kept: dict[str, Any] = {}
                for k, v in cdata.items():
                    if k not in _keep_keys:
                        continue
                    if isinstance(v, str) and len(v) > _value_cap:
                        kept[k] = v[:_value_cap] + "…"
                    else:
                        kept[k] = v
                trimmed[cid] = kept
            else:
                trimmed[cid] = cdata
        bundle["active_characters"] = trimmed
        return bundle

    def _truncate_string_bodies(
        self, bundle: dict[str, Any], *, target_tokens: int
    ) -> dict[str, Any]:
        """Hard-truncate the largest top-level string fields.

        Repeats while any single field owns more than half the budget.
        Touches scalar strings only — lists and dicts are shrunk by the
        list/char trimmers.
        """
        target_chars = target_tokens * 4
        for _ in range(5):
            biggest_key = None
            biggest_len = 0
            for key, value in bundle.items():
                if key == "phase" or not isinstance(value, str):
                    continue
                if len(value) > biggest_len:
                    biggest_key = key
                    biggest_len = len(value)
            if biggest_key is None or biggest_len <= target_chars // 2:
                break
            bundle[biggest_key] = bundle[biggest_key][: target_chars // 2] + "…"
        return bundle

    # ------------------------------------------------------------------
    # Phase-specific assemblers
    # ------------------------------------------------------------------

    def _assemble_orient(self, state: dict[str, Any]) -> dict[str, Any]:
        """ORIENT: world state, promises, warnings, reflections."""
        chapter = state.get("chapter_number", 1)
        characters = state.get("orient_result", {}).get("characters", [])

        # Core: load active characters and world state.
        self.core.clear()
        self.core.load_characters(characters)
        self.core.load_world_state(
            state.get("orient_result", {}).get("world_state", {})
        )

        # Episodic: recent summaries for continuity.
        recent = self.episodic.get_recent(chapter=chapter, k=3)
        reflections = self.episodic.get_recent_reflections(k=2)

        # Archival: open promises.
        promises = self.archival.get_open_promises(overdue=True)

        return {
            "world_state": self.core.get("world_state", "current", {}),
            "active_characters": self.core.get_all("characters"),
            "active_promises": promises,
            "recent_summaries": [
                {"ch": s.chapter_number, "sc": s.scene_number, "summary": s.summary}
                for s in recent
            ],
            "recent_reflections": reflections,
            "continuity_flags": [],
        }

    def _assemble_plan(self, state: dict[str, Any]) -> dict[str, Any]:
        """PLAN: beat context, character goals, orient warnings, style rules."""
        chapter = state.get("chapter_number", 1)
        scene_chars = state.get("orient_result", {}).get("characters", [])
        char_names = [
            c.get("name", c.get("id", "")) for c in scene_chars
        ]

        # Archival: facts about characters in this scene.
        facts = self.archival.hipporag_query(entities=char_names, k=20)

        # Archival: open promises.
        promises = self.archival.get_open_promises(overdue=True)

        # Episodic: recent summaries.
        recent = self.episodic.get_recent(chapter=chapter, k=5)

        # Core: style rules.
        style_rules = self.core.get_all("style_rules")

        return {
            "recent_scenes": [
                {"ch": s.chapter_number, "sc": s.scene_number, "summary": s.summary}
                for s in recent
            ],
            "facts": facts,
            "promises": promises,
            "orient_warnings": state.get("orient_result", {}).get("warnings", []),
            "style_rules": list(style_rules.values()),
            "character_goals": [
                c.get("goals", []) for c in scene_chars
            ],
        }

    def _assemble_draft(self, state: dict[str, Any]) -> dict[str, Any]:
        """DRAFT: recent prose, voice refs, style rules, beat sheet."""
        return {
            "recent_prose": state.get("recent_prose", ""),
            "beat_sheet": state.get("plan_output", {}),
            "voice_refs": self.core.get_all("voice_refs"),
            "style_rules": list(self.core.get_all("style_rules").values()),
        }

    def _assemble_evaluate(self, state: dict[str, Any]) -> dict[str, Any]:
        """EVALUATE: draft text, canon facts, writing system, contract."""
        draft = state.get("draft_output", {})
        scene_chars = state.get("orient_result", {}).get("characters", [])
        char_names = [
            c.get("name", c.get("id", "")) for c in scene_chars
        ]

        canon_facts = self.archival.hipporag_query(entities=char_names, k=30)

        return {
            "draft_text": draft.get("prose", ""),
            "word_count": draft.get("word_count", 0),
            "canon_facts": canon_facts,
            "scene_contract": state.get("plan_output", {}).get(
                "done_when", []
            ),
            "writing_system": self.core.get("style_rules", "writing_system", {}),
        }

    # ------------------------------------------------------------------
    # Scene result storage
    # ------------------------------------------------------------------

    def store_scene_result(self, state: dict[str, Any]) -> None:
        """Persist scene outputs to episodic memory.

        Called after commit node accepts a scene.
        """
        draft = state.get("draft_output", {})
        summary = draft.get("prose", "")[:500]
        word_count = draft.get("word_count", 0)

        self.episodic.store_summary(
            book_number=state.get("book_number", 1),
            chapter_number=state.get("chapter_number", 1),
            scene_number=state.get("scene_number", 1),
            summary=summary,
            word_count=word_count,
        )

        # Store extracted facts.
        for fact in state.get("extracted_facts", []):
            fact_id = fact.get("fact_id")
            content = fact.get("text")
            if not fact_id or not content:
                raise ValueError(
                    "extracted_facts entries must include 'fact_id' and 'text'"
                )
            self.episodic.store_fact(
                fact_id=fact_id,
                entity=fact.get("entity", fact.get("narrator", "unknown")),
                content=content,
                source_scene=(
                    f"b{state.get('book_number', 1)}"
                    f"c{state.get('chapter_number', 1)}"
                    f"s{state.get('scene_number', 1)}"
                ),
            )

        # Store style observations.
        for obs in state.get("style_observations", []):
            self.episodic.store_observation(
                dimension=obs.get("dimension", "general"),
                observation=obs.get("observation", str(obs)),
                scene_ref=(
                    f"b{state.get('book_number', 1)}"
                    f"c{state.get('chapter_number', 1)}"
                    f"s{state.get('scene_number', 1)}"
                ),
            )

        logger.debug(
            "Stored scene result: b%d/c%d/s%d (%d words, %d facts, %d obs)",
            state.get("book_number", 1),
            state.get("chapter_number", 1),
            state.get("scene_number", 1),
            word_count,
            len(state.get("extracted_facts", [])),
            len(state.get("style_observations", [])),
        )

    # ------------------------------------------------------------------
    # Promotion gates
    # ------------------------------------------------------------------

    def run_promotion_gates(
        self,
        violations: list[dict[str, Any]] | None = None,
    ) -> PromotionResult:
        """Run all promotion gates on the current episodic state."""
        return self._promotion.run(
            episodic=self.episodic,
            violations=violations,
        )

    # ------------------------------------------------------------------
    # Reflexion
    # ------------------------------------------------------------------

    def run_reflexion(
        self,
        state: dict[str, Any],
        judge_feedback: list[dict[str, Any]] | None = None,
    ) -> ReflexionResult:
        """Run the reflexion loop on a reverted scene."""
        return self._reflexion.reflect(
            state=state,
            judge_feedback=judge_feedback,
        )

    # ------------------------------------------------------------------
    # Sliding window maintenance
    # ------------------------------------------------------------------

    def evict_old_data(self, current_chapter: int, book: int = 1) -> int:
        """Evict episodic data outside the sliding window."""
        return self.episodic.evict_old_summaries(
            current_chapter=current_chapter,
            book=book,
        )
