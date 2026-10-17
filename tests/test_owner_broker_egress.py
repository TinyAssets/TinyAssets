"""Real relay/broker/grant/accounting, separate box and shared data roots."""
from __future__ import annotations

import asyncio
import json
import os
import socket
import sqlite3
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError

import httpx
import pytest

from tests.support.broker_double import seed_owner_admission
from tests.support.broker_ipc import broker  # noqa: F401
from tinyassets import owner_runtime
from tinyassets.boxes.lifecycle import admit_center
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.server import BOX_HOST
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig
from tinyassets.providers.definition import ProviderDefinition, _definition_id
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    CredentialBlindBroker,
    UpstreamStream,
)

SECRET = "broker-only-synthetic-provider-secret"


@pytest.fixture
def box(broker, tmp_path, monkeypatch):  # noqa: F811
    from tinyassets.boxes import relay as relays
    from tinyassets.broker.owner_reads import Plans
    from tinyassets.broker.policy import PolicyStore
    from tinyassets.commons_catalog import publish_definition
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    for name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        monkeypatch.delenv(name, raising=False)

    shared, root = tmp_path / "shared", tmp_path / "box"
    shared.mkdir()
    root.mkdir()
    seed_owner_admission(shared, principal="owner", center="center")
    identities = OwnerIdentities(shared / ".broker/owner-identities.db")
    uid = identities.owner_machine("owner")
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INTEGER, "
                   "project_id INTEGER, bhard_bytes INTEGER)")
        db.execute("INSERT INTO box_records VALUES (?, ?, ?, ?)", ("owner", uid, uid, 1024**3))
    Plans(shared).apply("owner", tier="paid", event_created=1)
    published = publish_definition(shared / ".published", author_id="publisher", payload={
        "schema_version": 1, "name": "Public agent", "description": "published only",
        "tags": [], "components": {"identity": {"kind": "soul", "config": {}}},
    })
    ledger_path = shared / ".broker/outbound.db"

    def ledger_for(owner):
        return ConnectionLedger(ledger_path, data_root=shared,
                                verify_authenticated_principal=lambda: owner)

    ledger = ledger_for("owner")
    ledger.create_connection(
        connection_id="conn-model", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST", "GET"),
        provider="http", destination="compute:conn-model", credential_ref="vault://http/test",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                            "methods": ["POST", "GET"]}],
    )
    ledger.grant_connection(grant_id="grant-model", connection_id="conn-model",
                            owner_user_id="owner", universe_id="center")
    fields = dict(universe_id="center", owner_user_id="owner", access_method="api_key_http",
                  protocol="openai_chat", ref="grant-model", model="reply-model")
    definition = ProviderDefinition(id=_definition_id(**fields), **fields,
                                    visibility="private", created_at="2026-10-16T00:00:00Z")
    calls = []
    reply = {"model": "reply-model", "choices": [{"message": {
        "role": "assistant", "content": "hello through the broker"}, "finish_reason": "stop"}]}

    def network(**kwargs):
        calls.append(kwargs)
        assert kwargs["credential"] == SECRET
        if kwargs.get("on_connect"):
            kwargs["on_connect"](None)
        return UpstreamStream.complete({"status": 200, "reason": "OK", "headers": {},
                                        "body": json.dumps(reply).encode()}, ())

    def dispatch_for(owner, center, grant, resource):
        return CredentialBlindBroker(
            ledger_for(owner), resolve_credential=lambda *_: SECRET,
            network_request=network).dispatch

    broker.server._roles = {os.getuid(): BOX_HOST}
    broker.server._owner_identities = identities
    broker.server._data_root = shared
    broker.server._ledger_for = ledger_for
    broker.server._dispatch_for = dispatch_for
    broker.server._policy_store = PolicyStore(shared, identities)
    broker.server._policy_store.bootstrap()

    class Admission:
        broker_socket = broker.path

        def grant(self, owner, center, purpose):
            assert identities.admitted_owner(center) == owner
            return SimpleNamespace(uid=os.getuid())

        def authorize(self, grant, center):
            return identities.admitted_owner(center) == "owner"

    # Same-uid IPC harness: production's peer check is covered by broker split tests.
    monkeypatch.setattr(relays, "peer", lambda sock: (os.getpid(), 1002, 1002))
    relay = relays.OwnerRelays(tmp_path / "relay", Admission())
    path = relay.bind("owner", os.getuid())
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "0")
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "0")
    monkeypatch.setattr(owner_runtime, "OWNER_MOUNT", root)
    monkeypatch.setattr(owner_runtime, "_is_owner_mount", lambda candidate: candidate == root)
    runtime = owner_runtime.start(path)
    from tinyassets.broker import supervisor

    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: owner_runtime.broker_egress())
    (root / "center").mkdir()
    try:
        yield SimpleNamespace(root=root, shared=shared, ledger=ledger, calls=calls,
                              definition=definition, runtime=runtime, published=published,
                              broker=broker, reply=reply)
    finally:
        runtime.tools.close()
        relay.close()
        async def drain():
            tasks = [task for task in asyncio.all_tasks() if task is not asyncio.current_task()]
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

        asyncio.run_coroutine_threadsafe(drain(), broker.loop).result(5)


