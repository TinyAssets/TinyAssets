"""D25 serving custody reads use the caller's admitted owner over broker IPC."""
from types import SimpleNamespace

import pytest

from tinyassets import provider_serving_binding as serving
from tinyassets.broker import ledger_queries, supervisor
from tinyassets.providers.definition import register_definition
from tinyassets.storage.outbound_connections import ConnectionLedger, ProxyRequestError


@pytest.fixture
def case(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    ledger = ConnectionLedger(tmp_path / ".broker/outbound.db", data_root=tmp_path)
    definitions = {}
    for owner in ("alice", "bob"):
        ledger.create_connection(
            connection_id=f"conn-{owner}", owner_user_id=owner, connection_class="http",
            connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
            destination=f"compute:{owner}", credential_ref=f"vault://http/{owner}",
            allowed_endpoints=[{"host": "models.example.com", "path_template": "/chat",
                                "methods": ["POST"]}])
        ledger.grant_connection(grant_id=f"grant-{owner}", connection_id=f"conn-{owner}",
                                owner_user_id=owner, universe_id="cc-alice")
        definitions[owner] = register_definition(
            universe_id="cc-alice", owner_user_id=owner, access_method="api_key_http",
            protocol="chat_messages", model=f"{owner}-fixture", ref=f"grant-{owner}")
    definitions["foreign"] = register_definition(
        universe_id="cc-alice", owner_user_id="alice", access_method="api_key_http",
        protocol="chat_messages", model="foreign-fixture", ref="grant-bob")
    calls = []

    class Client:
        def __init__(self, path, *, principal, command_center, **kwargs):
            self.principal, self.center = principal, command_center

        def ledger_query(self, **kwargs):
            calls.append((self.principal, self.center, kwargs))
            return ledger_queries.local_query(ledger, principal=self.principal,
                                              command_center=self.center, **kwargs)

    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: SimpleNamespace(
        socket_path=tmp_path / "broker.sock", fence=lambda: (1, "synthetic"), verify_broker=None))
    monkeypatch.setattr("tinyassets.broker.client.BrokerClient", Client)

    def no_local(*args, **kwargs):
        raise AssertionError("daemon opened a local ledger")

    monkeypatch.setattr("tinyassets.storage.outbound_connections.ConnectionLedger", no_local)
    return SimpleNamespace(root=tmp_path, ledger=ledger, definitions=definitions, calls=calls)


def test_serving_context_and_initial_id_use_same_admitted_scope(case):
    definition = case.definitions["alice"]
    provider = f"api_key_http:{definition.id}"
    assert serving._open_serving_context(case.root, "cc-alice", "alice", definition.id) == (
        provider, "grant-alice", "conn-alice", "vault://http/alice")
    assert serving._open_connection_id(case.root, "cc-alice", provider,
                                        owner_user_id="alice") == "conn-alice"
    assert case.calls == [("alice", "cc-alice", {
        "query": ledger_queries.GRANTED_RESOURCE, "grant_id": "grant-alice",
        "connection_id": ""})] * 2
    assert not (case.root / "outbound.db").exists()


@pytest.mark.parametrize("definition,owner", [
    ("alice", "bob"), ("bob", "alice"), ("foreign", "alice"), ("alice", ""),
])
def test_serving_cannot_infer_or_borrow_definition_owner(case, definition, owner):
    did = case.definitions[definition].id
    with pytest.raises(serving.ServingProviderNotOwned):
        serving._open_serving_context(case.root, "cc-alice", owner, did)
    with pytest.raises(serving.ServingProviderNotOwned):
        serving._open_connection_id(case.root, "cc-alice", f"api_key_http:{did}",
                                    owner_user_id=owner)


@pytest.mark.parametrize("kind", ["grant", "connection"])
def test_serving_rechecks_revoked_authority(case, kind):
    if kind == "grant":
        case.ledger.revoke_grant("grant-alice")
    else:
        case.ledger.revoke_connection("conn-alice")
    with pytest.raises(serving.ServingProviderNotOwned):
        serving._open_serving_context(case.root, "cc-alice", "alice", case.definitions["alice"].id)


