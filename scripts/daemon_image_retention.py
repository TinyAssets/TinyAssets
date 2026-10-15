"""Bounded, fail-closed retention of recoverable daemon image cache only.

Two modes, selected by ``TINYASSETS_DAEMON_IMAGE_RETENTION_MODE``:

``count`` (default)
    The keep set is a rule, not a pressure response. Every owned image
    repository is counted on its own, including the box harness rootfs: keep
    the current image resolved from deployed configuration plus one rollback.
    A pass also keeps every image any container references and explicit mutable
    tags. Registry-verified images outside that set are removed whatever the
    disk percentage.
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
import time
import urllib.parse
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path

REPOSITORY = "ghcr.io/tinyassets/tinyassets-daemon"
DIGEST = re.compile(r"sha256:[0-9a-f]{64}\Z")
FENCE_LOCK = Path("/run/lock/tinyassets-deploy-fence.lock")
MUTATION_LOCK = Path("/var/lock/tinyassets-host-mutation.lock")
FENCE_STATE = Path("/var/lib/tinyassets-deploy/retire-cheat-loop-task-2-1-fence.json")
MAX_OUTPUT = 4 * 1024 * 1024
MODES = ("count", "threshold")
# The newest older image bridges the swap-to-receipt canary window.
KEEP_OLDER = 1
MAX_REMOVALS = 32
# Registry proof per pass is bounded so one unrecoverable image cannot starve
# the rest, and so the locked phase always keeps its 60-second budget.
MAX_VERIFY_ATTEMPTS = 64
LOCKED_BUDGET = 60
RECEIPT_MAX_BYTES = 16 * 1024 * 1024  # includes the full Git containment inventory
PULL_FREE_BYTES = 10 * 1024**3  # ~4.3 GiB image plus extraction and runtime reserve
LOG_MAX_SIZE = "10m"
LOG_MAX_FILE = 3
LOG_MAX_BYTES = 10 * 1024**2
HARNESS_DIR = re.compile(r"harness-[0-9a-f]{64}\Z")
DEFAULT_HARNESS_ROOT = Path("/opt/tinyassets/boxhostd")
DEFAULT_DOCKER_DAEMON_JSON = Path("/etc/docker/daemon.json")
_LOG_SIZE = re.compile(r"^(\d+)([kmgt]?)$", re.IGNORECASE)


class Refusal(Exception):
    """Sanitized evidence unavailable; never authorizes fallback cleanup."""


class Context:
    """Partial pass state printed with every refusal."""

    def __init__(self):
        self.kept = []
        self.removed = []
        self.paths = ["/"]
        self.docker = None
        self.disk_fn = shutil.disk_usage


def split_ref(value):
    if not isinstance(value, str):
        return None
    repo, separator, digest = value.partition("@")
    if not separator or not DIGEST.fullmatch(digest):
        return None
    if not repo or "/" not in repo or any(c.isspace() for c in repo):
        return None
    return repo, digest


def owned_repo(value):
    """A repository this deploy publishes. Third-party sidecars are not owned."""
    parsed = split_ref(value)
    if not parsed:
        return None
    repo = parsed[0]
    if repo == REPOSITORY or repo.startswith("ghcr.io/tinyassets/"):
        return repo
    return None


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
        matches = env_assignments(path).get("TINYASSETS_IMAGE", [])
        if len(matches) != 1 or not matches[0] or any(c.isspace() for c in matches[0]):
            raise Refusal("configured_image_unavailable")
        return matches[0]
    except (OSError, UnicodeError):
        raise Refusal("configured_image_unavailable") from None


def read_configured_images(path=Path("/etc/tinyassets/env")):
    """Every deployed ``*_IMAGE`` ref. The daemon image stays mandatory."""
    primary = read_configured_image(path)
    found = [primary]
    try:
        assignments = env_assignments(path)
    except (OSError, UnicodeError):
        raise Refusal("configured_image_unavailable") from None
    for key, values in assignments.items():
        if not key.endswith("_IMAGE") or key == "TINYASSETS_IMAGE":
            continue
        if len(values) != 1:
            raise Refusal("configured_image_unavailable")
        value = values[0]
        if not value:
            continue
        if owned_repo(value) is None or any(c.isspace() for c in value):
            raise Refusal("configured_image_unavailable")
        found.append(value)
    return found


def pressure(path, disk_fn=shutil.disk_usage):
    usage = disk_fn(path)
    total, free = usage.total, usage.free
    if not all(math.isfinite(v) for v in (total, free)) or total <= 0 or free < 0 or free > total:
        raise Refusal("invalid_disk_measurement")
    return 100 * (1 - free / total)


def immutable(value, repository=REPOSITORY):
    parsed = split_ref(value)
    return parsed is not None and parsed[0] == repository


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

    def command(self, *args, timeout_cap=10):
        remaining = self.deadline - self.clock()
        if remaining <= 0:
            raise Refusal("budget_expired")
        try:
            result = self.runner(
                ["docker", *args],
                capture_output=True,
                text=True,
                timeout=min(timeout_cap, remaining),
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
    if len(active) != 1 or not active[0].get("State", {}).get("Running"):
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


def candidates(
    images, containers, daemon, configured, receipt, keep_older=KEEP_OLDER, configured_refs=None
):
    """Pure selection; mutable aliases and every ambiguity preserve cache.

    Each owned repository keeps its own current image and one older rollback.
    A repository with no current image resolved from deployed configuration is
    left untouched.
    """
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
    for ref in (configured, receipt.get("rollback_target")):
        if ref == "" and ref == receipt.get("rollback_target"):
            continue
        if not isinstance(ref, str) or not ref or ref not in refs:
            raise Refusal("protected_reference_unmapped")
        protected.add(refs[ref])
    if not configured:
        raise Refusal("configured_image_missing")
    extra = [] if configured_refs is None else list(configured_refs)
    for ref in extra:
        if not isinstance(ref, str) or not ref or ref == configured:
            continue
        if ref not in refs:
            continue
        protected.add(refs[ref])
    current_times = {}
    daemon_repo = None
    for ref in by_id[current].get("RepoDigests") or []:
        repo = owned_repo(ref)
        if repo:
            daemon_repo = repo
            break
    if daemon_repo:
        current_times[daemon_repo] = current_time
    for ref in (configured, *extra):
        if not isinstance(ref, str) or not ref or ref not in refs:
            continue
        repo = owned_repo(ref)
        if repo and repo not in current_times:
            current_times[repo] = stamp(by_id[refs[ref]].get("Created"))
    older_by_repo = {}
    eligible = []
    for identity, row in by_id.items():
        digests = row.get("RepoDigests") or []
        repos = [owned_repo(ref) for ref in digests]
        repos = [repo for repo in repos if repo]
        if not repos or len(set(repos)) != 1:
            continue
        repo = repos[0]
        if repo not in current_times:
            continue
        created = stamp(row.get("Created"))
        if created < current_times[repo]:
            older_by_repo.setdefault(repo, []).append((created, identity))
        # Containerd can repeat the exact immutable digest in RepoTags. This
        # is not a mutable alias; every extra or different alias stays excluded.
        tags = row.get("RepoTags") or []
        if len(digests) == 1 and owned_repo(digests[0]) == repo and (not tags or tags == digests):
            eligible.append((created, identity, digests[0]))
    for rows in older_by_repo.values():
        protected.update(identity for _, identity in sorted(rows, reverse=True)[:keep_older])
    return [
        dict(image_id=identity, ref=ref)
        for _, identity, ref in sorted(eligible)
        if identity not in protected
    ], sorted(protected)


def check_pull_headroom(
    docker=None, environ=None, disk_fn=shutil.disk_usage, image_ref="", context=None
):
    """Check root and Docker's actual image filesystem, after retention."""
    docker = docker or Docker(time.monotonic() + 30)
    env = os.environ if environ is None else environ
    if context is not None:
        context.docker = docker
        context.disk_fn = disk_fn
    if image_ref:
        if not immutable(image_ref):
            raise Refusal("invalid_pull_reference")
        images, _ = docker.inventory()
        if any(image_ref in (row.get("RepoDigests") or []) for row in images):
            return  # An immutable cached image needs no download/extraction reserve.
    info = docker.json("info", "--format", "{{json .}}")
    store = storage_path(info, env.get("TINYASSETS_IMAGE_RETENTION_STORAGE_PATH", ""))
    paths = sorted({"/", store})
    if context is not None:
        context.paths = paths
    for path in paths:
        usage = disk_fn(path)
        pct = pressure(path, disk_fn=lambda _: usage)
        if pct >= 80 or usage.free < PULL_FREE_BYTES:
            raise Refusal("deploy_disk_headroom_insufficient")


