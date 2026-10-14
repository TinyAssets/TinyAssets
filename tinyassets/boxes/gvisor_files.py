"""The host file transport: fixed harness worker through the pinned runsc.

No owner content is opened on the host. No LocalBoxProvider is instantiated.
Relay paths and resource limits come from host admission, never RPC arguments.
"""

from __future__ import annotations

import selectors
import subprocess
import threading
import time

from tinyassets.boxes.file_wire import MAX_MESSAGE, decode, encode
from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.provider import BoxError


class GvisorFileBackend:
    def __init__(self, runtime: GvisorRuntime, *, relay_for, memory_for,
                 idle_s: float = 60, request_timeout: float = 30) -> None:
        if idle_s <= 0 or request_timeout <= 0:
            raise ValueError("positive lifecycle bounds required")
        self.runtime = runtime
        self.relay_for = relay_for
        self.memory_for = memory_for
        self.idle_s = idle_s
        self.request_timeout = request_timeout
        self._awake: dict[int, float] = {}
        self._guard = threading.RLock()
        self._locks: dict[int, threading.RLock] = {}

    def _lock(self, uid: int) -> threading.RLock:
        self.runtime.box_id(uid)
        with self._guard:
            return self._locks.setdefault(uid, threading.RLock())

    def recover(self) -> None:
        import json

        # The runsc state root is private to this service. A survivor must be
        # stopped even if its bundle was lost during an interrupted launch.
        states = json.loads(self.runtime._command("list", "--format=json").stdout)
        if not isinstance(states, list):
            raise BoxError("invalid sandbox census")
        owners = set()
        for state in states:
            name = state["id"]
            uid = int(name.removeprefix("owner-"))
            if self.runtime.box_id(uid) != name:
                raise BoxError("unexpected sandbox in private runsc state")
            owners.add(uid)
        for path in self.runtime.bundle_root.iterdir():
            uid = int(path.name.removeprefix("owner-"))
            if self.runtime.box_id(uid) != path.name or path.is_symlink():
                raise BoxError("unexpected sandbox bundle")
            owners.add(uid)
        for uid in owners:
            self.runtime.stop(uid)
        self._awake.clear()

    def _wake(self, uid: int) -> None:
        if uid not in self._awake:
            try:
                self.runtime.start(uid, uid, relay=self.relay_for(uid),
                                   memory_bytes=self.memory_for(uid))
            except BaseException:
                # start may have launched a sandbox before discovering a failure.
                # A failed stop is an error, never permission to accept another call.
                self.runtime.stop(uid)
                raise
        self._awake[uid] = time.monotonic()

    def call(self, uid: int, operation: str, args: dict) -> dict:
        from tinyassets.boxes.owner_files import validate

        # Host args have already projected center paths. Defense in depth against
        # a future internal caller accidentally exposing a different worker op.
        check_args = dict(args)
        if operation == "write":
            check_args["expect_generation"] = None
        validate(operation, check_args)
        body = encode({"operation": operation, "args": args})
        with self._lock(uid):
            self._wake(uid)
            try:
                result = self._invoke(uid, body)
            except BaseException:
                self.stop(uid)
                raise
            self._awake[uid] = time.monotonic()
            return decode(result)

    def _invoke(self, uid: int, body: bytes) -> bytes:
        from tinyassets.boxes.gvisor_runtime import OVERLAY

        argv = [str(self.runtime.runsc), "--root", str(self.runtime.state_root),
                "--systemd-cgroup=false", "--network=none", "--platform=systrap",
                f"--overlay2={OVERLAY}", "exec", self.runtime.box_id(uid),
                "/opt/venv/bin/python", "-I", "-B", "-c",
                "import sys; sys.path.insert(0, '/app'); "
                "from tinyassets.boxes.file_worker import main; main()"]
        proc = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, start_new_session=True)
        deadline = time.monotonic() + self.request_timeout
        chunks: list[bytes] = []
        total = 0
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(proc.stdin, selectors.EVENT_WRITE, "input")
                selector.register(proc.stdout, selectors.EVENT_READ, "output")
                selector.register(proc.stderr, selectors.EVENT_READ, "error")
                import os

                for pipe in (proc.stdin, proc.stdout, proc.stderr):
                    os.set_blocking(pipe.fileno(), False)
                sent = 0
                while selector.get_map():
                    left = deadline - time.monotonic()
                    if left <= 0:
                        raise BoxError("sandbox file request timed out")
                    for key, _ in selector.select(min(left, 0.1)):
                        if key.data == "input":
                            count = os.write(key.fd, body[sent:sent + 65536])
                            sent += count
                            if sent == len(body):
                                selector.unregister(key.fileobj)
                                key.fileobj.close()
                            continue
                        chunk = os.read(key.fd, 65536)
                        if not chunk:
                            selector.unregister(key.fileobj)
                            continue
                        total += len(chunk)
                        if total > MAX_MESSAGE:
                            raise BoxError("sandbox response exceeds its byte bound")
                        if key.data == "output":
                            chunks.append(chunk)
                if proc.wait(timeout=max(0.01, deadline - time.monotonic())) != 0:
                    raise BoxError("sandbox file worker failed")
                return b"".join(chunks)
        finally:
            if proc.poll() is None:
                proc.kill()
                proc.wait(timeout=5)
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                pipe.close()

    def stop(self, uid: int) -> None:
        with self._lock(uid):
            self.runtime.stop(uid)
            self._awake.pop(uid, None)

    def reap_idle(self) -> None:
        with self._guard:
            owners = list(self._awake)
        for uid in owners:
            with self._lock(uid):
                if uid in self._awake and time.monotonic() - self._awake[uid] >= self.idle_s:
                    self.stop(uid)

    def close(self) -> None:
        with self._guard:
            owners = list(self._awake)
        for uid in owners:
            self.stop(uid)
