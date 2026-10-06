"""DA2: the bounded mapper's inherited, read-only channel to the broker.

The D70 window creates this unnamed SOCK_SEQPACKET pair before the broker
fork; PID1 delivers the mapper PID with the proof hash, so the broker serves
nothing here before it knows its one peer. Every packet must carry
SCM_CREDENTIALS for exactly that PID (pinned by a pidfd) as host
300000:300000. Three read-only ops; no allocation, write, path or numeric
identity from the daemon. Anything else refuses and closes the channel.
"""
from __future__ import annotations

import array
import json
import os
import select
import socket
import struct
import threading
from typing import Any

MAPPER_HOST_ID = 300000
MAX_PACKET = 4096
_FIELDS = {"OWNER_MACHINE": {"op", "principal"}, "CENTER_STATE": {"op", "center"},
           "ADMISSION_ROW": {"op", "generation"}}


class MapperChannelClosed(RuntimeError):
    """The channel refused a packet and is permanently closed."""


class MapperChannel:
    def __init__(self, channel: socket.socket, mapper_pid: int, identities: Any) -> None:
        if (channel.family != socket.AF_UNIX or channel.type != socket.SOCK_SEQPACKET
                or channel.getsockname() or channel.getpeername()
                or type(mapper_pid) is not int or mapper_pid <= 1):
            raise PermissionError("invalid mapper channel bootstrap")
        self._channel = channel
        self._pid = mapper_pid
        self._pidfd = os.pidfd_open(mapper_pid)
        self._identities = identities
        channel.set_inheritable(False)
        channel.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)

    def _alive(self) -> bool:
        return not select.select([self._pidfd], [], [], 0)[0]

    def serve_one(self) -> bool:
        """Answer one packet; False once the channel is closed (EOF or refusal)."""
        packet, ancillary, flags, _ = self._channel.recvmsg(
            MAX_PACKET, socket.CMSG_SPACE(12) + socket.CMSG_SPACE(32), socket.MSG_CMSG_CLOEXEC)
        received, credentials, unknown = [], [], False
        for level, kind, payload in ancillary:
            if level == socket.SOL_SOCKET and kind == socket.SCM_RIGHTS:
                fds = array.array("i")
                fds.frombytes(payload[:len(payload) - len(payload) % fds.itemsize])
                received.extend(fds)
            elif level == socket.SOL_SOCKET and kind == socket.SCM_CREDENTIALS:
                credentials.append(struct.unpack("3i", payload))
            else:
                unknown = True
        for fd in received:
            os.close(fd)
        if not packet and not ancillary:
            self.close()
            return False
        try:
            if (received or unknown or flags & (socket.MSG_TRUNC | socket.MSG_CTRUNC)
                    or credentials != [(self._pid, MAPPER_HOST_ID, MAPPER_HOST_ID)]
                    or not self._alive()):
                raise PermissionError("unauthenticated mapper packet")
            answer = self.answer(json.loads(packet))
        except Exception:  # noqa: BLE001 - any refusal closes; no state was touched
            try:
                self._channel.sendall(b'{"op":"REFUSED"}')
            except OSError:
                pass
            self.close()
            return False
        self._channel.sendall(json.dumps(answer).encode())
        return True

    def answer(self, request: Any) -> dict[str, Any]:
        if (not isinstance(request, dict) or request.get("op") not in _FIELDS
                or set(request) != _FIELDS[request["op"]] or self._identities is None):
            raise ValueError("unsupported mapper operation")
        op = request["op"]
        if op == "OWNER_MACHINE":
            machine = self._identities.owner_machine(request["principal"])
            return {"op": "ABSENT"} if machine is None else {"op": "OWNER_MACHINE_IS",
                                                              "machine": machine}
        if op == "CENTER_STATE":
            return {"op": "CENTER_STATE_IS",
                    "state": self._identities.center_state(request["center"])}
        if type(request["generation"]) is not int:
            raise ValueError("invalid admission generation")
        row = self._identities.admission_row(request["generation"])
        if row is None:
            return {"op": "ABSENT"}
        return {"op": "ADMISSION_ROW_IS", "generation": row.generation, "event": row.event,
                "principal": row.principal, "center": row.center, "machine": row.machine}

    def close(self) -> None:
        self._channel.close()
        if self._pidfd >= 0:
            os.close(self._pidfd)
            self._pidfd = -1

    def serve_forever(self) -> None:
        try:
            while self.serve_one():
                pass
        except OSError:
            self.close()


def start(fd: int, mapper_pid: int, identities: Any) -> threading.Thread:
    channel = MapperChannel(socket.socket(fileno=fd), mapper_pid, identities)
    thread = threading.Thread(target=channel.serve_forever, name="mapper-channel", daemon=True)
    thread.start()
    return thread
