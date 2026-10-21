"""Credential-pinned framing for boxhostd lifecycle and broker registration."""

from __future__ import annotations

import array
import json
import os
import select
import socket
import struct

from tinyassets import rpc_frames as rf
from tinyassets.boxes import provider as contract

MAX_MESSAGE = 64 * 1024


def _receive(sock: socket.socket, size: int, peer: tuple[int, int, int],
             peer_fd: int | None = None) -> bytes:
    parts = []
    while size:
        if peer_fd is not None and select.select([peer_fd], [], [], 0)[0]:
            raise contract.BoxAuthError("owner channel peer has exited")
        data, ancillary, flags, _ = sock.recvmsg(
            min(size, 65536), socket.CMSG_SPACE(12) + socket.CMSG_SPACE(256),
            socket.MSG_CMSG_CLOEXEC,
        )
        credentials, invalid = [], False
        for level, kind, value in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                credentials.append(struct.unpack("3i", value))
            elif level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds = array.array("i")
                fds.frombytes(value[:len(value) - len(value) % fds.itemsize])
                for fd in fds:
                    os.close(fd)
                invalid = True
            else:
                invalid = True
        if invalid or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC) or credentials != [peer]:
            raise contract.BoxAuthError("owner channel credentials changed")
        if peer_fd is not None and select.select([peer_fd], [], [], 0)[0]:
            raise contract.BoxAuthError("owner channel peer has exited")
        if not data:
            raise EOFError("owner channel closed")
        parts.append(data)
        size -= len(data)
    return b"".join(parts)


def receive(sock: socket.socket, peer: tuple[int, int, int], peer_fd: int | None = None) -> dict:
    kind, stream, size = rf.decode_header(_receive(sock, rf.HEADER_BYTES, peer, peer_fd))
    if kind != rf.CONTROL or stream != rf.CONNECTION or size > MAX_MESSAGE:
        raise contract.BoxOperationRefused("owner channel message exceeds its bound")
    result = json.loads(_receive(sock, size, peer, peer_fd))
    if not isinstance(result, dict):
        raise contract.BoxOperationRefused("invalid box control document")
    if result.pop("op", None) != "BOX":
        raise contract.BoxOperationRefused("invalid box control frame")
    return result


def send(sock: socket.socket, document: dict) -> None:
    sock.sendall(rf.control(rf.CONNECTION, {**document, "op": "BOX"}))


