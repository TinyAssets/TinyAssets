"""Dependency-free worker inside the box; credential-free RPC uses stdio."""
from __future__ import annotations

import base64
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def main():
    bootstrap = json.loads(sys.stdin.buffer.readline(2 * 1024 * 1024))
    stopped = threading.Event()
    output_lock = threading.Lock()
    condition = threading.Condition()
    answers = {}

    def emit(message):
        with output_lock:
            data = b"\x1eTA1 " + json.dumps(message).encode() + b"\n"
            while data:
                data = data[os.write(1, data):]

    def replies():
        pending = bytearray()
        while not stopped.is_set():
            # Raw fd reads avoid holding a Python buffered-I/O lock in a daemon
            # thread when the command finishes and the interpreter shuts down.
            raw = os.read(0, 65536)
            if not raw:
                stopped.set()
                with condition:
                    condition.notify_all()
                return
            pending.extend(raw)
            if len(pending) > 8 * 1024 * 1024 + 256:
                stopped.set()
                return
            while b"\n" in pending:
                line, _, rest = pending.partition(b"\n")
                pending[:] = rest
                message = json.loads(line)
                with condition:
                    answers[message["request"]] = message["answer"]
                    condition.notify_all()

    with tempfile.TemporaryDirectory(prefix="ta-") as directory:
        client = Path(directory) / "ta"
        client.write_text(bootstrap["client"], encoding="utf-8")
        client.chmod(0o700)
        address = "\0ta-" + os.urandom(16).hex()
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(address)
        server.listen(8)
        server.settimeout(0.1)

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
                        raw = conn.makefile("rb").readline(1048833)
                        if len(raw) > 1048832 or not raw.endswith(b"\n"):
                            raise ValueError("invalid request")
                        message = json.loads(raw)
                        if (set(message) != {"request", "message"}
                                or not isinstance(message["request"], str)
                                or not re.fullmatch(r"[a-f0-9]{32}", message["request"])):
                            raise ValueError("invalid envelope")
                        request = message["request"]
                        # Reconnects resend the SAME identity; host receipts
                        # verify its payload digest before returning a result.
                        with condition:
                            answers.pop(request, None)
                        emit({**message, "delivery": os.urandom(16).hex()})
                        with condition:
                            condition.wait_for(lambda: request in answers or stopped.is_set())
                            answer = answers.pop(request, {"error": "ta turn expired"})
                        conn.sendall(json.dumps(answer).encode() + b"\n")
                    except (OSError, ValueError, TypeError):
                        try:
                            conn.sendall(b'{"error":"remote ta outcome unknown; '
                                         b'do not retry blindly"}\n')
                        except OSError:
                            pass

        threading.Thread(target=replies, daemon=True).start()
        thread = threading.Thread(target=serve, daemon=True)
        thread.start()
        env = {**os.environ, "TA_SOCKET": "@" + address[1:],
               "PATH": directory + ":" + os.environ.get("PATH", "/usr/bin:/bin")}
        emit({"ready": True})
        try:
            process = subprocess.Popen(["/bin/bash", "-c", bootstrap["command"]], env=env,
                                       stdin=subprocess.DEVNULL,
                                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
            while data := process.stdout.read1(4096):
                emit({"output": base64.b64encode(data).decode()})
            return process.wait()
        finally:
            stopped.set()
            with condition:
                condition.notify_all()
            server.close()
            thread.join(timeout=1)


if __name__ == "__main__":
    sys.exit(main())
