"""Compose admitted acquisition and offline installation in an unpublished lease.

The workspace effector owns consent, transfer reservation and publication. This
module owns only fresh private scratch and the two supervised stages. It never
accepts a command, environment, registry or path from a workflow packet.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from tinyassets import node_sandbox as sandbox
from tinyassets import workspace_fs as fs
from tinyassets import workspace_resolver as resolver
from tinyassets.workspace_provision_process import run_provision_stage

_MANIFESTS = Path("/provision/manifests")
_CACHE = Path("/provision/cache")
_NODE_NAMES = ("package.json", "package-lock.json")
FRAME_BOUND = 12 * 1024 * 1024

_VERIFY = r'''
import hashlib, json, os, subprocess, sys
from pathlib import Path
settings = json.loads(sys.argv[1])
for names, digest in settings['digests']:
    blobs = [(Path('/provision/manifests') / name).read_bytes() for name in names]
    if hashlib.sha256(b'\0'.join(blobs)).hexdigest() != digest:
        raise SystemExit(65)
def command(argv):
    # Parent drains the inherited streams cumulatively. No package-sized
    # capture_output buffer and no inherited broker descriptor in subprocesses.
    subprocess.run(argv, env=settings['env'], cwd='/tmp', stdin=subprocess.DEVNULL,
                   close_fds=True, check=True)
'''

_ACQUIRE = r'''
if settings['python']:
    Path('/provision/cache/python').mkdir()
if settings['node']:
    prefix = Path('/provision/cache/npm-acquire')
    prefix.mkdir()
    for name in ('package.json', 'package-lock.json'):
        (prefix / name).write_bytes((Path('/provision/manifests') / name).read_bytes())
    Path('/provision/cache/node').mkdir()
_forward(3128, '/registry.sock')
for argv in settings['commands']:
    command(argv)
'''

_INSTALL = r'''
if settings['python']:
    # Exclusive creation also refuses a checkout-provided .venv or symlink.
    os.mkdir('/workspace/.venv', 0o700)
    command([sys.executable, '-I', '-m', 'venv', '/workspace/.venv'])
if settings['node']:
    for name in ('package.json', 'package-lock.json'):
        original = (Path('/workspace') / name).read_bytes()
        if original != (Path('/provision/manifests') / name).read_bytes():
            raise SystemExit(65)
for argv in settings['commands']:
    command(argv)
'''


@dataclass(frozen=True)
class ProvisionResult:
    failure: str | None
    bytes_to_charge: int


def execute_in_cell(
    manifests: resolver.ProvisionManifests,
    *,
    lease_fd: int,
    repo_fd: int,
    storage_bound: int,
    timeout_s: float,
    cancelled: Callable[[], bool],
    acquired: Callable[[], None],
) -> ProvisionResult:
    """Run as the owner, after admission; never publish a partial installation.

    All writer processes must have verified death before scratch removal. A
    termination failure propagates, retains scratch for lease recovery, and
    leaves the caller's full transfer reservation untouched.
    """
    if sys.platform != "linux":
        raise ValueError("provisioning requires Linux isolation")
    if type(manifests) is not resolver.ProvisionManifests or not (
        manifests.python or manifests.node
    ):
        raise ValueError("provisioning requires admitted manifests")
    if any(type(fd) is not int or fd < 3 for fd in (lease_fd, repo_fd)):
        raise ValueError("provisioning requires held directory descriptors")
    if any(type(bound) is not int or bound <= 0
           for bound in (storage_bound,)):
        raise ValueError("provisioning requires positive resource bounds")
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= sandbox.MAX_WORKSPACE_TIMEOUT_SECONDS):
        raise ValueError("invalid provisioning deadline")
    if not callable(cancelled):
        raise ValueError("provisioning requires cancellation checking")

    deadline = time.monotonic() + timeout_s
    if cancelled():
        return ProvisionResult("cancelled", 0)
    # Do not reuse or delete an environment supplied by the repository.
    if manifests.python:
        try:
            os.stat(".venv", dir_fd=repo_fd, follow_symlinks=False)
        except FileNotFoundError:
            pass
        else:
            return ProvisionResult("existing_python_environment", 0)

    attempt = "provision-" + uuid.uuid4().hex
    handles: list[int] = []
    attempt_created = False
    writers_stopped = True
    charge = 0

    def usage() -> int:
        # The lease contains checkout AND private staging/cache, so expanded
        # tarballs and installed files share the same reservation.
        return fs.measure_tree_beneath(lease_fd, max_bytes=storage_bound)

    try:
        attempt_fd = fs.create_cell_subdir(lease_fd, attempt)
        handles.append(attempt_fd)
        attempt_created = True
        manifest_fd = fs.create_cell_subdir(attempt_fd, "manifests")
        handles.append(manifest_fd)
        cache_fd = fs.create_cell_subdir(attempt_fd, "cache")
        handles.append(cache_fd)
        staging = Path(fs.bind_target_for(manifest_fd))
        digests = []
        acquire_commands = []
        install_commands = []
        if manifests.python:
            staged = resolver.stage_python_plan(manifests.python, staging)
            inside = resolver.StagedManifest(_MANIFESTS / staged.path.name, staged.digest)
            digests.append(([staged.path.name], staged.digest))
            acquire_commands.append(resolver.pip_download_argv(
                inside, _CACHE / "python", python=sys.executable))
            install_commands.append(resolver.pip_offline_install_argv(
                inside, _CACHE / "python", "/workspace/.venv/bin/python"))
        overlays = []
        if manifests.node:
            staged = resolver.stage_node_plan(manifests.node, staging)
            digests.append((_NODE_NAMES, staged.digest))
            for prefix, commands, builder in (
                (_CACHE / "npm-acquire", acquire_commands, resolver.npm_fetch_argv),
                (Path("/workspace"), install_commands, resolver.npm_offline_install_argv),
            ):
                inside = resolver.StagedNodeManifests(
                    prefix, prefix / _NODE_NAMES[0], prefix / _NODE_NAMES[1], staged.digest)
                commands.append(builder(inside, _CACHE / "node"))
            for name in _NODE_NAMES:
                fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW, dir_fd=manifest_fd)
                handles.append(fd)
                overlays.append(fd)
        environment = resolver.resolver_environment("/tmp", "/usr/local/bin:/usr/bin:/bin")
        settings = dict(digests=digests, env=environment, python=bool(manifests.python),
                        node=bool(manifests.node), timeout=timeout_s)
        acquire = sandbox.BwrapLauncher().for_provision(
            sandbox.ProvisionMount(manifest_fd, cache_fd, "acquire"))
        install = sandbox.BwrapLauncher().for_workspace(sandbox.WorkspaceMount(
            fs.bind_target_for(repo_fd), pass_fds=(repo_fd,))).for_provision(
                sandbox.ProvisionMount(manifest_fd, cache_fd, "install",
                                       tuple(overlays) if overlays else None))
        import inspect
        import runpy

        forward = runpy.run_path('/usr/local/libexec/ta-git.py')['_forward']
        source = inspect.getsource(forward) + '\n' + _VERIFY + _ACQUIRE
        if cancelled():
            return ProvisionResult("cancelled", 0)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ProvisionResult("timeout", 0)
        writers_stopped = False
        result = run_provision_stage(
            acquire, source, [json.dumps(dict(settings, commands=acquire_commands))],
            timeout_s=remaining, storage_bound=storage_bound, storage_usage=usage,
            cancelled=cancelled)
        writers_stopped = True
        # Host confirms every registry tunnel is closed BEFORE offline install.
        acquired()
        if result.failure:
            return ProvisionResult(result.failure, charge)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return ProvisionResult("timeout", charge)
        writers_stopped = False
        result = run_provision_stage(
            install, _VERIFY + _INSTALL,
            [json.dumps(dict(settings, commands=install_commands))],
            timeout_s=remaining, storage_bound=storage_bound, storage_usage=usage,
            cancelled=cancelled)
        writers_stopped = True
        return ProvisionResult(result.failure, charge)
    except (OSError, resolver.ResolverError, ValueError):
        return ProvisionResult("execution_failed", charge)
    finally:
        for fd in reversed(handles):
            os.close(fd)
        if attempt_created and writers_stopped:
            fs._remove_beneath(lease_fd, attempt)


def cell_main(proof):
    """Fixed entry: canonical plans only, never caller code, paths or commands."""
    from tinyassets.workspace_provision import admit_node, admit_requirements

    def send(value):
        sys.stdout.buffer.write(json.dumps(value).encode() + b'\n')
        sys.stdout.buffer.flush()

    def read():
        raw = sys.stdin.buffer.readline(FRAME_BOUND + 1)
        if len(raw) > FRAME_BOUND or not raw.endswith(b'\n'):
            raise ValueError('invalid provisioning frame')
        return json.loads(raw)

    send({'cell': proof})
    request = read()
    if set(request) != {'python', 'node', 'storage_bound', 'timeout_s'}:
        raise ValueError('invalid provisioning request')
    python = admit_requirements(request['python']) if request['python'] is not None else None
    node = admit_node(*request['node']) if request['node'] is not None else None

    def acquired():
        send({'acquired': True})
        if read() != {'install': True}:
            raise RuntimeError('registry retirement was not acknowledged')

    lease_fd = fs.open_dir_nofollow('/lease')
    repo_fd = fs.open_subdir_nofollow(lease_fd, 'repo')
    try:
        result = execute_in_cell(
            resolver.ProvisionManifests(python, node), lease_fd=lease_fd, repo_fd=repo_fd,
            storage_bound=request['storage_bound'], timeout_s=request['timeout_s'],
            cancelled=lambda: False, acquired=acquired)
        send({'failure': result.failure})
    finally:
        os.close(repo_fd)
        os.close(lease_fd)
    return 0
