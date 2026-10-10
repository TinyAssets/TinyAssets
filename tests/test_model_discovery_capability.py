"""Model profile metadata publication, independent of any powered agent."""

import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tinyassets.api.provider_capability import configure_provider_capability
from tinyassets.providers import definition as definitions
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    ModelDiscoveryCapability,
    SsrfValidationError,
)

CATALOGUE = "https://owned.example/api/v1/models/user?output_modalities=all"
BENCHMARK = "https://owned.example/api/v1/benchmarks"
DESCRIPTOR = {
    "protocol": "openrouter_user_models_v1",
    "catalogue_url": CATALOGUE,
    "benchmark_url": BENCHMARK,
}


@pytest.fixture
def rig(tmp_path, monkeypatch):
    from tests.support.broker_double import seed_owner_admission
    from tinyassets import daemon_server, provider_serving_binding
    from tinyassets.api import permissions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_owner_admission(tmp_path, principal="owner", center="u-models")
    (tmp_path / "u-models").mkdir()
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    monkeypatch.setattr(permissions, "current_actor_id", lambda: "owner")
    monkeypatch.setattr(
        daemon_server,
        "list_universe_acl",
        lambda base, *, universe_id: (
            [{"actor_id": "owner", "permission": "admin"}] if universe_id == "u-models" else []
        ),
    )

    def must_not_need_serving(*args, **kwargs):
        raise AssertionError("discovery configuration must not depend on a powered agent")

    monkeypatch.setattr(daemon_server, "get_founder_home", must_not_need_serving)
    monkeypatch.setattr(
        provider_serving_binding,
        "resolve_current_serving_provider_authority",
        must_not_need_serving,
    )
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="conn-models",
        owner_user_id="owner",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("GET", "POST"),
        provider="http",
        destination="compute:models",
        credential_ref="vault://http/synthetic",
        allowed_endpoints=[
            {
                "host": "owned.example",
                "path_template": "/api/v1/models/user",
                "methods": ["GET"],
                "allowed_query": ["output_modalities"],
                "required_query": ["output_modalities"],
                "query_patterns": {"output_modalities": "^all$"},
            },
            {"host": "owned.example", "path_template": "/api/v1/benchmarks", "methods": ["GET"]},
            {"host": "owned.example", "path_template": "/custom/chat", "methods": ["POST"]},
        ],
    )
    grant = ledger.grant_connection(
        grant_id="grant-models",
        connection_id="conn-models",
        owner_user_id="owner",
        universe_id="u-models",
    )
    definition = definitions.register_definition(
        universe_id="u-models",
        owner_user_id="owner",
        access_method="api_key_http",
        protocol="openai_chat",
        model="legacy-fixed",
        ref=grant.grant_id,
    )

    def publish(**kwargs):
        args = dict(
            connection_id="conn-models",
            capability_kind="model_discovery",
            descriptor=DESCRIPTOR,
            enabled=True,
            expected_grant=grant,
        )
        return ledger.configure_capability(**(args | kwargs))

    def api(**changes):
        doc = {
            "capability_kind": "model_discovery",
            "enabled": True,
            "definition_id": definition.id,
            "descriptor": DESCRIPTOR,
        } | changes
        if doc["enabled"] is False:
            doc.pop("descriptor", None)
        return configure_provider_capability(universe_id="u-models", payload=doc)

    return SimpleNamespace(
        ledger=ledger, grant=grant, definition=definition, publish=publish, api=api, base=tmp_path
    )


def test_typed_roundtrip_uses_existing_table_without_changing_identity_or_grant(rig):
    before_view = rig.ledger.get_connection_view("conn-models")
    first = rig.publish()
    assert isinstance(first, ModelDiscoveryCapability)
    assert first.descriptor() == DESCRIPTOR
    assert rig.publish() == first
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") == first
    assert rig.ledger.get_connection_view("conn-models") == before_view
    assert rig.ledger.get_grant("grant-models") == rig.grant
    assert definitions.get_definition("u-models", rig.definition.id) == rig.definition
    with rig.ledger._connect() as raw:
        assert raw.execute("SELECT COUNT(*) FROM connection_capabilities").fetchone()[0] == 1
    assert not hasattr(first, "credential_ref")
    assert not hasattr(first, "owner_filtered")


def test_authenticated_unpowered_owner_can_configure_and_remove(rig):
    result = rig.api()
    assert result == {
        "status": "configured",
        "capability_kind": "model_discovery",
        "provider": f"api_key_http:{rig.definition.id}",
        "scope": "connection",
        "descriptor": DESCRIPTOR,
    }
    assert rig.api(enabled=False)["status"] == "revoked"
    assert rig.api(enabled=False)["status"] == "revoked"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None
    assert rig.ledger.get_grant("grant-models") == rig.grant


