"""Native owner children through real loopback -> relay -> broker, local-only."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace

import pytest

from tests.support.admission import Plans, drive, projection, suspend
from tests.support.broker_ipc import broker  # noqa: F401
from tests.support.synthetic_git import synthetic
from tinyassets import owner_runtime
from tinyassets import rpc_frames as rf
from tinyassets.boxes.admission import OwnerRegistry
from tinyassets.boxes.boxd import OwnerBoxd
from tinyassets.boxes.relay import OwnerRelays
from tinyassets.broker.admission import local_mutate
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.public_egress import PublicEgress
from tinyassets.broker.server import BOX_HOST
from tinyassets.owner_public_egress import JAIL_PROXY_URL, PROXY_ENV


@pytest.fixture
def native(broker, tmp_path, monkeypatch, request):  # noqa: F811
    from tinyassets.boxes import bootstrap, relay

    shared = tmp_path / "shared"
    (shared / ".broker/state").mkdir(parents=True, mode=0o700)
    (shared / ".broker").chmod(0o700)

    identities = OwnerIdentities(shared / ".broker/state/owner-identities.db", initialize=True,
                                 plans=Plans(shared))
    uid = identities.resolve("alice", allocate=True).uid
    drive(identities.admit("alice", routing=projection(identities.path)))
    Plans(shared).apply("alice", tier="paid", event_created=1)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT, machine INT, "
                   "project_id INT, bhard_bytes INT)")
        db.execute("INSERT INTO box_records VALUES (?, ?, ?, ?)", ("alice", uid, uid, 1024**3))
    broker.server._roles = {os.getuid(): BOX_HOST}
    broker.server._data_root = shared
    broker.server._owner_identities = identities
    from tinyassets.storage.outbound_connections import ConnectionLedger

    broker.server._ledger_for = lambda owner: ConnectionLedger(
        shared / ".broker/outbound.db", data_root=shared,
        verify_authenticated_principal=lambda: owner)
    from tinyassets.broker.policy import PolicyStore

    broker.server._policy_store = PolicyStore(shared, identities)
    broker.server._policy_store.bootstrap()
    proxy = broker.server._public_egress = PublicEgress(
        identities, shared, binding=broker.server.egress_binding)
    # Same-uid protocol rig only. Production pins the broker's kernel role uid.
    monkeypatch.setattr(bootstrap, "peer", lambda sock: (os.getpid(), 1002, 1002))
    monkeypatch.setattr(relay, "peer", lambda sock: (os.getpid(), 1002, 1002))
    relays = OwnerRelays(tmp_path / "relay", OwnerRegistry(shared, broker.path))
    path = relays.bind("alice", os.getuid())
    root = tmp_path / "owner"
    home = root / "center"
    home.mkdir(parents=True)
    from tinyassets.owner_centers import Centers

    centers = Centers(root)
    if getattr(request, "param", True):
        centers.create("center")
    if getattr(request, "param", True) == "retired":
        centers.retire("center")
    core = OwnerBoxd(owner_id="alice", owner_root=root, state_dir=root / ".owner-boxd",
                     relay=path)
    runtime = owner_runtime.OwnerRuntime("alice", os.getuid(), root, os.getpid(), core, path)
    monkeypatch.setattr(owner_runtime, "_runtime", runtime)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    # Broker threads in this protocol rig are in the box's Python process.
    thread_id = threading.get_ident()
    refuse = owner_runtime.refuse_router_store
    monkeypatch.setattr(owner_runtime, "refuse_router_store", lambda name: (
        None if threading.get_ident() != thread_id else refuse(name)))
    try:
        yield SimpleNamespace(core=core, home=home, proxy=proxy, identities=identities,
                              broker=broker, relay=path)
    finally:
        core.close()
        relays.close()


def run(native, code, **kwargs):
    return native.core.run([sys.executable, "-c", code], cwd=native.home,
                           wall_seconds=5, output_bytes=16384, **kwargs)


@pytest.mark.verifies(
    'owner-network-egress-policy:Ask uses existing owner consent')
@pytest.mark.parametrize("method", ["GET", "CONNECT"])
@pytest.mark.parametrize("answer", ["approve", "deny", "clear", "mute"])
def test_raw_ask_creates_one_standing_proposal_and_retries(native, monkeypatch, method, answer):
    import hashlib

    from tests.test_owner_policy import _session
    from tinyassets.broker.client import BrokerClient
    from tinyassets.broker.egress import destination
    from tinyassets.daemon_server import grant_universe_ownership
    from tinyassets.storage.pending_requests import list_pending

    grant_universe_ownership(native.home.parent, universe_id="center", owner_id="alice")
    store = native.broker.server._policy_store
    cookie = "raw-proxy-owner-" + "r" * 40
    _session(native.broker.server._data_root, "alice", cookie)
    proof = {"session_hash": hashlib.sha256(cookie.encode()).hexdigest()}
    client = BrokerClient(native.relay, principal="alice", command_center="center",
                          fence=lambda: (0, ""))
    snapshot = client.owner_policy("read")
    snapshot["policy"]["egress"]["mode"] = "ask"
    client.owner_policy("write", session_proof=proof,
                        expected_generation=snapshot["generation"], policy=snapshot["policy"])
    monkeypatch.setattr("tinyassets.broker.public_egress.destination",
                        lambda binding, host, port: destination(
                            binding, host, port, resolver=lambda *_: ["8.8.8.8"]))
    target = "example.test:443" if method == "CONNECT" else "http://example.test:443/"
    head = f"{method} {target} HTTP/1.1\r\n\r\n".encode()
    code = ("import socket; s=socket.create_connection(('127.0.0.1',3128)); "
            f"s.sendall({head!r}); "
            "print(s.recv(16384).decode()); s.close()")
    for _ in range(2):
        done = run(native, code)
        assert done.returncode == 0, done.stdout
        assert b"403 Forbidden" in done.stdout
    rows = list_pending(native.home)
    rows = [row for row in rows if row["action"].get("type") == "policy_proposal"]
    assert len(rows) == 1
    proposal = rows[0]["action"]
    assert "until you remove this rule" in rows[0]["body"]
    from starlette.requests import Request

    from tinyassets import onboarding
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://app.test/mcp"})
    monkeypatch.setattr(onboarding, "_read_home", lambda *_: "center")
    session = {**proof, "expires_at": time.time() + 1000,
               "identity_json": json.dumps({"user_id": "alice"})}
    if answer != "approve":
        from tinyassets.api.pending_requests import _answer_request

        with identity_context(Identity(user_id="alice", username="alice")):
            result = _answer_request(universe_id="center", owner_session=session, payload={
                "request_id": rows[0]["request_id"], "decision": "declined",
                "dismiss": answer == "clear", "dont_ask_again": answer == "mute"})
        assert "error" not in result, result
        assert client.owner_policy("read")["generation"] == proposal["expected_generation"]
        assert not client.owner_policy("read")["policy"]["egress"]["destinations"]
        assert b"403 Forbidden" in run(native, code).stdout
        return

    async def approve(row):
        async def receive():
            return {"type": "http.request", "body": json.dumps({
                "action": "apply_proposal", "center": "center",
                "request_id": row["request_id"]}).encode()}

        request = Request({"type": "http", "method": "POST", "path": "/app/api/policy",
                           "query_string": b"scope=egress", "headers": [
                               (b"host", b"app.test"), (b"origin", b"https://app.test"),
                               (b"content-type", b"application/json")]}, receive)
        with identity_context(Identity(user_id="alice", username="alice",
                                       metadata={"router_owner_session": session})):
            return await onboarding._handle_policy(request)

    # An intervening owner edit makes the displayed proposal stale.
    current = client.owner_policy("read")
    client.owner_policy("write", session_proof=proof,
                        expected_generation=current["generation"], policy=current["policy"])
    assert asyncio.run(approve(rows[0])).status_code == 409
    assert not client.owner_policy("read")["policy"]["egress"]["destinations"]
    assert b"403 Forbidden" in run(native, code).stdout
    refreshed = list_pending(native.home)[-1]
    response = asyncio.run(approve(refreshed))
    assert response.status_code == 200, response.body
    with pytest.raises(PermissionError):
        client.owner_policy("write", session_proof=proof,
                            expected_generation=proposal["expected_generation"],
                            policy=proposal["policy"])
    assert destination(native.broker.server.egress_binding("alice"), "example.test", 443,
                       resolver=lambda *_: ["8.8.8.8"]) == ["8.8.8.8"]
    assert store.read("alice").document["egress"]["destinations"] == [
        {"host": "example.test", "port": 443}]


@pytest.fixture
def http_origin():
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802
            seen.append(self.path)
            self.send_response(200)
            self.send_header("Content-Length", "8")
            self.end_headers()
            self.wfile.write(b"local-ok")

        def do_POST(self):  # noqa: N802
            assert self.rfile.read(int(self.headers["Content-Length"])) == b"abc"
            self.do_GET()

        def log_message(self, *_):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever)
    thread.start()
    try:
        yield server.server_port, seen
    finally:
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("tunnel", [False, True])
@pytest.mark.parametrize("native", [True, False, "retired"], indirect=True,
                         ids=["center", "zero-centers", "retired-center"])
def test_owner_command_http_and_connect_reach_local_origin(
    native, http_origin, monkeypatch, tunnel,
):
    port, seen = http_origin
    checked = []
    documents = []
    serve = native.proxy.serve

    async def public(reader, writer, document):
        documents.append(document)
        await serve(reader, writer, document)

    monkeypatch.setattr(native.proxy, "serve", public)

    def addresses(binding, host, actual_port):
        assert (host, actual_port) == ("example.test", port)
        checked.append((host, actual_port))
        return ["127.0.0.1"]  # same DNS-policy seam as broker egress tests

    monkeypatch.setattr("tinyassets.broker.public_egress.destination", addresses)
    if tunnel:
        code = ("import http.client; "
                "c=http.client.HTTPConnection('127.0.0.1',3128,timeout=3); "
                f"c.set_tunnel('example.test',{port}); c.request('GET','/download'); "
                "print(c.getresponse().read().decode()); c.close()")
    else:
        code = ("import urllib.request; "
                f"print(urllib.request.urlopen('http://example.test:{port}/download',"
                "timeout=3).read().decode())")
    forwarder = native.core._public_forwarder
    for _ in range(2):
        done = run(native, code)
        assert done.returncode == 0, done.stdout
        assert done.stdout == b"local-ok\n"
        assert native.core._public_forwarder is forwarder
    assert seen == ["/download", "/download"]
    assert checked == [("example.test", port)] * 2
    assert documents == [{"op": "BOX_PUBLIC", "principal": "alice"}] * 2
    assert not native.core._running


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_native_bridge_preserves_body_and_response_after_half_close(
    native, http_origin, monkeypatch,
):
    port, seen = http_origin

    def addresses(binding, host, actual_port):
        assert (host, actual_port) == ("example.test", port)
        return ["127.0.0.1"]

    monkeypatch.setattr("tinyassets.broker.public_egress.destination", addresses)
    request = (f"POST http://example.test:{port}/upload HTTP/1.1\r\n"
               "Content-Length: 3\r\n\r\nabc").encode()
    done = run(native, "import socket; c=socket.create_connection(('127.0.0.1',3128),3); "
               f"c.sendall({request!r}); c.shutdown(socket.SHUT_WR); "
               "print(c.makefile('rb').read().decode()); c.close()")
    assert done.returncode == 0, done.stdout
    assert b"200 OK" in done.stdout
    assert done.stdout.endswith(b"local-ok\n")
    assert seen == ["/upload"]


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_every_owner_command_gets_proxy_defaults_without_ambient_proxy(native, monkeypatch):
    for name in PROXY_ENV:
        monkeypatch.setenv(name, "unusable-ambient-proxy")
    code = ("import os,json; print(json.dumps({k:os.environ[k] for k in "
            + repr(list(PROXY_ENV)) + "}))")
    done = run(native, code)
    assert done.returncode == 0
    assert json.loads(done.stdout) == PROXY_ENV
    assert JAIL_PROXY_URL == "http://127.0.0.1:3128"
    from tinyassets.agent_loop.owner_tools import OwnerTools
    from tinyassets.wake_conditions import run_probe

    command = f"test \"$HTTPS_PROXY\" = '{JAIL_PROXY_URL}'"
    assert run_probe(native.home, {"owner": "alice"}, {"command": command})
    op_id = native.core.operation_prefix() + "proxy"
    assert asyncio.run(OwnerTools(native.home).bash(op_id, command)) == "[exit code 0]"


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("refusal", ["admission", "registry", "tombstone", "billing", "abuse",
                                   "private", "metadata", "owner_cap", "host_cap"])
def test_native_proxy_refuses_without_opening_any_upstream(native, monkeypatch, refusal):
    opened = []
    real_open = asyncio.open_connection

    async def record_open(*args, **kwargs):
        opened.append(args)
        return await real_open(*args, **kwargs)

    monkeypatch.setattr(asyncio, "open_connection", record_open)
    host = "169.254.169.254" if refusal == "metadata" else "127.0.0.1"
    if refusal == "admission":
        with sqlite3.connect(native.identities.path) as db:
            db.execute("DELETE FROM owner_admission")
    elif refusal == "registry":
        with sqlite3.connect(native.identities.path) as db:
            db.execute("DELETE FROM box_records")
    elif refusal == "tombstone":
        drive(local_mutate(native.identities.path, "alice", "erase",
            routing=projection(native.identities.path)))
    elif refusal in {"billing", "abuse"}:
        suspend(native.identities.plans, "alice", reason=refusal, suspended=True)
    elif refusal == "owner_cap":
        native.proxy.active["alice"] = 32
    elif refusal == "host_cap":
        native.proxy.active["other"] = 64
    code = ("import socket; c=socket.create_connection(('127.0.0.1',3128),3); "
            f"c.sendall(b'CONNECT {host}:80 HTTP/1.1\\r\\n\\r\\n'); "
            "c.shutdown(socket.SHUT_WR); "
            "\ntry: print(repr(c.recv(4096)))\nexcept ConnectionResetError: print('refused')\n"
            "c.close()")
    before = dict(native.proxy.active)
    done = run(native, code)
    assert done.returncode == 0, done.stdout
    if refusal in {"private", "metadata"}:
        assert b"HTTP/1.1 403 Forbidden" in done.stdout
    else:
        assert done.stdout in {b"b''\n", b"refused\n"}, done.stdout
    assert not opened
    assert native.proxy.active == before


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("document", [
    {"op": "BOX_PUBLIC", "center": "center"},
    {"op": "BOX_PUBLIC", "center": "foreign"},
    {"op": "BOX_PUBLIC", "principal": "alice"},
    {"op": "BOX_PUBLIC", "principal": "bob"},
    {"op": "BOX_CONNECT", "principal": "bob"},
    {"op": "BOX_SHARED", "center": "center"},
    {"op": "BOX_CHANNEL"},
    {"op": "UNKNOWN"},
])
def test_invalid_relay_operations_refuse_before_broker_contact(native, monkeypatch, document):
    from tinyassets.boxes import relay

    contacts = []

    def broker_peer(sock):
        contacts.append(sock)
        return os.getpid(), 1002, 1002

    monkeypatch.setattr(relay, "peer", broker_peer)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(3)
        channel.connect(str(native.relay))
        channel.sendall(rf.control(rf.CONNECTION, document))
        assert rf.read_frame_blocking(channel) is None
    assert contacts == []
    assert not native.proxy.active


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_provisioning_package_child_inherits_proxy_and_uses_broker(
    native, http_origin, monkeypatch, tmp_path,
):
    from tinyassets.workspace_provision import admit_node
    from tinyassets.workspace_provision_execution import execute_provision
    from tinyassets.workspace_resolver import ProvisionManifests

    port, seen = http_origin

    def addresses(binding, host, actual_port):
        assert (host, actual_port) == ("example.test", port)
        return ["127.0.0.1"]

    monkeypatch.setattr("tinyassets.broker.public_egress.destination", addresses)
    package = '{"name":"empty","version":"1.0.0"}'
    lock = '{"name":"empty","version":"1.0.0","lockfileVersion":3,"packages":{}}'
    plan = admit_node(package, lock)
    lease = native.home / "workspaces" / "lease"
    repo = lease / "repo"
    repo.mkdir(parents=True)
    (repo / "package.json").write_text(plan.normalized_package_json)
    (repo / "package-lock.json").write_text(plan.normalized_lockfile)
    # Substitute only the package manager: execute_provision, stage, native
    # child, inherited env, forwarder, relay and broker all remain real.
    binaries = tmp_path / "bin"
    binaries.mkdir()
    npm = binaries / "npm"
    npm.write_text(f"#!{sys.executable}\nimport os,sys,urllib.request\n"
                   "assert sys.argv[1:] == ['ci','--ignore-scripts','--no-audit','--no-fund']\n"
                   f"assert all(os.environ[k] == v for k,v in {PROXY_ENV!r}.items())\n"
                   f"assert urllib.request.urlopen('http://example.test:{port}/package',"
                   "timeout=3).read() == b'local-ok'\n")
    npm.chmod(0o700)
    monkeypatch.setenv("PATH", str(binaries) + os.pathsep + os.environ["PATH"])
    result = execute_provision(
        ProvisionManifests(None, plan), lease_path=lease, max_transfer_bytes=1024**2,
        storage_bound=1024**2, timeout_s=5, cancelled=lambda: False,
        universe_dir=native.home, principal="alice",
    )
    assert result.failure is None
    assert result.bytes_to_charge == 0
    assert seen == ["/package"]


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_daemon_close_joins_active_forwarder_and_releases_listener(native):
    forwarder = native.core._public_forwarder
    with socket.create_connection(  # hermetic-ok: fixed box-local loopback only
        ("127.0.0.1", 3128), timeout=3,
    ) as guest:
        deadline = time.monotonic() + 3
        while not forwarder._workers and time.monotonic() < deadline:
            time.sleep(.01)
        assert forwarder._workers
        native.core.close()
        assert guest.recv(4096) == b""
    assert not forwarder._thread.is_alive()
    assert not forwarder._workers
    with pytest.raises(OSError):
        socket.create_connection(  # hermetic-ok: local listener refusal, no public network
            ("127.0.0.1", 3128), timeout=1,
        )
    # Restart after an idle sleep can immediately own the fixed loopback port.
    fresh = OwnerBoxd(owner_id="alice", owner_root=native.home.parent,
                      state_dir=native.home.parent / ".owner-boxd", relay=native.relay)
    try:
        assert fresh._public_forwarder._thread.is_alive()
    finally:
        fresh.close()


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_shutdown_interrupts_stalled_relay_admission(native, tmp_path):
    forwarder = native.core._public_forwarder
    path = tmp_path / "stalled.sock"
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(path))
        listener.listen(1)
        listener.settimeout(3)
        forwarder.relay = path
        with socket.create_connection(  # hermetic-ok: fixed box-local loopback only
            ("127.0.0.1", 3128), timeout=3,
        ) as guest:
            relay, _ = listener.accept()
            with relay:
                relay.settimeout(3)
                assert rf.read_frame_blocking(relay).control() == {"op": "BOX_PUBLIC"}
                started = time.monotonic()
                native.core.close()
                assert time.monotonic() - started < 3
                assert guest.recv(1) == relay.recv(1) == b""
    assert not forwarder._workers
    assert not forwarder._thread.is_alive()


@pytest.fixture
def git_origin(native, tmp_path, monkeypatch):
    # The two real brokers must have distinct Unix socket/ledger paths.
    yield from synthetic.__wrapped__(tmp_path / "git-origin", monkeypatch)


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("mode", ["open", "allowlist", "ask"])
def test_authenticated_git_uses_real_registry_relay_alongside_public_proxy(
        native, git_origin, mode):
    import hashlib

    from tests.test_owner_policy import _session
    from tinyassets.broker.client import BrokerClient
    from tinyassets.daemon_server import grant_universe_ownership
    from tinyassets.owner_git import routes

    # Keep the real registry, BOX_HOST admission and relay from the native
    # rig; attach the existing synthetic git origin and credential dispatcher.
    native.broker.server._ledger_for = git_origin.ledger
    native.broker.server._dispatch_for = lambda *_: git_origin.dispatch
    client = BrokerClient(native.relay, principal="alice", command_center="center",
                          fence=lambda: (0, ""))
    grant_universe_ownership(native.home.parent, universe_id="center", owner_id="alice")
    cookie = "git-owner-session-" + "g" * 40
    _session(native.broker.server._data_root, "alice", cookie)
    proof = {"session_hash": hashlib.sha256(cookie.encode()).hexdigest()}

    def policy(destinations):
        current = client.owner_policy("read")
        current["policy"]["egress"] = {"mode": mode, "destinations": destinations}
        client.owner_policy("write", session_proof=proof,
                            expected_generation=current["generation"], policy=current["policy"])

    policy([])
    with routes(client, [git_origin.record], "developer") as env:
        if mode != "open":
            denied = native.core.run(["git", "ls-remote", "https://git.test/owner/repo.git"],
                                    cwd=native.home, env=env, wall_seconds=5, output_bytes=16384)
            assert denied.returncode != 0
            assert not git_origin.state["seen"]
            assert git_origin.secret.encode() not in denied.stdout
            policy([{"host": "git.test", "port": 443}])
        done = native.core.run(["git", "ls-remote", "https://git.test/owner/repo.git"],
                               cwd=native.home, env=env, wall_seconds=5, output_bytes=16384)
        assert done.returncode == 0, done.stdout
        assert git_origin.secret.encode() not in done.stdout
    assert git_origin.state["seen"] == [("/owner/repo.git/info/refs?service=git-upload-pack", True)]
    assert not native.proxy.active
    assert native.core._public_forwarder._thread.is_alive()
    # A foreign center is still refused by broker admission on the same relay.
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(3)
        channel.connect(str(native.relay))
        channel.sendall(rf.control(rf.CONNECTION, {"op": "BOX_CONNECT", "center": "foreign"}))
        assert rf.read_frame_blocking(channel) is None


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("op", ["BOX_CONNECT", "BOX_PUBLIC"])
def test_relay_bootstrap_refuses_fenced_owner_with_retained_admission(native, monkeypatch, op):
    from tinyassets.broker.admission import local_mutate

    document = {"op": op}

    def connect():
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
            channel.settimeout(5)
            channel.connect(str(native.relay))
            channel.sendall(rf.control(rf.CONNECTION, document))
            return rf.read_frame_blocking(channel)

    assert connect().control() == {"op": "BOX_CONNECTED"}
    # This rig combines host and box Python contexts. Fence from the host side.
    with monkeypatch.context() as host:
        host.setattr(owner_runtime, "_runtime", None)
        drive(local_mutate(native.identities.path, "alice", "erase",
            routing=projection(native.identities.path)))
    from tinyassets.broker.admission import read

    assert read(native.identities, "alice").state == "erased"
    assert connect() is None
