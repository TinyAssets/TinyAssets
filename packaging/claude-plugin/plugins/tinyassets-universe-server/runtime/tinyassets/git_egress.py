"""Ephemeral git URL rewrites on a command center's existing checking proxy.

Only this trusted launch context chooses grants. The box gets an opaque route,
which expires with the invocation, and never gets broker IPC or a credential.
"""
from __future__ import annotations

import contextlib
import secrets
import shlex
import threading
import time
from urllib.parse import urlsplit

from tinyassets.broker.git_client import exchange
from tinyassets.storage.workspace_authority import connection_git_host, parse_git_scope

HOST = "ta-git.invalid"
_lock = threading.Lock()
_routes = {}


class Body:
    def __init__(self, conn, buffered, headers, check):
        self.conn, self.buffer = conn, buffered
        self.check = check
        length, encoding = headers.get("content-length"), headers.get("transfer-encoding")
        if (length is not None and encoding is not None) or encoding not in (None, "chunked"):
            raise ValueError("ambiguous git body")
        if length is not None and (not length.isascii() or not length.isdigit()):
            raise ValueError("invalid git length")
        if length is None and encoding is None:
            raise ValueError("git body length required")
        self.chunked = encoding is not None
        self.remaining = 0 if self.chunked else int(length)
        self.finished = False

    def take(self, n):
        while len(self.buffer) < n:
            self.check()
            self.conn.settimeout(15)
            piece = self.conn.recv(min(65536, n - len(self.buffer)))
            if not piece:
                raise ValueError("truncated git upload")
            self.buffer += piece
        piece, self.buffer = self.buffer[:n], self.buffer[n:]
        return piece

    def read(self, size):
        self.check()
        if self.finished:
            return b""
        if self.chunked and not self.remaining:
            line = bytearray()
            while not line.endswith(b"\r\n") and len(line) < 32:
                line += self.take(1)
            raw = bytes(line[:-2])
            if not raw or any(c not in b"0123456789abcdefABCDEF" for c in raw):
                raise ValueError("invalid git chunk")
            self.remaining = int(raw, 16)
            if not self.remaining:
                if self.take(2) != b"\r\n":
                    raise ValueError("git trailers refused")
                self.finished = True
                return b""
        if not self.remaining:
            self.finished = True
            return b""
        piece = self.take(min(size, self.remaining))
        self.remaining -= len(piece)
        if self.chunked and not self.remaining and self.take(2) != b"\r\n":
            raise ValueError("invalid git chunk ending")
        return piece


def serve(proxy, conn, raw):
    """Return False for ordinary egress; git surrogate traffic is consumed here."""
    try:
        line = raw.split(b"\r\n", 1)[0].decode("ascii")
        method, url, version = line.split(" ")
        parts = urlsplit(url)
    except (UnicodeError, ValueError):
        return False  # ordinary proxy parser owns malformed non-route traffic
    if parts.hostname != HOST:
        return False
    started = False
    try:
        if parts.scheme != "http" or parts.netloc != HOST or version != "HTTP/1.1":
            raise ValueError("invalid git route")
        head, buffered = raw.split(b"\r\n\r\n", 1)
        headers = {}
        for row in head.split(b"\r\n")[1:]:
            key, value = row.decode("ascii").split(":", 1)
            if key != key.strip() or key.lower() in headers:
                raise ValueError("ambiguous git headers")
            headers[key.lower()] = value.strip()
        if headers.get("host") != HOST or any(k in headers for k in (
                "authorization", "proxy-authorization", "content-encoding")):
            raise ValueError("invalid git headers")
        _, route_id, target = parts.path.split("/", 2)
        with _lock:
            route = _routes.get((id(proxy), route_id))
        if route is None:
            raise PermissionError("expired git route")
        client, grant, view, incarnation, agent, live, generation, sockets = route

        def check():
            if not live.is_set() or client._fence() != generation or time.monotonic() > deadline:
                raise PermissionError("expired git route")

        deadline = time.monotonic() + 600
        check()
        with _lock:
            sockets.add(conn)
        target = "/" + target + ("?" + parts.query if parts.query else "")
        kind = "git_write" if "git-receive-pack" in target else "git_read"
        repo = target.split(".git/", 1)[0].lstrip("/")
        verb = f"{kind}:{repo}"
        document = {"host": connection_git_host(view), "method": method, "target": target,
                    "agent": agent, "incarnation": incarnation, "upload": method == "POST"}
        from tinyassets.broker.git_http import target as validate

        # Pure validation; no broker/vault file access in this process.
        validate(view, verb, document)
        body = Body(conn, buffered, headers, check) if method == "POST" else None
        if body is None and (buffered or "transfer-encoding" in headers
                             or headers.get("content-length", "0") != "0"):
            raise ValueError("unexpected git body")
        if headers.get("expect") == "100-continue":
            conn.sendall(b"HTTP/1.1 100 Continue\r\n\r\n")
        elif "expect" in headers:
            raise ValueError("invalid git expectation")

        def send_head(doc):
            nonlocal started
            check()
            service = "git-upload-pack" if kind == "git_read" else "git-receive-pack"
            suffix = "result" if body else "advertisement"
            conn.sendall(("HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n"
                          "Connection: close\r\nContent-Type: application/x-"
                          f"{service}-{suffix}\r\n\r\n").encode())
            started = True

        def send_data(chunk):
            check()
            conn.sendall(f"{len(chunk):x}\r\n".encode() + chunk + b"\r\n")

        exchange(client, grant_id=grant.grant_id, connection_id=view.connection_id,
                 verb=verb, request=document, upload=body, head=send_head, data=send_data)
        conn.sendall(b"0\r\n\r\n")
    except Exception:  # fixed errors only; never echo upstream or request material
        if not started:
            with contextlib.suppress(OSError):
                conn.sendall(b"HTTP/1.1 403 Forbidden\r\nConnection: close\r\n"
                             b"Content-Length: 0\r\n\r\n")
    finally:
        with _lock:
            if "sockets" in locals():
                sockets.discard(conn)
    return True


