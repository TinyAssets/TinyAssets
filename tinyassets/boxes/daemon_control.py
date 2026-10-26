"""UID-authenticated calls to boxhostd's existing control socket."""
from __future__ import annotations

import os
import time
from contextvars import ContextVar
from pathlib import Path

from tinyassets.boxes.bootstrap import connect
from tinyassets.boxes.control_channel import receive, send

REQUEST_DRIVEN = ContextVar("boxhostd_request_driven", default=False)


def request(operation: str, owner: str | None = None, **fields) -> dict:
    root = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
    # Opaque volume commands include compression and two bounded transfers.
    # Keep the broker call alive through execution, then commit its result.
    volume_ops = {"create", "snapshot", "restore", "evict", "destroy",
                  "delete_snapshots"}
    # A requested wake may queue for 45 seconds before its 60 second startup.
    # The broker's own readiness socket allows 5 seconds, and that call is
    # what asks for pins. Keep the catalog read inside that budget.
    timeout = 5 if operation == "readiness" else (
        3 if operation == "pins" else (
            4 * 3600 if operation in volume_ops else 120))
    sock, identity = connect(root / "daemon.sock",
                             timeout=timeout)
    with sock:
        sock.settimeout(timeout)
        document = {"lifecycle": operation, **fields}
        if REQUEST_DRIVEN.get():
            document["_request_driven"] = True
        if owner is not None:
            document["owner"] = owner
        send(sock, document)
        result = receive(sock, identity)
    if set(result) == {"error"}:
        if result["error"] == "unadmitted":
            raise LookupError("owner has no live admission")
        raise RuntimeError("owner box lifecycle failed: " + result["error"])
    return result


def broker_bootstrap(timeout: float = 30.0):
    """Adopt the registered broker before starting the persistent router.

    The broker process and the daemon start together. boxhostd closes the
    bootstrap socket until ``BrokerRegistration.ready()`` finishes, which the
    client sees as ``EOFError``. Wait inside this call until that registration
    lands or ``timeout`` seconds have passed.
    """
    from tinyassets.boxes.bootstrap import RUNTIME
    from tinyassets.boxes.provider import BoxAuthError

    if os.geteuid() != 1001:
        raise BoxAuthError("only the router may bootstrap")
    deadline = time.monotonic() + timeout
    while True:
        try:
            sock, identity = connect(RUNTIME / "daemon.sock")
            with sock:
                send(sock, {"bootstrap": True})
                response = receive(sock, identity)
            if (set(response) != {"proof", "broker_socket"}
                    or response["broker_socket"] != str(RUNTIME / "broker/broker.sock")
                    or not isinstance(response["proof"], str) or len(response["proof"]) != 64):
                raise BoxAuthError("box broker bootstrap is unavailable")
            return response
        except (EOFError, OSError, BoxAuthError):
            if time.monotonic() >= deadline:
                raise
            time.sleep(0.2)
