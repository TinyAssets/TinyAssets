"""Host-owned files beside an owner's box, anchored under the data root."""

from __future__ import annotations

import os
import re
import stat
import uuid
from contextlib import contextmanager
from pathlib import Path

from tinyassets import workspace_fs as fs
from tinyassets.storage import data_dir
from tinyassets.universe_files import _lstat_nofollow_windows, _windows_parent

PAUSE = ".pause"
WORKER_HEARTBEAT = ".worker_supervisor.json"
WORKER_HEARTBEAT_PATTERN = ".worker_supervisor.<id>.json"
LEDGER = "auto_ship_attempts.jsonl"
LEDGER_LOCK = ".auto_ship_attempts.jsonl.lock"
CENTER_FILE_HOMES: dict[str, str] = {
    PAUSE: "host",
    WORKER_HEARTBEAT: "host",
    WORKER_HEARTBEAT_PATTERN: "host",
    LEDGER: "host",
    LEDGER_LOCK: "host",
    "soul.md": "box",
}


def _center(center: str) -> str:
    if (not isinstance(center, str) or not center or center in {".", ".."}
            or center.startswith(".") or any(c in center for c in "/\\\0")):
        raise ValueError(f"unsafe command center id: {center!r}")
    return center


def center_from_path(universe_dir: Path) -> str:
    path = Path(universe_dir)
    center = _center(path.name)
    if os.path.abspath(path.parent) != os.path.abspath(data_dir()):
        raise ValueError(f"command center path is outside data root: {path}")
    return center


def _path(center: str, name: str) -> Path:
    return data_dir() / _center(center) / name


@contextmanager
def _directory(center: str):
    center = _center(center)
    root = fs.open_dir_nofollow(Path(data_dir()).absolute())
    try:
        child = fs.open_subdir_nofollow(root, center)
        try:
            yield child
        finally:
            os.close(child)
    finally:
        os.close(root)


def _read(center: str, name: str, *, max_bytes: int = 16 * 1024 * 1024) -> bytes | None:
    if fs._POSIX:
        with _directory(center) as directory:
            try:
                return fs.read_regular_file_beneath(directory, name, max_bytes=max_bytes)
            except FileNotFoundError:
                return None
    root = Path(data_dir())
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


def _write(center: str, name: str, data: bytes, *, append: bool = False) -> None:
    if fs._POSIX:
        with _directory(center) as directory:
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
    root = Path(data_dir())
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


def _remove(center: str, name: str) -> None:
    if fs._POSIX:
        with _directory(center) as directory:
            info = os.stat(name, dir_fd=directory, follow_symlinks=False)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise OSError(f"invalid host file: {name}")
            os.unlink(name, dir_fd=directory)
            os.fsync(directory)
    else:
        _lstat_nofollow_windows(Path(data_dir()), f"{_center(center)}/{name}").unlink()


def is_paused(center: str) -> bool:
    return _read(center, PAUSE, max_bytes=4096) is not None


def set_paused(center: str, timestamp: str) -> None:
    _write(center, PAUSE, timestamp.encode("utf-8"))


def clear_paused(center: str) -> None:
    _remove(center, PAUSE)


def worker_heartbeat_name(worker_id: str | None = None) -> str:
    clean = re.sub(r"[^A-Za-z0-9_.-]+", "-", (worker_id or "").strip()).strip(".-")
    return f".worker_supervisor.{clean}.json" if clean and clean != "default" else WORKER_HEARTBEAT


def write_worker_heartbeat(center: str, worker_id: str | None, data: bytes) -> None:
    _write(center, worker_heartbeat_name(worker_id), data)


def read_worker_heartbeats(center: str, *, max_bytes: int) -> dict[str, bytes]:
    if fs._POSIX:
        with _directory(center) as directory:
            names = os.listdir(directory)
    else:
        root = _lstat_nofollow_windows(Path(data_dir()), _center(center))
        names = os.listdir(root)
    valid = [name for name in names if name == WORKER_HEARTBEAT or (
        name.startswith(".worker_supervisor.") and name.endswith(".json")
        and len(name) > len(".worker_supervisor..json"))]
    return {name: data for name in sorted(valid)
            if (data := _read(center, name, max_bytes=max_bytes)) is not None}


def ledger_path(center: str) -> Path:
    return _path(center, LEDGER)


def read_ledger(center: str) -> bytes | None:
    return _read(center, LEDGER)


def write_ledger(center: str, data: bytes, *, append: bool = False) -> None:
    _write(center, LEDGER, data, append=append)


def open_ledger_lock(center: str) -> int:
    center = _center(center)
    if fs._POSIX:
        with _directory(center) as directory:
            fd = os.open(LEDGER_LOCK, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC,
                         0o600, dir_fd=directory)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                os.close(fd)
                raise OSError("invalid ledger lock")
            return fd
    root = Path(data_dir())
    parent = _windows_parent(root, [center, LEDGER_LOCK], create=False)
    try:
        _lstat_nofollow_windows(root, f"{center}/{LEDGER_LOCK}")
    except FileNotFoundError:
        pass
    return os.open(parent / LEDGER_LOCK, os.O_RDWR | os.O_CREAT, 0o600)