def test_model_sync_and_stream_use_broker_without_box_credentials(box):
    from tinyassets.request_budget import TurnRequestBudget

    provider = ApiKeyHttpProvider(box.definition)
    for streamed in (False, True):
        budget = TurnRequestBudget("owner", "center")
        budget.persist(box.root)
        attempt = budget.reserve(owner="owner", universe="center", source_ref=provider.name,
                                 model="reply-model", free=True)
        config = ModelConfig(invocation_owner_user_id="owner", request_budget=budget,
                             request_attempt=attempt)
        try:
            response = (asyncio.run(provider.complete("hello", "", config,
                                                     universe_dir=box.root / "center"))
                        if streamed else provider._complete_sync(
                            "hello", "", config, universe_dir=box.root / "center"))
            assert response.text == "hello through the broker"
        finally:
            budget.close()
    assert len(box.calls) == 2
    assert SECRET not in json.dumps(dict(os.environ))
    assert all(SECRET.encode() not in p.read_bytes() for p in box.root.rglob("*") if p.is_file())
    assert not (box.root / ".broker").exists()
    assert not (box.shared / "center").exists()


def test_authenticated_external_call_and_grant_revocation(box):
    from tinyassets.storage.outbound_connections import GrantResolutionError, _broker_channel

    channel = _broker_channel(box.root, principal="owner", command_center="center",
                              grant_id="grant-model", connection_id="conn-model")
    result = channel.request("GET", {"url": "https://models.example.com/v1/chat"})
    assert result["status"] == 200 and len(box.calls) == 1
    box.ledger.revoke_grant("grant-model")
    with pytest.raises(GrantResolutionError):
        channel.request("GET", {"url": "https://models.example.com/v1/chat"})
    assert len(box.calls) == 1


def test_plan_and_catalog_are_shared_reads_not_box_databases(box):
    from tinyassets.commons_catalog import get_definition, list_definitions
    from tinyassets.storage.subscription_state import get_plan, get_tier, read_tier

    assert get_tier(box.root / "center") == read_tier(box.root / "center") == "paid"
    assert get_plan(box.root / "center") == {"tier": "paid", "ends_at": None}
    assert list_definitions(box.root)[0]["name"] == "Public agent"
    assert get_definition(box.root, box.published["agent_definition_id"])["name"] == "Public agent"
    assert not (box.root / ".tinyassets.db").exists()
    assert not list(box.root.rglob(".subscription_state.db"))


def test_missing_broker_never_uses_local_provider(box, monkeypatch):
    monkeypatch.setattr(owner_runtime, "_runtime", replace(box.runtime, relay=Path("/absent.sock")))
    with pytest.raises((OSError, RuntimeError)):
        ApiKeyHttpProvider(box.definition)._complete_sync(
            "hello", "", ModelConfig(invocation_owner_user_id="owner"),
            universe_dir=box.root / "center")
    assert not box.calls


def test_center_creation_admits_through_bound_relay(box, monkeypatch):
    from tinyassets.agent_loop import served_chat
    from tinyassets.broker.owner_identities import admitted_owner

    def forbidden():
        raise AssertionError("an existing owner box must not bind a host provider")

    monkeypatch.setattr(served_chat, "configured_box_provider", forbidden)
    generation = admit_center(box.root, principal="untrusted-field", center="new-center")
    assert generation > 0
    assert admit_center(box.root, principal="owner", center="new-center") == generation
    assert admitted_owner(box.root, center="new-center") == "owner"
    assert box.broker.server._owner_identities.admitted_owner("new-center") == "owner"
    assert not (box.root / ".broker").exists()


