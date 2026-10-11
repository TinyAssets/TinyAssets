"""Commit node -- scene graph; entry after draft, decides accept/second_draft/revert verdict.

Runs structural evaluation, editorial reading, fact/entity extraction,
world state updates, and worldbuild signal generation. Returns a verdict
based on structural hard failures and editorial concerns.

Contract
--------
Input:  SceneState with ``draft_output`` populated.
Output: Partial SceneState with ``verdict``, ``commit_result``,
        ``second_draft_used``, and accumulated evidence lists.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from domains.fantasy_daemon.phases._provider_stub import call_for_extraction, call_provider
from domains.fantasy_daemon.phases.fact_extraction import (
    detect_promises,
    extract_facts_from_llm_response,
    extract_facts_regex,
)
from domains.fantasy_daemon.phases.world_state_db import (
    add_promise,
    connect,
    init_db,
    record_scene,
    store_fact,
    upsert_character,
)
from tinyassets.enrichment_signals import append_enrichment_signals
from tinyassets.evaluation.editorial import EditorialNotes, read_editorial
from tinyassets.evaluation.process import ProcessEvaluation, evaluate_scene_process
from tinyassets.evaluation.structural import StructuralEvaluator, StructuralResult
from tinyassets.ingestion.canon_io import iter_canon_files
from tinyassets.universe_files import write_data_path

logger = logging.getLogger(__name__)

# Module-level evaluator instance (stateless, safe to reuse).
_structural_evaluator = StructuralEvaluator()


def commit(state: dict[str, Any]) -> dict[str, Any]:
    """Evaluate the draft and return a verdict.

    1. Run structural evaluation (deterministic checks).
    2. Run editorial reader (different model from writer).
    3. Extract facts from prose (LLM + regex fallback).
    4. Detect narrative promises.
    5. Update world state database.
    6. Compute verdict: structural hard failure → revert,
       clearly_wrong editorial concern → second_draft, else accept.

    Parameters
    ----------
    state : SceneState
        Must contain ``draft_output`` from the draft node.

    Returns
    -------
    dict
        Partial state with:
        - ``verdict``: ``'accept'``, ``'second_draft'``, or ``'revert'``.
        - ``commit_result``: evaluation details.
        - ``editorial_notes``: editorial feedback (dict or None).
        - ``second_draft_used``: updated flag if this was a second draft.
        - ``extracted_facts``: facts found in the prose.
        - ``extracted_promises``: promises detected.
        - ``style_observations``: editorial observations.
        - ``quality_trace``: decision trace entry.
        - ``quality_debt``: any degraded-lane markers.
    """
    from domains.fantasy_daemon.phases._activity import activity_log, update_phase

    draft_output = state.get("draft_output") or {}
    scene_id = draft_output.get("scene_id", "unknown")
    prose = draft_output.get("prose", "")
    is_revision = draft_output.get("is_revision", False)
    second_draft_used = is_revision or state.get("second_draft_used", False)
    activity_log(state, f"Commit: evaluating {scene_id}")
    update_phase(state, "commit")

    # Short-circuit: if the draft node flagged provider failure, revert
    # immediately instead of running eval/extraction on empty prose.
    if draft_output.get("provider_failed"):
        activity_log(state, f"Commit: reverting {scene_id} - draft provider failed")
        commit_result = {
            "scene_id": scene_id,
            "overall_score": 0.0,
            "structural_checks": [],
            "warnings": ["Provider returned empty prose"],
            "provider_failed": True,
        }
        trace_entry = {
            "node": "commit",
            "scene_id": scene_id,
            "action": "revert_provider_failed",
            "verdict": "revert",
        }
        process_eval = evaluate_scene_process(
            state,
            pending_trace=[trace_entry],
            verdict="revert",
            second_draft_used=second_draft_used,
            commit_result=commit_result,
        )
        commit_result["process_evaluation"] = process_eval.to_dict()
        trace_entry["process_score"] = process_eval.aggregate_score
        trace_entry["process_failures"] = process_eval.failing_checks
        return {
            "verdict": "revert",
            "commit_result": commit_result,
            "editorial_notes": None,
            "second_draft_used": second_draft_used,
            "extracted_facts": [],
            "extracted_promises": [],
            "style_observations": [],
            "quality_trace": [trace_entry],
            "quality_debt": [],
        }

    from domains.fantasy_daemon.phases._paths import resolve_db_path
    db_path = resolve_db_path(state)

    # --- Memory context for evaluate phase ---
    memory_context = _assemble_memory(state, "evaluate")

    # --- 1. Structural evaluation (Tier 1) ---
    structural = _structural_evaluator.evaluate(state)

    # --- 2. Fact extraction ---
    facts_list = _extract_facts(prose, scene_id, state)

    # --- 3. Promise detection ---
    promises = detect_promises(prose, scene_id, state.get("chapter_number", 1))

    # --- 3b. Entity extraction → KG indexing ---
    _index_prose_entities(prose, scene_id, state)

    # --- 3c. Generate worldbuild signals ---
    worldbuild_signals = _generate_worldbuild_signals(facts_list, state)

    # --- 4. Update world state ---
    _update_world_state(
        db_path=db_path,
        scene_id=scene_id,
        state=state,
        facts_list=facts_list,
        promises=promises,
        word_count=draft_output.get("word_count", 0),
        prose=prose,
    )

    # --- 5. Editorial reader (different model from writer) ---
    editorial_context = _build_editorial_context(state)
    editorial = _run_editorial(prose, structural, editorial_context)

    # --- 6. Compute verdict ---
    verdict, quality_debt = _compute_editorial_verdict(
        structural, editorial, second_draft_used,
    )

    # --- 6b. Persist scene_history with the real verdict ---
    # Must happen AFTER verdict is computed, otherwise scene_history rows
    # are pinned at "pending" forever and accept_rate telemetry reads as
    # 0/0 on universes with 30+ committed scenes.
    _record_scene_verdict(
        db_path=db_path,
        scene_id=scene_id,
        state=state,
        word_count=draft_output.get("word_count", 0),
        verdict=verdict,
    )

    # Serialize editorial notes
    editorial_dict = _serialize_editorial(editorial)

    # Store editorial feedback as notes
    _store_editorial_as_notes(state, editorial)

    # Style observations left empty — learn.py's _editorial_to_observations
    # converts editorial_notes into properly dimensioned observations.
    style_obs: list[dict[str, str]] = []

    # Build commit result
    commit_result = {
        "scene_id": scene_id,
        "structural_pass": not structural.hard_failure and len(structural.violations) == 0,
        "structural_score": structural.aggregate_score,
        "structural_checks": [
            {
                "name": c.name,
                "passed": c.passed,
                "score": c.score,
                "violations": c.violations,
            }
            for c in structural.checks
        ],
        "warnings": structural.violations,
        "hard_failure": structural.hard_failure,
        "editorial_notes": editorial_dict,
        "overall_score": structural.aggregate_score,
    }

    # Convert facts to serializable dicts
    extracted_facts = [_serialize_fact(f) for f in facts_list]

    trace_entry = {
        "node": "commit",
        "scene_id": scene_id,
        "action": "commit_real",
        "verdict": verdict,
        "structural_score": structural.aggregate_score,
        "facts_extracted": len(facts_list),
        "promises_detected": len(promises),
        "worldbuild_signals": len(worldbuild_signals),
        "is_revision": is_revision,
    }
    process_eval = evaluate_scene_process(
        state,
        pending_trace=[trace_entry],
        verdict=verdict,
        second_draft_used=second_draft_used,
        commit_result=commit_result,
    )
    commit_result["process_evaluation"] = process_eval.to_dict()
    trace_entry["process_score"] = process_eval.aggregate_score
    trace_entry["process_failures"] = process_eval.failing_checks
    _store_consistency_audit_as_notes(state, structural, process_eval, scene_id)

    # --- Store scene result in memory on accept ---
    if verdict == "accept":
        _store_to_memory(state, extracted_facts)
        _export_prose(state, prose)
        _emit_scene_packet(
            state=state,
            scene_id=scene_id,
            facts_list=facts_list,
            promises=promises,
            structural=structural,
            editorial=editorial,
            verdict=verdict,
            word_count=draft_output.get("word_count", 0),
            is_revision=is_revision,
            worldbuild_signals=worldbuild_signals,
        )
        # Persist worldbuild signals for the universe-level nodes
        if worldbuild_signals:
            _persist_worldbuild_signals(state, worldbuild_signals)

    # --- Save draft to version store ---
    _save_to_version_store(state, prose, verdict, structural.aggregate_score)

    activity_log(
        state,
        f"Commit: {scene_id} -- score {structural.aggregate_score:.2f}, "
        f"verdict={verdict.upper()}",
    )

    return {
        "verdict": verdict,
        "commit_result": commit_result,
        "second_draft_used": second_draft_used,
        "extracted_facts": extracted_facts,
        "extracted_promises": promises,
        "worldbuild_signals": worldbuild_signals,
        "editorial_notes": editorial_dict,
        "style_observations": style_obs,
        "memory_context": memory_context,
        "quality_trace": [trace_entry],
        "quality_debt": quality_debt,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_editorial_context(state: dict[str, Any]) -> dict[str, str]:
    """Extract context for the editorial reader.

    Returns a dict with keys: previous_scene, canon_facts, direction_notes.
    """
    previous_scene = state.get("recent_prose", "")

    orient_result = state.get("orient_result") or {}
    canon_facts = orient_result.get("canon_context", "")

    direction_notes = ""
    universe_path = state.get("_universe_path")
    if universe_path:
        try:
            from tinyassets.notes import (
                format_notes_for_context,
                get_active_direction_notes,
            )

            direction_notes = format_notes_for_context(
                get_active_direction_notes(universe_path)
            )
        except Exception:
            logger.debug(
                "Failed to read active direction notes for editorial context",
                exc_info=True,
            )

    # Include premise so the editorial reader can flag premise departure
    premise = ""
    wf_instructions = state.get("workflow_instructions") or {}
    if isinstance(wf_instructions, dict):
        premise = wf_instructions.get("premise", "")
    if not premise:
        premise = state.get("premise_kernel", "")

    return {
        "previous_scene": previous_scene,
        "canon_facts": canon_facts,
        "direction_notes": direction_notes,
        "premise": premise,
    }


def _run_editorial(
    prose: str,
    structural: "StructuralResult",
    context: dict[str, str],
) -> EditorialNotes | None:
    """Run the editorial reader on the scene.

    Skips if structural evaluation already hard-failed (no point in
    editorial feedback on structurally broken prose). Returns None
    if the editorial call fails or is unavailable.
    """
    if structural.hard_failure:
        return None

    return read_editorial(
        prose,
        previous_scene=context.get("previous_scene", ""),
        canon_facts=context.get("canon_facts", ""),
        direction_notes=context.get("direction_notes", ""),
        premise=context.get("premise", ""),
    )


def _compute_editorial_verdict(
    structural: "StructuralResult",
    editorial: EditorialNotes | None,
    second_draft_used: bool,
) -> tuple[str, list[dict[str, Any]]]:
    """Compute verdict from structural + editorial results.

    Returns (verdict, quality_debt).

    Routing:
    1. Structural hard failure → revert.
    2. Any clearly_wrong editorial concern and not second_draft_used → second_draft.
    3. Otherwise → accept (never block).
    """
    debt: list[dict[str, Any]] = []

    # Rule 1: structural hard failure
    if structural.hard_failure:
        return "revert", debt

    # Rule 2: clearly wrong concerns trigger revision (once)
    if editorial and not second_draft_used:
        wrong_concerns = [c for c in editorial.concerns if c.clearly_wrong]
        if wrong_concerns:
            debt.append({
                "type": "editorial_clearly_wrong",
                "concerns": [c.text for c in wrong_concerns],
            })
            return "second_draft", debt

    # Low structural score records debt but still accepts
    if structural.aggregate_score < 0.6:
        debt.append({
            "type": "low_structural_score",
            "score": structural.aggregate_score,
            "violations": structural.violations,
        })

    # Rule 3: accept (never block)
    return "accept", debt


def _serialize_editorial(editorial: EditorialNotes | None) -> dict[str, Any] | None:
    """Convert EditorialNotes to a JSON-serializable dict."""
    if editorial is None:
        return None
    return {
        "protect": editorial.protect,
        "concerns": [
            {
                "text": c.text,
                "quoted_passage": c.quoted_passage,
                "clearly_wrong": c.clearly_wrong,
            }
            for c in editorial.concerns
        ],
        "next_scene": editorial.next_scene,
    }


def _serialize_fact(fact: Any) -> dict[str, Any]:
    """Convert a FactWithContext-like object to the shared extracted-fact schema."""
    if hasattr(fact, "to_dict"):
        serialized = dict(fact.to_dict())
    else:
        serialized = {
            "fact_id": getattr(fact, "fact_id", str(hash(str(fact)))),
            "text": getattr(fact, "text", str(fact)),
        }

    entity = _infer_fact_entity(fact)
    if entity:
        serialized["entity"] = entity
    return serialized


def _store_editorial_as_notes(
    state: dict[str, Any],
    editorial: EditorialNotes | None,
) -> None:
    """Store editorial reader feedback as notes in the unified notes system."""
    if editorial is None:
        return
    universe_path = state.get("_universe_path")
    if not universe_path:
        return
    try:
        import uuid as _uuid

        from tinyassets.notes import Note, add_notes_bulk

        batch: list[Note] = []
        for item in editorial.protect:
            batch.append(Note(
                id=str(_uuid.uuid4()), source="editor", text=item,
                category="protect",
            ))
        for concern in editorial.concerns:
            batch.append(Note(
                id=str(_uuid.uuid4()), source="editor", text=concern.text,
                category="error" if concern.clearly_wrong else "concern",
                clearly_wrong=concern.clearly_wrong,
                quoted_passage=concern.quoted_passage,
            ))
        if editorial.next_scene:
            batch.append(Note(
                id=str(_uuid.uuid4()), source="editor",
                text=editorial.next_scene, category="direction",
            ))
        add_notes_bulk(universe_path, batch)
    except Exception as e:
        logger.warning("Failed to store editorial notes: %s", e)


def _store_consistency_audit_as_notes(
    state: dict[str, Any],
    structural: "StructuralResult",
    process_eval: "ProcessEvaluation",
    scene_id: str,
) -> None:
    """Persist structural and process audit findings as notes."""
    universe_path = state.get("_universe_path")
    if not universe_path:
        return

    notes = _build_consistency_audit_notes(scene_id, structural, process_eval)
    if not notes:
        return

    try:
        from tinyassets.notes import add_notes_bulk

        add_notes_bulk(universe_path, notes)
    except Exception as e:
        logger.warning("Failed to store consistency audit notes: %s", e)


def _build_consistency_audit_notes(
    scene_id: str,
    structural: "StructuralResult",
    process_eval: "ProcessEvaluation",
) -> list[Any]:
    """Build durable notes from structural and process audit failures."""
    import uuid as _uuid

    from tinyassets.notes import Note

    notes: list[Note] = []

    for check in structural.checks:
        if check.passed:
            continue
        issue = check.violations[0] if check.violations else (
            f"Scene failed the {check.name.replace('_', ' ')} check."
        )
        notes.append(Note(
            id=str(_uuid.uuid4()),
            source="structural",
            text=issue,
            category="error" if check.name in {"canon_breach", "timeline"} else "concern",
            target=scene_id,
        ))

    process_checks = {check.name: check for check in process_eval.checks}
    for name in process_eval.failing_checks:
        check = process_checks.get(name)
        if check is None:
            continue
        observation = check.observation or (
            f"Process audit failed: {name.replace('_', ' ')}."
        )
        notes.append(Note(
            id=str(_uuid.uuid4()),
            source="system",
            text=observation,
            category="concern",
            target=scene_id,
        ))

    return notes


def _extract_facts(
    prose: str,
    scene_id: str,
    state: dict[str, Any],
) -> list:
    """Extract facts using LLM with regex fallback."""
    chapter_number = state.get("chapter_number", 1)

    # Try LLM-based extraction
    try:
        raw_response = call_for_extraction(prose)
        llm_facts = extract_facts_from_llm_response(
            raw_response, scene_id, chapter_number
        )
        if llm_facts:
            return llm_facts
    except Exception as e:
        logger.warning("LLM fact extraction failed: %s; using regex fallback", e)

    # Regex fallback
    return extract_facts_regex(prose, scene_id, chapter_number)


def _update_world_state(
    *,
    db_path: str,
    scene_id: str,
    state: dict[str, Any],
    facts_list: list,
    promises: list[dict[str, Any]],
    word_count: int,
    prose: str = "",
) -> None:
    """Persist facts, promises, and characters to the world state DB.

    Scene history is NOT written here — it's recorded by
    `_record_scene_verdict` after the verdict is computed, so the
    `scene_history.verdict` column reflects the actual accept/reject
    decision instead of always saying "pending". Writing it here with
    a placeholder and never updating it is the regression that left
    sporemarch's accept_rate stuck at 0/0.
    """
    try:
        init_db(db_path)
        with connect(db_path) as conn:
            # Store extracted facts
            for fact in facts_list:
                store_fact(
                    conn,
                    fact_id=fact.fact_id,
                    text=fact.text,
                    source_type=fact.source_type.value,
                    language_type=fact.language_type.value,
                    narrator=fact.narrator,
                    confidence=fact.confidence,
                    scene_id=scene_id,
                    chapter_number=state.get("chapter_number", 1),
                    importance=fact.importance,
                )

            # Store detected promises
            for i, promise in enumerate(promises):
                add_promise(
                    conn,
                    promise_id=f"{scene_id}_promise_{i}",
                    text=promise.get("context", promise.get("trigger_text", "")),
                    created_scene=scene_id,
                    created_chapter=state.get("chapter_number", 1),
                    importance=promise.get("importance", 0.5),
                )

            # Upsert characters found in extracted facts (or prose fallback)
            _upsert_characters_from_facts(
                conn, facts_list, scene_id, prose=prose,
            )
    except Exception as e:
        logger.warning("Failed to update world state DB: %s", e)
        # Non-critical: commit should never fail because of DB issues


def _record_scene_verdict(
    *,
    db_path: str,
    scene_id: str,
    state: dict[str, Any],
    word_count: int,
    verdict: str,
) -> None:
    """Write the scene's verdict to scene_history.

    Called once after the commit node's verdict is computed, so readers
    of `scene_history` (accept_rate telemetry, dashboard seed-from-db,
    learning loop) see the real decision. `INSERT OR REPLACE` keyed on
    scene_id means a second_draft re-commit overwrites the prior row.
    """
    try:
        init_db(db_path)
        with connect(db_path) as conn:
            record_scene(
                conn,
                scene_id=scene_id,
                universe_id=state.get("universe_id", ""),
                book_number=state.get("book_number", 1),
                chapter_number=state.get("chapter_number", 1),
                scene_number=state.get("scene_number", 1),
                word_count=word_count,
                verdict=verdict,
            )
    except Exception as e:
        logger.warning(
            "Failed to record scene verdict for %s: %s", scene_id, e,
        )


# Pattern to extract capitalized names from fact text.
# Minimum 2 chars total (capital + 1 lowercase) to catch short names like Ryn, Kai.
_NAME_PATTERN = re.compile(r"\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b")

# Stopwords: capitalized tokens that must never be treated as character
# names. Multi-word matches whose FIRST word lands here are trimmed at
# the boundary rather than rejected — so "If Kael" becomes "Kael".
#
# Mission 26 (task #51) added articles + prepositions + sentence-starting
# conjunctions + common capitalized nouns previously leaking through
# (e.g. "Manual", "Oxygen"), plus the `_LEADING_STOPWORDS` fast-path
# that strips a bad first token before the length / rejection gate.
_NAME_STOPWORDS = frozenset({
    # Articles / determiners.
    "The", "A", "An",
    # Personal / possessive pronouns.
    "She", "He", "Her", "His", "They", "Their", "It", "Its",
    "We", "Our", "Us", "Me", "My", "You", "Your",
    # Demonstratives / wh-words.
    "This", "That", "These", "Those",
    "When", "Then", "What", "Where", "How", "Who", "Why", "Which",
    "Now", "Once", "Here", "There",
    # Conjunctions + sentence-starters observed in Mission 26.
    "But", "And", "Not", "Or", "So", "Yet", "If", "Though", "Because",
    "While", "Although", "Since", "Unless", "After", "Before",
    # Prepositions + motion/target markers.
    "From", "Into", "With", "For", "By", "At", "On", "In", "Of",
    "Without", "Within", "Through", "Across", "Against", "Around",
    "Upon", "Under", "Over", "Between", "Beyond",
    # Meta-narrative tokens from the fiction corpus.
    "Scene", "Chapter", "Book", "Prologue", "Epilogue",
    # Overgeneralized adjectives the old pattern picked up.
    "Northern", "Southern", "Eastern", "Western",
    "Ancient", "Great", "Old", "New", "Dark", "Bright",
    "Some", "Many", "Few", "All", "None", "Every", "Each",
    # Common capitalized nouns previously captured as "characters"
    # (Mission 26 evidence).
    "Manual", "Oxygen", "Stasis",
})

# Single-word gatekeeper — if a multi-word match starts with one of
# these, we strip it rather than rejecting the whole match. "If Kael"
# → "Kael"; "For Oxygen" → (drops; trailing single-word Oxygen is
# still gated by the stopword set above).
_LEADING_STOPWORDS = _NAME_STOPWORDS


def _trim_leading_stopwords(name: str) -> str:
    """Strip stopword prefix tokens from a multi-word match.

    "If Kael" → "Kael". "In The Hall Kael" → "Hall Kael" (keeps first
    non-stopword+tail). Returns "" when every token is a stopword.
    """
    tokens = name.split()
    while tokens and tokens[0] in _LEADING_STOPWORDS:
        tokens.pop(0)
    return " ".join(tokens)


# Minimum per-word length for a token to be a plausible name. "If" = 2
# chars is rejected; "Kai" = 3 chars is kept.
_MIN_TOKEN_LEN = 3


def _is_plausible_name(name: str) -> bool:
    """Gate a capitalized token sequence for character-name plausibility.

    Rejects:
      - Empty / too-short names (< 3 chars overall).
      - First token in _NAME_STOPWORDS (post-trim safety net).
      - ANY token in the sequence shorter than _MIN_TOKEN_LEN — catches
        "Ryn X" style leaks where an abbreviation masquerades as a
        middle name.
      - Single-word names that exactly match _NAME_STOPWORDS (redundant
        with _NAME_STOPWORDS check but defense-in-depth).
    """
    if not name or len(name) < _MIN_TOKEN_LEN:
        return False
    tokens = name.split()
    if not tokens:
        return False
    if tokens[0] in _NAME_STOPWORDS:
        return False
    for tok in tokens:
        if len(tok) < _MIN_TOKEN_LEN:
            return False
        if tok in _NAME_STOPWORDS:
            return False
    return True


def _infer_fact_entity(fact: Any) -> str | None:
    """Best-effort primary entity for episodic memory indexing."""
    pov_characters = getattr(fact, "pov_characters", None) or []
    if pov_characters:
        return str(pov_characters[0])

    narrator = getattr(fact, "narrator", None)
    if narrator:
        return str(narrator)

    text = getattr(fact, "text", "")
    for match in _NAME_PATTERN.finditer(text):
        name = _trim_leading_stopwords(match.group(1))
        if _is_plausible_name(name):
            return name

    return None


def _upsert_characters_from_facts(
    conn: Any,
    facts_list: list,
    scene_id: str,
    prose: str = "",
) -> None:
    """Extract character names from facts and upsert into character_states.

    Scans fact text, pov_characters, and narrator fields to find
    character names. Associates relevant fact IDs with each character.
    Falls back to scanning the prose directly if no characters are
    found from facts (handles LLM extraction failure gracefully).
    """
    # Collect character_name -> set of fact_ids
    characters: dict[str, set[str]] = {}

    for fact in facts_list:
        # From pov_characters field — trust more than free-text but still
        # gate to catch LLM hallucination like ["If", "Manual"].
        for name in getattr(fact, "pov_characters", []) or []:
            if _is_plausible_name(name or ""):
                characters.setdefault(name, set()).add(fact.fact_id)

        # From narrator field
        narrator = getattr(fact, "narrator", None)
        if narrator and _is_plausible_name(narrator):
            characters.setdefault(narrator, set()).add(fact.fact_id)

        # From fact text — look for capitalized names. Trim leading
        # stopwords ("If Kael" → "Kael") before plausibility gate.
        text = getattr(fact, "text", "")
        for match in _NAME_PATTERN.finditer(text):
            name = _trim_leading_stopwords(match.group(1))
            if _is_plausible_name(name):
                characters.setdefault(name, set()).add(fact.fact_id)

    # Fallback: scan prose directly if no characters found from facts
    if not characters and prose:
        for match in _NAME_PATTERN.finditer(prose):
            name = _trim_leading_stopwords(match.group(1))
            if _is_plausible_name(name):
                characters.setdefault(name, set())

    # Upsert each character
    for name, fact_ids in characters.items():
        char_id = name.lower().replace(" ", "_")
        try:
            upsert_character(
                conn,
                character_id=char_id,
                name=name,
                knowledge_facts=sorted(fact_ids),
                last_updated_scene=scene_id,
            )
        except Exception as e:
            logger.warning(
                "Failed to upsert character %s: %s", name, e,
            )

    if characters:
        logger.info(
            "Upserted %d characters from %d facts",
            len(characters), len(facts_list),
        )


def _assemble_memory(state: dict[str, Any], phase: str) -> dict:
    """Call MemoryManager.assemble_context if available."""
    from tinyassets import runtime_singletons as runtime

    mgr = runtime.memory_manager
    if mgr is None:
        return state.get("memory_context", {})
    try:
        return dict(mgr.assemble_context(phase, state))
    except Exception as e:
        logger.warning("MemoryManager.assemble_context(%s) failed: %s", phase, e)
        return state.get("memory_context", {})


def _store_to_memory(state: dict[str, Any], extracted_facts: list) -> None:
    """Store scene results to MemoryManager if available."""
    from tinyassets import runtime_singletons as runtime

    mgr = runtime.memory_manager
    if mgr is None:
        return
    try:
        store_state = dict(state)
        store_state["extracted_facts"] = extracted_facts
        mgr.store_scene_result(store_state)
    except Exception as e:
        logger.warning("MemoryManager.store_scene_result failed: %s", e)


def _save_to_version_store(
    state: dict[str, Any],
    prose: str,
    verdict: str,
    quality_score: float,
) -> None:
    """Save draft to OutputVersionStore if available."""
    from tinyassets import runtime_singletons as runtime

    store = runtime.version_store
    if store is None:
        return
    try:
        store.save_draft(
            book=state.get("book_number", 1),
            chapter=state.get("chapter_number", 1),
            scene=state.get("scene_number", 1),
            prose=prose,
            verdict=verdict,
            quality_score=quality_score,
        )
    except Exception as e:
        logger.warning("OutputVersionStore.save_draft failed: %s", e)


def _generate_worldbuild_signals(
    facts_list: list,
    state: dict[str, Any],
) -> list[dict[str, Any]]:
    """Compare extracted facts against existing canon to detect signals.

    Calls the LLM with the extracted facts and relevant canon excerpts
    to identify: new elements, contradictions, and expansions.

    Returns a list of signal dicts, each with ``type``, ``topic``, and
    ``detail`` keys.  Returns an empty list on failure (graceful).
    """
    if not facts_list:
        return []

    universe_path = state.get("_universe_path")
    if not universe_path:
        return []

    canon_dir = Path(universe_path) / "canon"
    if not canon_dir.exists():
        return []

    # Read existing canon files (truncated for prompt size)
    canon_excerpts = _read_canon_excerpts(canon_dir)
    if not canon_excerpts:
        # No canon yet -- every fact is implicitly a new element,
        # but we skip signal generation since worldbuild will bootstrap.
        return []

    # Build facts summary
    facts_text = "\n".join(
        f"- {f.text} (confidence={f.confidence}, source={f.source_type.value})"
        for f in facts_list[:20]  # Cap to keep prompt manageable
    )

    system = (
        "You are a worldbuilding continuity checker for a fantasy novel. "
        "Compare the newly extracted facts from a scene against the existing "
        "canon documents. Identify:\n"
        "1. **new_element**: A character, location, faction, artifact, or concept "
        "mentioned in the facts that has NO corresponding canon document.\n"
        "2. **contradiction**: A fact that CONFLICTS with what the canon states.\n"
        "3. **expansion**: Significant new detail about something that HAS a "
        "canon document but the doc is thin or missing this detail.\n\n"
        "Respond ONLY with a JSON array. Each element must have:\n"
        '  "type": one of "new_element", "contradiction", "expansion"\n'
        '  "topic": the worldbuilding topic (e.g., "character", "magic_system", "locations")\n'
        '  "detail": one sentence describing what was found\n'
        "If no signals are found, return an empty array: []"
    )

    prompt = (
        f"# Extracted Facts from Latest Scene\n\n{facts_text}\n\n"
        f"# Existing Canon\n\n{canon_excerpts}\n\n"
        "# Task\n\n"
        "What's new here? What contradicts existing notes? "
        "What expands on thin areas?"
    )

    try:
        raw = call_provider(
            prompt, system, role="extract", fallback_response="[]"
        )
        return _parse_worldbuild_signals(raw)
    except Exception as e:
        logger.warning("Worldbuild signal generation failed: %s", e)
        return []


def _read_canon_excerpts(canon_dir: Path, max_chars: int = 3000) -> str:
    """Read canon files and return a truncated summary for prompting.

    Enumeration is routed through :func:`iter_canon_files`, which resolves and
    contains every entry before ``is_file`` / ``read_text`` so a symlinked
    ``.md`` whose target lives outside ``canon_dir`` is skipped, not read.
    """
    excerpts: list[str] = []
    total = 0
    try:
        for f in iter_canon_files(canon_dir, suffix=".md"):
            try:
                content = f.read_text(encoding="utf-8")
                # Truncate individual files
                truncated = content[:1000]
                excerpts.append(f"### {f.name}\n{truncated}")
                total += len(truncated)
                if total >= max_chars:
                    break
            except OSError:
                continue
    except OSError:
        pass
    return "\n\n".join(excerpts)


def _parse_worldbuild_signals(raw: str) -> list[dict[str, Any]]:
    """Parse the LLM's JSON response into a list of worldbuild signals."""
    text = raw.strip()
    # Strip markdown code fences if present
    if text.startswith("```"):
        lines = text.split("\n")
        lines = [ln for ln in lines if not ln.strip().startswith("```")]
        text = "\n".join(lines)

    try:
        signals = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        logger.warning("Could not parse worldbuild signals as JSON")
        return []

    if not isinstance(signals, list):
        return []

    valid: list[dict[str, Any]] = []
    for item in signals:
        if not isinstance(item, dict):
            continue
        sig_type = item.get("type", "")
        if sig_type not in ("new_element", "contradiction", "expansion"):
            continue
        valid.append({
            "type": sig_type,
            "topic": item.get("topic", "unknown"),
            "detail": item.get("detail", ""),
        })

    return valid


