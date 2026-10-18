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


def _walk(fd: int, device: int, mount: str, *, erase: bool) -> int:
    # Keep opened directories, not owner-controlled path strings. Postorder
    # traversal has no Python recursion/depth bound and never follows links.
    stack = [(fd, iter(os.listdir(fd)), None)]
    count = 0
    try:
        while stack:
            current_fd, entries, parent_name = stack[-1]
            name = next(entries, None)
            if name is None:
                if erase:
                    os.fsync(current_fd)
                stack.pop()
                if stack:
                    os.close(current_fd)
                    if erase:
                        os.rmdir(parent_name, dir_fd=stack[-1][0])
                continue
            info = os.stat(name, dir_fd=current_fd, follow_symlinks=False)
            if info.st_dev != device:
                raise BoxError("owner tree contains a foreign mount")
            entry = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=current_fd)
            try:
                if _mount(entry) != mount:
                    raise BoxError("owner tree contains a foreign mount")
            finally:
                os.close(entry)
            if stat.S_ISDIR(info.st_mode):
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                                dir_fd=current_fd)
                try:
                    current = os.fstat(child)
                    if (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
                        raise BoxError("owner tree changed after quiescence")
                    if _mount(child) != mount:
                        raise BoxError("owner tree contains a foreign mount")
                    children = iter(os.listdir(child))
                except BaseException:
                    os.close(child)
                    raise
                stack.append((child, children, name))
            elif erase:
                os.unlink(name, dir_fd=current_fd)
            count += 1
    finally:
        for child, _, _ in stack[1:]:
            os.close(child)
    return count


def quarantine(store: Path, uid: int, *, destination_root: Path) -> Path:
    """Detach an unerased tree into root-private storage on the same filesystem."""
    import tempfile

    GvisorRuntime.box_id(uid)
    target = Path(tempfile.mkdtemp(prefix=".tinyassets-erasure-quarantine.", dir=destination_root))
    os.chown(target, 0, 0)
    target.chmod(0o700)
    parent = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    destination = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.rename(str(uid), str(uid), src_dir_fd=parent, dst_dir_fd=destination)
        os.fsync(parent)
        os.fsync(destination)
    finally:
        os.close(destination)
        os.close(parent)
    return target / str(uid)


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


def socket_tree(store: Path, uid: int) -> None:
    """Remove the stopped box's writable socket directory without following links."""
    GvisorRuntime.box_id(uid)
    parent = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        try:
            root = os.open(str(uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
        except FileNotFoundError:
            return
        try:
            if _mount(root) != _mount(parent):
                raise BoxError("socket tree root is a foreign mount")
            _walk(root, os.fstat(root).st_dev, _mount(root), erase=True)
        finally:
            os.close(root)
        os.rmdir(str(uid), dir_fd=parent)
        os.fsync(parent)
    finally:
        os.close(parent)
