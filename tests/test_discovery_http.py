"""Scoped discovery transport: real ledger/resolver/broker, synthetic network.

No account credentials or live sockets. This is not rendered app acceptance.
"""

from types import SimpleNamespace

import pytest

from tinyassets.exceptions import (
    ProviderUnavailableError,
)
from tinyassets.providers import discovery_http as discovery
from tinyassets.providers.definition import ProviderDefinition
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    CredentialBlindBroker,
    ScopedConnectionProxy,
    _credential_endpoint,
    _parse_canonical_https_url,
)

URL = "https://models.example.com/api/models/user"


@pytest.fixture
def rig(tmp_path, monkeypatch):
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner"
    )
    endpoints = [
        {"host": "models.example.com", "path_template": "/api/models/user", "methods": ["GET"]},
        {"host": "models.example.com", "path_template": "/custom/inference", "methods": ["POST"]},
    ]
    ledger.create_connection(
        connection_id="conn-discovery",
        owner_user_id="owner",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("GET", "POST"),
        provider="http",
        destination="compute:discovery",
        credential_ref="vault://http/synthetic",
        allowed_endpoints=endpoints,
    )
    ledger.grant_connection(
        grant_id="grant-discovery",
        connection_id="conn-discovery",
        owner_user_id="owner",

    )
    definition = ProviderDefinition(
        id="provdef-discovery",
        universe_id="universe",
        owner_user_id="owner",
        access_method="api_key_http",
        protocol="openai_chat",
        model="unchanged-legacy-pin",
        ref="grant-discovery",
        visibility="private",
        created_at="2026-09-09",
    )
    state = SimpleNamespace(
        ledger=ledger,
        db=tmp_path / "outbound.db",
        definition=definition,
        calls=[],
        auth=[],
        starts=[],
        closes=0,
        response={"status": 200, "body": '{"data":[]}'},
        before_dispatch=lambda: None,
        fail_request=False,
        fail_close=False,
    )

    def network(**kwargs):
        wire = kwargs["request"]
        canonical = _parse_canonical_https_url(wire["url"], allowed_ports=frozenset({443}))
        _credential_endpoint(
            canonical, kwargs["verb"], kwargs["allowed_endpoints"], kwargs["access_mode"]
        )
        state.auth.append(kwargs["auth_scheme"])
        state.calls.append((kwargs["verb"], wire))
        return state.response

    broker = CredentialBlindBroker(
        ledger, resolve_credential=lambda *_: "synthetic-nonsecret", network_request=network
    )

    class Channel:
        def request(self, verb, wire):
            state.before_dispatch()
            if state.fail_request:
                raise RuntimeError("synthetic-private-upstream-details")
            return broker.dispatch("grant-discovery", verb, wire)

        def close(self):
            state.closes += 1
            if state.fail_close:
                raise RuntimeError("synthetic-private-close-details")

    # The broker's two seams, answered from the same real ledger: its one
    # read transaction (``query_ledger``) and its scoped channel.
    from tinyassets.broker import ledger_queries
    from tinyassets.storage import outbound_connections

    def query_ledger(base, *, query, principal, command_center, grant_id):
        grant = ledger.get_grant(grant_id)
        if grant is None or grant.revoked_at is not None or grant.owner_user_id != principal:
            raise outbound_connections.GrantResolutionError("grant not admitted")
        with ledger._connect() as conn:
            row = conn.execute("SELECT * FROM outbound_connections WHERE connection_id = ?",
                               (grant.connection_id,)).fetchone()
        return {"resource": None if row is None else dict(row)}

    def broker_channel(base, *, principal, command_center, grant_id, connection_id):
        ledger.authorize_exact( grant_id=grant_id,
                               connection_id=connection_id)
        state.starts.append({"owner_user_id": principal, "universe_id": command_center,
                             "grant_id": grant_id, "connection_id": connection_id})
        view = ledger.get_connection_view(connection_id)
        return ScopedConnectionProxy(
            grant_id=grant_id, provider=view.provider, destination=view.destination,
            scopes=view.scopes, _channel=Channel(), access_mode=view.access_mode)

    monkeypatch.setattr(ledger_queries, "query_ledger", query_ledger)
    monkeypatch.setattr(discovery, "_broker_channel", broker_channel)

    def read(**kwargs):
        args = dict(
            db_path=state.db,
            definition=state.definition,
            owner_user_id="owner",
            universe_id="universe",
            url=URL,
        )
        return discovery.read_http_discovery_document(**(args | kwargs))

    state.read = read
    return state


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_full_channel_scopes_credentials_to_its_service(rig):
    with rig.ledger._connect() as raw:
        raw.execute(
            "UPDATE outbound_connections SET access_mode = 'full', scopes_json = '[\"POST\"]'"
        )
    another_path = "https://models.example.com/other/catalogue?version=2"
    assert rig.read(url=another_path) == {"data": []}
    other = "https://another.example.com/other/catalogue"
    assert rig.read(url=other) == {"data": []}
    assert rig.calls == [("GET", {"url": another_path}), ("GET", {"url": other})]
    assert rig.auth == ["bearer", "none"]
    assert rig.closes == 2


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_real_broker_rechecks_revocation_after_proxy_resolution(rig):
    rig.before_dispatch = lambda: rig.ledger.revoke_grant("grant-discovery")
    with pytest.raises(ProviderUnavailableError, match="transport failed"):
        rig.read()
    assert len(rig.starts) == 1 and rig.closes == 1 and not rig.calls


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_real_broker_passes_current_narrowed_endpoints_to_network(rig):
    def narrow():
        with rig.ledger._connect() as raw:
            raw.execute("UPDATE outbound_connections SET allowed_endpoints_json = '[]'")

    rig.before_dispatch = narrow
    assert rig.read() == {"data": []}
    assert rig.closes == 1
    assert rig.calls == [("GET", {"url": URL})]
    assert rig.auth == ["none"]
