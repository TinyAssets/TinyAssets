"""D26 HTTP compute routes use admitted owner context and no daemon ledger."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_broker_serving_consumers import case  # noqa: F401
from tinyassets.broker import ledger_queries, supervisor
from tinyassets.exceptions import ProviderUnavailableError
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.providers.base import ModelConfig
from tinyassets.storage.outbound_connections import ProxyRequestError


def config(**changes):
    return SimpleNamespace(temperature=0.2, max_tokens=20,
                           invocation_owner_user_id="alice", **changes)


@pytest.fixture
def compute(case, monkeypatch):  # noqa: F811
    calls, closed = [], []

    class Channel:
        def request(self, verb, request, **kwargs):
            calls.append((verb, request, kwargs))
            return {"status": 200, "body": json.dumps({
                "choices": [{"message": {"content": "broker answer"}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2}})}

        def close(self):
            closed.append(True)

    scopes = []

    def channel(root, **scope):
        assert root == case.root
        scopes.append(scope)
        return Channel()

    monkeypatch.setattr("tinyassets.storage.outbound_connections._broker_channel", channel)
    case.provider = ApiKeyHttpProvider(case.definitions["alice"])
    case.sent, case.closed, case.scopes = calls, closed, scopes
    return case


def test_compute_reads_and_acquires_without_local_ledger(compute):
    result = asyncio.run(compute.provider.complete(
        "hello", "system", config(), universe_dir=compute.root / "cc-alice"))
    assert (result.text, result.input_tokens, result.output_tokens) == ("broker answer", 3, 2)
    assert [call[2]["query"] for call in compute.calls] == [
        ledger_queries.GRANTED_RESOURCE, ledger_queries.AUTHORIZED_CONNECTION]
    assert compute.scopes == [dict(principal="alice", command_center="cc-alice",
                                   grant_id="grant-alice", connection_id="conn-alice")]
    assert compute.sent[0][0] == "POST"
    assert compute.sent[0][1]["url"] == "https://models.example.com/chat"
    assert compute.closed == [True]
    assert not (compute.root / "outbound.db").exists()


@pytest.mark.parametrize("definition,principal,center", [
    ("alice", "", "cc-alice"), ("alice", "bob", "cc-alice"),
    ("bob", "alice", "cc-alice"), ("foreign", "alice", "cc-alice"),
    ("alice", "alice", "cc-bob"),
])
def test_compute_refuses_missing_or_foreign_authority(compute, definition, principal, center):
    provider = ApiKeyHttpProvider(compute.definitions[definition])
    cfg = config()
    cfg.invocation_owner_user_id = principal
    with pytest.raises(ProviderUnavailableError):
        provider._complete_sync("hello", "", cfg, universe_dir=compute.root / center)
    assert not compute.sent and not compute.scopes


@pytest.mark.parametrize("kind", ["grant", "connection"])
def test_compute_revoked_authority_never_dispatches(compute, kind):
    if kind == "grant":
        compute.ledger.revoke_grant("grant-alice")
    else:
        compute.ledger.revoke_connection("conn-alice")
    with pytest.raises(ProviderUnavailableError):
        compute.provider._complete_sync("hello", "", config(),
                                        universe_dir=compute.root / "cc-alice")
    assert not compute.sent


def test_compute_rechecks_revocation_after_initial_read(compute, monkeypatch):
    original = ledger_queries.granted_resource_row

    def revoke(*args, **kwargs):
        row = original(*args, **kwargs)
        compute.ledger.revoke_grant("grant-alice")
        return row

    monkeypatch.setattr(ledger_queries, "granted_resource_row", revoke)
    with pytest.raises(ProviderUnavailableError, match="grant resolution failed"):
        compute.provider._complete_sync("hello", "", config(),
                                        universe_dir=compute.root / "cc-alice")
    assert not compute.scopes and not compute.sent


def test_compute_outage_and_malformed_projection_never_open_local(compute, monkeypatch):
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: None)
    with pytest.raises(ProviderUnavailableError, match="broker admission unavailable"):
        compute.provider._connection_context(compute.root / "cc-alice", config())
    monkeypatch.setattr(ledger_queries, "query_ledger", lambda *a, **k: {"resource": {}})
    with pytest.raises(ProviderUnavailableError, match="broker admission unavailable"):
        compute.provider._connection_context(compute.root / "cc-alice", config())
    assert not compute.sent


@pytest.mark.parametrize("stage", ["query", "channel"])
def test_compute_acquisition_outage_is_known_not_sent(compute, monkeypatch, stage):
    def offline(*args, **kwargs):
        raise ProxyRequestError("offline")

    if stage == "query":
        monkeypatch.setattr(ledger_queries, "authorized_connection", offline)
    else:
        monkeypatch.setattr("tinyassets.storage.outbound_connections._broker_channel", offline)
    with pytest.raises(ProviderUnavailableError, match="broker admission unavailable"):
        compute.provider._complete_sync("hello", "", config(),
                                        universe_dir=compute.root / "cc-alice")
    assert not compute.sent


@pytest.mark.parametrize("mode", ["exact", "full"])
def test_compute_proxy_preserves_access_mode(compute, mode):
    with compute.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET access_mode=?, scopes_json='[\"GET\"]' "
                     "WHERE connection_id='conn-alice'", (mode,))
    if mode == "exact":
        with pytest.raises(PermissionError, match="outside the granted connection scope"):
            compute.provider._complete_sync("hello", "", config(),
                                            universe_dir=compute.root / "cc-alice")
        assert not compute.sent
    else:
        assert compute.provider._complete_sync("hello", "", config(),
                    universe_dir=compute.root / "cc-alice").text == "broker answer"
    assert compute.closed == [True]


def test_compute_keeps_usage_reference_and_closes_on_transport_failure(compute, monkeypatch):
    reference = object()
    checks, issued, closed = [], [], []
    budget = SimpleNamespace(
        check_scope=lambda *args: checks.append(args),
        issue_reference=lambda *args, **kwargs: issued.append((args, kwargs)) or reference)

    class Failed:
        def request(self, verb, wire, *, inference_usage):
            assert inference_usage is reference
            raise ProxyRequestError("offline")

        def close(self):
            closed.append(True)

    monkeypatch.setattr("tinyassets.storage.outbound_connections._broker_channel",
                        lambda *a, **k: Failed())
    with pytest.raises(ProxyRequestError, match="offline"):
        compute.provider._complete_sync("hello", "", config(request_budget=budget,
                                       request_attempt=7), universe_dir=compute.root / "cc-alice")
    assert checks == [("alice", "cc-alice")]
    assert issued[0][0] == (7,)
    assert issued[0][1]["connection_id"] == "conn-alice"
    assert closed == [True]


def test_router_replaces_caller_principal_with_admitted_serving_owner(tmp_path):
    from tests.test_provider_served_router import _RecordingProvider, _served_context
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.providers.router import ProviderRouter

    _, _, capability, context = _served_context(tmp_path)
    seen = []

    class Capture(_RecordingProvider):
        async def complete(self, prompt, system, config, **kwargs):
            seen.append(config.invocation_owner_user_id)
            return await super().complete(prompt, system, config, **kwargs)

    router = ProviderRouter(providers={"codex": Capture("codex")})
    try:
        asyncio.run(router.call("writer", "hello", "system",
                               replace(ModelConfig(), invocation_owner_user_id="forged"),
                               universe_context=context, operation="converse"))
        assert seen == ["owner-1"]
    finally:
        revoke_provider_request(capability)


def test_router_releases_served_reservation_for_broker_admission_outage(tmp_path, monkeypatch):
    from tests.test_provider_served_router import _RecordingProvider, _served_context
    from tinyassets import provider_assignment
    from tinyassets.auth.middleware import revoke_provider_request
    from tinyassets.exceptions import AllProvidersExhaustedError
    from tinyassets.providers.definition import ProviderDefinition
    from tinyassets.providers.router import ProviderRouter

    universe, _, capability, context = _served_context(tmp_path)
    definition = ProviderDefinition(
        id="fixture", universe_id=universe.name, owner_user_id="owner-1",
        access_method="api_key_http", protocol="chat_messages", model="fixture",
        ref="grant-a", visibility="private", created_at="2026-10-04T00:00:00Z")
    compute = ApiKeyHttpProvider(definition)
    monkeypatch.setenv(supervisor.ENV_SWITCH, supervisor.PROCESS)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: None)
    released, abandoned = [], []
    original = provider_assignment.release_served_provider_budget

    def release(*args, **kwargs):
        released.append(True)
        return original(*args, **kwargs)

    monkeypatch.setattr(provider_assignment, "release_served_provider_budget", release)
    monkeypatch.setattr(provider_assignment, "abandon_served_provider_budget",
                        lambda *a, **k: abandoned.append(True))

    class Outage(_RecordingProvider):
        async def complete(self, prompt, system, config, **kwargs):
            # Real provider authority lookup under the actual router's admitted
            # principal, before any HTTP stream can exist.
            compute._connection_context(universe, config)
            raise AssertionError("unavailable broker admitted a source")

    router = ProviderRouter(providers={"codex": Outage("codex")})
    try:
        with pytest.raises(AllProvidersExhaustedError):
            asyncio.run(router.call("writer", "hello", "system",
                                   universe_context=context, operation="converse"))
        assert released == [True] and abandoned == []
    finally:
        revoke_provider_request(capability)
