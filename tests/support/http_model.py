"""Owner-bound HTTP serving fixtures shared by orchestration tests."""
import hashlib
from pathlib import Path

from tinyassets.providers.definition import _definition_id


def provider_name(owner="acct_alice", universe="universe_alice"):
    return "api_key_http:" + _definition_id(
        universe_id=universe, owner_user_id=owner, access_method="api_key_http",
        protocol="openai_chat", model="synthetic-model",
        ref="http_grant_" + hashlib.sha256((owner + universe).encode()).hexdigest()[:32],
    )

HTTP_PROVIDER = provider_name()

def seed_http_assignment(
    base_path: Path,
    *,
    owner_user_id: str = "acct_alice",
    universe_id: str = "universe_alice",
    select_for_serving: bool = True,
    return_binding: bool = False,
    model_access=None,
    model="synthetic-model",
    host="api.example.com",
) -> str:
    """Select one synthetic owner-bound HTTP provider for foreground runs."""
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.commons_catalog import create_binding, publish_definition
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.providers.definition import register_definition
    from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger

    seed_owner_admission(base_path, principal=owner_user_id, center=universe_id)
    universe_dir = base_path / universe_id
    universe_dir.mkdir(exist_ok=True)
    connection_id = "http_" + hashlib.sha256(owner_user_id.encode()).hexdigest()[:32]
    grant_key = hashlib.sha256((owner_user_id + universe_id).encode()).hexdigest()[:32]
    grant_id = "http_grant_" + grant_key
    ledger = ConnectionLedger(
        base_path / ".broker" / "outbound.db", data_root=base_path,
        verify_authenticated_principal=lambda: owner_user_id,
    )
    ledger.create_connection(
        connection_id=connection_id,
        owner_user_id=owner_user_id,
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("POST",) if model_access is None else ("GET", "POST"),
        provider="http",
        destination="compute:synthetic",
        credential_ref="vault://http/compute:synthetic",
        allowed_endpoints=[{
            "host": host,
            "path_template": "/v1/chat/completions",
            "methods": ["POST"],
        }] + ([{
            "host": host, "path_template": "/api/v1/models/user", "methods": ["GET"],
            "allowed_query": ["output_modalities"], "required_query": ["output_modalities"],
            "query_patterns": {"output_modalities": "^all$"},
        }] if model_access is not None else []),
    )
    ledger.grant_connection(
        grant_id=grant_id,
        connection_id=connection_id,
        owner_user_id=owner_user_id,
        universe_id=universe_id,
        unprompted_action_cap=ActionCap("http_requests", 100, "requests"),
    )
    definition = register_definition(
        universe_id=universe_id,
        owner_user_id=owner_user_id,
        access_method="api_key_http",
        protocol="openai_chat",
        model=model,
        ref=grant_id,
    )
    if model_access is not None:
        ledger.configure_capability(
            connection_id=connection_id, capability_kind="model_discovery", enabled=True,
            descriptor={"protocol": "openrouter_user_models_v1",
                        "catalogue_url": "https://api.example.com/api/v1/models/user?output_modalities=all",
                        "benchmark_url": ""},
            expected_grant=ledger.get_grant(grant_id),
        )
    else:
        ledger.configure_capability(
            connection_id=connection_id, capability_kind="model_use", enabled=True,
            descriptor={
                "wire": "openai_chat", "billing": "free",
                "models": [{"id": model, "tools": True, "context": 8192}],
            },
        )
    published = publish_definition(
        base_path,
        author_id=owner_user_id,
        payload={
            "schema_version": 1,
            "name": "Foreground open-provider agent",
            "description": "Synthetic foreground-run authority fixture.",
            "tags": ["test"],
            "components": {"identity": {"kind": "soul", "config": {}}},
        },
    )
    agent = create_binding(
        base_path,
        universe_id=universe_id,
        definition_id=published["agent_definition_id"],
        created_by=owner_user_id,
        payload={
            "schema_version": 1,
            "name": "Foreground open-provider agent",
            "role": "writer",
        },
    )
    if select_for_serving:
        connected = bind_serving_provider(
            base_path=base_path,
            universe_dir=universe_dir,
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            agent_binding_id=agent["agent_binding_id"],
            expected_revision=1,
            provider=definition.id,
            model_access=None if model_access is None else {definition.id: model_access},
        )
        set_serving(
            base_path=base_path,
            universe_dir=universe_dir,
            owner_user_id=owner_user_id,
            universe_id=universe_id,
            agent_binding_id=agent["agent_binding_id"],
            expected_revision=connected["agent_binding"]["revision"],
            enabled=True,
        )
    if return_binding:
        return universe_dir, agent
    return f"api_key_http:{definition.id}"