def _persist_worldbuild_signals(
    state: dict[str, Any],
    signals: list[dict[str, Any]],
) -> None:
    """Append enrichment signals to the universe's signals file.

    Writes to ``{universe_path}/enrichment_signals.json`` so that
    universe-level nodes (select_task, worldbuild) can read them
    without requiring signals to propagate through graph state.
    """
    universe_path = state.get("_universe_path")
    if not universe_path:
        return

    try:
        append_enrichment_signals(universe_path, signals)
        logger.info(
            "Persisted %d enrichment signals to %s",
            len(signals), Path(universe_path) / "enrichment_signals.json",
        )
    except OSError as e:
        logger.warning("Failed to persist enrichment signals: %s", e)


def _export_prose(state: dict[str, Any], prose: str) -> None:
    """Write accepted prose to a per-scene file on disk.

    Writes to ``{universe_path}/output/book-{N}/chapter-{NN}/scene-{NN}.md``.
    Each scene is its own file; the filesystem is the index.
    """
    universe_path = state.get("_universe_path")
    if not universe_path:
        return
    book = state.get("book_number", 1)
    chapter = state.get("chapter_number", 1)
    scene = state.get("scene_number", 1)
    try:
        chapter_dir = (
            Path(universe_path) / "output"
            / f"book-{book}" / f"chapter-{chapter:02d}"
        )
        scene_file = chapter_dir / f"scene-{scene:02d}.md"
        write_data_path(scene_file, prose)
        logger.info(
            "Exported scene %d prose to %s", scene, scene_file,
        )
    except OSError as e:
        logger.warning("Failed to export prose: %s", e)