def test_center_admission_rejects_foreign_retired_and_identity_fields(box):
    from tinyassets.owner_channel import request

    identities = box.broker.server._owner_identities
    identities.resolve("other", allocate=True)
    identities.admission("admit", "other", "foreign")
    identities.admission("admit", "owner", "retired")
    identities.admission("retire", "owner", "retired")
    for center in ("foreign", "retired", "../invalid"):
        with pytest.raises(PermissionError):
            admit_center(box.root, principal="owner", center=center)
    with pytest.raises(PermissionError):
        request("", {"op": "ADMIT_CENTER", "center": "spoofed", "principal": "other"})
    assert identities.center_state("spoofed") == "unadmitted"
    assert identities.admitted_owner("foreign") == "other"


def test_retired_owner_cannot_admit_on_an_already_open_channel(box):
    from tinyassets import rpc_frames as rf
    from tinyassets.owner_channel import connect

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        connect(channel, "")
        identities = box.broker.server._owner_identities
        identities.admission("retire", "owner", "center")
        channel.sendall(rf.control(rf.CONNECTION, {"op": "ADMIT_CENTER", "center": "late"}))
        assert rf.read_frame_blocking(channel).control() == {"op": "BOX_REFUSED"}
    assert identities.center_state("late") == "unadmitted"


def test_policy_source_display_and_capabilities_use_bound_channel(box):
    from tinyassets import policy_rules
    from tinyassets.providers.definition import register_definition
    from tinyassets.providers.source_display import source_display_name
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    home = box.root / "center"
    fields = box.definition.as_dict()
    fields.pop("id")
    fields["now"] = fields.pop("created_at")
    registered = register_definition(**fields)
    assert source_display_name(
        base=box.root, universe_id="center", provider="api_key_http:" + registered.id,
    ) == "compute:conn-model"
    policy = policy_rules.projection(home)
    assert policy["policy"]
    capabilities = Capabilities(home, ExecutionContext("center", "owner", "main"), [],
                                call_platform=None, check_authority=lambda: None)
    assert capabilities._bound_policy_owner()
    assert set(capabilities.connections()) == {
        "connection:conn-model:GET", "connection:conn-model:POST"}
    assert not (box.root / ".broker").exists()
    assert not (box.root / "outbound.db").exists()


@pytest.mark.parametrize("operation", ["connect", "connect_ex", "udp", "http", "sdk"])
def test_outbound_seam_refuses_all_direct_network(box, operation):
    with pytest.raises((PermissionError, httpx.ConnectError, URLError)):
        if operation == "http":
            httpx.get("https://203.0.113.1", trust_env=False)
        elif operation == "sdk":
            # urllib is also the transport used by provider adapters.
            import urllib.request

            urllib.request.urlopen("https://203.0.113.1", timeout=1)
        else:
            with socket.socket(socket.AF_INET, socket.SOCK_DGRAM if operation == "udp"
                               else socket.SOCK_STREAM) as sock:
                if operation == "udp":
                    sock.sendto(b"denied", ("203.0.113.1", 443))
                else:
                    getattr(sock, operation)(("203.0.113.1", 443))
    assert not box.calls


def test_vault_and_provider_cli_refuse(box):
    from tinyassets.credential_vault import credential_vault_path
    with pytest.raises(PermissionError, match="raw credential custody"):
        credential_vault_path(box.root / "center")


