"""The broker process's server: many streams per connection (I14 decisions 1-4, 6, 7).

One asyncio server on a Unix socket. Each connection is classified by the
connecting process's uid (``SO_PEERCRED``) against the broker's own role map;
an unmapped uid is refused before a frame is read. Frames are
:mod:`tinyassets.rpc_frames`.

A stream's life, for an ``OPEN`` on an owner channel:

1. the frame is checked for the role (an owner names principal and command
   center; a box channel is not served yet and is refused);
2. the grant is authorized with ``ConnectionLedger.authorize_exact``, the same
   checks ``resolve_exact_scoped_proxy`` runs, as that principal;
3. the stream's ``(generation, token)`` must equal the persisted fence;
4. the operation is admitted in the op store (namespace = principal and
   command center, bound to a digest of the request). A known operation is
   never sent again: the stream ends ``refused``/``duplicate`` carrying the
   operation's recorded state;
5. on the stream's thread: ``may_have_sent`` is written durably and
   ``ADMITTED`` sent, then the request leaves -- every network send (the
   first, an OAuth resend) under the fence's send lock with the stream's
   cancellation re-checked immediately before it;
6. ``HEAD``, then ``DATA`` within the caller's credit, then ``END``.

``side_effect_state`` is the OPERATION's: ``none`` only when the broker can
show no byte of it was ever sent (no record, or a record that never reached
``may_have_sent``), ``unknown`` otherwise. ``stream_sent`` says whether THIS
stream wrote.

Bounds: credit at most ``MAX_WINDOW`` per stream, at most ``LOOKAHEAD`` read
past it, an absolute deadline over the whole stream (admission to ``END``,
credit waits included), and a per-connection output queue of at most
``MAX_QUEUED_FRAMES`` frames whose producers block when it is full.

The upstream exchange is the hardened synchronous driver, so each live stream
runs it on a thread of its own: a waiting stream costs that thread and its
window in the broker (an async upstream is a later step, measured first).
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import socket
import struct
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from tinyassets import rpc_frames as rf
from tinyassets.broker.fence import Fence, Fenced
from tinyassets.broker.ops import OpIdInvalid, OpStore
from tinyassets.storage.outbound_connections import BrokerStreamStop

_LOG = logging.getLogger(__name__)

OWNER = "owner"
BOX = "box"
MAX_WINDOW = 256 * 1024
LOOKAHEAD = 16 * 1024
MAX_STREAMS = 4096
MAX_QUEUED_FRAMES = 64
#: Ordinary and longest per-request budgets (the driver's own) plus room for
#: the one OAuth resend; the stream's absolute deadline is drawn from these.
ORDINARY_BUDGET_S = 30.0
MAX_BUDGET_S = 600.0
RESEND_GRACE_S = 30.0
#: The longest one upstream read may wait when the caller named no idle bound:
#: also how long a cancelled stream's thread may take to notice.
DEFAULT_IDLE_S = 30.0
#: How long a final END may wait on a full queue for a peer that stopped reading.
END_WAIT_S = 5.0
#: The fixed, secret-free error classes an END may carry.
ERROR_CLASSES = frozenset({
    "PermissionError", "GrantResolutionError", "AmbiguousProxyOutcome",
    "OutboundDeadlineExceeded", "ConnectionAuthorizationError", "ProxyRequestError",
    "SsrfValidationError", "fenced", "duplicate", "expired", "refused",
    "InferenceUsageStopped", "InferenceUsageRequired", "ProviderAuthorityHeldError",
})


def peer_uid(sock: socket.socket) -> int:
    """The connecting process's uid, from the kernel. Linux only; fails closed elsewhere."""
    option = getattr(socket, "SO_PEERCRED", None)
    if option is None:
        raise PermissionError("peer credentials are unavailable on this host")
    _pid, uid, _gid = struct.unpack("3i", sock.getsockopt(socket.SOL_SOCKET, option,
                                                         struct.calcsize("3i")))
    return uid


