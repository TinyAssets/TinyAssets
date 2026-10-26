"""Bounded, fail-closed retention of recoverable daemon image cache only.

Two modes, selected by ``TINYASSETS_DAEMON_IMAGE_RETENTION_MODE``:

``count`` (default)
    The keep set is a rule, not a pressure response. Every pass removes each
    registry-verified daemon image outside the keep set, whatever the disk
    percentage: the running image, every image any container (running or
    stopped) references,     the configured ``TINYASSETS_IMAGE``, and the release
    receipt's ``rollback_target``. Explicit mutable tags
    pin images, including the cutover rollback.
    Pressure at or above the trigger still grades the pass, so disk the rule
    cannot relieve stays loud (``pressure_unmet``, exit 1).

``threshold``
    The original pressure-gated path: nothing happens below the trigger, and
    removal stops at the low watermark. Kept as an operator lever.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPOSITORY = "ghcr.io/tinyassets/tinyassets-daemon"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
BOX_HARNESS_ROOT = Path("/opt/tinyassets/boxhostd")
HARNESS_TREE = re.compile(r"^harness-[0-9a-f]{64}$")
HARNESS_STAGE = re.compile(r"^\.harness-stage\..+$")
FENCE_LOCK = Path("/run/lock/tinyassets-deploy-fence.lock")
MUTATION_LOCK = Path("/var/lock/tinyassets-host-mutation.lock")
FENCE_STATE = Path("/var/lib/tinyassets-deploy/retire-cheat-loop-task-2-1-fence.json")
MAX_OUTPUT = 4 * 1024 * 1024
MODES = ("count", "threshold")
# Current, every container image, the configured image, the receipt rollback
# target, and explicit mutable tag pins are already in the keep set.
KEEP_OLDER = 0
MAX_REMOVALS = 32
# Registry proof per pass is bounded so one unrecoverable image cannot starve
# the rest, and so the locked phase always keeps its 60-second budget.
MAX_VERIFY_ATTEMPTS = 64
LOCKED_BUDGET = 60
RECEIPT_MAX_BYTES = 16 * 1024 * 1024  # includes the full Git containment inventory
# The extracted harness-current rootfs (~2.5 GiB) already sits on the ~46.8 GiB
# post-C6 root, so a 10 GiB reserve refused pulls that still had room.
PULL_FREE_BYTES = 5 * 1024**3


class Refusal(Exception):
    """Sanitized evidence unavailable; never authorizes fallback cleanup."""


def read_regular(path, cap):
    """Read only a bounded regular file, without following its final symlink."""
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise Refusal("evidence_file_not_regular")
        raw = stream.read(cap + 1)
    if len(raw) > cap:
        raise Refusal("evidence_file_oversize")
    return raw


def read_configured_image(path=Path("/etc/tinyassets/env")):
    """Re-read the operator's current configuration under the mutation lock."""
    try:
        lines = read_regular(path, 1024 * 1024).decode("utf-8").splitlines()
        matches = []
        for line in lines:
            line = line.strip()
            if line.startswith("export "):
                line = line[7:].strip()
            key, separator, value = line.partition("=")
            if separator and key.strip() == "TINYASSETS_IMAGE":
                value = value.strip()
                if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                    value = value[1:-1]
                matches.append(value)
        if len(matches) != 1 or not matches[0] or any(c.isspace() for c in matches[0]):
            raise Refusal("configured_image_unavailable")
        return matches[0]
    except (OSError, UnicodeError):
        raise Refusal("configured_image_unavailable") from None


def pressure(path, disk_fn=shutil.disk_usage):
    usage = disk_fn(path)
    total, free = usage.total, usage.free
    if not all(math.isfinite(v) for v in (total, free)) or total <= 0 or free < 0 or free > total:
        raise Refusal("invalid_disk_measurement")
    return 100 * (1 - free / total)


def immutable(value):
    prefix = REPOSITORY + "@"
    return (
        isinstance(value, str)
        and value.startswith(prefix)
        and bool(DIGEST.fullmatch(value[len(prefix) :]))
    )


def stamp(value):
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError
        return result
    except (AttributeError, TypeError, ValueError):
        raise Refusal("invalid_image_timestamp") from None


