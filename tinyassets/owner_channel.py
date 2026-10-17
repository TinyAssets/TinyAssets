"""The admitted relay transport. No owner identity or fence comes from the box."""
from __future__ import annotations

import os
import weakref
from dataclasses import dataclass
from pathlib import Path

from tinyassets import rpc_frames as rf

_leases = weakref.WeakSet()


def _after_fork():
    # Close the child's descriptor without SHUT_RDWR on the parent's socket.
    for lease in list(_leases):
        lease.sock.close()
    _leases.clear()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)


@dataclass(frozen=True)
class OwnerChannel:
    socket_path: Path

    def fence(self):
        # The broker replaces these compatibility fields with its current fence.
        return 0, ""

    def verify_broker(self, sock):
        # The relay was admitted at process startup and authenticates the broker.
        pass


def connect(sock, center):
    from tinyassets.owner_runtime import broker_egress

    sock.connect(str(broker_egress().socket_path))
    sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CONNECT", "center": center}
                           if center else {"op": "BOX_SHARED"}))
    answer = rf.read_frame_blocking(sock)
    if (answer is None or answer.kind != rf.CONTROL or answer.stream != rf.CONNECTION
            or answer.control() != {"op": "BOX_CONNECTED"}):
        raise PermissionError("owner broker channel refused")


def request(center, document):
    import socket

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(30)
        connect(sock, center)
        sock.sendall(rf.control(rf.CONNECTION, document))
        answer = rf.read_frame_blocking(sock)
    if (answer is not None and answer.kind == rf.CONTROL and answer.stream == rf.CONNECTION
            and answer.control() == {"op": "BOX_PLAN_MISSING"}):
        from tinyassets.broker.owner_reads import PlanMissing

        raise PlanMissing("bound owner has no shared plan")
    if (answer is None or answer.kind != rf.CONTROL or answer.stream != rf.CONNECTION
            or answer.control().get("op") != "BOX_RESULT"):
        raise PermissionError("owner broker operation refused")
    return answer.control()["result"]


class UsageLease:
    """A parent lives while its broker channel lives, including across threads."""

    def __init__(self, center, usage_id):
        import socket
        import threading

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(15)
        self.stop = threading.Event()
        self.pid = os.getpid()
        try:
            connect(self.sock, center)
            self.sock.sendall(rf.control(rf.CONNECTION, {
                "op": "USAGE_LEASE", "usage_id": usage_id}))
            frame = rf.read_frame_blocking(self.sock)
            if frame is None or frame.control().get("op") != "BOX_RESULT":
                raise PermissionError("owner model usage lease refused")
            result = frame.control()["result"]
            self.owner_token, self.token = result["owner_token"], result["parent_token"]
        except BaseException:
            self.sock.close()
            raise
        self.thread = threading.Thread(target=self._heartbeat, daemon=True)
        _leases.add(self)
        self.thread.start()

    def _heartbeat(self):
        try:
            while not self.stop.wait(10):
                self.sock.sendall(rf.control(rf.CONNECTION, {"op": "BOX_PING"}))
                frame = rf.read_frame_blocking(self.sock)
                if frame is None or frame.control() != {"op": "BOX_PONG"}:
                    return
        except (OSError, rf.FrameError):
            pass
        finally:
            self.sock.close()

    def close(self):
        import socket

        if os.getpid() != self.pid:
            self.sock.close()
            return
        self.stop.set()
        try:
            self.sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        self.thread.join(16)
        self.sock.close()
