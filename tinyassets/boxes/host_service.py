"""Root box host, outside the daemon: authenticated admission and owner runtime."""

from __future__ import annotations

import os
import select
import signal
import socket
import sqlite3
import stat
import threading
from pathlib import Path

from tinyassets.boxes.admission import OwnerRegistry
from tinyassets.boxes.bootstrap import RUNTIME, peer
from tinyassets.boxes.file_channel import receive, send, serve_channel
from tinyassets.boxes.gvisor_files import GvisorFileBackend
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.owner_files import OwnerFileHost
from tinyassets.boxes.provider import BoxError
from tinyassets.boxes.quota import OwnerQuota
from tinyassets.boxes.relay import OwnerRelays


class HostService:
    def __init__(self, host: OwnerFileHost, admission: OwnerRegistry, quota: OwnerQuota,
                 relays: OwnerRelays) -> None:
        self.host, self.admission, self.quota, self.relays = host, admission, quota, relays
        self._broker_lock = threading.Lock()
        self._broker = None
        self._slots = threading.BoundedSemaphore(128)

    def register(self, sock):
        identity = peer(sock)
        pidfd = None
        try:
            if identity[1:] != (1002, 1002):
                raise BoxError("broker registration identity refused")
            pidfd = os.pidfd_open(identity[0])
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            sock.settimeout(30)
            document = receive(sock, identity, pidfd)
            proof = document.get("register")
            if set(document) != {"register"} or not isinstance(proof, str) or len(proof) != 64:
                raise BoxError("invalid broker registration")
            with self._broker_lock:
                if self._broker is not None and not select.select([self._broker[0]], [], [], 0)[0]:
                    raise BoxError("a live broker is already registered")
                self._broker = (pidfd, proof, False)
            send(sock, {"registered": True})
            if receive(sock, identity, pidfd) != {"ready": True}:
                raise BoxError("broker did not become ready")
            with self._broker_lock:
                if (self._broker is None or self._broker[0] != pidfd
                        or select.select([pidfd], [], [], 0)[0]):
                    raise BoxError("broker registration expired before readiness")
                self._broker = (pidfd, proof, True)
            send(sock, {"ready": True})
            sock.settimeout(None)
            while not select.select([pidfd], [], [], 0)[0]:
                ready, _, _ = select.select([sock, pidfd], [], [], 1)
                if sock in ready and not sock.recv(1):
                    break
        except (EOFError, OSError, ValueError):
            pass
        finally:
            with self._broker_lock:
                if self._broker is not None and self._broker[0] == pidfd:
                    self._broker = None
            if pidfd is not None:
                os.close(pidfd)
            sock.close()

    def daemon(self, sock):
        identity = peer(sock)
        pidfd = None
        try:
            if identity[1:] != (1001, 1001):
                raise BoxError("daemon channel identity refused")
            pidfd = os.pidfd_open(identity[0])
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            sock.settimeout(30)
            document = receive(sock, identity, pidfd)
            if document == {"bootstrap": True}:
                with self._broker_lock:
                    broker = self._broker
                    if broker is None or not broker[2] or select.select([broker[0]], [], [], 0)[0]:
                        raise BoxError("credential broker is not ready")
                    send(sock, {"proof": broker[1], "broker_socket":
                                str(self.admission.broker_socket)})
                return
            if set(document) != {"admit"} or not isinstance(document["admit"], dict):
                raise BoxError("invalid admission handshake")
            request = document["admit"]
            if set(request) != {"owner", "center", "session", "destroying"} or (
                type(request["destroying"]) is not bool
            ):
                raise BoxError("invalid admission fields")
            grant = self.admission.grant(request["owner"], request["center"], request["session"],
                                         destroying=request["destroying"])
            if not request["destroying"]:
                with self.host.lock(grant.owner_id):
                    self.host._authorized(grant, request["center"])
                    row = self.admission.record(grant.owner_id, request["center"])
                    self.quota.provision(grant.uid, row["quota"], request["center"])
                    self.relays.bind(grant.owner_id, grant.uid)
            send(sock, {"admitted": True})
            serve_channel(sock, peer=identity, grant=grant, host=self.host)
        except (EOFError, OSError, ValueError):
            pass
        finally:
            if pidfd is not None:
                os.close(pidfd)
            sock.close()

    def serve(self, path: Path, gid: int, handler, stop: threading.Event):
        path.unlink(missing_ok=True)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            listener.bind(str(path))
            os.chown(path, 0, gid)
            os.chmod(path, 0o660)
            listener.listen(32)
            listener.settimeout(1)
            while not stop.is_set():
                try:
                    sock, _ = listener.accept()
                except TimeoutError:
                    continue
                if not self._slots.acquire(blocking=False):
                    sock.close()
                    continue

                def run(connection=sock):
                    try:
                        handler(connection)
                    finally:
                        self._slots.release()

                threading.Thread(target=run, daemon=True).start()


