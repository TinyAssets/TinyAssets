"""Root operator commands enter the broker, never the host execution socket."""
import os
import socket
from pathlib import Path

from tinyassets import rpc_frames as rf
from tinyassets.boxes.bootstrap import peer


def request(operation, **arguments):
    root = Path(os.environ.get("TINYASSETS_BOXES_RUN", "/run/tinyassets-boxhostd"))
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(600)
        sock.connect(str(root / "broker/broker.sock"))
        if peer(sock)[1:] != (1002, 1002):
            raise PermissionError("lifecycle peer is not the broker")
        sock.sendall(rf.control(rf.CONNECTION, {
            "op": "LIFECYCLE_ADMIN", "operation": operation, "arguments": arguments}))
        frame = rf.read_frame_blocking(sock)
    if (frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION
            or frame.control().get("op") != "LIFECYCLE_RESULT"):
        raise RuntimeError("broker lifecycle command failed")
    return frame.control()["result"]