class Docker:
    def __init__(self, deadline, *, runner=subprocess.run, clock=time.monotonic):
        self.deadline, self.runner, self.clock = deadline, runner, clock

    def command(self, *args):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise Refusal("budget_expired")
        try:
            result = self.runner(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=min(10, remaining),
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            raise Refusal("docker_unavailable") from None
        if result.returncode or len(result.stdout) > MAX_OUTPUT:
            raise Refusal("docker_refused_or_invalid_output")
        return result.stdout

    def json(self, *args):
        try:
            return json.loads(self.command(*args))
        except (ValueError, TypeError):
            raise Refusal("invalid_docker_json") from None

    def inspect_many(self, kind, identities):
        rows = []
        for offset in range(0, len(identities), 50):
            chunk = self.json(kind, "inspect", *identities[offset : offset + 50])
            if not isinstance(chunk, list):
                raise Refusal("invalid_inventory")
            rows.extend(chunk)
        return rows

    def inventory(self):
        image_ids = sorted(set(self.command("image", "ls", "-aq", "--no-trunc").split()))
        container_ids = sorted(set(self.command("container", "ls", "-aq", "--no-trunc").split()))
        if not image_ids or not container_ids or len(image_ids) + len(container_ids) > 2000:
            raise Refusal("missing_or_oversize_inventory")
        images = self.inspect_many("image", image_ids)
        containers = self.inspect_many("container", container_ids)
        return images, containers


def storage_path(info, configured=""):
    if not isinstance(info, dict):
        raise Refusal("unknown_image_store")
    driver = info.get("Driver")
    status = info.get("DriverStatus") or []
    containerd = any(row == ["driver-type", "io.containerd.snapshotter.v1"] for row in status)
    if containerd:
        path = configured
    elif driver == "overlay2":
        path = info.get("DockerRootDir", "")
    else:
        raise Refusal("unknown_image_store")
    if not isinstance(path, str) or not Path(path).is_absolute() or not path:
        raise Refusal("unknown_image_store_filesystem")
    return path


def read_receipt(docker, containers):
    active = [row for row in containers if row.get("Name") == "/tinyassets-daemon"]
    state = active[0].get("State", {}) if len(active) == 1 else {}
    if len(active) != 1 or not (state.get("Running") or state.get("Restarting")):
        raise Refusal("current_daemon_unavailable")
    daemon = active[0]
    if any(
        item.startswith("TINYASSETS_RELEASE_STATE_PATH=")
        for item in daemon.get("Config", {}).get("Env", [])
    ):
        raise Refusal("release_receipt_override")
    mounts = [m for m in daemon.get("Mounts", []) if m.get("Destination") == "/data"]
    if (
        len(mounts) != 1
        or mounts[0].get("Type") != "volume"
        or mounts[0].get("Name") != "tinyassets-data"
    ):
        raise Refusal("release_volume_mismatch")
    volumes = docker.json("volume", "inspect", "tinyassets-data")
    if (
        not isinstance(volumes, list)
        or len(volumes) != 1
        or volumes[0].get("Mountpoint") != mounts[0].get("Source")
    ):
        raise Refusal("release_volume_mismatch")
    root = Path(volumes[0]["Mountpoint"])
    if not root.is_absolute():
        raise Refusal("invalid_release_mount")
    try:
        raw = read_regular(root / "release-state.json", RECEIPT_MAX_BYTES)
        receipt = json.loads(raw)
        if not isinstance(receipt, dict) or not isinstance(receipt.get("rollback_target"), str):
            raise ValueError
    except (OSError, ValueError, AttributeError):
        raise Refusal("release_receipt_unavailable") from None
    return daemon, receipt


def candidates(images, containers, daemon, configured, receipt, keep_older=KEEP_OLDER):
    """Pure selection; mutable aliases and every ambiguity preserve cache."""
    by_id = {}
    refs = {}
    for row in images:
        identity = row.get("Id")
        if not isinstance(identity, str) or not DIGEST.fullmatch(identity) or identity in by_id:
            raise Refusal("ambiguous_image_inventory")
        by_id[identity] = row
        for ref in (row.get("RepoDigests") or []) + (row.get("RepoTags") or []):
            if ref in refs and refs[ref] != identity:
                raise Refusal("ambiguous_image_reference")
            refs[ref] = identity
    current = daemon.get("Image")
    if current not in by_id:
        raise Refusal("current_image_unmapped")
    current_time = stamp(by_id[current].get("Created"))
    protected = set()
    for container in containers:
        identity = container.get("Image")
        if identity not in by_id:
            raise Refusal("container_image_unmapped")
        protected.add(identity)
    rollback = receipt.get("rollback_target")
    for slot, ref in (("configured", configured), ("rollback_target", rollback)):
        if ref == "" and ref == receipt.get("rollback_target"):
            continue
        if isinstance(ref, str) and ref and ref in refs:
            protected.add(refs[ref])
            continue
        # An immutable rollback digest that no local image carries is already
        # gone: there is nothing left to protect, and refusing would block
        # every later deploy. A mutable tag may still name a present image
        # under another reference, so it keeps refusing.
        if slot == "rollback_target" and immutable(ref) and not any(
                digest.endswith("@" + ref.rsplit("@", 1)[1])
                for row in by_id.values() for digest in row.get("RepoDigests") or []):
            print("rollback_target_absent", file=sys.stderr)
            continue
        raise Refusal("protected_reference_unmapped:" + slot)
    if not configured:
        raise Refusal("configured_image_missing")
    older = []
    eligible = []
    for identity, row in by_id.items():
        digests = row.get("RepoDigests") or []
        if not any(immutable(ref) for ref in digests):
            continue
        created = stamp(row.get("Created"))
        if created < current_time:
            older.append((created, identity))
        # Containerd can repeat the exact immutable digest in RepoTags. This
        # is not a mutable alias; every extra or different alias stays excluded.
        tags = row.get("RepoTags") or []
        if len(digests) == 1 and immutable(digests[0]) and (not tags or tags == digests):
            eligible.append((created, identity, digests[0]))
    protected.update(identity for _, identity in sorted(older, reverse=True)[:keep_older])
    return [
        dict(image_id=identity, ref=ref)
        for _, identity, ref in sorted(eligible)
        if identity not in protected
    ], sorted(protected)


def check_pull_headroom(
    docker=None, environ=None, disk_fn=shutil.disk_usage, image_ref="", *,
    repair_host_root: Path | None = None,
):
    """Check root and Docker's actual image filesystem, after retention.

    ``repair_host_root`` is the fixture root for tests. Production repairs the
    real host only when this process is root, which is the deploy prune step.
    """
    if repair_host_root is not None:
        repair_host_symlink_modes(host_root=Path(repair_host_root))
    elif hasattr(os, "geteuid") and os.geteuid() == 0:
        repair_host_symlink_modes()
    docker = docker or Docker(time.monotonic() + 30)
    env = os.environ if environ is None else environ
    if image_ref:
        if not immutable(image_ref):
            raise Refusal("invalid_pull_reference")
        images, _ = docker.inventory()
        if any(image_ref in (row.get("RepoDigests") or []) for row in images):
            return  # An immutable cached image needs no download/extraction reserve.
    info = docker.json("info", "--format", "{{json .}}")
    store = storage_path(info, env.get("TINYASSETS_IMAGE_RETENTION_STORAGE_PATH", ""))
    for path in sorted({"/", store}):
        usage = disk_fn(path)
        pct = pressure(path, disk_fn=lambda _: usage)
        if pct >= 90 or usage.free < PULL_FREE_BYTES:
            raise Refusal("deploy_disk_headroom_insufficient")


def image_refs(images):
    """Map every repo digest and tag onto its image id."""
    refs = {}
    seen = set()
    for row in images:
        if not isinstance(row, dict):
            raise Refusal("ambiguous_image_inventory")
        identity = row.get("Id")
        if not isinstance(identity, str) or not DIGEST.fullmatch(identity) or identity in seen:
            raise Refusal("ambiguous_image_inventory")
        seen.add(identity)
        for ref in (row.get("RepoDigests") or []) + (row.get("RepoTags") or []):
            if ref in refs and refs[ref] != identity:
                raise Refusal("ambiguous_image_reference")
            refs[ref] = identity
    return refs


def _grant_harness_write(entry):
    """Make real directories writable so unlink can remove their children.

    ``os.walk`` lists symlinks in ``dirnames`` and ``filenames``. ``os.chmod``
    follows them, so a loop (``usr/bin/X11 -> .``) raises ``ELOOP`` and an
    absolute link would change a host file. Unlink needs write on the
    directory, not on the file or symlink.
    """
    def _chmod_dir(path):
        if os.path.islink(path):
            return
        try:
            os.chmod(path, 0o700)
        except FileNotFoundError:
            return

    if os.path.islink(entry):
        return
    try:
        os.chmod(entry, 0o700)
    except FileNotFoundError:
        return
    for dirpath, dirnames, _filenames in os.walk(entry, topdown=True, followlinks=False):
        for dirname in dirnames:
            _chmod_dir(os.path.join(dirpath, dirname))


# Host paths a harness symlink must never reopen. The old walk chmod'd through
# absolute links; these trees stay at whatever mode the operator set.
_REPAIR_PROTECTED = (
    "/etc/tinyassets",
    "/etc/ssh",
    "/etc/ssl/private",
    "/root",
    "/home",
    "/var/lib/docker",
    "/var/lib/tinyassets-deploy",
)
_REPAIR_DEVICES = (
    "/dev/null", "/dev/zero", "/dev/full", "/dev/random",
    "/dev/urandom", "/dev/tty", "/dev/ptmx",
)
_REPAIR_STICKY = ("/run/lock", "/tmp", "/var/tmp")
_REPAIR_SYSTEM_DIRS = (
    "/run", "/etc", "/etc/ssl", "/etc/ssl/certs", "/etc/alternatives",
    "/var", "/var/lib", "/var/spool", "/opt", "/usr",
)
_REPAIR_WALK = (
    "/usr/bin", "/usr/sbin", "/usr/libexec", "/usr/local/bin",
    "/usr/local/sbin", "/usr/local/libexec", "/usr/lib", "/usr/share",
    "/etc/alternatives", "/etc/ssl/certs",
)
_REPAIR_DIR_ROOTS = ("/usr", "/etc/ssl/certs", "/etc/alternatives")
_REPAIR_LINK_EXEC = (
    "/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/", "/usr/libexec/",
    "/usr/local/bin/", "/usr/local/sbin/", "/usr/local/libexec/", "/opt/",
)
_REPAIR_DIRECT_EXEC = (
    "/usr/bin/", "/usr/sbin/", "/bin/", "/sbin/",
    "/usr/local/bin/", "/usr/local/sbin/",
)
_REPAIR_DIRECT_ROOTS = (
    "/usr/bin", "/usr/sbin", "/usr/libexec", "/usr/local/bin",
    "/usr/local/sbin", "/usr/local/libexec", "/usr/lib", "/usr/share",
    "/etc/ssl/certs",
)
_REPAIR_DIRECT_FILES = ("/etc/environment", "/etc/machine-id", "/etc/ssl/openssl.cnf")


def _repair_real_host(host_root: Path) -> bool:
    return os.path.realpath(host_root) == "/"


def _repair_fs(host_root: Path, logical: str) -> str:
    if _repair_real_host(host_root):
        return logical
    return os.path.join(str(host_root), logical.lstrip("/"))


def _repair_logical(host_root: Path, fs_path: str) -> str:
    if _repair_real_host(host_root):
        return fs_path
    relative = os.path.relpath(fs_path, host_root)
    return "/" if relative == "." else "/" + relative


def _repair_protected(logical: str) -> bool:
    path = os.path.normpath(logical)
    for prefix in _REPAIR_PROTECTED:
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def _repair_under(logical: str, roots: tuple[str, ...]) -> bool:
    path = os.path.normpath(logical)
    for root in roots:
        base = root.rstrip("/") or "/"
        if path == base or path.startswith(base + "/"):
            return True
    return False


def _repair_resolve(host_root: Path, target: str) -> str | None:
    """Resolve an absolute symlink target. Fixture roots stay inside host_root."""
    if not target.startswith("/"):
        return None
    if _repair_real_host(host_root):
        try:
            resolved = os.path.realpath(target)
        except OSError:
            return None
        return resolved if os.path.exists(resolved) else None
    logical = os.path.normpath(target)
    seen: set[str] = set()
    while len(seen) < 32:
        if logical in seen:
            return None
        seen.add(logical)
        fs_path = _repair_fs(host_root, logical)
        try:
            if os.path.islink(fs_path):
                nxt = os.readlink(fs_path)
                if nxt.startswith("/"):
                    logical = os.path.normpath(nxt)
                else:
                    logical = os.path.normpath(os.path.join(os.path.dirname(logical), nxt))
                continue
            if os.path.lexists(fs_path):
                return logical
        except OSError:
            return None
        return None
    return None


def _repair_file_mode(logical: str, fs_path: str, *, direct: bool) -> int:
    prefixes = _REPAIR_DIRECT_EXEC if direct else _REPAIR_LINK_EXEC
    if any(os.path.normpath(logical).startswith(prefix) for prefix in prefixes):
        return 0o755
    try:
        with open(fs_path, "rb") as handle:
            magic = handle.read(4)
    except OSError:
        return 0o644
    if magic.startswith(b"\x7fELF") or magic.startswith(b"#!"):
        return 0o755
    return 0o644


def repair_host_symlink_modes(
    harness_root: Path = BOX_HARNESS_ROOT, *, host_root: Path = Path("/"),
) -> list[str]:
    """Undo host modes changed through absolute symlinks in a harness rootfs.

    ``os.chmod`` follows links. An earlier prune ran as root inside
    ``harness-*`` and set host targets of absolute links to ``0o600`` (files
    and devices) or ``0o700`` (directories). ``/usr/bin/awk`` then failed with
    EACCES even for root. ``host_root`` is ``/`` on the droplet and a temp
    tree in tests, so the repair never resolves a fixture link against the
    machine running the test.
    """
    host_root = Path(host_root)
    harness_root = Path(harness_root)
    repaired: list[str] = []

    def _chmod(logical: str, fs_path: str, mode: int) -> None:
        try:
            os.chmod(fs_path, mode)
        except OSError:
            return
        repaired.append(logical)

    def _restore_named_dir(logical: str, mode: int, allowed: tuple[int, ...]) -> None:
        fs_path = _repair_fs(host_root, logical)
        if os.path.islink(fs_path):
            return
        try:
            st = os.lstat(fs_path)
        except OSError:
            return
        if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) in allowed:
            _chmod(logical, fs_path, mode)

    for logical in _REPAIR_DEVICES:
        fs_path = _repair_fs(host_root, logical)
        try:
            st = os.stat(fs_path)
        except OSError:
            continue
        if stat.S_ISCHR(st.st_mode) and stat.S_IMODE(st.st_mode) != 0o666:
            _chmod(logical, fs_path, 0o666)
    for logical in _REPAIR_STICKY:
        _restore_named_dir(logical, 0o1777, (0o700, 0o600))
    for logical in _REPAIR_SYSTEM_DIRS:
        _restore_named_dir(logical, 0o755, (0o700, 0o600))

    def _restore_regular(logical: str, fs_path: str) -> None:
        if _repair_protected(logical):
            return
        if not (
            _repair_under(logical, _REPAIR_DIRECT_ROOTS) or logical in _REPAIR_DIRECT_FILES
        ):
            return
        try:
            st = os.lstat(fs_path)
        except OSError:
            return
        imode = stat.S_IMODE(st.st_mode)
        if not stat.S_ISREG(st.st_mode) or imode not in (0o600, 0o700):
            return
        mode = _repair_file_mode(logical, fs_path, direct=True)
        # A symlink listed as a directory was chmod'd to 0o700. Only an
        # executable is restored from that mode; a data file stays put.
        if imode == 0o700 and mode != 0o755:
            return
        _chmod(logical, fs_path, mode)

    def _restore_link(fs_path: str) -> None:
        try:
            target = os.readlink(fs_path)
        except OSError:
            return
        if not target.startswith("/"):
            return
        logical = _repair_resolve(host_root, target)
        if logical is None or _repair_protected(logical):
            return
        resolved = logical if _repair_real_host(host_root) else _repair_fs(host_root, logical)
        try:
            st = os.lstat(resolved)
        except OSError:
            return
        imode = stat.S_IMODE(st.st_mode)
        if stat.S_ISCHR(st.st_mode) and logical.startswith("/dev/") and imode == 0o600:
            _chmod(logical, resolved, 0o666)
        elif stat.S_ISDIR(st.st_mode) and imode in (0o700, 0o600):
            _chmod(
                logical, resolved,
                0o1777 if logical in _REPAIR_STICKY else 0o755,
            )
        elif stat.S_ISREG(st.st_mode) and imode in (0o600, 0o700):
            mode = _repair_file_mode(logical, resolved, direct=False)
            if imode == 0o700 and mode != 0o755:
                return
            _chmod(logical, resolved, mode)

    def _restore_walk_dir(logical: str, fs_path: str) -> None:
        if _repair_protected(logical) or os.path.islink(fs_path):
            return
        if not _repair_under(logical, _REPAIR_DIR_ROOTS):
            return
        try:
            st = os.lstat(fs_path)
        except OSError:
            return
        if stat.S_ISDIR(st.st_mode) and stat.S_IMODE(st.st_mode) in (0o700, 0o600):
            _chmod(logical, fs_path, 0o755)

    for logical in _REPAIR_DIRECT_FILES:
        _restore_regular(logical, _repair_fs(host_root, logical))

    walk_roots = [_repair_fs(host_root, logical) for logical in _REPAIR_WALK]
    if harness_root.is_dir():
        for entry in sorted(harness_root.iterdir(), key=lambda item: item.name):
            if (
                entry.name.startswith("harness-")
                and entry.is_dir()
                and not entry.is_symlink()
            ):
                walk_roots.append(str(entry))
    for walk_root in walk_roots:
        if not os.path.isdir(walk_root) or os.path.islink(walk_root):
            continue
        for dirpath, dirnames, filenames in os.walk(walk_root, followlinks=False):
            logical_dir = _repair_logical(host_root, dirpath)
            _restore_walk_dir(logical_dir, dirpath)
            for name in dirnames:
                path = os.path.join(dirpath, name)
                if os.path.islink(path):
                    _restore_link(path)
            for name in filenames:
                path = os.path.join(dirpath, name)
                if os.path.islink(path):
                    _restore_link(path)
                else:
                    _restore_regular(_repair_logical(host_root, path), path)
    return repaired


