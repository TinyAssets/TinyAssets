"""The owner's read of their own universe folder (``/u``) from the connector.

Agents a universe runs coordinate through files in ``/u`` -- a board, a queue, a
log -- and until now only the universe's OWN served tools could read them. A
screen the owner built (a custom UI, through its bridge) or the owner's own
chatbot had no way to see them. These are the two reads that close that gap:
one directory listing and one bounded file read.

Owner-only, deliberately narrower than "can read the universe": a public
universe is readable by anyone, and its public face is not its working folder.
The gate is the same one pending requests use -- an explicit ``admin`` ACL row
for this actor on this universe -- and every refusal, a missing file and a path
that tries to leave the folder all return one ``not_found`` envelope, so the
read cannot be used to learn whether a universe or a path exists.

Paths never leave the folder: every component is checked, and the reads go
through ``tinyassets.universe_files``, which opens each component without
following a link.
"""

from __future__ import annotations

import base64
import os
import stat
from pathlib import Path, PurePosixPath
from typing import Any

#: One read returns at most this many bytes; the caller pages with next_offset.
MAX_READ_BYTES = 262_144
DEFAULT_READ_BYTES = 65_536
#: One listing returns at most this many entries, sorted, with ``truncated``.
MAX_LIST_ENTRIES = 500

_NOT_FOUND = {"error": "not_found", "resource": "command_center_file"}


def _owner_universe(universe_id: str) -> tuple[str, Path] | None:
    """``(uid, folder)`` when the caller holds admin on this universe, else None."""
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path, _request_universe, _universe_dir
    from tinyassets.daemon_server import list_universe_acl
    from tinyassets.principals import named_principal

    if not permissions.is_authenticated_request():
        return None
    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return None
    uid = _request_universe(universe_id)
    if not uid:
        return None
    try:
        rows = list_universe_acl(_base_path(), universe_id=uid)
    except Exception:  # noqa: BLE001 - fail closed on any storage error
        return None
    if not any(r.get("actor_id") == actor and r.get("permission") == "admin" for r in rows):
        return None
    try:
        folder = _universe_dir(uid)
    except ValueError:
        return None
    # NOT ``folder.is_dir()``: _universe_dir resolves the path, and resolving
    # strips a link at the universe root itself (astra round 2, P1: an owned
    # root junction to another universe served that universe's files). The
    # anchored open below refuses the root when it is a link.
    if folder.name != uid:
        return None
    return uid, Path(_base_path()) / uid


# ---------------------------------------------------------------------------
# Anchored traversal. Production is Linux: the data directory is opened once,
# the universe root beneath it with O_NOFOLLOW, and every further component with
# openat(O_NOFOLLOW) relative to the descriptor above it. Entries are stat()ed
# through the directory descriptor, never by path, so a directory swapped for a
# link after it was opened changes nothing this read sees (astra round 2, P1).
# Windows has no openat: there, a reparse point anywhere on the path -- the
# universe root included -- refuses the read. Windows hosts are single-tenant
# trays, so the residual race there crosses no user.
# ---------------------------------------------------------------------------


