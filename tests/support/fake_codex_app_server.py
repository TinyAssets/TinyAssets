"""A scripted stand-in for ``codex app-server`` on its stdio JSON-RPC.

Handed to the codex adapter through the owned-spawn seam
(``tests.support.owned_spawn``). It answers the protocol the adapter drives --
``initialize``, ``thread/start`` / ``thread/resume``, ``turn/start`` -- then
plays a turn: one ``item/tool/call`` request per scripted call (waiting for
the adapter's answer before the next), then the reply, its token usage and
``turn/completed``. Everything the adapter wrote is kept in ``received``.

It proves what the adapter sends and how it handles answers; it proves
nothing about the real CLI, whose captures are the evidence for that.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field


@dataclass
class Turn:
    calls: list[dict] = field(default_factory=list)   # {"tool":..., "arguments":...}
    reply: str = "done"
    status: str = "completed"
    error: str = ""
    usage: tuple[int, int, int] = (5, 2, 1)           # input, output, reasoning
    hang: bool = False                                 # never complete the turn
    server_requests: list[str] = field(default_factory=list)


class FakeAppServer:
    """A process-shaped object: ``stdin``/``stdout``/``stderr``, ``kill``, ``wait``."""

    def __init__(self, turn: Turn | None = None, *, thread_id: str = "thr-1",
                 stderr: bytes = b"", default_model: str = "cli-selected") -> None:
        self.turn = turn or Turn()
        self.thread_id = thread_id
        self.default_model = default_model
        self.stdout = asyncio.StreamReader(limit=32 * 1024 * 1024)
        self.stderr = asyncio.StreamReader()
        if stderr:
            self.stderr.feed_data(stderr)
        self.stderr.feed_eof()
        self.stdin = _Stdin(self)
        self.returncode: int | None = None
        self.pid = 0
        self.received: list[dict] = []
        self.tool_results: list[dict] = []
        self.declined: list[dict] = []
        self.killed = False
        self._waiting: dict = {}
        self._calls = list(self.turn.calls)
        self._serial = 0

    # --- process surface ------------------------------------------------------
    def kill(self) -> None:
        self.killed = True
        if self.returncode is None:
            self.returncode = -9
        if not self.stdout.at_eof():
            self.stdout.feed_eof()

    terminate = kill

    async def wait(self) -> int:
        return self.returncode if self.returncode is not None else 0

    # --- protocol -------------------------------------------------------------
    def emit(self, message: dict) -> None:
        if not self.stdout.at_eof():
            self.stdout.feed_data((json.dumps(message) + "\n").encode())

    def requests(self, method: str) -> list[dict]:
        return [m for m in self.received if m.get("method") == method]

    def _server_request(self, method: str, params: dict) -> str:
        self._serial += 1
        ident = f"srv-{self._serial}"
        self.emit({"id": ident, "method": method, "params": params})
        return ident

    def _next(self) -> None:
        if self._calls:
            call = self._calls.pop(0)
            ident = self._server_request("item/tool/call", {
                "threadId": self.thread_id, "turnId": "turn-1", "callId": f"call-{self._serial}",
                "tool": call["tool"], "arguments": call.get("arguments", {}),
                "namespace": call.get("namespace"),
            })
            self._waiting[ident] = "tool"
            return
        if self.turn.hang:
            return
        inp, out, reasoning = self.turn.usage
        if self.turn.reply:
            self.emit({"method": "item/completed", "params": {"item": {
                "type": "agentMessage", "id": "m1", "text": self.turn.reply}}})
        self.emit({"method": "thread/tokenUsage/updated", "params": {
            "threadId": self.thread_id, "turnId": "turn-1", "tokenUsage": {
                "last": {"inputTokens": inp, "outputTokens": out,
                         "reasoningOutputTokens": reasoning, "cachedInputTokens": 0,
                         "totalTokens": inp + out},
                "total": {"inputTokens": inp, "outputTokens": out,
                          "reasoningOutputTokens": reasoning, "cachedInputTokens": 0,
                          "totalTokens": inp + out}}}})
        self.emit({"method": "turn/completed", "params": {"threadId": self.thread_id, "turn": {
            "id": "turn-1", "status": self.turn.status, "items": [],
            "error": {"message": self.turn.error} if self.turn.error else None}}})

    def handle(self, message: dict) -> None:
        self.received.append(message)
        method, ident = message.get("method"), message.get("id")
        if method is None:
            kind = self._waiting.pop(ident, None)
            if kind == "tool":
                self.tool_results.append(message.get("result") or {})
                self._next()
            elif kind == "other":
                self.declined.append(message)
                if not any(v == "other" for v in self._waiting.values()):
                    self._next()
            return
        if method == "initialize":
            self.emit({"id": ident, "result": {"userAgent": "fake/0.160.0"}})
        elif method in ("thread/start", "thread/resume"):
            # The CLI names the model the thread is configured with, its own
            # pick when none was requested.
            model = message["params"].get("model") or self.default_model
            self.emit({"id": ident, "result": {"thread": {"id": self.thread_id, "model": model}}})
        elif method == "turn/start":
            self.emit({"id": ident, "result": {"turn": {"id": "turn-1", "status": "inProgress"}}})
            self.emit({"method": "turn/started", "params": {"threadId": self.thread_id}})
            if self.turn.server_requests:
                for name in self.turn.server_requests:
                    self._waiting[self._server_request(name, {})] = "other"
            else:
                self._next()


class _Stdin:
    def __init__(self, server: FakeAppServer) -> None:
        self._server = server
        self._buffer = b""

    def write(self, data: bytes) -> None:
        self._buffer += data
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            if line.strip():
                self._server.handle(json.loads(line))

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        return None