def _legacy_snapshot_dirs(host_root: Path) -> list[Path]:
    """Directory copies of gVisor trees left in pre-#5258 deploy snapshots.

    ``current`` is a symlink to ``release-*`` and ``cp -a`` snapshots the link.
    Older snapshots stored the binaries as real directories at
    ``bundle-snapshots/*/host/root/{current,release-*}`` and
    ``cutover/bundle/host/root/{current,release-*}``.
    """
    base = Path(host_root) / "var/lib/tinyassets-deploy"
    try:
        base_real = base.resolve(strict=True)
    except OSError:
        return []
    parents: list[Path] = []
    snapshots = base / "bundle-snapshots"
    if snapshots.is_dir() and not snapshots.is_symlink():
        try:
            snaps = sorted(snapshots.iterdir(), key=lambda item: item.name)
        except OSError:
            snaps = []
        for snap in snaps:
            if snap.is_symlink() or not snap.is_dir():
                continue
            parents.append(snap / "host" / "root")
    parents.append(base / "cutover" / "bundle" / "host" / "root")
    found = []
    for parent in parents:
        if parent.is_symlink() or not parent.is_dir():
            continue
        try:
            parent.resolve(strict=True).relative_to(base_real)
            entries = sorted(parent.iterdir(), key=lambda item: item.name)
        except (OSError, ValueError):
            continue
        for entry in entries:
            name = entry.name
            if name != "current" and not name.startswith("release-"):
                continue
            if entry.is_symlink() or not entry.is_dir():
                continue
            try:
                entry.resolve(strict=True).relative_to(base_real)
            except (OSError, ValueError):
                continue
            found.append(entry)
    return found