def test_model_in_fresh_box_process_has_no_secret(box):
    code = """
import asyncio, json, os, sys
from pathlib import Path
from tinyassets import owner_runtime
from tinyassets.providers.definition import ProviderDefinition
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig
from tinyassets.request_budget import TurnRequestBudget
root, relay, definition = Path(sys.argv[1]), Path(sys.argv[2]), json.loads(sys.argv[3])
root.mkdir()
owner_runtime.OWNER_MOUNT = root
owner_runtime._is_owner_mount = lambda candidate: candidate == root
runtime = owner_runtime.start(relay)
(root / 'center').mkdir()
from tinyassets.boxes.lifecycle import admit_center
from tinyassets.broker.owner_identities import admitted_owner
admit_center(root, principal='ignored-request-identity', center='process-center')
assert admitted_owner(root, center='process-center') == 'owner'
provider = ApiKeyHttpProvider(ProviderDefinition(**definition))
budget = TurnRequestBudget('owner', 'center')
budget.persist(root)
ordinal = budget.reserve(owner='owner', universe='center', source_ref=provider.name,
                         model='reply-model', free=True)
try:
    result = asyncio.run(provider.complete('hello', '', ModelConfig(
        invocation_owner_user_id='owner', request_budget=budget, request_attempt=ordinal),
        universe_dir=root / 'center'))
    print(json.dumps({'text': result.text, 'environment': dict(os.environ)}))
finally:
    budget.close()
    runtime.tools.close()
"""
    root = box.root / "process"
    completed = subprocess.run(
        [sys.executable, "-c", code, str(root), str(box.runtime.relay),
         json.dumps(box.definition.as_dict())], capture_output=True, text=True, timeout=45)
    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert box.broker.server._owner_identities.admitted_owner("process-center") == "owner"
    assert result["text"] == "hello through the broker"
    assert SECRET not in completed.stdout
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    forbidden = CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys()
    assert not forbidden & result["environment"].keys()
    assert not any(k.endswith(("_API_KEY", "_ACCESS_TOKEN", "_REFRESH_TOKEN", "_SECRET_KEY"))
                   and value for k, value in result["environment"].items())
    assert not (root / ".broker").exists()
    assert all(SECRET.encode() not in p.read_bytes() for p in root.rglob("*") if p.is_file())


def test_authenticated_effector_uses_channel_policy_and_grant(box, monkeypatch):
    from tinyassets.effectors.authenticated_external_call import (
        run_authenticated_external_call_effector,
    )
    from tinyassets.storage.effector_consents import grant_consent
    from tinyassets.ta_capabilities import ExecutionContext

    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    home = box.root / "center"
    grant_consent(home, sink="authenticated_external_call", destination="compute:conn-model",
                  granted_by="owner")
    packet = {"sink": "authenticated_external_call", "connection_id": "conn-model",
              "grant_id": "grant-model", "verb": "GET", "request": {"path": "/v1/chat"}}
    result = run_authenticated_external_call_effector(
        node_id="call", output_keys=["call"], run_state={"call": packet}, base_path=home,
        execution_context=ExecutionContext("center", "owner", "main"))
    assert not result.get("error_kind"), result
    assert len(box.calls) == 1, result


def test_usage_lease_disconnect_closes_allocation(box):
    from tinyassets.request_budget import RequestBudgetExceeded, TurnRequestBudget
    from tinyassets.storage.agent_request_usage import UsageStore

    budget = TurnRequestBudget("owner", "center")
    budget.persist(box.root)
    # Close the actual transport, without sending a SQLite 'close' mutation.
    budget._lease.close()
    deadline = time.monotonic() + 5
    store = UsageStore(box.root)
    while not store.receipt(("owner", "center", budget.usage_id))["closed"]:
        assert time.monotonic() < deadline
        time.sleep(0.01)
    with pytest.raises(RequestBudgetExceeded):
        budget.reserve(owner="owner", universe="center", source_ref="source",
                       model="model", free=True)


@pytest.mark.parametrize("name", ["OPENAI_API_KEY", "CLAUDE_CODE_OAUTH_TOKEN",
                                 "STRIPE_WEBHOOK_SECRET", "TINYASSETS_BILLING_ENTITLEMENT_KEY",
                                 "TINYASSETS_FCM_SERVICE_ACCOUNT_JSON"])
def test_owner_startup_refuses_credential_environment(tmp_path, monkeypatch, name):
    from tinyassets.platform_secrets import CHILD_FORBIDDEN_ENV, ENGINE_REQUIRED_SECRET_ENV

    for secret_name in CHILD_FORBIDDEN_ENV | ENGINE_REQUIRED_SECRET_ENV.keys():
        monkeypatch.delenv(secret_name, raising=False)
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    monkeypatch.setattr(owner_runtime, "OWNER_MOUNT", tmp_path)
    monkeypatch.setattr(owner_runtime, "_is_owner_mount", lambda candidate: True)
    monkeypatch.setenv(name, "synthetic-key")
    with pytest.raises(PermissionError, match="environment contains a raw credential"):
        owner_runtime.start(tmp_path / "relay.sock")
    assert owner_runtime.current() is None


