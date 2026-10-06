"""Synthetic smart HTTP through the real jail, checking proxy and broker IPC."""
from __future__ import annotations

import asyncio
import io
import os
import socket
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import urlsplit

import pytest

from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tinyassets import git_egress, universe_egress, universe_tools
from tinyassets.broker.client import BrokerClient
from tinyassets.broker.fence import Fence
from tinyassets.broker.git_client import exchange
from tinyassets.broker.git_http import target
from tinyassets.broker.ops import OpStore
from tinyassets.broker.server import OWNER, BrokerServer
from tinyassets.storage import outbound_connections as oc


@pytest.mark.parametrize("method,path,verb", [
    ("POST", "/owner/repo.git/git-receive-pack", "git_read:owner/repo"),
    ("GET", "/other/repo.git/info/refs?service=git-upload-pack", "git_read:owner/repo"),
    ("GET", "/owner/repo.git/info/refs?service=git-upload-pack&extra=1", "git_read:owner/repo"),
    ("GET", "/owner/%2e%2e/repo.git/info/refs?service=git-upload-pack", "git_read:owner/repo"),
    ("PUT", "/owner/repo.git/git-upload-pack", "git_read:owner/repo"),
])
def test_wire_target_refusals(method, path, verb):
    resource = SimpleNamespace(scopes=("git_read:owner/repo",), connection_type="http",
                               git_host="git.test", allowed_endpoints=())
    with pytest.raises(PermissionError):
        target(resource, verb, {"method": method, "target": path, "host": "git.test",
                               "upload": method == "POST", "agent": "dev", "incarnation": "x"})


@pytest.fixture
def synthetic(tmp_path, monkeypatch):
    assert os.name == "posix", "run this proof in the Linux oracle"
    secret = "synthetic-git-credential-never-in-box"
    remote = tmp_path / "remote" / "owner" / "repo.git"
    remote.parent.mkdir(parents=True)
    subprocess.run(["git", "init", "--bare", "-b", "main", str(remote)], check=True,
                   capture_output=True)
    subprocess.run(["git", "-C", str(remote), "config", "http.receivepack", "true"], check=True)
    state = {"seen": [], "response": None}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *_args):
            pass

        def do_GET(self):  # noqa: N802
            auth = self.headers.get("Authorization") == "Bearer " + secret
            state["seen"].append((self.path, auth))
            assert auth
            body = bytearray()
            if self.headers.get("Transfer-Encoding") == "chunked":
                while True:
                    n = int(self.rfile.readline().strip(), 16)
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
    from tinyassets.broker.catalog import local_page

    incarnation = local_page(book, principal="alice", command_center="center", cursor="", limit=1
                             )["items"][0]["incarnation"]
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
    universe_egress.ensure_proxy(workspace)
    proxy = universe_egress._PROXIES[str(workspace)]
    record = (book.require_active_grant("grant"), resource.to_view(), incarnation)
    try:
        yield SimpleNamespace(**locals())
    finally:
        listener.close()
        loop.call_soon_threadsafe(loop.stop)
        thread.join(5)
        http.shutdown()
        http.server_close()


def test_synthetic_jail_clone_push_fetch_and_no_token(synthetic):
    s = synthetic
    with git_egress.routes(s.proxy, s.client, [s.record], "developer") as prefix:
        command = prefix + r"""set -eu
git clone https://git.test/owner/repo.git checkout
cd checkout
git config user.name Synthetic
git config user.email synthetic@example.test
python3 -c "import os; open('binary', 'wb').write(os.urandom(2000000))"
git add binary
git commit -qm first
git push origin HEAD:main
git fetch origin
git fsck --full
env
git config --list --show-origin
find /u -type f -print
"""
        result = universe_tools.bash(s.workspace, command, agent_id="developer", timeout=120)
        assert "[exit code 0]" in result, result
        assert s.secret not in result
    expected = (s.workspace / "workspace" / "checkout" / "binary").read_bytes()
    landed = subprocess.run(["git", "-C", str(s.remote), "show", "main:binary"],
                             capture_output=True, check=True).stdout
    assert landed == expected
    assert s.state["seen"] and all(auth for _, auth in s.state["seen"])
    for path in (s.workspace / "workspace").rglob("*"):
        if path.is_file():
            assert s.secret.encode() not in path.read_bytes()


@pytest.mark.parametrize("failure", ["foreign", "revoked", "host", "repo", "incarnation",
                                     "write_scope", "redirect", "echo"])
def test_git_refusals(synthetic, failure):
    s = synthetic
    doc = {"host": "git.test", "method": "GET", "target":
           "/owner/repo.git/info/refs?service=git-upload-pack", "agent": "developer",
           "incarnation": s.incarnation, "upload": False}
    client, verb = s.client, "git_read:owner/repo"
    if failure == "foreign":
        client = BrokerClient(s.path, principal="bob", command_center="center",
                              fence=lambda: s.generation)
    elif failure == "revoked":
        with s.book._connect() as conn:
            conn.execute("UPDATE outbound_connection_grants SET revoked_at=1")
    elif failure in {"host", "incarnation"}:
        doc[failure] = "wrong"
    elif failure == "repo":
        doc["target"] = doc["target"].replace("owner/repo", "other/repo")
    elif failure == "write_scope":
        doc["target"] = doc["target"].replace("upload", "receive")
    elif failure == "redirect":
        s.state["response"] = 302, b"redirect"
    elif failure == "echo":
        s.state["response"] = 200, s.secret.encode()
    returned = io.BytesIO()
    with pytest.raises((PermissionError, oc.ProxyRequestError, oc.GrantResolutionError)):
        exchange(client, grant_id="grant", connection_id="git", verb=verb, request=doc,
                 upload=None, head=lambda _: None, data=returned.write)
    assert s.secret.encode() not in returned.getvalue()
    if failure not in {"redirect", "echo"}:
        assert not s.state["seen"]
