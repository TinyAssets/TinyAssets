"""Ephemeral UID-authenticated host bootstrap. No proof file or environment key."""

from __future__ import annotations

import os
import socket
import struct
from pathlib import Path

from tinyassets.boxes.file_channel import receive, send
from tinyassets.boxes.provider import BoxAuthError

RUNTIME = Path("/run/tinyassets-boxhostd")


def peer(sock: socket.socket) -> tuple[int, int, int]:
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def connect(path: Path) -> tuple[socket.socket, tuple[int, int, int]]:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        sock.settimeout(30)
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


def daemon_bootstrap() -> dict:
    if os.geteuid() != 1001:
        raise BoxAuthError("only the retired daemon may bootstrap")
    sock, identity = connect(RUNTIME / "daemon.sock")
    with sock:
        send(sock, {"bootstrap": True})
        response = receive(sock, identity)
    if (set(response) != {"proof", "broker_socket"}
            or response["broker_socket"] != str(RUNTIME / "broker/broker.sock")
            or not isinstance(response["proof"], str) or len(response["proof"]) < 32):
        raise BoxAuthError("box broker bootstrap is unavailable")
    return response


def channel_factory(owner, center, session, *, destroying=False):
    from tinyassets.boxes.file_channel import FileChannel

    sock, identity = connect(RUNTIME / "daemon.sock")
    try:
        send(sock, {"admit": {"owner": owner, "center": center, "session": session,
                              "destroying": destroying}})
        if receive(sock, identity) != {"admitted": True}:
            raise BoxAuthError("owner box admission refused")
        return FileChannel(sock, host_peer=identity)
    except BaseException:
        sock.close()
        raise
