"""Broker public-egress destination and bounded HTTP header parsing."""
from __future__ import annotations

import socket
import time
from urllib.parse import urlsplit

REFUSED_PORTS = frozenset({25, 465, 587})
_RESOLVE_TIMEOUT_S = 10.0
_HEAD_LIMIT = 16 * 1024
_HEAD_TIMEOUT_S = 30.0


class EgressRefused(Exception):
    """The proxy will not make this connection; the message says why."""


def _destination(head: bytes) -> tuple[str, int, bytes | None]:
    """Parse CONNECT or an absolute HTTP request into a pinned destination."""
    try:
        line, _, rest = head.partition(b"\r\n")
        method, target, version = line.decode("ascii").split(" ")
    except (UnicodeDecodeError, ValueError):
        raise EgressRefused("not an HTTP proxy request") from None
    if method.upper() == "CONNECT":
        host, sep, port = target.rpartition(":")
        if not sep or not port.isdigit():
            raise EgressRefused("CONNECT needs host:port")
        return host.strip("[]"), int(port), None
    parts = urlsplit(target)
    if parts.scheme != "http" or not parts.hostname:
        raise EgressRefused("only http:// requests or CONNECT tunnels are proxied")
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    first = f"{method} {path} {version}\r\n".encode("ascii")
    return parts.hostname, parts.port or 80, first + rest


def _checked_addresses(host: str, port: int) -> list[str]:
    """Every address ``host`` resolves to, each proven globally routable."""
    from tinyassets.storage.outbound_connections import (
        SsrfValidationError,
        _classify_global_address,
        _make_default_resolver,
        _resolve_pinned_addresses,
    )

    if port in REFUSED_PORTS:
        raise EgressRefused(f"port {port} (outbound mail) is not reachable from a universe")
    if not 0 < port < 65536:
        raise EgressRefused(f"port {port} is not a valid port")
    try:
        return _resolve_pinned_addresses(
            host, port, resolver=_make_default_resolver(_RESOLVE_TIMEOUT_S),
            validator=_classify_global_address,
        )
    except SsrfValidationError as exc:
        raise EgressRefused(f"{host}: {exc}") from None

def _read_head(conn: socket.socket) -> bytes:
    """Read a request head under one deadline, including trickled bytes."""
    deadline = time.monotonic() + _HEAD_TIMEOUT_S
    data = b""
    while b"\r\n\r\n" not in data:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise EgressRefused("the request head took too long")
        conn.settimeout(remaining)
        try:
            chunk = conn.recv(4096)
        except TimeoutError:
            raise EgressRefused("the request head took too long") from None
        if not chunk:
            raise EgressRefused("the request ended before its head")
        data += chunk
        if len(data) > _HEAD_LIMIT:
            raise EgressRefused("request head too large")
    return data
