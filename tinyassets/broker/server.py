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
import os
import socket
import sqlite3
import struct
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from tinyassets import rpc_frames as rf
from tinyassets.broker.fence import Fence, Fenced
from tinyassets.broker.ops import OpIdInvalid, OpStore
from tinyassets.storage.outbound_connections import BrokerStreamStop

_LOG = logging.getLogger(__name__)

OWNER = "owner"
BOX = "box"
BOX_HOST = "box_host"
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
    "GrantPathRefused",
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

    Transport options are excluded; the rest of the request
    document (url, headers, ``header_name``, body) is canonical JSON.
    """
    document = dict(request) if isinstance(request, dict) else {"request": request}
    document.pop("reply_budget_s", None)
    document.pop("reply_idle_s", None)
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
    cancel_socket: Any = None
    upload: Any = None
    authority: Any = None
    wake: threading.Condition = field(default_factory=threading.Condition)


class BrokerServer:
    def __init__(self, *, ledger_for: Callable[[str], Any],
                 dispatch_for: Callable[..., Callable[..., Any]],
                 ops: OpStore, fence: Fence, roles: Mapping[int, str],
                 uid_of: Callable[[socket.socket], int] = peer_uid,
                 owner_identities: Any = None, policy_store: Any = None,
                 provision_box: Callable[[str, str], None] | None = None,
                 data_root: Path | None = None) -> None:
        self._ledger_for = ledger_for
        self._dispatch_for = dispatch_for
        self._ops = ops
        self._fence = fence
        self._roles = dict(roles)
        self._uid_of = uid_of
        self._owner_identities = owner_identities
        self._data_root = data_root
        self._policy_store = policy_store
        self._provision_box = provision_box
        self._data_root = data_root
        from tinyassets.broker.public_egress import PublicEgress

        self._public_egress = PublicEgress(owner_identities, data_root)
        self._connections = set()
        self._streams: dict[tuple[int, int], _Stream] = {}
        self._streams_lock = threading.Lock()
        self._ops.recover()
        self._next_wakes = None
        self._deletion_jobs = None
        self._routing = None

    @property
    def routing(self):
        if self._routing is None:
            from tinyassets.broker.admission import register_authority
            from tinyassets.broker.routing import RoutingAuthority

            self._routing = RoutingAuthority(self)
            register_authority(self._owner_identities.path, self._routing)
        return self._routing

    @property
    def deletion_jobs(self):
        if self._deletion_jobs is None:
            from tinyassets.broker.deletion_jobs import DeletionJobs

            self._deletion_jobs = DeletionJobs(self)
        return self._deletion_jobs

    @property
    def next_wakes(self):
        if self._next_wakes is None:
            from tinyassets.broker.next_wake import NextWakeIndex

            self._next_wakes = NextWakeIndex(self._owner_identities)
        return self._next_wakes

    async def serve(self, path: Path) -> asyncio.AbstractServer:
        self._loop = asyncio.get_running_loop()
        return await asyncio.start_unix_server(self._connection, path=str(path), limit=16384)

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
        self._connections.add(connection)
        connection._handler = handler

        def pump_ended(task: asyncio.Task) -> None:
            # A write that failed means the peer is gone: tear the connection
            # down even if its handler is waiting on a full queue.
            if not task.cancelled() and task.exception() is not None and handler is not None:
                handler.cancel()

        pump.add_done_callback(pump_ended)
        try:
            while (frame := await rf.read_frame(reader)) is not None:
                if (connection._role == BOX_HOST and frame.kind == rf.CONTROL
                        and frame.stream == rf.CONNECTION
                        and frame.control()["op"] == "BOX_PUBLIC"):
                    await self._public_egress.serve(reader, writer, frame.control())
                    break
                await connection.handle(frame)
        except rf.FrameError:
            _LOG.warning("broker peer broke the framing; dropping it")
        except (ConnectionError, OSError, asyncio.CancelledError):
            pass
        finally:
            self._connections.discard(connection)
            connection.abandon()
            pump.cancel()
            await asyncio.gather(pump, return_exceptions=True)
            writer.close()

    def close_owner(self, principal):
        self._public_egress.close_owner(principal)
        for connection in tuple(self._connections):
            if principal in connection._principals:
                connection.close()

    async def withdraw_channels(self, principal):
        """C5: acknowledge closure on the event loop that owns the channels."""
        closing = [task for task, (owner, _) in self._public_egress.channels.items()
                   if owner == principal]
        closing.extend(c._handler for c in self._connections if principal in c._principals)
        self.close_owner(principal)
        await asyncio.gather(*closing, return_exceptions=True)

    def _cancel_older(self, generation: int) -> None:
        with self._streams_lock:
            older = [s for s in self._streams.values() if s.generation < generation]
        for stream in older:
            self.cancel(stream)

    def _close_older(self, generation: int) -> None:
        self._cancel_older(generation)

    @staticmethod
    def cancel(stream: _Stream) -> None:
        """Cancel reads through a pinned duplicate, never a recycled descriptor."""
        with stream.wake:
            stream.cancelled = True
            if stream.cancel_socket is not None:
                with contextlib.suppress(OSError):
                    stream.cancel_socket.shutdown(socket.SHUT_RDWR)
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
        self._usage_leases = []
        self._principals = set()

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
            with self._server._streams_lock:
                stream = self._server._streams.get((self._key, frame.stream))
            if stream is None or stream.upload is None:
                raise rf.FrameError("unexpected request data")
            stream.upload.put(frame.payload)
            return
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
        elif op == "UPLOAD_END" and stream is not None and stream.upload is not None:
            stream.upload.finish()
        elif op == "CREDIT" and stream is not None:
            amount = doc.get("n")
            if type(amount) is int and amount > 0:
                with stream.wake:
                    stream.credit = min(stream.credit + amount, MAX_WINDOW)
                    stream.wake.notify_all()
        elif op == "CANCEL" and stream is not None:
            await asyncio.to_thread(self._server.cancel, stream)
        elif op not in {"CANCEL", "CREDIT", "UPLOAD_END"}:
            raise rf.FrameError("unknown stream operation")

    async def _connection_op(self, op: str, doc: dict[str, Any]) -> None:
        if (self._role == BOX_HOST and op == "BOX_ADMISSION_ERASE"
                or self._role == OWNER and op == "OWNER_ADMISSION_ERASE"):
            from tinyassets.broker.admission import local_mutate

            try:
                expected = {"op", "principal"} | (
                    {"generation", "token"} if self._role == OWNER else set())
                if set(doc) != expected:
                    raise ValueError("invalid erasure transition")
                if self._role == OWNER and not self._server._fence.admits(
                        doc["generation"], doc["token"]):
                    raise Fenced("invalid fence")
                await asyncio.to_thread(local_mutate, self._server._owner_identities.path,
                                        doc["principal"], "erase")
                closing = [task for task, (owner, _) in self._server._public_egress.channels.items()
                           if owner == doc["principal"]]
                closing.extend(c._handler for c in self._server._connections
                               if doc["principal"] in c._principals)
                self._server.close_owner(doc["principal"])
                await asyncio.gather(*closing, return_exceptions=True)
                answer = {"op": "OWNER_ADMISSION_ERASED"}
            except Exception:
                answer = {"op": "BOX_REFUSED"}
            await self.send_async(rf.control(rf.CONNECTION, answer))
            return
        if self._role == BOX_HOST and doc == {"op": "BOX_VOLUME_PENDING"}:
            from tinyassets.broker.owner_volumes import pending

            result = await asyncio.to_thread(pending, self._server._owner_identities)
            await self.send_async(rf.control(rf.CONNECTION, {
                "op": "BOX_VOLUME_RESULT", "result": result}))
            return
        if self._role == BOX_HOST and op == "BOX_VOLUME":
            from tinyassets.broker.owner_volumes import dispatch

            try:
                result = await asyncio.to_thread(dispatch, self._server._owner_identities, doc)
                answer = {"op": "BOX_VOLUME_RESULT", "result": result}
            except Exception:
                answer = {"op": "BOX_REFUSED"}
            await self.send_async(rf.control(rf.CONNECTION, answer))
            return
        if self._role == BOX_HOST and op == "BOX_RETIRE_ADMISSION":
            answer = await asyncio.to_thread(self._retire_box_admission, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
            return
        if self._role == BOX_HOST and op in {"BOX_CHANNEL", "BOX_SHARED"}:
            # C6: withdrawal also cancels an admission still resolving.
            self._principals.add(doc.get("principal"))
        if self._role in {BOX_HOST, BOX}:
            from tinyassets.broker.admission import require_admitted

            try:
                owner = self._box_owner if self._role == BOX else doc.get("principal")
                if op != "BOX_LIFECYCLE":
                    await asyncio.to_thread(require_admitted, self._server._owner_identities, owner)
            except Exception:
                await self.send_async(rf.control(rf.CONNECTION, {"op": "BOX_REFUSED"}))
                return
        if self._role == BOX_HOST:
            if op == "BOX_LIFECYCLE":
                from tinyassets.broker.admission import OwnerErased
                from tinyassets.broker.box_admission import OwnerSuspended, lifecycle

                try:
                    result = await asyncio.to_thread(lifecycle, self._server._owner_identities, doc)
                    answer = {"op": "BOX_LIFECYCLE_IS", **result}
                except OwnerErased:
                    answer = {"op": "BOX_REFUSED"}
                except LookupError:
                    answer = {"op": "BOX_UNADMITTED"}
                except OwnerSuspended:
                    answer = {"op": "BOX_SUSPENDED"}
                except Exception:
                    answer = {"op": "BOX_REFUSED"}
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op == "BOX_CREDENTIAL":
                answer = await asyncio.to_thread(self._box_credential, doc)
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op not in {"BOX_ADMISSION", "BOX_CHANNEL", "BOX_SHARED"}:
                raise rf.FrameError("box host channel is read-only admission")
            from tinyassets.broker.box_admission import resolve

            try:
                if op == "BOX_SHARED":
                    await asyncio.to_thread(self._server._owner_identities.box_route,
                                            doc["principal"])
                    answer = {"op": "BOX_ADMITTED"}
                else:
                    answer = await asyncio.to_thread(resolve, self._server._owner_identities,
                                                     {**doc, "op": "BOX_ADMISSION"})
                if op in {"BOX_CHANNEL", "BOX_SHARED"}:
                    self._principals.add(doc["principal"])
                    self._box_owner, self._box_center = doc["principal"], doc["center"]
                    self._role = BOX
            except Exception:  # no authority or broker data on a refusal
                answer = {"op": "BOX_REFUSED"}
            await self.send_async(rf.control(rf.CONNECTION, answer))
            return
        if self._role == BOX:
            if op == "NEXT_WAKE":
                try:
                    if set(doc) != {"op", "at"}:
                        raise ValueError("invalid next-wake report")
                    await asyncio.to_thread(self._server.next_wakes.report,
                                            self._box_owner, doc["at"], now=time.time())
                    answer = {"op": "BOX_RESULT", "result": None}
                except Exception:
                    _LOG.exception("next-wake report refused")
                    answer = {"op": "BOX_REFUSED"}
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op == "RETIRE_CENTER":
                answer = await asyncio.to_thread(self._box_retire_center, doc)
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op == "ADMIT_CENTER":
                answer = await asyncio.to_thread(self._box_admit_center, doc)
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op == "BOX_IDENTITY":
                self._box_scope(doc)
                await self.send_async(rf.control(rf.CONNECTION, {
                    "op": "BOX_RESULT", "result": {"principal": self._box_owner}}))
                return
            if op == "OWNER_POLICY":
                if doc.get("action") not in {"read", "receipts", "classify", "decide", "proposal",
                                           "write", "issue_once"}:
                    raise rf.FrameError("unsupported box policy operation")
                if doc["action"] in {"write", "issue_once"} and "session_proof" not in doc:
                    raise rf.FrameError("protected policy changes require shared door proof")
                doc = {**doc, "principal": self._box_owner}
                if doc["action"] in {"classify", "decide", "proposal"}:
                    self._box_scope({})
                    if doc["action"] == "decide":
                        doc["request"] = {**doc["request"], "center": self._box_center}
                    else:
                        doc["center"] = self._box_center
                answer = await asyncio.to_thread(self._owner_policy, doc)
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op == "BOX_PING" and self._usage_leases:
                await self.send_async(rf.control(rf.CONNECTION, {"op": "BOX_PONG"}))
                return
            if op == "BOX_PUBLISH":
                from tinyassets.broker.owner_reads import publish

                try:
                    result = await asyncio.to_thread(
                        publish, self._server._data_root, self._box_owner, doc)
                    answer = {"op": "BOX_RESULT", "result": result}
                    rf.control(rf.CONNECTION, answer)
                except Exception:
                    answer = {"op": "BOX_REFUSED"}
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op == "BOX_READ":
                from tinyassets.broker.owner_reads import PlanMissing, read

                try:
                    await asyncio.to_thread(self._server._owner_identities.box_route,
                                            self._box_owner)
                    result = await asyncio.to_thread(
                        read, self._server._data_root, self._box_owner, doc)
                    answer = {"op": "BOX_RESULT", "result": result}
                    rf.control(rf.CONNECTION, answer)
                except PlanMissing:
                    answer = {"op": "BOX_PLAN_MISSING"}
                except Exception:
                    answer = {"op": "BOX_REFUSED"}
                await self.send_async(rf.control(rf.CONNECTION, answer))
                return
            if op == "USAGE_LEASE":
                await self._usage_lease(doc)
                return
            if op not in {"LEDGER_QUERY", "CONNECTION_CATALOG", "USAGE", "HTTP_CONNECT",
                          "CAPABILITY", "DISCONNECT", "STATUS", "MODEL_SOURCE"}:
                raise rf.FrameError("operation is not available on the box channel")
            doc = self._box_scope(doc)
        elif self._role != OWNER:
            raise rf.FrameError("only the owner channel may send connection operations")
        if op == "DELETION_JOB":
            from tinyassets.broker.deletion_jobs import WithdrawalPending

            try:
                if set(doc) != {"op", "operation", "arguments", "generation", "token"}:
                    raise ValueError("invalid deletion request")
                if not self._server._fence.admits(doc["generation"], doc["token"]):
                    raise Fenced("invalid fence")
                operations = {"enqueue": self._server.deletion_jobs.enqueue,
                              "status": self._server.deletion_jobs.status,
                              "billing_receipt": self._server.deletion_jobs.record_billing}
                result = await asyncio.to_thread(operations[doc["operation"]], **doc["arguments"])
                answer = {"op": "DELETION_JOB_IS", "result": result}
            except WithdrawalPending as pending:
                answer = {"op": "DELETION_JOB_PENDING", "result": pending.progress}
            except Exception:
                _LOG.exception("deletion job request remains pending")
                answer = {"op": "DELETION_JOB_REFUSED"}
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "BILLING_PLAN":
            answer = await asyncio.to_thread(self._billing_plan, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "OWNER_IDENTITY":
            answer = await asyncio.to_thread(self._owner_identity, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "RETIRE_OWNER":
            answer = await asyncio.to_thread(self._retire_owner, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "CENTER_ADMISSION":
            answer = await asyncio.to_thread(self._center_admission, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "ADMITTED_OWNER":
            answer = await asyncio.to_thread(self._admitted_owner, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "SOLE_CENTER":
            answer = await asyncio.to_thread(self._sole_center, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "OWNER_ADMISSION":
            from tinyassets.broker.admission import read

            try:
                if set(doc) != {"op", "principal", "generation", "token"}:
                    raise ValueError("invalid admission query")
                with self._server._fence.send(doc["generation"], doc["token"]):
                    record = read(self._server._owner_identities, doc["principal"])
                answer = {"op": "OWNER_ADMISSION_IS", "state": record.state,
                          "reasons": sorted(record.reasons)}
            except Exception:
                answer = {"op": "OWNER_ADMISSION_REFUSED"}
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op in {"OWNER_BOX_WAKE", "OWNER_BOX_REVOKE"}:
            answer = await asyncio.to_thread(self._owner_lifecycle, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "LEDGER_QUERY":
            answer = await asyncio.to_thread(self._ledger_query, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "MODEL_SOURCE":
            answer = await asyncio.to_thread(self._model_source, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "CLOSE_OWNER_CHANNELS":
            from tinyassets.broker.account_erasure import validate
            from tinyassets.broker.admission import read

            try:
                if set(doc) != {"op", "principal", "command_center", "generation", "token"}:
                    raise ValueError("unsupported closure fields")
                validate(doc["principal"], doc["command_center"])
                if (type(doc["generation"]) is not int or not isinstance(doc["token"], str)
                        or not self._server._fence.admits(doc["generation"], doc["token"])):
                    raise Fenced("invalid fence")
                if (await asyncio.to_thread(
                        read, self._server._owner_identities, doc["principal"])).state != "erased":
                    raise ValueError("owner is not fenced")
                self._server.close_owner(doc["principal"])
                answer = {"op": "OWNER_CHANNELS_CLOSED"}
            except Exception:
                answer = {"op": "OWNER_CHANNELS_REFUSED"}
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "ERASE_ACCOUNT":
            answer = await asyncio.to_thread(self._erase_account, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "USAGE":
            answer = await asyncio.to_thread(self._usage, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "HTTP_CONNECT":
            _namespace(doc.get("principal"), doc.get("command_center"))
            self._principals.add(doc["principal"])
            answer = await asyncio.to_thread(self._http_connect, doc)
            await self.send_async(rf.control(rf.CONNECTION, answer))
        elif op == "OWNER_POLICY":
            answer = await asyncio.to_thread(self._owner_policy, doc)
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
        elif op == "BROWSER_VAULT":
            answer = await asyncio.to_thread(self._browser_vault, doc)
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
        else:
            raise rf.FrameError("unknown connection operation")

    def _box_admit_center(self, doc):
        """A new center belongs to the relay's owner, never a supplied principal."""
        try:
            if set(doc) != {"op", "center"}:
                raise ValueError("invalid center admission")
            identities = self._server._owner_identities
            with self._server._fence.send(
                    self._server._fence.generation, self._server._fence.token):
                identities.box_route(self._box_owner)
                row = identities.admission("admit", self._box_owner, doc["center"])
            return {"op": "BOX_RESULT", "result": {
                "generation": row.generation, "machine": row.machine}}
        except Exception:  # noqa: BLE001 - no identity or persisted state on refusal
            return {"op": "BOX_REFUSED"}

    def _stop_owner_box(self, owner, uid):
        from tinyassets.boxes.daemon_control import request

        if request("retire", owner) != {"stopped": True}:
            raise RuntimeError("boxhostd did not acknowledge retirement")

    def _box_retire_center(self, doc):
        try:
            if set(doc) != {"op", "center"}:
                raise ValueError("invalid retirement")
            row = self._server._owner_identities.admission(
                "retire", self._box_owner, doc["center"])
            self._stop_owner_box(self._box_owner, row.machine)
            return {"op": "BOX_RESULT", "result": {
                "generation": row.generation, "machine": row.machine}}
        except Exception:
            return {"op": "BOX_REFUSED"}

    def _box_scope(self, doc):
        from tinyassets.broker.admission import require_admitted

        try:
            require_admitted(self._server._owner_identities, self._box_owner)
        except (PermissionError, LookupError) as exc:
            raise rf.FrameError("erased box admission") from exc
        # The socket selected this scope. Guest identity fields grant nothing.
        if (not getattr(self, "_box_center", None)
                or doc.get("principal", self._box_owner) != self._box_owner
                or doc.get("command_center", self._box_center) != self._box_center):
            raise rf.FrameError("retired box admission")
        return {**doc, "principal": self._box_owner, "command_center": self._box_center,
                "generation": self._server._fence.generation,
                "token": self._server._fence.token}

    async def _usage_lease(self, doc):
        import secrets

        from tinyassets.process_liveness import hold_liveness

        self._box_scope(doc)
        usage_id = doc.get("usage_id")
        if (self._usage_leases or set(doc) != {"op", "usage_id"}
                or not isinstance(usage_id, str) or len(usage_id) != 32
                or any(c not in "0123456789abcdef" for c in usage_id)):
            raise rf.FrameError("invalid box usage lease")
        ledger = self._server._ledger_for(self._box_owner)
        token, parent = "box_" + secrets.token_hex(16), "request_" + usage_id
        try:
            for name in (token, parent):
                # This opens a nonblocking flock. Do not detach acquisition into
                # a worker: cancellation could discard its returned descriptor.
                self._usage_leases.append(hold_liveness(ledger._data_root, name))
            await self.send_async(rf.control(rf.CONNECTION, {
                "op": "BOX_RESULT", "result": {"owner_token": token, "parent_token": parent}}))
        except Exception:
            self.abandon()
            raise

    def _box_credential(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.router_credentials import broker_request

        try:
            if set(doc) != {"op", "principal", "uid", "request"}:
                raise ValueError("invalid credential channel")
            identities = self._server._owner_identities
            if identities is None or self._server._data_root is None:
                raise RuntimeError("router credential admission is unavailable")
            if (type(doc["uid"]) is not int
                    or identities.box_route(doc["principal"])[0] != doc["uid"]):
                raise PermissionError("owner channel is no longer admitted")
            return broker_request(self._server._data_root, doc["principal"], doc["request"])
        except (OSError, ValueError, LookupError, PermissionError, RuntimeError, sqlite3.Error):
            return {"op": "ROUTER_CREDENTIAL_REFUSED"}

    def _billing_plan(self, doc):
        from tinyassets.broker.owner_reads import PlanMissing, Plans

        try:
            if (set(doc) != {"op", "action", "args", "generation", "token"}
                    or not isinstance(doc["args"], dict)):
                raise ValueError("invalid billing operation")
            with self._server._fence.send(doc["generation"], doc["token"]):
                plans = Plans(self._server._data_root)
                operations = {name: getattr(plans, name) for name in (
                    "read", "owner_for", "owner_for_checkout",
                    "apply_tier_event", "set_suspension", "bind_checkout_subscription",
                    "current_checkout_attempt", "begin_checkout_attempt",
                    "record_checkout_session", "settle_checkout_attempt", "checkout_settlements")}
                if doc["action"] == "begin_checkout_attempt":
                    from tinyassets.broker.admission import read, transition_lock

                    with transition_lock:
                        if read(self._server._owner_identities,
                                doc["args"]["owner"]).state not in {"admitted", "suspended"}:
                            raise PermissionError("checkout requires an account admission")
                        result = operations[doc["action"]](**doc["args"])
                else:
                    result = operations[doc["action"]](**doc["args"])
                return {"op": "BILLING_RESULT", "result": result}
        except PlanMissing:
            return {"op": "PLAN_MISSING"}
        except Exception:
            return {"op": "BILLING_REFUSED"}

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

    def _admitted_owner(self, doc: dict[str, Any]) -> dict[str, Any]:
        try:
            if (set(doc) != {'op', 'center', 'generation', 'token'}
                    or type(doc['generation']) is not int or type(doc['token']) is not str):
                raise ValueError('invalid admitted owner request')
            with self._server._fence.send(doc['generation'], doc['token']):
                principal = self._server._owner_identities.admitted_owner(doc['center'])
                return {'op': 'ADMITTED_OWNER_IS', 'principal': principal}
        except Exception:  # noqa: BLE001 - do not expose broker state on refusal
            return {'op': 'ADMITTED_OWNER_REFUSED'}

    def _owner_lifecycle(self, doc: dict[str, Any]) -> dict[str, Any]:
        try:
            if (set(doc) != {"op", "principal", "generation", "token"}
                    or type(doc["generation"]) is not int or type(doc["token"]) is not str):
                raise ValueError("invalid owner lifecycle request")
            with self._server._fence.send(doc["generation"], doc["token"]):
                if doc["op"] == "OWNER_BOX_WAKE":
                    self._server.routing.wake(doc["principal"])
                else:
                    self._server.routing.revoke(doc["principal"])
                return {"op": "OWNER_LIFECYCLE_DONE"}
        except LookupError:
            return {"op": "OWNER_BOX_UNADMITTED"}
        except Exception:  # noqa: BLE001 - never expose broker state on refusal
            return {"op": "OWNER_LIFECYCLE_REFUSED"}

    def _sole_center(self, doc: dict[str, Any]) -> dict[str, Any]:
        try:
            if (set(doc) != {"op", "principal", "generation", "token"}
                    or type(doc["generation"]) is not int or type(doc["token"]) is not str):
                raise ValueError("invalid center request")
            with self._server._fence.send(doc["generation"], doc["token"]):
                center = self._server._owner_identities.sole_center(doc["principal"])
                return {"op": "SOLE_CENTER_IS", "center": center}
        except LookupError:
            return {"op": "SOLE_CENTER_UNADMITTED"}
        except Exception:  # noqa: BLE001 - no persisted state on refusal
            return {"op": "SOLE_CENTER_REFUSED"}

    def _retire_owner(self, doc):
        try:
            if (set(doc) != {"op", "principal", "generation", "token"}
                    or type(doc["generation"]) is not int or not isinstance(doc["token"], str)):
                raise ValueError("invalid owner retirement")
            with self._server._fence.send(doc["generation"], doc["token"]):
                # Opaque lifecycle operations require the permanent owner binding.
                identities = self._server._owner_identities
                if identities.owner_machine(doc["principal"]) is None:
                    raise RuntimeError("owner deletion requires its permanent reservation")
                from tinyassets.boxes.daemon_control import request

                if request("destroy", doc["principal"]) != {"destroyed": True}:
                    raise RuntimeError("boxhostd did not confirm owner destruction")
            return {"op": "OWNER_RETIRED"}
        except Exception:
            return {"op": "OWNER_RETIRE_REFUSED"}

    def _retire_box_admission(self, doc):
        from tinyassets.broker.admission import read

        try:
            if (set(doc) != {"op", "principal", "uid"} or type(doc["uid"]) is not int
                    or not 300001 <= doc["uid"] <= 399999):
                raise ValueError("invalid admission retirement")
            if read(self._server._owner_identities, doc["principal"]).state != "erased":
                raise PermissionError("retirement requires an erasure fence")
            identities = self._server._owner_identities
            uid = identities.owner_machine(doc["principal"])
            if uid != doc["uid"]:
                raise PermissionError("retirement reservation differs")
            return {"op": "BOX_ADMISSION_RETIRED"}
        except Exception:
            return {"op": "BOX_REFUSED"}

    def _center_admission(self, doc: dict[str, Any]) -> dict[str, Any]:
        """DA1: append-only admit/retire; machine always from the reservation."""
        from tinyassets.broker.owner_identities import CenterUnadmitted

        try:
            if set(doc) != {"op", "event", "principal", "center", "generation", "token"}:
                raise ValueError("unsupported admission fields")
            if (not isinstance(doc["event"], str) or type(doc["generation"]) is not int
                    or not isinstance(doc["token"], str)):
                raise ValueError("invalid admission request")
            with self._server._fence.send(doc["generation"], doc["token"]):
                if self._server._owner_identities is None:
                    raise RuntimeError("owner identities are not initialized")
                row = self._server._owner_identities.admission(
                    doc["event"], doc["principal"], doc["center"])
                if doc["event"] == "retire":
                    self._stop_owner_box(doc["principal"], row.machine)
                if doc["event"] == "admit" and self._server._policy_store is not None:
                    self._server._policy_store.bootstrap_owner(doc["principal"])
                if doc["event"] == "admit" and self._server._provision_box is not None:
                    self._server._provision_box(doc["principal"], doc["center"])
                return {"op": "CENTER_ADMISSION_IS", "generation": row.generation,
                        "machine": row.machine}
        except CenterUnadmitted:
            # D218 retires a tree-less center only if the log admitted it.
            return {"op": "CENTER_ADMISSION_UNADMITTED"}
        except Exception:  # noqa: BLE001 - retry completes durable partial admission
            return {"op": "CENTER_ADMISSION_REFUSED"}

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
                if self._server._policy_store is not None:
                    erased = self._server._policy_store.erase(doc["principal"])
                    if erased:
                        counts["owner_policy"] = erased
                return {"op": "ACCOUNT_ERASED", "counts": counts}
        except Exception:  # noqa: BLE001 - no persisted values on the wire
            return {"op": "ACCOUNT_ERASURE_REFUSED"}

    def _model_source(self, doc):
        from tinyassets.broker.owner_sources import publish

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "definition"}:
                raise ValueError("unsupported source fields")
            _namespace(doc["principal"], doc["command_center"])
            with self._server._fence.send(doc["generation"], doc["token"]):
                result = publish(self._server._ledger_for(doc["principal"]),
                                 doc["principal"], doc["command_center"], doc["definition"])
            return {"op": "BOX_RESULT", "result": result}
        except Exception:
            return {"op": "BOX_REFUSED"}

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

    def _browser_vault(self, doc):
        from tinyassets.broker.browser_vault import local_operation

        try:
            if set(doc) != {'op', 'principal', 'command_center', 'generation', 'token', 'document'}:
                raise ValueError('invalid browser custody fields')
            _namespace(doc['principal'], doc['command_center'])
            with self._server._fence.send(doc['generation'], doc['token']):
                result = local_operation(self._server._ledger_for(doc['principal']),
                    principal=doc['principal'], command_center=doc['command_center'],
                    document=doc['document'])
                return {'op': 'BROWSER_RESULT', 'result': result}
        except Exception:  # noqa: BLE001 - credentials never enter error payloads
            return {'op': 'BROWSER_REFUSED'}

    def _connection_catalog(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.catalog import local_page, validate

        try:
            if not {"op", "principal", "command_center", "generation", "token",
                    "cursor", "limit"} <= set(doc) <= {
                    "op", "principal", "command_center", "generation", "token",
                    "cursor", "limit", "include_revoked"}:
                raise ValueError("unsupported catalog fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["cursor"], doc["limit"], doc.get("include_revoked", False))
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                result = local_page(self._server._ledger_for(doc["principal"]),
                                    principal=doc["principal"],
                                    command_center=doc["command_center"],
                                    cursor=doc["cursor"], limit=doc["limit"],
                                    include_revoked=doc.get("include_revoked", False))
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

    def _owner_policy(self, doc: dict[str, Any]) -> dict[str, Any]:
        """Recheck the protected-session proof against the router's live rows."""
        from tinyassets.broker.owner_identities import validate_principal
        from tinyassets.broker.policy import PolicyRefused

        action = doc.get("action") if isinstance(doc, dict) else None
        owner = doc.get("principal") if isinstance(doc, dict) else None
        try:
            if self._server._policy_store is None:
                raise PolicyRefused("policy store unavailable")
            if not isinstance(doc, dict) or doc.get("op") != "OWNER_POLICY":
                raise PolicyRefused("invalid policy request")
            validate_principal(owner)
            store = self._server._policy_store
            proof_key = "session_proof" if "session_proof" in doc else "cookie"
            if proof_key == "session_proof" and not isinstance(doc[proof_key], dict):
                raise PolicyRefused("interactive owner session required")
            if action == "read" and set(doc) == {"op", "action", "principal"}:
                result = store.read(owner).projection()
            elif action == "receipts" and set(doc) == {"op", "action", "principal"}:
                result = {"receipts": store.receipts(owner)}
            elif action == "proposal" and set(doc) == {
                    "op", "action", "principal", "center", "change"}:
                result = store.receipt_proposal(owner, doc["center"], doc["change"])
            elif action == "write" and set(doc) == {
                    "op", "action", "principal", proof_key, "expected_generation", "policy"}:
                result = store.write(owner, doc[proof_key], doc["expected_generation"],
                                     doc["policy"]).projection()
            elif action == "classify" and set(doc) == {
                    "op", "action", "principal", "center", "connection", "method", "path"}:
                action_class, operation = store.classify(
                    owner, doc["center"], doc["connection"], doc["method"], doc["path"])
                result = {"action_class": action_class, "operation": operation}
            elif action == "issue_once" and set(doc) == {
                    "op", "action", "principal", proof_key, "expected_generation",
                    "center", "grant_id", "connection_id", "verb", "request",
                    "destination"}:
                self._server._ledger_for(owner).authorize_exact(
                    universe_id=doc["center"], grant_id=doc["grant_id"],
                    connection_id=doc["connection_id"])
                if self._server._owner_identities.admitted_owner(doc["center"]) != owner:
                    raise PolicyRefused("center is not this owner's")
                raw = doc["request"]
                if not isinstance(raw, dict) or not isinstance(doc["destination"], str):
                    raise PolicyRefused("invalid approved request")
                wire = {"url": doc["destination"]}
                for name in ("headers", "body", "header_name"):
                    if name in raw:
                        wire[name] = raw[name]
                digest = request_digest(grant_id=doc["grant_id"],
                                        connection_id=doc["connection_id"],
                                        verb=doc["verb"], request=wire)
                result = store.issue_once(owner, doc[proof_key], doc["expected_generation"],
                                          digest)
            elif action == "decide" and set(doc) == {"op", "action", "principal", "request"}:
                request = doc["request"]
                if not isinstance(request, dict) or set(request) != {
                        "center", "action_class", "connection", "operation", "destination",
                        "amount", "source", "agent", "request_id"}:
                    raise PolicyRefused("invalid policy decision request")
                result = store.decide(owner, **request)
            else:
                raise PolicyRefused("unsupported policy operation")
            answer = {"op": "OWNER_POLICY_RESULT", "result": result}
            rf.control(rf.CONNECTION, answer)
            return answer
        except (PolicyRefused, ValueError, TypeError, sqlite3.Error, OSError) as exc:
            if action in {"write", "issue_once"}:
                try:
                    self._server._policy_store.receipt_refusal(
                        owner, action, {k: v for k, v in doc.items()
                                        if k not in {"cookie", "session_proof"}})
                except Exception:  # noqa: BLE001 - refuse regardless of receipt failure
                    _LOG.exception("policy refusal receipt unavailable")
            reason = str(exc) if isinstance(exc, PolicyRefused) else "policy unavailable"
            return {"op": "OWNER_POLICY_REFUSED", "reason": reason}

    def _usage(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.usage import local_operation, validate
        from tinyassets.request_budget import RequestBudgetExceeded
        from tinyassets.storage.agent_request_usage import ModelLaunchEnded

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
                except ModelLaunchEnded:
                    answer = {"op": "USAGE_MODEL_ENDED"}
                rf.control(rf.CONNECTION, answer)
                return answer
        except Exception:  # noqa: BLE001 - fixed refusal, no storage details on wire
            return {"op": "USAGE_REFUSED"}

    def _http_connect(self, doc: dict[str, Any]) -> dict[str, Any]:
        from tinyassets.broker.admission import OwnerErased, require_admitted
        from tinyassets.broker.http_connect import local_operation, validate

        try:
            if set(doc) != {"op", "principal", "command_center", "generation", "token",
                            "document"}:
                raise ValueError("unsupported connect fields")
            _namespace(doc["principal"], doc["command_center"])
            validate(doc["document"])
            if self._role == BOX and ("request_ref" in doc["document"]
                    or doc["document"].get("secret")
                    or doc["document"]["action"] in {
                        "rotate", "oauth_offer", "oauth_exchange", "preset_exchange"}
                    or doc["document"].get("policy", {}).get("auth_scheme", "none") != "none"):
                raise PermissionError("credential deposits require the door")
            if type(doc["generation"]) is not int or not isinstance(doc["token"], str):
                raise Fenced("invalid fence")
            with self._server._fence.send(doc["generation"], doc["token"]):
                if self._closed.is_set():
                    raise PermissionError("connection cancelled")
                require_admitted(self._server._owner_identities, doc["principal"])
                if doc["document"]["action"] in {
                        "oauth_offer", "oauth_exchange", "preset_exchange"}:
                    if (self._server._owner_identities is None
                            or self._server._owner_identities.admitted_owner(
                                doc["command_center"]) != doc["principal"]):
                        raise PermissionError("connection requires live center admission")
                result = local_operation(self._server._ledger_for(doc["principal"]),
                                    principal=doc["principal"],
                                    command_center=doc["command_center"],
                                    document=doc["document"],
                                    committed_via="door" if self._role == OWNER else "box")
                answer = {"op": "HTTP_CONNECT_RESULT", "result": result}
                rf.control(rf.CONNECTION, answer)
                return answer
        except OwnerErased:
            return {"op": "HTTP_CONNECT_RESULT", "result": {"error": "deposit_failed"}}
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
        if self._role == BOX:
            doc = self._box_scope(doc)
        principal, command_center = doc.get("principal"), doc.get("command_center")
        op_id = doc.get("op_id")

        async def refuse(error_class: str) -> None:
            effect = "unknown"
            if self._role == OWNER and isinstance(op_id, str):
                try:
                    namespace = _namespace(principal, command_center)
                    _, effect = await asyncio.to_thread(self._operation_state, namespace, op_id)
                except rf.FrameError:
                    # An invalid namespace gives us no authority to query an
                    # operation. It cannot prove that no prior send occurred.
                    effect = "unknown"
            await self.send_async(rf.control(stream_id, {
                "op": "END", "outcome": "refused", "error_class": error_class,
                "stream_sent": False, "side_effect_state": effect}))

        if self._role not in {OWNER, BOX}:
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
            namespace = _namespace(principal, command_center)
        except rf.FrameError:
            await refuse("refused")
            return
        self._principals.add(principal)
        try:
            from tinyassets.broker.admission import require_admitted

            await asyncio.to_thread(require_admitted, self._server._owner_identities, principal)
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
            if self._server._policy_store is not None:
                try:
                    store = self._server._policy_store
                    spend = store.read(principal).document["spend"]
                    if (doc.get("inference_usage") is not None
                            and (spend["source_ceilings"] or any(
                                spend[key] is not None for key in
                                ("daily", "monthly", "transaction", "ask_above")))):
                        store.decide(principal, center=command_center, action_class="spend",
                                     connection=connection_id, source=connection_id)
                        self._server._ops.finish(namespace, op_id, "refused")
                        return "refused"
                    raw_path = request.get("url") or request.get("path") or "/"
                    parsed = urlsplit(raw_path)
                    path = parsed.path or "/"
                    action_class, operation = store.classify(
                        principal, command_center, connection_id, verb, path)
                    host = parsed.hostname or getattr(resource, "destination", "")
                    policy = store.decide(
                        principal, center=command_center, action_class=action_class,
                        connection=connection_id, operation=operation, destination=host)
                    if policy["decision"] != "allow":
                        origin = (parsed.scheme or "https") + "://" + host.lower()
                        if parsed.port and parsed.port != 443:
                            origin += f":{parsed.port}"
                        standing = (policy["decision"] in ("ask", "hold")
                                    and store.allows_grant(
                                        principal, center=command_center,
                                        generation=policy["generation"],
                                        decision_id=doc.get("policy_grant_ref"),
                                        action_class=action_class, connection=connection_id,
                                        operation=operation, origin=origin,
                                        agent=doc.get("policy_agent"),
                                        task_id=doc.get("policy_task_id") or ""))
                        once = (policy["decision"] in ("ask", "hold") and not standing
                                and store.consume_once(principal, doc.get("policy_approval"),
                                                       digest, policy["generation"]))
                        if not (standing or once):
                            self._server._ops.finish(namespace, op_id, "refused")
                            return "refused"
                except Exception:  # noqa: BLE001 - policy unavailability never permits a send
                    self._server._ops.finish(namespace, op_id, "refused")
                    return "refused"
            stream = _Stream(stream_id, generation, token, namespace, op_id,
                             deadline=time.monotonic() + _stream_budget(request),
                             credit=credit)
            if verb.startswith(("git_read:", "git_write:")):
                stream.authority = lambda: ledger.authorize_exact(
                    universe_id=command_center, grant_id=grant_id, connection_id=connection_id)
                if request.get("upload") is True:
                    from tinyassets.broker.git_upload import Upload

                    stream.upload = Upload(lambda: self._checkpoint(stream), lambda: self.send(
                        rf.control(stream.id, {"op": "UPLOAD_CREDIT"}), stream))
            with self._server._streams_lock:
                self._server._streams[(self._key, stream_id)] = stream
            try:
                dispatch = self._server._dispatch_for(principal, command_center, grant_id,
                                                      resource)
                threading.Thread(
                    target=self._run, args=(stream, dispatch, grant_id, verb, request,
                                            doc.get("idle_s"), doc.get("inference_usage")),
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
        if stream.cancelled or self._closed.is_set():
            raise _Cancelled
        if self._role == BOX:
            self._box_scope({})
        if stream.authority is not None:
            stream.authority()
        if stream.upstream is not None and hasattr(stream.upstream, "check_authority"):
            stream.upstream.check_authority()
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
        if isinstance(sock, socket.socket):
            # SSLSocket.dup is unsupported; the raw duplicate is only used for
            # shutdown. Its lifetime is protected against close/FD reuse.
            with stream.wake:
                if stream.cancel_socket is not None:
                    stream.cancel_socket.close()
                stream.cancel_socket = socket.socket(fileno=os.dup(sock.fileno()))
                if stream.cancelled:
                    stream.cancel_socket.shutdown(socket.SHUT_RDWR)
                    raise _Cancelled
        stream.wrote = True

    def _run(self, stream: _Stream, dispatch: Callable[..., Any], grant_id: str, verb: str,
             request: dict[str, Any], idle_s: Any, inference_usage=None) -> None:
        from tinyassets.broker.owner_sources import BOX_REQUEST

        box_token = BOX_REQUEST.set(self._role == BOX)
        outcome, error_class, extra = "failed", "ProxyRequestError", {}
        try:
            if stream.cancelled or self._closed.is_set():
                raise _Cancelled
            self._server._ops.mark_may_have_sent(stream.namespace, stream.op_id)
            stream.marked = True
            self.send(rf.control(stream.id, {"op": "ADMITTED", "op_id": stream.op_id}),
                      stream)
            if stream.upload is not None:
                self.send(rf.control(stream.id, {"op": "UPLOAD_CREDIT"}), stream)
            upstream = dispatch(
                grant_id, verb, request, stream=True,
                idle_s=(min(idle_s, DEFAULT_IDLE_S)
                        if type(idle_s) in (int, float) and idle_s > 0 else DEFAULT_IDLE_S),
                guard=lambda: self._guard(stream),
                on_connect=lambda sock: self._connected(stream, sock),
                checkpoint=lambda: self._checkpoint(stream),
                deadline_at=stream.deadline,
                **({"body": stream.upload} if stream.upload is not None else {}),
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
            with stream.wake:
                if stream.cancel_socket is not None:
                    stream.cancel_socket.close()
                    stream.cancel_socket = None
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
            BOX_REQUEST.reset(box_token)

    def _pump_body(self, stream: _Stream, upstream: Any) -> None:
        buffered = bytearray()
        finished = False
        while True:
            # Authority may do IPC. Never hold the cancellation lock across it.
            if stream.authority is not None:
                self._checkpoint(stream)
            with stream.wake:
                if stream.cancelled:
                    raise _Cancelled
                ready = ((buffered and stream.credit > 0) or (finished and not buffered)
                         or (not finished and len(buffered) < LOOKAHEAD + stream.credit))
                if not ready:
                    remaining = stream.deadline - time.monotonic()
                    if remaining <= 0:
                        raise _Expired
                    stream.wake.wait(
                        min(0.1, remaining) if stream.authority is not None else remaining)
                    continue
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

    def close(self):
        self.abandon()
        self._writer.transport.abort()
        self._handler.cancel()

    def abandon(self) -> None:
        """The peer went away: stop queueing, cancel every stream it owned."""
        self._closed.set()
        from tinyassets.singleton_lock import release_singleton_lock

        for lease in self._usage_leases:
            release_singleton_lock(lease)
        self._usage_leases.clear()
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