@pytest.mark.parametrize("registered", [False, True])
def test_box_call_refreshes_only_inside_broker(box, monkeypatch, registered):
    from tinyassets.broker.process import _Dispatchers
    from tinyassets.broker.tokens import ConnectionTokens
    from tinyassets.connection_oauth import directory, tokens
    from tinyassets.platform_secrets import child_env
    from tinyassets.storage import outbound_connections as outbound

    # The broker is served in this process; the owner below runs in its own process.
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    bundle = tokens.TokenBundle(access_token="expired-access", refresh_token="single-use",
        expires_at=time.time() - 10, token_url="https://auth.example.com/token",
        client_id="registered-client", provider_id="fixture" if registered else "")
    keeper = ConnectionTokens(universe_dir=box.shared / "center", owner_user_id="owner")
    keeper._write("test", bundle)
    with box.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET auth_scheme='oauth2'")
    if registered:
        monkeypatch.setattr(directory, "entries", lambda: [{
            "id": "fixture", "token_endpoint": bundle.token_url,
            "client_id_env": "TINYASSETS_OAUTH_FIXTURE_CLIENT_ID",
            "client_secret_env": "TINYASSETS_OAUTH_FIXTURE_SECRET",
            "token_endpoint_auth_method": "client_secret_post"}])
        monkeypatch.setenv("TINYASSETS_OAUTH_FIXTURE_CLIENT_ID", bundle.client_id)
        monkeypatch.setenv("TINYASSETS_OAUTH_FIXTURE_SECRET", "platform-fixture-secret")
    refreshed = []

    def token_request(method, url, *, form, **kwargs):
        assert (method, url) == ("POST", bundle.token_url)
        assert form["grant_type"] == "refresh_token" and form["refresh_token"] == "single-use"
        assert (form.get("client_secret") == "platform-fixture-secret") is registered
        refreshed.append(os.getpid())
        return 200, {"access_token": "fresh-access", "refresh_token": "rotated-refresh",
                     "expires_in": 3600, "token_type": "Bearer"}

    def network(**kwargs):
        assert kwargs["credential"] == "fresh-access"
        return UpstreamStream.complete({"status": 200, "reason": "OK", "headers": {},
                                        "body": b"refreshed"}, ())

    monkeypatch.setattr(tokens, "request_json", token_request)
    monkeypatch.setattr(outbound, "_TrustedNetworkDriver", lambda *_: network)
    dispatchers = _Dispatchers(box.shared, allow_test_fixtures=True)
    box.broker.server._dispatch_for = dispatchers.dispatch_for
    code = """
import os, sys
from pathlib import Path
from tinyassets import owner_runtime
from tinyassets.storage.outbound_connections import _broker_channel
root, relay = Path(sys.argv[1]), Path(sys.argv[2])
root.mkdir()
owner_runtime.OWNER_MOUNT = root
owner_runtime._is_owner_mount = lambda candidate: candidate == root
runtime = owner_runtime.start(relay)
assert not any(n.startswith('TINYASSETS_OAUTH_') and n.endswith('_SECRET') for n in os.environ)
try:
    channel = _broker_channel(root, principal='owner', command_center='center',
        grant_id='grant-model', connection_id='conn-model')
    for _ in range(2):
        result = channel.request('GET', {'url': 'https://models.example.com/v1/chat'})
        assert result['status'] == 200 and result['body'] == 'refreshed'
    print(os.getpid())
finally:
    runtime.tools.close()
"""
    root = box.root / "refresh-process"
    completed = subprocess.run([sys.executable, "-c", code, str(root), str(box.runtime.relay)],
                               env=child_env(os.environ), capture_output=True,
                               text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
    assert refreshed == [os.getpid()] and int(completed.stdout) != os.getpid()
    assert tokens.decode(keeper._read("test")).refresh_token == "rotated-refresh"
    assert not (root / ".broker").exists()
    assert all(b"fresh-access" not in p.read_bytes() for p in root.rglob("*") if p.is_file())


@pytest.mark.parametrize("operation", ["REFRESH", "REFRESH_ACK"])
def test_box_channel_refuses_removed_refresh_operations(box, operation):
    from tinyassets import rpc_frames as rf
    from tinyassets.owner_channel import connect

    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.settimeout(5)
        connect(channel, "center")
        channel.sendall(rf.control(rf.CONNECTION, {"op": operation}))
        assert rf.read_frame_blocking(channel) is None
