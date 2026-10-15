"""Shared outbound floor and fixed in-box loopback forwarder."""
from __future__ import annotations

import socket
import time
from urllib.parse import urlsplit

JAIL_PROXY_URL = "http://127.0.0.1:3128"
REFUSED_PORTS = frozenset({25, 465, 587})
_RESOLVE_TIMEOUT_S = 10.0
_HEAD_LIMIT = 16 * 1024
_HEAD_TIMEOUT_S = 30.0
PROXY_ENV: tuple[tuple[str, str], ...] = tuple(
    (name, JAIL_PROXY_URL)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                 "ALL_PROXY", "all_proxy")
) + (("NO_PROXY", "localhost,127.0.0.1"), ("no_proxy", "localhost,127.0.0.1"))

FORWARDER = r'''
import os, socket, sys, threading
def report(exc, operation):
    number = exc.errno if isinstance(exc, OSError) else None
    os.write(2, ('TA_CELL_FAILURE relay:' + operation + ':errno=' + str(number) + '\n').encode())
sys.excepthook = lambda kind, exc, trace: report(exc, 'bootstrap')
args = sys.argv[1:]
sep = args.index("--")
servers = []
for spec in args[:sep]:
    port, path = spec.split("=", 1)
    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", int(port)))
    srv.listen(64)
    servers.append((srv, path))
command = args[sep + 1:]
box_proxy = any(path == "/run/boxhostd/relay.sock" for _, path in servers)
git_context, git_proxy = None, None
if box_proxy:
    sys.path.insert(0, "/app")
    from tinyassets.boxes.git_bridge import prepare
    git_context, git_proxy = prepare()
if os.fork():
    for srv, _ in servers:
        srv.close()
    os.execvp(command[0], command)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2) if box_proxy else (0, 1):
    os.dup2(null, fd)
threading.stack_size(256 * 1024)
def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError as exc:
        report(exc, 'pump')
        for s in (a, b):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass
        return
    try:
        b.shutdown(socket.SHUT_WR)
    except OSError as exc:
        report(exc, 'half-close')
        pass
def serve(c, path, slots):
    u = socket.socket(socket.AF_UNIX)
    try:
        first = b""
        if git_proxy is not None and path == "/run/boxhostd/relay.sock":
            from tinyassets.boxes.network import _read_head, EgressRefused
            try:
                first = _read_head(c)
            except EgressRefused:
                raise OSError("git proxy header incomplete or exceeds bound") from None
            from tinyassets.boxes.git_routes import serve as serve_git
            if serve_git(git_proxy, c, first):
                c.close(); u.close(); slots.release(); return
        u.connect(path)
        if path == "/run/boxhostd/relay.sock":
            sys.path.insert(0, "/app")
            from tinyassets import rpc_frames as rf
            u.sendall(rf.control(rf.CONNECTION, {
                "op": "BOX_PUBLIC", "center": os.environ["TINYASSETS_BOX_CENTER"]}))
            answer = rf.read_frame_blocking(u)
            if answer is None or answer.control() != {"op": "BOX_CONNECTED"}:
                raise OSError("owner relay refused")
        if first:
            u.sendall(first)
    except OSError as exc:
        report(exc, 'connect')
        c.close(); u.close(); slots.release(); return
    t = threading.Thread(target=pump, args=(u, c), daemon=True)
    t.start()
    pump(c, u)
    t.join()
    c.close(); u.close(); slots.release()
def accept(srv, path):
    slots = threading.BoundedSemaphore(16)
    while True:
        c, _ = srv.accept()
        if not slots.acquire(blocking=False):
            c.close(); continue
        threading.Thread(target=serve, args=(c, path, slots), daemon=True).start()
for srv, path in servers[1:]:
    threading.Thread(target=accept, args=(srv, path), daemon=True).start()
accept(*servers[0])
'''


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
