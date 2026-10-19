"""Step 1: real XFS/loop/runsc/rclone proof, synthetic bytes only.

Run with linux_oracle.py --as-root --docker-engine, TA_REHEARSAL_BASE_IMAGE
and TA_REHEARSAL_RUNSC_DIR. The inner venue has no network or host-data bind.
Missing prerequisites FAIL, never skip. Metrics are printed and in the receipt.
"""

from __future__ import annotations

import builtins
import gzip
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


def command(*args, **kwargs):
    result = subprocess.run(args, capture_output=True, text=True, timeout=600, **kwargs)
    assert result.returncode == 0, (args, result.stdout, result.stderr)
    return result.stdout.strip()


def test_owner_volumes_real_mounts(tmp_path):
    assert sys.platform == "linux" and os.geteuid() == 0, "root Linux oracle required"
    base = os.environ["TA_REHEARSAL_BASE_IMAGE"]
    pinned = Path(os.environ["TA_REHEARSAL_RUNSC_DIR"])
    root = Path(__file__).resolve().parents[1]
    install = (root / "deploy/install-boxhostd-preflight.sh").read_text()
    expected = re.search(r"^runsc_sha=(\w+)$", install, re.M)[1]
    assert hashlib.sha256((pinned / "runsc").read_bytes()).hexdigest() == expected
    from tinyassets.usage_policy import TIER_ORDER, limits_for

    smallest = min(int(limits_for(tier).storage_bytes) for tier in TIER_ORDER)
    name = "ta-volume-proof-" + uuid.uuid4().hex[:12]
    # Dependency installation belongs to this disposable test image only.
    dockerfile = (f"FROM {base}\nUSER 0\nRUN apt-get update -qq && "
                  "apt-get install -y -qq rclone xfsprogs util-linux && "
                  "rm -rf /var/lib/apt/lists/*\n")
    command("docker", "build", "-t", name, "-", input=dockerfile)
    created = False
    try:
        command("docker", "create", "--name", name, "--init", "--privileged", "--network", "none",
                "--label", "tinyassets.disposable=true", "--entrypoint", "python",
                "-e", f"TA_SMALLEST_VOLUME={smallest}", name,
                "/proof/tests/test_owner_volumes_proof.py")
        created = True
        package = tmp_path / "proof"
        (package / "tinyassets/boxes").mkdir(parents=True)
        (package / "tests").mkdir()
        (package / "tinyassets/__init__.py").touch()
        (package / "tinyassets/boxes/__init__.py").touch()
        shutil.copy2(root / "tinyassets/boxes/volumes.py", package / "tinyassets/boxes")
        shutil.copy2(__file__, package / "tests")
        command("docker", "cp", str(package), name + ":/proof")
        command("docker", "cp", str(pinned), name + ":/gvisor")
        result = subprocess.run(["docker", "start", "--attach", name],
                                capture_output=True, text=True, timeout=1200)
        print(result.stdout, flush=True)
        if Path("/out").is_dir():
            Path("/out/owner-volumes-proof.txt").write_text(result.stdout + result.stderr)
        assert result.returncode == 0, result.stderr + result.stdout
        assert command("docker", "inspect", "-f", "{{.State.ExitCode}}", name) == "0"
        assert "PROOFS a b c d e f g h i PASS" in result.stdout
    finally:
        if created:
            command("docker", "rm", "-f", name)
        command("docker", "image", "rm", name)