def prune_dangling(docker):
    """Remove truly unreferenced image IDs.

    A digest-pulled rollback is not disposable merely because its tag is <none>.
    No force and no volume prune is used.
    """
    images, containers = docker.inventory()
    used = {row["Image"] for row in containers}
    removed = []
    for row in images:
        if row["Id"] not in used and not row.get("RepoTags") and not row.get("RepoDigests"):
            docker.command("image", "rm", row["Id"])
            removed.append(row["Id"])
            if len(removed) >= MAX_REMOVALS:
                break
    return removed


def prune_cache(docker):
    """Dangling image layers and the builder cache. Never volumes."""
    docker.command("image", "prune", "-f", timeout_cap=50)
    docker.command("builder", "prune", "-af", timeout_cap=50)


def disk_rows(paths, disk_fn):
    rows = []
    seen = []
    for path in paths:
        if path in seen:
            continue
        seen.append(path)
        try:
            usage = disk_fn(path)
            pct = pressure(path, disk_fn=lambda _path, usage=usage: usage)
        except Exception:
            rows.append({"path": str(path), "free": None, "total": None, "percent": None})
            continue
        rows.append(
            {
                "path": str(path),
                "free": usage.free,
                "total": usage.total,
                "percent": pct,
            }
        )
    return rows


