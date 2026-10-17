"""Broker-derived persistent owner lifecycle reservations, with no owner file access."""

from __future__ import annotations

import socket
from pathlib import Path

from tinyassets import rpc_frames as rf
from tinyassets.boxes.provider import BoxAuthError
from tinyassets.broker.owner_identities import validate_center, validate_principal


class OwnerRegistry:
    def __init__(self, data_root: Path, broker_socket: Path) -> None:
        self.data_root, self.broker_socket = data_root, broker_socket

    def owner(self, owner: str, *, rotate: bool = False) -> dict:
        validate_principal(owner)
        from tinyassets.boxes.bootstrap import peer

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(self.broker_socket))
            if peer(sock)[1:] != (1002, 1002):
                raise BoxAuthError("lifecycle peer is not the credential broker")
            sock.sendall(rf.control(rf.CONNECTION, {
                "op": "BOX_LIFECYCLE", "principal": owner, "rotate": rotate}))
            frame = rf.read_frame_blocking(sock)
        if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
            raise BoxAuthError("invalid lifecycle response")
        result = frame.control()
        if result == {"op": "BOX_UNADMITTED"}:
            raise LookupError("owner has no live admission")
        expected = {"op", "uid", "quota", "centers"} | ({"secret"} if rotate else set())
        if (set(result) != expected or result["op"] != "BOX_LIFECYCLE_IS"
                or type(result["uid"]) is not int or not 300001 <= result["uid"] <= 399999
                or type(result["quota"]) is not int or result["quota"] <= 0
                or not isinstance(result["centers"], list) or not result["centers"]):
            raise BoxAuthError("broker refused owner lifecycle")
        for center in result["centers"]:
            validate_center(center)
        if rotate:
            import re

            if not isinstance(result["secret"], str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{43}", result["secret"]):
                raise BoxAuthError("invalid owner secret")
        return result