def _open_universe_dir(base: Path, uid: str, rel: str) -> int:
    """POSIX: a descriptor for ``base/uid/rel``, no component a link."""
    from tinyassets import workspace_fs as fs

    base_fd = os.open(str(base), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        current = fs.open_subdir_nofollow(base_fd, uid)
    finally:
        os.close(base_fd)
    try:
        for part in (rel.split("/") if rel else []):
            child = fs.open_subdir_nofollow(current, part)
            os.close(current)
            current = child
    except BaseException:
        os.close(current)
        raise
    return current


def _windows_path_is_clean(base: Path, uid: str, rel: str) -> Path:
    """Windows: the path, after refusing a reparse point at ANY component."""
    from tinyassets.universe_files import UniverseFileError

    current = base
    for part in [uid, *(rel.split("/") if rel else [])]:
        current = current / part
        info = os.lstat(current)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
            raise UniverseFileError(f"{part!r} is a link; command center files are read link-free")
    return current


def _entry(name: str, info: os.stat_result) -> dict[str, Any] | None:
    # A link is never followed and never listed; a Windows junction lstat()s as
    # a directory and only its reparse tag says what it is.
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_reparse_tag", 0):
        return None
    if stat.S_ISREG(info.st_mode):
        return {"name": name, "kind": "file", "size_bytes": info.st_size}
    if stat.S_ISDIR(info.st_mode):
        return {"name": name, "kind": "dir"}
    return None


def _list(base: Path, uid: str, rel: str) -> tuple[list[str], list[dict[str, Any]]]:
    from tinyassets import workspace_fs as fs

    entries: list[dict[str, Any]] = []
    if fs._POSIX:
        fd = _open_universe_dir(base, uid, rel)
        try:
            names = sorted(os.listdir(fd))
            for name in names:
                if len(entries) >= MAX_LIST_ENTRIES:
                    break
                try:
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                except OSError:
                    continue
                entry = _entry(name, info)
                if entry is not None:
                    entries.append(entry)
        finally:
            os.close(fd)
        return names, entries
    directory = _windows_path_is_clean(base, uid, rel)
    names = sorted(entry.name for entry in os.scandir(directory))
    for name in names:
        if len(entries) >= MAX_LIST_ENTRIES:
            break
        try:
            info = os.lstat(directory / name)
        except OSError:
            continue
        entry = _entry(name, info)
        if entry is not None:
            entries.append(entry)
    return names, entries


def _read(base: Path, uid: str, rel: str) -> bytes:
    from tinyassets import workspace_fs as fs
    from tinyassets.universe_files import MAX_UNIVERSE_FILE_BYTES, read_universe_file

    if fs._POSIX:
        fd = _open_universe_dir(base, uid, "")
        try:
            return fs.read_regular_file_beneath(fd, rel, max_bytes=MAX_UNIVERSE_FILE_BYTES)
        finally:
            os.close(fd)
    # The root is checked here; every component below it by the shared reader.
    _windows_path_is_clean(base, uid, "")
    return read_universe_file(base / uid, rel)


def _relative(raw: str) -> str | None:
    """The path under the folder, or None when it names anything else.

    ``/u`` is how the universe's own agents name the folder, so ``/u/notes/x``
    and ``notes/x`` mean the same file. Any other absolute path, ``..``, ``.``,
    a backslash or a NUL is refused rather than normalised.
    """
    from tinyassets.universe_files import UniverseFileError, _check_component

    text = str(raw or "").strip()
    if text in ("/u", "/u/"):
        return ""
    if text.startswith("/u/"):
        text = text[3:]
    if text.startswith("/") or "\\" in text:
        return None
    text = text.rstrip("/")
    if not text:
        return ""
    try:
        for part in text.split("/"):
            _check_component(part)
    except UniverseFileError:
        return None
    return str(PurePosixPath(text))


def _logical(folder: Path, rel: str) -> str:
    """Where ``rel`` lives in the agent's ``/u`` (harness W2).

    The agent's ``/u`` is its workspace with the universe's visible root
    entries bound over it at their own names. So a path whose first name is a
    root entry is the root's; any other names the workspace, where the
    agent's new files land (gpt-6-astra on #4194: a file written to
    ``/u/PLAN.md`` read back as missing).
    """
    from tinyassets.universe_tools import WORKSPACE_DIR

    first = rel.split("/", 1)[0]
    if not first or first.startswith(".") or os.path.lexists(folder / first):
        return rel
    return f"{WORKSPACE_DIR}/{rel}"


def list_files(*, universe_id: str = "", path: str = "") -> dict[str, Any]:
    owned = _owner_universe(universe_id)
    rel = _relative(path)
    if owned is None or rel is None:
        return dict(_NOT_FOUND)
    uid, folder = owned
    rel = _logical(folder, rel)
    try:
        names, entries = _list(folder.parent, uid, rel)
    except OSError:
        return dict(_NOT_FOUND)
    return {
        "universe_id": uid,
        "path": rel,
        "entries": entries,
        "truncated": len(names) > len(entries) and len(entries) >= MAX_LIST_ENTRIES,
    }


def read_file(
    *, universe_id: str = "", path: str = "", offset: int = 0, count: int = DEFAULT_READ_BYTES,
) -> dict[str, Any]:
    owned = _owner_universe(universe_id)
    rel = _relative(path)
    if owned is None or not rel:
        return dict(_NOT_FOUND)
    if type(offset) is not int or offset < 0:
        return {"error": "file_offset must be a non-negative integer"}
    if type(count) is not int or not 1 <= count <= MAX_READ_BYTES:
        return {"error": f"file_max_bytes must be between 1 and {MAX_READ_BYTES}"}
    uid, folder = owned
    rel = _logical(folder, rel)
    try:
        data = _read(folder.parent, uid, rel)
    except OSError:
        return dict(_NOT_FOUND)
    total = len(data)
    if offset > total:
        return {"error": "file_offset is past the end of the file"}
    chunk = data[offset:offset + count]
    try:
        whole_text = data.decode("utf-8")
    except UnicodeDecodeError:
        whole_text = None
    if whole_text is not None:
        # Never split a character: back the end off to a UTF-8 boundary, so
        # every chunk decodes and the chunks concatenate to the file.
        end = offset + len(chunk)
        while end < total and end > offset and (data[end] & 0xC0) == 0x80:
            end -= 1
        if end == offset and offset < total:
            # A window smaller than one character still makes progress.
            end = offset + 1
            while end < total and (data[end] & 0xC0) == 0x80:
                end += 1
        chunk = data[offset:end]
        body = {"encoding": "text", "text": chunk.decode("utf-8")}
    else:
        end = offset + len(chunk)
        body = {"encoding": "base64", "base64": base64.b64encode(chunk).decode("ascii")}
    return {
        "universe_id": uid,
        "path": rel,
        "size_bytes": total,
        "offset": offset,
        "length": end - offset,
        "next_offset": end if end < total else None,
        "eof": end >= total,
        **body,
    }


def read_whole_file(*, universe_id: str = "", path: str = "") -> bytes | None:
    """The whole file under the caller's own folder, or None for any refusal.

    The same owner gate and link-free traversal as ``read_file``, for a server
    caller that needs the bytes rather than a page of them (a custom UI asset
    taken from a file the agents wrote). Bounded by the universe file read bound.
    """
    owned = _owner_universe(universe_id)
    rel = _relative(path)
    # _owner_universe answers a not-found DOCUMENT when the folder cannot be
    # resolved; only a (uid, folder) pair is an admission.
    if not isinstance(owned, tuple) or not rel:
        return None
    uid, folder = owned
    try:
        return _read(folder.parent, uid, rel)
    except OSError:
        return None


__all__ = ["MAX_LIST_ENTRIES", "MAX_READ_BYTES", "list_files", "read_file", "read_whole_file"]
