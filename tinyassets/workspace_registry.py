"""Credential-free registry CONNECT transport for the provisioning broker.

Only the parent-side broker calls this module. It never hands a host-network
socket to the resolver: the supplied stream is a relay from the isolated
namespace, and upstream TLS bytes stay opaque. No listener, credential lookup,
checkout access or ambient proxy is used here. The execution supervisor must
own the broker process/deadline (including a stuck system DNS resolver), reserve
the byte charge before launch and revoke all relays before offline installation.
This transport alone does not enable workspace provisioning.
"""

from __future__ import annotations

import ipaddress
import math
import select
import socket
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

from tinyassets.workspace_git import WorkspaceGitError, pin_address

REGISTRY_HOSTS = frozenset({"pypi.org", "files.pythonhosted.org", "registry.npmjs.org"})
_HEADER_BOUND = 16 * 1024
_BUFFER_BOUND = 64 * 1024
_CONNECTED = b"HTTP/1.1 200 Connection Established\r\n\r\n"
_REFUSED = b"HTTP/1.1 502 Bad Gateway\r\nConnection: close\r\nContent-Length: 0\r\n\r\n"


class RegistryRefused(RuntimeError):
    """A fixed, non-secret transport classification; no upstream error body."""


@dataclass(frozen=True)
class TransferSnapshot:
    bytes_transferred: int
    connections: int
    active: int
    failure: str | None


class TransferBudget:
    """One shared attempt budget, not a new budget per simultaneous tunnel."""

    def __init__(self, *, max_bytes: int, max_connections: int, max_active: int,
                 timeout_s: float) -> None:
        if any(type(v) is not int or v <= 0 for v in
               (max_bytes, max_connections, max_active)):
            raise ValueError("registry limits must be positive integers")
        if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
                or not math.isfinite(timeout_s) or timeout_s <= 0):
            raise ValueError("registry deadline must be positive and finite")
        self.max_bytes = max_bytes
        self.max_connections = max_connections
        self.max_active = max_active
        self.deadline = time.monotonic() + timeout_s
        self._lock = threading.Lock()
        self._bytes = self._connections = self._active = 0
        self._failure: str | None = None

    def snapshot(self) -> TransferSnapshot:
        with self._lock:
            return TransferSnapshot(self._bytes, self._connections, self._active, self._failure)

    def cancel(self) -> None:
        self.fail("cancelled")

    def fail(self, reason: str) -> None:
        if reason not in {
            "cancelled", "timeout", "byte_limit", "connection_limit", "bad_connect",
            "address_refused", "transport_failed",
        }:
            raise ValueError("unknown registry failure")
        with self._lock:
            self._failure = self._failure or reason

    def check(self) -> float:
        with self._lock:
            if self._failure is None and time.monotonic() >= self.deadline:
                self._failure = "timeout"
            if self._failure:
                raise RegistryRefused(self._failure)
            return self.deadline - time.monotonic()

    def enter(self) -> None:
        self.check()
        with self._lock:
            if self._connections >= self.max_connections or self._active >= self.max_active:
                self._failure = self._failure or "connection_limit"
                raise RegistryRefused(self._failure)
            self._connections += 1
            self._active += 1

    def leave(self) -> None:
        with self._lock:
            self._active -= 1

    def receive(self, source: socket.socket, capacity: int) -> bytes:
        """Read nonblocking under the shared lock: concurrent reads cannot overshoot.

        Count both directions once at receipt, including bytes later discarded
        after a broken downstream. Kernel socket buffers are not wire-byte proof;
        the supervisor retains maximum reservation on uncertain termination.
        """
        self.check()
        with self._lock:
            remaining = self.max_bytes - self._bytes
            if remaining <= 0:
                self._failure = self._failure or "byte_limit"
                raise RegistryRefused(self._failure)
            data = source.recv(min(capacity, remaining))
            self._bytes += len(data)
            return data


def connect_host(header: bytes) -> str:
    """Accept exactly an HTTPS registry CONNECT, never a URL or forward proxy."""
    if len(header) > _HEADER_BOUND or not header.endswith(b"\r\n\r\n"):
        raise RegistryRefused("bad_connect")
    try:
        lines = header[:-4].decode("ascii").split("\r\n")
        method, authority, version = lines[0].split(" ")
    except (UnicodeError, ValueError):
        raise RegistryRefused("bad_connect") from None
    if method != "CONNECT" or version not in {"HTTP/1.0", "HTTP/1.1"}:
        raise RegistryRefused("bad_connect")
    if authority not in {host + ":443" for host in REGISTRY_HOSTS}:
        raise RegistryRefused("bad_connect")
    seen_host = False
    for line in lines[1:]:
        name, separator, value = line.partition(":")
        if (not separator or not name or any(c not in
                "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-" for c in name)
                or any(ord(c) < 32 and c != "\t" for c in value) or "\x7f" in value):
            raise RegistryRefused("bad_connect")
        lower = name.lower()
        if lower in {"proxy-authorization", "authorization", "transfer-encoding", "content-length"}:
            raise RegistryRefused("bad_connect")
        if lower == "host":
            if seen_host or value.strip() != authority:
                raise RegistryRefused("bad_connect")
            seen_host = True
    return authority[:-4]