def docker_df_totals(docker):
    try:
        client = docker or Docker(time.monotonic() + 20)
        raw = client.command("system", "df", "--format", "{{json .}}", timeout_cap=15)
    except Exception:
        return [{"error": "unavailable"}]
    rows = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except ValueError:
            return [{"error": "unparsed"}]
        if not isinstance(item, dict):
            return [{"error": "unparsed"}]
        rows.append(
            {
                "type": item.get("Type"),
                "total": item.get("TotalCount"),
                "size": item.get("Size"),
                "reclaimable": item.get("Reclaimable"),
            }
        )
    return rows


def refusal_payload(reason, context):
    return {
        "status": "refused",
        "reason": reason,
        "disk": disk_rows(context.paths or ["/"], context.disk_fn),
        "docker_df": docker_df_totals(context.docker),
        "kept": list(context.kept),
        "removed": list(context.removed),
    }


def log_size_bytes(value):
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return None
    if isinstance(value, int):
        return value
    match = _LOG_SIZE.fullmatch(value.strip())
    if not match:
        return None
    scale = {"": 1, "k": 1024, "m": 1024**2, "g": 1024**3, "t": 1024**4}
    return int(match.group(1)) * scale[match.group(2).lower()]


def ensure_docker_log_cap(path):
    """Merge the json-file size cap into daemon.json. Preserve every other key.

    A driver other than json-file is left alone. A cap already at or under
    10m x 3 is left alone. The file is the deployed daemon config; this does
    not restart dockerd.
    """
    path = Path(path)
    if path.is_symlink():
        raise Refusal("docker_daemon_config_symlink")
    existing = {}
    if path.exists():
        raw = path.read_bytes()
        if len(raw) > 1024 * 1024:
            raise Refusal("docker_daemon_config_oversize")
        try:
            existing = json.loads(raw) if raw.strip() else {}
        except ValueError:
            raise Refusal("docker_daemon_config_invalid") from None
        if not isinstance(existing, dict):
            raise Refusal("docker_daemon_config_invalid")
    driver = existing.get("log-driver")
    if driver not in (None, "json-file"):
        return {"changed": False, "reason": "foreign_log_driver"}
    opts = existing.get("log-opts") if isinstance(existing.get("log-opts"), dict) else {}
    size = log_size_bytes(opts.get("max-size"))
    files = opts.get("max-file")
    try:
        file_count = int(files) if files is not None and not isinstance(files, bool) else None
    except (TypeError, ValueError):
        file_count = None
    if (
        driver == "json-file"
        and size is not None
        and size <= LOG_MAX_BYTES
        and file_count is not None
        and file_count <= LOG_MAX_FILE
    ):
        return {"changed": False}
    updated = dict(existing)
    updated["log-driver"] = "json-file"
    new_opts = dict(opts)
    new_opts["max-size"] = LOG_MAX_SIZE
    new_opts["max-file"] = str(LOG_MAX_FILE)
    updated["log-opts"] = new_opts
    blob = json.dumps(updated, indent=2) + "\n"
    temporary = path.with_name(path.name + ".tmp")
    if temporary.is_symlink() or temporary.exists():
        temporary.unlink()
    temporary.write_text(blob)
    os.chmod(temporary, 0o644)
    os.replace(temporary, path)
    return {"changed": True}


