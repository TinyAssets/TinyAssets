"""Writer-tools helper -- called by plan and draft nodes (scene graph) for context retrieval."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from tinyassets.ingestion.canon_io import iter_canon_files
from tinyassets.notes import (
    format_notes_for_context,
    get_unread_notes_for_orient,
    mark_notes_read,
)
from tinyassets.retrieval.agentic_search import assemble_phase_search_context
from tinyassets.utils.json_parsing import parse_llm_json

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class WriterTool:
    """A local context tool available to the writer."""

    name: str
    title: str
    description: str
    phases: tuple[str, ...]
    renderer: Callable[[dict[str, Any]], str]


def select_and_run_writer_tools(
    phase: str,
    state: dict[str, Any],
) -> tuple[str, list[str]]:
    """Select and execute the writer tools for a phase.

    Returns a markdown context block plus the tool names that produced output.
    """
    available = _available_tools(phase)
    if not available:
        return "", []

    phase_state = dict(state)
    phase_state["_writer_phase"] = phase
    selected_names = _select_tool_names(phase, phase_state, available)
    sections: list[str] = []
    used: list[str] = []

    for tool in available:
        if tool.name not in selected_names:
            continue
        try:
            rendered = tool.renderer(phase_state).strip()
        except Exception:
            logger.warning("Writer tool %s failed", tool.name, exc_info=True)
            continue
        if not rendered:
            continue
        sections.append(f"## {tool.title}\n\n{rendered}")
        used.append(tool.name)

    if not sections:
        return "", used
    return "# Context Tools\n\n" + "\n\n".join(sections) + "\n\n", used


def _available_tools(phase: str) -> list[WriterTool]:
    return [tool for tool in _TOOLS if phase in tool.phases]


def _select_tool_names(
    phase: str,
    state: dict[str, Any],
    tools: list[WriterTool],
) -> list[str]:
    """Ask the selector which tools to use, with deterministic fallback."""
    from domains.fantasy_daemon.phases import _provider_stub

    allowed = {tool.name for tool in tools}
    defaults = _default_tool_names(phase, state, tools)
    plan_output = state.get("plan_output") or {}
    orient_result = state.get("orient_result") or {}
    scene_id = plan_output.get("scene_id") or orient_result.get("scene_id") or "unknown"
    warnings = orient_result.get("warnings", [])
    has_notes = bool(state.get("_universe_path"))
    is_revision = bool(state.get("_revision_feedback"))

    tool_lines = "\n".join(
        f'- "{tool.name}": {tool.description}'
        for tool in tools
    )
    system = (
        "You choose which local context tools a fiction writer should query "
        "before generating output. Return valid JSON as either an array of "
        'tool names or an object with a "tools" array. Choose at most 4 tools. '
        "Only use names from the provided list. No prose."
    )
    prompt = (
        f"Phase: {phase}\n"
        f"Scene: {scene_id}\n"
        f"Warnings available: {len(warnings)}\n"
        f"Universe path present: {has_notes}\n"
        f"Revision mode: {is_revision}\n\n"
        "Available tools:\n"
        f"{tool_lines}\n\n"
        "Choose only tools likely to materially improve this phase."
    )
    raw = _provider_stub.text_sync(
        prompt,
        system,
        role="extract",
    )
    parsed = _parse_tool_selection(raw, allowed)
    return parsed or defaults


def _parse_tool_selection(raw: str, allowed: set[str]) -> list[str]:
    """Parse a selector response into a de-duplicated ordered tool list."""
    data = parse_llm_json(raw, fallback=None)
    if data is None:
        return []

    if isinstance(data, dict):
        data = data.get("tools", [])
    if not isinstance(data, list):
        return []

    selected: list[str] = []
    for item in data:
        if not isinstance(item, str):
            continue
        name = item.strip()
        if name in allowed and name not in selected:
            selected.append(name)
        if len(selected) >= 4:
            break
    return selected


def _default_tool_names(
    phase: str,
    state: dict[str, Any],
    tools: list[WriterTool],
) -> list[str]:
    """Reasonable autonomous defaults when selection is unavailable."""
    preferred: list[str]
    if phase == "plan":
        preferred = [
            "story_search",
            "world_constraints",
        ]
    else:
        preferred = [
            "story_search",
            "recent_prose",
            "revision_feedback",
        ]

    available = {tool.name for tool in tools}
    selected: list[str] = []
    for name in preferred:
        if name not in available:
            continue
        if name == "story_search":
            phase_name = str(state.get("_writer_phase", phase))
            if not _get_search_context(state, phase_name):
                continue
        if name == "notes" and not state.get("_universe_path"):
            continue
        if name == "recent_prose" and not state.get("recent_prose"):
            continue
        if name == "revision_feedback" and not state.get("_revision_feedback"):
            continue
        if name == "world_constraints" and not state.get("_forcing_constraints"):
            continue
        selected.append(name)
        if len(selected) >= 4:
            break
    return selected


def _render_story_search(state: dict[str, Any]) -> str:
    phase = str(state.get("_writer_phase", "plan"))
    search = _get_search_context(state, phase)
    if not search:
        return ""

    parts: list[str] = []

    sources = search.get("sources", [])
    if sources:
        parts.append("Routed sources: " + ", ".join(sources[:8]))

    soul_section = _render_universe_soul_from_context(search.get("universe_soul", {}))
    if soul_section:
        parts.append(soul_section)

    world_section = _render_world_state_from_context(search.get("world_state", {}))
    if world_section:
        parts.append(world_section)

    notes_section = _render_notes(state)
    if notes_section:
        parts.append("Unread Notes:\n" + notes_section)

    memory_section = _render_story_memory_from_context(search.get("memory_context", {}))
    if memory_section:
        parts.append(memory_section)

    retrieval_section = _render_retrieval_context_from_context(
        search.get("retrieved_context", {})
    )
    if retrieval_section:
        parts.append(retrieval_section)

    canon_section = _render_canon_files(state)
    if canon_section:
        parts.append("Canon Files:\n\n" + canon_section)

    return "\n\n".join(parts)


def _render_universe_soul_from_context(soul_ctx: dict[str, Any]) -> str:
    if not soul_ctx:
        return ""

    parts: list[str] = []
    version_id = str(soul_ctx.get("version_id") or "")
    digest = str(soul_ctx.get("content_sha256") or "")
    if version_id or digest:
        label = version_id or "soul.md"
        hash_part = f", sha256={digest[:12]}" if digest else ""
        parts.append(f"Universe soul lens pinned to {label}{hash_part}.")

    content = str(soul_ctx.get("content") or "").strip()
    if content:
        parts.append("Pinned soul.md:\n" + content)
    else:
        purpose = str(soul_ctx.get("purpose") or "").strip()
        if purpose:
            parts.append(f"Purpose: {purpose}")

        why = str(soul_ctx.get("why") or "").strip()
        if why:
            parts.append(f"Why: {why}")

        hard_lines = _string_list(soul_ctx.get("hard_lines"))
        if hard_lines:
            parts.append(
                "Hard lines:\n"
                + "\n".join(f"- {item}" for item in hard_lines[:5])
            )

        soft_preferences = _string_list(soul_ctx.get("soft_preferences"))
        if soft_preferences:
            parts.append(
                "Soft preferences:\n"
                + "\n".join(f"- {item}" for item in soft_preferences[:5])
            )

    boundary = str(soul_ctx.get("identity_boundary") or "").strip()
    if boundary:
        parts.append(boundary)

    return "\n".join(parts)


def _render_world_state(state: dict[str, Any]) -> str:
    orient = state.get("orient_result", {}) or {}
    return _render_world_state_from_context(orient.get("world_state", {}) or {})


def _render_world_state_from_context(world_state: dict[str, Any]) -> str:
    if not world_state:
        return ""

    parts: list[str] = []
    chapter = world_state.get("chapter_number")
    scene = world_state.get("scene_number")
    if chapter is not None and scene is not None:
        parts.append(f"Current position: chapter {chapter}, scene {scene}.")

    avg_words = world_state.get("chapter_avg_words")
    if avg_words:
        parts.append(f"Typical scene length so far: about {avg_words} words.")

    active_promises = world_state.get("active_promises", []) or []
    if active_promises:
        promise_lines = [
            f"- {p.get('text', '')}"
            for p in active_promises[:5]
            if isinstance(p, dict) and p.get("text")
        ]
        if promise_lines:
            parts.append("Active promises:\n" + "\n".join(promise_lines))

    # Characters intentionally NOT rendered from world_state — they live in
    # memory_context.active_characters and are rendered by
    # _render_story_memory_from_context. Rendering them from both places
    # duplicated token cost downstream (BUG-024).

    recent_scenes = world_state.get("recent_scenes", []) or []
    if recent_scenes:
        scene_lines = []
        for item in recent_scenes[:3]:
            if not isinstance(item, dict):
                continue
            sid = item.get("scene_id") or "scene"
            summary = item.get("summary", "")
            if summary:
                scene_lines.append(f"- {sid}: {summary[:180]}")
        if scene_lines:
            parts.append("Recent committed scenes:\n" + "\n".join(scene_lines))

    return "\n\n".join(parts)


def _render_notes(state: dict[str, Any]) -> str:
    universe_path = state.get("_universe_path")
    if not universe_path:
        return ""
    notes = get_unread_notes_for_orient(universe_path)
    if not notes:
        return ""
    context = format_notes_for_context(notes)
    mark_notes_read(universe_path, [note.id for note in notes])
    return context[:2000]


def _render_story_memory(state: dict[str, Any]) -> str:
    return _render_story_memory_from_context(state.get("memory_context", {}) or {})


def _render_story_memory_from_context(memory_ctx: dict[str, Any]) -> str:
    if not memory_ctx:
        return ""

    parts: list[str] = []

    chars = memory_ctx.get("active_characters", {})
    if chars and isinstance(chars, dict):
        char_lines = []
        for name, info in list(chars.items())[:8]:
            if isinstance(info, dict):
                display_name = info.get("name") or name
                location = info.get("location")
                emotion = info.get("emotional_state")
                details = [
                    part for part in (location, emotion)
                    if part and part != "unknown" and part != "neutral"
                ]
                if details:
                    char_lines.append(f"- {display_name}: {', '.join(details)}")
                else:
                    desc = info.get("description", info.get("role", ""))
                    char_lines.append(
                        f"- {display_name}: {desc}" if desc else f"- {display_name}"
                    )
            elif isinstance(info, str):
                char_lines.append(f"- {name}: {info}")
        if char_lines:
            parts.append("Active characters:\n" + "\n".join(char_lines))

    summaries = memory_ctx.get("recent_summaries", [])
    if summaries:
        summary_lines = []
        for item in summaries[:4]:
            if not isinstance(item, dict):
                continue
            text = item.get("summary", "")
            if text:
                summary_lines.append(
                    f"- Ch{item.get('ch', '?')} Sc{item.get('sc', '?')}: {text}"
                )
        if summary_lines:
            parts.append("Recent summaries:\n" + "\n".join(summary_lines))

    facts = memory_ctx.get("facts", [])
    if facts:
        fact_lines = []
        for item in facts[:8]:
            if isinstance(item, dict):
                text = item.get("content", item.get("text", item.get("fact", "")))
                if text:
                    fact_lines.append(f"- {text}")
            elif isinstance(item, str):
                fact_lines.append(f"- {item}")
        if fact_lines:
            parts.append("Memory facts:\n" + "\n".join(fact_lines))

    style_rules = memory_ctx.get("style_rules", [])
    if style_rules:
        rule_lines = []
        for item in style_rules[:5]:
            if isinstance(item, dict):
                text = item.get("rule", item.get("text", ""))
                if text:
                    rule_lines.append(f"- {text}")
            elif isinstance(item, str):
                rule_lines.append(f"- {item}")
        if rule_lines:
            parts.append("Style rules:\n" + "\n".join(rule_lines))

    return "\n\n".join(parts)


def _render_retrieval_context(state: dict[str, Any]) -> str:
    return _render_retrieval_context_from_context(
        state.get("retrieved_context", {}) or {}
    )


def _render_retrieval_context_from_context(retrieved: dict[str, Any]) -> str:
    if not retrieved:
        return ""

    parts: list[str] = []

    facts = retrieved.get("facts", [])
    if facts:
        fact_lines = []
        for item in facts[:10]:
            if isinstance(item, dict):
                text = item.get("text", item.get("content", ""))
                if text:
                    fact_lines.append(f"- {text}")
        if fact_lines:
            parts.append("Retrieved facts:\n" + "\n".join(fact_lines))

    relationships = retrieved.get("relationships", [])
    if relationships:
        rel_lines = []
        for item in relationships[:8]:
            if not isinstance(item, dict):
                continue
            source = item.get("source", "")
            relation = item.get("relation_type", "related to")
            target = item.get("target", "")
            if source and target:
                rel_lines.append(f"- {source} {relation} {target}")
        if rel_lines:
            parts.append("Relationships:\n" + "\n".join(rel_lines))

    passages = retrieved.get("prose_chunks", [])
    if passages:
        chunk_lines = []
        for item in passages[:2]:
            if isinstance(item, dict):
                text = item.get("text", "")
            else:
                text = str(item)
            text = text.strip()
            if text:
                chunk_lines.append(text[:400])
        if chunk_lines:
            parts.append("Relevant passages:\n" + "\n\n---\n\n".join(chunk_lines))

    summaries = retrieved.get("community_summaries", [])
    if summaries:
        summary_lines = [summary[:250] for summary in summaries[:2] if isinstance(summary, str)]
        if summary_lines:
            parts.append("World summaries:\n" + "\n\n".join(summary_lines))

    return "\n\n".join(parts)


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _get_search_context(state: dict[str, Any], phase: str) -> dict[str, Any]:
    search = state.get("search_context")
    if isinstance(search, dict) and search.get("phase") == phase:
        return search
    try:
        search = assemble_phase_search_context(state, phase)
    except Exception:
        logger.warning("Failed to assemble search context for %s", phase, exc_info=True)
        return {}
    state["search_context"] = search
    return search


def _render_canon_files(state: dict[str, Any]) -> str:
    universe_path = state.get("_universe_path", "")
    if not universe_path:
        return ""

    canon_dir = Path(universe_path) / "canon"
    if not canon_dir.is_dir():
        return ""

    parts: list[str] = []
    total_chars = 0
    max_per_file = 1200
    max_total = 4000

    # Enumeration is routed through ``iter_canon_files`` so each entry is
    # resolved + contained before stat/read; a symlinked ``.md`` escaping
    # canon_dir is skipped, not read.
    for path in iter_canon_files(canon_dir, suffix=".md", include_hidden=False):
        try:
            content = path.read_text(encoding="utf-8")[:max_per_file].strip()
        except OSError:
            logger.warning("Failed to read canon file %s", path, exc_info=True)
            continue
        if not content:
            continue
        section = f"### {path.stem.replace('_', ' ').title()}\n\n{content}"
        if total_chars + len(section) > max_total:
            break
        parts.append(section)
        total_chars += len(section)

    return "\n\n".join(parts)


def _render_recent_prose(state: dict[str, Any]) -> str:
    recent = (state.get("recent_prose") or "").strip()
    if not recent:
        return ""
    return recent[-1500:]


def _render_revision_feedback(state: dict[str, Any]) -> str:
    feedback = state.get("_revision_feedback")
    if not isinstance(feedback, dict):
        return ""

    parts: list[str] = []

    warnings = feedback.get("warnings", [])
    if warnings:
        warning_lines = [f"- {item}" for item in warnings[:5]]
        parts.append("Structural problems:\n" + "\n".join(warning_lines))

    editorial = feedback.get("editorial_notes")
    if editorial and isinstance(editorial, dict):
        concerns = editorial.get("concerns", [])
        if concerns:
            concern_lines = []
            for item in concerns[:5]:
                if not isinstance(item, dict):
                    continue
                label = "ERROR" if item.get("clearly_wrong") else "concern"
                line = f"- [{label}] {item.get('text', '')}"
                quote = item.get("quoted_passage", "")
                if quote:
                    line += f' -- "{quote}"'
                concern_lines.append(line)
            if concern_lines:
                parts.append("Editorial concerns:\n" + "\n".join(concern_lines))

        protects = editorial.get("protect", [])
        if protects:
            protect_lines = [f"- {item}" for item in protects[:5] if isinstance(item, str)]
            if protect_lines:
                parts.append("Keep these strengths:\n" + "\n".join(protect_lines))

    style_obs = feedback.get("style_observations", [])
    if style_obs:
        obs_lines = []
        for item in style_obs[:5]:
            if isinstance(item, dict):
                dim = item.get("dimension", "")
                note = item.get("observation", "")
                obs_lines.append(f"- {dim}: {note}" if dim else f"- {note}")
            elif isinstance(item, str):
                obs_lines.append(f"- {item}")
        if obs_lines:
            parts.append("Style feedback:\n" + "\n".join(obs_lines))

    return "\n\n".join(parts)


def _render_world_constraints(state: dict[str, Any]) -> str:
    forcing = state.get("_forcing_constraints", [])
    if not forcing:
        return ""
    lines = [f"- {item}" for item in forcing[:10] if isinstance(item, str)]
    return "\n".join(lines)


_TOOLS: tuple[WriterTool, ...] = (
    WriterTool(
        name="story_search",
        title="Story Search",
        description=(
            "Unified routed search across world state, notes, memory, KG, "
            "RAPTOR summaries, vector passages, and canon files."
        ),
        phases=("plan", "draft"),
        renderer=_render_story_search,
    ),
    WriterTool(
        name="recent_prose",
        title="Recent Prose",
        description="The previous scene prose for continuity of action and voice.",
        phases=("draft",),
        renderer=_render_recent_prose,
    ),
    WriterTool(
        name="revision_feedback",
        title="Revision Feedback",
        description="Commit warnings and editorial notes from the prior draft attempt.",
        phases=("draft",),
        renderer=_render_revision_feedback,
    ),
    WriterTool(
        name="world_constraints",
        title="World Constraints",
        description="Forcing constraints synthesized for plan generation.",
        phases=("plan",),
        renderer=_render_world_constraints,
    ),
)
