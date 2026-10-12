"""Fantasy prompt builders using the owner-bound model call service."""

from __future__ import annotations

from typing import Any

from tinyassets.providers.model_call import text_sync

__all__ = [
    "text_sync",
    "call_for_plan",
    "call_for_draft",
    "call_for_extraction",
]


# ---------------------------------------------------------------------------
# Prompt-context formatters
# ---------------------------------------------------------------------------


def _format_retrieved_context(retrieved: dict[str, Any]) -> str:
    """Format retrieval router results into prompt sections.

    Extracts facts, relationships, prose chunks, and community summaries
    from the RetrievalRouter's output. Returns empty string if no
    useful content.
    """
    if not retrieved:
        return ""

    parts: list[str] = []

    # Facts from KG
    facts = retrieved.get("facts", [])
    if facts:
        fact_lines = []
        for f in facts[:15]:
            if isinstance(f, dict):
                text = f.get("text", f.get("content", ""))
                if text:
                    fact_lines.append(f"- {text}")
        if fact_lines:
            parts.append("## Known Facts\n\n" + "\n".join(fact_lines))

    # Relationships from KG
    rels = retrieved.get("relationships", [])
    if rels:
        rel_lines = []
        for r in rels[:10]:
            if isinstance(r, dict):
                src = r.get("source", "")
                tgt = r.get("target", "")
                rtype = r.get("relation_type", "related to")
                if src and tgt:
                    rel_lines.append(f"- {src} {rtype} {tgt}")
        if rel_lines:
            parts.append("## Relationships\n\n" + "\n".join(rel_lines))

    # Prose chunks from vector search
    chunks = retrieved.get("prose_chunks", [])
    if chunks:
        chunk_texts = []
        for c in chunks[:3]:
            if isinstance(c, dict):
                text = c.get("text", "")
                if text:
                    chunk_texts.append(text[:500])
            elif isinstance(c, str):
                chunk_texts.append(c[:500])
        if chunk_texts:
            parts.append(
                "## Relevant Passages\n\n"
                + "\n\n---\n\n".join(chunk_texts)
            )

    # Community summaries from RAPTOR
    summaries = retrieved.get("community_summaries", [])
    if summaries:
        summary_lines = [s[:300] for s in summaries[:3] if isinstance(s, str)]
        if summary_lines:
            parts.append(
                "## World Summaries\n\n" + "\n\n".join(summary_lines)
            )

    if not parts:
        return ""

    return "# Retrieved Context\n\n" + "\n\n".join(parts) + "\n\n"


def _format_memory_context(memory_ctx: dict[str, Any]) -> str:
    """Format memory context fields into prompt sections.

    Extracts useful fields (characters, facts, style rules, recent
    summaries) and formats them as markdown for prompt injection.
    Returns empty string if no useful content is available.
    """
    if not memory_ctx:
        return ""

    parts: list[str] = []

    # Active characters
    chars = memory_ctx.get("active_characters", {})
    if chars and isinstance(chars, dict):
        char_list = []
        for name, info in list(chars.items())[:10]:
            if isinstance(info, dict):
                desc = info.get("description", info.get("role", ""))
                char_list.append(f"- **{name}**: {desc}" if desc else f"- **{name}**")
            elif isinstance(info, str):
                char_list.append(f"- **{name}**: {info}")
        if char_list:
            parts.append("## Characters\n\n" + "\n".join(char_list))

    # Recent scene summaries
    summaries = memory_ctx.get("recent_summaries", [])
    if summaries:
        summary_lines = []
        for s in summaries[:5]:
            if isinstance(s, dict):
                ch = s.get("ch", "?")
                sc = s.get("sc", "?")
                text = s.get("summary", "")
                if text:
                    summary_lines.append(f"- Ch{ch} Sc{sc}: {text}")
        if summary_lines:
            parts.append("## Recent Events\n\n" + "\n".join(summary_lines))

    # Key facts
    facts = memory_ctx.get("facts", [])
    if facts:
        fact_lines = []
        for f in facts[:10]:
            if isinstance(f, dict):
                text = f.get("content", f.get("text", f.get("fact", "")))
                if text:
                    fact_lines.append(f"- {text}")
            elif isinstance(f, str):
                fact_lines.append(f"- {f}")
        if fact_lines:
            parts.append("## Key Facts\n\n" + "\n".join(fact_lines))

    # Style rules
    rules = memory_ctx.get("style_rules", [])
    if rules:
        rule_lines = []
        for r in rules[:5]:
            if isinstance(r, dict):
                text = r.get("rule", r.get("text", ""))
                if text:
                    rule_lines.append(f"- {text}")
            elif isinstance(r, str):
                rule_lines.append(f"- {r}")
        if rule_lines:
            parts.append("## Style Notes\n\n" + "\n".join(rule_lines))

    if not parts:
        return ""

    return "# Story Memory\n\n" + "\n\n".join(parts) + "\n\n"


# ---------------------------------------------------------------------------
# Owner-bound creative prompt builders
# ---------------------------------------------------------------------------