def _remove_legacy_snapshot_dirs(host_root: Path) -> list[str]:
    base = Path(host_root) / "var/lib/tinyassets-deploy"
    removed = []
    for entry in _legacy_snapshot_dirs(host_root):
        _grant_harness_write(entry)
        shutil.rmtree(entry)
        removed.append(entry.relative_to(base).as_posix())
    return removed


def _live_box_harnesses(bundles, root):
    """Harness names a running owner sandbox reads, or None when unknowable.

    boxhostd writes each sandbox's rootfs into its bundle's config.json and
    removes that file when the sandbox stops.
    """
    if not bundles.exists():
        return set()
    names = set()
    try:
        for config in bundles.glob("owner-*/config.json"):
            if config.is_symlink():
                return None
            path = Path(json.loads(config.read_text(encoding="utf-8"))["root"]["path"])
            if path.parent == root and HARNESS_TREE.fullmatch(path.name):
                names.add(path.name)
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return names


def prune_box_harnesses(
    daemon, configured, refs, root=None, *, repair_host=False, host_root=None,
):
    """Remove unreferenced box rootfs copies and abandoned extract stages.

    ``harness-current``, the running daemon image, and the configured image
    stay. Every other ``harness-<64 hex>`` directory and ``.harness-stage.*``
    directory is a full unshared ``tar -x`` and is removed.

    On the real host (or when a test passes ``repair_host=True``), restore
    host modes that an earlier chmod followed out of these trees. That runs
    before the trees are deleted, so their absolute symlinks are still evidence.
    The same pass removes legacy gVisor directory copies under deploy snapshots.
    """
    if root is None:
        root = Path(os.environ.get("TINYASSETS_BOX_HARNESS_ROOT", str(BOX_HARNESS_ROOT)))
    else:
        root = Path(root)
    legacy: list[str] = []
    if repair_host or root == BOX_HARNESS_ROOT:
        host = Path("/") if host_root is None else Path(host_root)
        repair_host_symlink_modes(root, host_root=host)
        legacy = _remove_legacy_snapshot_dirs(host)
    if not root.is_dir():
        return legacy
    keep = {"harness-current"}
    current = root / "harness-current"
    # readlink does not follow. resolve() walks the target and raises ELOOP
    # when harness-current or a path it names is a symlink loop.
    if current.is_symlink():
        try:
            keep.add(Path(os.readlink(current)).name)
        except OSError:
            pass
    image = daemon.get("Image", "") if isinstance(daemon, dict) else ""
    if isinstance(image, str) and image:
        keep.add("harness-" + image.removeprefix("sha256:"))
    if isinstance(configured, str) and isinstance(refs, dict) and configured in refs:
        identity = refs[configured]
        if isinstance(identity, str):
            keep.add("harness-" + identity.removeprefix("sha256:"))
    removed = []
    # Launches hold this lock shared from image selection until their bundle names
    # the image (tinyassets/boxes/images.py harness_lease); hold it exclusively from
    # the bundle scan through deletion. Busy launches: leave every image this time.
    lock = _harness_lock(root)
    if lock is None:
        return legacy
    try:
        # An owner left serving through a deploy still runs its previous image
        # until its own sleep; its sandbox reads that directory directly.
        live = _live_box_harnesses(Path(os.environ.get(
            "TINYASSETS_BOX_BUNDLE_ROOT", "/var/lib/boxhostd/state/bundles")), root)
        if live is None:
            return legacy  # Unknown live set: remove no box image at all.
        keep |= live
        removed = _remove_harnesses(root, keep)
    finally:
        os.close(lock)
    return removed + legacy


