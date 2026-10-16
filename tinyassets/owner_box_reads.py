"""Bounded owner-content reads through the admitted or configured box provider."""

from __future__ import annotations

import os
from pathlib import Path

from tinyassets.boxes.provider import BoxAuthError, DirEntry, FileStat, box_relpath
from tinyassets.center_platform_files import center_from_path


def binding_owner(universe_dir: Path) -> str:
    """Owner for internal callers already running in an admitted session."""
    from tinyassets.control_plane.binding import file_binding

    binding = file_binding(universe_dir)
    if binding is None:
        raise BoxAuthError("owner content requires an admitted owner")
    binding.validate(binding.handle.account_id, universe_dir)
    return binding.handle.account_id


def _access(universe_dir: Path, owner: str):
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.control_plane.binding import current_binding, file_binding

    root = Path(os.path.abspath(universe_dir))
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner box read requires an owner")
    admitted = current_binding()
    if admitted is not None and admitted.universe_dir == root:
        binding = file_binding(root)
        binding.validate(owner, root)
        return binding.provider, binding.handle
    center = center_from_path(root)
    provider, _ = configured_box_provider()
    if provider is None:
        raise RuntimeError("owner box provider is unavailable")
    return provider, provider.bind(
        center, account_id=owner, turn_id=f"owner-read:{owner}:{center}",
    )


def _box_path(relpath: str) -> str:
    path = f"/cc/{relpath}" if relpath else "/cc"
    box_relpath(path)
    return path


def box_stat(universe_dir: Path, relpath: str, *, owner: str) -> FileStat | None:
    path = _box_path(relpath)
    provider, handle = _access(universe_dir, owner)
    return provider.stat(handle, path)


def box_exists(universe_dir: Path, relpath: str, *, owner: str) -> bool:
    return box_stat(universe_dir, relpath, owner=owner) is not None


def box_read(universe_dir: Path, relpath: str, *, owner: str,
             max_bytes: int) -> bytes:
    path = _box_path(relpath)
    provider, handle = _access(universe_dir, owner)
    result = provider.read(handle, path, max_bytes=max_bytes)
    if result.size > max_bytes or len(result.data) != result.size:
        raise OSError(f"owner box file exceeds {max_bytes}-byte bound or is incomplete: {relpath}")
    from tinyassets.control_plane.binding import current_binding

    binding = current_binding()
    if binding is not None and binding.handle is handle:
        binding.check_read(result.generation)
    return result.data


def box_list(universe_dir: Path, relpath: str, *, owner: str,
             limit: int | None = None) -> list[DirEntry]:
    path = _box_path(relpath)
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("box listing limit must be a non-negative integer")
    provider, handle = _access(universe_dir, owner)
    entries = []
    cursor = None
    seen = set()
    while True:
        if limit is not None and len(entries) >= limit:
            return entries[:limit]
        page = provider.list(handle, path, cursor=cursor,
                             limit=min(200, limit - len(entries)) if limit is not None else 200)
        entries.extend(page.entries)
        if page.next_cursor is None:
            return entries
        if page.next_cursor in seen:
            raise RuntimeError("owner box listing cursor did not advance")
        seen.add(page.next_cursor)
        cursor = page.next_cursor


def box_tree_size(universe_dir: Path, relpath: str, *, owner: str) -> int:
    """Sum regular file sizes under a box directory."""
    info = box_stat(universe_dir, relpath, owner=owner)
    if info is None:
        return 0
    if info.kind == "file":
        return info.size
    if info.kind != "dir":
        return 0
    total = 0
    pending = [relpath]
    while pending:
        directory = pending.pop()
        for entry in box_list(universe_dir, directory, owner=owner):
            if entry.kind == "file":
                total += entry.size
            elif entry.kind == "dir":
                pending.append(f"{directory}/{entry.name}" if directory else entry.name)
    return total
