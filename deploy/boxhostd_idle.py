"""P2a boxhostd: no socket, no owner work, one root-only preflight signal.

P2b replaces this idle entry point with the real box host. SIGUSR1 is used
only by the manual gate workflow; the service never accepts network traffic.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import tempfile
import threading
from pathlib import Path

RUN_DIR = Path("/run/tinyassets-boxhostd")
HARNESS = Path("/opt/tinyassets/boxhostd/box_preflight.py")
STEPS = frozenset(
    {
        "cleanup_guard",
        "cleanup_process",
        "sandbox_delete",
        "quota_state",
        "quota_clear",
        "quota_tree_remove",
        "quota_state_remove",
        "work_remove",
    }
)
REASONS = frozenset(
    {
        "invalid_work_dir",
        "invalid_state",
        "path_missing",
        "permission_denied",
        "timeout",
        "operation_failed",
        "runsc_delete_failed",
        "quota_clear_failed",
        "missing_tool:xfs_quota",
        "missing_tool:setquota",
    }
)


def cleanup_failure(output: str) -> str:
    try:
        detail = json.loads(output)
        step, reason = detail["step"], detail["reason"]
        if step in STEPS and reason in REASONS:
            return f"cleanup.{step}:{reason}"
    except (ValueError, KeyError, TypeError):
        pass
    return "cleanup.cleanup_process:operation_failed"


def run_preflight(result: Path) -> None:
    work = Path(tempfile.mkdtemp(prefix="box-preflight-", dir="/var/tmp"))
    env = {"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"}
    process = subprocess.Popen(
        ["/usr/bin/python3", str(HARNESS), "--result", str(result), "--work-dir", str(work)],
        env=env,
        start_new_session=True,
    )
    failure = None
    try:
        process.wait(timeout=2400)
    except subprocess.TimeoutExpired:
        failure = "watchdog:timeout"
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.wait()
    finally:
        try:
            cleanup = subprocess.run(
                ["/usr/bin/python3", str(HARNESS), "--cleanup-only", "--work-dir", str(work)],
                env=env,
                timeout=180,
                check=False,
                capture_output=True,
                text=True,
            )
            if cleanup.returncode:
                failure = cleanup_failure(cleanup.stdout)
        except subprocess.TimeoutExpired:
            failure = "cleanup.cleanup_process:timeout"
        except PermissionError:
            failure = "cleanup.cleanup_process:permission_denied"
        except OSError:
            failure = "cleanup.cleanup_process:operation_failed"
    if failure:
        result.write_text(json.dumps({"pass": False, "fatal": failure}) + "\n")


def prepare_delegation() -> None:
    """Leave the service cgroup empty so v2 controllers can be delegated."""
    relative = Path("/proc/self/cgroup").read_text().splitlines()[0].split(":", 2)[2]
    if not relative.endswith("/boxhostd.service"):
        raise RuntimeError("boxhostd must start in its systemd service cgroup")
    parent = Path("/sys/fs/cgroup") / relative.lstrip("/")
    control = parent / "control"
    control.mkdir(exist_ok=True)
    (control / "cgroup.procs").write_text(str(os.getpid()))
    (parent / "cgroup.subtree_control").write_text("+cpu +memory +pids")


def main() -> None:
    pending = threading.Event()

    def request(_signum: int, _frame: object) -> None:
        pending.set()

    signal.signal(signal.SIGUSR1, request)
    prepare_delegation()
    while True:
        pending.wait()
        pending.clear()
        result = RUN_DIR / "preflight.json"
        result.unlink(missing_ok=True)
        # The harness records failed gates itself. A crash gets a fail-closed
        # result from the workflow's missing-file check.
        run_preflight(result)


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("boxhostd P2a must run as root for delegated cgroups")
    main()
