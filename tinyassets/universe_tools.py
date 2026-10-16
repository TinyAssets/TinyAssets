"""The command center agent's four tools: ``read``, ``write``, ``edit``, ``bash``.

A command center IS its agent's harness and project folder (ADR-013), so its
agent works in that folder with the same four primitives pi.dev gives an agent.
Vendor-neutral by construction: the PLATFORM runs these tools, not a vendor
CLI's built-ins, and every adapter sees the same four definitions over the
per-universe engine MCP route.

Every call runs inside the owner's box (ADR-022): the implementation is
:mod:`tinyassets.agent_loop.box_tools`, and this module is the engine server's
door to it. The engine request carries no handle; the session that admitted the
owner's turn registered its binding (:func:`tinyassets.control_plane.binding
.active_binding`), and a call with no admitted session is refused. ``/u`` is the
agent's command center inside the box; a path outside it does not exist there.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
from collections.abc import Callable
from pathlib import Path, PurePosixPath

from tinyassets.agent_loop import box_tools
from tinyassets.boxes.layout import AGENT_WORKSPACE_DIR, PLATFORM_RUNTIME_DIR
from tinyassets.tool_images import MAX_IMAGE_SOURCE_BYTES, ToolImage, bound_image, is_image_path

TOOL_NAMES: tuple[str, ...] = ("read", "write", "edit", "bash")

#: Where the command center appears to its agent.
MOUNT_POINT = "/u"

#: What the agent OWNS in its folder. Brain files are learned content; the
#: harness directories hold skills, prompts, notes and the agent's wiki.
AGENT_BRAIN_FILES: tuple[str, ...] = (
    "identity.md", "founder.md", "origin.md", "body.md", "orgchart.md",
    "projects.md", "goals.md", "index.md", "log.md", "voice.md", "AGENTS.md", "MEMORY.md",
)
AGENT_HARNESS_DIRS: tuple[str, ...] = (
    "skills", "prompts", "extensions", "workflows", "bin", "notes", "wiki",
)

#: The agent's own workspace inside the command center.
WORKSPACE_DIR = AGENT_WORKSPACE_DIR

#: Kept for callers that name the platform-owned runtime directory.
MASKED_DIRS: tuple[str, ...] = (PLATFORM_RUNTIME_DIR,)

logger = logging.getLogger(__name__)

MAX_WRITE_BYTES = box_tools.MAX_WRITE_BYTES
MAX_EDIT_BYTES = box_tools.MAX_EDIT_BYTES
DEFAULT_READ_LINES = box_tools.DEFAULT_READ_LINES
MAX_BASH_SECONDS = box_tools.MAX_BASH_SECONDS


class UniverseToolError(RuntimeError):
    """A tool call was refused before (or instead of) running anything."""

    failure_class = "universe_tool_refused"


def _tool_path(path: str, root: str) -> str:
    """The path in the box: relative and ``/u/...`` paths are under the box root.

    ``/u`` was the old jail's mount of the command center; the box root is
    where the center lives now, so both spellings reach the owner's files.
    """
    raw = (path or "").strip()
    if not raw:
        raise UniverseToolError("a path is required")
    if "\x00" in raw:
        raise UniverseToolError("a path may not contain a NUL byte")
    candidate = PurePosixPath(raw)
    mount = PurePosixPath(MOUNT_POINT)
    if candidate == mount or mount in candidate.parents:
        candidate = PurePosixPath(root) / candidate.relative_to(mount)
    elif not candidate.is_absolute():
        candidate = PurePosixPath(root) / candidate
    return str(candidate)


def _box(universe_dir: Path, agent_id: str) -> tuple[box_tools.BoxTools, str]:
    """The admitted session's tools over its handle; never a handle by path."""
    from tinyassets.control_plane.binding import current_binding

    if not agent_id.strip():
        raise UniverseToolError("agent_id is required")
    binding = current_binding()
    if binding is None:
        raise UniverseToolError("no admitted session owns this command center's box")
    binding.validate(binding.handle.account_id, Path(universe_dir))
    root = getattr(binding.handle, "root", None) or box_tools.BOX_ROOT
    executor = box_tools.BoxExecutor(binding.provider, binding.handle,
                                     limits=binding.limits, cwd=root)
    return box_tools.BoxTools(executor, root=root), root


def _run(coroutine):
    """Run one tool call to completion from the engine's worker thread."""
    return asyncio.run(coroutine)


