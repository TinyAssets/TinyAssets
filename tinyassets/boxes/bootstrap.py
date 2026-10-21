"""Ephemeral UID-authenticated host bootstrap. No proof file or environment key."""

from __future__ import annotations

import os
import socket
import struct
from pathlib import Path

from tinyassets.boxes.control_channel import receive, send
from tinyassets.boxes.provider import BoxAuthError

RUNTIME = Path("/run/tinyassets-boxhostd")


def peer(sock: socket.socket) -> tuple[int, int, int]:
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def connect(path: Path, *, timeout=30) -> tuple[socket.socket, tuple[int, int, int]]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        sock.settimeout(timeout)
        sock.connect(str(path))
        identity = peer(sock)
        if identity[1:] != (0, 0):
            raise BoxAuthError("box bootstrap peer is not the root host")
        return sock, identity
    except BaseException:
        sock.close()
        raise


def register_broker(proof: str):
    if os.geteuid() != 1002:
        raise BoxAuthError("only the broker may register its ephemeral lease")
    sock, identity = connect(RUNTIME / "broker-registration.sock")
    send(sock, {"register": proof})
    if receive(sock, identity) != {"registered": True}:
        sock.close()
        raise BoxAuthError("broker registration refused")
    return sock, identity
