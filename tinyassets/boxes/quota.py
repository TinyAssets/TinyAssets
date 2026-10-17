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
            check=True, capture_output=True, text=True, timeout=10,
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
                check=True, capture_output=True, text=True, timeout=10,
            )
        return tree

    def provision(self, uid: int, bhard_bytes: int, center: str) -> None:
        """Initialize only empty, root-owned roots; never repair owner-written trees."""
        import fcntl

        from tinyassets.broker.owner_identities import validate_center

        GvisorRuntime.box_id(uid)
        validate_center(center)
        if type(bhard_bytes) is not int or bhard_bytes <= 0 or bhard_bytes % 1024:
            raise BoxError("owner storage bound must be positive KiB")
        self._mounted()
        parent = os.open(self.store, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        try:
            try:
                os.mkdir(str(uid), 0o700, dir_fd=parent)
            except FileExistsError:
                pass
            fd = os.open(str(uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                info = os.fstat(fd)
                if info.st_uid == 0:
                    if info.st_gid != 0 or stat.S_IMODE(info.st_mode) != 0o700 or os.listdir(fd):
                        raise BoxError("incomplete owner root is not private and empty")
                    fields = list(struct.unpack("=IIIII8s", fcntl.ioctl(fd, 0x801c581f, bytes(28))))
                    if fields[3] not in (0, uid):
                        raise BoxError("incomplete root belongs to another quota project")
                    fields[0] |= 0x200
                    fields[3] = uid
                    # FS_IOC_FSSETXATTR on this empty descriptor, never recursive.
                    fcntl.ioctl(fd, 0x401c5820, struct.pack("=IIIII8s", *fields))
                    subprocess.run(["xfs_quota", "-x", "-c",
                                    f"limit -p bhard={bhard_bytes // 1024}k {uid}",
                                    str(self.store)],
                                   check=True, capture_output=True, text=True)
                    os.fsync(fd)
                    os.fchown(fd, uid, uid)
                    os.fsync(fd)
                    os.fsync(parent)
            finally:
                os.close(fd)
            self.admit(uid, bhard_bytes)
            root = os.open(str(uid), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
            try:
                for name in (center, ".agent-home"):
                    try:
                        os.mkdir(name, 0o700, dir_fd=root)
                        created = True
                    except FileExistsError:
                        created = False
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root)
                    try:
                        info = os.fstat(child)
                        if created or (info.st_uid == 0 and info.st_gid == 0
                                       and stat.S_IMODE(info.st_mode) == 0o700
                                       and not os.listdir(child)):
                            os.fchown(child, uid, uid)
                            os.fsync(child)
                            os.fsync(root)
                        info = os.fstat(child)
                        if (info.st_uid, info.st_gid) != (uid, uid):
                            raise BoxError("owner center/home directory has the wrong identity")
                    finally:
                        os.close(child)
            finally:
                os.close(root)
        finally:
            os.close(parent)

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
                       check=True, capture_output=True, text=True, timeout=10)
