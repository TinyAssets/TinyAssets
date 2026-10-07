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

_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]*\Z")
_DIGEST = re.compile(r"[a-f0-9]{64}\Z")


def stage(root, extensions):
    """Host-verified revision bytes, read-only for this one launch; no host paths."""
    for item in extensions:
        if not _NAME.fullmatch(item["name"]) or not _DIGEST.fullmatch(item["revision"]):
            raise ValueError("invalid extension delivery")
        package = root / item["name"] / item["revision"]
        for path, data in item["files"].items():
            parts = path.split("/")
            if path.startswith("/") or any(part in ("", ".", "..") for part in parts):
                raise ValueError("invalid extension path")
            target = package.joinpath(*parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(base64.b64decode(data, validate=True))
            target.chmod(0o555)
    for directory, _, _ in sorted(os.walk(root), reverse=True):
        os.chmod(directory, 0o555)


def unstage(root):
    for directory, _, _ in os.walk(root):
        os.chmod(directory, 0o700)


def main():
    bootstrap = json.loads(sys.stdin.buffer.readline(16 * 1024 * 1024))
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
        extensions = Path(directory) / "extensions"
        extensions.mkdir()
        stage(extensions, bootstrap.get("extensions", ()))
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
               "TA_EXTENSION_ROOT": str(extensions),
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
            unstage(extensions)


if __name__ == "__main__":
    sys.exit(main())
