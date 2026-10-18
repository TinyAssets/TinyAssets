"""Root boxhostd: broker bootstrap and persistent owner-daemon control."""

from __future__ import annotations

import json
import logging
import os
import select
import signal
import socket
import stat
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from tinyassets.boxes.admission import OwnerRegistry
from tinyassets.boxes.bootstrap import RUNTIME, peer
from tinyassets.boxes.control_channel import receive, send
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.owner_daemons import OwnerDaemons
from tinyassets.boxes.provider import BoxError
from tinyassets.boxes.quota import OwnerQuota
from tinyassets.boxes.relay import OwnerRelays


class HostService:
    def __init__(self, admission: OwnerRegistry, quota: OwnerQuota,
                 relays: OwnerRelays, daemons: OwnerDaemons) -> None:
        self.admission, self.quota, self.relays = admission, quota, relays
        self._broker_lock = threading.Lock()
        self._broker = None
        self.broker_ready = threading.Event()
        self._slots = threading.BoundedSemaphore(32)
        self._broker_slots = threading.BoundedSemaphore(4)
        self._erasure_slots = threading.BoundedSemaphore(32)
        # Waiting scheduled wakes cannot consume every listener worker and
        # prevent an interactive request from entering the priority queue.
        self._scheduled_slots = threading.BoundedSemaphore(8)
        self.daemons = daemons

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
            self.broker_ready.set()
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
                    self.broker_ready.clear()
            if pidfd is not None:
                os.close(pidfd)
            sock.close()

    def erasure(self, sock):
        self.daemon(sock, erasure_only=True)

    def daemon(self, sock, *, erasure_only=False):
        identity = peer(sock)
        pidfd = None
        try:
            if identity[1:] not in {(0, 0), (1001, 1001), (1002, 1002)}:
                raise BoxError("daemon channel identity refused")
            pidfd = os.pidfd_open(identity[0])
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_PASSCRED, 1)
            sock.settimeout(30)
            document = receive(sock, identity, pidfd)
            if erasure_only != (document.get("lifecycle") == "erasure"):
                raise BoxError("wrong control channel for erasure operation")
            if document == {"bootstrap": True} and identity[1] == 1001:
                with self._broker_lock:
                    broker = self._broker
                    if broker is None or not broker[2] or select.select([broker[0]], [], [], 0)[0]:
                        raise BoxError("credential broker is not ready")
                    send(sock, {"proof": broker[1], "broker_socket":
                                str(self.admission.broker_socket)})
                return
            send(sock, self.lifecycle(document, identity[1]))
        except (EOFError, OSError, ValueError):
            pass
        finally:
            if pidfd is not None:
                os.close(pidfd)
            sock.close()

    def lifecycle(self, document, uid):
        from tinyassets.broker.owner_identities import validate_principal

        try:
            if document.get("lifecycle") == "erasure":
                from tinyassets.erasure_ledger import ErasureLedger

                if ((uid != 1001 and not (uid == 1002 and document.get("ledger_op")
                                        in {"contains", "contains_many"}))
                        or set(document) != {"lifecycle", "ledger_op", "arguments"}
                        or document["ledger_op"] not in {
                            "contains", "contains_many", "add", "receipt", "save", "pending_page"}):
                    raise BoxError("erasure operation refused for this peer")
                result = ErasureLedger(self.admission.data_root).execute(
                    document["ledger_op"], document["arguments"])
                if document["ledger_op"] == "receipt":
                    # Paths and accumulated billing receipts stay in root custody;
                    # the router needs only the pending-path fence and phase state.
                    result.pop("billing_cleanup", None)
                    result.pop("erasure_failures", None)
                    result["quarantines"] = ["pending"] if result["quarantines"] else []
                return {"result": result}
            if self.daemons is None:
                raise BoxError("owner lifecycle is unavailable")
            if (uid in {0, 1002} and document.get("lifecycle") == "roll"
                    and set(document) in ({"lifecycle", "image"}, {"lifecycle", "image", "uid"})):
                return self.daemons.roll(document["image"], document.get("uid"))
            validate_principal(document.get("owner"))
            operation, owner = document["lifecycle"], document["owner"]
            if (uid == 1001 and operation == "resolve"
                    and set(document) == {"lifecycle", "owner", "cleanup"}
                    and type(document["cleanup"]) is bool):
                return self.daemons.resolve(owner, cleanup=document["cleanup"])
            if (uid == 1001 and operation == "release"
                    and set(document) == {"lifecycle", "owner", "lease"}
                    and isinstance(document["lease"], str)):
                self.daemons.release(owner, document["lease"])
                return {"released": True}
            if (uid == 1002 and operation == "destroy"
                    and set(document) == {"lifecycle", "owner", "uid"}):
                from tinyassets.erasure_ledger import ErasureLedger, principal_digest

                ledger = ErasureLedger(self.admission.data_root)
                ledger.execute("add", {"digest": principal_digest(owner), "principal": owner,
                                       "uids": [document["uid"]]})
                self.daemons.destroy(owner, document["uid"])
                return {"destroyed": True}
            if ((uid == 1001 or (uid == 1002 and document.get("scheduled") is True))
                    and operation == "ensure_awake"
                    and set(document) == {"lifecycle", "owner", "scheduled"}):
                scheduled = document["scheduled"]
                if type(scheduled) is not bool:
                    raise ValueError("scheduled must be boolean")
                if scheduled and not self._scheduled_slots.acquire(blocking=False):
                    raise BoxError("scheduled wake queue is full")
                try:
                    return {"uid": self.daemons.ensure_awake(owner, scheduled=scheduled)}
                finally:
                    if scheduled:
                        self._scheduled_slots.release()
            if (uid == 1001 and operation == "status"
                    and set(document) == {"lifecycle", "owner"}):
                return self.daemons.status(owner)
            if (uid == 1002 and operation == "retire"
                    and set(document) == {"lifecycle", "owner", "uid"}):
                self.daemons.retire(owner, document["uid"])
                return {"stopped": True}
            raise BoxError("lifecycle operation refused for this peer")
        except LookupError:
            return {"error": "unadmitted"}
        except Exception as exc:
            import logging

            logging.getLogger(__name__).exception("owner lifecycle failed")
            return {"error": str(exc)}

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
                slots = (self._erasure_slots if handler == self.erasure else
                         self._broker_slots if peer(sock)[1] == 1002 else self._slots)
                if not slots.acquire(blocking=False):
                    sock.close()
                    continue

                def run(connection=sock, capacity=slots):
                    try:
                        handler(connection)
                    finally:
                        capacity.release()

                threading.Thread(target=run, daemon=True).start()


