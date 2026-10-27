"""Model profile metadata publication, independent of any powered agent."""

from types import SimpleNamespace

import pytest

from tinyassets.api.provider_capability import configure_provider_capability
from tinyassets.providers import definition as definitions
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
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

    )
    from tests.support.broker_double import grant_consent

    # N10: the router's model connect consents its calls; no model call is exempt.
    grant_consent(tmp_path, owner="owner", sink="authenticated_external_call",
                  destination="compute:models")
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
