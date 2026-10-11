"""Consolidate node -- chapter graph; entry after all scenes complete, promotes facts before learn.

Promotes facts that have accumulated 3+ scene evidence via
MemoryManager promotion gates, evicts old episodic data outside the
sliding window, and generates a chapter summary.

Contract
--------
Input:  ChapterState after all scenes have completed.
Output: Partial ChapterState with ``chapter_summary`` and
        ``consolidated_facts``.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from domains.fantasy_daemon.phases._provider_stub import text_sync

logger = logging.getLogger(__name__)


def consolidate(state: dict[str, Any]) -> dict[str, Any]:
    """Consolidate scene outputs into chapter-level summaries.

    When a MemoryManager is available (via ``runtime.memory_manager``):
    1. Runs promotion gates to promote episodic facts with 3+ evidence.
    2. Evicts old episodic data outside the sliding window.

    Parameters
    ----------
    state : ChapterState
        Must contain ``scenes_completed`` and scene-level accumulated data.

    Returns
    -------
    dict
        Partial state with:
        - ``chapter_summary``: summary of the chapter.
        - ``consolidated_facts``: promoted facts.
    """
    chapter_id = (
        f"{state['universe_id']}-B{state['book_number']}"
        f"-C{state['chapter_number']}"
    )

    promoted_facts: list[dict[str, Any]] = []

    from tinyassets import runtime_singletons as runtime

    mgr = runtime.memory_manager
    if mgr is not None:
        try:
            result = mgr.run_promotion_gates()
            promoted_facts = result.promoted_facts
            logger.info(
                "Consolidation promoted %d facts, %d style rules",
                len(result.promoted_facts),
                len(result.promoted_style_rules),
            )
        except Exception as e:
            logger.warning("Promotion gates failed: %s", e)

        try:
            evicted = mgr.evict_old_data(
                current_chapter=state["chapter_number"],
                book=state.get("book_number", 1),
            )
            if evicted:
                logger.info("Evicted %d old episodic entries", evicted)
        except Exception as e:
            logger.warning("Eviction failed: %s", e)

    consolidated = [
        {
            "fact": f"Chapter {state['chapter_number']} consolidation complete.",
            "chapter_id": chapter_id,
            "promotion_level": "chapter",
        }
    ]
    for pf in promoted_facts:
        consolidated.append({
            "fact": pf.get("content", pf.get("fact_id", "")),
            "chapter_id": chapter_id,
            "promotion_level": "promoted",
            "fact_id": pf.get("fact_id"),
        })

    # --- Generate real chapter summary via LLM ---
    fallback_summary = (
        f"Chapter {state['chapter_number']} completed with "
        f"{state['scenes_completed']} scenes."
    )
    chapter_summary = _summarize_chapter(state, fallback_summary)

    return {
        "chapter_summary": chapter_summary,
        "consolidated_facts": consolidated,
    }


def _read_chapter_prose(state: dict[str, Any]) -> str:
    """Read chapter prose from per-scene files on disk.

    Reads ``scene-*.md`` files from the chapter directory, sorted by
    name, and joins them with scene separators.  Falls back to the
    legacy single chapter file if the directory doesn't exist.

    Returns the prose text, or empty string if unavailable.
    """
    universe_path = state.get("_universe_path")
    if not universe_path:
        return ""
    book = state.get("book_number", 1)
    chapter = state.get("chapter_number", 1)

    # Per-scene directory (new format)
    chapter_dir = (
        Path(universe_path) / "output"
        / f"book-{book}" / f"chapter-{chapter:02d}"
    )
    try:
        if chapter_dir.is_dir():
            scene_files = sorted(
                f for f in chapter_dir.iterdir()
                if f.is_file() and f.suffix == ".md"
            )
            if scene_files:
                parts = []
                for sf in scene_files:
                    parts.append(sf.read_text(encoding="utf-8"))
                return "\n\n---\n\n".join(parts)
    except OSError as e:
        logger.warning("Failed to read scene files from %s: %s", chapter_dir, e)

    # Legacy fallback: single chapter file
    chapter_file = (
        Path(universe_path) / "output"
        / f"book-{book}" / f"chapter-{chapter:02d}.md"
    )
    try:
        if chapter_file.exists():
            return chapter_file.read_text(encoding="utf-8")
    except OSError as e:
        logger.warning("Failed to read chapter prose from %s: %s", chapter_file, e)
    return ""


def _summarize_chapter(state: dict[str, Any], fallback: str) -> str:
    """Summarize chapter prose via an LLM call, falling back to *fallback*.

    Reads the exported chapter file, sends it to the provider with a
    summarization prompt, and returns the summary text.
    """
    prose = _read_chapter_prose(state)
    if not prose.strip():
        logger.info("No chapter prose on disk; using fallback summary")
        return fallback

    system = (
        "You are a fiction editor summarizing a chapter. Write a concise "
        "summary (3-5 sentences) covering: key events, character developments, "
        "and any plot threads opened or resolved. Be specific — name characters "
        "and places. Do not editorialize about prose quality."
    )
    prompt = (
        f"Summarize this chapter (Book {state.get('book_number', 1)}, "
        f"Chapter {state.get('chapter_number', 1)}):\n\n{prose[:6000]}"
    )

    try:
        summary = text_sync(prompt, system, role="extract")
        if summary and summary.strip():
            logger.info(
                "Generated real chapter summary (%d chars)", len(summary),
            )
            return summary.strip()
    except Exception as e:
        logger.warning("Chapter summarization failed: %s", e)

    return fallback