@contextmanager
def host_lock(state: Path):
    """One persistent lifecycle controller may own the private runsc state."""
    import fcntl

    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = state.lstat()
    if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.geteuid()
            or info.st_mode & 0o077):
        raise BoxError("box host state requires a private service-owned directory")
    descriptor = os.open(state / "service.lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        info = os.fstat(descriptor)
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.geteuid()
                or info.st_nlink != 1 or info.st_mode & 0o077):
            raise BoxError("box host service lock identity changed")
        fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield
    finally:
        os.close(descriptor)


def recover_daemons(runtime):
    """Prove the previous controller's sandboxes stopped before admission."""
    states = json.loads(runtime._command("list", "--format=json").stdout)
    if states is None:  # Pinned runsc serializes its empty Go slice as JSON null.
        states = []
    if not isinstance(states, list):
        raise BoxError("invalid sandbox census")
    owners = set()
    for state in states:
        name = state["id"]
        uid = int(name.removeprefix("owner-"))
        if runtime.box_id(uid) != name:
            raise BoxError("unexpected sandbox in private runsc state")
        owners.add(uid)
    for path in runtime.bundle_root.iterdir():
        uid = int(path.name.removeprefix("owner-"))
        if runtime.box_id(uid) != path.name or path.is_symlink():
            raise BoxError("unexpected sandbox bundle")
        owners.add(uid)
    for uid in sorted(owners):
        runtime.stop(uid)