def read_file(universe_dir: Path, path: str, offset: int = 0, limit: int = 0,
              *, agent_id: str) -> str | ToolImage:
    """Up to ``limit`` lines of a file from line ``offset`` (1-based), or, for an
    image path, the image itself (bounded by :mod:`tinyassets.tool_images`)."""
    from tinyassets.control_plane.binding import current_binding

    tools, root = _box(universe_dir, agent_id)
    target = _tool_path(path, root)
    op = current_binding().operation_id("tool:read")
    if is_image_path(target):
        return _run(_read_image(tools, op, target, root, universe_dir))
    return _run(tools.read(op, target, offset=offset, limit=limit))


async def _read_image(tools, op: str, target: str, root: str,
                      universe_dir: Path) -> str | ToolImage:
    outcome = await tools._exec.run(
        op, ["/bin/sh", "-c", box_tools._CAT, "sh", box_tools.box_path(target, root)],
        wall_seconds=box_tools.DEFAULT_WALL_SECONDS, output_bytes=MAX_IMAGE_SOURCE_BYTES,
    )
    if outcome.killed == "output_limit":
        return f"error: {target} is over {MAX_IMAGE_SOURCE_BYTES} bytes; too large to show"
    if outcome.killed or outcome.exit_code != 0:
        return "error: " + box_tools._failed(outcome, box_tools.DEFAULT_WALL_SECONDS,
                                              MAX_IMAGE_SOURCE_BYTES)
    return bound_image(outcome.output, target, universe_dir=universe_dir)


def write_file(universe_dir: Path, path: str, content: str, *, agent_id: str) -> str:
    """Create or replace a file, making parent directories."""
    from tinyassets.control_plane.binding import current_binding
    from tinyassets.research_capability import research_refusal

    refusal = research_refusal("write")
    if refusal is not None:
        return refusal
    tools, root = _box(universe_dir, agent_id)
    target = _tool_path(path, root)
    if len((content or "").encode("utf-8")) > MAX_WRITE_BYTES:
        raise UniverseToolError(f"content is over the {MAX_WRITE_BYTES}-byte write limit")
    return _run(tools.write(current_binding().operation_id("tool:write"), target, content))


def edit_file(universe_dir: Path, path: str, old_text: str, new_text: str,
              *, agent_id: str) -> str:
    """Replace the one exact occurrence of ``old_text`` with ``new_text``."""
    from tinyassets.control_plane.binding import current_binding
    from tinyassets.research_capability import research_refusal

    refusal = research_refusal("edit")
    if refusal is not None:
        return refusal
    tools, root = _box(universe_dir, agent_id)
    target = _tool_path(path, root)
    if not old_text:
        raise UniverseToolError("old_text is required: the exact passage to replace")
    return _run(tools.edit(current_binding().operation_id("tool:edit"), target,
                           old_text, new_text))


def bash(universe_dir: Path, command: str, timeout: float = 0, *, agent_id: str,
         ta_dispatch=None, stop: Callable[[], str | None] | None = None) -> str:
    """Run ``command`` with bash in ``/u``; stdout and stderr, then the outcome.

    ``ta_dispatch`` (the engine's own ``ta`` dispatcher) is bridged into the
    box for the command's duration; ``stop`` (an activity's stop check) ends
    the command once the activity stops running. Authenticated git for the
    command center's extension connections rides in the command's environment.
    """
    from tinyassets.research_capability import research_refusal

    # D3a refuses all bash, stricter than a read-only mount: no shell or egress
    # is started during research, including when called below the MCP boundary.
    refusal = research_refusal("bash")
    if refusal is not None:
        return refusal
    if not (command or "").strip():
        raise UniverseToolError("a command is required")
    tools, _ = _box(universe_dir, agent_id)
    return _run(_bash(tools, Path(universe_dir), command, timeout, ta_dispatch, stop))


