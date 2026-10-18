"""Host-only descriptor erasure after the durable owner fence and runtime stop."""

from __future__ import annotations

import os
import re
import stat
import subprocess
from pathlib import Path

from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.provider import BoxError


def verify_store(store: Path, fd: int) -> None:
    """Verify even an absent UID against setup's root-owned filesystem proof.

    Retained/quarantine directories may be below the mount root. Compare the
    opened descriptor too, so path replacement cannot substitute another device.
    """
    import json

    try:
        result = subprocess.run(
            ["findmnt", "--json", "--target", str(store), "-o", "TARGET,FSTYPE,UUID"],
            check=True, capture_output=True, text=True, timeout=10)
        mount, = json.loads(result.stdout)["filesystems"]
        root = Path(mount["target"])
        # findmnt's device/UUID also match a bind of an empty subtree. Match
        # the opened descriptor to the last (visible) mount at this path.
        mounts = [line.split() for line in Path("/proc/self/mountinfo").read_text().splitlines()]
        at_root = [row for row in mounts if re.sub(
            r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), row[4]) == str(root)]
        if not at_root or at_root[-1][3] != "/" or at_root[-1][0] != _mount(fd):
            raise BoxError("owner store mount must expose the whole filesystem root")
        device = root.stat().st_dev
        if (device == root.parent.stat().st_dev or device != os.fstat(fd).st_dev
                or mount["fstype"] != "xfs" or not mount["uuid"]):
            raise BoxError("owner store is not the expected mounted XFS filesystem")
        proof = os.open(root.parent / ("." + root.name + "-identity"),
                        os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(proof) as stream:
            info = os.fstat(stream.fileno())
            if (info.st_uid != 0 or not stat.S_ISREG(info.st_mode)
                    or info.st_mode & 0o077 or info.st_nlink != 1):
                raise BoxError("owner store identity is not root-private")
            if stream.read() != "xfs " + mount["uuid"] + "\n":
                raise BoxError("owner store filesystem identity changed")
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        raise BoxError("owner store mount identity cannot be verified") from exc


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
    GvisorRuntime.box_id(uid)
    parent = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        verify_store(store, parent)
    finally:
        os.close(parent)
    target = destination_root
    target.mkdir(mode=0o700, exist_ok=True)
    os.chown(target, 0, 0)
    target.chmod(0o700)
    parent = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    destination = os.open(target, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        verify_store(store, parent)
        verify_store(target, destination)
        os.rename(str(uid), str(uid), src_dir_fd=parent, dst_dir_fd=destination)
        os.fsync(parent)
        os.fsync(destination)
        outer = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            os.fsync(outer)
        finally:
            os.close(outer)
    finally:
        os.close(destination)
        os.close(parent)
    return target / str(uid)


def tree(store: Path, uid: int, *, erase: bool) -> int:
    GvisorRuntime.box_id(uid)
    parent = os.open(store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        verify_store(store, parent)
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