def test_served_discovery_setup_preserves_existing_connection_and_grant(rig, monkeypatch):
    from tinyassets import engine_mcp_server as engine
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    monkeypatch.setattr(engine, "_ACTOR_ID", "owner")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-models")
    monkeypatch.setattr(engine, "_engine_run_admit", lambda **kw: True)
    from tests.engine_authority_helpers import seed_bound_engine
    seed_bound_engine(monkeypatch)
    before = rig.ledger.get_connection_view("conn-models")
    document = {"capability_kind": "model_discovery", "enabled": True,
                "definition_id": rig.definition.id, "descriptor": DESCRIPTOR}

    def configure(payload):
        return json.loads(engine.write_graph(
            target="connection", operation="configure_provider_capability",
            payload_json=json.dumps(payload),
        ))

    assert configure(document)["status"] == "configured"
    outside = document | {"descriptor": DESCRIPTOR | {
        "benchmark_url": BENCHMARK.replace("owned.example", "other.example"),
    }}
    assert configure(outside)["error"] == "provider_capability_invalid"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery").descriptor() == (
        DESCRIPTOR
    )
    assert rig.ledger.get_connection_view("conn-models") == before
    assert rig.ledger.get_grant("grant-models") == rig.grant
    assert definitions.get_definition("u-models", rig.definition.id) == rig.definition
    with SQLiteProviderWorkAuthorityStore(rig.base).connection() as conn:
        assert conn.execute("SELECT count(*) FROM provider_work_bindings").fetchone()[0] == 0

    # A current tool identity does not turn a foreign universe's definition into access.
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-other")
    from tests.engine_authority_helpers import seed_bound_engine
    seed_bound_engine(monkeypatch)
    assert configure(document)["error"] == "not_found"
    assert rig.ledger.get_grant("grant-models") == rig.grant


def test_all_definitions_sharing_connection_share_and_remove_metadata(rig):
    second = definitions.register_definition(
        universe_id="u-models",
        owner_user_id="owner",
        access_method="api_key_http",
        protocol="openai_chat",
        model="another-legacy-pin",
        ref=rig.grant.grant_id,
    )
    assert rig.api()["status"] == "configured"
    assert rig.api(definition_id=second.id, enabled=False)["status"] == "revoked"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None
    assert definitions.get_definition("u-models", rig.definition.id) == rig.definition


@pytest.mark.parametrize(
    "changes",
    [
        {"protocol": "unknown_protocol"},
        {"protocol": []},
        {"owner_filtered": True},
        {"credential_ref": "vault://http/other"},
        {"catalogue_url": "https://owned.example/api/v1/models"},
        {"catalogue_url": CATALOGUE.split("?")[0]},
        {"catalogue_url": CATALOGUE + "&offset=1"},
        {"catalogue_url": CATALOGUE.replace("=all", "=text")},
        {"catalogue_url": CATALOGUE.replace("https:", "http:")},
        {"catalogue_url": CATALOGUE.replace("owned.example", "user:password@owned.example")},
        {"catalogue_url": CATALOGUE + "#fragment"},
        {"benchmark_url": BENCHMARK + "?limit=1"},
        {"benchmark_url": CATALOGUE},
    ],
)
def test_profile_rejects_global_partial_or_untrusted_descriptor_shape(rig, changes):
    with pytest.raises(ValueError):
        rig.publish(descriptor=DESCRIPTOR | changes)
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None


def test_optional_benchmark_may_be_absent_but_cannot_escape_grant(rig):
    assert rig.publish(descriptor={k: v for k, v in DESCRIPTOR.items() if k != "benchmark_url"})
    before = rig.ledger.get_connection_capability("conn-models", "model_discovery")
    with pytest.raises(SsrfValidationError):
        rig.publish(
            descriptor=DESCRIPTOR
            | {"benchmark_url": BENCHMARK.replace("owned.example", "other.example")}
        )
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") == before


@pytest.mark.parametrize(
    "column,value",
    [
        ("scopes_json", '["POST"]'),
        ("allowed_endpoints_json", "[]"),
        ("auth_scheme", "none"),
        ("owner_user_id", "other-owner"),
    ],
)
def test_publication_requires_current_get_and_credentialed_owner_authority(rig, column, value):
    with rig.ledger._connect() as raw:
        raw.execute(f"UPDATE outbound_connections SET {column} = ?", (value,))
    with pytest.raises((PermissionError, SsrfValidationError)):
        rig.publish()
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None


@pytest.mark.parametrize(
    "changes",
    [
        {"owner_user_id": "other"},
        {"universe_id": "other"},
        {"connection_id": "other"},
        {"granted_at": 0},
        {"revoked_at": 1},
        {"grant_id": "other"},
    ],
)
def test_transaction_fences_stale_or_forged_expected_grant(rig, changes):
    with pytest.raises(PermissionError):
        rig.publish(expected_grant=replace(rig.grant, **changes))
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None


