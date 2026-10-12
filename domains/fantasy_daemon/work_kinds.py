"""Fantasy-authoring execution kinds + scope inference.

Moved here from `tinyassets/work_targets.py` in Phase C.2 per
`taskproducer_phase_c.md (archived in git)` §2. BOOK/CHAPTER/SCENE are fantasy
concepts; a second domain would never emit them. `EXECUTION_KIND_NOTES`
stays on the generic engine side (every domain has notes-class work).

Validation lives at the producer layer — `FantasyAuthorialProducer`
(C.4) asserts outputs are in ``FANTASY_EXECUTION_KINDS``. The generic
engine only requires execution_kind be a non-empty string.
"""

from __future__ import annotations

import re
from typing import Any

from tinyassets.work_targets import (
    EXECUTION_KIND_NOTES,
    ROLE_NOTES,
    WorkTarget,
)

EXECUTION_KIND_BOOK = "book"
EXECUTION_KIND_CHAPTER = "chapter"
EXECUTION_KIND_SCENE = "scene"

FANTASY_EXECUTION_KINDS = (
    EXECUTION_KIND_NOTES,
    EXECUTION_KIND_BOOK,
    EXECUTION_KIND_CHAPTER,
    EXECUTION_KIND_SCENE,
)


_BOOK_NUMBER_RE = re.compile(r"\bbook[-\s]*(\d+)\b", re.IGNORECASE)
_CHAPTER_NUMBER_RE = re.compile(r"\bchapter[-\s]*(\d+)\b", re.IGNORECASE)
_SCENE_NUMBER_RE = re.compile(r"\bscene[-\s]*(\d+)\b", re.IGNORECASE)


def _coerce_int(value: Any, default: int | None = None) -> int | None:
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _match_number(pattern: re.Pattern[str], *values: str) -> int | None:
    for value in values:
        match = pattern.search(value)
        if match:
            return _coerce_int(match.group(1))
    return None


def infer_fantasy_execution_scope(
    target: WorkTarget | None,
) -> dict[str, Any]:
    """Infer runtime execution coordinates from a work target.

    Fantasy-specific: returns a scope dict keyed by
    ``book_number``/``chapter_number``/``scene_number`` as appropriate
    for the inferred ``execution_kind``. Pre-C.2 name was
    ``infer_execution_scope`` in the generic engine module.
    """
    if target is None:
        return {}

    metadata = dict(target.metadata)
    tags = {
        str(tag).strip().lower().replace("-", "_")
        for tag in target.tags
        if str(tag).strip()
    }
    title = target.title
    target_id = target.target_id

    execution_kind = str(metadata.get("execution_kind") or "").strip().lower()
    if execution_kind not in FANTASY_EXECUTION_KINDS:
        if target.role == ROLE_NOTES:
            execution_kind = EXECUTION_KIND_NOTES
        elif (
            "scene" in tags
            or metadata.get("scene_number") is not None
            or _match_number(_SCENE_NUMBER_RE, title, target_id) is not None
        ):
            execution_kind = EXECUTION_KIND_SCENE
        elif (
            "chapter" in tags
            or metadata.get("chapter_number") is not None
            or _match_number(_CHAPTER_NUMBER_RE, title, target_id) is not None
        ):
            execution_kind = EXECUTION_KIND_CHAPTER
        else:
            execution_kind = EXECUTION_KIND_BOOK

    scope: dict[str, Any] = {
        "execution_kind": execution_kind,
        "target_id": target.target_id,
    }

    if execution_kind == EXECUTION_KIND_NOTES:
        return scope

    book_number = _coerce_int(
        metadata.get("book_number"),
        _match_number(_BOOK_NUMBER_RE, title, target_id) or 1,
    ) or 1
    scope["book_number"] = book_number

    if execution_kind in {EXECUTION_KIND_CHAPTER, EXECUTION_KIND_SCENE}:
        chapter_number = _coerce_int(
            metadata.get("chapter_number"),
            _match_number(_CHAPTER_NUMBER_RE, title, target_id),
        )
        if chapter_number is not None:
            scope["chapter_number"] = chapter_number

    if execution_kind == EXECUTION_KIND_SCENE:
        scene_number = _coerce_int(
            metadata.get("scene_number"),
            _match_number(_SCENE_NUMBER_RE, title, target_id),
        )
        if scene_number is not None:
            scope["scene_number"] = scene_number

    chapters_target = _coerce_int(metadata.get("chapters_target"))
    if chapters_target is not None:
        scope["chapters_target"] = chapters_target

    scenes_target = _coerce_int(metadata.get("scenes_target"))
    if scenes_target is not None:
        scope["scenes_target"] = scenes_target

    return scope