def harness_root(environ):
    raw = environ.get("BOX_HARNESS_ROOT", "")
    path = Path(raw) if raw else DEFAULT_HARNESS_ROOT
    if not path.is_absolute():
        raise Refusal("invalid_harness_root")
    return path


def inspected_id(docker, ref):
    if not isinstance(ref, str) or not ref:
        return None
    try:
        rows = docker.json("image", "inspect", ref)
    except Refusal:
        return None
    if not isinstance(rows, list) or len(rows) != 1 or not isinstance(rows[0], dict):
        return None
    identity = rows[0].get("Id")
    if isinstance(identity, str) and DIGEST.fullmatch(identity):
        return identity
    return None


def harness_entries(root):
    rows = []
    for entry in root.iterdir():
        if entry.is_symlink():
            continue
        if HARNESS_DIR.fullmatch(entry.name) and entry.is_dir():
            rows.append(entry)
    return rows


def live_harness_name(root):
    link = root / "harness-current"
    if not link.exists() and not link.is_symlink():
        return None
    if not link.is_symlink():
        raise Refusal("harness_current_unmapped")
    target = os.readlink(link)
    if target.startswith("/") or "/" in target or not HARNESS_DIR.fullmatch(target):
        raise Refusal("harness_current_unmapped")
    return target


def plan_harness(root, current_id, rollback_id):
    """Keep the configured harness and one rollback. Return kept names and victims."""
    if not root.exists():
        return [], []
    if root.is_symlink() or not root.is_dir():
        raise Refusal("invalid_harness_root")
    entries = harness_entries(root)
    live = live_harness_name(root)
    current_name = None
    if isinstance(current_id, str) and current_id.startswith("sha256:"):
        current_name = "harness-" + current_id[len("sha256:") :]
        if not HARNESS_DIR.fullmatch(current_name):
            current_name = None
    if entries and current_name is None:
        raise Refusal("harness_current_unmapped")
    keep = {name for name in (current_name, live) if name}
    rollback_name = None
    if isinstance(rollback_id, str) and rollback_id.startswith("sha256:"):
        rollback_name = "harness-" + rollback_id[len("sha256:") :]
    present = {entry.name for entry in entries}
    if rollback_name in present:
        keep.add(rollback_name)
    else:
        others = [entry for entry in entries if entry.name not in keep]
        if others:
            newest = max(others, key=lambda entry: entry.stat().st_mtime)
            keep.add(newest.name)
    victims = [entry for entry in entries if entry.name not in keep]
    return sorted(keep), victims


def delete_harness(root, entry):
    if entry.is_symlink() or not entry.is_dir() or entry.parent != root:
        raise Refusal("harness_path_escaped")
    if not HARNESS_DIR.fullmatch(entry.name):
        raise Refusal("harness_path_escaped")
    shutil.rmtree(entry)


def env_assignments(path):
    lines = read_regular(path, 1024 * 1024).decode("utf-8").splitlines()
    found = {}
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[7:].strip()
        key, separator, value = line.partition("=")
        if not separator:
            continue
        key = key.strip()
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        found.setdefault(key, []).append(value)
    return found


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


