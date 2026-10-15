"""Host-only descriptor erasure after the durable owner fence and runtime stop."""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.provider import BoxError


def _mount(fd: int) -> str:
    # st_dev does not detect a same-filesystem bind mount. fdinfo identifies
    # the actual mount, including bind mounts, without following owner paths.
    for line in Path(f"/proc/self/fdinfo/{fd}").read_text().splitlines():
        if line.startswith("mnt_id:"):
            return line.split()[1]
    raise BoxError("kernel mount identity is unavailable")


def _walk(fd: int, device: int, mount: str, *, erase: bool, depth: int = 0) -> int:
    if depth > 128:
        raise BoxError("owner tree exceeds the erasure depth bound")
    count = 0
    for name in os.listdir(fd):
        info = os.stat(name, dir_fd=fd, follow_symlinks=False)
        if info.st_dev != device:
            raise BoxError("owner tree contains a foreign mount")
        entry = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=fd)
        try:
            if _mount(entry) != mount:
                raise BoxError("owner tree contains a foreign mount")
        finally:
            os.close(entry)
        if stat.S_ISDIR(info.st_mode):
            child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            try:
                current = os.fstat(child)
                if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                    raise BoxError("owner tree changed after quiescence")
                if _mount(child) != mount:
                    raise BoxError("owner tree contains a foreign mount")
                count += _walk(child, device, mount, erase=erase, depth=depth + 1)
            finally:
                os.close(child)
            if erase:
                os.rmdir(name, dir_fd=fd)
        elif erase:
            os.unlink(name, dir_fd=fd)
        count += 1
    if erase:
        os.fsync(fd)
    return count


def tree(store: Path, uid: int, *, erase: bool) -> int:
    GvisorRuntime.box_id(uid)
    parent = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            root = os.open(str(uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return 0
        try:
            info = os.fstat(root)
            if (info.st_uid, info.st_gid) != (uid, uid) or stat.S_IMODE(info.st_mode) != 0o700:
                raise BoxError("erasure target is not the permanent owner's private tree")
            mount = _mount(parent)
            if _mount(root) != mount:
                raise BoxError("owner tree root is a foreign mount")
            count = _walk(root, info.st_dev, mount, erase=erase) + 1
        finally:
            os.close(root)
        if erase:
            os.rmdir(str(uid), dir_fd=parent)
            os.fsync(parent)
        return count
    finally:
        os.close(parent)
