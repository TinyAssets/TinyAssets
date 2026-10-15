"""A synthetic smart-HTTP git origin behind the real broker git path.

The broker's credential-blind git dispatch, its op store and its fence are
real; only the destination is local (``git http-backend`` over loopback, the
TLS context pass-through, the resolver pinned to 127.0.0.1). The credential
never leaves the broker process.
"""
from __future__ import annotations

import asyncio
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tinyassets.broker.client import BrokerClient
from tinyassets.broker.fence import Fence
from tinyassets.broker.ops import OpStore
from tinyassets.broker.server import OWNER, BrokerServer
from tinyassets.storage import outbound_connections as oc


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    assert os.name == "posix", "run this proof in the Linux oracle"
    secret = "synthetic-git-credential-never-in-box"
    remote = tmp_path / "remote" / "owner" / "repo.git"
    remote.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(remote), "config", "http.receivepack", "true"], check=True)
    state = {"seen": [], "response": None, "connected": threading.Event()}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):  # noqa: N802
            auth = self.headers.get("Authorization") == "Bearer " + secret
            state["seen"].append((self.path, auth))
            assert auth
            state["connected"].set()
            body = bytearray()
            if self.headers.get("Transfer-Encoding") == "chunked":
                while True:
                    line = self.rfile.readline().strip()
                    if not line:
                        return  # an interrupted upload deliberately closes the socket
                    n = int(line, 16)
                    if not n:
                        assert self.rfile.read(2) == b"\r\n"
                        break
                    body += self.rfile.read(n)
                    assert self.rfile.read(2) == b"\r\n"
            else:
                body += self.rfile.read(int(self.headers.get("Content-Length", 0)))
            if state["response"]:
                status, payload = state["response"]
                self.send_response(status)
                self.send_header("Content-Length", str(len(payload)))
                self.send_header("Location", "https://elsewhere.test/stolen")
                self.end_headers()
                self.wfile.write(payload)
                return
            parsed = urlsplit(self.path)
            result = subprocess.run(["git", "http-backend"], input=body, check=True,
                capture_output=True, env={**os.environ,
                    "GIT_PROJECT_ROOT": str(remote.parent.parent),
                    "GIT_HTTP_EXPORT_ALL": "1", "PATH_INFO": parsed.path,
                    "QUERY_STRING": parsed.query, "REQUEST_METHOD": self.command,
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(len(body)), "REMOTE_USER": "synthetic"})
            head, payload = result.stdout.split(b"\r\n\r\n", 1)
            self.send_response(200)
            for row in head.decode().split("\r\n"):
                name, value = row.split(":", 1)
                self.send_header(name, value.strip())
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        do_POST = do_GET

    http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=http.serve_forever, daemon=True).start()
    # The sole transport seam: synthetic destination, real pin/peer checks and
    # HTTP parser. Production retains public DNS and verified TLS on port 443.
    from tinyassets.broker import git_http

    monkeypatch.setattr(git_http.ssl, "create_default_context", lambda: _PassThroughTLS())
    monkeypatch.setattr(oc, "_make_default_resolver", lambda timeout: lambda h, p: ["127.0.0.1"])
    monkeypatch.setattr(oc, "_classify_global_address", lambda addr: addr)
    monkeypatch.setattr(oc, "_default_open_socket", lambda address, timeout, source:
                        socket.create_connection(("127.0.0.1", http.server_port), timeout))
    db = tmp_path / "ledger.db"

    def ledger(principal="alice"):
        return oc.ConnectionLedger(db, verify_authenticated_principal=lambda: principal)

    book = ledger()
    book.create_connection(connection_id="git", owner_user_id="alice", connection_class="http",
        scopes=("git_read:owner/repo", "git_write:owner/repo"), provider="http",
        destination="git.test", credential_ref="vault://http/git.test", connection_type="http",
        auth_scheme="bearer", git_host="git.test",
        allowed_endpoints=[{"host": "git.test", "path_template": "/api", "methods": ["GET"]}])
    book.grant_connection(grant_id="grant", connection_id="git", owner_user_id="alice",
                          universe_id="center")
    incarnation = book.incarnation("git")
    resource = book._active_resource_for_grant("grant")
    dispatch = oc.CredentialBlindBroker(book, resolve_credential=lambda *_: secret,
                                       network_request=lambda **_: None).dispatch
    fence = Fence(tmp_path / "fence.json", verify_lease_proof=lambda g, p: True)
    generation = fence.barrier(1, "proof")
    server = BrokerServer(ledger_for=ledger, dispatch_for=lambda *_: dispatch,
                          ops=OpStore(tmp_path / "ops.db"), fence=fence, roles={os.getuid(): OWNER})
    path = tmp_path / "b.sock"
    loop = asyncio.new_event_loop()
    listener = loop.run_until_complete(server.serve(path))
    thread = threading.Thread(target=loop.run_forever, daemon=True)
    thread.start()
    client = BrokerClient(path, principal="alice", command_center="center",
                          fence=lambda: generation)
    workspace = tmp_path / "center"
    workspace.mkdir()
    record = (book.require_active_grant("grant"), resource.to_view(), incarnation)
    try:
        yield SimpleNamespace(**locals())
    finally:
        async def close_listener():
            listener.close()
            await listener.wait_closed()
            pending = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            await asyncio.gather(*pending, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(close_listener(), loop).result(5)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        loop.close()
        http.shutdown()
        http.server_close()
