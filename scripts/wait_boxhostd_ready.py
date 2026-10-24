"""Wait for boxhostd's broker lifecycle work to drain before a deploy canary.

Run as root on the host with PYTHONPATH=/opt/tinyassets/boxhostd.

The cap outlasts one snapshot transfer. volumes.snapshot gives rclone 900s;
a shorter wait declared the deploy failed while that copy was still the
drain the gate is waiting for.
"""

from __future__ import annotations

import time

from tinyassets.boxes.daemon_control import request
from tinyassets.broker.lifecycle_control import request as broker_request

# rclone --timeout 900s, plus two readiness probes.
SNAPSHOT_TRANSFER_S = 900


def wait_ready(probe=request, *, broker_probe=broker_request,
               timeout=SNAPSHOT_TRANSFER_S + 120, interval=5, clock=time.monotonic,
               sleep=time.sleep, log=None):
    def ask(fn):
        try:
            return fn("readiness")
        except (EOFError, OSError, TimeoutError, RuntimeError) as exc:
            return {"ready": False, "error": type(exc).__name__}

    deadline = clock() + timeout
    stable = 0
    announced = None
    while clock() < deadline:
        host = ask(probe)
        broker = ask(broker_probe)
        ready = host == {"ready": True} and broker == {"ready": True}
        if not ready:
            report = {"host": host, "broker": broker}
            if report != announced and log is not None:
                log(report)
                announced = report
            stable = 0
        else:
            stable += 1
            if stable == 2:
                return True
        sleep(min(interval, max(0, deadline - clock())))
    return False


if __name__ == "__main__":
    if wait_ready(log=lambda result: print(
            "boxhostd lifecycle not ready:", result, flush=True)):
        print("boxhostd lifecycle ready")
    else:
        raise SystemExit("boxhostd lifecycle readiness timed out")