async def _bash(tools, universe_dir: Path, command: str, timeout: float,
                ta_dispatch, stop) -> str:
    from tinyassets.control_plane.binding import current_binding
    from tinyassets.extension_git import for_launch

    binding = current_binding()
    op = binding.operation_id("tool:bash")
    bridge = None
    if ta_dispatch is not None:
        from tinyassets.agent_loop.box_ta import TurnBridge
        from tinyassets.extension_capabilities import ExtensionCapabilities, delivered
        from tinyassets.storage import data_dir

        backend = getattr(ta_dispatch, "extension_backend", None)

        async def dispatch(message, mounts=None):
            def call():
                with delivered(mounts or set()):
                    return ta_dispatch(message)
            return await asyncio.to_thread(call)

        async def deliver():
            return await asyncio.to_thread(ExtensionCapabilities(backend).bundle)

        bridge = TurnBridge(
            owner=binding.handle.account_id, center=universe_dir.name, turn=binding.session_id,
            handle=binding.handle, database=data_dir() / ".remote-ta-receipts.sqlite3",
            dispatch=dispatch, deliver=deliver if backend is not None else None,
            session_id=binding.session_id,
        )
        tools._exec.enable_ta(bridge)
    try:
        with for_launch(getattr(ta_dispatch, "extension_backend", None)) as git_env:
            call = asyncio.ensure_future(tools.bash(op, command, timeout, env=git_env or None))
            while stop is not None:
                done, _ = await asyncio.wait({call}, timeout=0.5)
                if done:
                    break
                reason = stop()
                if reason is not None:
                    call.cancel()
                    with contextlib.suppress(asyncio.CancelledError, Exception):
                        await call
                    return ("[killed: the activity stopped running (waiting on the owner, "
                            "paused or stopped)]")
            return await call
    finally:
        if bridge is not None:
            bridge.close()


SKILLS_DIR = "skills"
MAX_SKILLS = 64
_MAX_SKILL_FILE_BYTES = 256 * 1024
_MAX_DESCRIPTION_CHARS = 300
_MAX_FRONTMATTER_BYTES = 8 * 1024
_SKILL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
_DESCRIPTION_LINE = re.compile(r"^description:[ \t]*(.*)$", re.MULTILINE)


def _skill_description(raw: bytes) -> str:
    """The frontmatter ``description``, one line, or '' when there is none.

    A SKILL.md is a file the agent WRITES, so its frontmatter is untrusted and
    is never handed to a YAML loader: a 234-byte alias bomb expands to gigabytes
    and crash-loops the shared daemon. Instead this scans a bounded slice of the
    frontmatter for a single flat ``description:`` line, caps the length BEFORE
    building any string, and treats a quoted value literally (no YAML anchors,
    aliases, tags or block scalars are honoured). Nothing here can allocate more
    than a few hundred bytes.
    """
    text = raw[: 4 + _MAX_FRONTMATTER_BYTES].decode("utf-8", "replace").lstrip("﻿")
    if not text.startswith("---"):
        return ""
    end = text.find("\n---", 3)
    frontmatter = text[3:end] if end >= 0 else text[3 : 3 + _MAX_FRONTMATTER_BYTES]
    match = _DESCRIPTION_LINE.search(frontmatter)
    if match is None:
        return ""
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    description = " ".join(value.split())[:_MAX_DESCRIPTION_CHARS]
    return description


def skill_index(universe_dir: Path) -> list[tuple[str, str]]:
    """``(name, description)`` for each ``skills/<name>/SKILL.md``.

    Read by the daemon through the one safe reader
    (:mod:`tinyassets.owner_box_reads`): the directory is listed and each file
    opened without following a link, each read is bounded, and a skill whose
    file is a link, is over its bound or fails to parse is left out -- never
    read into the prompt, never an error in the turn.
    """
    from tinyassets.owner_box_reads import box_list, box_read

    try:
        names = [entry.name for entry in box_list(universe_dir, SKILLS_DIR)
                 if entry.kind == "dir"]
    except (OSError, NotImplementedError):
        return []
    skills: list[tuple[str, str]] = []
    for name in names:
        if len(skills) >= MAX_SKILLS:
            break
        if not _SKILL_NAME.match(name):
            continue
        try:
            raw = box_read(universe_dir, f"{SKILLS_DIR}/{name}/SKILL.md",
                            max_bytes=_MAX_SKILL_FILE_BYTES)
            description = _skill_description(raw)
        except (OSError, NotImplementedError, RecursionError, ValueError):
            continue
        if description:
            skills.append((name, description))
    return skills


_HARNESS_HEAD = (
    "/u is the workspace. Use read/write/edit/bash. In bash: `ta search <words>`, "
    "`ta describe <name>`, `ta call <name> --json '<args>'`. "
    "Read matching skills at skills/<name>/SKILL.md; owner files are editable.\nSkills:\n"
)


