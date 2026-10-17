"""UID-authenticated calls to boxhostd's existing control socket."""
from __future__ import annotations

import os
from pathlib import Path

from tinyassets.boxes.bootstrap import connect
from tinyassets.boxes.file_channel import receive, send


def request(operation: str, owner: str, **fields) -> dict:
    root = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
    sock, identity = connect(root / "daemon.sock")
    with sock:
        sock.settimeout(65)
        send(sock, {"lifecycle": operation, "owner": owner, **fields})
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