def main(*, rootfs: Path, data_root: Path) -> None:
    from tinyassets.boxes.layout import hold

    with hold(data_root):
        _serve(rootfs=rootfs, data_root=data_root)


def _serve(*, rootfs: Path, data_root: Path) -> None:
    if os.geteuid() != 0:
        raise RuntimeError("box host requires its dedicated root service")
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o755, exist_ok=True)
    broker_dir = RUNTIME / "broker"
    broker_dir.mkdir(mode=0o750, exist_ok=True)
    os.chown(broker_dir, 1002, 1101)
    os.chmod(broker_dir, 0o2750)
    state = Path("/var/lib/boxhostd/state")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    if (state / "boxhost.db").exists():
        raise RuntimeError("unpaired host-path receipts require reviewed restore")
    admission = OwnerRegistry(data_root, broker_dir / "broker.sock")
    relays = OwnerRelays(RUNTIME / "relays", admission)
    runtime = GvisorRuntime(
        runsc=Path("/opt/tinyassets/boxhostd/current/runsc"), rootfs=rootfs,
        owner_store=Path("/var/lib/boxhostd/owners"), state_root=state / "runsc",
        bundle_root=state / "bundles",
        cgroup_parent=Path("/sys/fs/cgroup/system.slice/boxhostd.service"))
    backend = GvisorFileBackend(runtime, relay_for=lambda uid: relays.directory / f"{uid}.sock",
                                memory_for=lambda uid: 1024**3)
    # The reviewed migration seeds authority. A missing/lost journal must not
    # silently become a new operation namespace on service restart.
    journal = data_root / ".boxhost/boxhost.db"
    info = journal.lstat()
    if (not stat.S_ISREG(info.st_mode) or info.st_uid != 0 or info.st_nlink != 1
            or info.st_mode & 0o077):
        raise RuntimeError("owner-box host requires its migrated private receipt journal")
    with sqlite3.connect(journal.as_uri() + "?mode=ro", uri=True) as db:
        if (db.execute("PRAGMA quick_check").fetchone() != ("ok",)
                or db.execute("SELECT v FROM host WHERE k='incarnation'").fetchone() is None):
            raise RuntimeError("owner-box receipt journal is incomplete")
        for table in ("boxes", "ops", "execs", "owner_destroys"):
            db.execute(f"SELECT * FROM {table} LIMIT 0")
    host = OwnerFileHost(state_dir=data_root / ".boxhost", backend=backend,
                         authorize=admission.authorize,
                         authorize_destroy=lambda grant, center:
                         admission.authorize(grant, center, destroying=True),
                         bound_for=lambda grant, center:
                         admission.record(grant.owner_id, center)["quota"])
    service = HostService(host, admission, OwnerQuota(runtime.owner_store), relays)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    listeners = [threading.Thread(target=service.serve, args=(
        RUNTIME / name, uid, handler, stop), daemon=True)
        for name, uid, handler in (("broker-registration.sock", 1002, service.register),
                                  ("daemon.sock", 1001, service.daemon))]
    try:
        for listener in listeners:
            listener.start()
        while not stop.wait(1):
            if not all(listener.is_alive() for listener in listeners):
                raise RuntimeError("box host admission listener failed")
            backend.reap_idle()
    finally:
        stop.set()
        for listener in listeners:
            listener.join(2)
        host.close()
        relays.close()