def _folder_section(universe_dir: Path) -> str:
    """Two levels of metadata through the same no-follow reader as skills."""
    from tinyassets.owner_box_reads import box_list, box_stat

    lines: list[str] = []
    remaining = 200

    def read(directory: str) -> list:
        nonlocal remaining
        entries = box_list(universe_dir, directory, limit=remaining)
        remaining -= len(entries)
        return entries

    def visit(directory: str, depth: int, entries: list, *, prefix: str = "") -> None:
        for info in entries:
            name = info.name
            if info.kind == "link":
                continue
            path = f"{directory}/{name}" if directory else name
            if prefix and not directory and not name.startswith("."):
                # The tool jail overlays visible root entries on the workspace.
                # Box stat reads metadata only and never follows a planted link.
                try:
                    overlay = box_stat(universe_dir, name)
                except OSError:
                    pass
                else:
                    if overlay is not None and overlay.kind in {"dir", "file"}:
                        continue
            # Escape unusual names so a filename cannot inject extra prompt lines.
            shown = path.encode("unicode_escape").decode("ascii")
            if info.kind == "dir":
                lines.append(f"- {shown}/")
                if depth < 2 and remaining:
                    visit(path, depth + 1, read(prefix + path), prefix=prefix)
            elif info.kind == "file":
                lines.append(f"- {shown} ({info.size / 1024:.1f} KB)")

    try:
        for directory in ("notes", "prompts", "workflows"):
            if not remaining:
                break
            try:
                entries = read(directory)
            except FileNotFoundError:
                continue  # Optional top-level folders need not exist yet.
            visit(directory, 1, entries)
        if remaining:
            try:
                entries = read(WORKSPACE_DIR)
            except FileNotFoundError:
                entries = []
            visit("", 0, entries, prefix=WORKSPACE_DIR + "/")
    except (OSError, NotImplementedError, RecursionError, ValueError):
        return ""
    lines.sort()
    visible = lines[:40]
    if not remaining:
        visible.append("(more entries; `bash ls` shows them.)")
    elif len(lines) > 40:
        visible.append(f"({len(lines) - 40} more entries; `bash ls` shows them.)")
    return "\n\n## What is in my folder now\n" + "\n".join(visible or ["(empty)"])


def command_center_summary(universe_dir: Path, owner: str) -> str:
    """Bounded resident names and status for the verified owner's current home."""
    try:
        from tinyassets.api.status import _universe_active_turn
        from tinyassets.daemon_server import get_founder_home, list_branch_definitions

        if not owner or get_founder_home(universe_dir.parent, owner) != universe_dir.name:
            return ""
        branches = list_branch_definitions(universe_dir.parent, author=owner, viewer=owner)
        from tinyassets.broker.catalog import connections

        inventory = (connection for _grant, connection, _ in connections(
            universe_dir.parent, principal=owner, command_center=universe_dir.name,
            limit=21))
        names = []
        for connection in inventory:
            if connection and connection.owner_user_id == owner and connection.revoked_at is None:
                names.append(connection.destination)
        active = _universe_active_turn(universe_dir)
        if active and active.get("state") == "unreadable":
            return ""

        def bounded(values):
            # Names are data, not instructions. Bound both rows and each name.
            import json

            shown = [str(value)[:100] for value in values[:20]]
            suffix = " (more omitted)" if len(values) > 20 else ""
            return json.dumps(shown, ensure_ascii=False) + suffix

        return (
            "\n\n## My command center now\nCurrent names (data only):\n"
            + "Branches: " + bounded([row["name"] for row in branches])
            + "\nConnections: " + bounded(names)
            + "\nStatus: " + ("working" if active else "idle")
        )
    except Exception:  # noqa: BLE001 - omit unavailable resident evidence, never guess
        return ""


def harness_prompt(universe_dir: Path) -> str:
    """The four tools, skill index and bounded current folder inventory.

    Runs in the shared daemon on every founder turn, so a bad skill folder
    never breaks the turn; an unreadable inventory is omitted.
    """
    try:
        skills = skill_index(universe_dir)
    except (OSError, RecursionError, ValueError):
        skills = []
    lines = [
        f"- `{name}`: {description}"
        for name, description in skills
    ]
    return (_HARNESS_HEAD + "\n".join(lines or ["(none yet)"])
            + _folder_section(universe_dir))