def main(*, rootfs: Path, data_root: Path, cgroup_parent: Path) -> None:
    from tinyassets.boxes.layout import hold

    if os.geteuid() != 0:
        raise RuntimeError("box host requires its dedicated root service")
    with hold(data_root), host_lock(Path("/var/lib/boxhostd/state")):
        _serve(rootfs=rootfs, data_root=data_root, cgroup_parent=cgroup_parent)


def _serve(*, rootfs: Path, data_root: Path, cgroup_parent: Path) -> None:
    from tinyassets.boxes.images import Images
    if os.geteuid() != 0:
        raise RuntimeError("box host requires its dedicated root service")
    os.umask(0o077)
    RUNTIME.mkdir(mode=0o755, exist_ok=True)
    os.chmod(RUNTIME, 0o755)  # Same mode as systemd RuntimeDirectory, even on first boot.
    broker_dir = RUNTIME / "broker"
    broker_dir.mkdir(mode=0o750, exist_ok=True)
    os.chown(broker_dir, 1002, 1101)
    os.chmod(broker_dir, 0o2750)
    state = Path("/var/lib/boxhostd/state")
    state.mkdir(mode=0o700, parents=True, exist_ok=True)
    admission = OwnerRegistry(data_root, broker_dir / "broker.sock")
    relays = OwnerRelays(RUNTIME / "relays", admission)
    runtime = GvisorRuntime(
        runsc=Path("/opt/tinyassets/boxhostd/current/runsc"), rootfs=rootfs,
        owner_store=Path("/var/lib/boxhostd/owners"), state_root=state / "runsc",
        bundle_root=state / "bundles",
        restore_epoch_file=data_root / ".restore-epoch",
        cgroup_parent=cgroup_parent)
    recover_daemons(runtime)
    from tinyassets.erasure_ledger import protect_root
    from tinyassets.restore_recovery import finish_erasures

    protect_root(data_root)

    quota = OwnerQuota(runtime.owner_store)
    daemons = OwnerDaemons(
        runtime, admission, quota, relays, RUNTIME,
        idle_seconds=float(os.environ.get("TINYASSETS_BOX_IDLE_SECONDS", "900")),
        awake_cap=int(os.environ.get("TINYASSETS_BOX_AWAKE_CAP", "6")),
        memory_bytes=int(os.environ.get("TINYASSETS_BOX_MEMORY_BYTES", str(1024**3))))
    daemons.images = Images(rootfs.parent, state / "images.json", rootfs)
    service = HostService(admission, quota, relays, daemons)
    stop = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    listeners = [threading.Thread(target=service.serve, args=(
        RUNTIME / name, uid, handler, stop), daemon=True)
        for name, uid, handler in (("erasure.sock", 1102, service.erasure),
                                  ("broker-registration.sock", 1002, service.register),
                                  ("daemon.sock", 1102, service.daemon))]
    try:
        # The broker registers before serving. Recovery must not block that
        # handshake; erased owners remain fenced while other owners can serve.
        for listener in listeners:
            listener.start()
        recovery_at = 0
        while not stop.wait(1):
            if not all(listener.is_alive() for listener in listeners):
                raise RuntimeError("box host control listener failed")
            if service.broker_ready.is_set() and time.monotonic() >= recovery_at:
                try:
                    complete = finish_erasures(data_root, runtime.owner_store, daemons=daemons)
                    recovery_at = float("inf") if complete else time.monotonic() + 30
                except Exception:
                    logging.getLogger(__name__).exception(
                        "startup erasure retry failed; ledger fences remain")
                    recovery_at = time.monotonic() + 30
            daemons.maintain()
    finally:
        stop.set()
        for listener in listeners:
            listener.join(2)
        daemons.close()
        relays.close()
