"""Unified notes system — one format for all feedback.

Each universe has a notes.json file. Sources include user (steering),
editor (editorial reader), structural (deterministic checks), and
system (enrichment signals, learning signals).

Notes flow: created → unread → read (orient consumed) → acted_on/dismissed
(commit processed). The writer reads notes and decides what to act on.
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class Note:
    """A single note in the unified notes system."""

    id: str
    source: str  # "user" | "editor" | "structural" | "system"
    text: str
    category: str  # "protect" | "concern" | "direction" | "observation" | "error"
    status: str = "unread"  # "unread" | "read" | "acted_on" | "dismissed"
    target: str | None = None  # file path or scene reference
    clearly_wrong: bool = False  # for concerns: provable error?
    quoted_passage: str = ""  # evidence from prose
    anchor: dict[str, Any] = field(default_factory=dict)  # optional line/span anchor
    tags: list[str] = field(default_factory=list)  # optional tags for filtering/categorization
    metadata: dict[str, Any] = field(default_factory=dict)  # optional metadata dict
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Note:
        return cls(
            id=data.get("id", str(uuid.uuid4())),
            source=data.get("source", "system"),
            text=data.get("text", ""),
            category=data.get("category", "observation"),
            status=data.get("status", "unread"),
            target=data.get("target"),
            clearly_wrong=data.get("clearly_wrong", False),
            quoted_passage=data.get("quoted_passage", ""),
            anchor=data.get("anchor", {}) if isinstance(data.get("anchor"), dict) else {},
            tags=data.get("tags", []) if isinstance(data.get("tags"), list) else [],
            metadata=data.get("metadata", {}) if isinstance(data.get("metadata"), dict) else {},
            timestamp=data.get("timestamp", time.time()),
        )


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------


def _notes_path(universe_path: str | Path) -> Path:
    return Path(universe_path) / "notes.json"


def _load_notes(universe_path: str | Path) -> list[Note]:
    """Load all notes from notes.json."""
    from tinyassets.universe_files import MAX_PLATFORM_FILE_BYTES, read_data_path

    path = _notes_path(universe_path)
    # Link-free. A REFUSED read raises: reading it as [] would let the next
    # _save_notes replace the file with one note.
    raw = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
    if raw is None:
        return []
    try:
        data = json.loads(raw.decode("utf-8"))
        if not isinstance(data, list):
            return []
        return [Note.from_dict(d) for d in data if isinstance(d, dict)]
    except ValueError as e:
        logger.warning("Failed to load notes: %s", e)
        return []


def _save_notes(universe_path: str | Path, notes: list[Note]) -> None:
    """Save all notes to notes.json."""
    from tinyassets.universe_files import write_data_path

    path = _notes_path(universe_path)
    try:
        write_data_path(path, json.dumps([n.to_dict() for n in notes], indent=2) + "\n")
    except OSError as e:
        logger.warning("Failed to save notes: %s", e)


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


def add_note(
    universe_path: str | Path,
    *,
    source: str,
    text: str,
    category: str,
    target: str | None = None,
    clearly_wrong: bool = False,
    quoted_passage: str = "",
    anchor: dict[str, Any] | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> Note:
    """Add a new note. Returns the created note."""
    note = Note(
        id=str(uuid.uuid4()),
        source=source,
        text=text,
        category=category,
        target=target,
        clearly_wrong=clearly_wrong,
        quoted_passage=quoted_passage,
        anchor=anchor or {},
        tags=tags or [],
        metadata=metadata or {},
    )
    notes = _load_notes(universe_path)
    notes.append(note)
    _save_notes(universe_path, notes)
    return note


def add_notes_bulk(
    universe_path: str | Path,
    new_notes: list[Note],
) -> None:
    """Add multiple notes in a single load-save cycle."""
    if not new_notes:
        return
    notes = _load_notes(universe_path)
    notes.extend(new_notes)
    _save_notes(universe_path, notes)


def list_notes(
    universe_path: str | Path,
    *,
    source: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> list[Note]:
    """List notes with optional filters."""
    notes = _load_notes(universe_path)
    if source:
        notes = [n for n in notes if n.source == source]
    if category:
        notes = [n for n in notes if n.category == category]
    if status:
        notes = [n for n in notes if n.status == status]
    return notes


def update_note_status(
    universe_path: str | Path,
    note_id: str,
    status: str,
) -> bool:
    """Update a note's status. Returns True if found and updated."""
    if status not in ("unread", "read", "acted_on", "dismissed"):
        return False
    notes = _load_notes(universe_path)
    for note in notes:
        if note.id == note_id:
            note.status = status
            _save_notes(universe_path, notes)
            return True
    return False


def bulk_update_status(
    universe_path: str | Path,
    note_ids: list[str],
    status: str,
) -> int:
    """Update multiple notes' status at once. Returns count updated."""
    if status not in ("unread", "read", "acted_on", "dismissed"):
        return 0
    notes = _load_notes(universe_path)
    id_set = set(note_ids)
    count = 0
    for note in notes:
        if note.id in id_set:
            note.status = status
            count += 1
    if count > 0:
        _save_notes(universe_path, notes)
    return count


def delete_note(universe_path: str | Path, note_id: str) -> bool:
    """Delete a note. Returns True if found and deleted."""
    notes = _load_notes(universe_path)
    original_len = len(notes)
    notes = [n for n in notes if n.id != note_id]
    if len(notes) < original_len:
        _save_notes(universe_path, notes)
        return True
    return False


# ---------------------------------------------------------------------------
# Orient integration
# ---------------------------------------------------------------------------


def get_unread_notes_for_orient(universe_path: str | Path) -> list[Note]:
    """Get unread notes for the orient phase.

    Returns unread notes sorted by priority: errors first, then concerns,
    then directions, then observations/protects.
    """
    notes = list_notes(universe_path, status="unread")
    priority = {"error": 0, "concern": 1, "direction": 2, "observation": 3, "protect": 4}
    notes.sort(key=lambda n: priority.get(n.category, 5))
    return notes


def format_notes_for_context(notes: list[Note]) -> str:
    """Format notes into a string for LLM context injection."""
    if not notes:
        return ""
    parts: list[str] = []
    for note in notes:
        label = f"[{note.source}/{note.category}]"
        if note.clearly_wrong:
            label = f"[{note.source}/ERROR]"
        line = f"{label} {note.text}"
        if note.quoted_passage:
            line += f' — "{note.quoted_passage}"'
        parts.append(line)
    return "\n".join(parts)


def mark_notes_read(universe_path: str | Path, note_ids: list[str]) -> int:
    """Mark notes as read after orient consumes them."""
    return bulk_update_status(universe_path, note_ids, "read")


def get_active_direction_notes(universe_path: str | Path) -> list[Note]:
    """Get active direction notes for commit and enrich phases.

    Returns notes with category="direction" and status in ["unread", "read"].
    These are the user's steering inputs that guide the writer's next moves.
    """
    notes = _load_notes(universe_path)
    return [
        n for n in notes
        if n.category == "direction" and n.status in ("unread", "read")
    ]
