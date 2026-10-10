"""MCP server exposing the TinyAssets daemon's file interface.

Provides tools for any AI client to read status, add notes for the daemon,
manage premises, read output, and control execution.

Usage::

    python -m tinyassets.mcp_server

Set ``TINYASSETS_UNIVERSE`` to the universe directory path,
or it defaults to ``output/default-universe/``.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from functools import wraps
from inspect import signature
from pathlib import Path

from fastmcp import FastMCP
from mcp.types import ToolAnnotations

from tinyassets.universe_soul import (
    legacy_premise_path,
    premise_from_soul,
    read_legacy_premise,
    write_universe_soul,
)

mcp = FastMCP(
    "tinyassets",
    instructions=(
        "TinyAssets daemon interface. Use these tools to monitor "
        "and guide an autonomous workflow daemon through notes. "
        "Start with get_status to see the daemon's current phase, then "
        "use get_progress for a human-readable summary of what's been produced."
    ),
    version="0.1.0",
)


def _repo_root() -> Path:
    """Absolute path to the repository root (two levels up from this module)."""
    return Path(__file__).resolve().parent.parent


def _universe_dir() -> Path:
    """Resolve the universe directory from environment or default.

    Precedence:
      1. ``$TINYASSETS_UNIVERSE`` — explicit per-universe override.
      2. ``tinyassets.storage.data_dir() / "default-universe"`` — anchored
         to the canonical ``TINYASSETS_DATA_DIR`` root (or its legacy
         alias / platform default).

    The default is anchored to the daemon's on-disk data root, NOT the
    repo root. Pre-2026-04-20 this defaulted to
    ``<repo_root>/output/default-universe`` which worked on a dev
    machine but wrote to ``/app/output/default-universe`` in a
    container — NOT the bind-mounted ``/data`` volume. Rooting at
    ``data_dir()`` closes the container CWD-drift bug class.

    Cross-universe contamination guard still applies: never CWD-relative.
    See STATUS.md #47/#48 + ``KnowledgeGraph`` / ``VectorStore``
    hard-refuse guards from #51.
    """
    env = os.environ.get("TINYASSETS_UNIVERSE")
    if env:
        return Path(env).expanduser().resolve()
    from tinyassets.storage import data_dir
    return data_dir() / "default-universe"





def _structured_return(raw):
    """Wrap an MCP tool result so FastMCP populates ``structured_content``.

    ChatGPT (OpenAI Apps SDK) wedges on substrate-changing tool calls when
    the response carries only ``content`` (text) without ``structuredContent``
    (typed dict) + ``_meta`` annotations. Claude tolerates either shape.

    Mirrors the helpers in tinyassets.universe_server applied via PR #493 + #495.
    """
    import json as _json
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, list):
        return {"result": raw}
    if isinstance(raw, str):
        try:
            parsed = _json.loads(raw)
        except (_json.JSONDecodeError, ValueError):
            return {"text": raw}
        if isinstance(parsed, dict):
            return parsed
        return {"result": parsed}
    return {"result": raw}


def _register_structured_tool(fn, *, server, title=None, tags=None, annotations=None):
    """Register an MCP adapter without changing the direct Python API."""

    @wraps(fn)
    def _tool(*args, **kwargs):
        return _structured_return(fn(*args, **kwargs))

    _tool.__name__ = f"_mcp_{fn.__name__}"
    _tool.__signature__ = signature(fn).replace(return_annotation=dict)
    kwargs = {"name": fn.__name__, "output_schema": None}
    if title is not None:
        kwargs["title"] = title
    if tags is not None:
        kwargs["tags"] = tags
    if annotations is not None:
        kwargs["annotations"] = annotations
    return server.tool(**kwargs)(_tool)


def get_status() -> str:
    """Read the daemon's current status including phase, word count, and accept rate.

    Call this first to orient yourself.
    """
    status_path = _universe_dir() / "status.json"
    if not status_path.exists():
        return "No status.json found. The daemon may not be running."
    try:
        return status_path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading status.json: {e}"


_mcp_get_status = _register_structured_tool(
    get_status,
    server=mcp,
    tags={'status', 'monitoring'},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True, openWorldHint=False),
)


def add_note(text: str, category: str = "direction") -> str:
    """Add a note that the daemon reads at each scene boundary.

    Notes are the primary feedback mechanism — use them to steer the story,
    protect elements you like, flag concerns, or record observations.

    Args:
        text: The note text (what you want the daemon to know).
        category: One of 'direction' (steer the story), 'protect' (preserve
            something), 'concern' (flag a problem), 'observation' (neutral
            note), or 'error' (report a mistake).
    """
    valid_categories = {"direction", "protect", "concern", "observation", "error"}
    if category not in valid_categories:
        category = "direction"
    try:
        from tinyassets.notes import add_note as add_note_to_store

        _universe_dir().mkdir(parents=True, exist_ok=True)
        note = add_note_to_store(
            _universe_dir(), source="user", text=text, category=category,
        )
        return f"Note added (id={note.id[:8]}..., category={category})."
    except Exception as e:
        return f"Error adding note: {e}"


_mcp_add_note = _register_structured_tool(
    add_note,
    server=mcp,
    tags={'notes', 'steering', 'direction'},
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False),
)


def steer(directive: str, category: str = "direction") -> str:
    """Backward-compatible alias for ``add_note``."""
    return add_note(directive, category)


def get_premise() -> str:
    """Read the current story premise from PROGRAM.md or soul.md.

    The premise seeds the daemon's creative direction.
    """
    universe_dir = _universe_dir()
    premise = read_legacy_premise(universe_dir).strip()
    if premise:
        return premise
    premise = premise_from_soul(universe_dir).strip()
    if premise:
        return premise
    return "No premise found. Use set_premise() to create one."


_mcp_get_premise = _register_structured_tool(
    get_premise,
    server=mcp,
    tags={'premise', 'story'},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)


def set_premise(text: str) -> str:
    """Write or overwrite the story premise as soul.md plus PROGRAM.md.

    The daemon reads this at startup to seed the story direction.
    Overwrites any existing premise — use get_premise first if you want to
    edit rather than replace.
    """
    universe_dir = _universe_dir()
    try:
        from tinyassets.role_center_admission import ensure_center_dir

        ensure_center_dir(universe_dir)
        write_universe_soul(
            universe_dir, purpose=text, lineage="created-from-premise",
        )
        legacy_premise_path(universe_dir).write_text(text, encoding="utf-8")
        return "soul.md updated."
    except OSError as e:
        return f"Error writing premise: {e}"


_mcp_set_premise = _register_structured_tool(
    set_premise,
    server=mcp,
    tags={'premise', 'story'},
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=True, idempotentHint=True),
)


def get_progress() -> str:
    """Read the human-readable progress summary.

    Includes story outline, word counts, and current chapter status.
    """
    progress_path = _universe_dir() / "progress.md"
    if not progress_path.exists():
        return "No progress.md found. The daemon may not have started writing yet."
    try:
        return progress_path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading progress.md: {e}"


_mcp_get_progress = _register_structured_tool(
    get_progress,
    server=mcp,
    tags={'progress', 'monitoring'},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)


def get_work_targets() -> str:
    """Read the durable work target registry.

    Shows what the daemon is working on, what's queued, and lifecycle state.
    """
    path = _universe_dir() / "work_targets.json"
    if not path.exists():
        return "No work_targets.json found."
    try:
        return path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading work_targets.json: {e}"


_mcp_get_work_targets = _register_structured_tool(
    get_work_targets,
    server=mcp,
    tags={'work-targets', 'planning'},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)


def get_review_state() -> str:
    """Read the latest review-state snapshot including daemon phase, word count, and accept rate."""
    status_path = _universe_dir() / "status.json"
    if not status_path.exists():
        return "No status.json found."
    try:
        return status_path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading status.json: {e}"


_mcp_get_review_state = _register_structured_tool(
    get_review_state,
    server=mcp,
    tags={'review', 'monitoring'},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)


def get_chapter(book: int, chapter: int) -> str:
    """Read a completed chapter from the daemon's output.

    Args:
        book: Book number (e.g. 1).
        chapter: Chapter number (e.g. 3). Returns the full chapter markdown.
    """
    chapter_path = _universe_dir() / "output" / f"book-{book}" / f"chapter-{chapter:02d}.md"
    if not chapter_path.exists():
        return f"Chapter file not found: book-{book}/chapter-{chapter:02d}.md"
    try:
        return chapter_path.read_text(encoding="utf-8")
    except OSError as e:
        return f"Error reading chapter file: {e}"


_mcp_get_chapter = _register_structured_tool(
    get_chapter,
    server=mcp,
    tags={'output', 'reading'},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)


def get_activity(lines: int = 20) -> str:
    """Read the most recent lines from the daemon's activity log.

    Shows scene completions, reviews, and errors.

    Args:
        lines: Number of lines to return (default 20, max 200).
    """
    log_path = _universe_dir() / "activity.log"
    if not log_path.exists():
        return "No activity.log found."
    try:
        content = log_path.read_text(encoding="utf-8")
        all_lines = content.strip().splitlines()
        tail = all_lines[-lines:] if len(all_lines) > lines else all_lines
        return "\n".join(tail)
    except OSError as e:
        return f"Error reading activity.log: {e}"


_mcp_get_activity = _register_structured_tool(
    get_activity,
    server=mcp,
    tags={'activity', 'monitoring'},
    annotations=ToolAnnotations(readOnlyHint=True, idempotentHint=True),
)


def pause() -> str:
    """Pause the daemon at the next scene boundary.

    The daemon checks for a pause signal between scenes.
    """
    pause_path = _universe_dir() / ".pause"
    try:
        _universe_dir().mkdir(parents=True, exist_ok=True)
        pause_path.write_text(
            datetime.now(timezone.utc).isoformat(), encoding="utf-8",
        )
        return "Pause signal written. The daemon will pause at the next scene boundary."
    except OSError as e:
        return f"Error writing pause signal: {e}"


_mcp_pause = _register_structured_tool(
    pause,
    server=mcp,
    tags={'control', 'daemon'},
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)


def resume() -> str:
    """Resume a paused daemon by removing the pause signal."""
    pause_path = _universe_dir() / ".pause"
    if not pause_path.exists():
        return "Daemon is not paused (no .pause file found)."
    try:
        pause_path.unlink()
        return "Pause signal removed. The daemon will resume."
    except OSError as e:
        return f"Error removing pause signal: {e}"


_mcp_resume = _register_structured_tool(
    resume,
    server=mcp,
    tags={'control', 'daemon'},
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)


def add_canon(filename: str, content: str) -> str:
    """Add a reference document to the canon/ directory for the daemon to ingest.

    Canon files provide worldbuilding context — character sheets, maps, lore
    documents, timelines, style guides, or any reference material the daemon
    should know about.

    Args:
        filename: Name for the file (e.g. "characters.md", "magic-system.md").
        content: File content to write.
    """
    canon_dir = _universe_dir() / "canon"
    # Sanitize filename to prevent path traversal
    safe_name = Path(filename).name
    if not safe_name:
        return "Invalid filename."
    try:
        canon_dir.mkdir(parents=True, exist_ok=True)
        target = canon_dir / safe_name
        target.write_text(content, encoding="utf-8")
        return f"Written {safe_name} to canon/."
    except OSError as e:
        return f"Error writing to canon/: {e}"


_mcp_add_canon = _register_structured_tool(
    add_canon,
    server=mcp,
    tags={'canon', 'worldbuilding'},
    annotations=ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True),
)


def main() -> None:
    """Run the MCP server."""
    from tinyassets.scoped_reset import prepare_service_writer_barrier
    from tinyassets.storage import data_dir

    writer_barrier = prepare_service_writer_barrier(data_dir())
    try:
        mcp.run()
    finally:
        writer_barrier.release()


if __name__ == "__main__":
    main()