def test_custody_digest_is_recomputed_from_broker_resource(case):
    from tinyassets.credential_vault import _connection_grant_record_digest

    provider = f"api_key_http:{case.definitions['alice'].id}"
    custody = SimpleNamespace(_record_digest=_connection_grant_record_digest(
        grant_id="grant-alice", connection_id="conn-alice", credential_ref="vault://http/alice",
        owner_user_id="alice", universe_id="cc-alice"))
    assert serving.verify_open_grant_custody(
        case.root, "cc-alice", "alice", provider, custody) == "conn-alice"
    with case.ledger._connect() as conn:
        conn.execute("UPDATE outbound_connections SET credential_ref = ? WHERE connection_id = ?",
                     ("vault://http/rotated", "conn-alice"))
    with pytest.raises(PermissionError, match="changed since binding"):
        serving.verify_open_grant_custody(case.root, "cc-alice", "alice", provider, custody)


def test_serving_broker_outage_has_no_local_fallback(case, monkeypatch):
    monkeypatch.setattr(supervisor, "get_supervisor", lambda _: None)
    with pytest.raises(ProxyRequestError, match="not running"):
        serving._open_serving_context(case.root, "cc-alice", "alice", case.definitions["alice"].id)


def test_real_bind_and_set_serving_use_broker_custody(case):
    from tinyassets.custom_agents import create_binding, publish_definition

    home = case.root / "cc-alice"
    home.mkdir(exist_ok=True)
    published = publish_definition(case.root, author_id="alice", payload={
        "schema_version": 1, "name": "Served", "description": "broker custody fixture",
        "tags": ["test"], "components": {"identity": {"kind": "soul", "config": {}}}})
    agent = create_binding(case.root, universe_id="cc-alice",
                           definition_id=published["agent_definition_id"], created_by="alice",
                           payload={"schema_version": 1, "name": "Served", "role": "writer"})
    connected = serving.bind_serving_provider(
        base_path=case.root, universe_dir=home, owner_user_id="alice", universe_id="cc-alice",
        agent_binding_id=agent["agent_binding_id"], expected_revision=1,
        provider=case.definitions["alice"].id)
    assert connected["status"] == "ready"
    result = serving.set_serving(
        base_path=case.root, universe_dir=home, owner_user_id="alice", universe_id="cc-alice",
        agent_binding_id=agent["agent_binding_id"],
        expected_revision=connected["agent_binding"]["revision"], enabled=True)
    assert result["agent_binding"]["status"] == "serving"
    from tinyassets.auth.middleware import (
        claim_provider_request,
        mint_provider_request_carrier,
        reserve_provider_request,
    )
    from tinyassets.provider_assignment import (
        authorize_served_provider_call,
        reserve_served_provider_budget,
    )

    reserve = reserve_provider_request(
        principal_id="alice", session_id="d25-session", request_id="d25-request",
        tool_name="converse")
    claim_provider_request(reserve, tool_name="converse")
    carrier = mint_provider_request_carrier(
        universe_id="cc-alice", agent_binding_id=agent["agent_binding_id"],
        binding_revision=result["agent_binding"]["revision"], operation="converse")
    with authorize_served_provider_call(
        case.root, universe_dir=home, request_carrier=carrier, role="writer", operation="converse",
    ) as authority:
        assert authority.authority_kind == "connection_grant"
        reservation = reserve_served_provider_budget(
            case.root, universe_dir=home, authority=authority,
            requested_output_tokens=100, estimated_input_tokens=50)
        assert reservation.reserved_total_tokens >= 150
    assert len(case.calls) >= 3
    assert all(principal == "alice" and center == "cc-alice"
               for principal, center, _ in case.calls)
    assert not (case.root / "outbound.db").exists()
