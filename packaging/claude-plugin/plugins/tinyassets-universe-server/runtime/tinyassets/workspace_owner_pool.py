"""Where a command center's workspace leases live, and who may write there.

The daemon cannot make a directory the owner owns: it holds no CAP_CHOWN, and
a command center's root admits the owner r-x only (``role_center_admission``).
A node cell, though, mounts a workspace only when that directory is
``(owner, owner)`` -- so the LEASE has to be created by the owner's own cell.

This module is the one place that reconciles those two facts. The daemon
prepares the pool PARENT it already owns (``<center>/workspaces`` and the pool
or repository directory beneath it) with two ACLs:

* access: this one owner rwx, nobody else anything -- so the owner's cell can
  create its lease there, and no other owner can;
* default: the daemon rwx -- so every lease, repository and object the cell
  creates below is one the daemon can still open, measure and reclaim.

Then the cell creates the lease (``workspace_fs.create_cell_lease_dir``). The
entropy rule on a lease name still holds: the parent has two writers, and the
unguessable name is what keeps either from targeting the other's.

A center that forward migration relabelled already has an owner-owned
``workspaces``; that is accepted as it stands, because the owner can write its
own directory and the daemon's access is whatever the migration granted. If
that access is gone, the walk below refuses by name rather than guessing.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

from tinyassets import role_modes
from tinyassets.workspace_fs import (
    OWNER_WORK_DIR_MODE,
    UnsafePoolPath,
    open_dir_nofollow,
    open_subdir_nofollow,
)

#: ``<center>/workspaces``: ``workspace_pool.WORKSPACES_DIR``, spelled there.
#: The scratch pool is a directory INSIDE it, so a scratch lease is an owner
#: directory in the owner's own tree and a node cell can mount it.
SCRATCH_DIR = "scratch"

__all__ = ["SCRATCH_DIR", "pool_parts", "prepare"]


def pool_parts(storage: str, repo_key: str) -> tuple[str, ...]:
    """The center-relative directories the DAEMON owns above a lease."""
    from tinyassets.workspace_pool import WORKSPACES_DIR

    if storage == "scratch":
        return (WORKSPACES_DIR, SCRATCH_DIR)
    if storage == "universe":
        return (WORKSPACES_DIR, repo_key)
    raise ValueError(f"unknown workspace storage class: {storage!r}")


def _acl_bytes(owner: int, named: dict[int, int], *, mask: int) -> bytes:
    """One packer for both ACLs; the admission module owns the byte format."""
    from tinyassets.role_center_admission import _acl

    return _acl(owner, named, mask=mask)


def _label(fd: int, name: str, *, machine: int) -> None:
    """Give exactly this owner write access, and the daemon access below."""
    access = _acl_bytes(7, {machine: 7}, mask=7)
    default = _acl_bytes(7, {role_modes.DAEMON_UID: 7}, mask=7)
    os.fchmod(fd, OWNER_WORK_DIR_MODE)
    for attribute, wanted in (
        ("system.posix_acl_access", access), ("system.posix_acl_default", default),
    ):
        os.setxattr(fd, attribute, wanted)
        if os.getxattr(fd, attribute) != wanted:
            raise UnsafePoolPath(f"pool directory {name!r} ACL readback differs")
    info = os.fstat(fd)
    if stat.S_IMODE(info.st_mode) != OWNER_WORK_DIR_MODE:
        raise UnsafePoolPath(f"pool directory {name!r} did not keep its owner mode")


def prepare(center: Path, parts: tuple[str, ...], *, machine: int) -> Path:
    """Create and label every pool directory above a lease; return the parent.

    Idempotent, and never creates the center root itself (only admission does
    that). Each component is created and opened through the descriptor of the
    one above it, so no step re-resolves a path by name.
    """
    if os.name != "posix":
        raise NotImplementedError("an owner workspace pool needs POSIX openat")
    if not parts or any(
            not part or part in (".", "..") or "/" in part or "\\" in part for part in parts):
        raise ValueError("a pool parent is one or more plain directory names")
    daemon = os.geteuid()
    opened = [open_dir_nofollow(center)]
    try:
        for part in parts:
            try:
                os.mkdir(part, OWNER_WORK_DIR_MODE, dir_fd=opened[-1])
            except FileExistsError:
                pass
            except PermissionError as exc:
                raise UnsafePoolPath(
                    f"the daemon may not create {part!r} in this command center: "
                    f"{exc.strerror}"
                ) from None
            try:
                opened.append(open_subdir_nofollow(opened[-1], part))
            except PermissionError as exc:
                raise UnsafePoolPath(
                    f"the daemon may not open the workspace pool directory {part!r} "
                    f"({exc.strerror}); its label admits no daemon access"
                ) from None
            info = os.fstat(opened[-1])
            if not stat.S_ISDIR(info.st_mode):
                raise UnsafePoolPath(f"pool directory {part!r} is not a directory")
            if info.st_uid == daemon:
                _label(opened[-1], part, machine=machine)
            elif info.st_uid != machine:
                raise UnsafePoolPath(
                    f"pool directory {part!r} is owned by uid {info.st_uid}, neither "
                    "the daemon nor this command center's owner"
                )
    finally:
        for fd in opened:
            try:
                os.close(fd)
            except OSError:
                pass
    return Path(center, *parts)
