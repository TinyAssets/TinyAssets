"""Dependency-free reverse RPC worker; this source runs INSIDE the remote box.

All inputs are public. The trusted collector authenticates the existing box
channel, never a token held by this process. A fresh socket serves one exec.
"""
from __future__ import annotations

import base64
import json
import os
import socket
import subprocess
import sys
import threading
from pathlib import Path


def main():
    directory, command, client_source = sys.argv[1:]
    root = Path(directory)
    root.mkdir(mode=0o700)  # refuse existing/planted paths
    client = root / "ta"
    client.write_text(client_source, encoding="utf-8")
    client.chmod(0o700)
    # AF_UNIX paths have a small limit; abstract sockets stay inside the box's
    # network namespace and never create a reusable filesystem endpoint.
    address = "\0ta-" + os.urandom(16).hex()
    server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    server.bind(address)
    server.listen(8)
    server.settimeout(0.1)
    stopped = threading.Event()
    output_lock = threading.Lock()

    def emit(message):
        with output_lock:
            data = b"\x1eTA1 " + json.dumps(message).encode() + b"\n"
            while data:
                data = data[os.write(1, data):]

    def serve():
        while not stopped.is_set():
            try:
                conn, _ = server.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            with conn:
                conn.settimeout(600)
                try:
                    raw = conn.makefile("rb").readline(1048577)
                    if len(raw) > 1048576 or not raw.endswith(b"\n"):
                        raise ValueError("invalid request")
                    message = json.loads(raw)
                    request = os.urandom(16).hex()
                    emit({"request": request, "message": message})
                    response = root / request
                    while not stopped.wait(0.01):
                        if response.exists():
                            # Provider.write publishes atomically. No credentials
                            # are stored here; this is only the capability result.
                            data = response.read_bytes()
                            conn.sendall(data + b"\n")
                            break
                except (OSError, ValueError):
                    try:
                        conn.sendall(b'{"error":"remote ta request failed"}\n')
                    except OSError:
                        pass

    thread = threading.Thread(target=serve, daemon=True)
    thread.start()
    env = {**os.environ, "TA_SOCKET": "@" + address[1:],
           "PATH": str(root) + ":" + os.environ.get("PATH", "/usr/bin:/bin")}
    try:
        process = subprocess.Popen(["/bin/bash", "-c", command], env=env,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        while data := process.stdout.read1(4096):
            emit({"output": base64.b64encode(data).decode()})
        return process.wait()
    finally:
        stopped.set()
        server.close()
        thread.join(timeout=1)
        # The driver owns process-tree cleanup. Keep mailbox receipts in the box
        # for stream replay; they confer no authority after the collector closes.


if __name__ == "__main__":
    sys.exit(main())
