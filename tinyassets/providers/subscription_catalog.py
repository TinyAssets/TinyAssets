"""Owner-local native source facts adapted to the common selection contract."""
from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone

from tinyassets.providers.subscription_runtime import SubscriptionRuntime
from tinyassets.providers.subscription_source import digest


def context(definition, owner, universe):
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.owner_runtime import require

    require(_universe_dir(universe), owner)
    if (definition.owner_user_id != owner or definition.universe_id != universe
            or definition.access_method != "subscription_cli"):
        raise PermissionError("subscription source scope mismatch")
    runtime = SubscriptionRuntime(definition.ref)
    if not runtime.authenticated():
        raise PermissionError("subscription login is unavailable")
    # This is a credential-free connection incarnation; native token refresh
    # does not revoke the owner's accepted model scope.
    marker = runtime.directory / "connection"
    if not marker.is_file():
        raise PermissionError("subscription login has not been connected")
    return runtime, digest({"source": runtime.source, "connection": marker.read_text()})


def snapshot(definition):
    from tinyassets.providers.declared_models import declared_model_contract
    from tinyassets.providers.discovery_snapshot import DiscoverySnapshot
    from tinyassets.providers.model_policy import ConnectionModels, Scores

    runtime, fingerprint = context(definition, definition.owner_user_id, definition.universe_id)
    models = runtime.source["models"]
    contract = declared_model_contract({"wire": "chat_messages", "billing": "flat",
        "models": [{**model, "tools": True} for model in models]}, auth_scheme="native_login")
    provider = "subscription_cli:" + definition.id
    connection = ConnectionModels(provider, "subscription_cli:" + runtime.source["id"],
                                  "subscription_cli", "fresh",
                                  True, True, (), definition.model,
                                  availability_basis="owner_configured_contract")
    connection = replace(contract.model_decoder(None, connection=connection), owner_filtered=True)
    now = datetime.now(timezone.utc)
    ranking = runtime.source.get("ranking")
    if ranking is not None:
        freshness = "fresh" if now < datetime.fromisoformat(ranking["expires_at"]) else "stale"
        contract = replace(contract, ranking_source=ranking["source"])
        connection = replace(connection, models=tuple(replace(model, scores=Scores(
            ranking["source"], freshness, **ranking["scores"][model.model_id]))
            if model.model_id in ranking["scores"] else model for model in connection.models))
    return DiscoverySnapshot(definition.owner_user_id, definition.universe_id, provider,
                             definition.id, definition.ref, fingerprint, "", "", now, now,
                             connection, (), contract)
