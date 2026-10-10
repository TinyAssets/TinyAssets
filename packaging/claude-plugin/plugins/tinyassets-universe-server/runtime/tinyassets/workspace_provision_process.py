"""Supervise one private provisioning jail; not a public command executor.

The coordinator supplies trusted stage code and owned typed mounts only AFTER
consent, manifest admission and the maximum transfer reservation. It supplies
a bounded, no-follow storage measurement over its held lease/cache handles.
This module does not grant authority, create a lease, or publish a checkout.
Returned output is internal diagnostic data, never public evidence verbatim.
"""

from __future__ import annotations

import json
import math
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass

from tinyassets import node_sandbox as sandbox

_BOOTSTRAP = """
import json, resource, sys
settings = json.loads(sys.argv[1])
exec(settings['rlimit_helper'])
if _apply_rlimits(resource, settings['timeout'], settings['profile']):
    raise SystemExit(78)
source = sys.argv[2]
sys.argv = ['-c', *sys.argv[3:]]
exec(compile(source, '<provision-stage>', 'exec'), {'__name__': '__main__'})
"""


@dataclass(frozen=True)
class StageResult:
    failure: str | None
    stdout: bytes
    stderr: bytes


def run_provision_stage(
    launcher: sandbox.BwrapLauncher,
    source: str,
    args: list[str],
    *,
    timeout_s: float,
    storage_bound: int,
    storage_usage: Callable[[], int],
    cancelled: Callable[[], bool],
) -> StageResult:
    """Run inside the owner cell, supervising one nested stage until it is dead.

    Acquisition gets only the fixed registry socket. Offline installation gets
    no relay. Measurement errors refuse the stage and teardown errors propagate.
    """
    if type(launcher) is not sandbox.BwrapLauncher or launcher.provision_mount is None:
        raise ValueError("provisioning requires a typed bubblewrap launcher")
    acquire = launcher.provision_mount.phase == "acquire"
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s)
            or not 0 < timeout_s <= sandbox.MAX_WORKSPACE_TIMEOUT_SECONDS):
        raise ValueError("invalid provisioning timeout")
    if type(storage_bound) is not int or storage_bound <= 0:
        raise ValueError("storage bound must be a positive integer")
    if not callable(storage_usage) or not callable(cancelled):
        raise ValueError("provisioning requires storage and cancellation checks")
    if type(source) is not str or type(args) is not list or any(type(x) is not str for x in args):
        raise ValueError("invalid trusted stage source or arguments")

    limits = sandbox.WorkspaceLimits()
    deadline = time.monotonic() + timeout_s
    process = None
    drains = []
    breaches: list[str] = []
    failure = None

    def check() -> str | None:
        try:
            if cancelled():
                return "cancelled"
        except Exception:
            return "cancel_check_failed"
        if time.monotonic() >= deadline:
            return "timeout"
        if breaches or sum(drain.total for drain in drains) > limits.max_output_bytes:
            return "output_limit"
        try:
            used = storage_usage()
        except Exception:
            return "storage_measurement_failed"
        if type(used) is not int or used < 0:
            return "storage_measurement_failed"
        if used > storage_bound:
            return "storage_limit"
        rss = 0
        for child in (process,):
            if child is None or child.poll() is not None:
                continue
            try:
                used = sandbox.PROCESS_TREE_RSS_READER(child.pid)
            except Exception:
                return "memory_measurement_failed"
            if type(used) is not int or used < 0:
                return "memory_measurement_failed"
            rss += used
        if rss > limits.rss_cap_bytes:
            return "memory_limit"
        return None

    try:
        failure = check()
        if failure is None:
            settings = {"timeout": timeout_s, "profile": limits.rlimit_profile(),
                        "rlimit_helper": sandbox._RLIMIT_HELPER}
            command = launcher.build_argv(_BOOTSTRAP, [json.dumps(settings), source, *args])
            if acquire:
                # Only the fixed registry relay enters the acquisition namespace.
                # The installer namespace receives no relay at all.
                boundary = command.index('--')
                command[boundary:boundary] = ['--ro-bind', '/registry.sock', '/registry.sock']
            process = subprocess.Popen(
                command,
                pass_fds=launcher.pass_fds, close_fds=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                env=launcher.env("/tmp"), cwd="/", start_new_session=True,
            )
            for stream, label in ((process.stdout, "provision-stdout"),
                                  (process.stderr, "provision-stderr")):
                drain = sandbox._BoundedDrain(stream, limits.max_output_bytes, label, breaches)
                drain.start()
                drains.append(drain)
            while process.poll() is None:
                failure = check()
                if failure:
                    break
                try:
                    process.wait(timeout=min(0.05, max(0, deadline - time.monotonic())))
                except subprocess.TimeoutExpired:
                    pass
    except (OSError, ValueError, RuntimeError):
        failure = "launch_or_supervision_failed"
    finally:
        # A termination failure propagates loudly; never return a normal failed
        # receipt that would let a caller publish/release a still-live sandbox.
        try:
            if process is not None:
                sandbox._terminate_child(process, launcher)
        finally:
            for drain in drains:
                drain.join(timeout=1)
            if any(drain.is_alive() for drain in drains):
                raise sandbox.SandboxTerminationError("provisioning output drain still alive")
            if process is not None:
                process.stdout.close()
                process.stderr.close()
            failure = failure or check()
            if failure is None and (process is None or process.returncode != 0):
                failure = "process_failed"
    # Keep at most ONE cumulative cap, not one full cap per stream. Untrusted
    # package logs must never be attached to a public receipt by the caller.
    stdout = drains[0].data if drains else b""
    remaining = max(0, limits.max_output_bytes - len(stdout))
    stderr = drains[1].data[:remaining] if len(drains) > 1 else b""
    return StageResult(failure, stdout, stderr)
