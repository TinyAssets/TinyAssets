"""Host-owned files beside an owner's box, anchored under the caller's base."""

from __future__ import annotations

import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from tinyassets import workspace_fs as fs
from tinyassets.boxes.layout import (
    LEDGER,
    LEDGER_LOCK,
    PAUSE,
    WORKER_HEARTBEAT,
    host_center_entry,
)
from tinyassets.boxes.layout import (
    WORKER_HEARTBEAT_PATTERN as WORKER_HEARTBEAT_PATTERN,
)
from tinyassets.universe_files import _lstat_nofollow_windows, _windows_parent


def _center(center: str) -> str:
    if (not isinstance(center, str) or not center or center in {".", ".."}
            or center.startswith(".") or any(c in center for c in "/\\\0")):
        raise ValueError(f"unsafe command center id: {center!r}")
    return center


def center_from_path(universe_dir: Path, *, base_path: Path | None = None) -> str:
    path = Path(universe_dir)
    if ".." in path.parts:
        raise ValueError(f"unsafe command center path: {path}")
    center = _center(path.name)
    base = path.parent if base_path is None else Path(base_path)
    if os.path.abspath(path.parent) != os.path.abspath(base):
        raise ValueError(f"command center path is outside caller base: {path}")
    return center


def _path(universe_dir: Path, name: str) -> Path:
    center_from_path(universe_dir)
    return Path(universe_dir) / name


@contextmanager
def _directory(universe_dir: Path):
    path = Path(universe_dir)
    center = center_from_path(path)
    root = fs.open_dir_nofollow(path.parent.absolute())
    try:
        child = fs.open_subdir_nofollow(root, center)
        try:
            yield child
        finally:
            os.close(child)
    finally:
        os.close(root)


def _read(universe_dir: Path, name: str, *, max_bytes: int = 16 * 1024 * 1024) -> bytes | None:
    try:
        return _read_existing(universe_dir, name, max_bytes=max_bytes)
    except FileNotFoundError:
        return None


def _read_existing(universe_dir: Path, name: str, *, max_bytes: int) -> bytes | None:
    if fs._POSIX:
        with _directory(universe_dir) as directory:
            try:
                return fs.read_regular_file_beneath(directory, name, max_bytes=max_bytes)
            except FileNotFoundError:
                return None
    root = Path(universe_dir).parent
    center = center_from_path(universe_dir)
    center_path = _lstat_nofollow_windows(root, _center(center))
    if not stat.S_ISDIR(center_path.lstat().st_mode):
        raise OSError(f"command center is not a directory: {center_path}")
    try:
        path = _lstat_nofollow_windows(root, f"{center}/{name}")
    except FileNotFoundError:
        return None
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_size > max_bytes:
        raise OSError(f"invalid host file: {path}")
    with path.open("rb") as handle:
        data = handle.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise OSError(f"host file too large: {path}")
    return data


def _write(universe_dir: Path, name: str, data: bytes, *, append: bool = False) -> None:
    if fs._POSIX:
        with _directory(universe_dir) as directory:
            try:
                existing = os.stat(name, dir_fd=directory, follow_symlinks=False)
            except FileNotFoundError:
                existing = None
            if existing is not None and (not stat.S_ISREG(existing.st_mode)
                                         or existing.st_nlink != 1):
                raise OSError(f"invalid host file: {name}")
            flags = os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC
            target = name if append else f".{uuid.uuid4().hex}.tmp"
            if append:
                flags |= os.O_APPEND
            else:
                flags |= os.O_EXCL
            fd = os.open(target, flags, 0o600, dir_fd=directory)
            try:
                view = memoryview(data)
                while view:
                    view = view[os.write(fd, view):]
                os.fsync(fd)
            finally:
                os.close(fd)
            if not append:
                try:
                    os.replace(target, name, src_dir_fd=directory, dst_dir_fd=directory)
                except BaseException:
                    os.unlink(target, dir_fd=directory)
                    raise
            os.fsync(directory)
        return
    root = Path(universe_dir).parent
    center = center_from_path(universe_dir)
    parent = _windows_parent(root, [_center(center), name], create=False)
    target = parent / name
    try:
        _lstat_nofollow_windows(root, f"{center}/{name}")
    except FileNotFoundError:
        pass
    temporary = target if append else parent / f".{uuid.uuid4().hex}.tmp"
    with temporary.open("ab" if append else "xb") as handle:
        handle.write(data)
        handle.flush()
        os.fsync(handle.fileno())
    if not append:
        os.replace(temporary, target)


def _remove(universe_dir: Path, name: str) -> None:
    if fs._POSIX:
        with _directory(universe_dir) as directory:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError(f"invalid host file: {name}")
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
    else:
        root = Path(universe_dir).parent
        _lstat_nofollow_windows(root, f"{center_from_path(universe_dir)}/{name}").unlink()


def is_paused(universe_dir: Path) -> bool:
    return _read(universe_dir, PAUSE, max_bytes=4096) is not None


def set_paused(universe_dir: Path, timestamp: str) -> None:
    _write(universe_dir, PAUSE, timestamp.encode("utf-8"))


def clear_paused(universe_dir: Path) -> None:
    _remove(universe_dir, PAUSE)


def worker_heartbeat_name(worker_id: str | None = None) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", (worker_id or "").strip()).strip(".-")
    return f".worker_supervisor.{clean}.json" if clean and clean != "default" else WORKER_HEARTBEAT


def write_worker_heartbeat(universe_dir: Path, worker_id: str | None, data: bytes) -> None:
    _write(universe_dir, worker_heartbeat_name(worker_id), data)


def read_worker_heartbeats(universe_dir: Path, *, max_bytes: int) -> dict[str, bytes]:
    try:
        return _read_worker_heartbeats(universe_dir, max_bytes=max_bytes)
    except FileNotFoundError:
        return {}


def _read_worker_heartbeats(universe_dir: Path, *, max_bytes: int) -> dict[str, bytes]:
    if fs._POSIX:
        with _directory(universe_dir) as directory:
            names = os.listdir(directory)
    else:
        root = _lstat_nofollow_windows(Path(universe_dir).parent, center_from_path(universe_dir))
        names = os.listdir(root)
    valid = [name for name in names
             if name.startswith(".worker_supervisor.") and host_center_entry(name)]
    return {name: data for name in sorted(valid)
            if (data := _read(universe_dir, name, max_bytes=max_bytes)) is not None}


def ledger_path(universe_dir: Path) -> Path:
    return _path(universe_dir, LEDGER)


def read_ledger(universe_dir: Path) -> bytes | None:
    return _read(universe_dir, LEDGER)


def write_ledger(universe_dir: Path, data: bytes, *, append: bool = False) -> None:
    _write(universe_dir, LEDGER, data, append=append)


def open_ledger_lock(universe_dir: Path) -> int:
    center = center_from_path(universe_dir)
    if fs._POSIX:
        with _directory(universe_dir) as directory:
            fd = os.open(LEDGER_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(fd)
                raise OSError("invalid ledger lock")
            return fd
    root = Path(universe_dir).parent
    center = center_from_path(universe_dir)
    parent = _windows_parent(root, [center, LEDGER_LOCK], create=False)
    try:
        _lstat_nofollow_windows(root, f"{center}/{LEDGER_LOCK}")
    except FileNotFoundError:
        pass
    return os.open(parent / LEDGER_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