def test_new_kind_requires_grant_context_even_to_remove(rig):
    rig.publish()
    for enabled in (True, False):
        with pytest.raises(PermissionError):
            rig.publish(enabled=enabled, expected_grant=None)
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery")


def test_revocation_preserves_metadata_but_cannot_publish_again(rig):
    stored = rig.publish()
    rig.ledger.revoke_grant("grant-models")
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") == stored
    assert rig.api() == {"error": "not_found", "resource": "connection"}


def test_revoke_between_handler_read_and_write_is_refused(rig, monkeypatch):
    original = ConnectionLedger.configure_capability

    def revoke_first(self, **kwargs):
        self.revoke_grant("grant-models")
        return original(self, **kwargs)

    monkeypatch.setattr(ConnectionLedger, "configure_capability", revoke_first)
    assert rig.api() == {"error": "not_found", "resource": "connection"}
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None


@pytest.mark.parametrize(
    "fields",
    [
        {"owner_user_id": "owner"},
        {"connection_id": "conn-models"},
        {"grant_id": "grant-models"},
        {"definition_id": None},
        {"enabled": "true"},
    ],
)
def test_api_closed_shape_does_not_accept_caller_authority_fields(rig, fields):
    assert rig.api(**fields)["error"] == "provider_capability_invalid"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery") is None


def test_api_requires_authentication_and_exact_owner_even_for_admin(rig, monkeypatch):
    from tinyassets.api import permissions

    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: False)
    assert rig.api()["error"] == "authentication_required"
    monkeypatch.setattr(permissions, "is_authenticated_request", lambda: True)
    foreign = definitions.register_definition(
        universe_id="u-models",
        owner_user_id="other-owner",
        access_method="api_key_http",
        protocol="openai_chat",
        model="another",
        ref=rig.grant.grant_id,
    )
    assert rig.api(definition_id=foreign.id) == {"error": "not_found", "resource": "connection"}


def test_api_verified_lookup_rejects_tampered_definition(rig):
    path = definitions._store_path("u-models")
    rows = json.loads(path.read_text(encoding="utf-8"))
    rows[0]["ref"] = "grant-tampered"
    path.write_text(json.dumps(rows), encoding="utf-8")
    assert rig.api() == {"error": "not_found", "resource": "connection"}


def test_api_verified_lookup_rejects_definition_copied_from_other_universe(rig):
    foreign = definitions.register_definition(
        universe_id="u-other",
        owner_user_id="owner",
        access_method="api_key_http",
        protocol="openai_chat",
        model="other-model",
        ref=rig.grant.grant_id,
    )
    path = definitions._store_path("u-models")
    path.write_text(json.dumps([foreign.as_dict()]), encoding="utf-8")
    assert rig.api(definition_id=foreign.id) == {"error": "not_found", "resource": "connection"}


def test_corrupt_stored_descriptor_is_not_reinterpreted_as_valid_profile(rig):
    rig.publish()
    with rig.ledger._connect() as raw:
        raw.execute(
            "UPDATE connection_capabilities SET descriptor_json = ?",
            (json.dumps(DESCRIPTOR | {"owner_filtered": True}),),
        )
    with pytest.raises(ValueError):
        rig.ledger.get_connection_capability("conn-models", "model_discovery")


def test_existing_write_graph_operation_routes_unpowered_discovery(rig):
    from tinyassets.universe_server import write_graph

    response = json.loads(
        write_graph(
            target="connection",
            operation="configure_provider_capability",
            graph_id="u-models",
            payload_json=json.dumps(
                {
                    "capability_kind": "model_discovery",
                    "enabled": True,
                    "definition_id": rig.definition.id,
                    "descriptor": DESCRIPTOR,
                }
            ),
        )
    )
    assert response["status"] == "configured"
    assert rig.ledger.get_connection_capability("conn-models", "model_discovery")


@pytest.mark.parametrize("value", [None, [], 42])
def test_malformed_optional_benchmark_is_not_silently_discarded(rig, value):
    with pytest.raises(ValueError, match="benchmark_url must be a string"):
        rig.publish(descriptor=DESCRIPTOR | {"benchmark_url": value})


def test_profile_publication_preserves_full_channel_semantics(rig):
    with rig.ledger._connect() as raw:
        raw.execute(
            "UPDATE outbound_connections SET access_mode = 'full', scopes_json = '[\"POST\"]'"
        )
    assert rig.publish().catalogue_url == CATALOGUE


def test_path_permission_alone_does_not_grant_catalogue_query(rig):
    with rig.ledger._connect() as raw:
        endpoints = json.loads(
            raw.execute("SELECT allowed_endpoints_json FROM outbound_connections").fetchone()[0]
        )
        for key in ("allowed_query", "required_query", "query_patterns"):
            endpoints[0].pop(key, None)
        raw.execute(
            "UPDATE outbound_connections SET allowed_endpoints_json = ?", (json.dumps(endpoints),)
        )
    with pytest.raises(SsrfValidationError):
        rig.publish()
