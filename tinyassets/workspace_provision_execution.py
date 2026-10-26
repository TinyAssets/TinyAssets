"""Provision a checked-out workspace lease inside the admitted owner's box.

The coordinator supplies admitted manifests and the lease only AFTER consent,
manifest admission and the maximum transfer reservation. One bounded execution
in the box creates the environment (a ``.venv`` from the Python manifest, an
``npm ci`` from the Node manifests) with the box's own public egress; the
storage charge is the lease's measured growth, never more than the
reservation. This module grants no authority, creates no lease and publishes
no checkout. Package logs are internal diagnostics, never public evidence.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tinyassets.boxes.node import MAX_WORKSPACE_TIMEOUT_SECONDS
from tinyassets.workspace_resolver import ProvisionManifests

FRAME_BOUND = 12 * 1024 * 1024
_OUTPUT_BYTES = 1024 * 1024

_STAGE = r'''
import json, os, subprocess, sys, tempfile
settings = json.loads(sys.stdin.readline())
repo = settings["repo"]
os.chdir(repo)
def command(argv):
    subprocess.run(argv, cwd=repo, stdin=subprocess.DEVNULL, close_fds=True, check=True)
if settings["python"] is not None:
    # Exclusive creation refuses a checkout-provided .venv or symlink.
    os.mkdir(os.path.join(repo, ".venv"), 0o700)
    command([sys.executable, "-I", "-m", "venv", os.path.join(repo, ".venv")])
    with tempfile.TemporaryDirectory(prefix="ta-requirements-") as scratch:
        manifest = os.path.join(scratch, "requirements.txt")
        with open(manifest, "w", encoding="utf-8") as stream:
            stream.write(settings["python"])
        command([os.path.join(repo, ".venv", "bin", "python"), "-I", "-m", "pip", "install",
                 "--no-input", "--disable-pip-version-check", "--requirement", manifest])
if settings["node"] is not None:
    for name, expected in zip(("package.json", "package-lock.json"), settings["node"]):
        with open(os.path.join(repo, name), "rb") as stream:
            if stream.read() != expected.encode("utf-8"):
                raise SystemExit(65)
    command(["npm", "ci", "--ignore-scripts", "--no-audit", "--no-fund"])
'''


@dataclass(frozen=True)
class ProvisionResult:
    failure: str | None
    bytes_to_charge: int


def execute_provision(manifests, *, lease_path, max_transfer_bytes, storage_bound,
                      timeout_s, cancelled, universe_dir, principal) -> ProvisionResult:
    from tinyassets.effectors.workspace import _box_tree_bytes, _lease_center_root
    from tinyassets.owner_runtime import require

    if type(manifests) is not ProvisionManifests or not (manifests.python or manifests.node):
        raise ValueError('provisioning requires admitted manifests')
    if any(type(v) is not int or v <= 0 for v in (max_transfer_bytes, storage_bound)):
        raise ValueError('provisioning requires positive resource bounds')
    if (type(timeout_s) not in (int, float) or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= MAX_WORKSPACE_TIMEOUT_SECONDS or not callable(cancelled)):
        raise ValueError('invalid provisioning deadline or cancellation')
    runtime = require(Path(universe_dir), principal)
    lease = Path(lease_path)
    try:
        relative = lease.relative_to(_lease_center_root(Path(universe_dir)))
    except ValueError:
        raise PermissionError('provisioning lease is outside the admitted center') from None
    if any(part in ('', '.', '..') for part in relative.parts) or not relative.parts:
        raise PermissionError('invalid provisioning lease path')
    root = str(universe_dir)
    settings = {
        'repo': root + '/' + (relative / 'repo').as_posix(),
        'python': manifests.python.normalized_text if manifests.python else None,
        'node': ([manifests.node.normalized_package_json, manifests.node.normalized_lockfile]
                 if manifests.node else None),
    }
    payload = json.dumps(settings).encode() + b'\n'
    if len(payload) > FRAME_BOUND:
        raise ValueError('provisioning manifests exceed the box frame bound')
    if cancelled():
        return ProvisionResult('cancelled', 0)
    before = _box_tree_bytes(lease, storage_bound)
    failure = _stage(runtime, root, payload, timeout_s, cancelled)
    after = _box_tree_bytes(lease, storage_bound + 1)
    if failure is None and after > storage_bound:
        failure = 'storage_limit'
    charge = min(max(after - before, 0), max_transfer_bytes)
    return ProvisionResult(failure, charge if failure is None else max_transfer_bytes)


def _stage(runtime, root: str, payload: bytes, timeout_s: float,
           cancelled: Callable[[], bool]) -> str | None:
    from tinyassets.boxes.provider import BoxError
    from tinyassets.owner_process import OwnerProcessCancelled

    try:
        outcome = runtime.tools.run(
            ["python", "-I", "-c", _STAGE], cwd=root,
            stdin=payload, wall_seconds=float(timeout_s), output_bytes=_OUTPUT_BYTES,
            should_cancel=cancelled,
        )
    except OwnerProcessCancelled:
        return 'cancelled'
    except BoxError as exc:
        return str(exc).rsplit(': ', 1)[-1]
    except Exception:
        return 'launch_or_supervision_failed'
    return None if outcome.returncode == 0 else 'process_failed'
