"""Guarded canon-directory I/O chokepoint.

Every canon read, write, or enumeration must flow through this module so
containment is enforced in exactly one place. The containment primitive is
:func:`tinyassets.ingestion.canon_names.resolve_within_canon`, which calls
``.resolve()`` (following symlinks and collapsing ``..``) and rejects any
path that does not land under the resolved ``canon_dir``.

Why a chokepoint instead of scattered checks: every caller that does
``for f in canon_dir.iterdir(): f.read_text()`` or ``canon_dir / name`` and
then opens it is a place a symlinked ``.md`` (or a symlinked subdir under a
recursive walk, or an LLM-supplied ``../`` filename) can escape the canon
sandbox before any check runs. Routing all of them through these helpers
means a new caller cannot reintroduce the traversal class without going out
of its way.

Legitimate subdirectories (e.g. ``canon/sources/foo.txt``) stay allowed
because they still resolve under ``canon_root``; only escapes are rejected.

This module imports only :mod:`tinyassets.ingestion.canon_names`,
:mod:`tinyassets.universe_files` (the link-free reader/writer every canon
byte goes through) and the stdlib, so it carries no workflow import weight.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path

from tinyassets.ingestion.canon_names import resolve_within_canon
from tinyassets.universe_files import (
    MAX_PLATFORM_FILE_BYTES,
    read_data_path,
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
) -> Iterator[Path]:
    """Yield resolved, contained regular files inside ``canon_dir``.

    Enumeration replaces the unguarded ``for f in canon_dir.iterdir()`` /
    ``canon_dir.glob(...)`` / ``canon_dir.rglob(...)`` pattern. Each entry is
    resolved under ``canon_dir`` *before* any stat or read; an entry that
    escapes (symlink pointing outside, or a ``..`` component surfaced by a
    recursive walk) is skipped with a warning rather than raising, so a single
    poisoned entry cannot abort a whole enumeration pass.

    Parameters
    ----------
    canon_dir:
        The universe ``canon/`` directory (the canon ROOT). Containment is
        always measured against this directory's resolved path. Missing
        directories yield nothing.
    suffix:
        Optional suffix filter, case-insensitive, e.g. ``".md"`` or
        ``(".md", ".txt", ".markdown")``. ``None`` yields every contained file.
    subdir:
        Optional canon subdirectory to enumerate (e.g. ``"sources"``). The
        *containment root stays ``canon_dir``* — the subdir is itself resolved
        and contained against the canon root before any listing, so a symlinked
        ``sources/`` whose target lives outside canon is rejected (yields
        nothing) rather than becoming a trusted root. Passing the subdir as
        ``canon_dir`` instead would make a symlinked subdir its own root and
        defeat containment; route subdir enumeration through this parameter.
    recursive:
        When ``True`` walk subdirectories (replaces ``rglob``). Subdirs that
        are legitimate (resolve under canon_root) are descended; symlinked
        subdirs that escape are skipped.
    include_hidden:
        When ``False`` skip dotfiles (names beginning with ``.``) — markers
        and manifests stay out of content enumeration.
    kind:
        Error-message label forwarded to the containment check (debugging
        only; escapers are logged, not raised, during enumeration).

    Only paths that are (a) contained, (b) regular files (``.is_file()``
    checked on the *resolved* path so a symlink to a dir/device is excluded),
    and (c) suffix-matching are yielded. Results are sorted for determinism.
    """
    if not canon_dir.exists():
        return
    if canon_dir.is_symlink():
        # A linked root would enumerate another universe's canon as this one's.
        raise ValueError("canon directory is a link; refusing to enumerate it")

    # Containment is always measured against the resolved canon ROOT.
    canon_root = canon_dir.resolve()

    # Resolve the listing directory against the canon ROOT so a symlinked
    # subdir is caught here rather than silently becoming a trusted root.
    if subdir is not None:
        try:
            listing_dir = resolve_within_canon(canon_dir, subdir, kind="subdir")
        except ValueError:
            logger.warning(
                "Skipping canon subdir escaping canon dir: %s", subdir
            )
            return
    else:
        listing_dir = canon_root

    if not listing_dir.exists():
        return

    suffixes: tuple[str, ...] | None
    if suffix is None:
        suffixes = None
    elif isinstance(suffix, str):
        suffixes = (suffix.lower(),)
    else:
        suffixes = tuple(s.lower() for s in suffix)

    raw_iter = listing_dir.rglob("*") if recursive else listing_dir.iterdir()

    contained: list[Path] = []
    for entry in raw_iter:
        if not include_hidden and entry.name.startswith("."):
            continue
        # Build the name relative to the resolved canon ROOT so every entry
        # (including subdir entries like ``sources/foo.txt``) round-trips
        # through the containment check against the canon root rather than
        # the subdir. ``listing_dir`` is already resolved + contained, so its
        # children are descendants of ``canon_root``.
        try:
            rel = entry.relative_to(canon_root)
        except ValueError:
            # The listing dir is itself contained, so its children are
            # descendants of canon_root; treat any non-descendant as an escape.
            logger.warning("Skipping canon entry outside canon dir: %s", entry)
            continue
        try:
            resolved = resolve_within_canon(canon_dir, str(rel), kind=kind)
        except ValueError:
            logger.warning("Skipping canon entry escaping canon dir: %s", entry.name)
            continue
        if not resolved.is_file():
            continue
        if suffixes is not None and resolved.suffix.lower() not in suffixes:
            continue
        contained.append(resolved)

    for path in sorted(contained):
        yield path


def read_canon_text(
    canon_dir: Path,
    name: str,
    *,
    encoding: str = "utf-8",
    kind: str = "existing file",
    **kwargs: object,
) -> str:
    """Resolve ``name`` under ``canon_dir`` then ``read_text``.

    Raises :class:`ValueError` if ``name`` escapes containment (before any I/O).
    """
    data = read_canon_bytes(canon_dir, name, kind=kind)
    text = data.decode(encoding, **kwargs)  # type: ignore[arg-type]
    return text.replace("\r\n", "\n").replace("\r", "\n")


def read_canon_bytes(
    canon_dir: Path,
    name: str,
    *,
    kind: str = "existing file",
) -> bytes:
    """Resolve ``name`` under ``canon_dir``, then read it with no link followed.

    Containment is checked on the resolved path (links INSIDE canon still
    work); the bytes are then read by that resolved relative name from the
    lexical canon dir through :func:`tinyassets.universe_files.read_data_path`,
    so a canon root swapped for a link after the check is refused rather than
    followed. ``FileNotFoundError`` when absent.
    """
    path = resolve_within_canon(canon_dir, name, kind=kind)
    rel = path.relative_to(canon_dir.resolve())
    data = read_data_path(canon_dir / rel, max_bytes=MAX_PLATFORM_FILE_BYTES)
    if data is None:
        raise FileNotFoundError(str(canon_dir / name))
    return data


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
