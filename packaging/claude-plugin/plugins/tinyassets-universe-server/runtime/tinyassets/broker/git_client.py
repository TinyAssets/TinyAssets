"""Binary git exchange on the existing authenticated owner broker channel."""
from __future__ import annotations

import os
import socket

from tinyassets import rpc_frames as rf
from tinyassets.broker.client import MAX_WINDOW, _raise_for
from tinyassets.broker.ops import new_op_id
from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome


def exchange(client, *, grant_id, connection_id, verb, request, upload, head, data):
    generation, token = client._fence()
    document = {"op": "OPEN", "op_id": new_op_id(), "generation": generation,
                "token": token, "principal": client._principal,
                "command_center": client._command_center, "grant_id": grant_id,
                "connection_id": connection_id, "verb": verb, "request": request,
                "credit": MAX_WINDOW}
    ended_upload = upload is None
    got_head = False
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(client._timeout)
        sock.connect(os.fspath(client._path))
        if client._verify_peer:
            client._verify_peer(sock)
        try:
            sock.sendall(rf.control(1, document))
            while True:
                frame = rf.read_frame_blocking(sock)
                if frame is None or frame.stream != 1:
                    raise rf.FrameError("git broker disconnected")
                if frame.kind == rf.DATA:
                    if not got_head:
                        raise rf.FrameError("git data before headers")
                    data(frame.payload)
                    sock.sendall(rf.control(1, {"op": "CREDIT", "n": len(frame.payload)}))
                    continue
                doc = frame.control()
                if doc["op"] == "UPLOAD_CREDIT":
                    if ended_upload:
                        raise rf.FrameError("unexpected upload credit")
                    chunk = upload.read(rf.MAX_DATA_FRAME)
                    if chunk:
                        sock.sendall(rf.data(1, chunk))
                    else:
                        ended_upload = True
                        sock.sendall(rf.control(1, {"op": "UPLOAD_END"}))
                elif doc["op"] == "HEAD":
                    if got_head or not ended_upload:
                        raise rf.FrameError("unexpected git headers")
                    got_head = True
                    head(doc)
                elif doc["op"] == "END":
                    if doc.get("outcome") != "completed":
                        _raise_for(doc)
                    if not got_head:
                        raise rf.FrameError("missing git headers")
                    return
                elif doc["op"] != "ADMITTED":
                    raise rf.FrameError("unexpected git frame")
        except (OSError, rf.FrameError):
            raise AmbiguousProxyOutcome(
                "git transport interrupted; inspect remote refs before retrying a push") from None
