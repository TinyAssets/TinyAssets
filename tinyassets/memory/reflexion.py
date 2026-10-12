"""Reflexion engine -- self-critique and verbal reflection on revert.

When the commit node issues a ``revert`` verdict the reflexion engine:
1. Generates a self-critique of the failed prose.
2. Writes a verbal reflection summarising what went wrong.
3. Stores the reflection in episodic memory.
4. Updates memory weights (future orient context includes the lesson).

Uses the LLM via ``text_sync`` when available, falling back to
template-based critique when no provider is configured.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from tinyassets.exceptions import ProviderAuthorityHeldError

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class ReflexionResult:
    """Outcome of a reflexion cycle."""

    critique: str
    reflection: str
    updated_weights: dict[str, float]


class ReflexionEngine:
    """Generates self-critique and verbal reflection after a revert.

    Uses the LLM provider for richer self-critique when available,
    falling back to template-based reflection otherwise.
    """

    def __init__(self, episodic: Any = None) -> None:
        self._episodic = episodic

    def reflect(
        self,
        state: dict[str, Any],
        judge_feedback: list[dict[str, Any]] | None = None,
        editorial_notes: dict[str, Any] | None = None,
    ) -> ReflexionResult:
        """Run the reflexion loop on a reverted scene.

        Parameters
        ----------
        state : dict
            The scene state at the point of revert.
        judge_feedback : list[dict] | None
            Legacy judge verdicts (backward compat).
        editorial_notes : dict | None
            Editorial reader notes (protect, concerns, next_scene).
        """
        # Prefer editorial notes; fall back to judge_feedback for compat
        if editorial_notes is None:
            editorial_notes = state.get("editorial_notes")
        critique = self._generate_critique(state, judge_feedback, editorial_notes)
        reflection = self._generate_reflection(critique, state)
        weights = self._update_weights(critique)

        # Persist to episodic memory if available.
        if self._episodic is not None:
            chapter = state.get("chapter_number", 0)
            scene = state.get("scene_number", 0)
            self._episodic.store_reflection(
                chapter_number=chapter,
                scene_number=scene,
                critique=critique,
                reflection=reflection,
            )

        logger.info(
            "Reflexion complete for ch%d/sc%d",
            state.get("chapter_number", 0),
            state.get("scene_number", 0),
        )

        return ReflexionResult(
            critique=critique,
            reflection=reflection,
            updated_weights=weights,
        )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _generate_critique(
        self,
        state: dict[str, Any],
        feedback: list[dict[str, Any]] | None,
        editorial_notes: dict[str, Any] | None = None,
    ) -> str:
        """Build a structured critique from editorial notes and state.

        Attempts to call the LLM for richer analysis. Falls back to
        template-based assembly when no provider is available.
        """
        # Always build the template critique as a baseline/fallback.
        template_critique = self._template_critique(
            state, feedback, editorial_notes,
        )

        # Attempt LLM-driven critique
        try:
            llm_critique = self._llm_critique(state, feedback, template_critique)
            if llm_critique:
                return llm_critique
        except ProviderAuthorityHeldError:
            # Includes PlatformLLMCallRefusedError (Hard Rule 15): a refused
            # model call propagates; it is never papered over with a template.
            raise
        except Exception as e:
            logger.debug("LLM critique unavailable, using template: %s", e)

        return template_critique

    def _generate_reflection(
        self, critique: str, state: dict[str, Any]
    ) -> str:
        """Synthesise a verbal reflection from the critique.

        Attempts to call the LLM for a richer reflection. Falls back
        to template-based synthesis when no provider is available.
        """
        # Attempt LLM-driven reflection
        try:
            llm_reflection = self._llm_reflection(critique, state)
            if llm_reflection:
                return llm_reflection
        except ProviderAuthorityHeldError:
            # Includes PlatformLLMCallRefusedError: propagate, never template.
            raise
        except Exception as e:
            logger.debug("LLM reflection unavailable, using template: %s", e)

        return self._template_reflection(critique, state)

    @staticmethod
    def _template_critique(
        state: dict[str, Any],
        feedback: list[dict[str, Any]] | None,
        editorial_notes: dict[str, Any] | None = None,
    ) -> str:
        """Build a template-based critique from editorial notes and state."""
        parts = []

        # Editorial notes (primary source)
        if editorial_notes and isinstance(editorial_notes, dict):
            for concern in editorial_notes.get("concerns", []):
                if isinstance(concern, dict):
                    text = concern.get("text", "")
                    label = "ERROR" if concern.get("clearly_wrong") else "concern"
                    quote = concern.get("quoted_passage", "")
                    entry = f"[editorial {label}] {text}"
                    if quote:
                        entry += f' — "{quote}"'
                    parts.append(entry)
            for item in editorial_notes.get("protect", []):
                if isinstance(item, str):
                    parts.append(f"[editorial strength] {item}")

        # Legacy judge feedback (backward compat)
        if feedback:
            for fb in feedback:
                verdict = fb.get("verdict", "unknown")
                reason = fb.get("rationale", fb.get("reason", ""))
                provider = fb.get("provider", "judge")
                parts.append(f"[{provider}] verdict={verdict}: {reason}")

        # Add context from quality trace if available.
        for entry in state.get("quality_trace", []):
            if entry.get("action") in ("revert", "second_draft"):
                parts.append(
                    f"[trace] {entry.get('action')}: "
                    f"{entry.get('reason', 'no reason')}"
                )

        if not parts:
            parts.append("Scene reverted but no specific feedback available.")

        return "\n".join(parts)

    @staticmethod
    def _template_reflection(critique: str, state: dict[str, Any]) -> str:
        """Synthesise a template-based verbal reflection from the critique."""
        scene_id = (
            f"book {state.get('book_number', '?')}, "
            f"chapter {state.get('chapter_number', '?')}, "
            f"scene {state.get('scene_number', '?')}"
        )
        return (
            f"Reflection on {scene_id}: The draft was reverted. "
            f"Key issues: {critique[:500]}. "
            "Next attempt should address these points directly."
        )

    @staticmethod
    def _llm_critique(
        state: dict[str, Any],
        feedback: list[dict[str, Any]] | None,
        template_critique: str,
    ) -> str:
        """Call the LLM for a structured self-critique.

        Returns the LLM response or empty string if unavailable.
        """
        from tinyassets.providers.model_call import text_sync

        scene_id = (
            f"book {state.get('book_number', '?')}, "
            f"chapter {state.get('chapter_number', '?')}, "
            f"scene {state.get('scene_number', '?')}"
        )

        draft_text = state.get("draft_output", {})
        if isinstance(draft_text, dict):
            draft_text = draft_text.get("prose", "")
        draft_snippet = str(draft_text)[:1500] if draft_text else ""

        system = (
            "You are a fiction writing self-critic. Analyze why a scene "
            "draft was reverted and identify the root causes. Be specific "
            "and actionable. Focus on: prose quality, continuity errors, "
            "pacing problems, character voice consistency, and narrative "
            "tension. Keep your critique under 500 words."
        )

        feedback_text = template_critique

        prompt = (
            f"# Scene: {scene_id}\n\n"
            f"# Judge Feedback\n\n{feedback_text}\n\n"
        )
        if draft_snippet:
            prompt += f"# Draft Excerpt\n\n{draft_snippet}\n\n"

        prompt += (
            "# Task\n\n"
            "Analyze the root causes of this revert. What specific "
            "aspects of the prose failed? What should the next attempt "
            "do differently? Structure your critique with clear categories."
        )

        # Use empty string as fallback sentinel -- caller checks for it
        result = text_sync(
            prompt, system, role="judge"
        )
        return result

    @staticmethod
    def _llm_reflection(critique: str, state: dict[str, Any]) -> str:
        """Call the LLM for a synthesized verbal reflection.

        Returns the LLM response or empty string if unavailable.
        """
        from tinyassets.providers.model_call import text_sync

        scene_id = (
            f"book {state.get('book_number', '?')}, "
            f"chapter {state.get('chapter_number', '?')}, "
            f"scene {state.get('scene_number', '?')}"
        )

        system = (
            "You are a fiction writing coach synthesizing a lesson from "
            "a failed scene draft. Write a concise reflection (2-4 sentences) "
            "that captures what went wrong and what the next attempt must do "
            "differently. This reflection will be stored in memory and used "
            "to guide future writing. Be concrete and specific."
        )

        prompt = (
            f"# Scene: {scene_id}\n\n"
            f"# Critique\n\n{critique}\n\n"
            f"# Task\n\n"
            f"Write a concise reflection summarizing the key lesson "
            f"from this failed draft. What is the single most important "
            f"thing to change next time?"
        )

        result = text_sync(
            prompt, system, role="judge"
        )
        return result

    @staticmethod
    def _update_weights(critique: str) -> dict[str, float]:
        """Adjust memory weights based on critique content.

        Currently returns static adjustments.  Future versions will
        parse critique categories and adjust per-dimension.
        """
        weights: dict[str, float] = {}

        critique_lower = critique.lower()
        if "continuity" in critique_lower or "consistency" in critique_lower:
            weights["continuity_check"] = 1.5
        if "voice" in critique_lower or "dialogue" in critique_lower:
            weights["voice_refs"] = 1.3
        if "pacing" in critique_lower:
            weights["pacing_analysis"] = 1.3
        if "character" in critique_lower:
            weights["character_state"] = 1.4

        return weights