class Boxes:
    def __init__(self, root: Path):
        self.root = root
        self.live = {}
        # Match production's supervisor-leaf delegation inside this disposable
        # container's cgroup namespace. Never disable runsc's cgroup handling.
        cgroup = Path("/sys/fs/cgroup")
        (cgroup / "supervisor").mkdir()
        for pid in (cgroup / "cgroup.procs").read_text().split():
            (cgroup / "supervisor/cgroup.procs").write_text(pid)
        assert not (cgroup / "cgroup.procs").read_text().strip()
        (cgroup / "cgroup.subtree_control").write_text("+cpu +memory +pids")
        self.rootfs = root / "rootfs"
        for directory in ("bin", "dev", "proc", "sys", "tmp", "home/owner"):
            (self.rootfs / directory).mkdir(parents=True, exist_ok=True)
        # A minimal genuine dynamic rootfs, with no host owner-content paths.
        for name in ("sh", "sleep", "dd", "cat", "sync", "fallocate"):
            binary = Path(shutil.which(name))
            paths = [str(binary), *re.findall(r"(/\S+)", command("ldd", str(binary)))]
            for source in paths:
                source = source.rstrip(":")
                target = self.rootfs / source.lstrip("/")
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(source, target)
                target.chmod(0o755)
            target = self.rootfs / "bin" / name
            if target != self.rootfs / str(binary).lstrip("/"):
                shutil.copyfile(binary, target)
                target.chmod(0o755)
        self.argv = ["/gvisor/runsc", "--root", str(root / "runsc"),
                     "--systemd-cgroup=false", "--network=none", "--platform=systrap",
                     "--overlay2=root:memory,size=256m"]

    def start(self, handle):
        uid = handle.uid
        bundle = self.root / str(uid)
        bundle.mkdir(exist_ok=True)
        (bundle / "config.json").unlink(missing_ok=True)
        command(*self.argv, "spec", "--bundle", str(bundle))
        config = json.loads((bundle / "config.json").read_text())
        config["root"] = {"path": str(self.rootfs), "readonly": True}
        config["linux"]["cgroupsPath"] = f"/owner-{uid}"
        config["process"].update(args=["/bin/sleep", "infinity"], terminal=False,
                                 cwd="/home/owner", user={"uid": uid, "gid": uid},
                                 env=["PATH=/bin", "HOME=/home/owner"],
                                 noNewPrivileges=True,
                                 capabilities={k: [] for k in (
                                     "bounding", "effective", "inheritable", "permitted",
                                     "ambient")})
        config["mounts"] = [m for m in config["mounts"] if m["type"] in (
            "proc", "sysfs", "tmpfs", "devpts", "mqueue")]
        config["mounts"].append(handle.oci_mount())
        (bundle / "config.json").write_text(json.dumps(config))
        # Keep the foreground supervisor as our child so cleanup can reap it.
        output = tempfile.TemporaryFile()
        process = subprocess.Popen([*self.argv, "run", "--bundle", str(bundle),
                                    f"owner-{uid}"], stdout=subprocess.DEVNULL, stderr=output)
        self.live[uid] = process, output
        try:
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline:
                state = subprocess.run([*self.argv, "state", f"owner-{uid}"],
                                       capture_output=True, text=True, timeout=10)
                if state.returncode == 0 and json.loads(state.stdout)["status"] == "running":
                    return
                if process.poll() is not None:
                    output.seek(0)
                    raise AssertionError(output.read().decode())
                time.sleep(.05)
            raise TimeoutError(f"box {uid} did not start")
        except BaseException:
            self.stop(uid)
            raise

    def shell(self, uid, script, *, check=True, timeout=120):
        with subprocess.Popen([*self.argv, "exec", "--user", f"{uid}:{uid}",
                               f"owner-{uid}", "/bin/sh", "-c", script],
                              stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
            try:
                stdout, stderr = process.communicate(timeout=timeout)
                result = subprocess.CompletedProcess(
                    process.args, process.returncode, stdout, stderr)
            finally:
                if process.poll() is None:
                    # Killing the exec client alone leaves its in-box writer alive.
                    try:
                        self.stop(uid)
                    finally:
                        process.kill()
                        process.wait(timeout=30)
        if check:
            assert result.returncode == 0, result.stderr
        return result

    def stop(self, uid):
        if uid in self.live:
            process, output = self.live[uid]
            try:
                if process.poll() is None:
                    command(*self.argv, "kill", "--all", f"owner-{uid}", "KILL")
            finally:
                process.wait(timeout=30)
                command(*self.argv, "delete", "--force", f"owner-{uid}")
                output.close()
                del self.live[uid]


def digest(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def interrupted_restore(volumes, uid, reference, phase):
    """Kill the actual restore process at a boundary, without cleanup handlers."""
    from tinyassets.boxes import volumes as module

    pid = os.fork()
    if pid == 0:
        def die():
            os._exit(73)

        transfer, copy, replace, sync = (volumes._transfer, volumes._copy,
                                         module.os.replace, volumes._sync)

        def transferring(*args):
            if phase == "download-before":
                die()
            transfer(*args)
            if phase == "download-after":
                die()

        def copying(source, destination=None):
            if phase == "expansion-midway":
                destination.write(source.read(1024 * 1024))
                destination.flush()
                os.fsync(destination.fileno())
                die()
            result = copy(source, destination)
            if phase == "verification-before":
                die()
            return result

        def replacing(*args):
            if phase == "rename-before":
                die()
            replace(*args)
            if phase == "rename-after":
                die()

        def syncing(path):
            if phase == "directory-fsync-before":
                die()
            sync(path)
            if phase == "directory-fsync-after":
                die()

        volumes._transfer = transferring
        volumes._copy = copying
        module.os.replace = replacing
        volumes._sync = syncing
        volumes.restore(uid, *reference)
        os._exit(74)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 73, phase


def inner_proof(root):
    from tinyassets.boxes.volumes import OwnerVolumes, VolumeError

    store, mounts = root / "store", root / "mounts"
    store.mkdir()
    backing = root / "outer.img"
    with backing.open("wb") as stream:
        stream.truncate(12 * 1024**3)
    command("mkfs.xfs", "-f", "-m", "reflink=1", str(backing))
    outer = command("losetup", "--find", "--show", str(backing))
    command("mount", "--make-rprivate", "/")
    command("mount", "-t", "xfs", outer, str(store))
    remote = root / "remote"
    remote.mkdir()
    os.environ["RCLONE_CONFIG_PROOF_TYPE"] = "local"
    try:
        volumes = OwnerVolumes(store, mounts, "proof:" + str(remote))
        boxes = Boxes(root)
    except BaseException:
        command("umount", str(store))
        command("losetup", "--detach", outer)
        raise
    uids = list(range(300001, 300007))
    size = int(os.environ["TA_SMALLEST_VOLUME"])
    metrics = {"image_bytes": size, "kernel": os.uname().release,
               "outer_xfs": command("xfs_info", str(store)),
               "runsc": command("/gvisor/runsc", "--version")}
    stop = threading.Event()
    peak = [0]

    def measure():
        while not stop.wait(.01):
            fs = os.statvfs(store)
            peak[0] = max(peak[0], (fs.f_blocks - fs.f_bfree) * fs.f_frsize)

    monitor = threading.Thread(target=measure)
    monitor.start()
    try:
        # (i), (a): format the real smallest plan, then write as its real UID.
        uid = uids[0]
        volumes.create(uid, size)
        with pytest.raises(FileExistsError):
            volumes.create(uid, size)
        handle = volumes.attach(uid)
        fs = os.statvfs(handle.mountpoint)
        metrics.update(usable_bytes=fs.f_bavail * fs.f_frsize,
                       filesystem_overhead_bytes=size - fs.f_bavail * fs.f_frsize,
                       oci_mount=handle.oci_mount())
        boxes.start(handle)
        boxes.shell(uid, "printf image-proof > /home/owner/proof; sync")
        assert (handle.mountpoint / "proof").read_bytes() == b"image-proof"
        assert not (boxes.rootfs / "home/owner/proof").exists()
        # (b): open host reference makes unmount fail; no lazy detach permitted.
        with (handle.mountpoint / "proof").open("rb"):
            with pytest.raises(subprocess.CalledProcessError):
                volumes.detach(uid)
            assert volumes._loops(uid)
        with pytest.raises(VolumeError, match="detached"):
            volumes.snapshot(uid)
        boxes.stop(uid)
        volumes.detach(uid)
        volumes._detached(uid)
        # Fresh interpreter reattaches and reads, then completely detaches.
        code = ("from pathlib import Path; from tinyassets.boxes.volumes import OwnerVolumes; "
                f"v=OwnerVolumes(Path({str(store)!r}),Path({str(mounts)!r}),"
                f"{'proof:' + str(remote)!r}); h=v.attach({uid}); "
                "assert (h.mountpoint/'proof').read_bytes()==b'image-proof'; "
                f"v.detach({uid})")
        command(sys.executable, "-c", code, env={**os.environ, "PYTHONPATH": "/proof"})
        with ThreadPoolExecutor(max_workers=2) as pool:
            attempts = [pool.submit(volumes.attach, uid) for _ in range(2)]
            outcomes = []
            for attempt in attempts:
                try:
                    outcomes.append(attempt.result())
                except VolumeError:
                    outcomes.append(None)
        assert sum(outcome is not None for outcome in outcomes) == 1
        assert len(volumes._loops(uid)) == 1
        volumes.detach(uid)
        print("PROOF a b i PASS", flush=True)

        # (c/d): inspect shared extents on OUTER XFS, and time actual rclone.
        base_run, original_transfer = volumes._run, volumes._transfer
        transfer_times = []
        immutable = store / "immutable-snapshot.img"

        def run(*args):
            assert not (args[0] == "umount" and any(a in ("-l", "--lazy") for a in args))
            result = base_run(*args)
            if args[:2] == ("cp", "--reflink=always"):
                extents = command("xfs_io", "-r", "-c", "fiemap -v", args[-1])
                # FIEMAP_EXTENT_SHARED = 0x2000; xfs_io flags are hex.
                assert any(int(flag, 16) & 0x2000 for flag in
                           re.findall(r"\s(0x[0-9a-f]+)\s*$", extents, re.M)), extents
                metrics["outer_reflink_confirmed"] = True
                if not immutable.exists():
                    # Keep the exact reflink inode after product staging cleanup
                    # so later live writes exercise CoW, not just object immutability.
                    os.link(args[-1], immutable)
            return result

        def transfer(source, destination):
            started = time.monotonic()
            original_transfer(source, destination)
            transfer_times.append(time.monotonic() - started)

        volumes._run, volumes._transfer = run, transfer
        started = time.monotonic()
        reference = volumes.snapshot(uid)
        metrics["snapshot_seconds"] = time.monotonic() - started
        metrics["upload_seconds"], metrics["readback_transfer_seconds"] = transfer_times
        frozen = remote / f"{reference[0]}.img.gz"
        frozen_hash = digest(frozen)
        with gzip.open(frozen, "rb") as source:
            started = time.monotonic()
            assert volumes._copy(source) == reference[1]
            metrics["readback_hash_seconds"] = time.monotonic() - started
        handle = volumes.attach(uid)
        boxes.start(handle)
        boxes.shell(uid, "printf later > /home/owner/proof; sync")
        boxes.stop(uid)
        volumes.detach(uid)
        assert digest(frozen) == frozen_hash
        assert digest(immutable) == reference[1]
        old_hash = digest(store / f"{uid}.img")

        def corrupt(source, destination):
            original_transfer(source, destination)
            if destination.startswith("proof:"):
                Path(destination.removeprefix("proof:")).write_bytes(b"corrupt upload")

        volumes._transfer = corrupt
        with pytest.raises((VolumeError, gzip.BadGzipFile, EOFError)):
            volumes.snapshot(uid)
        volumes._transfer = original_transfer
        assert digest(frozen) == frozen_hash
        child = os.fork()
        if child == 0:
            def interrupted_upload(source, destination):
                assert destination.startswith("proof:")
                Path(destination.removeprefix("proof:")).write_bytes(b"partial upload")
                os._exit(73)

            volumes._transfer = interrupted_upload
            volumes.snapshot(uid)
            os._exit(74)
        _, status = os.waitpid(child, 0)
        assert os.waitstatus_to_exitcode(status) == 73
        assert digest(frozen) == frozen_hash  # Previous recovery reference survives.
        print("PROOF c d PASS", flush=True)

        # (e): kill at every install stage. No torn published image, retry works.
        old = store / "old.img"
        command("cp", "--reflink=always", str(store / f"{uid}.img"), str(old))
        for phase in ("download-before", "download-after", "expansion-midway",
                      "verification-before", "rename-before", "rename-after",
                      "directory-fsync-before", "directory-fsync-after"):
            command("cp", "--reflink=always", str(old), str(store / f"{uid}.img"))
            interrupted_restore(volumes, uid, reference, phase)
            assert digest(store / f"{uid}.img") in (old_hash, reference[1]), phase
            volumes.restore(uid, *reference)
            assert digest(store / f"{uid}.img") == reference[1], phase
        with pytest.raises(VolumeError, match="SHA256"):
            volumes.restore(uid, reference[0], "0" * 64)
        assert digest(store / f"{uid}.img") == reference[1]
        # Recovery requires no local snapshot metadata or old volume.
        volumes.delete(uid)
        fresh = OwnerVolumes(store, mounts, "proof:" + str(remote))
        fresh.restore(uid, *reference)
        assert digest(store / f"{uid}.img") == reference[1]
        print("PROOF e PASS", flush=True)

        # (g): six real owners run at once and cannot see each other's mounts.
        for owner in uids[1:]:
            volumes.create(owner, size)
        for owner in uids:
            boxes.start(volumes.attach(owner))
        assert len(boxes.live) == 6
        for owner in uids:
            boxes.shell(owner, f"printf {owner} > /home/owner/identity; sync")
            assert boxes.shell(owner, "cat /home/owner/identity").stdout == str(owner)
            with pytest.raises(VolumeError, match="detached"):
                volumes.snapshot(owner)
        # Lock evidence uses two real grows, deliberately held inside _run.
        entered, release = threading.Event(), threading.Event()
        original_run = volumes._run
        calls = []

        def held(*args):
            if args[0] == "xfs_growfs" and args[-1] == str(mounts / str(uid)):
                calls.append(args)
                if len(calls) == 1:
                    entered.set()
                    assert release.wait(30)
            return original_run(*args)

        volumes._run = held
        with ThreadPoolExecutor(max_workers=3) as pool:
            first = pool.submit(volumes.grow, uid, size + 512 * 1024**2)
            assert entered.wait(30)
            second = pool.submit(volumes.grow, uid, size + 1024**3)
            other = pool.submit(volumes.grow, uids[1], size + 512 * 1024**2)
            other.result(30)  # Different UID completes while the first is held.
            assert not second.done() and len(calls) == 1
            release.set()
            first.result(30)
            second.result(30)
        volumes._run = original_run
        assert len(calls) == 2
        # Snapshot/delete use that same lock, including the remote round trip.
        last = uids[-1]
        boxes.stop(last)
        volumes.detach(last)
        entered.clear()
        release.clear()
        ordering = []
        original_copy = volumes._copy

        def held_upload(source, destination):
            if destination.startswith("proof:"):
                entered.set()
                assert release.wait(30)
            original_transfer(source, destination)

        def checked_copy(source, destination=None):
            result = original_copy(source, destination)
            if str(source.name).endswith(".readback"):
                ordering.append("verified")
            return result

        def erase():
            volumes.delete(last)
            ordering.append("delete")

        volumes._transfer = held_upload
        volumes._copy = checked_copy
        with ThreadPoolExecutor(max_workers=2) as pool:
            taking = pool.submit(volumes.snapshot, last)
            assert entered.wait(30)
            deleting = pool.submit(erase)
            assert not deleting.done()
            release.set()
            taking.result(60)
            deleting.result(60)
        volumes._transfer = original_transfer
        volumes._copy = original_copy
        assert ordering == ["verified", "delete"]
        with pytest.raises(subprocess.CalledProcessError):
            volumes.snapshot(last)
        print("PROOF g PASS", flush=True)

        # (h): actual ENOSPC within one image; another owner still writes.
        grown = os.statvfs(mounts / str(uid))
        assert grown.f_bavail * grown.f_frsize > metrics["usable_bytes"]
        with pytest.raises(VolumeError, match="shrink"):
            volumes.grow(uid, size)
        # Reuse the erased owner's UID with a fresh smallest-plan image, not
        # the grown image. Allocate extents inside runsc without streaming GiB
        # through its gofer; descend to one XFS block to consume the tail too.
        volumes.create(last, size)
        boxes.start(volumes.attach(last))
        started = time.monotonic()
        try:
            full = boxes.shell(last, "offset=0; for chunk in 67108864 1048576 4096; do "
                               "while fallocate -o $offset -l $chunk /home/owner/full; do "
                               "offset=$((offset + chunk)); done; done; "
                               "printf 'allocated_bytes=%s\\n' $offset; "
                               "dd if=/dev/zero of=/home/owner/write-fails "
                               "bs=4096 count=1 conv=fsync",
                               check=False, timeout=60)
            assert full.returncode != 0 and "No space left on device" in full.stderr, full
            assert "dd: " in full.stderr
            assert "No space left on device" in full.stderr.split("dd: ")[-1]
            metrics["exhaustion_seconds"] = time.monotonic() - started
            metrics["exhaustion_allocated_bytes"] = int(full.stdout.split("allocated_bytes=")[1])
            assert metrics["exhaustion_allocated_bytes"] > 0
            boxes.shell(uids[1], "printf unaffected > /home/owner/healthy; sync")
            assert boxes.shell(uids[1], "cat /home/owner/healthy").stdout == "unaffected"
            # Exercise the old leak: a timed-out exec must stop and reap the
            # entire box, including the command, before volume detachment.
            with pytest.raises(subprocess.TimeoutExpired):
                boxes.shell(last, "sleep infinity", timeout=.5)
            assert last not in boxes.live
        finally:
            boxes.stop(last)
        volumes.detach(last)
        volumes._detached(last)
        print("PROOF h PASS", flush=True)

        # (f): deny Python content access throughout product delete.
        for owner in uids:
            boxes.stop(owner)
        touched = []

        def guard(function):
            def wrapped(path, *args, **kwargs):
                if isinstance(path, (str, bytes, os.PathLike)):
                    value = os.fsdecode(path)
                    if value == str(mounts) or value.startswith(str(mounts) + "/"):
                        touched.append(value)
                        raise AssertionError("product accessed mounted owner contents")
                return function(path, *args, **kwargs)
            return wrapped

        with pytest.MonkeyPatch.context() as patch:
            for name in ("walk", "scandir", "listdir", "open", "stat", "lstat"):
                patch.setattr(os, name, guard(getattr(os, name)))
            patch.setattr(builtins, "open", guard(builtins.open))
            import io
            patch.setattr(io, "open", guard(io.open))
            for owner in uids:
                volumes.delete(owner)
        assert not touched
        for owner in uids:
            assert not (store / f"{owner}.img").exists()
            assert not list(store.glob(f".{owner}.*"))
            volumes._detached(owner)
        metrics["peak_outer_physical_bytes"] = peak[0]
        print("METRICS " + json.dumps(metrics, sort_keys=True), flush=True)
        print("PROOFS a b c d e f g h i PASS", flush=True)
    finally:
        stop.set()
        monitor.join()
        for uid in uids:
            boxes.stop(uid)
        # A detach failure must never strand a different box's processes.
        for uid in uids:
            volumes.detach(uid)
        command("umount", str(store))
        command("losetup", "--detach", outer)
        assert not command("losetup", "--associated", str(backing),
                           "--noheadings", "--output", "NAME")


if __name__ == "__main__":
    sys.path.insert(0, "/proof")
    # Docker removes this private root and runsc's persistent null-netns mount
    # with the container. inner_proof explicitly releases every loop first.
    inner_proof(Path(tempfile.mkdtemp(prefix="ta-volume-")))
