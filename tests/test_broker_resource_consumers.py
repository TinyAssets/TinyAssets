"""D23 consumers select authenticated named queries before local construction."""
from types import SimpleNamespace

import pytest

from tinyassets.api.compute_connection import _validate_http_grant
from tinyassets.api.model_access_requests import _connection_incarnations
from tinyassets.broker import ledger_queries, supervisor
from tinyassets.providers.definition import register_definition
from tinyassets.providers.source_display import source_display_name
from tinyassets.storage.outbound_connections import ConnectionLedger, ProxyRequestError


@pytest.fixture
def consumers(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    for owner in ("alice", "bob"):
        ledger.create_connection(
            connection_id=f"conn-{owner}", owner_user_id=owner, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("GET",), provider="http",
            destination=f"compute:{owner}", credential_ref="vault://http/fixture",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/models",
                                "methods": ["GET"]}],
        )
        ledger.grant_connection(grant_id=f"grant-{owner}", connection_id=f"conn-{owner}",
                                owner_user_id=owner, universe_id="cc-alice")
    definition = register_definition(
        universe_id="cc-alice", owner_user_id="alice", access_method="api_key_http",
        protocol="chat_messages", model="fixture", ref="grant-alice",
    )
    provider = f"api_key_http:{definition.id}"
    foreign_definition = register_definition(
        universe_id="cc-alice", owner_user_id="alice", access_method="api_key_http",
        protocol="chat_messages", model="foreign-fixture", ref="grant-bob",
    )
    calls = []

    class Client:
        def __init__(self, path, *, principal, command_center, **kwargs):
            self.principal, self.center = principal, command_center

        def ledger_query(self, **kwargs):
            calls.append((self.principal, self.center, kwargs))
            return ledger_queries.local_query(ledger, principal=self.principal,
                                              command_center=self.center, **kwargs)

    monkeypatch.setenv(supervisor.ENV_SWITCH, supervisor.PROCESS)
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: SimpleNamespace(
        socket_path=tmp_path / "broker.sock", fence=lambda: (1, "synthetic"), verify_broker=None))
    monkeypatch.setattr("tinyassets.broker.client.BrokerClient", Client)

    def no_local(*args, **kwargs):
        raise AssertionError("daemon attempted a local ConnectionLedger")

    monkeypatch.setattr("tinyassets.storage.outbound_connections.ConnectionLedger", no_local)
    return SimpleNamespace(root=tmp_path, ledger=ledger, provider=provider, calls=calls,
                           foreign_provider=f"api_key_http:{foreign_definition.id}")


def test_all_resource_consumers_use_scoped_query_without_local_ledger(consumers):
    c = consumers
    assert _validate_http_grant(base=c.root, universe_id="cc-alice", actor="alice",
                                grant_id="grant-alice") is None
    assert _connection_incarnations(c.root, "alice", "cc-alice", [c.provider]) == {
        c.provider: c.ledger.incarnation("conn-alice")}
    assert source_display_name(base=c.root, universe_id="cc-alice", provider=c.provider) == (
        "compute:alice")
    assert len(c.calls) == 3
    assert all(owner == "alice" and center == "cc-alice"
               and args == {"query": ledger_queries.GRANTED_RESOURCE,
                            "grant_id": "grant-alice", "connection_id": ""}
               for owner, center, args in c.calls)
    assert not (c.root / "outbound.db").exists()


@pytest.mark.parametrize("change", [{"actor": "bob"}, {"universe_id": "cc-bob"},
                                  {"grant_id": "grant-bob"}, {"grant_id": "absent"}])
def test_compute_grant_query_preserves_uniform_foreign_refusal(consumers, change):
    args = dict(base=consumers.root, universe_id="cc-alice", actor="alice", grant_id="grant-alice")
    assert _validate_http_grant(**(args | change)) == {
        "error": "not_found", "resource": "connection"}


@pytest.mark.parametrize("kind", ["grant", "connection"])
def test_revoked_resources_are_never_captured_or_displayed(consumers, kind):
    c = consumers
    if kind == "grant":
        c.ledger.revoke_grant("grant-alice")
    else:
        c.ledger.revoke_connection("conn-alice")
    with pytest.raises(PermissionError, match="model connection changed"):
        _connection_incarnations(c.root, "alice", "cc-alice", [c.provider])
    assert source_display_name(base=c.root, universe_id="cc-alice", provider=c.provider) == ""


def test_unavailable_broker_never_opens_local_ledger(consumers, monkeypatch):
    c = consumers
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: None)
    with pytest.raises(ProxyRequestError, match="selected but not running"):
        _validate_http_grant(base=c.root, universe_id="cc-alice", actor="alice",
                             grant_id="grant-alice")
    with pytest.raises(ProxyRequestError, match="selected but not running"):
        _connection_incarnations(c.root, "alice", "cc-alice", [c.provider])
    assert source_display_name(base=c.root, universe_id="cc-alice", provider=c.provider) == ""


def test_owned_definition_cannot_capture_or_label_foreign_grant(consumers):
    c = consumers
    provider = c.foreign_provider
    with pytest.raises(PermissionError, match="model connection changed"):
        _connection_incarnations(c.root, "alice", "cc-alice", [provider])
    assert source_display_name(base=c.root, universe_id="cc-alice", provider=provider) == ""
    assert len(c.calls) == 2
    assert all(args["grant_id"] == "grant-bob" for _, _, args in c.calls)


@pytest.mark.parametrize("projection", [None, {}, {"resource": None}, {"resource": []}])
def test_malformed_resource_projection_is_a_fixed_transport_error(monkeypatch, projection):
    monkeypatch.setattr(ledger_queries, "query_ledger", lambda *args, **kwargs: projection)
    with pytest.raises(ProxyRequestError, match="invalid credential broker resource projection"):
        ledger_queries.granted_resource_row("/synthetic", principal="alice",
                                        command_center="cc-alice", grant_id="grant-alice")