def _harness_lock(root, timeout=None):
    import fcntl

    if timeout is None:
        timeout = float(os.environ.get("TINYASSETS_HARNESS_LOCK_SECONDS", "120"))

    fd = os.open(root / ".harness.lock", os.O_RDWR | os.O_CREAT, 0o600)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except BlockingIOError:
            if time.monotonic() >= deadline:
                os.close(fd)
                return None
            time.sleep(0.5)


def _remove_harnesses(root, keep):
    removed = []
    for entry in root.iterdir():
        name = entry.name
        if name in keep or not (HARNESS_TREE.fullmatch(name) or HARNESS_STAGE.fullmatch(name)):
            continue
        if entry.is_symlink() or not entry.is_dir():
            continue
        _grant_harness_write(entry)
        shutil.rmtree(entry)
        removed.append(name)
    return removed


def prune_dangling(docker):
    """Remove truly unreferenced image IDs and their dangling parent layers.

    A digest-pulled rollback is not disposable merely because its tag is <none>.
    No force, volume, build-cache or system prune is used.
    """
    images, containers = docker.inventory()
    used = {row["Image"] for row in containers}
    removed = []
    for row in images:
        if (row["Id"] not in used and not row.get("RepoTags")
                and not row.get("RepoDigests")):
            docker.command("image", "rm", row["Id"])
            removed.append(row["Id"])
            if len(removed) >= MAX_REMOVALS:
                break
    return removed


class SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        parsed = urllib.parse.urlsplit(newurl)
        if parsed.scheme != "https" or not (
            parsed.hostname == "ghcr.io"
            or (parsed.hostname or "").endswith(".githubusercontent.com")
        ):
            raise Refusal("registry_redirect_refused")
        target = super().redirect_request(req, fp, code, msg, headers, newurl)
        if target is not None and parsed.hostname != "ghcr.io":
            target.remove_header("Authorization")
        return target


class Registry:
    def __init__(self, deadline, *, opener=None, clock=time.monotonic):
        self.deadline, self.clock = deadline, clock
        self.opener = opener or urllib.request.build_opener(SafeRedirect())
        self.token = None

    def request(self, url, *, head=False, token=True):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise Refusal("budget_expired")
        headers = {
            "Accept": (
                "application/vnd.oci.image.index.v1+json, "
                "application/vnd.oci.image.manifest.v1+json, "
                "application/vnd.docker.distribution.manifest.list.v2+json, "
                "application/vnd.docker.distribution.manifest.v2+json"
            )
        }
        if token:
            headers["Authorization"] = "Bearer " + self.get_token()
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise Refusal("budget_expired")
        request = urllib.request.Request(url, headers=headers, method="HEAD" if head else "GET")
        try:
            with self.opener.open(request, timeout=min(5, remaining)) as response:
                raw = b"" if head else response.read(MAX_OUTPUT + 1)
                if response.status != 200 or len(raw) > MAX_OUTPUT:
                    raise Refusal("registry_object_unavailable")
                return raw
        except (OSError, ValueError):
            raise Refusal("registry_unavailable") from None

    def get_token(self):
        if self.token is None:
            raw = self.request(
                "https://ghcr.io/token?service=ghcr.io&scope=repository:tinyassets/tinyassets-daemon:pull",
                token=False,
            )
            try:
                self.token = json.loads(raw)["token"]
                if not isinstance(self.token, str) or not 0 < len(self.token) < 65536:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise Refusal("registry_token_unavailable") from None
        return self.token

    def manifest(self, digest):
        if not DIGEST.fullmatch(digest):
            raise Refusal("invalid_registry_digest")
        raw = self.request(f"https://ghcr.io/v2/tinyassets/tinyassets-daemon/manifests/{digest}")
        if "sha256:" + hashlib.sha256(raw).hexdigest() != digest:
            raise Refusal("registry_manifest_hash_mismatch")
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError
            return data
        except ValueError:
            raise Refusal("invalid_registry_manifest") from None

    def verify(self, candidate, platform):
        ref, identity = candidate["ref"], candidate["image_id"]
        if not immutable(ref):
            raise Refusal("invalid_removal_reference")
        digest = ref.split("@", 1)[1]
        manifest = self.manifest(digest)
        if "manifests" in manifest:
            selected = [
                child
                for child in manifest["manifests"]
                if child.get("platform", {}).get("os") == platform[0]
                and child.get("platform", {}).get("architecture") == platform[1]
                and not child.get("platform", {}).get("variant")
            ]
            if len(selected) != 1:
                raise Refusal("registry_platform_ambiguous")
            manifest = self.manifest(selected[0]["digest"])
        config = manifest.get("config", {}).get("digest")
        if identity not in (digest, config):
            raise Refusal("local_registry_identity_mismatch")
        if not isinstance(config, str) or not DIGEST.fullmatch(config):
            raise Refusal("invalid_registry_digest")
        raw = self.request(f"https://ghcr.io/v2/tinyassets/tinyassets-daemon/blobs/{config}")
        if "sha256:" + hashlib.sha256(raw).hexdigest() != config:
            raise Refusal("registry_config_hash_mismatch")
        try:
            config_data = json.loads(raw)
            if (config_data.get("os"), config_data.get("architecture")) != platform:
                raise Refusal("registry_platform_mismatch")
        except (ValueError, AttributeError):
            raise Refusal("invalid_registry_config") from None
        layers = manifest.get("layers")
        if not isinstance(layers, list) or len(layers) > 256:
            raise Refusal("invalid_registry_layers")
        for descriptor in [manifest.get("config", {}), *layers]:
            blob = descriptor.get("digest")
            if not isinstance(blob, str) or not DIGEST.fullmatch(blob):
                raise Refusal("invalid_registry_digest")
            self.request(f"https://ghcr.io/v2/tinyassets/tinyassets-daemon/blobs/{blob}", head=True)