def request_digest(*, grant_id: str, connection_id: str, verb: str, request: Any) -> str:
    """The request's identity for the op record: everything that shapes the effect.

    Transport options (``reply_budget_s``) are excluded; the rest of the request
    document (url, headers, ``header_name``, body) is canonical JSON.
    """
    document = dict(request) if isinstance(request, dict) else {"request": request}
    document.pop("reply_budget_s", None)
    canonical = json.dumps(
        {"grant": grant_id, "connection": connection_id, "verb": str(verb).upper(),
         "request": document},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str,
    )
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _stream_budget(request: dict[str, Any]) -> float:
    asked = request.get("reply_budget_s")
    # Clamp before converting: float() of an enormous int overflows.
    if type(asked) is int and asked > ORDINARY_BUDGET_S:
        return float(min(asked, int(MAX_BUDGET_S))) + RESEND_GRACE_S
    if type(asked) is float and asked == asked and asked > ORDINARY_BUDGET_S:
        return min(asked, MAX_BUDGET_S) + RESEND_GRACE_S
    return ORDINARY_BUDGET_S + RESEND_GRACE_S


@dataclass
class _Stream:
    id: int
    generation: int
    token: str
    namespace: str
    op_id: str
    deadline: float
    credit: int = 0
    cancelled: bool = False
    #: ``may_have_sent`` is durable for the operation (conservative).
    marked: bool = False
    #: This stream reached a network write (a guarded send began).
    wrote: bool = False
    upstream: Any = None
    wake: threading.Condition = field(default_factory=threading.Condition)
    refresh_sequence: int = 0
    refresh_result: bool | None = None
    refresh_pending: bool = False


class BrokerServer:
    def __init__(self, *, ledger_for: Callable[[str], Any],
                 dispatch_for: Callable[..., Callable[..., Any]],
                 ops: OpStore, fence: Fence, roles: Mapping[int, str],
                 uid_of: Callable[[socket.socket], int] = peer_uid,
                 owner_identities: Any = None) -> None:
        self._ledger_for = ledger_for
        self._dispatch_for = dispatch_for
        self._ops = ops
        self._fence = fence
        self._roles = dict(roles)
        self._uid_of = uid_of
        self._owner_identities = owner_identities
        self._streams: dict[tuple[int, int], _Stream] = {}
        self._streams_lock = threading.Lock()
        self._ops.recover()

    async def serve(self, path: Path) -> asyncio.AbstractServer:
        return await asyncio.start_unix_server(self._connection, path=str(path))

    async def _connection(self, reader: asyncio.StreamReader,
                          writer: asyncio.StreamWriter) -> None:
        sock = writer.get_extra_info("socket")
        try:
            role = self._roles.get(self._uid_of(sock))
        except Exception:  # noqa: BLE001 - no identity, no service
            role = None
        if role is None:
            writer.close()
            return
        connection = _Connection(self, writer, role)
        pump = asyncio.ensure_future(connection.pump())
        handler = asyncio.current_task()

        def pump_ended(task: asyncio.Task) -> None:
            # A write that failed means the peer is gone: tear the connection
            # down even if its handler is waiting on a full queue.
            if not task.cancelled() and task.exception() is not None and handler is not None:
                handler.cancel()

        pump.add_done_callback(pump_ended)
        try:
            while (frame := await rf.read_frame(reader)) is not None:
                await connection.handle(frame)
        except rf.FrameError:
            _LOG.warning("broker peer broke the framing; dropping it")
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            connection.abandon()
            pump.cancel()
            writer.close()

    def _cancel_older(self, generation: int) -> None:
        with self._streams_lock:
            older = [s for s in self._streams.values() if s.generation < generation]
        for stream in older:
            self.cancel(stream)

    def _close_older(self, generation: int) -> None:
        self._cancel_older(generation)

    @staticmethod
    def cancel(stream: _Stream) -> None:
        """Never blocks, never touches a socket: marks the stream cancelled.

        The stream's own thread stops at its next check: before any send
        (guard), at connect before the write, and between body reads -- each
        read bounded by the idle timeout. Acting on sockets from another thread
        raced their lifetime, so cancellation is a flag with a bound, not an abort.
        """
        with stream.wake:
            stream.cancelled = True
            stream.wake.notify_all()


