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
    from tinyassets.exceptions import ProviderConfinementError
    from tinyassets.providers.owned_process import aspawn_owned

    with pytest.raises(PermissionError, match="raw credential custody"):
        credential_vault_path(box.root / "center")
    with pytest.raises(ProviderConfinementError, match="broker_endpoint"):
        asyncio.run(aspawn_owned(["codex"]))


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
owner_runtime.OWNER_MOUNT = root
owner_runtime._is_owner_mount = lambda candidate: candidate == root
runtime = owner_runtime.start(relay)
(root / 'center').mkdir()
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
