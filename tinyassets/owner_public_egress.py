"""One loopback HTTP/CONNECT bridge owned by the in-box daemon."""
from __future__ import annotations

import contextlib
import select
import socket
import threading
from pathlib import Path

from tinyassets import rpc_frames as rf

JAIL_PROXY_URL = "http://127.0.0.1:3128"
LISTEN_ADDRESS = ("127.0.0.1", 3128)
PROXY_ENV = {
    name: JAIL_PROXY_URL for name in
    ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy")
} | {"NO_PROXY": "localhost,127.0.0.1", "no_proxy": "localhost,127.0.0.1"}


class OwnerPublicForwarder:
    """Copy bytes to the owner-bound relay; only the broker interprets HTTP.

    Threads share no command pipes or subprocess lifetime. Shutdown interrupts
    admission, reads and writes, then joins every worker before returning.
    """

    def __init__(self, relay: Path, *, listen=LISTEN_ADDRESS, allowed_hosts=None):
        self.relay = relay
        self.allowed_hosts = None if allowed_hosts is None else frozenset(allowed_hosts)
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._workers = {}
        self._listener = socket.socket()
        try:
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._listener.bind(listen)
            self.address = self._listener.getsockname()
            self._listener.listen(64)
            self._listener.settimeout(.2)
            self._thread = threading.Thread(target=self._accept, daemon=True)
            self._thread.start()
        except BaseException:
            self._listener.close()
            raise

    def _accept(self):
        while not self._stop.is_set():
            try:
                guest, _ = self._listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with self._lock:
                if self._stop.is_set() or len(self._workers) >= 64:
                    guest.close()
                    continue
                relay = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                worker = threading.Thread(target=self._serve, args=(guest, relay), daemon=True)
                self._workers[worker] = (guest, relay)
                worker.start()

    def _serve(self, guest, relay):
        from tinyassets.boxes.network import EgressRefused

        try:
            with guest, relay:
                guest.settimeout(30)
                relay.settimeout(30)
                head = bytearray()
                if self.allowed_hosts is not None:
                    from tinyassets.boxes.network import _destination

                    while b"\r\n\r\n" not in head:
                        part = guest.recv(1)
                        if not part or len(head) >= 16384:
                            return
                        head.extend(part)
                    host, port, _ = _destination(bytes(head))
                    if host.lower() not in self.allowed_hosts or port != 443:
                        guest.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                        return
                relay.connect(str(self.relay))
                relay.sendall(rf.control(rf.CONNECTION, {"op": "BOX_PUBLIC"}))
                answer = rf.read_frame_blocking(relay)
                if (answer is None or answer.kind != rf.CONTROL
                        or answer.stream != rf.CONNECTION
                        or answer.control() != {"op": "BOX_CONNECTED"}):
                    return
                if head:
                    relay.sendall(head)
                readers = [guest, relay]
                while readers and not self._stop.is_set():
                    ready, _, _ = select.select(readers, [], [], 1)
                    for source in ready:
                        destination = relay if source is guest else guest
                        data = source.recv(65536)
                        if data:
                            destination.sendall(data)
                        else:
                            readers.remove(source)
                            destination.shutdown(socket.SHUT_WR)
        except (OSError, ValueError, rf.FrameError, EgressRefused):
            pass  # EOF is a refusal, never a direct-network fallback.
        finally:
            with self._lock:
                self._workers.pop(threading.current_thread(), None)

    def close(self):
        self._stop.set()
        self._listener.close()
        self._thread.join(5)
        with self._lock:
            workers = list(self._workers.items())
            for _, sockets in workers:
                for connection in sockets:
                    with contextlib.suppress(OSError):
                        connection.shutdown(socket.SHUT_RDWR)
        for worker, _ in workers:
            worker.join(5)
        if self._thread.is_alive() or any(worker.is_alive() for worker, _ in workers):
            raise RuntimeError("owner public forwarder did not stop")