def call_for_plan(
    orient_result: dict[str, Any],
    *,
    writer_context: str = "",
) -> str:
    """Call provider to generate a scene plan.

    Raises if no owner-bound model is available.
    """
    system = (
        "You are a fiction planning assistant. Generate a beat sheet for a scene. "
        "Return a JSON object with an 'alternatives' array. Each alternative has: "
        "beats (array of {beat_number, description, tension}), done_when (string), "
        "promise_resolutions (array), estimated_word_count (int). "
        "Generate 3-5 alternative beat sheets."
    )

    warnings_text = ""
    overdue = orient_result.get("overdue_promises", [])
    if overdue:
        warnings_text = "\n\nWarnings to address:\n" + "\n".join(
            f"- {p.get('text', str(p))}" for p in overdue
        )

    pacing = orient_result.get("pacing_flags", [])
    if pacing:
        warnings_text += "\n\nPacing notes:\n" + "\n".join(
            f"- {f.get('text', str(f))}" for f in pacing
        )

    context_section = writer_context
    if not context_section:
        # Backwards-compatible fallback for direct callers that still pass
        # pre-assembled prompt context through orient_result.
        canon_context = orient_result.get("canon_context", "")
        canon_section = ""
        if canon_context:
            canon_section = f"\n\n# World Context\n\n{canon_context}\n"

        memory_section = _format_memory_context(orient_result.get("memory_context", {}))
        retrieved_section = _format_retrieved_context(orient_result.get("retrieved_context", {}))

        constraints_section = ""
        forcing = orient_result.get("forcing_constraints", [])
        if forcing:
            constraint_lines = "\n".join(f"- {c}" for c in forcing[:10])
            constraints_section = (
                f"\n\n# World Constraints\n\n{constraint_lines}\n"
            )
        context_section = (
            f"{canon_section}"
            f"{retrieved_section}"
            f"{memory_section}"
            f"{constraints_section}"
        )

    prompt = (
        f"Scene context: {orient_result.get('scene_id', 'unknown')}\n"
        f"Arc position: {orient_result.get('arc_position', 'rising_action')}\n"
        f"{context_section}"
        f"{warnings_text}\n\n"
        "Generate 3-5 alternative beat sheets for this scene."
    )

    result = text_sync(
        prompt,
        system,
        role="writer",
    )
    return result


def call_for_draft(
    plan_output: dict[str, Any],
    orient_result: dict[str, Any],
    recent_prose: str = "",
    revision_feedback: dict[str, Any] | None = None,
    *,
    writer_context: str = "",
) -> str:
    """Call provider to generate prose from a beat sheet.

    Raises if no owner-bound model is available.
    """
    system = (
        "You are a skilled fantasy author. Write vivid, immersive prose for the "
        "following scene beats. Use third-person limited POV, past tense. "
        "Target 800-1500 words. Include sensory details, character interiority, "
        "and natural dialogue where appropriate."
    )

    beats_text = "\n".join(
        f"Beat {b.get('beat_number', i+1)}: {b.get('description', '')}"
        for i, b in enumerate(plan_output.get("beats", []))
    )

    prompt = f"Beat sheet:\n{beats_text}\n\n"

    if plan_output.get("done_when"):
        prompt += f"Scene is complete when: {plan_output['done_when']}\n\n"

    if writer_context:
        prompt += writer_context
        prompt += "Write the scene prose now."
        result = text_sync(
            prompt,
            system,
            role="writer",
        )
        return result

    # Include canon context so prose is grounded in the universe
    canon_context = orient_result.get("canon_context", "")
    if canon_context:
        prompt += f"# World Context\n\n{canon_context}\n\n"

    # Include memory context (voice refs, style rules)
    memory_section = _format_memory_context(orient_result.get("memory_context", {}))
    if memory_section:
        prompt += memory_section

    # Include retrieved context (KG facts, relationships, vector matches)
    retrieved_section = _format_retrieved_context(orient_result.get("retrieved_context", {}))
    if retrieved_section:
        prompt += retrieved_section

    if recent_prose:
        # Include last 2000 chars for continuity (roughly a full scene)
        prompt += f"Previous prose (for continuity):\n...{recent_prose[-2000:]}\n\n"

    if revision_feedback:
        prompt += "REVISION REQUESTED. Previous draft had these issues:\n"

        # Structural issues
        warnings = revision_feedback.get("warnings", [])
        if warnings:
            prompt += "Structural problems:\n"
            for w in warnings[:5]:
                prompt += f"- {w}\n"
            prompt += "\n"

        # Editorial feedback
        editorial = revision_feedback.get("editorial_notes")
        if editorial and isinstance(editorial, dict):
            concerns = editorial.get("concerns", [])
            if concerns:
                prompt += "Editorial concerns:\n"
                for c in concerns:
                    if isinstance(c, dict):
                        label = "ERROR" if c.get("clearly_wrong") else "concern"
                        prompt += f"- [{label}] {c.get('text', '')}"
                        quote = c.get("quoted_passage", "")
                        if quote:
                            prompt += f' — "{quote}"'
                        prompt += "\n"
                prompt += "\n"
            protects = editorial.get("protect", [])
            if protects:
                prompt += "Keep these strengths:\n"
                for p in protects:
                    prompt += f"- {p}\n"
                prompt += "\n"

        # Style observations
        style_obs = revision_feedback.get("style_observations", [])
        if style_obs:
            prompt += "Style feedback:\n"
            for obs in style_obs[:5]:
                if isinstance(obs, dict):
                    dim = obs.get("dimension", "")
                    note = obs.get("observation", "")
                    prompt += f"- {dim}: {note}\n" if dim else f"- {note}\n"
                elif isinstance(obs, str):
                    prompt += f"- {obs}\n"
            prompt += "\n"

        prompt += "Address these issues in your revision.\n\n"

    prompt += "Write the scene prose now."

    result = text_sync(
        prompt,
        system,
        role="writer",
    )
    return result


def call_for_extraction(prose: str, pov_character: str | None = None) -> str:
    """Call provider to extract facts from prose.

    Raises if no owner-bound model is available.
    """
    from domains.fantasy_daemon.phases.fact_extraction import (
        FACT_EXTRACTION_SYSTEM,
        build_extraction_prompt,
    )

    prompt = build_extraction_prompt(prose, pov_character)

    result = text_sync(
        prompt,
        FACT_EXTRACTION_SYSTEM,
        role="extract",
    )
    return result
