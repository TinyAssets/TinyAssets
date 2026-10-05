"""The async broker client: many concurrent streams over one connection (I14).

What the thin loop (S7) holds per waiting turn: a coroutine and a credit
window, no thread. One connection per client is multiplexed by stream id;
a demultiplexer task hands every frame to its stream's queue without blocking,
so one slow consumer never holds up another stream's ``HEAD`` or ``END``.

    async with client.stream(grant_id=..., connection_id=..., verb="POST",
                             request={...}, op_id=new_op_id()) as stream:
        head = await stream.head()          # status, reason, headers
        async for chunk in stream.body():   # scanned bytes, as they arrive
            ...

Leaving the block early (or being cancelled) sends ``CANCEL``. A stream that
ends badly raises the same typed errors as the request/close client. A
transport failure after ``OPEN`` is an :class:`AmbiguousProxyOutcome`: the
request may have left. Credit rolls: the window is refilled as the consumer
takes chunks, never past ``MAX_WINDOW``.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from tinyassets import rpc_frames as rf
from tinyassets.broker.client import MAX_WINDOW, _raise_for

_END = object()


class _Stream:
    def __init__(self, client: AsyncBrokerClient, stream_id: int, refresh=None) -> None:
        self._client = client
        self.id = stream_id
        self.queue: asyncio.Queue[Any] = asyncio.Queue()
        self._head: dict[str, Any] | None = None
        self.end: dict[str, Any] | None = None
        self.admitted = False
        self._refresh = refresh
        self._refresh_sequence = 0

    async def _next(self) -> Any:
        item = await self.queue.get()
        if isinstance(item, BaseException):
            raise item
        return item

    async def head(self) -> dict[str, Any]:
        while self._head is None:
            item = await self._next()
            if item is _END:
                _raise_for(self.end or {})
            if isinstance(item, dict):
                if item["op"] == "ADMITTED":
                    self.admitted = True
                elif item["op"] == "HEAD":
                    self._head = item
                elif item["op"] == "REFRESH":
                    if (type(item.get("sequence")) is not int
                            or item["sequence"] != self._refresh_sequence + 1):
                        raise rf.FrameError("unexpected refresh sequence")
                    self._refresh_sequence += 1
                    ok = False
                    try:
                        if self._refresh is not None:
                            # Cancellation stops waiting, never the thread that
                            # already holds vault admission and may have spent.
                            await asyncio.to_thread(self._refresh, item)
                            ok = True
                    except Exception:  # noqa: BLE001 - no credential details in ACK
                        pass
                    await self._client._send(rf.control(self.id, {
                        "op": "REFRESH_ACK", "sequence": self._refresh_sequence, "ok": ok}))
        return self._head

    async def body(self) -> AsyncIterator[bytes]:
        await self.head()
        while True:
            item = await self._next()
            if item is _END:
                end = self.end or {}
                if end.get("outcome") != "completed":
                    _raise_for(end)
                return
            if isinstance(item, bytes):
                await self._client._send(rf.control(self.id, {"op": "CREDIT", "n": len(item)}))
                yield item


class AsyncBrokerClient:
    @classmethod
    def for_owner(cls, data_root, *, principal, command_center):
        """Use the admitted daemon's broker; MCP has no worker fallback."""
        from tinyassets.broker.supervisor import broker_selected, get_supervisor
        from tinyassets.storage.outbound_connections import ProxyRequestError

        supervisor = get_supervisor(data_root) if broker_selected() else None
        if supervisor is None:
            raise ProxyRequestError("streaming MCP requires the running credential broker")
        from tinyassets.broker.refresh import prepare

        return cls(supervisor.socket_path, principal=principal, command_center=command_center,
                   fence=supervisor.fence, verify_peer=supervisor.verify_broker,
                   refresh_factory=lambda grant, connection: prepare(
                       data_root, principal=principal, command_center=command_center,
                       grant_id=grant, connection_id=connection))

    def __init__(self, path: Path, *, principal: str, command_center: str,
                 fence: Callable[[], tuple[int, str]], verify_peer=None,
                 refresh_factory=None) -> None:
        self._path = Path(path)
        self._principal = principal
        self._command_center = command_center
        self._fence = fence
        self._verify_peer = verify_peer
        self._refresh_factory = refresh_factory
        self._ids = itertools.count(1)
        self._streams: dict[int, _Stream] = {}
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._demux: asyncio.Task | None = None
        self._lock = asyncio.Lock()

    async def _connect(self) -> None:
        async with self._lock:
            if self._writer is not None and not self._writer.is_closing():
                return
            if self._demux is not None:
                await self._demux
            from tinyassets.storage.outbound_connections import ProxyRequestError

            try:
                self._reader, self._writer = await asyncio.open_unix_connection(
                    os.fspath(self._path))
                if self._verify_peer is not None:
                    try:
                        self._verify_peer(self._writer.get_extra_info("socket"))
                    except BaseException:
                        self._writer.close()
                        self._reader = self._writer = None
                        raise
            except OSError:
                raise ProxyRequestError("the credential broker is unavailable") from None
            self._demux = asyncio.ensure_future(self._demultiplex())

    async def _send(self, frame: bytes) -> None:
        from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome

        writer = self._writer
        try:
            if writer is None or writer.is_closing():
                raise ConnectionResetError("the broker connection is closed")
            writer.write(frame)
            await writer.drain()
        except OSError:
            raise AmbiguousProxyOutcome("the broker connection failed mid-request") from None

    async def _demultiplex(self) -> None:
        from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome

        try:
            while (frame := await rf.read_frame(self._reader)) is not None:
                stream = self._streams.get(frame.stream)
                if stream is None:
                    continue
                if frame.kind == rf.DATA:
                    stream.queue.put_nowait(frame.payload)
                    continue
                document = frame.control()
                if document["op"] == "END":
                    stream.end = document
                    stream.queue.put_nowait(_END)
                    self._streams.pop(frame.stream, None)
                else:
                    stream.queue.put_nowait(document)
        except (OSError, rf.FrameError):
            pass
        finally:
            if self._writer is not None:
                self._writer.close()
            self._writer = self._reader = self._demux = None
            # The connection is gone: every open stream's outcome is unknown.
            for stream in list(self._streams.values()):
                stream.queue.put_nowait(AmbiguousProxyOutcome(
                    "the broker connection failed mid-request"))
            self._streams.clear()

    @contextlib.asynccontextmanager
    async def stream(self, *, grant_id: str, connection_id: str, verb: str,
                     request: dict[str, Any], op_id: str,
                     idle_s: float | None = None,
                     mcp_binding: dict | None = None) -> AsyncIterator[_Stream]:
        from tinyassets.storage.outbound_connections import AmbiguousProxyOutcome

        refresh = (await asyncio.to_thread(self._refresh_factory, grant_id, connection_id)
                   if self._refresh_factory is not None else None)
        await self._connect()
        generation, token = self._fence()
        stream = _Stream(self, next(self._ids), refresh)
        self._streams[stream.id] = stream
        document = {
            "op": "OPEN", "op_id": op_id, "generation": generation, "token": token,
            "principal": self._principal, "command_center": self._command_center,
            "grant_id": grant_id, "connection_id": connection_id, "verb": verb,
            "request": request, "credit": MAX_WINDOW,
        }
        if mcp_binding is not None:
            document["mcp_binding"] = mcp_binding
        if idle_s is not None:
            document["idle_s"] = idle_s
        if refresh is not None:
            document["refresh"] = True
        try:
            await self._send(rf.control(stream.id, document))
        except AmbiguousProxyOutcome:
            self._streams.pop(stream.id, None)
            raise
        try:
            yield stream
        finally:
            if stream.end is None and stream.id in self._streams:
                # Left early or cancelled: the broker stops the upstream.
                with contextlib.suppress(AmbiguousProxyOutcome):
                    await asyncio.shield(self._send(rf.control(stream.id, {"op": "CANCEL"})))

    async def close(self) -> None:
        if self._writer is not None:
            self._writer.close()
        if self._demux is not None:
            demux = self._demux
            demux.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await demux

    async def status(self, op_id: str) -> dict:
        """Reconcile without opening or replaying a stream; never infer success."""
        from tinyassets.broker.ops import canonical_op_id
        from tinyassets.storage.outbound_connections import ProxyRequestError

        op_id = canonical_op_id(op_id)
        writer = None
        try:
            async with asyncio.timeout(30):
                reader, writer = await asyncio.open_unix_connection(os.fspath(self._path))
                if self._verify_peer is not None:
                    self._verify_peer(writer.get_extra_info("socket"))
                generation, token = self._fence()
                writer.write(rf.control(rf.CONNECTION, {
                    "op": "STATUS", "principal": self._principal,
                    "command_center": self._command_center, "op_id": op_id,
                    "generation": generation, "token": token,
                }))
                await writer.drain()
                frame = await rf.read_frame(reader)
                if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
                    raise rf.FrameError("invalid status response")
                answer = frame.control()
                if (answer.get("op") != "STATUS_IS" or answer.get("op_id") != op_id
                        or answer.get("side_effect_state") not in {"none", "unknown"}):
                    raise rf.FrameError("invalid status response")
                return answer
        except (OSError, TimeoutError, rf.FrameError):
            raise ProxyRequestError("broker operation status unavailable") from None
        finally:
            if writer is not None:
                writer.close()
                await writer.wait_closed()
