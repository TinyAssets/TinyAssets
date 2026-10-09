"""Actual capability consumers over broker IPC with no daemon ledger file."""
# ruff: noqa: F811 -- imported pytest fixtures
import socket
import sys

import pytest

from tests.test_broker_capabilities import document
from tests.support.broker_ipc import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.broker.capabilities import capability_operation
from tinyassets.broker.client import BrokerRefused
from tinyassets.storage.outbound_connections import GrantResolutionError, ProxyRequestError

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")


def operation(fixture, **changes):
    return capability_operation(fixture.root, **(
        dict(principal="alice", command_center="cc-alice") | document() | changes))


def test_capability_real_ipc_configures_reads_and_disables(discovery):
    assert operation(discovery).descriptor() == document()["descriptor"]
    assert operation(discovery, action="read", enabled=False, descriptor=None) is not None
    assert operation(discovery, enabled=False, descriptor=None) is None
    assert operation(discovery, action="read", enabled=False, descriptor=None) is None
    assert not (discovery.root / "outbound.db").exists()


@pytest.mark.parametrize("change", [
    {"principal": "bob"}, {"command_center": "cc-bob"},
    {"grant_id": "missing"}, {"connection_id": "foreign"},
])
def test_capability_real_ipc_refuses_foreign_scope(discovery, change):
    with pytest.raises(GrantResolutionError):
        operation(discovery, **change)
    assert discovery.ledger.get_connection_capability("conn-a", "constant_headers") is None
    assert not (discovery.root / "outbound.db").exists()


def test_capability_real_ipc_stale_or_unavailable_never_falls_back(discovery, monkeypatch):
    from tinyassets.broker import supervisor

    discovery.broker.state["token"] = "stale"
    with pytest.raises(BrokerRefused):
        operation(discovery)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    with pytest.raises(ProxyRequestError):
        operation(discovery)
    assert not (discovery.root / "outbound.db").exists()


def test_connection_uses_actual_consumer_over_ipc(discovery, monkeypatch):
    from tinyassets.api.connection_uses import apply_connection_uses

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(discovery.root))
    result = apply_connection_uses(
        base=discovery.root, uid="cc-alice", actor="alice", grant_id="grant-a",
        uses={}, constant_headers={"X-Fixture": "yes"})
    assert result["constant_headers"] == {"X-Fixture": "yes"}
    assert discovery.ledger.get_connection_capability(
        "conn-a", "constant_headers").descriptor() == document()["descriptor"]
    assert not (discovery.root / "outbound.db").exists()


def test_provider_capability_and_voice_consumers_over_ipc(discovery, monkeypatch):
    import json

    from tinyassets import daemon_server, provider_serving_binding
    from tinyassets.api import permissions
    from tinyassets.api.connection_uses import configure_connection
    from tinyassets.api.provider_capability import configure_provider_capability
    from tinyassets.onboarding.realtime_voice import _default_proxy_factory, _resolve_voice_binding
    from tinyassets.provider_serving_binding import CurrentServingProviderAuthority

    root = discovery.root
    (root / "cc-alice").mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "alice")
    monkeypatch.setattr(daemon_server, "get_founder_home", lambda *a: "cc-alice")
    monkeypatch.setattr(daemon_server, "list_universe_acl", lambda *a, **k: [
        {"actor_id": "alice", "permission": "admin"}])
    monkeypatch.setattr(provider_serving_binding, "resolve_current_serving_provider_authority",
                        lambda *a, **k: CurrentServingProviderAuthority(
                            provider="api_key_http:fixture", access_method="api_key_http",
                            connection_id="conn-a", grant_id="grant-a"))
    with discovery.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET scopes_json=?, allowed_endpoints_json=?",
                     (json.dumps(["POST"]), json.dumps([{
                         "host": "models.example.com", "path_template": "/session",
                         "methods": ["POST"]}])))
    descriptor = dict(protocol="tinyassets.voice.v1",
                      session_url="https://models.example.com/session", service_name="Fixture",
                      privacy_url="https://models.example.com/privacy")
    result = configure_provider_capability(universe_id="cc-alice", payload={
        "capability_kind": "realtime_voice", "enabled": True, "descriptor": descriptor})
    assert result["status"] == "configured"
    assert result["descriptor"] == descriptor
    binding = _resolve_voice_binding(root / "cc-alice", "alice")
    assert binding.session_url == descriptor["session_url"]
    proxy = _default_proxy_factory(root / "cc-alice", "alice", binding)
    assert proxy.grant_id == "grant-a"
    proxy.close()
    result = configure_connection(universe_id="cc-alice", payload={
        "connection_id": "conn-a", "constant_headers": {"X-Fixture": "configured"}})
    assert result["status"] == "configured"
    assert result["constant_headers"] == {"X-Fixture": "configured"}
    assert not (root / "outbound.db").exists()


def test_capability_ipc_preserves_endpoint_and_pricing_errors(discovery):
    from tinyassets.storage.outbound_connections import (
        MODEL_USE_PRICED_CONFLICT,
        SsrfValidationError,
    )

    with pytest.raises(SsrfValidationError):
        operation(discovery, capability_kind="model_discovery", descriptor={
            "protocol": "openrouter_user_models_v1",
            "catalogue_url": "https://foreign.example/api/v1/models/user?output_modalities=all"})
    with discovery.ledger._connect() as conn:
        conn.execute("INSERT INTO connection_capabilities VALUES (?, ?, ?, 0)",
                     ("conn-a", "model_discovery", "{}"))
        conn.execute("UPDATE outbound_connections SET scopes_json='[\"GET\",\"POST\"]'")
    with pytest.raises(ValueError) as caught:
        operation(discovery, capability_kind="model_use", descriptor={
            "wire": "chat_messages", "models": [{"id": "fixture", "tools": True,
                                                   "context": 20000}], "billing": "free"})
    assert str(caught.value) == MODEL_USE_PRICED_CONFLICT


def test_connection_uses_revoked_between_precheck_and_write_is_not_found(discovery, monkeypatch):
    from tinyassets.api.connection_uses import apply_connection_uses
    from tinyassets.broker.client import BrokerClient

    original = BrokerClient.capability

    def revoke(client, document):
        discovery.ledger.revoke_grant("grant-a")
        return original(client, document)

    monkeypatch.setattr(BrokerClient, "capability", revoke)
    assert apply_connection_uses(
        base=discovery.root, uid="cc-alice", actor="alice", grant_id="grant-a",
        uses={}, constant_headers={"X-Fixture": "yes"}) == {
            "error": "not_found", "resource": "connection"}
    assert discovery.ledger.get_connection_capability("conn-a", "constant_headers") is None