def ghcr_name(repo):
    prefix = "ghcr.io/"
    if not isinstance(repo, str) or not repo.startswith(prefix) or repo.count("/") != 2:
        raise Refusal("invalid_removal_reference")
    return repo[len(prefix) :]


def scope_name(url):
    marker = "https://ghcr.io/v2/"
    if not isinstance(url, str) or not url.startswith(marker):
        return None
    rest = url[len(marker) :]
    for boundary in ("/manifests/", "/blobs/"):
        name, separator, _ = rest.partition(boundary)
        if separator and name.count("/") == 1:
            return name
    return None


class Registry:
    def __init__(self, deadline, *, opener=None, clock=time.monotonic):
        self.deadline, self.clock = deadline, clock
        self.opener = opener or urllib.request.build_opener(SafeRedirect())
        self.tokens = {}

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
            name = scope_name(url)
            if not name:
                raise Refusal("invalid_removal_reference")
            headers["Authorization"] = "Bearer " + self.get_token(name)
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

    def get_token(self, name):
        if name not in self.tokens:
            raw = self.request(
                "https://ghcr.io/token?service=ghcr.io&scope=repository:" + name + ":pull",
                token=False,
            )
            try:
                token = json.loads(raw)["token"]
                if not isinstance(token, str) or not 0 < len(token) < 65536:
                    raise ValueError
            except (ValueError, KeyError, TypeError):
                raise Refusal("registry_token_unavailable") from None
            self.tokens[name] = token
        return self.tokens[name]

    def manifest(self, name, digest):
        if not DIGEST.fullmatch(digest):
            raise Refusal("invalid_registry_digest")
        raw = self.request(f"https://ghcr.io/v2/{name}/manifests/{digest}")
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
        repo = owned_repo(ref)
        if repo is None:
            raise Refusal("invalid_removal_reference")
        name = ghcr_name(repo)
        digest = ref.split("@", 1)[1]
        manifest = self.manifest(name, digest)
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
            manifest = self.manifest(name, selected[0]["digest"])
        config = manifest.get("config", {}).get("digest")
        if identity not in (digest, config):
            raise Refusal("local_registry_identity_mismatch")
        if not isinstance(config, str) or not DIGEST.fullmatch(config):
            raise Refusal("invalid_registry_digest")
        raw = self.request(f"https://ghcr.io/v2/{name}/blobs/{config}")
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
            self.request(f"https://ghcr.io/v2/{name}/blobs/{blob}", head=True)


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
    high=85,
    low=75,
    mode="count",
    environ=None,
    docker=None,
    registry=None,
    locker=locks,
    receipt_reader=read_receipt,
    config_reader=read_configured_image,
    configured_images_reader=None,
    measure=pressure,
    clock=time.monotonic,
    context=None,
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
    if configured_images_reader is None and config_reader is read_configured_image:
        configured_images_reader = read_configured_images
    if context is not None:
        context.docker = docker
        context.paths = ["/", path]
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
        kept=[],
        harness_kept=[],
        harness_removed=[],
    )

    def configured_refs():
        if configured_images_reader is None:
            return None
        return configured_images_reader()

    def remember(images, protected_ids):
        by_id = {row.get("Id"): row for row in images}
        kept = []
        for identity in protected_ids:
            row = by_id.get(identity) or {}
            digests = [ref for ref in (row.get("RepoDigests") or []) if owned_repo(ref)]
            kept.append(digests[0] if digests else identity)
        report["kept"] = sorted(set(kept))
        if context is None:
            return
        context.kept = list(report["kept"]) + list(report["harness_kept"])
        context.removed = list(report["removed"]) + list(report["harness_removed"])

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
    choices, protected = candidates(
        images, containers, daemon, config_reader(), receipt, configured_refs=configured_refs()
    )
    remember(images, protected)
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
                    images,
                    containers,
                    daemon,
                    config_reader(),
                    receipt,
                    configured_refs=configured_refs(),
                )
                report["protected_image_ids"] = protected
                remember(images, protected)
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
                    remember(images, protected)
                    print(json.dumps(dict(event="daemon_image_removed", ref=choice["ref"])))
                    report["after_pct"] = measure(path)
    root = harness_root(env)
    if root.exists():
        current_id = inspected_id(docker, config_reader())
        rollback_id = inspected_id(docker, receipt.get("rollback_target") or "")
        kept_names, victims = plan_harness(root, current_id, rollback_id)
        report["harness_kept"] = kept_names
        report["harness_selected"] = [entry.name for entry in victims]
        remember(images, protected)
    if not dry_run and acting:
        with locker():
            docker.deadline = min(deadline, clock() + LOCKED_BUDGET)
            report["dangling_removed"] = prune_dangling(docker)
            prune_cache(docker)
            if root.exists():
                current_id = inspected_id(docker, config_reader())
                images, containers = docker.inventory()
                daemon, receipt = receipt_reader(docker, containers)
                rollback_id = inspected_id(docker, receipt.get("rollback_target") or "")
                kept_names, victims = plan_harness(root, current_id, rollback_id)
                report["harness_kept"] = kept_names
                for entry in victims:
                    delete_harness(root, entry)
                    report["harness_removed"].append(entry.name)
                remember(images, report.get("protected_image_ids") or protected)
        images, containers = docker.inventory()
        daemon, receipt = receipt_reader(docker, containers)
        remaining, protected = candidates(
            images,
            containers,
            daemon,
            config_reader(),
            receipt,
            configured_refs=configured_refs(),
        )
        report["remaining"] = len(remaining)
        report["protected_image_ids"] = protected
        remember(images, protected)
        report["after_pct"] = measure(path)
    if dry_run:
        report["status"] = "dry_run" if acting else "below_threshold"
    elif counting and report["remaining"]:
        report["status"] = "retention_incomplete"
    elif triggered:
        report["status"] = "pressure_relieved" if report["after_pct"] <= low else "pressure_unmet"
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
    parser.add_argument(
        "--prepare-pull",
        action="store_true",
        help="require completed pruning and disk headroom before a pull",
    )
    parser.add_argument("--check-headroom", action="store_true")
    parser.add_argument("--image-ref", default="")
    options = parser.parse_args(argv)
    context = Context()
    try:
        if options.check_headroom:
            check_pull_headroom(image_ref=options.image_ref, context=context)
            print(json.dumps(dict(status="pull_headroom_ok")))
            return 0
        activation = os.environ.get("TINYASSETS_DAEMON_IMAGE_RETENTION_APPLY", "0")
        if activation not in ("0", "1"):
            raise Refusal("invalid_retention_activation")
        mode = os.environ.get("TINYASSETS_DAEMON_IMAGE_RETENTION_MODE", "count")
        if mode not in MODES:
            raise Refusal("invalid_retention_mode")
        dry_run = (
            not options.apply
            or activation != "1"
            or os.environ.get("DRY_RUN", "").lower() in ("1", "true", "yes")
        )
        log_cap = None
        if not dry_run:
            cap_path = Path(
                os.environ.get("TINYASSETS_DOCKER_DAEMON_JSON", str(DEFAULT_DOCKER_DAEMON_JSON))
            )
            if cap_path.parent.is_dir():
                log_cap = ensure_docker_log_cap(cap_path)
        report = retain(
            dry_run=dry_run,
            high=float(os.environ.get("DISK_AUTOPRUNE_PCT", "85")),
            low=float(os.environ.get("DISK_AUTOPRUNE_LOW_PCT", "75")),
            mode=mode,
            context=context,
        )
        if log_cap is not None:
            report["log_cap"] = log_cap
        if options.prepare_pull:
            if report["status"] not in ("retained", "pressure_relieved"):
                raise Refusal("pre_pull_retention_incomplete")
            check_pull_headroom(image_ref=options.image_ref, context=context)
    except (Refusal, OSError, ValueError, TypeError, KeyError, AttributeError, ImportError) as exc:
        reason = str(exc) if isinstance(exc, Refusal) else "evidence_unavailable"
        print(json.dumps(refusal_payload(reason, context), sort_keys=True))
        return 2
    report.update(apply_requested=options.apply, apply_enabled=activation == "1")
    print(json.dumps(report, sort_keys=True))
    return 0 if report["status"] in ("below_threshold", "pressure_relieved", "retained") else 1


if __name__ == "__main__":
    raise SystemExit(main())