@contextmanager
def locks(paths=(FENCE_LOCK, MUTATION_LOCK), state=FENCE_STATE):
    import fcntl

    descriptors = []
    try:
        for path in paths:
            fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            descriptors.append(fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refusal("host_mutation_busy") from None
        try:
            os.lstat(state)
        except FileNotFoundError:
            pass
        else:
            raise Refusal("deployment_fence_present")
        yield
    finally:
        for fd in reversed(descriptors):
            os.close(fd)


def retain(
    *,
    dry_run=True,
    high=90,
    low=80,
    mode="count",
    environ=None,
    docker=None,
    registry=None,
    locker=locks,
    receipt_reader=read_receipt,
    config_reader=read_configured_image,
    measure=pressure,
    clock=time.monotonic,
):
    if mode not in MODES:
        raise Refusal("invalid_retention_mode")
    if not 0 < low < high < 100:
        raise Refusal("invalid_watermarks")
    env = os.environ if environ is None else environ
    deadline = clock() + 120
    docker = docker or Docker(deadline, clock=clock)
    registry = registry or Registry(deadline, clock=clock)
    info = docker.json("info", "--format", "{{json .}}")
    path = storage_path(info, env.get("TINYASSETS_IMAGE_RETENTION_STORAGE_PATH", ""))
    before = measure(path)
    report = dict(
        status="below_threshold",
        mode=mode,
        dry_run=dry_run,
        before_pct=before,
        after_pct=before,
        driver=info.get("Driver"),
        removed=[],
        selected=[],
        unverified=[],
    )
    triggered = before >= high
    counting = mode == "count"
    # Count mode removes excess by rule; threshold mode only under pressure.
    acting = counting or triggered
    if not acting and not dry_run:
        return report
    platform = (
        info.get("OSType"),
        {"x86_64": "amd64", "aarch64": "arm64"}.get(
            info.get("Architecture"), info.get("Architecture")
        ),
    )
    if platform not in (("linux", "amd64"), ("linux", "arm64")):
        raise Refusal("unknown_host_platform")
    images, containers = docker.inventory()
    daemon, receipt = receipt_reader(docker, containers)
    choices, protected = candidates(images, containers, daemon, config_reader(), receipt)
    verified = []
    for choice in choices[:MAX_VERIFY_ATTEMPTS]:
        if len(verified) >= MAX_REMOVALS or clock() >= deadline - LOCKED_BUDGET:
            break
        try:
            registry.verify(choice, platform)
        except Refusal as exc:
            # Never remove what cannot be re-pulled, but one such image must
            # not block the rule for every other excess image.
            report["unverified"].append(dict(ref=choice["ref"], reason=str(exc)))
            continue
        verified.append(choice)
    report.update(verified=verified, protected_image_ids=protected, candidates=len(choices))
    if verified:
        with locker():
            docker.deadline = min(deadline, clock() + LOCKED_BUDGET)
            for choice in verified:
                if clock() >= docker.deadline:
                    raise Refusal("budget_expired")
                images, containers = docker.inventory()
                daemon, receipt = receipt_reader(docker, containers)
                fresh, protected = candidates(
                    images, containers, daemon, config_reader(), receipt
                )
                report["protected_image_ids"] = protected
                if choice not in fresh:
                    continue
                if not acting:
                    continue  # Threshold dry-run still proves preservation below the trigger.
                if not counting:
                    report["after_pct"] = measure(path)
                    if report["after_pct"] <= low:
                        break
                report["selected"].append(choice)
                if not dry_run:
                    # Non-force removal by ID also releases containerd's digest
                    # alias; Docker removes unreferenced parent layers by default.
                    docker.command("image", "rm", choice["image_id"])
                    report["removed"].append(choice["ref"])
                    print(json.dumps(dict(event="daemon_image_removed", ref=choice["ref"])))
                    report["after_pct"] = measure(path)
    if not dry_run and acting:
        with locker():
            docker.deadline = min(deadline, clock() + LOCKED_BUDGET)
            report["dangling_removed"] = prune_dangling(docker)
            images, containers = docker.inventory()
            daemon, _receipt = receipt_reader(docker, containers)
            report["harnesses_removed"] = prune_box_harnesses(
                daemon, config_reader(), image_refs(images),
            )
        images, containers = docker.inventory()
        daemon, receipt = receipt_reader(docker, containers)
        remaining, _ = candidates(images, containers, daemon, config_reader(), receipt)
        report["remaining"] = len(remaining)
        report["after_pct"] = measure(path)
    if dry_run:
        report["status"] = "dry_run" if acting else "below_threshold"
    elif counting and report["remaining"]:
        report["status"] = "retention_incomplete"
    elif triggered:
        report["status"] = (
            "pressure_relieved" if report["after_pct"] <= low else "pressure_unmet"
        )
    elif counting:
        report["status"] = "retention_incomplete" if report["remaining"] else "retained"
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--apply",
        action="store_true",
        help="request removal; also requires TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY=1",
    )
    parser.add_argument("--prepare-pull", action="store_true",
                        help="require completed pruning and disk headroom before a pull")
    parser.add_argument("--check-headroom", action="store_true")
    parser.add_argument("--image-ref", default="")
    options = parser.parse_args(argv)
    try:
        # Repair before retain(). A stopped daemon raises
        # current_daemon_unavailable inside retain and would otherwise skip
        # the host-mode repair on --apply --prepare-pull.
        if ((options.apply or options.prepare_pull or options.check_headroom)
                and hasattr(os, "geteuid") and os.geteuid() == 0):
            repair_host_symlink_modes()
        if options.check_headroom:
            check_pull_headroom(image_ref=options.image_ref)
            print(json.dumps(dict(status="pull_headroom_ok")))
            return 0
        activation = os.environ.get("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "0")
        if activation not in ("0", "1"):
            raise Refusal("invalid_retention_activation")
        mode = os.environ.get("TINYASSETS_DAEMON_IMAGE_RETENTION_MODE", "count")
        if mode not in MODES:
            raise Refusal("invalid_retention_mode")
        report = retain(
            dry_run=not options.apply
            or activation != "1"
            or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes"),
            high=float(os.environ.get("DISK_AUTOPRUNE_PCT", "90")),
            low=float(os.environ.get("DISK_AUTOPRUNE_LOW_PCT", "80")),
            mode=mode,
        )
        if options.prepare_pull:
            if report["status"] not in ("retained", "pressure_relieved"):
                raise Refusal("pre_pull_retention_incomplete")
            check_pull_headroom(image_ref=options.image_ref)
    except (Refusal, OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        reason = str(exc) if isinstance(exc, Refusal) else "evidence_unavailable"
        print(json.dumps(dict(status="refused", reason=reason)))
        return 2
    report.update(apply_requested=options.apply, apply_enabled=activation == "1")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("below_threshold", "pressure_relieved", "retained") else 1


if __name__ == "__main__":
    raise SystemExit(main())
