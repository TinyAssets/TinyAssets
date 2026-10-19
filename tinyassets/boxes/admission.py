"""Broker-derived persistent owner lifecycle reservations, with no owner file access."""

from __future__ import annotations

import socket
from pathlib import Path

from tinyassets import rpc_frames as rf
from tinyassets.boxes.provider import BoxAuthError
from tinyassets.broker.owner_identities import validate_center, validate_principal


class BoxSuspended(BoxAuthError):
    """Broker explicitly suspended this owner's ordinary lifecycle admission."""


class OwnerRegistry:
    def __init__(self, data_root: Path, broker_socket: Path) -> None:
        self.data_root, self.broker_socket = data_root, broker_socket

    def pending_volumes(self) -> list[str]:
        return self._volume_request({"op": "BOX_VOLUME_PENDING"})

    def volume(self, owner: str, action: str = "binding", **arguments) -> dict | list:
        """C2 metadata mutations remain in the broker's admission authority."""
        validate_principal(owner)
        return self._volume_request({"op": "BOX_VOLUME", "principal": owner,
                                     "action": action, "arguments": arguments})

    def _volume_request(self, document):
        from tinyassets.boxes.bootstrap import peer

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(30)
            sock.connect(str(self.broker_socket))
            if peer(sock)[1:] != (1002, 1002):
                raise BoxAuthError("lifecycle peer is not the credential broker")
            sock.sendall(rf.control(rf.CONNECTION, document))
            frame = rf.read_frame_blocking(sock)
        if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
            raise BoxAuthError("invalid volume lifecycle response")
        result = frame.control()
        if set(result) != {"op", "result"} or result["op"] != "BOX_VOLUME_RESULT":
            raise BoxAuthError("broker refused volume lifecycle")
        return result["result"]

    def erase_admission(self, owner: str) -> None:
        from tinyassets.boxes.bootstrap import peer

        validate_principal(owner)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(self.broker_socket))
            if peer(sock)[1:] != (1002, 1002):
                raise BoxAuthError("lifecycle peer is not the credential broker")
            sock.sendall(rf.control(rf.CONNECTION, {
                "op": "BOX_ADMISSION_ERASE", "principal": owner}))
            frame = rf.read_frame_blocking(sock)
        if (frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION
                or frame.control() != {"op": "OWNER_ADMISSION_ERASED"}):
            raise BoxAuthError("broker refused admission erasure")

    def retire(self, owner: str, uid: int) -> None:
        """Root cleanup retires broker admission without recursively destroying."""
        from tinyassets.boxes.bootstrap import peer

        validate_principal(owner)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(self.broker_socket))
            if peer(sock)[1:] != (1002, 1002):
                raise BoxAuthError("lifecycle peer is not the credential broker")
            sock.sendall(rf.control(rf.CONNECTION, {
                "op": "BOX_RETIRE_ADMISSION", "principal": owner, "uid": uid}))
            frame = rf.read_frame_blocking(sock)
        if (frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION
                or frame.control() != {"op": "BOX_ADMISSION_RETIRED"}):
            raise BoxAuthError("broker refused admission retirement")

    def owner(self, owner: str, *, rotate: bool = False) -> dict:
        validate_principal(owner)
        from tinyassets.boxes.bootstrap import peer

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(10)
            sock.connect(str(self.broker_socket))
            if peer(sock)[1:] != (1002, 1002):
                raise BoxAuthError("lifecycle peer is not the credential broker")
            doc = {"op": "BOX_LIFECYCLE", "principal": owner, "rotate": rotate}
            sock.sendall(rf.control(rf.CONNECTION, doc))
            frame = rf.read_frame_blocking(sock)
        if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
            raise BoxAuthError("invalid lifecycle response")
        result = frame.control()
        if result == {"op": "BOX_UNADMITTED"}:
            raise LookupError("owner has no live admission")
        if result == {"op": "BOX_SUSPENDED"}:
            raise BoxSuspended("suspended")
        expected = {"op", "uid", "quota", "centers", "volume_generation", "snapshot_id",
                    "snapshot_sha256"} | ({"secret"} if rotate else set())
        if (set(result) != expected or result["op"] != "BOX_LIFECYCLE_IS"
                or type(result["uid"]) is not int or not 300001 <= result["uid"] <= 399999
                or type(result["quota"]) is not int or result["quota"] <= 0
                or type(result["volume_generation"]) is not int or result["volume_generation"] < 1
                or not isinstance(result["centers"], list)):
            raise BoxAuthError("broker refused owner lifecycle")
        for center in result["centers"]:
            validate_center(center)
        if rotate:
            import re

            if not isinstance(result["secret"], str) or not re.fullmatch(
                    r"[A-Za-z0-9_-]{43}", result["secret"]):
                raise BoxAuthError("invalid owner secret")
        return result
