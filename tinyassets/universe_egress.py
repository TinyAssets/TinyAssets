"""Public network for the universe tool jail, through a checking proxy.

Change ``universe-agent-harness``, slice S3. The universe agent's ``bash`` had no
network at all, and that was the first thing tiny listed as blocking it ("Local
FastMCP/pytest/Ruff/browser capabilities remain absent"): no ``pip install``, no
``git clone``, no ``curl`` of a page.

The jail still has NO network interface of its own: it keeps its empty network
namespace. What it gets is one unix socket, bound in at :data:`JAIL_SOCKET`,
served by this module in the daemon. The socket lives in the data root's
``.universe-sidecars/<universe>/``, outside the universe: a workflow provider
jail binds the universe read-write, so a socket inside it could be swapped for
a link that bubblewrap would follow at launch. Inside the jail a tiny forwarder
(:data:`FORWARDER`) listens on ``127.0.0.1:3128`` and passes each connection to
that socket, and ``HTTP(S)_PROXY`` point standard clients at it. So the jail can
reach exactly what this proxy agrees to connect, and nothing else -- there is
no route around it, whatever the address family or protocol.

What it agrees to is the floor, which is cross-user only (founder, 2026-08-31):
the destination is resolved HERE, every resolved address must be globally
routable (``outbound_connections._classify_global_address``: no loopback,
private, link-local/metadata, CGNAT, ULA, NAT64-wrapped private and so on), and
the connection is made to the address that was checked, so DNS rebinding and
translated destinations cannot reach the host, the container network or another
universe's engine port. Outbound mail ports are refused because spam from the
shared address burns every user's reputation. Per-universe concurrency is
bounded because the box is shared. Nothing else is restricted: what a universe
fetches is its owner's business.
"""

from __future__ import annotations

import contextlib
import hashlib
import logging
import os
import select
import socket
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

from tinyassets.providers.provider_jail import UNIVERSE_SIDECARS_DIR

logger = logging.getLogger(__name__)

#: Where the proxy socket appears inside the tool jail (on its private /tmp).
JAIL_SOCKET = "/tmp/.ta-egress.sock"
#: Where the in-jail forwarder listens.
JAIL_PROXY_URL = "http://127.0.0.1:3128"

#: SMTP submission and relay: mail from the shared address is cross-user harm.
REFUSED_PORTS = frozenset({25, 465, 587})
#: Concurrent connections one universe may hold through this process.
MAX_CONNECTIONS = 32
_HEAD_LIMIT = 16 * 1024
_HEAD_TIMEOUT_S = 30.0
_CONNECT_TIMEOUT_S = 15.0
_RESOLVE_TIMEOUT_S = 10.0
_IDLE_TIMEOUT_S = 300.0

#: The environment that points standard clients at the forwarder.
PROXY_ENV: tuple[tuple[str, str], ...] = tuple(
    (name, JAIL_PROXY_URL)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy",
                 "ALL_PROXY", "all_proxy")
) + (("NO_PROXY", "localhost,127.0.0.1"), ("no_proxy", "localhost,127.0.0.1"))

#: Where the universe's own engine MCP relay socket appears inside a jail.
JAIL_ENGINE_SOCKET = "/tmp/.ta-engine.sock"

