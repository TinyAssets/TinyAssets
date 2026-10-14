"""One owner-bound outbound socket; admitted connector streams reach the broker."""

from __future__ import annotations

import os
import select
import socket
import threading
from pathlib import Path

from tinyassets import rpc_frames as rf
from tinyassets.boxes.admission import OwnerRegistry
from tinyassets.boxes.bootstrap import peer
from tinyassets.boxes.provider import BoxAuthError


class OwnerRelays:
    def __init__(self, directory: Path, admission: OwnerRegistry) -> None:
        self.directory, self.admission = directory, admission
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self._bindings = {}
        self._listeners = {}
        self._lock = threading.Lock()
        self._slots = threading.BoundedSemaphore(64)

    def bind(self, owner: str, uid: int) -> Path:
        with self._lock:
            if uid in self._bindings:
                if self._bindings[uid] != owner:
                    raise BoxAuthError("relay UID is already bound to another owner")
                return self.directory / f"{uid}.sock"
            path = self.directory / f"{uid}.sock"
            path.unlink(missing_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(path))
            os.chown(path, uid, uid)
            os.chmod(path, 0o600)
            listener.listen(16)
            self._bindings[uid] = owner
            self._listeners[uid] = listener
            threading.Thread(target=self._accept, args=(listener, owner, uid), daemon=True).start()
            return path

    def _accept(self, listener, owner, uid):
        while True:
            try:
                guest, _ = listener.accept()
            except OSError:
                return
            if not self._slots.acquire(blocking=False):
                guest.close()
                continue
            threading.Thread(target=self._serve, args=(guest, owner, uid), daemon=True).start()

    def _serve(self, guest, owner, uid):
        try:
            # The socket itself is unique and mounted only into this owner box.
            # runsc may proxy AF_UNIX peers as root; no identity is accepted from
            # that peer or request. Owner identity was fixed by bind(), above.
            with guest, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as broker:
                guest.settimeout(30)
                first = rf.read_frame_blocking(guest)
                if first is None or first.kind != rf.CONTROL or first.stream != rf.CONNECTION:
                    raise BoxAuthError("relay requires a bounded center selection")
                doc = first.control()
                if set(doc) != {"op", "center"} or doc["op"] != "BOX_CONNECT":
                    raise BoxAuthError("invalid relay operation")
                center = doc["center"]
                grant = self.admission.grant(owner, center, "relay")
                if grant.uid != uid:
                    raise BoxAuthError("foreign center on owner relay")
                broker.settimeout(30)
                broker.connect(str(self.admission.broker_socket))
                if peer(broker)[1:] != (1002, 1002):
                    raise BoxAuthError("relay peer is not the broker")
                broker.sendall(rf.control(rf.CONNECTION, {
                    "op": "BOX_CHANNEL", "principal": owner, "center": center}))
                answer = rf.read_frame_blocking(broker)
                if answer is None or answer.control().get("op") != "BOX_ADMITTED":
                    raise BoxAuthError("broker refused box channel")
                guest.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CONNECTED"}))
                # Fixed-size forwarding prevents a peer's input from growing an
                # unbounded queue. The broker owns framing, credit and policy.
                while True:
                    ready, _, _ = select.select([guest, broker], [], [], 30)
                    if not ready or not self.admission.authorize(grant, center):
                        return
                    for source in ready:
                        data = source.recv(65536)
                        if not data:
                            return
                        (broker if source is guest else guest).sendall(data)
        except (OSError, ValueError, rf.FrameError):
            pass  # no identity or credential details in guest errors
        finally:
            guest.close()
            self._slots.release()

    def close(self):
        for listener in self._listeners.values():
            listener.close()
