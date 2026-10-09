"""Scoped discovery transport: real ledger/resolver/broker, synthetic network.

No account credentials or live sockets. This is not rendered app acceptance.
"""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.support.catalog_decoders import decode_openrouter_models
from tinyassets.exceptions import (
    ProviderOverloadedError,
    ProviderProtocolError,
    ProviderRateLimitedError,
    ProviderUnavailableError,
)
from tinyassets.providers import discovery_http as discovery
from tinyassets.providers.definition import ProviderDefinition
from tinyassets.providers.model_policy import ConnectionModels
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    CredentialBlindBroker,
    ScopedConnectionProxy,
    _enforce_endpoint_allowlist,
    _parse_canonical_https_url,
)

URL = "https://models.example.com/api/models/user"


@pytest.mark.parametrize("facts", [{}, {"resource": {}}, {"resource": None}])
def test_malformed_broker_projection_is_a_typed_refusal(tmp_path, monkeypatch, facts):
    from tinyassets.broker import ledger_queries

    monkeypatch.setattr(ledger_queries, "query_ledger", lambda *a, **kw: facts)
    with pytest.raises(discovery.ModelDiscoveryUnavailable) as caught:
        discovery.read_granted_discovery_document(
            db_path=tmp_path / "outbound.db", grant_id="grant", owner_user_id="owner",
            universe_id="universe", url=URL,
        )
    assert caught.value.reason == "discovery_unavailable"
    assert not (tmp_path / "outbound.db").exists()


def test_owned_grant_can_discover_before_model_registration(rig):
    assert discovery.read_granted_discovery_document(
        db_path=rig.db, grant_id="grant-discovery", owner_user_id="owner",
        universe_id="universe", url=URL,
    ) == {"data": []}
    assert len(rig.calls) == 1


@pytest.mark.parametrize("owner,universe", [("stranger", "universe"), ("owner", "other")])
def test_pre_registration_discovery_keeps_owner_and_universe_fences(rig, owner, universe):
    with pytest.raises(discovery.ModelDiscoveryUnavailable):
        discovery.read_granted_discovery_document(
            db_path=rig.db, grant_id="grant-discovery", owner_user_id=owner,
            universe_id=universe, url=URL,
        )
    assert not rig.calls


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
        universe_id="universe",
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
        _enforce_endpoint_allowlist(
            canonical, kwargs["verb"], kwargs["allowed_endpoints"], kwargs["access_mode"]
        )
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
        if (grant is None or grant.revoked_at is not None or grant.owner_user_id != principal
                or grant.universe_id != command_center):
            raise outbound_connections.GrantResolutionError("grant not admitted")
        with ledger._connect() as conn:
            row = conn.execute("SELECT * FROM outbound_connections WHERE connection_id = ?",
                               (grant.connection_id,)).fetchone()
        return {"resource": None if row is None else dict(row)}

    def broker_channel(base, *, principal, command_center, grant_id, connection_id):
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


def test_exact_get_uses_existing_resolver_and_closes_without_mutation(rig):
    before_grant = rig.ledger.get_grant("grant-discovery")
    before_view = rig.ledger.get_connection_view("conn-discovery")
    assert rig.read() == {"data": []}
    assert rig.calls == [("GET", {"url": URL})]
    assert len(rig.starts) == 1 and rig.closes == 1
    assert rig.starts[0]["owner_user_id"] == "owner"
    assert rig.starts[0]["universe_id"] == "universe"
    assert rig.ledger.get_grant("grant-discovery") == before_grant
    assert rig.ledger.get_connection_view("conn-discovery") == before_view
    assert rig.definition.model == "unchanged-legacy-pin"


def test_full_channel_keeps_its_existing_host_boundary(rig):
    with rig.ledger._connect() as raw:
        raw.execute(
            "UPDATE outbound_connections SET access_mode = 'full', scopes_json = '[\"POST\"]'"
        )
    another_path = "https://models.example.com/other/catalogue?version=2"
    assert rig.read(url=another_path) == {"data": []}
    with pytest.raises(ProviderUnavailableError):
        rig.read(url="https://another.example.com/other/catalogue")
    assert rig.calls == [("GET", {"url": another_path})] and rig.closes == 1


