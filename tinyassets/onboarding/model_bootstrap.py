"""Owner-side display of an existing model confirmation, without credential custody."""

from pathlib import Path

from tinyassets.onboarding.hosted_model_auth import (
    AcquisitionPreset,
    HostedAuthError,
)


def _pending_confirmation(base: Path, *, universe: Path, uid: str, owner: str,
                          preset: AcquisitionPreset) -> dict | None:
    """Redisplay this owner's existing free-only request after partial activation.

    Read-only: never redeposit (which could rotate custody), recreate consent or
    relax the existing answer action's revision/home/authority checks. Ambiguity
    holds instead of picking a request. Revoked access may be shown for recovery;
    only the existing answer path decides whether it can actually activate.
    """
    from tinyassets.api.model_access_requests import grant_sentence
    from tinyassets.broker.ledger_queries import BOOTSTRAP_RECOVERY, query_ledger
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage.outbound_connections import ProxyRequestError
    from tinyassets.storage.pending_requests import list_pending

    matches = []
    for request in list_pending(universe):
        action = request.get("action") or {}
        if action.get("type") != "bind_model_access":
            continue
        did = action.get("provider", "")
        access = action.get("model_access")
        if access != {did: ModelAccess("discovered").document()}:
            continue
        definition = get_definition(uid, did)
        if (definition is None or definition.owner_user_id != owner
                or definition.access_method != "api_key_http"):
            continue
        facts = query_ledger(base, query=BOOTSTRAP_RECOVERY, principal=owner,
                             command_center=uid, grant_id=definition.ref)
        if not isinstance(facts, dict) or set(facts) != {"recovery"}:
            raise ProxyRequestError("invalid bootstrap recovery projection")
        recovery = facts["recovery"]
        if recovery is None:
            continue
        if (not isinstance(recovery, dict)
                or set(recovery) != {"destination", "descriptor"}
                or not isinstance(recovery["destination"], str)
                or recovery["descriptor"] is not None
                and not isinstance(recovery["descriptor"], dict)):
            raise ProxyRequestError("invalid bootstrap recovery projection")
        if (recovery["destination"] == "model:" + preset.id
                and recovery["descriptor"] == {"protocol": preset.id,
                    "catalogue_url": preset.catalogue_url,
                    "benchmark_url": preset.benchmark_url}):
            matches.append({**request, "grant_sentence": grant_sentence(action)})
    if len(matches) > 1:
        raise HostedAuthError("model_confirmation_requires_review", 409)
    if not matches:
        return None
    return {"status": "confirmation_required", "universe_id": uid,
            "request_id": matches[0]["request_id"], "request": matches[0]}
