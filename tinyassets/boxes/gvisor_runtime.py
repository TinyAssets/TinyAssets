"""Host-side gVisor sandbox lifecycle. Called only by boxhostd.

The runtime accepts a numeric owner identity already verified by the broker.
It never infers identity from a box request. The attached opaque volume is the
durable writable mount; a separate runtime bind exports the app socket. Rootfs writes
use the same bounded memory overlay proven by G4.
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

from tinyassets.boxes.provider import BoxError

OVERLAY = "root:memory,size=256m"
TMP_BYTES = 256 * 1024 * 1024
PIDS = 1024


def public_url() -> str:
    """Production boxes must use boxhostd's explicit public origin."""
    value = os.environ.get("UNIVERSE_SERVER_URL", "").strip()
    if not value:
        raise BoxError("UNIVERSE_SERVER_URL is required to start an owner box")
    return value


class GvisorRuntime:
    """One delegated service cgroup, one sandbox per permanent owner UID."""

    def __init__(
        self, *, runsc: Path, rootfs: Path,
        state_root: Path, bundle_root: Path, cgroup_parent: Path,
    ) -> None:
        self.runsc = Path(runsc)
        self.rootfs = Path(rootfs)
        self.state_root = Path(state_root)
        self.bundle_root = Path(bundle_root)
        self.cgroup_parent = Path(cgroup_parent)
        if not self.runsc.is_file() or not os.access(self.runsc, os.X_OK):
            raise BoxError("the pinned runsc binary is unavailable")
        if not self.rootfs.is_dir() or self.rootfs.is_symlink():
            raise BoxError("the harness rootfs is unavailable or a link")
        for path in (self.state_root, self.bundle_root):
            path.mkdir(parents=True, exist_ok=True, mode=0o700)


    def memory(self, uid: int) -> int:
        """What the box's cgroup holds now; the broker admits wakes against it."""
        return int((self.cgroup_parent / self.box_id(uid) / "memory.current").read_text())

    def usage(self, uid: int) -> dict:
        """W10 receipt facts, read before runsc deletes the cgroup at stop."""
        cgroup = self.cgroup_parent / self.box_id(uid)
        cpu = dict(line.split() for line in (cgroup / "cpu.stat").read_text().splitlines())
        return {"peak_memory_bytes": int((cgroup / "memory.peak").read_text()),
                "cpu_usec": int(cpu["usage_usec"])}

    @staticmethod
    def box_id(uid: int) -> str:
        if type(uid) is not int or not 300001 <= uid <= 399999:
            raise BoxError("owner UID is outside the permanent owner range")
        return f"owner-{uid}"

    def _command(self, *args: str, timeout: float = 120) -> subprocess.CompletedProcess[str]:
        # A detached sandbox inherits its stdio. Pipes would stay open for the
        # daemon's lifetime and make communicate() time out after runsc exits.
        # Inherit the host service's stderr so launch failures and daemon errors
        # reach its journal without a pipe owned by this short-lived command.
        output = ({"stdout": subprocess.DEVNULL}
                  if args[:2] == ("run", "--detach") else {"capture_output": True})
        return subprocess.run(
            [
                str(self.runsc), "--root", str(self.state_root),
                "--systemd-cgroup=false", "--network=none", "--platform=systrap",
                "--host-uds=all",
                f"--overlay2={OVERLAY}", *args,
            ],
            check=True, text=True, timeout=timeout, **output,
        )

    def _config(self, config: dict, *, uid: int, gid: int, volume_mount: dict,
                volume_generation: int, relay: Path, memory_bytes: int, app_dir: Path | None = None,
                secret_file: Path | None = None, rootfs: Path | None = None,
                rewind: bool = False) -> dict:
        if memory_bytes not in (1024**3, 2 * 1024**3):
            raise BoxError("box memory must be one of the platform floor sizes")
        if not relay.is_socket():
            raise BoxError("owner relay socket is unavailable")
        rootfs = self.rootfs if rootfs is None else rootfs
        if rootfs.is_symlink() or not rootfs.is_dir():
            raise BoxError("the harness rootfs is unavailable or a link")
        config["root"] = {"path": str(rootfs), "readonly": True}
        config["process"].update(
            # Under fleet memory pressure the kernel kills a sandbox, never the
            # boxhostd supervisor every owner depends on (OOMScoreAdjust=-500).
            oomScoreAdj=500,
            args=["/bin/sleep", "infinity"], terminal=False, cwd="/home/owner",
            user={"uid": uid, "gid": gid},
            noNewPrivileges=True,
            capabilities={kind: [] for kind in (
                "bounding", "effective", "inheritable", "permitted", "ambient"
            )},
            env=[
                "PATH=/usr/local/bin:/usr/bin:/bin",
                "HOME=/home/owner",
                f"TINYASSETS_VOLUME_GENERATION={volume_generation}",
                *(["TINYASSETS_VOLUME_REWIND=1"] if rewind else []),
                "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright",
                f"UNIVERSE_SERVER_URL={public_url()}",
            ],
        )
        # runsc spec may include host binds (/etc/hosts, resolv.conf, etc.).
        # Keep only the synthetic kernel mounts; no host path enters the box.
        config["mounts"] = [
            mount for mount in config.get("mounts", [])
            if mount.get("type") in {"proc", "sysfs", "devpts", "mqueue", "tmpfs"}
            and mount.get("destination") in {
                "/proc", "/sys", "/dev", "/dev/pts", "/dev/mqueue",
            }
        ]
        config["mounts"].extend([
            volume_mount,
            {"destination": "/run/boxhostd/relay.sock", "type": "bind",
             "source": str(relay), "options": ["bind", "rw", "nosuid", "nodev"]},
            {"destination": "/tmp", "type": "tmpfs", "source": "tmpfs",
             "options": ["nosuid", "nodev", "mode=1777", f"size={TMP_BYTES}"]},
        ])
        if app_dir is not None:
            if secret_file is None or not secret_file.is_file():
                raise BoxError("owner daemon requires its read-only secret")
            config["process"].update(
                args=["/opt/venv/bin/python", "-B", "-m", "tinyassets.owner_daemon",
                      "--socket", "/run/owner/app.sock", "--relay", "/run/boxhostd/relay.sock",
                      "--secret-file", "/run/boxhostd/secret"],
                cwd="/app",
            )
            config["mounts"].extend([
                {"destination": "/run/owner", "type": "bind", "source": str(app_dir),
                 "options": ["bind", "rw", "nosuid", "nodev", "noexec"]},
                {"destination": "/run/boxhostd/secret", "type": "bind",
                 "source": str(secret_file),
                 "options": ["bind", "ro", "nosuid", "nodev", "noexec"]},
            ])
        box = self.box_id(uid)
        cgroup = self.cgroup_parent / box
        if cgroup.exists():
            raise BoxError("box cgroup already exists; refusing to bypass limits")
        cgroup_root = Path("/sys/fs/cgroup")
        if not cgroup.is_relative_to(cgroup_root):
            raise BoxError("box cgroup is outside the delegated cgroup tree")
        config.setdefault("linux", {})["cgroupsPath"] = (
            f"/{cgroup.relative_to(cgroup_root).as_posix()}"
        )
        config["linux"]["resources"] = {
            "memory": {"limit": memory_bytes, "swap": memory_bytes},
            "pids": {"limit": PIDS},
            "cpu": {"shares": 1024},
        }
        return config

    def start(self, uid: int, gid: int, *, relay: Path, memory_bytes: int,
              volume_mount: dict, volume_generation: int,
              app_dir: Path | None = None, secret_file: Path | None = None,
              rootfs: Path | None = None, rewind: bool = False) -> None:
        box = self.box_id(uid)
        bundle = self.bundle_root / box
        public_url()  # Refuse before creating a bundle or invoking runsc.
        bundle.mkdir(mode=0o700, exist_ok=False)
        self._command("spec", "--bundle", str(bundle), timeout=5)
        config_path = bundle / "config.json"
        config = self._config(
            json.loads(config_path.read_text(encoding="utf-8")),
            uid=uid, gid=gid, volume_mount=volume_mount, volume_generation=volume_generation,
            relay=relay, memory_bytes=memory_bytes,
            app_dir=app_dir, secret_file=secret_file, rootfs=rootfs, rewind=rewind,
        )
        config_path.write_text(json.dumps(config), encoding="utf-8")
        self._command("run", "--detach", "--bundle", str(bundle), box, timeout=30)
        try:
            self._assert_limited(uid, memory_bytes)
        except BaseException as error:
            try:
                self.stop(uid)
            except BaseException as cleanup_error:
                raise BoxError("unlimited sandbox started and cleanup failed") from cleanup_error
            raise BoxError("sandbox started outside its verified resource floor") from error

    def _assert_limited(self, uid: int, memory_bytes: int) -> None:
        box = self.box_id(uid)
        cgroup = self.cgroup_parent / box
        state = json.loads(self._command("state", box, timeout=5).stdout)
        pid = state["pid"]
        if type(pid) is not int or pid <= 0:
            raise BoxError("runsc returned no sandbox PID")
        members = {
            int(member)
            for procs in cgroup.rglob("cgroup.procs")
            for member in procs.read_text().split()
        }
        if pid not in members:
            raise BoxError("runsc sandbox is outside its limited cgroup")
        for name, expected in (("memory.max", memory_bytes), ("pids.max", PIDS)):
            if int((cgroup / name).read_text().strip()) != expected:
                raise BoxError(f"box cgroup {name} differs from the floor")
        if int((cgroup / "cpu.weight").read_text().strip()) <= 0:
            raise BoxError("box cgroup has no CPU weight")

    def exec(
        self, uid: int, argv: list[str], *, timeout: float,
    ) -> subprocess.CompletedProcess[str]:
        if not argv or any(not isinstance(arg, str) or "\0" in arg for arg in argv):
            raise BoxError("invalid box exec argv")
        return self._command("exec", self.box_id(uid), *argv, timeout=timeout)

    def stop(self, uid: int) -> None:
        box = self.box_id(uid)
        try:
            self._command("kill", box, "KILL", timeout=10)
        except subprocess.CalledProcessError:
            pass  # delete --force must still run for an already exited box.
        try:
            self._command("delete", "--force", box, timeout=10)
        except subprocess.CalledProcessError:
            # A repeated stop is safe only after the runtime proves absence.
            # An unavailable runtime is never interpreted as an absent sandbox.
            states = json.loads(self._command("list", "--format=json", timeout=10).stdout)
            if states is None:  # Pinned runsc encodes an empty census as null.
                states = []
            if not isinstance(states, list) or any(item["id"] == box for item in states):
                raise BoxError("cannot prove the owner sandbox stopped") from None
        bundle = self.bundle_root / box
        (bundle / "config.json").unlink(missing_ok=True)
        if bundle.exists():
            bundle.rmdir()

    def alive(self, uid: int) -> bool:
        states = json.loads(self._command("list", "--format=json", timeout=10).stdout)
        if states is None:
            states = []
        return any(item["id"] == self.box_id(uid) and item["status"] == "running"
                   for item in states)

    def shutdown(self, uid: int, *, timeout: float = 30) -> None:
        """SIGTERM lets uvicorn finish lifespan and flush owner stores."""
        if self.alive(uid):
            self._command("kill", self.box_id(uid), "TERM", timeout=10)
        deadline = time.monotonic() + timeout
        while self.alive(uid):
            if time.monotonic() >= deadline:
                raise BoxError("owner daemon did not flush before shutdown deadline")
            time.sleep(.1)
        self.stop(uid)
