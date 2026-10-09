"""Compose a granted catalogue into an inert, free-only bootstrap candidate."""

from dataclasses import replace
from pathlib import Path

from tinyassets.onboarding.hosted_model_auth import AcquisitionPreset, HostedAuthError


def prepare_candidate(*, base: Path, uid: str, owner: str, grant_id: str,
                      preset: AcquisitionPreset) -> str:
    """No credential handling or serving. Caller first deposits the exact grant.

    The registered descriptor needs a model, but no model release is bundled in
    onboarding. Derive its inert seed from fresh granted data and the SAME free
    eligibility policy used by execution. Later consent uses discovered scope,
    not this seed as a pinned default or permission for legacy execution.
    """
    from tinyassets.api.compute_connection import connect_compute
    from tinyassets.api.provider_capability import configure_provider_capability
    from tinyassets.onboarding.serving import _require_current_admin
    from tinyassets.providers.definition import list_definitions
    from tinyassets.providers.discovery_http import read_granted_discovery_document
    from tinyassets.providers.discovery_protocols import discovery_protocol
    from tinyassets.providers.model_policy import (
        Catalog,
        ConnectionModels,
        ModelPolicy,
        order_models,
    )
    from tinyassets.providers.protocol_encoders import agent_codec_for
    from tinyassets.providers.wire_dialects import same_dialect
    from tinyassets.shared_self import require_founder_home

    def current():
        require_founder_home(base, uid, owner)
        _require_current_admin(base, universe_id=uid, owner=owner)

    current()
    contract = discovery_protocol(preset.id)
    matches = [d for d in list_definitions(uid) if d.ref == grant_id]
    if len(matches) > 1:
        raise HostedAuthError("model_candidate_requires_review", 409)
    if matches:
        definition = matches[0]
        if (definition.owner_user_id != owner or definition.access_method != "api_key_http"
                or not same_dialect(definition.protocol, contract.inference_protocol)
                or definition.visibility != "private"):
            raise HostedAuthError("model_candidate_requires_review", 409)
        did = definition.id
    else:
        payload = read_granted_discovery_document(
            db_path=base / "outbound.db", grant_id=grant_id, owner_user_id=owner,
            universe_id=uid, url=preset.catalogue_url,
        )
        connection = contract.model_decoder(payload, connection=ConnectionModels(
            connection_id=grant_id, provider_scope=preset.id, source_kind="http",
            freshness="fresh", owner_filtered=contract.account_filtered,
            executor_tools=agent_codec_for(contract.inference_protocol) is not None, models=(),
        ))
        order = order_models(
            Catalog(owner, uid, (connection,)),
            ModelPolicy(generation=0, mode="automatic", fallbacks=(), cost_caps=None),
            replace(contract.text_interaction, needs_tools=True),
            owner_id=owner, universe_id=uid,
        )
        if not order.candidates:
            raise HostedAuthError("no_eligible_free_agent_model", 409)
        current()
        registered = connect_compute(universe_id=uid, payload={
            "access_method": "api_key_http", "protocol": contract.inference_protocol,
            "model": order.candidates[0].ref.model_id, "ref": grant_id, "visibility": "private",
        })
        if registered.get("status") != "registered":
            raise HostedAuthError("model_candidate_registration_incomplete", 409)
        did = registered["definition_id"]
    current()
    from tinyassets.broker.capabilities import capability_operation
    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.storage.outbound_connections import GrantResolutionError

    try:
        resource = granted_resource_row(base, principal=owner, command_center=uid,
                                        grant_id=grant_id)
        existing = capability_operation(
            base, principal=owner, command_center=uid, grant_id=grant_id,
            connection_id=resource["connection_id"], capability_kind="model_discovery")
    except GrantResolutionError:
        raise HostedAuthError("model_connection_requires_recovery", 409) from None
    descriptor = {"protocol": preset.id, "catalogue_url": preset.catalogue_url,
                  "benchmark_url": preset.benchmark_url}
    if existing is not None and existing.descriptor() != descriptor:
        raise HostedAuthError("model_discovery_requires_review", 409)
    if existing is not None:
        return did
    configured = configure_provider_capability(universe_id=uid, payload={
        "capability_kind": "model_discovery", "definition_id": did, "enabled": True,
        "descriptor": descriptor,
    })
    if configured.get("status") != "configured":
        raise HostedAuthError("model_discovery_configuration_incomplete", 409)
    return did
