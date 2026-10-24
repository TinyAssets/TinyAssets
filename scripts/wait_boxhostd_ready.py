"""Wait until boxhostd and the broker both report exactly {"ready": True}.

Run as root on the host with PYTHONPATH=/opt/tinyassets/boxhostd.

The host returns that document only when the broker registration is held and
no background copy is running or queued. A request wake is not that copy.
The broker returns it only when admission is open, every lifecycle task has
finished, and pending() is empty. pending() ignores kind=create and
kind=running. It still counts snapshot, restore, evict, and erase, plus
excess snapshots and a cold local volume.

volumes.snapshot gives rclone --timeout 900s twice: the upload and the
readback. The cap outlasts that publish. A shorter wait declared the deploy
failed while the copy the gate is waiting for was still running, and the
probe used to discard the answer that said which side was false.
"""

from __future__ import annotations

import time

from tinyassets.boxes.daemon_control import request
from tinyassets.broker.lifecycle_control import request as broker_request

# One rclone --timeout. A publish uploads, then reads the object back.
SNAPSHOT_TRANSFER_S = 900
PUBLISH_TRANSFERS = 2
WAIT_TIMEOUT_S = PUBLISH_TRANSFERS * SNAPSHOT_TRANSFER_S + 120


def wait_ready(probe=request, *, broker_probe=broker_request,
               timeout=WAIT_TIMEOUT_S, interval=5, clock=time.monotonic,
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
