"""Phase-aware agentic search policy.

Combines phase-specific memory assembly with retrieval routing so the writer
sees one coherent search surface instead of separate memory and retrieval
bundles.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import asdict
from pathlib import Path
from typing import Any, Callable

from tinyassets.knowledge.tag_matrix import TagMatrixQuery
from tinyassets.memory.scoping import MemoryScope

logger = logging.getLogger(__name__)


def _scope_from_state(state: dict[str, Any]) -> MemoryScope | None:
    """Derive a ``MemoryScope`` from graph state.

    Priority: ``state["scope"]`` if it's a ``MemoryScope``, else build
    one from ``state["universe_id"]``. Returns ``None`` when neither is
    present — the caller must decide whether to skip retrieval or fall
    back (Stage 1 chooses to skip + log, not raise, so nodes stay
    crash-free).
    """
    maybe_scope = state.get("scope")
    if isinstance(maybe_scope, MemoryScope):
        return maybe_scope
    universe_id = state.get("universe_id") or state.get("_universe_id")
    if universe_id:
        return MemoryScope(universe_id=str(universe_id))
    return None


def assemble_phase_search_context(
    state: dict[str, Any],
    phase: str,
) -> dict[str, Any]:
    """Build the unified search surface for a graph phase."""
    memory_context = assemble_memory_context(state, phase)
    universe_soul = assemble_soul_lens_context(state)
    prior_retrieved = state.get("retrieved_context", {}) or {}
    phase_retrieved = run_phase_retrieval(
        state,
        phase,
        memory_context=memory_context,
    )
    retrieved_context = _merge_retrieved_contexts(prior_retrieved, phase_retrieved)

    orient_result = state.get("orient_result", {}) or {}
    world_state = (
        orient_result.get("world_state")
        or memory_context.get("world_state", {})
    )
    active_promises = (
        memory_context.get("active_promises")
        or orient_result.get("active_promises", [])
        or world_state.get("active_promises", [])
    )
    facts = _merge_fact_lists(
        memory_context.get("facts", []),
        retrieved_context.get("facts", []),
        retrieved_context.get("canon_facts", []),
    )

    sources = _dedupe_strings(
        [
            _soul_source(universe_soul),
            *(retrieved_context.get("sources", []) or []),
            f"memory:{phase}" if memory_context else "",
            "world_state" if world_state else "",
        ]
    )

    return {
        "phase": phase,
        "universe_soul": universe_soul,
        "memory_context": memory_context,
        "retrieved_context": retrieved_context,
        "world_state": world_state,
        "active_promises": active_promises,
        "recent_summaries": memory_context.get("recent_summaries", []),
        "recent_reflections": memory_context.get("recent_reflections", []),
        "style_rules": memory_context.get("style_rules", []),
        "facts": facts,
        "relationships": retrieved_context.get("relationships", []),
        "prose_chunks": retrieved_context.get("prose_chunks", []),
        "community_summaries": retrieved_context.get("community_summaries", []),
        "warnings": _merge_warning_lists(
            orient_result.get("warnings", []),
            retrieved_context.get("warnings", []),
        ),
        "sources": sources,
        "token_count": retrieved_context.get("token_count", 0),
    }


def assemble_soul_lens_context(state: dict[str, Any]) -> dict[str, Any]:
    """Read the universe's pinned soul profile for prompt/context assembly."""
    universe_dir = _universe_dir_from_state(state)
    if universe_dir is None:
        return {}

    try:
        from tinyassets.universe_soul import read_pinned_universe_soul

        pinned = read_pinned_universe_soul(universe_dir)
    except Exception as exc:
        logger.warning("Failed to read command center soul lens: %s", exc)
        return {}

    if pinned is None:
        return {}
    return dict(pinned.context())


def assemble_memory_context(state: dict[str, Any], phase: str) -> dict[str, Any]:
    """Call MemoryManager.assemble_context if available."""
    from tinyassets import runtime_singletons as runtime

    mgr = runtime.memory_manager
    if mgr is None:
        return state.get("memory_context", {})
    try:
        return dict(mgr.assemble_context(phase, state))
    except Exception as exc:
        logger.warning("MemoryManager.assemble_context(%s) failed: %s", phase, exc)
        return state.get("memory_context", {})


def _universe_dir_from_state(state: dict[str, Any]) -> Path | None:
    explicit_path = state.get("_universe_path") or state.get("universe_path")
    if explicit_path:
        return Path(str(explicit_path))

    base_path = state.get("_base_path") or state.get("base_path")
    universe_id = state.get("universe_id") or state.get("_universe_id")
    if base_path and universe_id:
        return Path(str(base_path)) / str(universe_id)
    return None


def _soul_source(universe_soul: dict[str, Any]) -> str:
    version_id = str(universe_soul.get("version_id", "") or "")
    return f"soul:{version_id}" if version_id else ""