@pytest.mark.parametrize("response", [None, [], {"error": "synthetic-private-details"}])
def test_invalid_envelope_is_refused_without_revealing_remote_reason(rig, response):
    rig.response = response
    with pytest.raises(ProviderUnavailableError) as caught:
        rig.read()
    assert str(caught.value) == "discovery proxy returned no valid HTTP status"
    assert rig.closes == 1


@pytest.mark.parametrize(
    "kwargs",
    [
        {"owner_user_id": "someone-else"},
        {"owner_user_id": ""},
        {"universe_id": "someone-else"},
        {"universe_id": ""},
    ],
)
def test_wrong_authenticated_context_is_refused_before_proxy(rig, kwargs):
    with pytest.raises(ProviderUnavailableError):
        rig.read(**kwargs)
    assert not rig.starts


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_user_id": "someone-else"},
        {"universe_id": "someone-else"},
        {"ref": "grant-absent"},
        {"access_method": "subscription_cli"},
    ],
)
def test_definition_cannot_borrow_another_context(rig, changes):
    with pytest.raises(ProviderUnavailableError):
        rig.read(definition=replace(rig.definition, **changes))
    assert not rig.starts


@pytest.mark.parametrize("target", ["grant", "connection"])
def test_revoked_authority_is_refused_before_proxy(rig, target):
    if target == "grant":
        rig.ledger.revoke_grant("grant-discovery")
    else:
        rig.ledger.revoke_connection("conn-discovery")
    with pytest.raises(ProviderUnavailableError):
        rig.read()
    assert not rig.starts


@pytest.mark.parametrize(
    "table, column, value",
    [
        ("outbound_connection_grants", "owner_user_id", "someone-else"),
        ("outbound_connection_grants", "universe_id", "someone-else"),
        ("outbound_connections", "owner_user_id", "someone-else"),
        ("outbound_connections", "scopes_json", '["POST"]'),
    ],
)
def test_current_rows_not_just_definition_must_authorize_read(rig, table, column, value):
    with rig.ledger._connect() as raw:
        raw.execute(f"UPDATE {table} SET {column} = ?", (value,))
    with pytest.raises(ProviderUnavailableError):
        rig.read()
    assert not rig.starts


@pytest.mark.parametrize(
    "url",
    [
        "https://attacker.example/api/models/user",
        "https://models.example.com/custom/inference",
        URL + "?undeclared=1",
        URL + "#fragment",
        "http://models.example.com/api/models/user",
        "https://user:secret@models.example.com/api/models/user",
        URL.replace("/api/", "/../api/"),
        URL.replace("/api/", "/%2fapi/"),
        URL.replace(".com/", ".com:444/"),
        "",
        None,
        URL + "/" + "x" * 2048,
    ],
)
def test_unauthorized_or_noncanonical_url_opens_no_proxy(rig, url):
    with pytest.raises(ProviderUnavailableError):
        rig.read(url=url)
    assert not rig.starts


def test_same_context_is_checked_again_by_real_proxy_resolver(rig, monkeypatch):
    original = ConnectionLedger.resolve_exact_scoped_proxy

    def revoke_then_resolve(self, **kwargs):
        rig.ledger.revoke_grant("grant-discovery")
        return original(self, **kwargs)

    monkeypatch.setattr(ConnectionLedger, "resolve_exact_scoped_proxy", revoke_then_resolve)
    with pytest.raises(ProviderUnavailableError, match="transport failed"):
        rig.read()
    assert not rig.starts and not rig.calls


