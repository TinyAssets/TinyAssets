"""Shared path and I/O helpers for the universe-server action handlers.

Extracted from ``tinyassets/universe_server.py`` preamble as Bundle 1 of
the #29 decomposition plan. These helpers are used by 3+ future submodules
(branches, runs, evaluation, market, wiki, status) and therefore live in
a shared leaf module with no project-level circular-import risk.

Public surface (stable contract):
    _base_path()               → Path: canonical data root
    _universe_dir(uid)         → Path: specific universe directory (path-traversal guarded)
    _default_universe()        → str:  default universe ID
    _read_json(path)           → dict | list | None: safe JSON reader
    _read_text(path, default)  → str:  safe text reader

ADDED 2026-04-26 (Task #8 — wiki-adjacent batch):
    _wiki_root()               → Path: canonical wiki directory root
    _wiki_pages_dir()          → Path: promoted-pages subtree (wiki_root/pages)
    _wiki_drafts_dir()         → Path: drafts subtree (wiki_root/drafts)
    _find_all_pages(directory) → list[Path]: recursive .md scan helper

The 4 wiki-adjacent helpers move here (rather than into a new
``wiki_helpers.py``) to keep the leaf-module count small and because
``_wiki_pages_dir`` / ``_wiki_drafts_dir`` both call ``_wiki_root``;
splitting them would create a `helpers.py → universe_server.py → helpers.py`
cycle until the wiki extraction (Task #9) lands. See
``docs/exec-plans/completed/2026-04-26-decomp-step-1-prep.md`` for the
helpers-already-extracted lesson that prompted this batch.
"""

from __future__ import annotations

import json
import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
_WIKI_ROOT_OVERRIDE: ContextVar[Path | None] = ContextVar(
    "workflow_wiki_root_override",
    default=None,
)


def _base_path() -> Path:
    """Resolve the base directory containing all universe directories.

    Delegates to ``tinyassets.storage.data_dir`` — canonical env var
    ``TINYASSETS_DATA_DIR``. This replaces the earlier CWD-relative
    ``"output"`` default which wrote to ``/app/output`` in containers
    instead of the bind-mounted ``/data`` volume — the 2026-04-19
    containerization bug class.
    """
    from tinyassets.storage import data_dir
    return data_dir()


def _universe_dir(universe_id: str) -> Path:
    """Resolve a specific universe directory with path-traversal guard."""
    base = _base_path()
    result = (base / universe_id).resolve()
    if not result.is_relative_to(base):
        raise ValueError(f"Invalid universe_id: {universe_id}")
    return result


def _owned_universe_dir_name(base: Path, name: str) -> str:
    """``name`` if it is a universe here -- an owned id WITH a directory -- else ``""``.

    A pointer (`.active_universe`, ``UNIVERSE_SERVER_DEFAULT_UNIVERSE``, a
    directory listing) has to satisfy both halves before a resolver hands it
    back: an ownership row names it, and the directory is really there. The
    answer is always ``name`` itself, never a re-spelling -- a universe id is
    simultaneously a path component and an authority key, and any function that
    returns one spelling to a caller who needs the other breaks it (see
    `daemon_server.owned_universe_id` for both directions this failed in).
    """
    from tinyassets.daemon_server import owned_universe_id

    candidate = (name or "").strip()
    if not candidate or not base.is_dir():
        return ""
    # Path-traversal guarded like `_universe_dir`: a pointer is caller-influenced
    # (a marker file, an env var), so `../outside` must not even be stat'ed as a
    # candidate universe.
    try:
        resolved = (base / candidate).resolve()
        if not resolved.is_relative_to(base.resolve()) or resolved == base.resolve():
            return ""
    except OSError:
        return ""
    if not resolved.is_dir():
        return ""
    return candidate if owned_universe_id(base, candidate) else ""


