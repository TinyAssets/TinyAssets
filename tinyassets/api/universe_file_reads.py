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
through the owner box provider, which refuses links at the file boundary.
"""

from __future__ import annotations

import base64
from pathlib import Path, PurePosixPath
from typing import Any

from tinyassets.owner_box_reads import box_list, box_read, box_stat
from tinyassets.universe_files import MAX_UNIVERSE_FILE_BYTES

#: One read returns at most this many bytes; the caller pages with next_offset.
MAX_READ_BYTES = 262_144
DEFAULT_READ_BYTES = 65_536
#: One listing returns at most this many entries, sorted, with ``truncated``.
MAX_LIST_ENTRIES = 500

_NOT_FOUND = {"error": "not_found", "resource": "command_center_file"}


def _owner_universe(universe_id: str) -> tuple[str, Path, str] | None:
    """``(uid, folder, actor)`` when the caller holds admin, else None."""
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path, _request_universe
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
    if Path(uid).name != uid or uid.startswith(".") or "/" in uid or "\\" in uid:
        return None
    return uid, Path(_base_path()) / uid, actor


def _list(folder: Path, rel: str) -> tuple[list[str], list[dict[str, Any]]]:
    items = sorted(box_list(folder, rel), key=lambda item: item.name)
    entries = [
        {"name": item.name, "kind": item.kind, **(
            {"size_bytes": item.size} if item.kind == "file" else {})}
        for item in items if item.kind in {"file", "dir"}
    ]
    return [item.name for item in items], entries[:MAX_LIST_ENTRIES]


def _read(folder: Path, rel: str) -> bytes:
    return box_read(folder, rel, max_bytes=MAX_UNIVERSE_FILE_BYTES)


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
    from tinyassets.universe_tools import WORKSPACE_DIR

    first = rel.split("/", 1)[0]
    if not first or first.startswith(".") or box_stat(folder, first):
        return rel
    return f"{WORKSPACE_DIR}/{rel}"


def list_files(*, universe_id: str = "", path: str = "") -> dict[str, Any]:
    owned = _owner_universe(universe_id)
    rel = _relative(path)
    if owned is None or rel is None:
        return dict(_NOT_FOUND)
    uid, folder, _actor = owned
    try:
        rel = _logical(folder, rel)
        names, entries = _list(folder, rel)
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
    uid, folder, _actor = owned
    try:
        rel = _logical(folder, rel)
        data = _read(folder, rel)
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
    # Only the owner gate's tuple is an admission.
    if not isinstance(owned, tuple) or not rel:
        return None
    uid, folder, _actor = owned
    try:
        return _read(folder, _logical(folder, rel))
    except OSError:
        return None


__all__ = ["MAX_LIST_ENTRIES", "MAX_READ_BYTES", "list_files", "read_file", "read_whole_file"]