@contextlib.contextmanager
def routes(proxy, client, connections, agent):
    """Trusted inputs from the current owner launch, not box request fields."""
    live = threading.Event()
    live.set()
    identifiers, rewrites, sockets = [], {}, set()
    try:
        for grant, view, incarnation in connections:
            host = connection_git_host(view)
            for scope in view.scopes:
                parsed = parse_git_scope(scope)
                if not host or parsed is None:
                    continue
                repo = parsed[1]
                source = f"https://{host}/{repo}.git"
                if source in rewrites:
                    if rewrites[source][0] != grant.grant_id:
                        raise PermissionError("ambiguous git grants; connect one repository grant")
                    continue
                identifier = secrets.token_hex(16)
                identifiers.append(identifier)
                with _lock:
                    _routes[id(proxy), identifier] = (
                        client, grant, view, incarnation, agent, live, client._fence(), sockets)
                rewrites[source] = grant.grant_id, f"http://{HOST}/{identifier}/{repo}.git"
        env = {"GIT_CONFIG_COUNT": str(len(rewrites)), "GIT_TERMINAL_PROMPT": "0"}
        for index, (source, (_, replacement)) in enumerate(rewrites.items()):
            env[f"GIT_CONFIG_KEY_{index}"] = f"url.{replacement}.insteadOf"
            env[f"GIT_CONFIG_VALUE_{index}"] = source
        yield "export " + " ".join(f"{k}={shlex.quote(v)}" for k, v in env.items()) + "; "
    finally:
        live.clear()
        with _lock:
            for identifier in identifiers:
                _routes.pop((id(proxy), identifier), None)
            for conn in tuple(sockets):
                with contextlib.suppress(OSError):
                    conn.shutdown(2)


@contextlib.contextmanager
def for_bash(universe_dir, agent):
    from tinyassets import universe_egress
    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.catalog import connections
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.supervisor import broker_selected, get_supervisor
    from tinyassets.daemon_server import get_founder_home, universe_access_permission

    if not broker_selected():
        yield ""
        return
    # A missing/ambiguous git grant disables authenticated git, not local bash.
    # Only setup failures are handled here; exceptions from the command itself
    # propagate normally. No ambient identity or credential fallback is used.
    with contextlib.ExitStack() as stack:
        try:
            root = universe_dir.resolve()
            principal = current_identity().user_id
            if not (get_founder_home(root.parent, principal) == root.name
                    or universe_access_permission(root.parent, universe_id=root.name,
                                                  actor_id=principal) == "admin"):
                raise PermissionError("git owner scope is not admitted")
            supervisor = get_supervisor(root.parent)
            if supervisor is None:
                raise RuntimeError("git broker unavailable")
            client = BrokerClient(supervisor.socket_path, principal=principal,
                                  command_center=root.name, fence=supervisor.fence,
                                  verify_peer=supervisor.verify_broker)
            proxy = universe_egress._PROXIES[str(root)]
            prefix = stack.enter_context(routes(proxy, client, connections(
                root.parent, principal=principal, command_center=root.name), agent))
        except (OSError, RuntimeError, LookupError, ValueError):
            prefix = ("printf '%s\\n' 'Authenticated git unavailable: owner, broker, "
                      "egress or unique repository grant not admitted.' >&2; "
                      "export GIT_TERMINAL_PROMPT=0; ")
        yield prefix
