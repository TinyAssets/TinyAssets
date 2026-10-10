"""Daemon-side broker adoption. The PID1 bootstrap owns its lifecycle.

The bootstrap forks the broker before it retires and hands the daemon the
lease proof in memory; the fence stays in this process and no owner-channel
file exists. There is no other way to reach the broker.
"""
from __future__ import annotations

import ctypes
import os
import select
import socket
import struct
import threading
from pathlib import Path

from tinyassets import rpc_frames as rf

_START_TIMEOUT_S = 35.0
_registry: dict[Path, BrokerSupervisor] = {}
_registry_lock = threading.RLock()


class BrokerUidSplitRequired(RuntimeError):
    """The daemon cannot reach the bootstrapped broker over its authenticated boundary."""


def _peer(sock: socket.socket) -> tuple[int, int, int]:
    return struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))


def _protect_daemon() -> None:
    """After exec, before creating a proof, check retirement and deny ptrace."""
    if not hasattr(socket, "SO_PEERCRED") or os.getuid() != 1001:
        raise BrokerUidSplitRequired("credential broker needs the per-role uid split")
    from tinyassets import workspace_fs

    # Numeric self PID avoids /proc/self's symlink; retain the common bounded,
    # no-follow descriptor reader even for this kernel-owned status document.
    directory = workspace_fs.open_dir_nofollow(Path('/proc') / str(os.getpid()))
    try:
        raw = workspace_fs.read_regular_file_beneath(directory, 'status', max_bytes=65536)
    finally:
        os.close(directory)
    fields = dict(line.split(':', 1) for line in raw.decode().splitlines())
    if (os.getresuid() != (1001, 1001, 1001) or os.getresgid() != (1001, 1001, 1001)
            or os.getgroups() != [1100, 1101, 1102]
            or any(int(fields[key], 16) for key in
                   ("CapInh", "CapPrm", "CapEff", "CapBnd", "CapAmb"))
            or int(fields["NoNewPrivs"]) != 1):
        raise BrokerUidSplitRequired("credential broker needs the per-role uid split")
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0 or libc.prctl(3, 0, 0, 0, 0) != 0:
        raise BrokerUidSplitRequired("daemon non-dumpability failed")


class BrokerSupervisor:
    def __init__(self, data_root: Path, *, broker_pid: int, socket_path: Path,
                 proof: str) -> None:
        _protect_daemon()
        if (type(broker_pid) is not int or broker_pid <= 0
                or not isinstance(proof, str) or len(proof) < 32):
            raise BrokerUidSplitRequired("invalid broker bootstrap")
        self._root = Path(data_root).resolve()
        self._pid = os.getpid()
        self._proof = proof
        self._pair: tuple[int, str] | None = None
        self._socket = Path(socket_path)
        self._bootstrap_pid = broker_pid
        self._bootstrap_pidfd = os.pidfd_open(broker_pid)

    @classmethod
    def from_bootstrap(cls, data_root: Path, *, broker_pid: int,
                       socket_path: Path, proof: str) -> BrokerSupervisor:
        """Adopt the broker forked before host authority was retired.

        Startup-only memory handoff, never discovered from an environment value.
        This instance cannot restart a dead broker.
        """
        return cls(data_root, broker_pid=broker_pid, socket_path=socket_path, proof=proof)

    @property
    def socket_path(self) -> Path:
        return self._socket

    def _same_process(self) -> None:
        if os.getpid() != self._pid:
            raise BrokerUidSplitRequired("broker owner state cannot be inherited by a child")
        if select.select([self._bootstrap_pidfd], [], [], 0)[0]:
            raise BrokerUidSplitRequired("bootstrapped broker exited; container restart required")

    def _fence(self) -> None:
        self._same_process()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(_START_TIMEOUT_S)
            sock.connect(str(self._socket))
            self.verify_broker(sock)
            sock.sendall(rf.control(rf.CONNECTION, {"op": "FENCE", "proof": self._proof}))
            answer = rf.read_frame_blocking(sock)
        document = answer.control() if answer is not None else {}
        generation, token = document.get("generation"), document.get("token")
        if (document.get("op") != "FENCE_ACK" or type(generation) is not int
                or generation < 1 or not isinstance(token, str) or not token):
            raise BrokerUidSplitRequired("the broker refused the owner's fence")
        self._pair = generation, token

    def verify_broker(self, sock: socket.socket) -> None:
        self._same_process()
        peer = _peer(sock)
        if peer != (self._bootstrap_pid, 1002, 1002):
            raise BrokerUidSplitRequired("broker peer does not have the broker identity")

    def fence(self) -> tuple[int, str]:
        self._same_process()
        if self._pair is None:
            from tinyassets.storage.outbound_connections import ProxyRequestError

            raise ProxyRequestError("credential broker is not running")
        return self._pair

    def start(self) -> None:
        self._same_process()
        with _registry_lock:
            if get_supervisor(self._root) is self:
                return
            if _registry:
                raise BrokerUidSplitRequired("this daemon already holds a broker acquisition")
            try:
                # Startup already proved listening readiness. Fence only this
                # exact live process; nothing here can start another one.
                self._fence()
            except (OSError, rf.FrameError) as exc:
                raise BrokerUidSplitRequired(
                    "credential broker needs the per-role uid split") from exc
            _registry[self._root] = self


def get_supervisor(data_root: Path) -> BrokerSupervisor | None:
    with _registry_lock:
        result = _registry.get(Path(data_root).resolve())
        if result is not None:
            result._same_process()
        return result
