"""A blocking client for the broker process: one stream collected to its end.

This is what ``ScopedConnectionProxy.request`` becomes once the broker serves
it (I14 decision 8): the same return document (``status``, ``reason``,
``headers``, ``body`` decoded as UTF-8 with replacement) and the same typed
errors. The broker has already scanned every byte; this client holds no
credential and scans nothing.

Credit rolls: at most ``MAX_WINDOW`` outstanding, replenished as data arrives,
so the broker never buffers more than a window for it.
"""

from __future__ import annotations

import os
import socket
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from tinyassets import rpc_frames as rf

MAX_WINDOW = 256 * 1024
_STREAM_ID = 1


class BrokerRefused(PermissionError):
    """The broker refused the stream before anything was sent."""


def _raise_for(end: dict[str, Any]) -> None:
    from tinyassets.storage.outbound_connections import (
        AmbiguousProxyOutcome,
        ConnectionAuthorizationError,
        GrantResolutionError,
        OutboundDeadlineExceeded,
        ProxyRequestError,
        SsrfValidationError,
    )

    error_class = end.get("error_class")
    outcome = end.get("outcome")
    if error_class == "InferenceUsageStopped":
        from tinyassets.storage.agent_request_usage import InferenceUsageStopped

        raise InferenceUsageStopped(end.get("reason"), end.get("usage_id"))
    if error_class == "ProviderAuthorityHeldError":
        from tinyassets.exceptions import ProviderAuthorityHeldError

        raise ProviderAuthorityHeldError("inference usage authority refused")
    if outcome == "refused":
        if error_class == "GrantResolutionError":
            raise GrantResolutionError("outbound connection grant identity mismatch")
        error = BrokerRefused(f"the broker refused the request ({error_class})")
        error.side_effect_state = end.get("side_effect_state", "unknown")
        raise error
    if error_class == "OutboundDeadlineExceeded":
        raise OutboundDeadlineExceeded("outbound request exceeded its time budget")
    if error_class == "ConnectionAuthorizationError":
        # The structured, secret-free failure travels as today's worker sent it.
        failure = end.get("failure")
        detail = failure.get("provider_detail", "") if isinstance(failure, dict) else ""
        raise ConnectionAuthorizationError(str(detail))
    if error_class == "GrantResolutionError":
        raise GrantResolutionError("outbound connection authority changed")
    if error_class == "AmbiguousProxyOutcome":
        raise AmbiguousProxyOutcome("destination outcome ambiguous")
    if error_class == "SsrfValidationError":
        raise SsrfValidationError("outbound response exceeds the size bound")
    if error_class == "PermissionError":
        raise PermissionError("verb is outside the granted connection scope")
    raise ProxyRequestError("outbound request failed")


class BrokerClient:
    """One principal's view of the broker; one request at a time per client."""

    def __init__(self, path: Path, *, principal: str, command_center: str,
                 fence: Callable[[], tuple[int, str]], timeout: float = 660.0,
                 verify_peer: Callable[[socket.socket], None] | None = None) -> None:
        self._path = Path(path)
        self._principal = principal
        self._command_center = command_center
        self._fence = fence
        self._timeout = timeout
        self._verify_peer = verify_peer
        self._lock = threading.Lock()

    def ledger_query(self, *, query: str, grant_id: str,
                     connection_id: str = "") -> dict[str, Any]:
        """One named, read-only operation; never retry through a local ledger."""
        from tinyassets.broker.ledger_queries import validate_query
        from tinyassets.storage.outbound_connections import GrantResolutionError, ProxyRequestError

        validate_query(query, self._principal, self._command_center, grant_id, connection_id)
        generation, token = self._fence()
        document = {"op": "LEDGER_QUERY", "query": query, "principal": self._principal,
                    "command_center": self._command_center, "grant_id": grant_id,
                    "connection_id": connection_id, "generation": generation, "token": token}
        with self._lock, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self._timeout)
            try:
                sock.connect(os.fspath(self._path))
                if self._verify_peer is not None:
                    self._verify_peer(sock)
                sock.sendall(rf.control(rf.CONNECTION, document))
                frame = rf.read_frame_blocking(sock)
                if frame is None or frame.kind != rf.CONTROL or frame.stream != rf.CONNECTION:
                    raise rf.FrameError("invalid ledger response")
                answer = frame.control()
            except (OSError, rf.FrameError):
                raise ProxyRequestError("credential broker query unavailable") from None
        if answer.get("op") == "LEDGER_REFUSED":
            if answer.get("error_class") == "GrantResolutionError":
                raise GrantResolutionError("outbound connection grant identity mismatch")
            raise BrokerRefused("credential broker query refused")
        if answer.get("op") != "LEDGER_RESULT" or not isinstance(answer.get("result"), dict):
            raise ProxyRequestError("invalid credential broker query response")
        return answer["result"]

    def request(self, *, grant_id: str, connection_id: str, verb: str, request: dict[str, Any],
                op_id: str, idle_s: float | None = None,
                inference_usage: dict[str, Any] | None = None) -> dict[str, Any]:
        generation, token = self._fence()
        open_doc = {
            "op": "OPEN", "op_id": op_id, "generation": generation, "token": token,
            "principal": self._principal, "command_center": self._command_center,
            "grant_id": grant_id, "connection_id": connection_id, "verb": verb,
            "request": request, "credit": MAX_WINDOW,
        }
        if inference_usage is not None:
            open_doc["inference_usage"] = inference_usage
        if idle_s is not None:
            open_doc["idle_s"] = idle_s
        from tinyassets.storage.outbound_connections import (
            AmbiguousProxyOutcome,
            ProxyRequestError,
        )

        with self._lock, socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
            sock.settimeout(self._timeout)
            try:
                sock.connect(os.fspath(self._path))
            except OSError:
                # Nothing was sent: the broker never saw this request.
                raise ProxyRequestError("the credential broker is unavailable") from None
            if self._verify_peer is not None:
                self._verify_peer(sock)  # before sending any owner-channel factor
            try:
                head, body, end = self._exchange(sock, open_doc)
            except (OSError, rf.FrameError):
                # Transport only (typed ENDs are raised below, outside this
                # try): the request may have reached the broker and left it.
                raise AmbiguousProxyOutcome(
                    "the broker connection failed mid-request") from None
        if end.get("outcome") != "completed" or head is None:
            _raise_for(end)
        return {
            "status": head["status"], "reason": head.get("reason", ""),
            "headers": head.get("headers", {}),
            "body": bytes(body).decode("utf-8", errors="replace"),
            **({"redirect_count": head["redirect_count"]}
               if head.get("redirect_count") else {}),
        }

    @staticmethod
    def _exchange(sock: socket.socket, open_doc: dict[str, Any]
                  ) -> tuple[dict[str, Any] | None, bytearray, dict[str, Any]]:
        """The stream to its END, raising only transport errors."""

        sock.sendall(rf.control(_STREAM_ID, open_doc))
        head: dict[str, Any] | None = None
        body = bytearray()
        while True:
            frame = rf.read_frame_blocking(sock)
            if frame is None:
                raise ConnectionResetError("the broker connection closed mid-request")
            if frame.kind == rf.DATA:
                body += frame.payload
                sock.sendall(rf.control(_STREAM_ID, {"op": "CREDIT",
                                                     "n": len(frame.payload)}))
                continue
            doc = frame.control()
            if doc["op"] == "HEAD":
                head = doc
            elif doc["op"] == "END":
                return head, body, doc
