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

    def __init__(self, relay: Path):
        self.relay = relay
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._workers = {}
        self._listener = socket.socket()
        try:
            self._listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            self._listener.bind(LISTEN_ADDRESS)
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
        try:
            with guest, relay:
                guest.settimeout(30)
                relay.settimeout(30)
                relay.connect(str(self.relay))
                relay.sendall(rf.control(rf.CONNECTION, {"op": "BOX_PUBLIC"}))
                answer = rf.read_frame_blocking(relay)
                if (answer is None or answer.kind != rf.CONTROL
                        or answer.stream != rf.CONNECTION
                        or answer.control() != {"op": "BOX_CONNECTED"}):
                    return
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
        except (OSError, ValueError, rf.FrameError):
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