def _emit_scene_packet(
    state: dict[str, Any],
    scene_id: str,
    facts_list: list,
    promises: list,
    structural: "StructuralResult",
    editorial: EditorialNotes | None,
    verdict: str,
    word_count: int,
    is_revision: bool,
    worldbuild_signals: list,
) -> None:
    """Write a structured ScenePacket JSON alongside the prose file.

    The packet captures everything the commit pipeline extracted for this
    scene in machine-readable form.  Persisted at the same path as the
    prose file but with a ``.packet.json`` extension.
    """
    from tinyassets.packets import (
        EditorialVerdict,
        FactRef,
        PromiseRef,
        ScenePacket,
    )

    universe_path = state.get("_universe_path")
    if not universe_path:
        return

    orient_result = state.get("orient_result") or {}

    # Build fact refs from extracted facts
    fact_refs = []
    for f in facts_list:
        if hasattr(f, "fact_id"):
            raw_st = getattr(f, "source_type", "unknown")
            fact_refs.append(FactRef(
                fact_id=f.fact_id,
                text=f.text,
                source_type=raw_st.value if hasattr(raw_st, "value") else str(raw_st),
                confidence=getattr(f, "confidence", 0.5),
                importance=getattr(f, "importance", 0.5),
            ))
        elif isinstance(f, dict):
            raw_st = f.get("source_type", "unknown")
            fact_refs.append(FactRef(
                fact_id=f.get("fact_id", ""),
                text=f.get("text", ""),
                source_type=raw_st.value if hasattr(raw_st, "value") else str(raw_st),
                confidence=f.get("confidence", 0.5),
                importance=f.get("importance", 0.5),
            ))

    # Build promise refs
    promise_refs = []
    for p in promises:
        if isinstance(p, dict):
            promise_refs.append(PromiseRef(
                promise_type=p.get("promise_type", ""),
                trigger_text=p.get("trigger_text", ""),
                context=p.get("context", ""),
                scene_id=scene_id,
                chapter_number=state.get("chapter_number", 1),
                importance=p.get("importance", 0.5),
            ))

    # Build editorial verdict
    editorial_verdict = None
    if editorial is not None or structural is not None:
        concerns = []
        protect: list[str] = []
        if editorial is not None:
            for c in getattr(editorial, "concerns", []):
                concerns.append({
                    "text": getattr(c, "text", str(c)),
                    "clearly_wrong": getattr(c, "clearly_wrong", False),
                })
            protect = list(getattr(editorial, "protect", []))

        editorial_verdict = EditorialVerdict(
            verdict=verdict,
            structural_pass=not structural.hard_failure,
            structural_score=structural.aggregate_score,
            hard_failure=structural.hard_failure,
            concerns=concerns,
            protect=protect,
        )

    # Extract participants as plain strings from orient character dicts
    raw_characters = orient_result.get("characters", [])
    participants = []
    for c in raw_characters:
        if isinstance(c, dict):
            name = c.get("name") or c.get("character_id") or c.get("id", "")
            if name:
                participants.append(str(name))
        elif isinstance(c, str) and c:
            participants.append(c)

    packet = ScenePacket(
        scene_id=scene_id,
        universe_id=state.get("universe_id", ""),
        book_number=state.get("book_number", 1),
        chapter_number=state.get("chapter_number", 1),
        scene_number=state.get("scene_number", 1),
        pov_character=orient_result.get("pov_character"),
        location=orient_result.get("location"),
        time_marker=orient_result.get("time_marker"),
        participants=participants,
        facts_introduced=fact_refs,
        promises_opened=promise_refs,
        editorial=editorial_verdict,
        word_count=word_count,
        is_revision=is_revision,
        enrichment_signals=worldbuild_signals,
        worldbuild_signals=worldbuild_signals,
    )

    # Write packet JSON next to the prose file
    book = state.get("book_number", 1)
    chapter = state.get("chapter_number", 1)
    scene = state.get("scene_number", 1)
    try:
        chapter_dir = (
            Path(universe_path) / "output"
            / f"book-{book}" / f"chapter-{chapter:02d}"
        )
        packet_file = chapter_dir / f"scene-{scene:02d}.packet.json"
        write_data_path(packet_file, json.dumps(packet.to_dict(), indent=2, default=str) + "\n")
        logger.info("Emitted scene packet to %s", packet_file)
    except OSError as e:
        logger.warning("Failed to emit scene packet: %s", e)


