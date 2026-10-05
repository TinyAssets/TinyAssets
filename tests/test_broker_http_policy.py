"""HTTP extension uses live broker authority and one complete policy CAS."""
# ruff: noqa: F811 -- imported fixtures
import socket
import sys

import pytest

from tests.test_broker_disconnect import removal  # noqa: F401
from tests.test_broker_discovery_http import discovery  # noqa: F401
from tests.test_broker_server import broker  # noqa: F401
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.http_policy import read_policy, update_policy
from tinyassets.storage.outbound_connections import GrantResolutionError

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"), reason="Unix peer credentials")

ENDPOINT = {"host": "models.example.com", "path_template": "/extra", "methods": ["GET"]}


def read(rig):
    return read_policy(rig.root, principal="alice", command_center="cc-alice",
                       destination="fixture")


def update(rig, **changes):
    resource, _, expected = read(rig)
    return update_policy(rig.root, **(dict(principal="alice", command_center="cc-alice",
        destination="fixture", expected=expected, action="extend", git_host=resource.git_host,
        endpoints=[*[e.as_dict() for e in resource.allowed_endpoints], ENDPOINT],
        scopes=resource.scopes) | changes))


def test_actual_extend_and_full_mode_without_daemon_ledger(removal):
    from tinyassets.api.http_connection import extend_http, preview_extend_http

    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        preview = preview_extend_http(universe_id="cc-alice", payload={
            "destination": "fixture", "endpoints": [ENDPOINT]})
        assert preview.get("status") == "extends", preview
        result = extend_http(universe_id="cc-alice", payload={
            "destination": "fixture", "endpoints": [ENDPOINT]})
        assert result["status"] == "extended", result
        assert len(result["allowed_endpoints"]) == 2
        result = extend_http(universe_id="cc-alice", payload={
            "destination": "fixture", "access": "full"})
        assert result["status"] == "extended" and result["access"] == "full", result
    assert removal.ledger.access_mode(removal.connection) == "full"
    assert not (removal.root / "outbound.db").exists()


@pytest.mark.parametrize("scope", [{"principal": "bob"}, {"command_center": "cc-bob"}])
def test_policy_foreign_scope_refuses_without_mutation(removal, scope):
    before = removal.ledger._get_connection_resource(removal.connection)
    with pytest.raises(GrantResolutionError):
        update(removal, **scope)
    assert removal.ledger._get_connection_resource(removal.connection) == before


@pytest.mark.parametrize("changed", ["incarnation", "scopes_json", "access_mode", "endpoints_json"])
def test_policy_compares_every_observed_field(removal, changed):
    _, _, expected = read(removal)
    expected[changed] = "stale"
    before = removal.ledger._get_connection_resource(removal.connection)
    assert update(removal, expected=expected) is False
    assert removal.ledger._get_connection_resource(removal.connection) == before


def test_revocation_between_preview_and_write_refuses(removal, monkeypatch):
    from tinyassets.api.http_connection import extend_http
    from tinyassets.broker.client import BrokerClient
    original = BrokerClient.http_policy

    def revoked(client, document):
        removal.ledger.revoke_grant(removal.grant)
        return original(client, document)

    monkeypatch.setattr(BrokerClient, "http_policy", revoked)
    before = removal.ledger._get_connection_resource(removal.connection)
    with identity_context(Identity(user_id="alice", username="alice", capabilities=["write"])):
        with pytest.raises(GrantResolutionError):
            extend_http(universe_id="cc-alice", payload={
                "destination": "fixture", "endpoints": [ENDPOINT]})
    assert removal.ledger._get_connection_resource(removal.connection) == before


def test_broker_refuses_policy_narrowing(removal):
    from tinyassets.broker.client import BrokerRefused
    before = removal.ledger._get_connection_resource(removal.connection)
    with pytest.raises(BrokerRefused):
        update(removal, endpoints=[ENDPOINT])
    assert removal.ledger._get_connection_resource(removal.connection) == before


def test_lost_mutation_ack_is_not_replayed(removal, monkeypatch):
    from tinyassets.broker.client import BrokerClient
    from tinyassets.storage.outbound_connections import ProxyRequestError
    original = BrokerClient.http_policy
    calls = []

    def lost(client, document):
        calls.append(document)
        original(client, document)
        raise ProxyRequestError("lost acknowledgement")

    monkeypatch.setattr(BrokerClient, "http_policy", lost)
    with pytest.raises(ProxyRequestError):
        update(removal)
    assert len(calls) == 1
    assert len(removal.ledger._get_connection_resource(removal.connection).allowed_endpoints) == 2
    assert not (removal.root / "outbound.db").exists()
