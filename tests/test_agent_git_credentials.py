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
    universe_egress.ensure_proxy(workspace)
    proxy = universe_egress._PROXIES[str(workspace)]
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
git clone https://git.test/owner/repo.git /u/second
cmp binary /u/second/binary
git -C /u/second config user.name Synthetic
git -C /u/second config user.email synthetic@example.test
echo second > /u/second/next
git -C /u/second add next
git -C /u/second commit -qm second
git -C /u/second push origin HEAD:main
git fetch origin
test "$(git show origin/main:next)" = second
git fsck --full
env
python3 -c "import pathlib; print(pathlib.Path('/proc/self/cmdline').read_bytes())"
git config --list --show-origin
find /u -type f -print
"""
        result = universe_tools.bash(s.workspace, command, agent_id="developer", timeout=120)
        assert "[exit code 0]" in result, result
        assert s.secret not in result
    expected = (s.workspace / universe_tools.WORKSPACE_DIR / "checkout" / "binary").read_bytes()
    landed = subprocess.run(["git", "-C", str(s.remote), "show", "main:binary"],
                             capture_output=True, check=True).stdout
    assert landed == expected
    assert s.state["seen"] and all(auth for _, auth in s.state["seen"])
    for path in (s.workspace / universe_tools.WORKSPACE_DIR).rglob("*"):
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


@pytest.mark.parametrize("phase", ["upload", "download"])
@pytest.mark.parametrize("change", ["revoke", "scope", "generation"])
def test_authority_change_stops_credit_starved_stream(synthetic, phase, change):
    from tinyassets import rpc_frames as rf
    from tinyassets.broker.ops import new_op_id

    s = synthetic
    upload = phase == "upload"
    s.state["response"] = 200, b"x" * 200000
    request = {"host": "git.test", "method": "POST" if upload else "GET",
               "target": "/owner/repo.git/git-upload-pack" if upload else
                   "/owner/repo.git/info/refs?service=git-upload-pack",
               "agent": "developer", "incarnation": s.incarnation, "upload": upload}
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
        sock.settimeout(5)
        sock.connect(str(s.path))
        sock.sendall(rf.control(1, {"op": "OPEN", "op_id": new_op_id(),
            "generation": s.generation[0], "token": s.generation[1],
            "principal": "alice", "command_center": "center", "grant_id": "grant",
            "connection_id": "git", "verb": "git_read:owner/repo", "request": request,
            "credit": 0}))
        assert rf.read_frame_blocking(sock).control()["op"] == "ADMITTED"
        expected = "UPLOAD_CREDIT" if upload else "HEAD"
        assert rf.read_frame_blocking(sock).control()["op"] == expected
        assert s.state["connected"].wait(5)
        if change == "generation":
            s.fence.barrier(2, "replacement", cancel_older=s.server._cancel_older)
        else:
            with s.book._connect() as conn:
                if change == "revoke":
                    conn.execute("UPDATE outbound_connection_grants SET revoked_at=1")
                else:
                    conn.execute("UPDATE outbound_connections SET scopes_json='[]'")
        end = rf.read_frame_blocking(sock).control()
        assert end["op"] == "END"
        assert end["outcome"] != "completed"
        assert end["side_effect_state"] == "unknown"
        assert s.secret not in str(end)


def test_route_cannot_move_to_another_proxy_or_outlive_launch(synthetic):
    s = synthetic
    with git_egress.routes(s.proxy, s.client, [s.record], "developer"):
        route_id = next(key[1] for key in git_egress._routes if key[0] == id(s.proxy))
        raw = (f"GET http://ta-git.invalid/{route_id}/owner/repo.git/info/refs"
               "?service=git-upload-pack HTTP/1.1\r\nHost: ta-git.invalid\r\n\r\n").encode()
        left, right = socket.socketpair()
        with left, right:
            assert git_egress.serve(object(), left, raw)
            assert right.recv(1024).startswith(b"HTTP/1.1 403")
    left, right = socket.socketpair()
    with left, right:
        assert git_egress.serve(s.proxy, left, raw)
        assert right.recv(1024).startswith(b"HTTP/1.1 403")
    assert not s.state["seen"]


def extension_launch(s, monkeypatch):
    import json

    from tinyassets.extension_capabilities import ExtensionCapabilities
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    # Keep incarnation reads on this fixture's real served broker.
    monkeypatch.setattr("tinyassets.broker.ledger_queries.query_ledger",
                        lambda base, **scope: s.client.ledger_query(**{
                            key: value for key, value in scope.items()
                            if key not in {"principal", "command_center"}}))
    service = Capabilities(s.workspace, ExecutionContext("center", "alice", "main"),
                           [], None, lambda: None)
    service.connections = lambda: {f"connection:git:{v}": (s.record[0], s.record[1], v)
                                   for v in s.record[1].scopes}
    unit = ExtensionCapabilities(service)
    manifest = {"schema_version": 2, "name": "git", "connections": [
        {"name": "repo", "description": "Git repository", "verbs": list(s.record[1].scopes)}]}
    installed = unit.store.install({"extension.json": json.dumps(manifest).encode()})
    unit.call("extension:activate", {"name": "git", "revision": installed["revision"],
        "expected_generation": 0,
        "bindings": {"repo": {"connection_id": "git", "grant_id": "grant"}}})
    monkeypatch.setattr(oc, "_broker_channel", lambda *a, **k: SimpleNamespace(_client=s.client))
    def dispatch(message):
        return {"capabilities": [], "extension_capabilities": unit.catalog()}
    dispatch.extension_backend = service
    return dispatch, unit


original = oc.ConnectionLedger.incarnation


def test_bash_selects_current_owner_catalog_without_manual_rewrite(synthetic, monkeypatch):
    s = synthetic
    dispatch, unit = extension_launch(s, monkeypatch)
    result = universe_tools.bash(s.workspace,
        "git clone https://git.test/owner/repo.git automatic", agent_id="main", timeout=30,
        ta_dispatch=dispatch)
    assert "[exit code 0]" in result, result
    assert s.state["seen"] and all(auth for _, auth in s.state["seen"])
    assert s.secret not in result
    assert not any(key[0] == id(s.proxy) for key in git_egress._routes)


@pytest.mark.parametrize("failure", ["identity", "owner", "broker", "proxy", "ambiguous"])
def test_unavailable_git_does_not_disable_local_bash(synthetic, monkeypatch, failure):
    s = synthetic
    dispatch, unit = extension_launch(s, monkeypatch)
    service = dispatch.extension_backend
    if failure in {"identity", "owner"}:
        service.check_authority = lambda: "owner authority unavailable"
    if failure == "broker":
        monkeypatch.setattr(oc, "_broker_channel", lambda *a, **k: None)
    if failure == "proxy":
        monkeypatch.delitem(universe_egress._PROXIES, str(s.workspace))
        monkeypatch.setattr(universe_tools, "_egress_socket", lambda _: None)
    if failure == "ambiguous":
        from dataclasses import replace
        second = replace(s.record[0], grant_id="duplicate")
        # Duplicate repository routes are refused by the transport itself.
        with pytest.raises(PermissionError):
            with git_egress.routes(s.proxy, s.client,
                    [s.record, (second, s.record[1], s.record[2])], "main"):
                pytest.fail("ambiguous grant admitted")
        service.check_authority = lambda: "ambiguous owner grant"
    result = universe_tools.bash(s.workspace, "echo local-command-ran", agent_id="main",
                                 ta_dispatch=dispatch)
    assert "[exit code 0]" in result, result
    assert "local-command-ran" in result
    assert "Authenticated git unavailable" in result
    assert not s.state["seen"]
    assert not any(key[0] == id(s.proxy) for key in git_egress._routes)


def test_upload_credit_callback_does_not_hold_event_loop_lock():
    from tinyassets.broker.git_upload import Upload

    handled = threading.Event()

    def credit():
        def next_frame():
            upload.put(b"next")
            handled.set()

        thread = threading.Thread(target=next_frame, daemon=True)
        thread.start()
        assert handled.wait(2), "credit callback holds the DATA handler's lock"
        thread.join(2)

    upload = Upload(lambda: None, credit)
    upload.put(b"first")
    assert upload.read() == b"first"
    assert handled.is_set()