def _read_connect(client: socket.socket, budget: TransferBudget) -> str:
    header = bytearray()
    while len(header) < _HEADER_BOUND:
        budget.check()
        if not select.select([client], [], [], min(0.1, budget.check()))[0]:
            continue
        chunk = client.recv(min(4096, _HEADER_BOUND - len(header)))
        if not chunk:
            raise RegistryRefused("bad_connect")
        header.extend(chunk)
        if b"\r\n\r\n" in header:
            # No bodies, pipelined requests or TLS before CONNECT acceptance.
            return connect_host(bytes(header))
    raise RegistryRefused("bad_connect")


def _connect_pinned(addresses: tuple[str, ...], budget: TransferBudget) -> socket.socket:
    for address in addresses:
        # Numeric sockaddr only: never allow connect() to do a second DNS lookup.
        numeric = ipaddress.ip_address(address)
        family = socket.AF_INET6 if numeric.version == 6 else socket.AF_INET
        upstream = socket.socket(family, socket.SOCK_STREAM)
        try:
            upstream.settimeout(min(10.0, budget.check()))
            upstream.connect((str(numeric), 443))
            budget.check()
            return upstream
        except (OSError, RegistryRefused):
            upstream.close()
            budget.check()
    raise RegistryRefused("transport_failed")


def _pump(client: socket.socket, upstream: socket.socket, budget: TransferBudget) -> None:
    sockets = (client, upstream)
    peer = {client: upstream, upstream: client}
    pending = {client: bytearray(), upstream: bytearray()}
    readable = set(sockets)
    write_shutdown: set[socket.socket] = set()
    for stream in sockets:
        stream.setblocking(False)
    while readable or any(pending.values()):
        budget.check()
        for destination in sockets:
            if (peer[destination] not in readable and not pending[destination]
                    and destination not in write_shutdown):
                destination.shutdown(socket.SHUT_WR)
                write_shutdown.add(destination)
        readers = [s for s in readable if len(pending[peer[s]]) < _BUFFER_BOUND]
        writers = [s for s in sockets if pending[s]]
        ready_read, ready_write, _ = select.select(
            readers, writers, [], min(0.1, budget.check())
        )
        for source in ready_read:
            destination = peer[source]
            try:
                chunk = budget.receive(source, _BUFFER_BOUND - len(pending[destination]))
            except BlockingIOError:
                continue
            if chunk:
                pending[destination].extend(chunk)
            else:
                readable.remove(source)
        for destination in ready_write:
            try:
                sent = destination.send(pending[destination])
            except BlockingIOError:
                continue
            if not sent:
                raise RegistryRefused("transport_failed")
            del pending[destination][:sent]


def serve_registry_tunnel(
    client: socket.socket, budget: TransferBudget, *,
    resolver: Callable[[str, int], list[str]] | None = None,
    classifier: Callable[[str], str] | None = None,
) -> None:
    """Own and close one relay, including its refusal and upstream socket.

    The execution broker may invoke this concurrently with ONE shared budget.
    DNS/classifier injection is for deterministic tests, never an input in a
    workflow packet. Only the existing production classifier is used by default.
    Fixed failure evidence lives in the budget; no exception detail is returned.
    """
    entered = connected = False
    upstream = None
    try:
        budget.enter()
        entered = True
        host = _read_connect(client, budget)
        addresses = pin_address(host, resolver, classifier)
        budget.check()
        upstream = _connect_pinned(addresses, budget)
        client.settimeout(min(1.0, budget.check()))
        client.sendall(_CONNECTED)
        connected = True
        _pump(client, upstream, budget)
    except WorkspaceGitError:
        budget.fail("address_refused")
    except RegistryRefused as exc:
        budget.fail(str(exc))
    except (OSError, ValueError):
        budget.fail("transport_failed")
    finally:
        if not connected:
            try:
                client.settimeout(0.1)
                client.sendall(_REFUSED)
            except OSError:
                pass
        client.close()
        if upstream is not None:
            upstream.close()
        if entered:
            budget.leave()
