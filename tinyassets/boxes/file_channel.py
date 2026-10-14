"""Owner-bound channel, established by UID-authenticated host admission.

There is no unauthenticated bind-by-owner listener. The bootstrap must verify
admission, then hand serve_channel its grant and pinned daemon peer.
Neither this protocol nor its messages can allocate identities or grants.
"""

from __future__ import annotations

import array
import json
import os
import select
import socket
import struct
import threading

from tinyassets import rpc_frames as rf
from tinyassets.boxes import provider as contract
from tinyassets.boxes.file_wire import MAX_MESSAGE, decode, encode
from tinyassets.boxes.handles import OwnerGrant
from tinyassets.boxes.owner_files import OwnerFileHost


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
    result = decode(_receive(sock, size, peer, peer_fd))
    if result.pop("op", None) != "BOX":
        raise contract.BoxOperationRefused("invalid box control frame")
    return result


def send(sock: socket.socket, document: dict) -> None:
    sock.sendall(rf.control(rf.CONNECTION, {**json.loads(encode(document)), "op": "BOX"}))


_ERRORS = {name: getattr(contract, name) for name in (
    "BoxError", "BoxAuthError", "BoxOperationRefused", "BoxBusy", "BoxNotFound",
    "BoxPathError", "WriteConflict", "OpIdReuse", "StaleHandle",
)}


def serve_channel(sock: socket.socket, *, peer: tuple[int, int, int],
                  grant: OwnerGrant, host: OwnerFileHost) -> None:
    """An authenticated bootstrap invokes this on an unnamed inherited socket."""
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
    sock.settimeout(60)
    peer_fd = -1
    try:
        peer_fd = os.pidfd_open(peer[0])
        while True:
            request = receive(sock, peer, peer_fd)
            try:
                if set(request) == {"bind"}:
                    answer = {"result": host.bind(grant, request["bind"])}
                elif set(request) == {"bind_for_destroy"}:
                    answer = {"result": host.bind(grant, request["bind_for_destroy"],
                                                   destroying=True)}
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
        host.close_session(grant)
        sock.close()
        if peer_fd >= 0:
            os.close(peer_fd)


class FileChannel:
    """Client for one authenticated admission. Transport loss is always unknown."""

    def __init__(self, sock: socket.socket, *, host_peer: tuple[int, int, int]) -> None:
        self.sock = sock
        self.host_peer = host_peer
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
        sock.settimeout(60)
        self._lock = threading.RLock()
        try:
            # A host process is PID 0 from a container PID namespace. Its root
            # credential is still kernel supplied on every frame; guest owners
            # cannot acquire it. In one namespace pin lifetime as well.
            if host_peer[0] == 0 and host_peer[1:] != (0, 0):
                raise contract.BoxAuthError("invisible peer must be the root host")
            self._peer_fd = os.pidfd_open(host_peer[0]) if host_peer[0] else None
        except BaseException:
            sock.close()
            raise

    def request(self, document: dict) -> object:
        with self._lock:
            try:
                send(self.sock, document)
                response = receive(self.sock, self.host_peer, self._peer_fd)
            except (EOFError, OSError, ValueError) as error:
                self.close()
                raise contract.BoxError(
                    "owner channel lost; operation outcome is unknown") from error
        if set(response) == {"error"}:
            raise _ERRORS.get(response["error"], contract.BoxError)("owner file operation failed")
        if set(response) != {"result"}:
            raise contract.BoxError("invalid owner file response")
        return response["result"]

    def close(self) -> None:
        with self._lock:
            self.sock.close()
            if self._peer_fd is not None and self._peer_fd >= 0:
                os.close(self._peer_fd)
                self._peer_fd = -1
