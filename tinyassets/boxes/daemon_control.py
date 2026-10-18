"""UID-authenticated calls to boxhostd's existing control socket."""
from __future__ import annotations

import os
from pathlib import Path

from tinyassets.boxes.bootstrap import connect
from tinyassets.boxes.control_channel import receive, send


def request(operation: str, owner: str | None = None, **fields) -> dict:
    root = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
    sock, identity = connect(root / ("erasure.sock" if operation == "erasure" else "daemon.sock"))
    with sock:
        sock.settimeout(600 if operation == "roll" else 65)
        document = {"lifecycle": operation, **fields}
        if owner is not None:
            document["owner"] = owner
        send(sock, document)
        result = receive(sock, identity)
    if result.get("error") == "unadmitted":
        raise LookupError("owner has no live admission")
    if "error" in result:
        raise RuntimeError("owner box lifecycle failed: " + result["error"])
    return result


def ensure_awake(owner: str, *, scheduled: bool = False) -> int:
    result = request("ensure_awake", owner, scheduled=scheduled)
    uid = result.get("uid")
    if set(result) != {"uid"} or type(uid) is not int or not 300001 <= uid <= 399999:
        raise RuntimeError("invalid box wake response")
    return uid


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