def run_phase_retrieval(
    state: dict[str, Any],
    phase: str,
    *,
    memory_context: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Route a phase query across the retrieval backends."""
    query = build_phase_query(state, phase, memory_context=memory_context)
    if not query:
        return {}

    scope = _scope_from_state(state)
    if scope is None:
        logger.warning(
            "run_phase_retrieval: no scope in state "
            "(missing universe_id / scope); skipping retrieval for phase %s",
            phase,
        )
        return {}
    tag_query = _tag_query_from_state(state)

    kg = None
    owns_kg = False
    try:
        from tinyassets import runtime_singletons as runtime
        from tinyassets.retrieval.router import RetrievalRouter

        kg = runtime.knowledge_graph
        if kg is None:
            from domains.fantasy_daemon.phases._paths import resolve_kg_path
            from tinyassets.knowledge.knowledge_graph import KnowledgeGraph

            kg_path = resolve_kg_path(state)
            if kg_path:
                kg = KnowledgeGraph(kg_path)
                owns_kg = True

        vector_store = runtime.vector_store or state.get("_vector_store")
        raptor_tree = runtime.raptor_tree or state.get("_raptor_tree")
        embed_fn = runtime.embed_fn or state.get("_embed_fn")
        provider_call = _build_provider_call()

        router = RetrievalRouter(
            kg=kg,
            vector_store=vector_store,
            raptor_tree=raptor_tree,
            provider_call=provider_call,
            embed_fn=embed_fn,
        )

        orient_result = state.get("orient_result", {}) or {}
        coro = router.query(
            query=query,
            phase=phase,
            scope=scope,
            access_tier=int(orient_result.get("access_tier", 0) or 0),
            pov_character=orient_result.get("pov_character"),
            chapter_number=state.get("chapter_number"),
            tag_query=tag_query,
        )

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None

        if loop is not None and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                result = pool.submit(asyncio.run, coro).result()
        else:
            result = asyncio.run(coro)

        facts = [asdict(fact) for fact in result.facts]
        return {
            "facts": facts,
            "canon_facts": facts,
            "relationships": result.relationships,
            "prose_chunks": result.prose_chunks,
            "community_summaries": result.community_summaries,
            "warnings": result.warnings,
            "sources": result.sources,
            "token_count": result.token_count,
        }
    except Exception as exc:
        logger.warning(
            "Agentic retrieval failed for phase %s, returning empty context: %s",
            phase,
            exc,
        )
        return {}
    finally:
        if owns_kg and kg is not None:
            try:
                kg.close()
            except Exception:
                logger.debug("Failed to close temporary knowledge graph", exc_info=True)


def _tag_query_from_state(state: dict[str, Any]) -> TagMatrixQuery | None:
    """Derive an optional tag-matrix filter from graph state."""
    raw = (
        state.get("tag_matrix")
        or state.get("tag_filter")
        or state.get("knowledge_tags")
    )
    if raw is None:
        return None
    if isinstance(raw, TagMatrixQuery):
        return raw
    if not isinstance(raw, dict):
        return None
    return TagMatrixQuery(
        domain_tags=raw.get("domain_tags") or raw.get("domains") or (),
        shape_tags=raw.get("shape_tags") or raw.get("shapes") or (),
        include_general=bool(raw.get("include_general", True)),
        include_commons=bool(raw.get("include_commons", True)),
        include_untagged=bool(raw.get("include_untagged", False)),
    )


def build_phase_query(
    state: dict[str, Any],
    phase: str,
    *,
    memory_context: dict[str, Any] | None = None,
    scene_id: str | None = None,
) -> str:
    """Construct a natural-language search query for a graph phase."""
    orient_result = state.get("orient_result", {}) or {}
    plan_output = state.get("plan_output") or {}
    memory_context = memory_context or {}
    scene_id = (
        scene_id
        or plan_output.get("scene_id")
        or orient_result.get("scene_id")
        or "unknown"
    )

    if phase == "orient":
        parts = [f"Relationships and open promises for scene {scene_id}."]

        overdue = orient_result.get("overdue_promises", [])
        if overdue:
            promise_names = [
                promise.get("text", "")
                for promise in overdue[:3]
                if isinstance(promise, dict) and promise.get("text")
            ]
            if promise_names:
                parts.append(f"Overdue promises: {', '.join(promise_names)}.")

        gaps = orient_result.get("character_gaps", [])
        if gaps:
            chars = [
                gap.get("character_id") or gap.get("name", "")
                for gap in gaps[:3]
                if isinstance(gap, dict)
            ]
            chars = [char for char in chars if char]
            if chars:
                parts.append(f"Character gaps: {', '.join(chars)}.")

        pov = orient_result.get("pov_character") or state.get("_pov_character")
        if pov:
            parts.append(f"POV character: {pov}.")
        return " ".join(parts)

    characters = _character_names(orient_result.get("characters", []))
    warnings = orient_result.get("warnings", [])

    if phase == "plan":
        parts = [f"Overall theme and global summary relevant to planning scene {scene_id}."]
        if characters:
            parts.append(f"Focus characters: {', '.join(characters[:5])}.")
        if warnings:
            warning_text = [
                item.get("text", "")
                for item in warnings[:3]
                if isinstance(item, dict) and item.get("text")
            ]
            if warning_text:
                parts.append(f"Warnings to account for: {' | '.join(warning_text)}.")
        promises = memory_context.get("active_promises", [])
        if promises:
            promise_text = [
                item.get("text", "")
                for item in promises[:3]
                if isinstance(item, dict) and item.get("text")
            ]
            if promise_text:
                parts.append(f"Open promises: {' | '.join(promise_text)}.")
        return " ".join(parts)

    if phase == "draft":
        parts = [
            (
                "Voice, atmosphere, prose continuity, and sensory details "
                f"for drafting scene {scene_id}."
            ),
        ]
        beats = plan_output.get("beats", [])
        beat_lines = [
            beat.get("description", "")
            for beat in beats[:3]
            if isinstance(beat, dict) and beat.get("description")
        ]
        if beat_lines:
            parts.append(f"Current beats: {' | '.join(beat_lines)}.")
        if characters:
            parts.append(f"Focus characters: {', '.join(characters[:5])}.")
        return " ".join(parts)

    if phase == "evaluate":
        parts = [f"Canon facts and world rules relevant to evaluating scene {scene_id}."]
        if characters:
            parts.append(f"Focus characters: {', '.join(characters[:5])}.")
        done_when = plan_output.get("done_when")
        if done_when:
            parts.append(f"Scene contract: {done_when}.")
        return " ".join(parts)

    return ""


def _build_provider_call() -> Callable[[str, str, str], Any] | None:
    """Return the async decomposition callable when real providers are enabled."""
    from tinyassets.providers import call as provider_call

    if provider_call.is_force_mock():
        return None

    async def _async_provider_call(prompt: str, system: str, role: str) -> str:
        return provider_call.call_provider(
            prompt,
            system,
            role=role,
            fallback_response="",
        )

    return _async_provider_call


def _character_names(characters: list[dict[str, Any]]) -> list[str]:
    names: list[str] = []
    for char in characters:
        if not isinstance(char, dict):
            continue
        name = char.get("name") or char.get("character_id") or char.get("id")
        if name and name not in names:
            names.append(name)
    return names


def _merge_retrieved_contexts(
    left: dict[str, Any],
    right: dict[str, Any],
) -> dict[str, Any]:
    """Merge prior and phase-specific retrieval into one routed result."""
    facts = _merge_fact_lists(
        left.get("facts", []),
        right.get("facts", []),
        left.get("canon_facts", []),
        right.get("canon_facts", []),
    )
    relationships = _merge_keyed_dict_lists(
        left.get("relationships", []),
        right.get("relationships", []),
        key_fn=lambda item: (
            item.get("source"),
            item.get("relation_type"),
            item.get("target"),
        ),
    )

    return {
        "facts": facts,
        "canon_facts": facts,
        "relationships": relationships,
        "prose_chunks": _dedupe_strings(
            [
                *(left.get("prose_chunks", []) or []),
                *(right.get("prose_chunks", []) or []),
            ]
        ),
        "community_summaries": _dedupe_strings(
            [
                *(left.get("community_summaries", []) or []),
                *(right.get("community_summaries", []) or []),
            ]
        ),
        "warnings": _merge_warning_lists(
            left.get("warnings", []),
            right.get("warnings", []),
        ),
        "sources": _dedupe_strings(
            [
                *(left.get("sources", []) or []),
                *(right.get("sources", []) or []),
            ]
        ),
        "token_count": int(left.get("token_count", 0) or 0)
        + int(right.get("token_count", 0) or 0),
    }


def _merge_fact_lists(*fact_lists: list[Any]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    for fact_list in fact_lists:
        for item in fact_list or []:
            if not isinstance(item, dict):
                continue
            fact_id = str(item.get("fact_id", "") or "")
            text = str(item.get("text", item.get("content", item.get("fact", ""))) or "")
            if not fact_id and not text:
                continue
            key = (fact_id, text)
            if key in seen:
                continue
            seen.add(key)
            normalized = dict(item)
            if text and "text" not in normalized:
                normalized["text"] = text
            merged.append(normalized)
    return merged


def _merge_keyed_dict_lists(
    *value_lists: list[Any],
    key_fn: Callable[[dict[str, Any]], Any],
) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    seen: set[Any] = set()

    for value_list in value_lists:
        for item in value_list or []:
            if not isinstance(item, dict):
                continue
            key = key_fn(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(dict(item))
    return merged


def _merge_warning_lists(*warning_lists: list[Any]) -> list[Any]:
    merged: list[Any] = []
    seen: set[str] = set()

    for warning_list in warning_lists:
        for item in warning_list or []:
            if isinstance(item, dict):
                text = str(item.get("text", "") or item)
                normalized = dict(item)
            else:
                text = str(item)
                normalized = item
            if not text or text in seen:
                continue
            seen.add(text)
            merged.append(normalized)
    return merged


def _dedupe_strings(values: list[Any]) -> list[str]:
    output: list[str] = []
    seen: set[str] = set()

    for value in values:
        if not isinstance(value, str):
            continue
        text = value.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        output.append(text)
    return output