class _Connection:
    """One peer: its streams, and one bounded output queue drained by a pump."""

    def __init__(self, server: BrokerServer, writer: asyncio.StreamWriter, role: str) -> None:
        self._server = server
        self._writer = writer
        self._role = role
        self._loop = asyncio.get_running_loop()
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(MAX_QUEUED_FRAMES)
        self._closed = threading.Event()
        self._key = id(self)

    async def pump(self) -> None:
        while True:
            frame = await self._queue.get()
            self._writer.write(frame)
            await self._writer.drain()

    def send(self, frame: bytes | list[bytes], stream: _Stream | None = None,
             *, final: bool = False) -> None:
        """From a stream thread: queue frames, blocking while the queue is full.

        A stream's producer stops waiting when the peer is gone, or when the
        stream is cancelled or past its deadline. Its final END waits at most
        ``END_WAIT_S`` for a peer that has stopped reading, then is dropped.
        """
        for item in frame if isinstance(frame, list) else [frame]:
            if self._closed.is_set():
                return
            future = asyncio.run_coroutine_threadsafe(self._queue.put(item), self._loop)
            started = time.monotonic()
            while True:
                try:
                    future.result(timeout=0.25)
                    break
                except TimeoutError:
                    if self._closed.is_set():
                        future.cancel()
                        return
                    if final and time.monotonic() - started > END_WAIT_S:
                        future.cancel()
                        return
                    if stream is not None and not final:
                        if stream.cancelled:
                            future.cancel()
                            raise _Cancelled
                        if time.monotonic() >= stream.deadline:
                            future.cancel()
                            raise _Expired

    async def send_async(self, frame: bytes) -> None:
        await self._queue.put(frame)

    async def handle(self, frame: rf.Frame) -> None:
        if frame.kind == rf.DATA:
            return  # request bodies are inline in v1; stray data is ignored
        doc = frame.control()
        op = doc["op"]
        if frame.stream == rf.CONNECTION:
            await self._connection_op(op, doc)
            return
        with self._server._streams_lock:
            stream = self._server._streams.get((self._key, frame.stream))
        if op == "OPEN":
            if stream is not None:
                raise rf.FrameError("stream id reused while open")
            await self._open(frame.stream, doc)
        elif op == "CREDIT" and stream is not None:
            amount = doc.get("n")
            if type(amount) is int and amount > 0:
                with stream.wake:
                    stream.credit = min(stream.credit + amount, MAX_WINDOW)
                    stream.wake.notify_all()
        elif op == "CANCEL" and stream is not None:
            await asyncio.to_thread(self._server.cancel, stream)
        elif op == "REFRESH_ACK" and stream is not None:
            with stream.wake:
                if (set(doc) != {"op", "sequence", "ok"}
                        or type(doc["sequence"]) is not int or type(doc["ok"]) is not bool
                        or not stream.refresh_pending
                        or doc["sequence"] != stream.refresh_sequence
                        or stream.refresh_result is not None):
                    raise rf.FrameError("invalid refresh acknowledgement")
                stream.refresh_result = doc["ok"]
                stream.wake.notify_all()

    async def _connection_op(self, op: str, doc: dict[str, Any]) -> None:
        if self._role != OWNER:
            raise rf.FrameError("only the owner channel may send connection operations")
        if op == "OWNER_IDENTITY":
            answer = await asyncio.to_thread(self._owner_identity, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "LEDGER_QUERY":
            answer = await asyncio.to_thread(self._ledger_query, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "ERASE_ACCOUNT":
            answer = await asyncio.to_thread(self._erase_account, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "USAGE":
            answer = await asyncio.to_thread(self._usage, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "HTTP_CONNECT":
            answer = await asyncio.to_thread(self._http_connect, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "HTTP_POLICY":
            answer = await asyncio.to_thread(self._http_policy, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "DISCONNECT":
            answer = await asyncio.to_thread(self._disconnect, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "CAPABILITY":
            answer = await asyncio.to_thread(self._capability, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "CONNECTION_CATALOG":
            answer = await asyncio.to_thread(self._connection_catalog, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "FENCE":
            try:
                generation, token = await asyncio.to_thread(
                    self._server._fence.barrier, doc.get("generation"), doc.get("proof"),
                    cancel_older=self._server._cancel_older,
                    close_older=self._server._close_older,
                )
            except Fenced:
                await self.send_async(rf.control(rf.CONNECTION, {"op": "FENCE_REFUSED"}))
                return
            await self.send_async(rf.control(rf.CONNECTION, {
                "op": "FENCE_ACK", "generation": generation, "token": token}))
        elif op == "STATUS":
            namespace = _namespace(doc.get("principal"), doc.get("command_center"))
            state, effect = await asyncio.to_thread(
                self._operation_state, namespace, str(doc.get("op_id", "")))
            await self.send_async(rf.control(rf.CONNECTION, {
                "op": "STATUS_IS", "op_id": doc.get("op_id"), "state": state,
                "side_effect_state": effect}))

    def _owner_identity(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.owner_identities import validate_principal

        try:
            if set(doc) != {"op", "principal", "allocate", "generation", "token"}:
                raise ValueError("unsupported identity fields")
            validate_principal(doc["principal"])
            if (type(doc["allocate"]) is not bool or type(doc["generation"]) is not int
                    or not isinstance(doc["token"], str)):
                raise ValueError("invalid identity request")
            with self._server._fence.send(doc["generation"], doc["token"]):
                if self._server._owner_identities is None:
                    raise RuntimeError("owner identities are not initialized")
                identity = self._server._owner_identities.resolve(
                    doc["principal"], allocate=doc["allocate"])
                return {"op": "OWNER_IDENTITY_IS", "uid": identity.uid, "gid": identity.gid}
        except Exception:  # noqa: BLE001 - no identity, path or persisted state on refusal
            return {"op": "OWNER_IDENTITY_REFUSED"}

    def _erase_account(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.account_erasure import local_erase, validate

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token"}:
                raise ValueError("unsupported erasure fields")
            validate(doc["principal"], doc["command_center"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                counts = local_erase(self._server._ledger_for(doc["principal"]),
                                     principal=doc["principal"],
                                     command_center=doc["command_center"])
                return {"op": "ACCOUNT_ERASED", "counts": counts}
        except Exception:  # noqa: BLE001 - no persisted values on the wire
            return {"op": "ACCOUNT_ERASURE_REFUSED"}

    def _ledger_query(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.ledger_queries import local_query, validate_query

        try:
            if set(doc) != {"op", "query", "principal", "command_center", "grant_id",
                            "connection_id", "generation", "token"}:
                raise ValueError("unsupported ledger query fields")
            validate_query(doc["query"], doc["principal"], doc["command_center"],
                           doc["grant_id"], doc["connection_id"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            # Authenticate before even constructing the private ledger. Hold
            # the generation across the transaction, just as for an egress send.
            with self._server._fence.send(doc["generation"], doc["token"]):
                ledger = self._server._ledger_for(doc["principal"])
                result = local_query(
                    ledger, query=doc["query"], principal=doc["principal"],
                    command_center=doc["command_center"], grant_id=doc["grant_id"],
                    connection_id=doc["connection_id"],
                )
                answer = {"op": "LEDGER_RESULT", "result": result}
                # Validate the bounded wire representation in the worker, so
                # malformed persisted data cannot terminate the server handler.
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception as exc:  # noqa: BLE001 - no paths, SQL or values on the wire
            error = "fenced" if isinstance(exc, Fenced) else (
                "GrantResolutionError" if type(exc).__name__ == "GrantResolutionError"
                else "refused")
            return {"op": "LEDGER_REFUSED", "error_class": error}

    def _connection_catalog(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.catalog import local_page, validate

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "cursor", "limit"}:
                raise ValueError("unsupported catalog fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["cursor"], doc["limit"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                result = local_page(self._server._ledger_for(doc["principal"]),
                                    principal=doc["principal"],
                                    command_center=doc["command_center"],
                                    cursor=doc["cursor"], limit=doc["limit"])
                answer = {"op": "CATALOG_RESULT", "result": result}
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception:  # noqa: BLE001 - fixed refusal, no persisted values on wire
            return {"op": "CATALOG_REFUSED"}

    def _disconnect(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.disconnect import local_operation, validate

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "document"}:
                raise ValueError("unsupported disconnect fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["document"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                result = local_operation(self._server._ledger_for(doc["principal"]),
                                    principal=doc["principal"],
                                    command_center=doc["command_center"],
                                    document=doc["document"])
                answer = {"op": "DISCONNECT_RESULT", "result": result}
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception as exc:  # noqa: BLE001 - fixed refusal, no persisted values on wire
            return {"op": "DISCONNECT_REFUSED", "error_class":
                    "GrantResolutionError" if type(exc).__name__ == "GrantResolutionError"
                    else "refused"}

    def _http_policy(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.http_policy import local_operation, validate

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "document"}:
                raise ValueError("unsupported policy fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["document"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                result = local_operation(self._server._ledger_for(doc["principal"]),
                                    principal=doc["principal"],
                                    command_center=doc["command_center"],
                                    document=doc["document"])
                answer = {"op": "HTTP_POLICY_RESULT", "result": result}
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception as exc:  # noqa: BLE001 - fixed refusal, no persisted values on wire
            return {"op": "HTTP_POLICY_REFUSED", "error_class":
                    "GrantResolutionError" if type(exc).__name__ == "GrantResolutionError"
                    else "refused"}

    def _usage(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.usage import local_operation, validate
        from tinyassets.request_budget import RequestBudgetExceeded

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "usage_id", "document"}:
                raise ValueError("unsupported accounting fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["document"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                try:
                    result = local_operation(self._server._ledger_for(doc["principal"]),
                                             principal=doc["principal"],
                                             command_center=doc["command_center"],
                                             usage_id=doc["usage_id"], document=doc["document"])
                    answer = {"op": "USAGE_RESULT", "result": result}
                except RequestBudgetExceeded as exc:
                    answer = {"op": "USAGE_STOPPED", "reason": exc.reason,
                              "receipt": exc.request_receipt}
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception:  # noqa: BLE001 - fixed refusal, no storage details on wire
            return {"op": "USAGE_REFUSED"}

    def _http_connect(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.http_connect import local_operation, validate

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "document"}:
                raise ValueError("unsupported connect fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["document"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                result = local_operation(self._server._ledger_for(doc["principal"]),
                                    principal=doc["principal"],
                                    command_center=doc["command_center"],
                                    document=doc["document"])
                answer = {"op": "HTTP_CONNECT_RESULT", "result": result}
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception as exc:  # noqa: BLE001 - fixed refusal, no persisted values on wire
            return {"op": "HTTP_CONNECT_REFUSED", "error_class":
                    "GrantResolutionError" if type(exc).__name__ == "GrantResolutionError"
                    else "refused"}

    def _capability(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.capabilities import local_operation, validate
        from tinyassets.storage.outbound_connections import (
            MODEL_USE_PRICED_CONFLICT,
            SsrfValidationError,
        )

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "document"}:
                raise ValueError("unsupported capability fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["document"])
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                result = local_operation(self._server._ledger_for(doc["principal"]),
                                         principal=doc["principal"],
                                         command_center=doc["command_center"],
                                         document=doc["document"])
                answer = {"op": "CAPABILITY_RESULT", "result": result}
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception as exc:  # noqa: BLE001 - no persisted values or secrets on the wire
            if isinstance(exc, Fenced):
                error = "fenced"
            elif isinstance(exc, SsrfValidationError):
                error = "endpoint"
            elif type(exc).__name__ == "GrantResolutionError":
                error = "GrantResolutionError"
            elif isinstance(exc, LookupError):
                error = "lookup"
            elif isinstance(exc, ValueError):
                error = "priced_conflict" if str(exc) == MODEL_USE_PRICED_CONFLICT else "invalid"
            else:
                error = "refused"
            return {"op": "CAPABILITY_REFUSED", "error_class": error}

    def _operation_state(self, namespace: str, op_id: str) -> tuple[str, str]:
        """``(state, side_effect_state)`` of an operation, never ``none`` on a guess."""
        try:
            record = self._server._ops.status(namespace, op_id)
        except OpIdInvalid:
            return "invalid", "none"  # never admissible, so never sent
        if record == "not_found":
            return "not_found", "none"
        if record == "expired":
            return "expired", "unknown"
        return record.state, "unknown" if record.sent else "none"

    async def _open(self, stream_id: int, doc: dict[str, Any]) -> None:
        principal, command_center = doc.get("principal"), doc.get("command_center")
        op_id = doc.get("op_id")

        async def refuse(error_class: str) -> None:
            effect = "unknown"
            if self._role == OWNER and isinstance(op_id, str):
                try:
                    namespace = _namespace(principal, command_center)
                    _, effect = await asyncio.to_thread(self._operation_state, namespace, op_id)
                except rf.FrameError:
                    effect = "none"  # no namespace: no operation it could name
            await self.send_async(rf.control(stream_id, {
                "op": "END", "outcome": "refused", "error_class": error_class,
                "stream_sent": False, "side_effect_state": effect}))

        if self._role != OWNER:
            await refuse("refused")  # the box channel's derivation lands with boxhostd
            return
        with self._server._streams_lock:
            crowded = len(self._server._streams) >= MAX_STREAMS
        if crowded:
            await refuse("refused")
            return
        grant_id, connection_id = doc.get("grant_id"), doc.get("connection_id")
        verb, request = doc.get("verb"), doc.get("request")
        generation, token = doc.get("generation"), doc.get("token")
        if not all(isinstance(v, str) and v for v in
                   (principal, command_center, grant_id, connection_id, verb, op_id)) \
                or not isinstance(request, dict) or type(generation) is not int \
                or not isinstance(token, str):
            await refuse("refused")
            return
        try:
            ledger = self._server._ledger_for(principal)
            _grant, resource = await asyncio.to_thread(
                ledger.authorize_exact, universe_id=command_center, grant_id=grant_id,
                connection_id=connection_id,
            )
        except Exception as exc:  # noqa: BLE001 - mapped to a fixed class
            name = type(exc).__name__
            await refuse(name if name in ERROR_CLASSES else "GrantResolutionError")
            return
        if not self._server._fence.admits(generation, token):
            await refuse("fenced")
            return
        namespace = _namespace(principal, command_center)
        digest = request_digest(grant_id=grant_id, connection_id=connection_id, verb=verb,
                                request=request)
        credit = doc.get("credit")
        credit = min(max(credit, 0), MAX_WINDOW) if type(credit) is int else 0

        def admit_and_start() -> str | None:
            """One unit, run to the end even if the handler is cancelled: an
            operation is never left reserved with no stream to settle it."""
            try:
                admission = self._server._ops.admit(namespace, op_id, digest)
            except OpIdInvalid:
                return "refused"
            if admission.kind != "new":
                return {"expired": "expired", "existing": "duplicate",
                        "mismatch": "duplicate"}.get(admission.kind, "refused")
            if self._closed.is_set():
                self._server._ops.finish(namespace, op_id, "refused")
                return "refused"
            stream = _Stream(stream_id, generation, token, namespace, op_id,
                             deadline=time.monotonic() + _stream_budget(request),
                             credit=credit)
            with self._server._streams_lock:
                self._server._streams[(self._key, stream_id)] = stream
            try:
                dispatch = self._server._dispatch_for(principal, command_center, grant_id,
                                                      resource)
                threading.Thread(
                    target=self._run, args=(stream, dispatch, grant_id, verb, request,
                                            doc.get("idle_s"), doc.get("inference_usage"),
                                            doc.get("refresh") is True),
                    name=f"broker-stream-{stream_id}", daemon=True,
                ).start()
            except Exception:  # noqa: BLE001 - nothing was sent: settle it as refused
                with self._server._streams_lock:
                    self._server._streams.pop((self._key, stream_id), None)
                self._server._ops.finish(namespace, op_id, "refused")
                return "refused"
            return None

        refused = await asyncio.shield(asyncio.to_thread(admit_and_start))
        if refused is not None:
            await refuse(refused)

    def _checkpoint(self, stream: _Stream) -> None:
        if stream.cancelled:
            raise _Cancelled
        if time.monotonic() >= stream.deadline:
            raise _Expired
        if not self._server._fence.admits(stream.generation, stream.token):
            raise _Fenced

    @contextlib.contextmanager
    def _guard(self, stream: _Stream) -> Iterator[None]:
        """Held across each network send: fence and cancellation re-checked first."""
        with self._server._fence.send(stream.generation, stream.token):
            self._checkpoint(stream)
            yield

    def _connected(self, stream: _Stream, sock: Any) -> None:
        """Connected, the request not yet written: everything re-checked once
        more (name resolution may have taken the deadline, or a cancel arrived).
        Raising here aborts the exchange before its first byte; past this point
        the stream has written."""
        self._checkpoint(stream)
        stream.wrote = True

    def _refresh(self, stream: _Stream, destination: str, rejected_digest: str) -> None:
        from tinyassets.storage.outbound_connections import ConnectionAuthorizationError

        with stream.wake:
            self._checkpoint(stream)
            stream.refresh_sequence += 1
            stream.refresh_result = None
            stream.refresh_pending = True
        try:
            self.send(rf.control(stream.id, {
                "op": "REFRESH", "sequence": stream.refresh_sequence,
                "destination": destination, "rejected_digest": rejected_digest}), stream)
            with stream.wake:
                while stream.refresh_result is None:
                    self._checkpoint(stream)
                    stream.wake.wait(min(0.1, max(0, stream.deadline - time.monotonic())))
                self._checkpoint(stream)
                if not stream.refresh_result:
                    raise ConnectionAuthorizationError("daemon refresh failed")
        finally:
            with stream.wake:
                stream.refresh_pending = False

    def _run(self, stream: _Stream, dispatch: Callable[..., Any], grant_id: str, verb: str,
             request: dict[str, Any], idle_s: Any, inference_usage=None,
             refresh_enabled: bool = False) -> None:
        outcome, error_class, extra = "failed", "ProxyRequestError", {}
        try:
            if stream.cancelled:
                raise _Cancelled
            self._server._ops.mark_may_have_sent(stream.namespace, stream.op_id)
            stream.marked = True
            self.send(rf.control(stream.id, {"op": "ADMITTED", "op_id": stream.op_id}),
                      stream)
            upstream = dispatch(
                grant_id, verb, request, stream=True,
                idle_s=(min(idle_s, DEFAULT_IDLE_S)
                        if type(idle_s) in (int, float) and idle_s > 0 else DEFAULT_IDLE_S),
                guard=lambda: self._guard(stream),
                on_connect=lambda sock: self._connected(stream, sock),
                checkpoint=lambda: self._checkpoint(stream),
                deadline_at=stream.deadline,
                **({"refresh_request": lambda destination, rejected: self._refresh(
                    stream, destination, rejected)} if refresh_enabled else {}),
                **({"inference_usage": inference_usage, "operation_id": stream.op_id}
                   if inference_usage is not None else {}),
            )
            stream.upstream = upstream
            if stream.cancelled:
                raise _Cancelled
            self.send(rf.control(stream.id, {
                "op": "HEAD", "status": upstream.status, "reason": upstream.reason,
                "headers": upstream.headers, "redirect_count": upstream.redirect_count,
            }), stream)
            self._pump_body(stream, upstream)
            outcome, error_class = "completed", None
        except _Cancelled:
            outcome, error_class = "cancelled", None
        except (_Expired, TimeoutError):
            outcome, error_class = "failed", "OutboundDeadlineExceeded"
        except (Fenced, _Fenced):
            outcome, error_class = "cancelled", "fenced"
        except Exception as exc:  # noqa: BLE001 - mapped to a fixed class
            if stream.cancelled:
                # Shutting the socket down is how a cancel unblocks a read; the
                # read's own error is that, not a destination failure.
                outcome, error_class = "cancelled", None
            else:
                from tinyassets.request_budget import RequestBudgetExceeded

                name = type(exc).__name__
                error_class = name if name in ERROR_CLASSES else "ProxyRequestError"
                if isinstance(exc, RequestBudgetExceeded):
                    error_class = "InferenceUsageStopped"
                    extra = {"reason": exc.reason,
                             "usage_id": exc.request_receipt.get("usage_id")}
                failure = getattr(exc, "failure", None)
                if name == "ConnectionAuthorizationError" and isinstance(failure, dict):
                    extra = {"failure": failure}
        finally:
            if stream.upstream is not None and outcome != "completed":
                stream.upstream.close()
            with self._server._streams_lock:
                self._server._streams.pop((self._key, stream.id), None)
            try:
                self._server._ops.finish(stream.namespace, stream.op_id,
                                         outcome if stream.marked else "refused")
            except Exception:  # noqa: BLE001 - the record keeps may_have_sent: unknown
                _LOG.warning("could not record the end of operation %s", stream.op_id)
            self.send(rf.control(stream.id, {
                "op": "END", "outcome": outcome, "error_class": error_class,
                "stream_sent": stream.wrote,
                # The operation's state: may_have_sent was durable, so unknown.
                "side_effect_state": "unknown" if stream.marked else "none", **extra,
            }), stream, final=True)

    def _pump_body(self, stream: _Stream, upstream: Any) -> None:
        buffered = bytearray()
        finished = False
        while True:
            with stream.wake:
                while True:
                    if stream.cancelled:
                        raise _Cancelled
                    if (buffered and stream.credit > 0) or (finished and not buffered) \
                            or (not finished and len(buffered) < LOOKAHEAD + stream.credit):
                        break
                    remaining = stream.deadline - time.monotonic()
                    if remaining <= 0:
                        raise _Expired
                    stream.wake.wait(remaining)
                credit = stream.credit
            if buffered and credit > 0:
                piece = bytes(buffered[:credit])
                del buffered[:len(piece)]
                with stream.wake:
                    stream.credit -= len(piece)
                self.send(rf.data(stream.id, piece), stream)
                continue
            if finished:
                return
            if time.monotonic() >= stream.deadline:
                raise _Expired
            chunk = upstream.read(LOOKAHEAD + credit - len(buffered))
            if chunk is None:
                finished = True
            else:
                buffered += chunk

    def abandon(self) -> None:
        """The peer went away: stop queueing, cancel every stream it owned."""
        self._closed.set()
        with self._server._streams_lock:
            mine = [s for (key, _), s in self._server._streams.items() if key == self._key]
        for stream in mine:
            self._server.cancel(stream)


class _Cancelled(BrokerStreamStop):
    pass


class _Expired(BrokerStreamStop):
    pass


class _Fenced(BrokerStreamStop):
    pass


def _namespace(principal: Any, command_center: Any) -> str:
    if not isinstance(principal, str) or not isinstance(command_center, str) \
            or not principal or not command_center or "|" in principal:
        raise rf.FrameError("a namespace needs a principal and a command center")
    return f"{principal}|{command_center}"
