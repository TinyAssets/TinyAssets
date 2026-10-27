"""C1/C2: opaque XFS images; no owner-content access in the shared lifecycle.

Only the admission authority supplies UIDs. The caller stops the runtime before
detach and keeps admission closed through restore and generation publication.
Mounts live in the lifecycle service's private mount namespace; application
services must not receive this mount root. Lane a owns that service wiring.
"""

from __future__ import annotations

import gzip
import hashlib
import os
import re
import subprocess
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path


class VolumeError(RuntimeError):
    """The requested lifecycle transition did not complete."""


@dataclass(frozen=True)
class MountHandle:
    uid: int
    device: str
    mountpoint: Path

    def oci_mount(self) -> dict:
        """Install only in this UID's spec; use runsc's root-only overlay."""
        return {"destination": "/home/owner", "type": "bind",
                "source": str(self.mountpoint),
                "options": ["bind", "rw", "nosuid", "nodev", "private"]}


class OwnerVolumes:
    """Host-only primitive, configured with trusted roots and an rclone remote.

    flock serializes processes AND threads (each operation opens its own lock
    descriptor). Lock files are permanent: unlinking one would split the lock.
    No fleet lock is held while operating on an owner.
    """

    def __init__(self, store: Path, mount_root: Path, remote: str) -> None:
        self.store = Path(store).absolute()
        self.mount_root = Path(mount_root).absolute()
        self.remote = remote.rstrip("/")
        self._held = threading.local()
        if not self.remote or ":" not in self.remote:
            raise VolumeError("an explicit rclone remote is required")
        if self.store.is_relative_to(self.mount_root):
            raise VolumeError("image store must be outside the volume mount root")
        self.store.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.locks = self.store / ".locks"
        self.locks.mkdir(exist_ok=True, mode=0o700)
        # mkdir, not exists/is_dir/stat: even an existing mount is opaque.
        try:
            os.mkdir(self.mount_root, 0o700)
        except FileExistsError:
            pass
        info = self._run("xfs_info", str(self.store)).stdout
        if not re.search(r"\breflink=1\b", info):
            raise VolumeError("outer image store must be XFS with reflink enabled")

    @staticmethod
    def _run(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(args, check=True, capture_output=True, text=True, timeout=3600)

    def _image(self, uid: int) -> Path:
        if type(uid) is not int or not 300001 <= uid <= 399999:
            raise VolumeError("an admitted permanent owner UID is required")
        return self.store / f"{uid}.img"

    @contextmanager
    def _lock(self, uid: int, *, blocking=True, shared=False):
        import fcntl

        self._image(uid)
        held = getattr(self._held, "uids", set())
        if uid in held:
            yield
            return
        mode = fcntl.LOCK_SH if shared else fcntl.LOCK_EX
        with (self.locks / str(uid)).open("a+b") as lock:
            fcntl.flock(lock, mode | (0 if blocking else fcntl.LOCK_NB))
            self._held.uids = held | {uid}
            try:
                yield
            finally:
                self._held.uids = held

    def operation(self, uid: int, *, blocking=True, shared=False):
        """C1/C2: serialize local execution only; the broker owns lifecycle authority."""
        return self._lock(uid, blocking=blocking, shared=shared)

    def size(self, uid: int) -> int:
        """Opaque image length, never a filesystem path inside a volume."""
        with self._lock(uid):
            return self._image(uid).stat().st_size

    def delete_snapshots(self, uid: int, snapshot_ids, released) -> dict[str, str]:
        """One deletion path; only map-released references may retire a pin.

        Returns one outcome per id: ``deleted``, ``pinned`` (a platform backup
        still pins it), or ``failed:<class>`` with a fixed class, never rclone's
        text. One failed object no longer hides what happened to the others.
        """
        from tinyassets.platform_backup import pinned_snapshots, retention_lock

        with self._lock(uid), retention_lock():
            if snapshot_ids is None:
                names = self._run("rclone", "lsf", "--recursive", "--files-only", self.remote,
                                  "--include", f"/{uid}/**").stdout.splitlines()
                snapshot_ids = [name.removeprefix(f"{uid}/").removesuffix(".img.gz")
                                for name in names]
            if (not isinstance(snapshot_ids, list) or not isinstance(released, list)
                    or any(not isinstance(item, str) or not re.fullmatch(r"[a-f0-9]{32}", item)
                           for item in [*snapshot_ids, *released])):
                raise VolumeError("snapshot references are required")
            try:
                pins = pinned_snapshots(self.remote)
            except (subprocess.SubprocessError, ValueError):
                # No empty-on-error fallback: an unreadable catalog deletes nothing.
                return dict.fromkeys(snapshot_ids, "failed:catalog_unreadable")
            outcomes = {}
            for snapshot_id in snapshot_ids:
                if (uid, snapshot_id) in pins and snapshot_id not in released:
                    outcomes[snapshot_id] = "pinned"
                    continue
                outcomes[snapshot_id] = "deleted"
                try:
                    self._run("rclone", "deletefile",
                              f"{self.remote}/{uid}/{snapshot_id}.img.gz")
                except subprocess.CalledProcessError as exc:
                    # rclone 3 is directory not found and 4 is file not found.
                    # The object is already gone, so the broker may forget the
                    # row. Any other exit keeps the row and names the code.
                    if exc.returncode not in {3, 4}:
                        outcomes[snapshot_id] = f"failed:rclone_exit_{exc.returncode}"
                except subprocess.TimeoutExpired:
                    outcomes[snapshot_id] = "failed:rclone_timeout"
            return outcomes

    @staticmethod
    def _sync(path: Path) -> None:
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _loops(self, uid: int) -> list[str]:
        result = self._run("losetup", "--associated", str(self._image(uid)),
                           "--noheadings", "--output", "NAME")
        return result.stdout.split()

    @staticmethod
    def _mounts() -> list[tuple[str, str]]:
        # Kernel metadata only; do not resolve or stat a mount target.
        def decode(value):
            return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m[1], 8)), value)

        result = []
        for line in Path("/proc/self/mountinfo").read_text().splitlines():
            left, right = line.split(" - ", 1)
            result.append((decode(left.split()[4]), decode(right.split()[1])))
        return result

    def _detached(self, uid: int) -> None:
        target = str(self.mount_root / str(uid))
        if self._loops(uid) or any(path == target for path, _ in self._mounts()):
            raise VolumeError("volume must be fully detached")

    def create(self, uid: int, size_bytes: int) -> None:
        """Format a sparse image exclusively; no existing image is overwritten."""
        with self._lock(uid):
            self._detached(uid)
            image = self._image(uid)
            if image.exists():
                raise FileExistsError(image)
            if type(size_bytes) is not int or size_bytes <= 0:
                raise VolumeError("positive image size required")
            stage = self.store / f".{uid}.create"
            prototype = self.store / f".{uid}.prototype"
            # mkfs assigns the root inode's owner without mounting/inspecting it.
            prototype.write_text(f"ignored\n0 0\nd--700 {uid} {uid}\n$\n")
            try:
                with stage.open("wb") as stream:
                    stream.truncate(size_bytes)
                self._run("mkfs.xfs", "-f", "-p", str(prototype), str(stage))
                self._sync(stage)
                os.link(stage, image)  # exclusive publication, unlike replace
                self._sync(self.store)
            finally:
                prototype.unlink(missing_ok=True)
                stage.unlink(missing_ok=True)

    def attach(self, uid: int) -> MountHandle:
        with self._lock(uid):
            self._detached(uid)
            image = self._image(uid)
            if not image.is_file():
                raise FileNotFoundError(image)
            target = self.mount_root / str(uid)
            try:
                os.mkdir(target, 0o700)
            except FileExistsError:
                pass
            device = self._run("losetup", "--find", "--show", str(image)).stdout.strip()
            try:
                self._run("mount", "--no-canonicalize", "-t", "xfs", "-o",
                          "nosuid,nodev", device, str(target))
                self._run("mount", "--no-canonicalize", "--make-private", str(target))
            except BaseException:
                self._detach(uid)
                raise
            return MountHandle(uid, device, target)

    def _detach(self, uid: int) -> None:
        target = str(self.mount_root / str(uid))
        devices = self._loops(uid)
        mounts = self._mounts()
        if any(path != target and device in devices for path, device in mounts):
            raise VolumeError("runtime filesystem references remain")
        if any(path == target for path, _ in mounts):
            # A busy mount fails here. Never -l, -f, or deferred cleanup.
            self._run("umount", "--no-canonicalize", target)
        for device in devices:
            self._run("losetup", "--detach", device)
        # Linux loop detach can defer even when losetup exits successfully.
        if devices:
            deadline = time.monotonic() + 5.0
            while (remaining := self._loops(uid)) and time.monotonic() < deadline:
                time.sleep(0.05)
                for device in remaining:
                    try:
                        self._run("losetup", "--detach", device)
                    except subprocess.CalledProcessError:
                        pass
        self._detached(uid)

    def detach(self, uid: int) -> None:
        with self._lock(uid):
            self._detach(uid)

    def grow(self, uid: int, new_size: int) -> None:
        """Caller closes admission before growing; shrink is always refused."""
        with self._lock(uid):
            image = self._image(uid)
            if type(new_size) is not int or new_size < image.stat().st_size:
                raise VolumeError("volume shrink is forbidden")
            devices = self._loops(uid)
            target = str(self.mount_root / str(uid))
            if len(devices) != 1 or (target, devices[0]) not in self._mounts():
                raise VolumeError("growth requires the attached volume")
            with image.open("r+b") as stream:
                stream.truncate(new_size)
                stream.flush()
                os.fsync(stream.fileno())
            self._run("losetup", "--set-capacity", devices[0])
            self._run("xfs_growfs", target)

    @staticmethod
    def _copy(source, destination=None) -> str:
        """Hash raw image bytes; preserve holes when expanding a snapshot."""
        digest = hashlib.sha256()
        length = 0
        while chunk := source.read(1024 * 1024):
            digest.update(chunk)
            length += len(chunk)
            if destination is not None:
                if chunk.count(0) == len(chunk):
                    destination.seek(len(chunk), os.SEEK_CUR)
                else:
                    destination.write(chunk)
        if destination is not None:
            destination.truncate(length)
            destination.flush()
            os.fsync(destination.fileno())
        return digest.hexdigest()

    def _transfer(self, source: str, destination: str) -> None:
        self._run("rclone", "copyto", "--ignore-times", "--contimeout", "60s",
                  "--timeout", "900s", source, destination)

    def snapshot(self, uid: int, snapshot_id: str | None = None) -> tuple[str, str]:
        """Return (object ID, raw-image SHA256) only after remote readback."""
        with self._lock(uid):
            self._detached(uid)
            snapshot_id = snapshot_id or uuid.uuid4().hex
            if not re.fullmatch(r"[a-f0-9]{32}", snapshot_id):
                raise VolumeError("snapshot reference is required")
            if not self._image(uid).is_file():
                raise VolumeError("local image is absent")
            clone = self.store / f".{uid}.clone"
            packed = self.store / f".{uid}.gz"
            readback = self.store / f".{uid}.readback"
            try:
                self._run("cp", "--reflink=always", str(self._image(uid)), str(clone))
                with clone.open("rb") as stream:
                    sha256 = self._copy(stream)
                with clone.open("rb") as source, gzip.open(packed, "wb", compresslevel=1) as dest:
                    while chunk := source.read(1024 * 1024):
                        dest.write(chunk)
                remote = f"{self.remote}/{uid}/{snapshot_id}.img.gz"
                self._transfer(str(packed), remote)
                self._transfer(remote, str(readback))
                with gzip.open(readback, "rb") as stream:
                    if self._copy(stream) != sha256:
                        raise VolumeError("snapshot readback SHA256 mismatch")
                return snapshot_id, sha256
            finally:
                for path in (clone, packed, readback):
                    path.unlink(missing_ok=True)

    def restore(self, uid: int, snapshot_id: str, sha256: str) -> None:
        """Retry from the same reference after interruption; publication is atomic.

        Stage names are deterministic. A retry replaces incomplete download and
        expansion stages, verifies again, then renames. The old image survives
        until rename; after rename only the complete verified image is visible.
        Lane a keeps admission closed until its generation commit is durable.
        """
        with self._lock(uid):
            self._detached(uid)
            if not re.fullmatch(r"[a-f0-9]{32}", snapshot_id or ""):
                raise VolumeError("snapshot reference is required")
            if not re.fullmatch(r"[a-f0-9]{64}", sha256 or ""):
                raise VolumeError("snapshot SHA256 is required")
            packed = self.store / f".{uid}.download"
            stage = self.store / f".{uid}.restore"
            self._transfer(f"{self.remote}/{uid}/{snapshot_id}.img.gz", str(packed))
            with gzip.open(packed, "rb") as source, stage.open("wb") as dest:
                actual = self._copy(source, dest)
            if actual != sha256:
                raise VolumeError("restore SHA256 mismatch")
            os.replace(stage, self._image(uid))
            self._sync(self.store)
            packed.unlink()

    def delete(self, uid: int) -> None:
        with self._lock(uid):
            self._detach(uid)
            self._image(uid).unlink(missing_ok=True)
            # Known opaque stages only; an interrupted operation must not leave
            # owner image bytes behind after erasure. No directory enumeration.
            for suffix in ("create", "prototype", "clone", "gz", "readback",
                           "download", "restore"):
                (self.store / f".{uid}.{suffix}").unlink(missing_ok=True)
            self._sync(self.store)
