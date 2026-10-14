"""Owner-bound inherited channel, established by authenticated host admission.

There is deliberately no public bind-by-owner listener. The bootstrap must
verify admission, then hand serve_channel its grant and pinned daemon peer.
Neither this protocol nor its messages can allocate identities or grants.
"""

from __future__ import annotations

import array
import os
import socket
import struct
import threading

from tinyassets.boxes import provider as contract
from tinyassets.boxes.file_wire import MAX_MESSAGE, decode, encode
from tinyassets.boxes.handles import OwnerGrant
from tinyassets.boxes.owner_files import OwnerFileHost


def _receive(sock: socket.socket, size: int, peer: tuple[int, int, int]) -> bytes:
    parts = []
    while size:
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
        if not data:
            raise EOFError("owner channel closed")
        parts.append(data)
        size -= len(data)
    return b"".join(parts)


def receive(sock: socket.socket, peer: tuple[int, int, int]) -> dict:
    size = struct.unpack("!I", _receive(sock, 4, peer))[0]
    if size > MAX_MESSAGE:
        raise contract.BoxOperationRefused("owner channel message exceeds its bound")
    return decode(_receive(sock, size, peer))


def send(sock: socket.socket, document: dict) -> None:
    body = encode(document)
    sock.sendall(struct.pack("!I", len(body)) + body)


_ERRORS = {name: getattr(contract, name) for name in (
    "BoxError", "BoxAuthError", "BoxOperationRefused", "BoxBusy", "BoxNotFound",
    "BoxPathError", "WriteConflict", "OpIdReuse", "StaleHandle",
)}


def serve_channel(sock: socket.socket, *, peer: tuple[int, int, int],
                  grant: OwnerGrant, host: OwnerFileHost) -> None:
    """An authenticated bootstrap invokes this on an unnamed inherited socket."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    sock.settimeout(60)
    try:
        while True:
            request = receive(sock, peer)
            try:
                if set(request) == {"bind"}:
                    answer = {"result": host.bind(grant, request["bind"])}
                elif set(request) == {"token", "operation", "args", "op_id"}:
                    answer = host.request(grant, **request)
                else:
                    raise contract.BoxOperationRefused("invalid owner channel request")
            except contract.BoxError as error:
                answer = {"error": type(error).__name__}
            send(sock, answer)
    except (EOFError, OSError, ValueError):
        pass  # channel is closed, never rebound or automatically replayed
    finally:
        host.handles.close_session(grant.owner_id, grant.session_id)
        sock.close()


class FileChannel:
    """Client for one authenticated admission. Transport loss is always unknown."""

    def __init__(self, sock: socket.socket, *, host_peer: tuple[int, int, int]) -> None:
        self.sock = sock
        self.host_peer = host_peer
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        sock.settimeout(60)
        self._lock = threading.Lock()

    def request(self, document: dict) -> object:
        with self._lock:
            try:
                send(self.sock, document)
                response = receive(self.sock, self.host_peer)
            except (EOFError, OSError, ValueError) as error:
                self.sock.close()
                raise contract.BoxError(
                    "owner channel lost; operation outcome is unknown") from error
        if set(response) == {"error"}:
            raise _ERRORS.get(response["error"], contract.BoxError)("owner file operation failed")
        if set(response) != {"result"}:
            raise contract.BoxError("invalid owner file response")
        return response["result"]

    def close(self) -> None:
        self.sock.close()
