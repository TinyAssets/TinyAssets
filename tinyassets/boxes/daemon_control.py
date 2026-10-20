"""UID-authenticated calls to boxhostd's existing control socket."""
from __future__ import annotations

import os
from pathlib import Path

from tinyassets.boxes.bootstrap import connect
from tinyassets.boxes.control_channel import receive, send


def request(operation: str, owner: str | None = None, **fields) -> dict:
    root = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
    # Opaque volume commands include compression and two bounded transfers.
    # Keep the broker call alive through execution, then commit its result.
    volume_ops = {"create", "snapshot", "restore", "evict", "destroy",
                  "delete_snapshot", "delete_snapshots"}
    timeout = 4 * 3600 if operation in volume_ops else 65
    sock, identity = connect(root / "daemon.sock",
                             timeout=timeout)
    with sock:
        sock.settimeout(timeout)
        document = {"lifecycle": operation, **fields}
        if owner is not None:
            document["owner"] = owner
        send(sock, document)
        result = receive(sock, identity)
    if set(result) == {"error"}:
        if result["error"] == "unadmitted":
            raise LookupError("owner has no live admission")
        raise RuntimeError("owner box lifecycle failed: " + result["error"])
    return result


def broker_bootstrap():
    """Adopt the registered broker before starting the persistent router."""
    from tinyassets.boxes.bootstrap import RUNTIME
    from tinyassets.boxes.provider import BoxAuthError

    if os.geteuid() != 1001:
        raise BoxAuthError("only the router may bootstrap")
    sock, identity = connect(RUNTIME / "daemon.sock")
    with sock:
        send(sock, {"bootstrap": True})
        response = receive(sock, identity)
    if (set(response) != {"proof", "broker_socket"}
            or response["broker_socket"] != str(RUNTIME / "broker/broker.sock")
            or not isinstance(response["proof"], str) or len(response["proof"]) != 64):
        raise BoxAuthError("box broker bootstrap is unavailable")
    return response
