"""Owner-scoped bootstrap composition over existing vault, grants and consent."""

from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from tinyassets.onboarding.hosted_model_auth import AcquisitionPreset, HostedAuthError


def endpoint_policy(preset: AcquisitionPreset) -> list[dict]:
    """Exact installed endpoints, never catalogue-provided URLs or wildcard scope."""
    import re

    endpoints = []
    for url, method in ((preset.inference_url, "POST"), (preset.catalogue_url, "GET"),
                        (preset.benchmark_url, "GET")):
        parts = urlsplit(url)
        endpoint = {"host": parts.netloc, "path_template": parts.path, "methods": [method]}
        query = parse_qs(parts.query, strict_parsing=True)
        if query:
            if any(len(values) != 1 for values in query.values()):
                raise HostedAuthError("invalid_acquisition_preset", 503)
            endpoint.update(allowed_query=sorted(query), required_query=sorted(query),
                            query_patterns={key: "^" + re.escape(value[0]) + "$"
                                            for key, value in query.items()})
        endpoints.append(endpoint)
    return endpoints


def _resume_key(base: Path, *, universe: Path, uid: str, owner: str, destination: str) -> str:
    """Recover only a current owner's existing deposit, including a partial write.

    Not a second secret store or a browser disclosure. Existing deposit ownership
    precedes vault visibility; no unowned/legacy record may be adopted here.
    """
    from tinyassets.credential_vault import load_credential_vault
    from tinyassets.storage.current_home import check_current_home
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
        conn.execute("BEGIN")
        check_current_home(conn, owner, uid)
        if conn.execute("SELECT 1 FROM sqlite_master WHERE type = 'table' "
                        "AND name = 'llm_credential_deposit_owners'").fetchone() is None:
            raise HostedAuthError("model_authorization_required", 409)
        recorded = conn.execute(
            "SELECT owner_user_id FROM llm_credential_deposit_owners "
            "WHERE universe_id = ? AND service = ?", (uid, "http:" + destination),
        ).fetchone()
        if recorded is None or recorded[0] != owner:
            raise HostedAuthError("model_authorization_required", 409)
        records = [r for r in load_credential_vault(universe)
                   if r.get("credential_type") == "http" and r.get("service") == destination]
        if len(records) != 1 or not isinstance(records[0].get("token"), str):
            raise HostedAuthError("model_authorization_required", 409)
        return records[0]["token"]


def complete_bootstrap(*, base: Path, uid: str, owner: str, preset: AcquisitionPreset,
                       key: str | None = None) -> dict:
    """Deposit/resume setup; return an unanswered model request, never enable.

    With a new OAuth key the current setup must still be empty under the same
    per-universe gesture lock used by other onboarding connections. Network code
    exchange already completed outside that lock. A changed setup holds rather
    than overwriting; the vendor may have created a key which the owner can manage.
    """
    from tinyassets.api.http_connection import connect_http
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.onboarding.model_bootstrap_binding import (
        ensure_bootstrap_binding,
        reconnect_owned_binding,
    )
    from tinyassets.onboarding.model_bootstrap_candidate import prepare_candidate
    from tinyassets.onboarding.model_setup import model_setup_state
    from tinyassets.onboarding.serving import _gesture_lock
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.shared_self import require_founder_home

    destination = "model:" + preset.id
    with _gesture_lock(uid):
        universe = require_founder_home(base, uid, owner)
        state = model_setup_state(base, universe=universe, uid=uid, owner=owner)
        if key is not None and state not in {"empty", "disconnected"}:
            raise HostedAuthError("model_setup_changed", 409)
        if state == "connected":
            return {"status": "connected", "universe_id": uid}
        if key is None:
            pending = _pending_confirmation(base, universe=universe, uid=uid,
                                            owner=owner, preset=preset)
            if pending is not None:
                return pending
        assignment = load_provider_assignment(base, universe_id=uid)
        reconnecting = (assignment is not None and assignment.state == "unassigned"
                        and assignment.owner_user_id == owner)
        if assignment is not None and not reconnecting:
            raise HostedAuthError("model_connection_requires_recovery", 409)
        if key is None:
            key = _resume_key(base, universe=universe, uid=uid, owner=owner,
                              destination=destination)
        provisioned = connect_http(universe_id=uid, payload={
            "destination": destination, "secret": key, "auth_scheme": "bearer",
            "allowed_endpoints": endpoint_policy(preset), "access": "exact",
        })
        if provisioned.get("status") != "provisioned":
            raise HostedAuthError("model_connection_deposit_incomplete", 409)
    # All following steps are inert and resumable through deterministic existing
    # stores. Do not hold the gesture lock while calling its first-binding helper.
    did = prepare_candidate(base=base, uid=uid, owner=owner,
                            grant_id=provisioned["grant_id"], preset=preset)
    binding = (reconnect_owned_binding(base, uid=uid, owner=owner) if reconnecting
               else ensure_bootstrap_binding(base, uid=uid, owner=owner))
    require_founder_home(base, uid, owner)
    # The existing public binding action accepts the registered definition id;
    # capture_action normalizes it into the manifest's api_key_http identity.
    provider = did
    # Raised by the platform, not by the agent, so the agent cannot withdraw it.
    request = request_from_user(universe_id=uid, origin="platform", payload={
        "kind": "Models", "title": "Power your command center with free models",
        "body": (f"Use eligible free models from your {preset.display_name} account. "
                 "No paid-model access or credit purchase is approved. Your source's privacy "
                 "settings and free limits still apply. You can connect another LLM later."),
        "fields": [], "action": {
            "type": "bind_model_access", "agent_binding_id": binding["agent_binding_id"],
            "expected_revision": binding["revision"], "provider": provider,
            "model_access": {provider: ModelAccess("discovered").document()},
        },
    })
    if request.get("error") or not request.get("request_id"):
        raise HostedAuthError("model_confirmation_requires_review", 409)
    return {"status": "confirmation_required", "universe_id": uid,
            "request_id": request["request_id"], "request": request}


def _pending_confirmation(base: Path, *, universe: Path, uid: str, owner: str,
                          preset: AcquisitionPreset) -> dict | None:
    """Redisplay this owner's existing free-only request after partial activation.

    Read-only: never redeposit (which could rotate custody), recreate consent or
    relax the existing answer action's revision/home/authority checks. Ambiguity
    holds instead of picking a request. Revoked access may be shown for recovery;
    only the existing answer path decides whether it can actually activate.
    """
    from tinyassets.api.model_access_requests import grant_sentence
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage.outbound_connections import ConnectionLedger
    from tinyassets.storage.pending_requests import list_pending

    matches = []
    ledger = ConnectionLedger(base / "outbound.db")
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
        grant = ledger.get_grant(definition.ref)
        if grant is None or grant.owner_user_id != owner or grant.universe_id != uid:
            continue
        connection = ledger.get_connection(grant.connection_id)
        profile = ledger.get_connection_capability(grant.connection_id, "model_discovery")
        if (connection is None or connection.owner_user_id != owner
                or connection.destination != "model:" + preset.id or profile is None
                or profile.descriptor() != {"protocol": preset.id,
                    "catalogue_url": preset.catalogue_url, "benchmark_url": preset.benchmark_url}):
            continue
        matches.append({**request, "grant_sentence": grant_sentence(action)})
    if len(matches) > 1:
        raise HostedAuthError("model_confirmation_requires_review", 409)
    if not matches:
        return None
    return {"status": "confirmation_required", "universe_id": uid,
            "request_id": matches[0]["request_id"], "request": matches[0]}
