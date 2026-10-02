"""A scripted ``BoxProvider`` (target architecture D2) for the thin-loop tests.

It runs nothing. Each ``start_exec`` is recorded with its ``op_id`` and argv,
and answered by a script the test supplies; a repeated ``op_id`` returns the
recorded execution and never runs the script again, which is the D2 contract
the loop's lost-reply handling relies on.
"""

from __future__ import annotations

import threading
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any, Callable


@dataclass
class Exec:
    op_id: str
    argv: list[str]
    stdin: Any
    cwd: str
    events: list[Any] = field(default_factory=list)
    cancelled: bool = False


def out(data: bytes, offset: int) -> Any:
    return SimpleNamespace(kind="stdout", data=data, offset=offset)


def exit_event(code: int) -> Any:
    return SimpleNamespace(kind="exit", code=code)


class FakeBox:
    """Scripted executions keyed by op_id; ``script(argv, stdin) -> (bytes, code)``."""

    def __init__(self, script: Callable[[list[str], Any], tuple[bytes, int]] | None = None):
        self.script = script or (lambda argv, stdin: (b"ok\n", 0))
        self.execs: dict[str, Exec] = {}
        self.starts: list[str] = []
        self.cancels: list[str] = []
        self.binds: list[tuple[str, str, str | None]] = []
        self.fail_start: int = 0
        self.fail_stream: int = 0
        self.hang = False
        self.status = SimpleNamespace(state="unknown_after_restore")
        self.released = threading.Event()
        self.lock = threading.Lock()

    # ── D2 surface ──────────────────────────────────────────────────────────
    def bind(self, cc, *, account, turn):
        self.binds.append((cc, account, turn))
        return SimpleNamespace(cc=cc, account=account, turn=turn, root="/cc")

    def start_exec(self, h, op_id, argv, *, stdin=None, env=None, cwd="/cc", limits=None):
        with self.lock:
            self.starts.append(op_id)
            first = op_id not in self.execs
            if first:
                self.execs[op_id] = Exec(op_id, list(argv), stdin, cwd)
        if first:
            # Outside the lock: concurrent executions really run concurrently.
            data, code = self.script(list(argv), stdin)
            self.execs[op_id].events = (
                [out(data, len(data)), exit_event(code)] if data else [exit_event(code)])
        with self.lock:
            if self.fail_start:
                self.fail_start -= 1
                raise ConnectionError("synthetic lost reply")
        return op_id

    def stream(self, h, exec_id, *, from_offset=0):
        record = self.execs[exec_id]
        if self.fail_stream:
            self.fail_stream -= 1
            raise ConnectionError("synthetic stream break")
        if self.hang:
            self.released.wait(10)
            yield exit_event(137)
            return
        yield from record.events

    def cancel(self, h, exec_id):
        self.cancels.append(exec_id)
        self.execs[exec_id].cancelled = True
        self.released.set()

    def exec_status(self, h, op_id):
        return self.status