def _default_universe() -> str:
    """Return the default universe ID, or first available.

    A universe is a directory somebody OWNS (founder, 2026-09-02), so every
    branch here resolves through :func:`_owned_universe_dir_name`. A MARKER OR
    AN ENV VALUE IS A POINTER, NOT A GRANT: both used to be returned before any
    ownership check, so a stale ``.active_universe`` or a configured default
    naming ``scratch`` routed requests into an operational directory. The final
    branch returned the first non-hidden directory, which handed out an
    operational store that sorted first (``cloud-automation-inputs``,
    ``_aaa-scratch``) as the default universe.
    """
    base = _base_path()
    from tinyassets.daemon_server import owned_universe_ids
    from tinyassets.storage import active_universe_id
    active = active_universe_id(base)
    if active:
        resolved = _owned_universe_dir_name(base, active)
        if resolved:
            return resolved

    default = os.environ.get("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "")
    if default:
        resolved = _owned_universe_dir_name(base, default)
        if resolved:
            return resolved
        # An unowned configured default still answers when the data root holds
        # no universes at all -- a fresh install pointing at the name it is
        # about to create. It never wins over a real one.
        if not owned_universe_ids(base):
            return default

    if base.is_dir():
        for child in sorted(base.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith(".")
                # The DIRECTORY name, resolved case-insensitively against the
                # ownership rows, so a restored `U-Mine` is returned as the path
                # that exists rather than the ACL spelling that does not.
                and _owned_universe_dir_name(base, child.name)
            ):
                return child.name
    return "default-universe"


def _request_universe(universe_id: str = "") -> str:
    """Resolve the universe for a request with an omitted ``universe_id``.

    The shared MCP resolver (Codex 2026-07-02 adapt): an explicit id wins; an
    AUTHENTICATED founder resolves to their bound home (falling back to the
    identity-neutral public universe — never through the host-global
    ``.active_universe`` marker or another founder's serial home, which leaked
    cross-founder on `universe action=inspect` and friends); an unbound /
    dev caller keeps the legacy single-tenant default resolution. Unlike
    get_status's ``_resolve_entry_universe`` this NEVER creates a universe.
    """
    requested = (universe_id or "").strip()
    if requested:
        return requested

    from tinyassets.api import permissions

    if not permissions.is_authenticated_request():
        return _default_universe()

    from tinyassets.daemon_server import get_founder_home

    base = _base_path()
    home = get_founder_home(base, permissions.current_actor_id())
    if home and (base / home).is_dir():
        return home
    return _designated_public_universe()


def _designated_public_universe() -> str:
    """Identity-neutral public landing universe for an authenticated founder who
    has no home and cannot create one.

    Unlike :func:`_default_universe` this NEVER reads the host-global
    ``.active_universe`` marker and NEVER returns another founder's serial home
    (``u-`` + ULID) — either would leak one founder's universe to another on an
    omitted-scope read (universe-creation spec: "First MCP contact ... SHALL NOT
    use a root-global ``.active_universe`` marker to decide which universe a
    chatbot speaks as"). Env-designated default wins; otherwise the first
    non-serial public directory; else the literal ``default-universe``.

    Every branch requires an OWNER, for the same reason as
    :func:`_default_universe` -- landing an authenticated founder in an
    operational directory is the same bug whichever resolver does it.
    """
    from tinyassets.ids import is_universe_serial

    base = _base_path()
    default = os.environ.get("UNIVERSE_SERVER_DEFAULT_UNIVERSE", "")
    if default:
        resolved = _owned_universe_dir_name(base, default)
        if resolved:
            return resolved
    if base.is_dir():
        from tinyassets.daemon_server import owned_universe_ids

        if default and not owned_universe_ids(base):
            return default
        for child in sorted(base.iterdir()):
            if (
                child.is_dir()
                and not child.name.startswith(".")
                and not is_universe_serial(child.name)
                and _owned_universe_dir_name(base, child.name)
            ):
                return child.name
    return "default-universe"


def _read_json(path: Path) -> dict[str, Any] | list[Any] | None:
    """Read a JSON file; ``None`` when absent or not valid JSON.

    Read through :func:`tinyassets.universe_files.read_data_path`: under the
    data dir no link is followed at any component. A REFUSED read (a planted
    link, a non-regular file, over the bound) raises
    :class:`~tinyassets.universe_files.UniverseFileError` rather than reading
    as ``None``, because a caller that writes back would otherwise overwrite
    or create the file on the strength of "absent".
    """
    from tinyassets.universe_files import MAX_PLATFORM_FILE_BYTES, read_data_path

    data = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
    if data is None:
        return None
    try:
        return json.loads(data.decode("utf-8"))
    except ValueError as exc:
        logger.warning("Failed to read %s: %s", path, exc)
    return None


def _read_platform_text(path: Path, default: str, errors: str) -> str:
    """A text file outside the wiki: a platform log or record (``activity.log``,
    run logs). ``default`` only when absent; a refused read raises, see
    :func:`_read_json`."""
    from tinyassets.universe_files import MAX_PLATFORM_FILE_BYTES, read_data_path

    data = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
    if data is None:
        return default
    text = data.decode("utf-8", errors=errors)
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_text(path: Path, default: str = "", *, errors: str = "strict") -> str:
    """Read a text file; a wiki page is read link-free and bounded.

    The universe's agent writes its own wiki from the tool jail, so a page is
    untrusted input to the daemon reading it. It is walked from the wiki root
    with no link at any component. Absent reads as ``default``. A link, a
    non-regular file or a page over the bound raises
    :class:`~tinyassets.universe_files.UniverseFileError` instead of reading
    as empty, because a read-modify-write that saw "" would overwrite the page.
    Every refusal other than "absent" propagates, whichever ``OSError`` the
    host's safe reader raises it as.

    Inside a universe-scoped wiki operation (:func:`_scoped_wiki_root`), a path
    OUTSIDE that wiki is refused rather than read: a page path resolved through
    a planted link would otherwise land on another universe's file and the
    plain reader would follow it.
    """
    from tinyassets.universe_files import (
        MAX_UNIVERSE_FILE_BYTES,
        UniverseFileError,
        read_universe_file,
    )

    root = _wiki_root()
    try:
        relpath = path.relative_to(root).as_posix()
    except ValueError:
        if _WIKI_ROOT_OVERRIDE.get() is not None:
            raise UniverseFileError(
                f"{path.name!r} is outside this universe's wiki; nothing was read"
            ) from None
        return _read_platform_text(path, default, errors)
    from tinyassets.universe_files import is_data_path, read_data_path

    if is_data_path(path):
        # Under the data dir: walked from the data root, so a wiki ROOT that
        # is (or was swapped for) a link is refused too, not just a page.
        data = read_data_path(path, max_bytes=MAX_UNIVERSE_FILE_BYTES)
        if data is None:
            return default
    else:
        try:
            data = read_universe_file(root, relpath)
        except FileNotFoundError:
            return default
    text = data.decode("utf-8", errors=errors)
    return text.replace("\r\n", "\n").replace("\r", "\n")


# ─────────────────────────────────────────────────────────────────────
# Wiki-adjacent path helpers (Task #8, 2026-04-26)
# ─────────────────────────────────────────────────────────────────────


def _wiki_root() -> Path:
    """Resolve the wiki root directory.

    Delegates to ``tinyassets.storage.wiki_path`` — canonical env var
    ``TINYASSETS_WIKI_PATH``. Platform default is ``data_dir() / "wiki"``.

    Pre-2026-04-20 this hardcoded ``r"C:\\Users\\Jonathan\\Projects\\Wiki"``
    as the fallback, which broke every non-host deploy. See
    ``tinyassets.storage.wiki_path`` for the precedence + rationale.
    """
    override = _WIKI_ROOT_OVERRIDE.get()
    if override is not None:
        return override

    from tinyassets.storage import wiki_path
    return wiki_path()


@contextmanager
def _scoped_wiki_root(root: Path) -> Iterator[None]:
    """Temporarily route wiki helpers to an explicit wiki root.

    Never ``resolve()``: a universe's ``wiki -> /data/<other>/wiki`` link would
    turn the scoped root into the other universe's wiki. A linked root is
    refused; the root is kept as the (absolute) path the caller named.
    """
    from tinyassets.universe_files import UniverseFileError

    if root.is_symlink():
        raise UniverseFileError(f"the wiki root {root.name!r} is a link; nothing was opened")
    token = _WIKI_ROOT_OVERRIDE.set(root if root.is_absolute() else root.absolute())
    try:
        yield
    finally:
        _WIKI_ROOT_OVERRIDE.reset(token)


def _wiki_pages_dir() -> Path:
    return _wiki_root() / "pages"


def _wiki_drafts_dir() -> Path:
    return _wiki_root() / "drafts"


def _find_all_pages(directory: Path) -> list[Path]:
    """Recursively find all .md files under a directory."""
    if not directory.is_dir():
        return []
    return sorted(
        p for p in directory.rglob("*.md") if not p.is_symlink() and p.is_file()
    )


__all__ = [
    "_base_path",
    "_default_universe",
    "_find_all_pages",
    "_owned_universe_dir_name",
    "_read_json",
    "_read_text",
    "_scoped_wiki_root",
    "_universe_dir",
    "_wiki_drafts_dir",
    "_wiki_pages_dir",
    "_wiki_root",
]
