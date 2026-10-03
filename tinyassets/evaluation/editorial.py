"""Editorial reader -- natural-language feedback from a different model.

One editorial voice replaces the judge ensemble. The editor reads the
scene and returns structured notes: what's working, what concerns them,
and a one-sentence suggestion for the next scene. The writer (a different
model) decides what to act on.

Uses the judge/evaluate role so Codex handles editorial reading, not Opus.
Separate worker from evaluator.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from tinyassets.utils.json_parsing import parse_llm_json

logger = logging.getLogger(__name__)


@dataclass
class EditorialConcern:
    """A single editorial concern with quoted evidence."""

    text: str
    quoted_passage: str = ""
    clearly_wrong: bool = False


@dataclass
class EditorialNotes:
    """Structured editorial feedback on a scene."""

    protect: list[str] = field(default_factory=list)
    """What's working well (1-2 items). The writer should preserve these."""

    concerns: list[EditorialConcern] = field(default_factory=list)
    """What concerns the editor (1-3 items). Each has quoted text and a
    clearly_wrong flag. clearly_wrong = provable error (wrong name, broken
    continuity). Not clearly_wrong = might be an intentional creative choice."""

    next_scene: str = ""
    """One sentence about what the next scene should do."""

    raw_response: str = ""
    """The raw LLM response for debugging."""


_EDITORIAL_SYSTEM = (
    "You are an editorial reader for a fantasy novel. Read the scene and "
    "give honest, specific feedback.\n\n"
    "Respond with EXACTLY one JSON object:\n"
    "{\n"
    '  "protect": ["what\'s working (1-2 items)"],\n'
    '  "concerns": [\n'
    "    {\n"
    '      "text": "what concerns you",\n'
    '      "quoted_passage": "exact words from the scene",\n'
    '      "clearly_wrong": true or false\n'
    "    }\n"
    "  ],\n"
    '  "next_scene": "one sentence for what the next scene should do"\n'
    "}\n\n"
    "Rules:\n"
    "- 1-2 things that are working. Be specific -- name the technique.\n"
    "- 1-3 concerns. Quote the exact passage. For each:\n"
    "  clearly_wrong = provable error (wrong name, contradicts canon, "
    "broken continuity, impossible action, or total premise departure)\n"
    "  clearly_wrong = false means it might be an intentional creative choice\n"
    "- If a Command Center Premise is provided, flag clearly_wrong if the scene's "
    "protagonist, world, or setting has no connection to the premise.\n"
    "- One sentence for next scene direction.\n"
    "- Return ONLY the JSON object."
)


def read_editorial(
    prose: str,
    *,
    previous_scene: str = "",
    canon_facts: str = "",
    direction_notes: str = "",
    premise: str = "",
    provider_call: Any = None,
) -> EditorialNotes | None:
    """Run the editorial reader on a scene.

    Uses the judge role (Codex, not Opus) to maintain separation between
    writer and evaluator.

    Parameters
    ----------
    prose : str
        The scene text to evaluate.
    previous_scene : str
        Previous scene prose for continuity context.
    canon_facts : str
        Relevant canon facts for accuracy checking.
    direction_notes : str
        Active user direction notes the scene should follow.
    premise : str
        The universe premise (PROGRAM.md). The editorial reader should flag
        scenes that depart entirely from this premise.
    provider_call : callable
        The provider call function (defaults to call_provider from _provider_stub).

    Returns
    -------
    EditorialNotes or None
        Structured notes on success, None if the editorial call fails or
        returns a mock response.
    """
    if not prose.strip():
        return None

    if provider_call is None:
        from tinyassets.providers.call import call_provider
        provider_call = call_provider

    # Build prompt with context sections
    parts: list[str] = []
    if premise:
        parts.append(f"## Command Center Premise\n{premise[:2000]}")
    if previous_scene:
        parts.append(f"## Previous Scene\n{previous_scene[-2000:]}")
    if canon_facts:
        parts.append(f"## Canon Facts\n{canon_facts[:2000]}")
    if direction_notes:
        parts.append(f"## Active Direction Notes\n{direction_notes[:1000]}")
    parts.append(f"## Scene to Read\n{prose[:6000]}")
    prompt = "\n\n".join(parts)

    try:
        raw = provider_call(prompt, _EDITORIAL_SYSTEM, role="judge")

        # Skip mock responses
        if raw is None or "[Mock" in raw:
            return None

        return _parse_editorial_response(raw)
    except Exception as e:
        logger.warning("Editorial reader failed: %s", e)
        return None


def _parse_editorial_response(raw: str) -> EditorialNotes | None:
    """Parse the editorial reader's JSON response into EditorialNotes."""
    data = parse_llm_json(raw, expect_type=dict, fallback=None)
    if data is None:
        logger.warning("Could not parse editorial response as JSON")
        return None

    # Parse protect list
    protect = []
    for item in data.get("protect", []):
        if isinstance(item, str) and item.strip():
            protect.append(item.strip())

    # Parse concerns
    concerns = []
    for item in data.get("concerns", []):
        if isinstance(item, dict):
            concern_text = item.get("text", "")
            if not concern_text:
                continue
            concerns.append(EditorialConcern(
                text=concern_text,
                quoted_passage=item.get("quoted_passage", ""),
                clearly_wrong=bool(item.get("clearly_wrong", False)),
            ))
        elif isinstance(item, str) and item.strip():
            concerns.append(EditorialConcern(text=item.strip()))

    # Parse next_scene
    next_scene = data.get("next_scene", "")
    if not isinstance(next_scene, str):
        next_scene = ""

    return EditorialNotes(
        protect=protect,
        concerns=concerns,
        next_scene=next_scene.strip(),
        raw_response=raw,
    )
