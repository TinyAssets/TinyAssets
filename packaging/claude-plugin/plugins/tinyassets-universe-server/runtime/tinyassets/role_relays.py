"""Daemon-owned relay sockets; no sidecar directory is exposed to a cell."""
from __future__ import annotations

import os
import re
import stat
import struct
from contextlib import contextmanager

from tinyassets import role_modes, workspace_fs


@contextmanager
def directory(path, *, create):
    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

    if (path.parent.parent.name != UNIVERSE_SIDECARS_DIR
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", path.parent.name)
            or not re.fullmatch(
                r"(?:egress-[0-9]+|engine-[0-9]+-[a-f0-9]{12}|ta-[a-f0-9]{32}|registry-[a-f0-9]{32})\.sock",
                path.name)):
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


def pin_for_owner(path, center, uid, *, kind):
    """Grant one owner access to an exact daemon socket, never its parent."""
    from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST
    from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

    pattern = (rf'egress-{os.getpid()}\.sock' if kind == 'egress'
               else r'registry-[a-f0-9]{32}\.sock' if kind == 'registry'
               else r'ta-[a-f0-9]{32}\.sock' if kind == 'ta'
               else rf'engine-{os.getpid()}-[a-f0-9]{{12}}\.sock' if kind == 'engine' else '')
    if (type(uid) is not int or not OWNER_ID_FIRST <= uid <= OWNER_ID_LAST
            or path.parent != center.parent / UNIVERSE_SIDECARS_DIR / center.name
            or not pattern or not re.fullmatch(pattern, path.name)):
        raise PermissionError('relay socket is outside the admitted owner scope')
    with directory(path, create=False) as parent:
        fd = os.open(path.name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISSOCK(info.st_mode) or info.st_nlink != 1
                or os.geteuid() != 1001 or info.st_uid != 1001
                or info.st_gid != role_modes.WORK_GID):
            raise PermissionError('relay socket is not a protected daemon inode')
        undefined = 0xffffffff
        acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in (
            (1, 6, undefined), (2, 6, uid), (4, 0, undefined),
            (16, 6, undefined), (32, 0, undefined)))
        # Socket inodes cannot be opened for read/write. This magic fd path
        # names the pinned inode even if its directory entry is replaced.
        pinned = f'/proc/self/fd/{fd}'
        os.setxattr(pinned, 'system.posix_acl_access', acl)
        if os.getxattr(pinned, 'system.posix_acl_access') != acl:
            raise PermissionError('relay socket owner ACL readback differs')
        after = os.fstat(fd)
        if ((after.st_dev, after.st_ino, after.st_uid, after.st_gid) !=
                (info.st_dev, info.st_ino, info.st_uid, info.st_gid)
                or stat.S_IMODE(after.st_mode) != role_modes.RELAY_SOCKET_MODE):
            raise PermissionError('relay socket custody changed')
        # bwrap resolves an O_PATH bind source through its protected parent.
        # Grant only this owner search, never listing/write or a parent mount.
        traverse = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in (
            (1, 7, undefined), (2, 1, uid), (4, 0, undefined),
            (16, 1, undefined), (32, 0, undefined)))
        with directory(path, create=False) as parent:
            os.setxattr(parent, 'system.posix_acl_access', traverse)
            if (os.getxattr(parent, 'system.posix_acl_access') != traverse
                    or stat.S_IMODE(os.fstat(parent).st_mode) != role_modes.SIDECAR_DIRECTORY_MODE):
                raise PermissionError('relay traverse ACL readback differs')
        return fd
    except BaseException:
        os.close(fd)
        raise


def remove(path, expected):
    """Remove only this invocation's still-named daemon socket."""
    with directory(path, create=False) as parent:
        try:
            info = os.stat(path.name, dir_fd=parent, follow_symlinks=False)
        except FileNotFoundError:
            return
        if ((info.st_dev, info.st_ino) != expected or not stat.S_ISSOCK(info.st_mode)
                or info.st_uid != role_modes.DAEMON_UID or info.st_nlink != 1):
            raise PermissionError('relay socket changed before removal')
        os.unlink(path.name, dir_fd=parent)
