"""P2a boxhostd: no socket, no owner work, one root-only preflight signal.

P2b replaces this idle entry point with the real box host. SIGUSR1 is used
only by the manual gate workflow; the service never accepts network traffic.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
from pathlib import Path

RUN_DIR = Path("/run/tinyassets-boxhostd")
HARNESS = Path("/opt/tinyassets/boxhostd/box_preflight.py")


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
        subprocess.run(
            ["/usr/bin/python3", str(HARNESS), "--result", str(result)],
            check=False,
            timeout=2400,
            env={"PATH": "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"},
        )


if __name__ == "__main__":
    if os.geteuid() != 0:
        raise SystemExit("boxhostd P2a must run as root for delegated cgroups")
    main()
