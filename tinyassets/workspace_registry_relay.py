"""One invocation's registry-only checking proxy, owned by the daemon.

No child runs at the daemon uid. The owner cell receives this exact socket,
never the command center's general egress. Closing it revokes acquisition.
"""
from __future__ import annotations

import secrets
import socket
import threading
import time

from tinyassets import role_relays
from tinyassets.workspace_registry import TransferBudget, serve_registry_tunnel


class RegistryRelay:
    def __init__(self, center, *, max_bytes, timeout_s):
        self.budget = TransferBudget(max_bytes=max_bytes, timeout_s=timeout_s,
                                     max_connections=64, max_active=8)
        self.path = (center.parent / '.universe-sidecars' / center.name
                     / ('registry-' + secrets.token_hex(16) + '.sock'))
        self._lock = threading.Lock()
        self._workers = []
        self._closed = False
        self._server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._identity = role_relays.bind(self._server, self.path)
            self._server.listen(8)
            self._server.settimeout(0.1)
        except BaseException:
            self._server.close()
            raise
        self._thread = threading.Thread(target=self._accept, daemon=True)
        self._thread.start()

    def _accept(self):
        while not self._closed:
            try:
                conn, _ = self._server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                self._workers = [w for w in self._workers if w.is_alive()]
                if self._closed or len(self._workers) >= 8:
                    conn.close()
                    if not self._closed:
                        self.budget.fail('connection_limit')
                    continue
                worker = threading.Thread(target=serve_registry_tunnel,
                                          args=(conn, self.budget), daemon=True)
                self._workers.append(worker)
                worker.start()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self._server.close()
        self._thread.join(timeout=1)
        end = time.monotonic() + 2
        with self._lock:
            workers = tuple(self._workers)
        for worker in workers:
            worker.join(timeout=max(0, end - time.monotonic()))
        # Give completed clients' charged upstream tails a bounded clean drain.
        # Cancellation of a still-active tunnel is uncertain termination, even
        # when its worker responds promptly: retain the maximum reservation.
        before = self.budget.snapshot()
        self.budget.cancel()
        end = time.monotonic() + 0.25
        for worker in workers:
            worker.join(timeout=max(0, end - time.monotonic()))
        role_relays.remove(self.path, self._identity)
        self.snapshot = self.budget.snapshot()
        self.failure = before.failure or ('transport_failed' if before.active else None)
        if any(w.is_alive() for w in workers) or self.snapshot.active:
            self.failure = self.failure or 'transport_failed'
        self.charge = (self.budget.max_bytes if self.failure
                       else self.snapshot.bytes_transferred)