def _index_prose_entities(
    prose: str,
    scene_id: str,
    state: dict[str, Any],
) -> None:
    """Extract entities/relationships from prose and index into KG.

    Uses the fiction-specific entity extraction pipeline (with alias
    registry) and the indexer module. Non-blocking -- failures are
    logged but don't affect the commit verdict.
    """
    from tinyassets import runtime_singletons as runtime

    kg = runtime.knowledge_graph
    vs = runtime.vector_store
    embed_fn = runtime.embed_fn
    if kg is None and vs is None:
        logger.debug("Entity indexing skipped: no KG or VectorStore")
        return

    if not prose.strip():
        logger.debug("Entity indexing skipped: empty prose")
        return

    try:
        from tinyassets.ingestion.indexer import index_text
        from tinyassets.memory.scoping import MemoryScope

        # Memory-scope Stage 2b: tag writes with the caller's universe
        # tier. Sub-tiers (goal/branch/user) stay None on the commit
        # path until the phase plumbs them through state — which is
        # 2b.3 / task scope for later landings.
        universe_id = (
            state.get("universe_id")
            or state.get("_universe_id")
            or ""
        )
        scope = MemoryScope(universe_id=str(universe_id)) if universe_id else None

        result = index_text(
            prose,
            source_id=scene_id,
            knowledge_graph=kg,
            vector_store=vs,
            embed_fn=embed_fn,
            provider_call=call_provider,
            chapter_number=state.get("chapter_number", 0),
            scope=scope,
        )
        logger.info(
            "Entity indexing for %s: %d entities, %d edges, %d facts, %d chunks",
            scene_id,
            result.get("entities", 0),
            result.get("edges", 0),
            result.get("facts", 0),
            result.get("chunks_indexed", 0),
        )
    except Exception as e:
        logger.warning("Entity indexing failed for %s: %s", scene_id, e)
