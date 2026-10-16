"""Canon I/O: box-only reads and enumeration; guarded owner writes."""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from tinyassets.ingestion.canon_names import resolve_within_canon
from tinyassets.universe_files import (
    MAX_PLATFORM_FILE_BYTES,
    write_data_path,
)

logger = logging.getLogger(__name__)

#: The canon folder's name inside a command center. Every function here takes
#: ``canon_dir`` as a parameter, so this module did not previously need the
#: name -- but its callers all spell it as a bare literal
#: (``tinyassets/api/universe.py``, ``tinyassets/work_targets.py``), and
#: ``command_center_packages`` has to refuse the folder by name to keep uploads
#: out of a published package. Named here, beside the I/O that owns it, so
#: there is one place to change and one place to find.
CANON_DIRNAME = "canon"

__all__ = [
    "CANON_DIRNAME",
    "safe_canon_path",
    "iter_canon_files",
    "read_canon_text",
    "read_canon_bytes",
    "write_canon_text",
    "write_canon_bytes",
]


def safe_canon_path(canon_dir: Path, name: str, kind: str = "path") -> Path:
    """Resolve ``name`` under ``canon_dir`` and confirm containment.

    Thin alias over :func:`resolve_within_canon` so callers import a single
    canon-I/O surface. Raises :class:`ValueError` when ``name`` escapes the
    resolved canon directory (``..`` traversal or symlinked target outside).
    """
    return resolve_within_canon(canon_dir, name, kind=kind)


def iter_canon_files(
    canon_dir: Path,
    suffix: str | tuple[str, ...] | None = None,
    *,
    subdir: str | None = None,
    recursive: bool = False,
    include_hidden: bool = True,
    kind: str = "existing file",
    owner: str | None = None,
) -> Iterator[Path]:
    """Yield logical canon paths from the owner's box, never host metadata.

    Internal pipeline callers inherit their admitted session owner. Platform
    callers outside a session must supply the recorded owner explicitly.
    Links are skipped, including linked subdirectories; results are sorted.
    """
    from tinyassets.owner_box_reads import binding_owner, box_list, box_stat

    owner = owner if owner is not None else binding_owner(canon_dir.parent)
    try:
        start = _canon_relative(subdir) if subdir is not None else ""
    except ValueError:
        logger.warning("Skipping invalid canon subdir: %s", subdir)
        return
    suffixes = ((suffix.lower(),) if isinstance(suffix, str)
                else tuple(s.lower() for s in suffix) if suffix is not None else None)
    pending = [start]
    contained = []
    while pending:
        directory = pending.pop()
        rel = f"canon/{directory}" if directory else "canon"
        info = box_stat(canon_dir.parent, rel, owner=owner)
        if info is None or info.kind != "dir":
            continue
        try:
            entries = box_list(canon_dir.parent, rel, owner=owner)
        except FileNotFoundError:
            continue
        for entry in entries:
            if not include_hidden and entry.name.startswith("."):
                continue
            name = f"{directory}/{entry.name}" if directory else entry.name
            _canon_relative(name)
            if entry.kind == "dir" and recursive:
                pending.append(name)
            elif entry.kind == "file":
                path = canon_dir / name
                if suffixes is None or path.suffix.lower() in suffixes:
                    contained.append(path)
    yield from sorted(contained)


def _canon_relative(name: str) -> str:
    """Validate a canon-relative name without resolving a host path."""
    from tinyassets.boxes.provider import BoxPathError, box_relpath

    try:
        if not name:
            raise ValueError("empty canon path")
        return box_relpath(f"/cc/{name}")
    except BoxPathError as exc:
        raise ValueError(f"invalid canon path: {name!r}") from exc


def read_canon_text(
    canon_dir: Path,
    name: str,
    *,
    encoding: str = "utf-8",
    kind: str = "existing file",
    owner: str | None = None,
    **kwargs: object,
) -> str:
    """Resolve ``name`` under ``canon_dir`` then ``read_text``.

    Raises :class:`ValueError` if ``name`` escapes containment (before any I/O).
    """
    data = read_canon_bytes(canon_dir, name, kind=kind, owner=owner)
    text = data.decode(encoding, **kwargs)  # type: ignore[arg-type]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_canon_bytes(
    canon_dir: Path,
    name: str,
    *,
    kind: str = "existing file",
    owner: str | None = None,
) -> bytes:
    """Read bounded bytes from the box; validate the logical canon-relative name."""
    from tinyassets.owner_box_reads import binding_owner, box_read

    relative = _canon_relative(name)
    owner = owner if owner is not None else binding_owner(canon_dir.parent)
    return box_read(canon_dir.parent, f"canon/{relative}", owner=owner,
                    max_bytes=MAX_PLATFORM_FILE_BYTES)


def write_canon_text(
    canon_dir: Path,
    name: str,
    data: str,
    *,
    encoding: str = "utf-8",
    kind: str = "filename",
) -> Path:
    """Resolve ``name`` under ``canon_dir`` then ``write_text``.

    Raises :class:`ValueError` if ``name`` escapes containment (before the
    write executes), so an LLM- or signal-supplied filename can never clobber
    a file outside the canon sandbox. Returns the resolved path written.
    """
    return write_canon_bytes(canon_dir, name, data.encode(encoding), kind=kind)


def write_canon_bytes(
    canon_dir: Path,
    name: str,
    data: bytes,
    *,
    kind: str = "filename",
) -> Path:
    """Check ``name`` under ``canon_dir``, then write it with no link followed.

    The write goes to the LEXICAL path through
    :func:`tinyassets.universe_files.write_data_path` (temp + rename in a
    directory opened link-free), never through the resolved one.
    """
    path = resolve_within_canon(canon_dir, name, kind=kind)
    write_data_path(canon_dir / name, data)
    return path
