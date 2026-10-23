"""One admitted owner, real relay and HTTP unix socket, existing model wire double."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import signal
import socket
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import uvicorn

from tests import test_interactive_http_agent as model
from tinyassets import owner_runtime
from tinyassets.boxes.relay import OwnerRelays

reader = model.reader
served = model.served
agent = model.agent
_real_engine_client = model.engine_tool_client._make_client
_real_resolve_proxy = model.ApiKeyHttpProvider._resolve_proxy


@pytest.fixture(autouse=True)
def _owner_box_double(monkeypatch, _request_admission_hmac_key, _identity_fingerprint_key):
    """Seed existing fixtures before startup; then use the real local mode."""
    from tests.support.box_double import install
    from tinyassets.control_plane import binding
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    for name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        monkeypatch.delenv(name, raising=False)

    real = binding.file_binding
    double = install(monkeypatch)
    try:
        yield SimpleNamespace(double=double, file_binding=real)
    finally:
        double.close()


@pytest.fixture
def owner_mode(tmp_path, monkeypatch, agent, in_process_broker, _owner_box_double, request):
    from tinyassets import owner_public_egress
    from tinyassets.api import runs as api_runs

    # The real lifespan now owns boot recovery. Its process-lifetime lock must
    # not survive this fixture and suppress recovery in a later test's root.
    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_DONE", False)
    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_LOCK", None)
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    monkeypatch.setenv("TINYASSETS_VOLUME_GENERATION", "1")
    monkeypatch.setattr(owner_runtime, "OWNER_MOUNT", tmp_path)
    monkeypatch.setattr(owner_runtime, "_is_owner_mount", lambda path: path == tmp_path)
    # The CLI subprocess proof shares this fixture's host netns. Production
    # boxes bind 3128 in separate null netns; the native egress rig proves it.
    monkeypatch.setattr(owner_public_egress, "LISTEN_ADDRESS", ("127.0.0.1", 0))
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "0")
    attempts = []
    real_connect, real_connect_ex = socket.socket.connect, socket.socket.connect_ex

    def guarded(original):
        def connect(sock, address):
            if sock.family in (socket.AF_INET, socket.AF_INET6):
                if not ipaddress.ip_address(address[0]).is_loopback:
                    attempts.append(address)
                    raise AssertionError(f"outbound network attempted: {address}")
            return original(sock, address)

        return connect

    monkeypatch.setattr(socket.socket, "connect", guarded(real_connect))
    monkeypatch.setattr(socket.socket, "connect_ex", guarded(real_connect_ex))
    with tempfile.TemporaryDirectory(prefix="taod-") as directory:
        # WHOAMI must not call any admission/broker method: bind fixed the identity.
        import sqlite3

        from tinyassets.boxes import relay as relay_module
        from tinyassets.broker.server import BOX_HOST

        live = in_process_broker.supervisor_for(tmp_path)
        live.server._roles = {os.getuid(): BOX_HOST}
        live.server._data_root = tmp_path
        from tinyassets.storage.outbound_connections import CredentialBlindBroker, UpstreamStream

        secret = "synthetic-broker-only-chat-credential"

        def network(**kwargs):
            assert kwargs["credential"] == secret
            kwargs["on_connect"](None)
            document = kwargs["request"]
            agent.wires.append((kwargs["verb"], document))
            learning = any(message.get("content") == model.universe_intelligence._LEARNING_SYSTEM
                           for message in document["body"]["messages"])
            use_tool = (not learning and len(agent.wires) <= agent.requested_rounds
                        and document["body"].get("tool_choice") != "none")
            message = {"role": "assistant", "content": "{}" if learning else
                       None if use_tool else "finished exact answer"}
            if use_tool:
                message["tool_calls"] = [{"id": "wire-id", "type": "function", "function": {
                    "name": agent.tool_call[0], "arguments": agent.tool_call[1]}}]
            body = json.dumps({"model": "actual-answer-model", "choices": [{
                "message": message, "finish_reason": "tool_calls" if use_tool else "stop"}]})
            return UpstreamStream.complete({"status": 200, "headers": {}, "body": body}, ())

        def dispatch_for(owner, center, grant, resource):
            return CredentialBlindBroker(
                live._dispatchers.ledger_for(owner), resolve_credential=lambda *_: secret,
                network_request=network).dispatch

        live.server._dispatch_for = dispatch_for
        monkeypatch.setattr(model.ApiKeyHttpProvider, "_resolve_proxy", _real_resolve_proxy)
        uid = live.identities.owner_machine("owner")
        with sqlite3.connect(live.identities.path) as db:
            db.execute("CREATE TABLE IF NOT EXISTS box_records (principal TEXT PRIMARY KEY, "
                       "machine INTEGER, project_id INTEGER, bhard_bytes INTEGER)")
            db.execute("INSERT OR REPLACE INTO box_records VALUES (?, ?, ?, ?)",
                       ("owner", uid, uid, 1024**3))
            from tinyassets.broker.owner_volumes import provision as bind_volume
            bind_volume(db, "owner", uid)
        # Seed the published-only store, as the cutover will; owner operational
        # state remains in tmp_path and is never read by this shared query.
        from tinyassets.storage import db_path
        published = tmp_path / ".published"
        published.mkdir()
        with sqlite3.connect(db_path(tmp_path)) as source:
            with sqlite3.connect(db_path(published)) as destination:
                source.backup(destination)

        class Admission:
            broker_socket = live.socket_path



        monkeypatch.setattr(relay_module, "peer", lambda sock: (os.getpid(), 1002, 1002))
        relay = OwnerRelays(Path(directory), Admission())
        path = relay.bind("owner", os.getuid())
        from tinyassets.owner_centers import Centers

        centers = Centers(tmp_path)
        for center in getattr(request, "param", ()):
            centers.create(center)
        runtime = owner_runtime.start(path)
        assert runtime.tools._public_forwarder._thread.is_alive()
        from tinyassets.control_plane import binding

        monkeypatch.setattr(binding, "file_binding", _owner_box_double.file_binding)
        try:
            yield SimpleNamespace(runtime=runtime, directory=Path(directory), relay=path)
        finally:
            runtime.tools.close()
            relay.close()
            if api_runs._RUNS_RECOVERY_LOCK is not None:
                from tinyassets.singleton_lock import release_singleton_lock

                release_singleton_lock(api_runs._RUNS_RECOVERY_LOCK)
    assert attempts == []


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tinyassets import daemon_server, provider_serving_binding

    home = daemon_server.get_founder_home
    authority = provider_serving_binding.resolve_current_serving_provider_authority
    result = model.rig.__wrapped__(tmp_path, monkeypatch)
    monkeypatch.setattr(daemon_server, "get_founder_home", home)
    monkeypatch.setattr(
        provider_serving_binding, "resolve_current_serving_provider_authority", authority
    )
    return result


def test_startup_refuses_missing_or_unbound_relay(tmp_path, monkeypatch):
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    with pytest.raises(OSError):
        owner_runtime.start(tmp_path / "absent.sock")
    assert owner_runtime.current() is None


def test_startup_refuses_a_negative_relay_answer(tmp_path, monkeypatch):
    from tinyassets import rpc_frames as rf

    monkeypatch.setattr(owner_runtime, "_runtime", None)
    monkeypatch.setenv("TINYASSETS_VOLUME_GENERATION", "1")
    monkeypatch.setattr(owner_runtime, "OWNER_MOUNT", tmp_path)
    monkeypatch.setattr(owner_runtime, "_is_owner_mount", lambda path: path == tmp_path)
    with tempfile.TemporaryDirectory(prefix="taod-refused-") as directory:
        path = Path(directory) / "relay.sock"
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
            listener.bind(str(path))
            listener.listen(1)

            def refuse():
                channel, _ = listener.accept()
                with channel:
                    assert rf.read_frame_blocking(channel).control() == {"op": "WHOAMI"}
                    channel.sendall(rf.control(rf.CONNECTION, {"op": "REFUSED"}))

            thread = threading.Thread(target=refuse)
            thread.start()
            try:
                with pytest.raises(PermissionError, match="refused startup admission"):
                    owner_runtime.start(path)
                assert owner_runtime.current() is None
            finally:
                thread.join(5)
                assert not thread.is_alive()


@pytest.mark.parametrize("owner_mode", [(), ("first", "second", "third")], indirect=True)
def test_relay_whoami_is_bound_identity_without_a_center(owner_mode):
    from tinyassets import rpc_frames as rf

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(2)
        channel.connect(str(owner_mode.relay))
        channel.sendall(rf.control(rf.CONNECTION, {"op": "WHOAMI"}))
        assert rf.read_frame_blocking(channel).control() == {
            "op": "OWNER_IS", "principal": "owner", "uid": os.getuid(),
        }
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(2)
        channel.connect(str(owner_mode.relay))
        channel.sendall(rf.control(rf.CONNECTION, {"op": "WHOAMI", "principal": "request-owner"}))
        assert rf.read_frame_blocking(channel) is None


def test_owner_admission_and_local_tools(owner_mode, monkeypatch):
    from tinyassets import platform_runtime_provenance as provenance
    from tinyassets import universe_tools
    from tinyassets.agent_loop import served_chat
    from tinyassets.control_plane.binding import current_binding, file_binding
    from tinyassets.universe_files import ensure_data_directory, read_data_path, write_data_path

    def forbidden(*args, **kwargs):
        raise AssertionError("shared BoxProvider/exec must not be called")

    assert not hasattr(served_chat, "configured_box_provider")
    assert not hasattr(owner_mode.runtime.tools, "start_exec")
    monkeypatch.setattr(provenance._PROCESS_OBSERVATION, "observe", forbidden)
    monkeypatch.setattr(provenance._PROCESS_OBSERVATION, "peek", forbidden)
    root = owner_mode.runtime.root / "center"
    root.mkdir()
    assert provenance.resolve_process_cloud_admission().verdict == "owner-box"
    assert provenance.admitted_cloud_executor_class() == "owner-box"
    assert current_binding() is None and file_binding(root) is None
    from tinyassets.providers.base import get_sandbox_status

    assert get_sandbox_status()["box_available"] is True
    ensure_data_directory(root / "empty/nested")
    assert (root / "empty/nested").is_dir()
    write_data_path(root / "plain.txt", "ordinary local data")
    assert read_data_path(root / "plain.txt") == b"ordinary local data"
    from tinyassets.storage_accounting import _center_bytes

    assert _center_bytes(root, scope="center") == len(b"ordinary local data")
    assert "wrote" in universe_tools.write_file(root, "notes/test.txt", "one", agent_id="main")
    assert "edited" in universe_tools.edit_file(
        root, "notes/test.txt", "one", "two", agent_id="main"
    )
    assert "two" in universe_tools.read_file(root, "notes/test.txt", agent_id="main")
    assert "error:" in universe_tools.read_file(root, "absent.txt", agent_id="main")
    assert "error:" in universe_tools.edit_file(root, "absent.txt", "one", "two", agent_id="main")
    assert "local shell" in universe_tools.bash(root, "echo 'local shell'", agent_id="main")
    assert "killed" in universe_tools.bash(root, "sleep 30", timeout=1, agent_id="main")


@pytest.mark.parametrize("tool", ["write", "bash"])
def test_app_mcp_and_chat_over_unix_socket(owner_mode, agent, monkeypatch, tool):
    from tinyassets import universe_server
    from tinyassets.agent_loop import served_chat
    from tinyassets.auth import middleware as auth
    from tinyassets.auth.provider import Identity
    from tinyassets.control_plane.binding import current_binding
    from tinyassets.providers import call

    def forbidden(*args, **kwargs):
        raise AssertionError("shared box binding/provider must not be called")

    assert not hasattr(served_chat, "configured_box_provider")
    assert not hasattr(owner_mode.runtime.tools, "start_exec")
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(call, "make_interactive_agent_turn", agent.factory)
    monkeypatch.setattr(model.engine_tool_client, "_make_client", _real_engine_client)
    agent.tool_call = (
        "write",
        json.dumps({"path": "notes/from-chat.txt", "content": "owner wrote this"}),
    )
    if tool == "bash":
        agent.tool_call = ("bash", json.dumps({"command": (
            "mkdir -p notes; ta call read_graph --json '{\"target\":\"status\"}' "
            "> notes/ta-status.json && printf 'owner wrote this' > notes/from-chat.txt"
        )}))
    root = agent.served.context.universe_dir
    (root / "soul.md").write_text("# Owner soul", encoding="utf-8")
    (root / "hello.txt").write_text("owner data", encoding="utf-8")

    import base64
    import secrets
    from dataclasses import asdict

    from tinyassets.owner_ingress import OwnerIngress

    identity = Identity(user_id="owner", username="owner", capabilities=[
        "tinyassets.universe.write", "tinyassets.extensions.read",
        "tinyassets.extensions.write", "tinyassets.extensions.admin"])

    class Auth:
        def resolve_token(self, token):
            pytest.fail("owner must authenticate the router, not a public bearer")

        def is_auth_required(self):
            return True

    monkeypatch.setattr(auth, "_provider", Auth())
    secret = secrets.token_urlsafe(32)
    secret_file = owner_mode.directory / "http-secret"
    secret_file.write_text(secret)
    app = OwnerIngress(universe_server.create_streamable_http_app(), secret_file)
    uds = owner_mode.directory / "app.sock"
    server = uvicorn.Server(uvicorn.Config(app, uds=str(uds), log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started
        headers = {"x-box-secret": secret, "x-owner": "owner",
                   "x-owner-identity": base64.urlsafe_b64encode(
                       json.dumps(asdict(identity)).encode()).decode()}
        with httpx.Client(
            transport=httpx.HTTPTransport(uds=str(uds)),
            base_url="http://localhost",
            headers=headers,
            timeout=60,
        ) as client:
            response = client.post(
                "/app/api/file", json={"graph_id": root.name, "path": "hello.txt"}
            )
            assert response.status_code == 200, response.text
            assert response.text == "owner data"

        async def mcp_calls():
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport

            def http_client(**kwargs):
                return httpx.AsyncClient(
                    transport=httpx.AsyncHTTPTransport(uds=str(uds)), headers=headers, timeout=60
                )

            async with Client(
                StreamableHttpTransport("http://localhost/mcp", httpx_client_factory=http_client)
            ) as client:
                read = await client.call_tool(
                    "read_graph", {"target": "status", "graph_id": root.name}
                )
                assert not read.is_error, read
                assert root.name in str(read), read
                reply = await client.call_tool(
                    "converse", {"graph_id": root.name, "message": "write a note"}
                )
                assert not reply.is_error, reply
                assert "finished exact answer" in str(reply), reply

        asyncio.run(mcp_calls())
        assert (root / "notes/from-chat.txt").read_text() == "owner wrote this"
        if tool == "bash":
            assert root.name in (root / "notes/ta-status.json").read_text()
        assert current_binding() is None
        assert agent.latest().state == "completed"
    finally:
        server.should_exit = True
        thread.join(30)
        assert not thread.is_alive()


def test_pause_and_resume_gate_the_owner_tick(owner_mode, agent, monkeypatch):
    from tests.test_automations import NOW, _consumer_with_inline_executor, _seed_branch
    from tinyassets import automations
    from tinyassets.api.universe import _action_control_daemon

    root = agent.served.context.universe_dir
    _seed_branch(root.parent, author="owner")
    stored = automations.register_automation(
        root.parent, universe_id=root.name, owner_principal_id="owner", name="Due work",
        branch_def_id="branch_automation_demo", interval_seconds=600, now=NOW,
    )
    ran = []
    monkeypatch.setattr(automations, "run_due_automation",
                        lambda base, automation, due_at, **kw:
                        ran.append(automation.automation_id) or "ok:ran:test-run")
    consumer, inline = _consumer_with_inline_executor(root.parent)
    try:
        paused = json.loads(_action_control_daemon(universe_id=root.name, text="pause"))
        assert paused["status"] == "signal_written"
        assert (root / ".pause").is_file()
        assert consumer.poll_once() == 0
        assert not inline.submissions and not ran
        resumed = json.loads(_action_control_daemon(universe_id=root.name, text="resume"))
        assert "error" not in resumed, resumed
        assert not (root / ".pause").exists()
        assert consumer.poll_once() == 1
        assert ran == [stored.automation_id]
    finally:
        consumer.stop()


def test_cli_starts_a_real_subprocess_on_unix_socket(owner_mode):
    import subprocess
    import sys

    uds = owner_mode.directory / "subprocess.sock"
    secret_file = owner_mode.directory / "box-secret"
    secret_file.write_text("s" * 43)
    secret_file.chmod(0o400)
    with tempfile.TemporaryFile() as log:
        mount = owner_mode.runtime.root / "subprocess"
        mount.mkdir()
        # A test process has no boxhostd mount: point the fixed mount at a temp
        # tree, then run the real CLI entry unchanged.
        launcher = (
            "import os, sys; from pathlib import Path; import tinyassets.owner_runtime as r; "
            f"r.OWNER_MOUNT = Path({str(mount)!r}); r._is_owner_mount = lambda p: True; "
            "os.environ['TINYASSETS_VOLUME_GENERATION'] = '1'; "
            "from tinyassets.owner_daemon import main; "
            f"sys.argv = ['owner_daemon', '--socket', {str(uds)!r}, "
            f"'--relay', {str(owner_mode.relay)!r}, '--secret-file', {str(secret_file)!r}]; "
            "main()"
        )
        process = subprocess.Popen([sys.executable, "-c", launcher],
                                   stdout=log, stderr=log)
        try:
            deadline = time.monotonic() + 30
            while not uds.exists() and process.poll() is None and time.monotonic() < deadline:
                time.sleep(.05)
            if not uds.exists():
                log.seek(0)
                pytest.fail(log.read().decode())
            with httpx.Client(transport=httpx.HTTPTransport(uds=str(uds)),
                              base_url="http://localhost") as client:
                response = client.get("/mcp")
                assert response.status_code == 401, response.text
        finally:
            process.terminate()
            process.wait(timeout=30)
        # Uvicorn restores and re-raises SIGTERM after its graceful lifespan exit.
        assert process.returncode in (0, -signal.SIGTERM)


def test_mcp_scope_gates_without_a_box_auth_provider(owner_mode, agent, monkeypatch):
    """Router-authenticated MCP calls that go through require_action_scope.

    ``_provider`` is unset and the box has neither ``UNIVERSE_SERVER_AUTH`` nor
    ``UNIVERSE_SERVER_DEV_USER``. Reads succeed. A read-only identity is refused
    at the scope gate instead of the dev-provider crash.
    """
    import base64
    import secrets
    from dataclasses import asdict

    from tinyassets.auth import middleware as auth
    from tinyassets.auth.provider import Identity
    from tinyassets.owner_ingress import OwnerIngress
    from tinyassets.universe_server import create_streamable_http_app

    created = []

    def create_provider():
        created.append("provider")
        raise AssertionError("owner box instantiated an auth provider")

    monkeypatch.setattr(auth, "_provider", None)
    monkeypatch.setattr(auth, "create_provider", create_provider)
    monkeypatch.delenv("UNIVERSE_SERVER_AUTH", raising=False)
    monkeypatch.delenv("UNIVERSE_SERVER_DEV_USER", raising=False)
    graph_id = agent.served.context.universe_dir.name
    secret = secrets.token_urlsafe(32)
    secret_file = owner_mode.directory / "scope-secret"
    secret_file.write_text(secret)

    def headers_for(*capabilities):
        identity = Identity(user_id="owner", username="owner", capabilities=list(capabilities))
        return {
            "x-box-secret": secret,
            "x-owner": "owner",
            "x-owner-identity": base64.urlsafe_b64encode(
                json.dumps(asdict(identity)).encode()
            ).decode(),
        }

    app = OwnerIngress(create_streamable_http_app(), secret_file)
    uds = owner_mode.directory / "scope.sock"
    server = uvicorn.Server(uvicorn.Config(app, uds=str(uds), log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        deadline = time.monotonic() + 30
        while not server.started and thread.is_alive() and time.monotonic() < deadline:
            time.sleep(0.05)
        assert server.started

        async def exercise():
            from fastmcp import Client
            from fastmcp.client.transports import StreamableHttpTransport

            def client_for(headers):
                def http_client(**kwargs):
                    return httpx.AsyncClient(
                        transport=httpx.AsyncHTTPTransport(uds=str(uds)),
                        headers=headers,
                        timeout=60,
                    )

                return Client(
                    StreamableHttpTransport(
                        "http://localhost/mcp", httpx_client_factory=http_client
                    )
                )

            async def call(client, name, arguments):
                result = await client.call_tool(name, arguments)
                assert not result.is_error, result
                return json.loads(result.content[0].text)

            authorized = headers_for("read", "write", "costly", "submit_request", "list")
            async with client_for(authorized) as client:
                branches = await call(client, "read_graph", {"target": "branches", "limit": 5})
                assert isinstance(branches.get("branches"), list), branches
                graphs = await call(client, "read_graph", {"target": "graphs", "limit": 5})
                centers = graphs.get("command_centers")
                assert isinstance(centers, list) and any(
                    row.get("id") == graph_id for row in centers
                ), graphs
                graph = await call(
                    client, "read_graph", {"target": "graph", "graph_id": graph_id}
                )
                assert graph.get("command_center_id") == graph_id, graph
                page = await call(client, "read_page", {"query": "owner"})
                assert isinstance(page.get("results"), list), page
                for name, arguments in (
                    ("write_graph", {
                        "target": "branch", "operation": "create", "payload_json": "{}",
                    }),
                    ("run_graph", {"branch_def_id": "missing-branch", "graph_id": graph_id}),
                    ("write_page", {
                        "scope": "commons",
                        "page": "owner-box-scope",
                        "category": "notes",
                        "content": "owner box scope",
                    }),
                ):
                    body = await call(client, name, arguments)
                    assert body.get("auth_scope_required") is not True, (name, body)

            readonly = headers_for("read", "list")
            async with client_for(readonly) as client:
                still_readable = await call(
                    client, "read_graph", {"target": "branches", "limit": 5}
                )
                assert "branches" in still_readable, still_readable
                for name, arguments in (
                    ("write_graph", {
                        "target": "branch", "operation": "create", "payload_json": "{}",
                    }),
                    ("run_graph", {"branch_def_id": "missing-branch", "graph_id": graph_id}),
                    ("write_page", {
                        "scope": "commons",
                        "page": "owner-box-scope-denied",
                        "category": "notes",
                        "content": "must not land",
                    }),
                ):
                    body = await call(client, name, arguments)
                    assert body.get("auth_scope_required") is True, body

        asyncio.run(exercise())
        assert created == []
    finally:
        server.should_exit = True
        thread.join(30)
        assert not thread.is_alive()


def test_refuses_to_start_without_the_boxhostd_owner_mount(tmp_path, monkeypatch):
    """No caller-chosen data root: outside the owner mount nothing starts."""
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    monkeypatch.setenv("TINYASSETS_VOLUME_GENERATION", "1")
    monkeypatch.setattr(owner_runtime, "OWNER_MOUNT", tmp_path / "not-a-mount")
    with pytest.raises(PermissionError, match="owner mount"):
        owner_runtime.start(tmp_path / "relay.sock")
    assert owner_runtime.current() is None
    assert not (tmp_path / "not-a-mount").exists()


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T8')