#: Runs inside the jail as ``python3 -c FORWARDER PORT=SOCKET... -- command...``
#: (see :func:`forwarder_argv`): binds each loopback port, forks a quiet child
#: that relays every connection on a port to its unix socket, then execs the
#: command. The child dies with the jail.
FORWARDER = r'''
import os, socket, sys, threading
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
if os.fork():
    for srv, _ in servers:
        srv.close()
    os.execvp(command[0], command)
null = os.open(os.devnull, os.O_RDWR)
for fd in (0, 1, 2):
    os.dup2(null, fd)
threading.stack_size(256 * 1024)
def pump(a, b):
    try:
        while True:
            data = a.recv(65536)
            if not data:
                break
            b.sendall(data)
    except OSError:
        pass
    for s in (a, b):
        try:
            s.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
def serve(c, path, slots):
    u = socket.socket(socket.AF_UNIX)
    try:
        u.connect(path)
    except OSError:
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


def forwarder_argv(
    python: str, command: list[str], *, engine_port: int | None = None,
) -> list[str]:
    """``command`` behind the in-jail forwarder: the proxy port, plus the
    universe's own engine MCP port when it has one."""
    specs = [f"{JAIL_PROXY_URL.rsplit(':', 1)[1]}={JAIL_SOCKET}"]
    if engine_port is not None:
        specs.append(f"{int(engine_port)}={JAIL_ENGINE_SOCKET}")
    return [python, "-I", "-S", "-c", FORWARDER, *specs, "--", *command]


class EgressRefused(Exception):
    """The proxy will not make this connection; the message says why."""


def _destination(head: bytes) -> tuple[str, int, bytes | None]:
    """(host, port, bytes to send first) from a proxy request head.

    ``CONNECT host:port`` opens a tunnel (nothing to forward). An absolute-form
    ``http://`` request is forwarded with its request line rewritten to origin
    form. Anything else is refused.
    """
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


def _open(addresses: list[str], port: int) -> socket.socket:
    last: OSError | None = None
    for address in addresses:
        try:
            return socket.create_connection((address, port), timeout=_CONNECT_TIMEOUT_S)
        except OSError as exc:
            last = exc
    raise EgressRefused(f"could not connect: {last}")


def _read_head(conn: socket.socket) -> bytes:
    """The request head, read under ONE deadline for the whole head.

    A per-``recv`` timeout lets a client that trickles a byte at a time hold a
    daemon thread forever (gpt-6-astra refute of S3a).
    """
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


def _send_within(sock: socket.socket, data: bytes) -> bool:
    """Send all of ``data`` on a non-blocking socket, or give up after the idle bound."""
    view = memoryview(data)
    while view:
        _, writable, _ = select.select([], [sock], [], _IDLE_TIMEOUT_S)
        if not writable:
            return False
        try:
            sent = sock.send(view)
        except (BlockingIOError, InterruptedError):
            continue
        except OSError:
            return False
        view = view[sent:]
    return True


def _relay(a: socket.socket, b: socket.socket) -> None:
    """Copy both ways until both sides finish or nothing moves for the idle bound.

    Both sockets are non-blocking and every write is deadline-bound, so a peer
    that stops reading cannot pin a daemon thread past the idle bound.
    """
    a.setblocking(False)
    b.setblocking(False)
    reading = {a: b, b: a}
    while reading:
        readable, _, _ = select.select(list(reading), [], [], _IDLE_TIMEOUT_S)
        if not readable:
            return
        for sock in readable:
            target = reading.get(sock)
            if target is None:
                continue
            try:
                data = sock.recv(65536)
            except (BlockingIOError, InterruptedError):
                continue
            except OSError:
                return
            if not data:
                del reading[sock]
                with contextlib.suppress(OSError):
                    target.shutdown(socket.SHUT_WR)
                continue
            if not _send_within(target, data):
                return


def _refuse(conn: socket.socket, status: str, reason: str) -> None:
    body = f"egress refused: {reason}\n".encode("utf-8", "replace")
    with contextlib.suppress(OSError):
        conn.sendall(
            f"HTTP/1.1 {status}\r\nContent-Type: text/plain\r\n"
            f"Content-Length: {len(body)}\r\nConnection: close\r\n\r\n".encode("ascii")
            + body
        )


#: Open connections across every universe in this process: the floor that
#: keeps one universe's traffic from taking the shared daemon's threads.
MAX_HOST_CONNECTIONS = 128
_HOST_SLOTS = threading.BoundedSemaphore(MAX_HOST_CONNECTIONS)
#: One budget per universe for the life of the process, shared by every
#: generation of its proxy, so restarting a proxy never mints a fresh budget.
_UNIVERSE_SLOTS: dict[str, threading.BoundedSemaphore] = {}



class EgressProxy:
    """One universe's proxy in this process, listening on a unix socket."""

    def __init__(self, socket_path: Path, universe: str) -> None:
        from tinyassets.broker.supervisor import broker_selected

        self.socket_path = socket_path
        self.universe = universe
        self._slots = _UNIVERSE_SLOTS.setdefault(
            universe, threading.BoundedSemaphore(MAX_CONNECTIONS),
        )
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self._role_identity = None
        try:
            if broker_selected():
                from tinyassets.role_relays import bind

                self._role_identity = bind(server, socket_path)
            else:
                with contextlib.suppress(FileNotFoundError):
                    socket_path.unlink()
                server.bind(str(socket_path))
                os.chmod(socket_path, 0o600)
            server.listen(64)
        except BaseException:
            server.close()
            raise
        self._server = server
        thread = threading.Thread(target=self._accept, name=f"egress-{universe}", daemon=True)
        thread.start()

    def alive(self) -> bool:
        if self._role_identity is not None:
            from tinyassets.role_relays import identity

            try:
                return identity(self.socket_path) == self._role_identity
            except FileNotFoundError:
                return False
        return self.socket_path.is_socket()

    def _accept(self) -> None:
        while True:
            try:
                conn, _ = self._server.accept()
            except OSError:
                return
            if not _HOST_SLOTS.acquire(blocking=False):
                _refuse(conn, "503 Service Unavailable",
                        "the host is at its connection limit; retry shortly")
                conn.close()
                continue
            if not self._slots.acquire(blocking=False):
                _HOST_SLOTS.release()
                _refuse(conn, "503 Service Unavailable",
                        f"this universe already has {MAX_CONNECTIONS} open connections")
                conn.close()
                continue
            threading.Thread(target=self._serve, args=(conn,), daemon=True).start()

    def _serve(self, conn: socket.socket) -> None:
        upstream = None
        try:
            head = _read_head(conn)
            from tinyassets.git_egress import serve

            if serve(self, conn, head):
                return
            host, port, first = _destination(head)
            upstream = _open(_checked_addresses(host, port), port)
            if first is None:
                ok = _send_within(conn, b"HTTP/1.1 200 Connection established\r\n\r\n")
            else:
                ok = _send_within(upstream, first)
            if ok:
                _relay(conn, upstream)
        except EgressRefused as exc:
            logger.info("egress refused for %s: %s", self.universe, exc)
            _refuse(conn, "403 Forbidden", str(exc))
        except OSError:
            pass
        finally:
            for sock in (conn, upstream):
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.close()
            self._slots.release()
            _HOST_SLOTS.release()


_PROXIES: dict[str, EgressProxy] = {}
_LOCK = threading.Lock()


def ensure_proxy(universe_dir: Path) -> Path | None:
    """The proxy socket for ``universe_dir`` in this process, started on first use.

    Returns ``None`` where unix sockets are unavailable (a Windows tray): the
    jail then has no network, as before.
    """
    if not hasattr(socket, "AF_UNIX"):
        return None
    root = Path(universe_dir).resolve()
    key = str(root)
    with _LOCK:
        proxy = _PROXIES.get(key)
        if proxy is not None and proxy.alive():
            return proxy.socket_path
        directory = root.parent / UNIVERSE_SIDECARS_DIR / root.name
        from tinyassets.broker.supervisor import broker_selected

        if not broker_selected():
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        path = directory / f"egress-{os.getpid()}.sock"
        proxy = EgressProxy(path, root.name)
        _PROXIES[key] = proxy
        return path


class EngineRelay(EgressProxy):
    """One universe's way to its OWN engine MCP server, which listens on the
    daemon's loopback.

    A provider CLI reaches the universe's engine tools over HTTP at
    ``127.0.0.1:<port>`` (``engine_mcp_http``). Its jail has no network, and the
    proxy above refuses loopback by design, so this is a second socket with
    exactly one destination: the port the owner-checked route names right now
    (``read_engine_mcp_route``, re-read for every connection). It can never
    reach another universe's engine server, the daemon's app port or anything
    else on loopback; the engine server's own bearer check still applies.
    """

    def __init__(self, socket_path: Path, universe: str, route_port) -> None:
        self._route_port = route_port
        super().__init__(socket_path, universe)

    def _serve(self, conn: socket.socket) -> None:
        upstream = None
        try:
            port = self._route_port()
            if port is None:
                return
            upstream = socket.create_connection(("127.0.0.1", port), timeout=_CONNECT_TIMEOUT_S)
            _relay(conn, upstream)
        except OSError:
            pass
        finally:
            for sock in (conn, upstream):
                if sock is not None:
                    with contextlib.suppress(OSError):
                        sock.close()
            self._slots.release()
            _HOST_SLOTS.release()


_ENGINE_RELAYS: dict[tuple[str, str, str], EngineRelay] = {}


def _route_port(actor_id: str, graph_id: str) -> int | None:
    from tinyassets.engine_mcp_http import read_engine_mcp_route

    route = read_engine_mcp_route(actor_id=actor_id, graph_id=graph_id)
    return None if route is None else urlsplit(route.url).port


def ensure_engine_relay(
    universe_dir: Path, *, actor_id: str, graph_id: str,
) -> tuple[Path, int] | None:
    """The relay socket to this owner's engine MCP server for ``universe_dir``,
    and the port the server listens on now; ``None`` when no route exists (the
    CLI then has no HTTP engine server to reach)."""
    if not hasattr(socket, "AF_UNIX"):
        return None
    port = _route_port(actor_id, graph_id)
    if port is None:
        return None
    root = Path(universe_dir).resolve()
    key = (str(root), actor_id, graph_id)
    with _LOCK:
        relay = _ENGINE_RELAYS.get(key)
        if relay is None or not relay.alive():
            directory = root.parent / UNIVERSE_SIDECARS_DIR / root.name
            from tinyassets.broker.supervisor import broker_selected

            if not broker_selected():
                directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            tag = hashlib.sha256(f"{actor_id}\0{graph_id}".encode()).hexdigest()[:12]
            relay = EngineRelay(
                directory / f"engine-{os.getpid()}-{tag}.sock", root.name,
                lambda: _route_port(actor_id, graph_id),
            )
            _ENGINE_RELAYS[key] = relay
        return relay.socket_path, port
