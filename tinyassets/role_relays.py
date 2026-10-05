"""Daemon-owned relay sockets; no sidecar directory is exposed to a cell."""
from __future__ import annotations

import os
import re
import stat
from contextlib import contextmanager

from tinyassets import role_modes, workspace_fs


@contextmanager
def directory(path, *, create):
    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

    if (path.parent.parent.name != UNIVERSE_SIDECARS_DIR
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", path.parent.name)
            or not re.fullmatch(r"(?:egress-[0-9]+|engine-[0-9]+-[a-f0-9]{12})\.sock", path.name)):
        raise PermissionError("invalid role relay path")
    descriptor = workspace_fs.open_dir_nofollow(path.parent.parent.parent)
    try:
        for component, gid, mode in (
            (UNIVERSE_SIDECARS_DIR, role_modes.DAEMON_UID, role_modes.SIDECAR_PARENT_MODE),
            (path.parent.name, role_modes.WORK_GID, role_modes.SIDECAR_DIRECTORY_MODE),
        ):
            if create:
                try:
                    os.mkdir(component, 0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = workspace_fs.open_subdir_nofollow(descriptor, component)
            os.close(descriptor)
            descriptor = child
            info = os.fstat(descriptor)
            if info.st_uid != role_modes.DAEMON_UID:
                raise PermissionError("relay directory is not daemon owned")
            if create:
                os.fchown(descriptor, -1, gid)
                os.fchmod(descriptor, mode)
            info = os.fstat(descriptor)
            if info.st_gid != gid or stat.S_IMODE(info.st_mode) != mode:
                raise PermissionError("relay directory permissions unavailable")
        yield descriptor
    finally:
        os.close(descriptor)


def identity(path):
    with directory(path, create=False) as descriptor:
        info = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        if (not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1
                or info.st_uid != role_modes.DAEMON_UID or info.st_gid != role_modes.WORK_GID
                or stat.S_IMODE(info.st_mode) != role_modes.RELAY_SOCKET_MODE):
            raise PermissionError("relay socket identity unavailable")
        return info.st_dev, info.st_ino


def bind(server, path):
    with directory(path, create=True) as descriptor:
        try:
            previous = os.stat(path.name, dir_fd=descriptor, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            if (not stat.S_ISSOCK(previous.st_mode) or previous.st_nlink != 1
                    or previous.st_uid != role_modes.DAEMON_UID):
                raise PermissionError("refusing non-daemon or non-socket relay replacement")
            os.unlink(path.name, dir_fd=descriptor)
        # Linux AF_UNIX lacks bindat. The pinned, daemon-only directory supplies
        # that operation without changing the process cwd or umask in a thread.
        server.bind(f"/proc/self/fd/{descriptor}/{path.name}")
        os.chown(path.name, -1, role_modes.WORK_GID, dir_fd=descriptor, follow_symlinks=False)
        os.chmod(path.name, role_modes.RELAY_SOCKET_MODE, dir_fd=descriptor,
                 follow_symlinks=False)
        return identity(path)
