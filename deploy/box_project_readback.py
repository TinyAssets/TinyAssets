"""Read back XFS project assignments and hard limits after quota mutations."""

from __future__ import annotations

import os
import stat
import struct
import subprocess
from pathlib import Path
from typing import Callable

FS_IOC_FSGETXATTR = 0x801C581F
FS_XFLAG_PROJINHERIT = 0x200
SAMPLE_ENTRIES = 64


def read_project(path: Path) -> tuple[int, bool]:
    """Read fsx_projid and FS_XFLAG_PROJINHERIT from the inode itself."""
    import fcntl

    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        flags, _, _, project, _, _ = struct.unpack(
            "=IIIII8s", fcntl.ioctl(fd, FS_IOC_FSGETXATTR, bytes(28)))
    finally:
        os.close(fd)
    return project, bool(flags & FS_XFLAG_PROJINHERIT)


def verify_tree(
    tree: Path, project: int, inherit: bool, *,
    reader: Callable[[Path], tuple[int, bool]] = read_project,
) -> None:
    """Check the root and a bounded deterministic sample of real descendants."""
    tree = Path(tree)
    if not stat.S_ISDIR(tree.lstat().st_mode):
        raise RuntimeError(f"quota tree is not a real directory: {tree}")
    paths = [tree]
    for directory, dirs, files in os.walk(tree, followlinks=False):
        dirs.sort()
        for name in sorted([*dirs, *files]):
            path = Path(directory) / name
            if path.is_symlink():
                continue
            paths.append(path)
            if len(paths) > SAMPLE_ENTRIES:
                break
        if len(paths) > SAMPLE_ENTRIES:
            break
    for path in paths:
        found = reader(path)
        if found != (project, inherit):
            raise RuntimeError(
                f"XFS project read-back mismatch at {path}: "
                f"expected {(project, inherit)}, found {found}")


def read_hard_limit(store: Path, project: int) -> int:
    """Return the reported hard block limit in KiB; reject missing/ambiguous rows."""
    result = subprocess.run(
        ["xfs_quota", "-x", "-c", "report -p -n", str(store)],
        check=True, capture_output=True, text=True,
    )
    if result.stderr.strip():
        raise RuntimeError(f"XFS quota report failed: {result.stderr.strip()}")
    limits = []
    for line in result.stdout.splitlines():
        fields = line.split()
        if fields and fields[0].lstrip("#") == str(project):
            if len(fields) < 4 or not fields[3].isdecimal():
                raise RuntimeError(f"invalid XFS quota report row for project {project}")
            limits.append(int(fields[3]))
    if len(limits) != 1:
        raise RuntimeError(f"XFS quota report has {len(limits)} rows for project {project}")
    return limits[0]


def verify_owner(
    store: Path, tree: Path, project: int, hard_bytes: int, *,
    reader: Callable[[Path], tuple[int, bool]] = read_project,
    limit_reader: Callable[[Path, int], int] = read_hard_limit,
) -> None:
    verify_tree(tree, project, True, reader=reader)
    found = limit_reader(store, project)
    expected = hard_bytes // 1024
    if found != expected:
        raise RuntimeError(
            f"XFS hard limit read-back mismatch for project {project}: "
            f"expected {expected} KiB, found {found} KiB")