def test_real_broker_rechecks_revocation_after_proxy_resolution(rig):
    rig.before_dispatch = lambda: rig.ledger.revoke_grant("grant-discovery")
    with pytest.raises(ProviderUnavailableError, match="transport failed"):
        rig.read()
    assert len(rig.starts) == 1 and rig.closes == 1 and not rig.calls


def test_real_broker_passes_current_narrowed_endpoints_to_network(rig):
    def narrow():
        with rig.ledger._connect() as raw:
            raw.execute("UPDATE outbound_connections SET allowed_endpoints_json = '[]'")

    rig.before_dispatch = narrow
    with pytest.raises(ProviderUnavailableError, match="transport failed"):
        rig.read()
    assert rig.closes == 1 and not rig.calls


@pytest.mark.parametrize("field", ["fail_request", "fail_close"])
def test_failed_transport_closes_once_and_does_not_leak_details(rig, field):
    setattr(rig, field, True)
    with pytest.raises(ProviderUnavailableError) as caught:
        rig.read()
    assert str(caught.value) == "discovery transport failed" and rig.closes == 1
    assert len(rig.calls) <= 1


@pytest.mark.parametrize(
    "status, error",
    [
        (429, ProviderRateLimitedError),
        (500, ProviderOverloadedError),
        (503, ProviderOverloadedError),
        (403, ProviderProtocolError),
        (404, ProviderProtocolError),
        (302, ProviderProtocolError),
        (206, ProviderProtocolError),
        (204, ProviderProtocolError),
        (True, ProviderUnavailableError),
        (200.0, ProviderUnavailableError),
        ("200", ProviderUnavailableError),
        (None, ProviderUnavailableError),
    ],
)
def test_status_mapping_no_retry_or_redirect(rig, status, error):
    rig.response = {
        "status": status,
        "body": "private-remote-error",
        "location": "https://attacker.example",
    }
    with pytest.raises(error) as caught:
        rig.read()
    assert "private-remote-error" not in str(caught.value)
    assert len(rig.calls) == 1 and rig.closes == 1


@pytest.mark.parametrize(
    "body",
    [
        "",
        None,
        {},
        "not json",
        "[]",
        "null",
        '{"data":[],"data":[1]}',
        '{"price":NaN}',
        '{"price":Infinity}',
        '{"price":-Infinity}',
        "[" * 2000 + "]" * 2000,
        "\ud800",
    ],
)
def test_invalid_json_is_refused_after_close_without_body_disclosure(rig, body):
    rig.response = {"status": 200, "body": body}
    with pytest.raises(ProviderProtocolError, match="bounded JSON object"):
        rig.read()
    assert rig.closes == 1


@pytest.mark.parametrize("text", ["x" * 80, "界" * 30])
def test_decoding_applies_transport_byte_bound_not_character_count(rig, monkeypatch, text):
    monkeypatch.setattr(discovery, "_SSRF_MAX_BODY_BYTES", 64)
    rig.response["body"] = json.dumps({"data": text}, ensure_ascii=False)
    with pytest.raises(ProviderProtocolError):
        rig.read()
    assert rig.closes == 1


def test_refresh_adds_unknown_model_without_changing_grant_or_following_links(rig):
    context = ConnectionModels(
        "conn-discovery", "opaque-provider", "http", "fresh", True, False, ()
    )
    before = rig.ledger.get_connection_view("conn-discovery")
    first = decode_openrouter_models(rig.read(), connection=context)
    rig.response["body"] = json.dumps(
        {
            "data": [
                {
                    "id": "new-provider/brand-new-model",
                    "pricing": {},
                    "links": {"catalogue": "https://attacker.example"},
                }
            ]
        }
    )
    second = decode_openrouter_models(rig.read(), connection=context)
    assert first.models == () and second.models[0].model_id == "new-provider/brand-new-model"
    assert not second.executor_tools  # Read success cannot upgrade executor readiness.
    assert rig.ledger.get_connection_view("conn-discovery") == before
    assert rig.calls == [("GET", {"url": URL}), ("GET", {"url": URL})]
    assert rig.closes == 2
