"""Bounded platform reads under the center's broker-admitted box identity."""

from __future__ import annotations

import os
from pathlib import Path

from tinyassets.boxes.provider import BoxAuthError, DirEntry, FileStat, box_relpath
from tinyassets.center_platform_files import center_from_path


class UnadmittedCenter(BoxAuthError):
    """No proven center to read; presence probes may report absent."""


def _access(universe_dir: Path):
    from tinyassets.agent_loop.served_chat import configured_box_provider
    from tinyassets.broker.owner_identities import CenterUnadmitted, admitted_owner
    from tinyassets.center_platform_files import _directory
    from tinyassets.control_plane.binding import current_binding

    center = center_from_path(universe_dir)
    root = Path(os.path.abspath(universe_dir))
    # Preserve the caller's authority key: never resolve the final component.
    # A bind may materialize a local box. Open the stub with O_NOFOLLOW before
    # either the session-binding fast path or a broker/provider bind.
    try:
        with _directory(root):
            pass
    except FileNotFoundError as exc:
        raise UnadmittedCenter(f"command center {center!r} has no host stub") from exc
    binding = current_binding()
    if binding is not None and binding.universe_dir == root:
        binding.validate(binding.handle.account_id, root)
        return binding.provider, binding.handle
    try:
        owner = admitted_owner(root.parent, center=center)
    except (PermissionError, CenterUnadmitted) as exc:
        raise UnadmittedCenter(f"command center {center!r} is not admitted") from exc
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


def box_stat(universe_dir: Path, relpath: str) -> FileStat | None:
    path = _box_path(relpath)
    provider, handle = _access(universe_dir)
    return provider.stat(handle, path)


def box_exists(universe_dir: Path, relpath: str) -> bool:
    try:
        return box_stat(universe_dir, relpath) is not None
    except UnadmittedCenter:
        return False


def box_read(universe_dir: Path, relpath: str, *,
             max_bytes: int) -> bytes:
    path = _box_path(relpath)
    provider, handle = _access(universe_dir)
    result = provider.read(handle, path, max_bytes=max_bytes)
    if result.size > max_bytes or len(result.data) != result.size:
        raise OSError(f"owner box file exceeds {max_bytes}-byte bound or is incomplete: {relpath}")
    from tinyassets.control_plane.binding import current_binding

    binding = current_binding()
    if binding is not None and binding.handle is handle:
        binding.check_read(result.generation)
    return result.data


class BoxConfigInvalid(ValueError):
    """An owner config file in the box is not bounded, safe YAML."""


def box_read_yaml(universe_dir: Path, relpath: str) -> object | None:
    """Bounded, alias-free YAML from the box; None when the file is absent."""
    from tinyassets.universe_files import MAX_CONFIG_BYTES, UniverseFileError, load_untrusted_yaml

    try:
        raw = box_read(universe_dir, relpath, max_bytes=MAX_CONFIG_BYTES)
    except FileNotFoundError:
        return None
    try:
        return load_untrusted_yaml(raw.decode("utf-8"), max_bytes=MAX_CONFIG_BYTES)
    except (UniverseFileError, UnicodeDecodeError) as exc:
        raise BoxConfigInvalid(f"{relpath}: {exc}") from exc


def box_list(universe_dir: Path, relpath: str, *,
             limit: int | None = None) -> list[DirEntry]:
    path = _box_path(relpath)
    if limit is not None and (type(limit) is not int or limit < 0):
        raise ValueError("box listing limit must be a non-negative integer")
    provider, handle = _access(universe_dir)
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


def box_tree_size(universe_dir: Path, relpath: str) -> int:
    """Sum regular file sizes under a box directory."""
    info = box_stat(universe_dir, relpath)
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
        for entry in box_list(universe_dir, directory):
            if entry.kind == "file":
                total += entry.size
            elif entry.kind == "dir":
                pending.append(f"{directory}/{entry.name}" if directory else entry.name)
    return total
