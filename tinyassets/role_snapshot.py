"""Descriptor-only ACLs for daemon-owned, sealed owner launch snapshots.

These are protected credential metadata, not engine-owned workspace files.
The daemon remains their only writer; one dedicated identity gets read/traverse.
No shared work-group access or default ACL is retained.
"""
from __future__ import annotations

import os
import stat
import struct
from pathlib import Path


def owner_uid(universe: Path) -> int | None:
    from tinyassets import role_decoder, workspace_fs
    from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST

    if role_decoder._bounded_client is None:
        return None
    fd = workspace_fs.open_dir_nofollow(universe)
    try:
        info = os.fstat(fd)
        if info.st_uid != 1001 or not OWNER_ID_FIRST <= info.st_gid <= OWNER_ID_LAST:
            raise PermissionError('snapshot requires a migrated daemon-owned center')
        return info.st_gid
    finally:
        os.close(fd)


def seal(fd: int, uid: int, *, directory: bool, traverse_only: bool = False) -> None:
    from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST

    info = os.fstat(fd)
    if (type(uid) is not int or not OWNER_ID_FIRST <= uid <= OWNER_ID_LAST
            or os.geteuid() != 1001 or info.st_uid != 1001
            or (directory and not stat.S_ISDIR(info.st_mode))
            or (not directory and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1))):
        raise PermissionError('snapshot descriptor has invalid custody')
    # Linux POSIX ACL xattr version 2; tags USER_OBJ, USER, GROUP_OBJ, MASK,
    # OTHER. The kernel validates the ACL on the already-open inode.
    read = (1 if traverse_only else 5) if directory else 4
    undefined = 0xffffffff
    acl = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in (
        (1, 7 if directory else 4, undefined), (2, read, uid),
        (4, 0, undefined), (16, read, undefined), (32, 0, undefined)))
    # Remove inherited defaults before publishing this directory to its owner.
    if directory:
        import errno

        try:
            os.removexattr(fd, 'system.posix_acl_default')
        except OSError as exc:
            if exc.errno != errno.ENODATA:
                raise
    os.setxattr(fd, 'system.posix_acl_access', acl)
    if os.getxattr(fd, 'system.posix_acl_access') != acl:
        raise PermissionError('snapshot ACL readback differs')
    final = os.fstat(fd)
    if (final.st_uid != 1001 or (final.st_dev, final.st_ino) != (info.st_dev, info.st_ino)
            or final.st_mode & 0o007):
        raise PermissionError('snapshot custody changed while sealing')
    os.fsync(fd)
