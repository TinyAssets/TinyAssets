"""Wait for boxhostd's broker lifecycle work to drain before a deploy canary.

Run as root on the host with PYTHONPATH=/opt/tinyassets/boxhostd.
"""

from __future__ import annotations

import time

from tinyassets.boxes.daemon_control import request


def wait_ready(probe=request, *, timeout=540, interval=5, clock=time.monotonic,
               sleep=time.sleep):
    deadline = clock() + timeout
    stable = 0
    while clock() < deadline:
        try:
            result = probe("readiness")
        except (OSError, TimeoutError, RuntimeError):
            result = {"ready": False}
        if result == {"ready": True}:
            stable += 1
            if stable == 2:
                return True
        else:
            stable = 0
        sleep(min(interval, max(0, deadline - clock())))
    return False


if __name__ == "__main__":
    if wait_ready():
        print("boxhostd lifecycle ready")
    else:
        raise SystemExit("boxhostd lifecycle readiness timed out")
