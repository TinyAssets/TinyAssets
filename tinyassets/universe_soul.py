"""Domain-neutral command center soul profile helpers.

PR-139 slice 3 keeps the old ``PROGRAM.md`` premise file as a compatibility
mirror while introducing ``soul.md`` as the durable universe-intent artifact.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, replace
from pathlib import Path

from tinyassets.universe_files import write_data_path

SOUL_FILENAME = "soul.md"
SOUL_VERSIONS_DIR = "soul_versions"
LEGACY_PREMISE_FILENAME = "PROGRAM.md"
SOUL_SCHEMA_VERSION = 1
DEFAULT_DOMAIN_SHAPE = "general"
DEFAULT_EDIT_AUTHORITY = "soul.edit"
NO_LOOP_DECLARED = ""
NO_LOOP_MARKER = "_None recorded._"


@dataclass(frozen=True)
class UniverseSoul:
    schema_version: int = SOUL_SCHEMA_VERSION
    # Persona identity — the universe's name as its embodied projection
    # speaks it (e.g. "Tiny"). Empty = no name declared yet.
    name: str = ""
    purpose: str = ""
    why: str = ""
    hard_lines: tuple[str, ...] = ()
    soft_preferences: tuple[str, ...] = ()
    open_to_contributors: tuple[str, ...] = ()
    domain_shape: str = DEFAULT_DOMAIN_SHAPE
    lineage: str = "template"
    edit_authority: str = DEFAULT_EDIT_AUTHORITY
    loop_branch_def_id: str = NO_LOOP_DECLARED
    # Each entry is a "<sink>:<destination>" grant naming a real-world hand
    # this universe's founder authorizes (e.g. "authenticated_external_call:github").
    # Empty = nothing declared (transitional: effectors fall through to the
    # connection + consent gates until the soul-authority cutover).
    effect_authority: tuple[str, ...] = ()

    def summary(self) -> dict[str, object]:
        return {
            "path": SOUL_FILENAME,
            "schema_version": self.schema_version,
            "name": self.name,
            "purpose": self.purpose,
            "domain_shape": self.domain_shape,
            "lineage": self.lineage,
            "edit_authority": self.edit_authority,
            "loop_branch_def_id": self.loop_branch_def_id,
            "effect_authority": list(self.effect_authority),
            "versions_dir": SOUL_VERSIONS_DIR,
        }


@dataclass(frozen=True)
class PinnedUniverseSoul:
    soul: UniverseSoul
    content: str
    version_id: str
    content_sha256: str

    def context(self, *, max_chars: int = 4000) -> dict[str, object]:
        content = self.content[:max_chars].rstrip()
        truncated = len(self.content) > max_chars
        return {
            "path": SOUL_FILENAME,
            "version_id": self.version_id,
            "content_sha256": self.content_sha256,
            "schema_version": self.soul.schema_version,
            "name": self.soul.name,
            "purpose": self.soul.purpose,
            "why": self.soul.why,
            "hard_lines": list(self.soul.hard_lines),
            "soft_preferences": list(self.soul.soft_preferences),
            "open_to_contributors": list(self.soul.open_to_contributors),
            "domain_shape": self.soul.domain_shape,
            "lineage": self.soul.lineage,
            "edit_authority": self.soul.edit_authority,
            "loop_branch_def_id": self.soul.loop_branch_def_id,
            "effect_authority": list(self.soul.effect_authority),
            "identity_boundary": (
                "Command center soul guides this context only; it does not change "
                "the actor identity or user memory scope."
            ),
            "content": content,
            "truncated": truncated,
        }


def soul_path(universe_dir: Path) -> Path:
    return universe_dir / SOUL_FILENAME


def legacy_premise_path(universe_dir: Path) -> Path:
    return universe_dir / LEGACY_PREMISE_FILENAME


def has_soul(universe_dir: Path) -> bool:
    return soul_path(universe_dir).is_file()


def render_soul_markdown(soul: UniverseSoul) -> str:
    return "\n".join([
        "# Universe Soul",
        "",
        f"- Schema version: {soul.schema_version}",
        f"- Name: {soul.name or NO_LOOP_MARKER}",
        f"- Domain shape: {soul.domain_shape}",
        f"- Lineage: {soul.lineage}",
        f"- Edit authority: {soul.edit_authority}",
        f"- Loop branch: {soul.loop_branch_def_id or NO_LOOP_MARKER}",
        "",
        "## Purpose",
        "",
        soul.purpose.strip(),
        "",
        "## Why",
        "",
        soul.why.strip(),
        "",
        "## Hard Lines",
        "",
        _render_list(soul.hard_lines),
        "",
        "## Soft Preferences",
        "",
        _render_list(soul.soft_preferences),
        "",
        "## Open To Contributors",
        "",
        _render_list(soul.open_to_contributors),
        "",
        "## Edit Authority",
        "",
        soul.edit_authority.strip() or DEFAULT_EDIT_AUTHORITY,
        "",
        "## Effect Authority",
        "",
        _render_list(soul.effect_authority),
        "",
    ])


def read_universe_soul(universe_dir: Path, *, strict: bool = False) -> UniverseSoul | None:
    """The parsed soul, or ``None``.

    Through the one safe reader: the agent can write/link in its own folder,
    so soul.md is untrusted and a link must not be followed (universe_files).
    By default any unreadable soul reads as ``None``. ``strict`` (for a
    read-modify-write) returns ``None`` only when soul.md is ABSENT and raises
    on a refusal, so an update never rebuilds the soul from defaults over a
    file it could not read.
    """
    from tinyassets.universe_files import read_universe_text

    try:
        text = read_universe_text(universe_dir, SOUL_FILENAME)
    except FileNotFoundError:
        return None
    except (OSError, UnicodeDecodeError):
        if strict:
            raise
        return None

    return UniverseSoul(
        schema_version=_read_int_meta(text, "Schema version", SOUL_SCHEMA_VERSION),
        name=_read_name_meta(text),
        purpose=_read_section(text, "Purpose"),
        why=_read_section(text, "Why"),
        hard_lines=_read_list_section(text, "Hard Lines"),
        soft_preferences=_read_list_section(text, "Soft Preferences"),
        open_to_contributors=_read_list_section(text, "Open To Contributors"),
        domain_shape=_read_meta(text, "Domain shape", DEFAULT_DOMAIN_SHAPE),
        lineage=_read_meta(text, "Lineage", "unknown"),
        edit_authority=(
            _read_section(text, "Edit Authority")
            or _read_meta(text, "Edit authority", DEFAULT_EDIT_AUTHORITY)
        ),
        loop_branch_def_id=_read_loop_branch_meta(text),
        effect_authority=_read_list_section(text, "Effect Authority"),
    )


def read_pinned_universe_soul(universe_dir: Path) -> PinnedUniverseSoul | None:
    soul = read_universe_soul(universe_dir)
    if soul is None:
        return None

    from tinyassets.universe_files import read_universe_text

    try:
        content = read_universe_text(universe_dir, SOUL_FILENAME)
    except (OSError, UnicodeDecodeError):
        return None

    version_id = _matching_soul_version_id(universe_dir, content)
    if version_id is None:
        digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
        version_id = f"{SOUL_FILENAME}@sha256:{digest[:12]}"

    return PinnedUniverseSoul(
        soul=soul,
        content=content,
        version_id=version_id,
        content_sha256=hashlib.sha256(content.encode("utf-8")).hexdigest(),
    )


def write_universe_soul(
    universe_dir: Path,
    *,
    name: str = "",
    purpose: str = "",
    why: str = "",
    hard_lines: tuple[str, ...] = (),
    soft_preferences: tuple[str, ...] = (),
    open_to_contributors: tuple[str, ...] = (),
    domain_shape: str = DEFAULT_DOMAIN_SHAPE,
    lineage: str = "template",
    edit_authority: str = DEFAULT_EDIT_AUTHORITY,
    loop_branch_def_id: str = NO_LOOP_DECLARED,
    effect_authority: tuple[str, ...] = (),
    clear_loop_branch: bool = False,
) -> UniverseSoul:
    """Write or update a command center soul.

    ``clear_loop_branch`` exists because an empty ``loop_branch_def_id``
    PRESERVES the existing value (every field here treats blank as "leave
    alone"). Without an explicit flag a caller cannot un-declare a loop, and a
    caller that tries gets silent success — reported by cross-family review
    2026-08-05.
    """
    universe_dir.mkdir(parents=True, exist_ok=True)
    # Collapse the persona name to a single line: a multiline name would inject
    # spurious meta lines / corrupt soul.md (Codex review 2026-06-25).
    name = " ".join(name.split())
    existing = read_universe_soul(universe_dir, strict=True)
    if existing is None:
        soul = UniverseSoul(
            name=name,
            purpose=purpose.strip(),
            why=why.strip(),
            hard_lines=tuple(item.strip() for item in hard_lines if item.strip()),
            soft_preferences=tuple(
                item.strip() for item in soft_preferences if item.strip()
            ),
            open_to_contributors=tuple(
                item.strip() for item in open_to_contributors if item.strip()
            ),
            domain_shape=domain_shape.strip() or DEFAULT_DOMAIN_SHAPE,
            lineage=lineage.strip() or "template",
            edit_authority=edit_authority.strip() or DEFAULT_EDIT_AUTHORITY,
            loop_branch_def_id=loop_branch_def_id.strip(),
            effect_authority=tuple(
                item.strip() for item in effect_authority if item.strip()
            ),
        )
    else:
        soul = replace(
            existing,
            name=name or existing.name,
            purpose=purpose.strip() if purpose.strip() else existing.purpose,
            why=why.strip() if why.strip() else existing.why,
            hard_lines=(
                tuple(item.strip() for item in hard_lines if item.strip())
                or existing.hard_lines
            ),
            soft_preferences=(
                tuple(item.strip() for item in soft_preferences if item.strip())
                or existing.soft_preferences
            ),
            open_to_contributors=(
                tuple(item.strip() for item in open_to_contributors if item.strip())
                or existing.open_to_contributors
            ),
            domain_shape=domain_shape.strip() or existing.domain_shape,
            lineage=lineage.strip() or existing.lineage,
            edit_authority=edit_authority.strip() or existing.edit_authority,
            loop_branch_def_id=(
                NO_LOOP_DECLARED
                if clear_loop_branch
                else (loop_branch_def_id.strip() or existing.loop_branch_def_id)
            ),
            effect_authority=(
                tuple(item.strip() for item in effect_authority if item.strip())
                or existing.effect_authority
            ),
        )

    rendered = render_soul_markdown(soul)
    write_data_path(soul_path(universe_dir), rendered)
    _write_soul_version(universe_dir, rendered)
    return soul


def ensure_universe_soul(
    universe_dir: Path,
    *,
    purpose: str = "",
    loop_branch_def_id: str = NO_LOOP_DECLARED,
) -> UniverseSoul:
    existing = read_universe_soul(universe_dir)
    if existing is not None and (
        (existing.purpose or not purpose.strip())
        and (existing.loop_branch_def_id or not loop_branch_def_id.strip())
    ):
        return existing
    return write_universe_soul(
        universe_dir,
        purpose=purpose,
        lineage="created-from-premise" if purpose.strip() else "template",
        loop_branch_def_id=loop_branch_def_id,
    )


def read_legacy_premise(universe_dir: Path) -> str:
    from tinyassets.universe_files import MAX_BRAIN_FILE_BYTES, read_universe_text

    try:
        return read_universe_text(
            universe_dir, LEGACY_PREMISE_FILENAME, max_bytes=MAX_BRAIN_FILE_BYTES,
        )
    except (OSError, UnicodeDecodeError):
        return ""


#: How many of the newest soul snapshots are compared per read. The folder is
#: untrusted input; an unbounded glob-and-read every turn is not.
_MAX_SOUL_VERSIONS_SCANNED = 256
_SOUL_VERSION_NAME = re.compile(r"^[0-9]{4}\.md$")


def _soul_version_names(universe_dir: Path) -> list[str]:
    """Snapshot names in ``soul_versions/``, oldest first, never via a link."""
    from tinyassets.universe_files import list_universe_dir

    try:
        names = list_universe_dir(universe_dir, SOUL_VERSIONS_DIR)
    except OSError:
        return []
    return sorted(name for name in names if _SOUL_VERSION_NAME.match(name))


def premise_from_soul(universe_dir: Path) -> str:
    soul = read_universe_soul(universe_dir)
    return soul.purpose if soul is not None else ""


def loop_branch_from_soul(universe_dir: Path) -> str:
    soul = read_universe_soul(universe_dir)
    return soul.loop_branch_def_id if soul is not None else NO_LOOP_DECLARED


def effect_authority_from_soul(universe_dir: Path) -> tuple[str, ...]:
    """Return the soul-declared effect-authority grants, or () if no soul exists.

    A genuinely ABSENT soul yields () — the authority resolver reads that as
    UNDECLARED. But a soul file that EXISTS and cannot be read is NOT "no soul":
    silently downgrading it to () would turn a real DENY into UNDECLARED and let
    an otherwise-denied effect fire (Codex reject 2026-08-20). ``read_universe_soul``
    swallows both FileNotFoundError and other OSErrors to None, so we distinguish
    them by the file's existence and RAISE on an existing-but-unreadable soul, so
    ``resolve_soul_effect_authority`` fails closed (DENIED). If we cannot even
    confirm absence, we fail closed too.
    """
    soul = read_universe_soul(universe_dir)
    if soul is not None:
        return soul.effect_authority
    # read_universe_soul returned None: distinguish a genuinely ABSENT soul
    # (-> () -> UNDECLARED) from an existing-but-unreadable one (-> raise ->
    # DENIED). Use lstat(), NOT is_file(): is_file() can suppress a metadata
    # error to False (Codex adapt 2026-08-20, Python 3.14.3), wrongly restoring
    # UNDECLARED. lstat() raises FileNotFoundError only when the path is truly
    # gone; ANY other failure — or any existing entry, including a dangling or
    # malformed symlink — means we cannot treat the soul as absent, so we fail
    # closed. Only the genuine-deletion race lands in ().
    try:
        soul_path(universe_dir).lstat()
    except FileNotFoundError:
        return ()
    except OSError:
        pass  # metadata error on an uncertain path -> fail closed below
    raise OSError(
        "soul file exists but could not be read; failing closed for "
        "effect-authority resolution"
    )


def _render_list(items: tuple[str, ...]) -> str:
    if not items:
        return "_None recorded._"
    return "\n".join(f"- {item}" for item in items)


def _write_soul_version(universe_dir: Path, rendered: str) -> None:
    from tinyassets.universe_files import MAX_BRAIN_FILE_BYTES, read_universe_text

    versions_dir = universe_dir / SOUL_VERSIONS_DIR
    versions = _soul_version_names(universe_dir)
    if versions:
        try:
            latest = read_universe_text(
                universe_dir, f"{SOUL_VERSIONS_DIR}/{versions[-1]}",
                max_bytes=MAX_BRAIN_FILE_BYTES,
            )
            if latest == rendered:
                return
        except (OSError, UnicodeDecodeError):
            pass
    next_number = 1
    if versions:
        try:
            next_number = int(versions[-1][:4]) + 1
        except ValueError:
            next_number = len(versions) + 1
    # Exclusive and link-free: never through a planted soul_versions link.
    write_data_path(versions_dir / f"{next_number:04d}.md", rendered, mode="exclusive")


def _matching_soul_version_id(universe_dir: Path, content: str) -> str | None:
    from tinyassets.universe_files import MAX_BRAIN_FILE_BYTES, read_universe_text

    newest = _soul_version_names(universe_dir)[::-1][:_MAX_SOUL_VERSIONS_SCANNED]
    for name in newest:
        try:
            text = read_universe_text(
                universe_dir, f"{SOUL_VERSIONS_DIR}/{name}", max_bytes=MAX_BRAIN_FILE_BYTES,
            )
        except (OSError, UnicodeDecodeError):
            continue
        if text == content:
            return f"{SOUL_VERSIONS_DIR}/{name}"
    return None


def _read_meta(text: str, key: str, default: str) -> str:
    pattern = rf"(?im)^-\s*{re.escape(key)}:\s*(.+?)\s*$"
    match = re.search(pattern, text)
    if not match:
        return default
    return match.group(1).strip() or default


def _read_int_meta(text: str, key: str, default: int) -> int:
    raw = _read_meta(text, key, str(default))
    try:
        return int(raw)
    except ValueError:
        return default


def _read_loop_branch_meta(text: str) -> str:
    raw = _read_meta(text, "Loop branch", NO_LOOP_DECLARED)
    if raw in {NO_LOOP_MARKER, "none", "None", "NONE"}:
        return NO_LOOP_DECLARED
    return raw


def _read_name_meta(text: str) -> str:
    """Read the persona Name meta line. An empty name renders as the
    ``_None recorded._`` placeholder (so the line carries content and the
    next meta value isn't absorbed by the regex); translate it back to ""."""
    raw = _read_meta(text, "Name", "")
    return "" if raw == NO_LOOP_MARKER else raw


def _read_section(text: str, heading: str) -> str:
    pattern = rf"(?ims)^##\s+{re.escape(heading)}\s*$\n(?P<body>.*?)(?=^##\s+|\Z)"
    match = re.search(pattern, text)
    if not match:
        return ""
    body = match.group("body").strip()
    return "" if body == "_None recorded._" else body


def _read_list_section(text: str, heading: str) -> tuple[str, ...]:
    body = _read_section(text, heading)
    if not body:
        return ()
    items: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped == "_None recorded._":
            continue
        if stripped.startswith("- "):
            items.append(stripped[2:].strip())
        else:
            items.append(stripped)
    return tuple(item for item in items if item)
