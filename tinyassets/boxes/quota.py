"""XFS project quota assignment for the mounted owner box store."""

from __future__ import annotations

import os
import stat
import struct
import subprocess
from pathlib import Path

from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.provider import BoxError


class OwnerQuota:
    """Host-only quota operations; project ID is the permanent owner UID."""

    def __init__(self, store: Path) -> None:
        self.store = Path(store)

    def _mounted(self) -> None:
        result = subprocess.run(
            ["findmnt", "--mountpoint", str(self.store), "-no", "FSTYPE,OPTIONS"],
            check=True, capture_output=True, text=True,
        )
        fields = result.stdout.strip().split(None, 1)
        if len(fields) != 2 or fields[0] != "xfs":
            raise BoxError("owner box store must be an XFS mount")
        options = set(fields[1].split(","))
        if not options.intersection({"pquota", "prjquota"}) or "discard" in options:
            raise BoxError("owner box store needs project quotas without discard")

    def set_bound(self, uid: int, gid: int, bhard_bytes: int) -> Path:
        GvisorRuntime.box_id(uid)
        if type(gid) is not int or gid != uid:
            raise BoxError("owner UID/GID mismatch")
        if type(bhard_bytes) is not int or bhard_bytes <= 0 or bhard_bytes % 1024:
            raise BoxError("owner storage bound must be positive KiB")
        self._mounted()
        tree = self.store / str(uid)
        try:
            info = tree.lstat()
        except FileNotFoundError:
            tree.mkdir(mode=0o700)
            os.chown(tree, uid, gid)
            info = tree.lstat()
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (uid, gid):
            raise BoxError("owner tree is not a directory of its permanent UID/GID")
        if stat.S_IMODE(info.st_mode) != 0o700:
            raise BoxError("owner tree must have mode 0700")
        for operation in (
            f"project -s -p {tree} {uid}",
            f"limit -p bhard={bhard_bytes // 1024}k {uid}",
        ):
            subprocess.run(
                ["xfs_quota", "-x", "-c", operation, str(self.store)],
                check=True, capture_output=True, text=True,
            )
        return tree

    def admit(self, uid: int, bhard_bytes: int) -> None:
        """Verify the migrated project; never recursively retag a live owner tree."""
        import fcntl

        GvisorRuntime.box_id(uid)
        if type(bhard_bytes) is not int or bhard_bytes <= 0 or bhard_bytes % 1024:
            raise BoxError("owner storage bound must be positive KiB")
        self._mounted()
        fd = os.open(self.store / str(uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            info = os.fstat(fd)
            if (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (uid, uid, 0o700):
                raise BoxError("owner quota root has the wrong identity or mode")
            # linux/fs.h: _IOR('X',31,struct fsxattr), five u32s and eight pad bytes.
            flags, _, _, project, _, _ = struct.unpack("=IIIII8s", fcntl.ioctl(
                fd, 0x801c581f, bytes(28)))
            if project != uid or not flags & 0x200:  # FS_XFLAG_PROJINHERIT
                raise BoxError("owner root lacks its migrated inheriting quota project")
        finally:
            os.close(fd)
        subprocess.run(["xfs_quota", "-x", "-c",
                        f"limit -p bhard={bhard_bytes // 1024}k {uid}", str(self.store)],
                       check=True, capture_output=True, text=True)
