"""Connect-card composition: owned key, granted /models, explicit model consent.

Two ways in: a pasted key (:func:`connect_source`) and signing in
(:func:`raise_sign_in_ask`, answered by the generic OAuth flow, then
:func:`offer_signed_in_source`). Both end in the same explicit model-access
confirmation (:func:`_offer_pool_access`).
"""

import os
from urllib.parse import urlsplit

from tinyassets.onboarding.hosted_model_auth import HostedAuthError

#: The public Client ID Metadata Document a sign-in source names as its client
#: id when no registered client is configured (``onboarding.handle_client_metadata``).
CLIENT_METADATA_PATH = "/app/oauth/client-metadata.json"
_PAID_USAGE_NOTE = ("Use a free/trial account with paid usage disabled. "
                    "TinyAssets cannot enforce the provider's billing settings.")


def connect_source(*, base, uid, owner, preset, key):
    from tinyassets.api.connection_uses import apply_connection_uses
    from tinyassets.api.http_connection import connect_http
    from tinyassets.onboarding.serving import _gesture_lock
    from tinyassets.providers.discovery_http import read_granted_discovery_document
    from tinyassets.providers.free_sources import discovered_agent_models
    from tinyassets.shared_self import require_founder_home

    with _gesture_lock(uid):
        require_founder_home(base, uid, owner)
        urls = [(preset["base_url"] + "/chat/completions", "POST"),
                (preset["base_url"] + "/models", "GET")]
        endpoints = [{"host": urlsplit(url).netloc, "path_template": urlsplit(url).path,
                      "methods": [method]} for url, method in urls]
        # This is the same deposit/authority path as every connect card. No
        # bearer token ever enters a provider definition or a browser response.
        deposit = connect_http(universe_id=uid, payload={
            "destination": "model-" + preset["id"], "secret": key,
            "auth_scheme": "bearer", "allowed_endpoints": endpoints, "access": "exact",
        })
        if deposit.get("status") != "provisioned":
            raise HostedAuthError("model_connection_deposit_incomplete", 409)
        payload = read_granted_discovery_document(
            db_path=base / "outbound.db", grant_id=deposit["grant_id"],
            owner_user_id=owner, universe_id=uid, url=urls[1][0],
        )
        models = discovered_agent_models(preset, payload)
        require_founder_home(base, uid, owner)
        applied = apply_connection_uses(
            base=base, uid=uid, actor=owner, grant_id=deposit["grant_id"],
            uses={"model": {"wire": preset["wire"], "billing": "free", "models": models}},
            constant_headers={}, owner_confirmed=True,
        )
        if applied.get("error") or not applied.get("provider"):
            raise HostedAuthError("model_connection_requires_recovery", 409)
        return _offer_pool_access(base=base, uid=uid, owner=owner, preset=preset,
                                  definition_id=applied["definition_id"],
                                  models=[m["id"] for m in models])


def _offer_pool_access(*, base, uid, owner, preset, definition_id, models):
    """Raise the owner's explicit confirmation adding this source to their agent.

    Caller holds the gesture lock and has checked the founder home.
    """
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.custom_agents import list_bindings
    from tinyassets.onboarding.model_bootstrap_binding import ensure_bootstrap_binding
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess

    assignment = load_provider_assignment(base, universe_id=uid)
    if assignment is not None and assignment.owner_user_id != owner:
        raise PermissionError("owned assignment required")
    if assignment is not None:
        matches = [b for b in list_bindings(base, universe_id=uid, limit=100)
                   if b["created_by"] == owner
                   and b["configuration"].get("provider_ref") == assignment.binding_id]
        if len(matches) != 1:
            raise PermissionError("one owned serving agent required")
        binding = matches[0]
    else:
        binding = ensure_bootstrap_binding(base, uid=uid, owner=owner)
    if binding is None or binding["created_by"] != owner:
        raise PermissionError("owned agent required")
    access = ({m.provider.removeprefix("api_key_http:"): m.access.document()
               for m in assignment.candidates}
              if assignment is not None else {})
    access[definition_id] = (ModelAccess("explicit", tuple(models), None)
                             if models is not None else ModelAccess()).document()
    root = (assignment.provider.removeprefix("api_key_http:") if assignment is not None
            else definition_id)
    result = request_from_user(universe_id=uid, origin="platform", payload={
        "kind": "LLM", "title": "Use your " + preset["name"] + " models",
        "body": " ".join((preset["offer"], preset.get("billing_note", _PAID_USAGE_NOTE),
                          "Your other accepted sources and spending ceilings stay the same.")),
        "fields": [], "action": {
            "type": "bind_model_access", "agent_binding_id": binding["agent_binding_id"],
            "expected_revision": binding["revision"], "provider": root,
            "model_access": access,
        },
    })
    if not result.get("request_id") or result.get("error"):
        raise HostedAuthError("model_confirmation_requires_review", 409)
    return {"status": "confirmation_required", "request_id": result["request_id"],
            "request": result, "universe_id": uid}


def offer_subscription_source(*, base, uid, owner, service):
    """Offer a deposited subscription missing from the accepted model setup.

    Deposit is not model consent. Existing membership, caps and root stay intact
    until the owner answers the ordinary bind_model_access request.
    """
    from tinyassets.onboarding import DEVICE_SIGN_IN_SERVICE
    from tinyassets.onboarding.serving import _gesture_lock
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.providers.free_sources import subscription_cards
    from tinyassets.shared_self import require_founder_home

    if service != DEVICE_SIGN_IN_SERVICE:
        return None
    with _gesture_lock(uid):
        require_founder_home(base, uid, owner)
        assignment = load_provider_assignment(base, universe_id=uid)
        if (assignment is None or not assignment.manifest_digest
                or assignment.state == "unassigned"
                or service in {member.provider for member in assignment.candidates}):
            return None
        cards = subscription_cards()
        if len(cards) != 1:
            raise HostedAuthError("model_confirmation_requires_review", 409)
        card = cards[0]
        return _offer_pool_access(
            base=base, uid=uid, owner=owner, definition_id=service, models=None,
            preset={"name": card["name"], "offer": card["note"],
                    "billing_note": (
                        "Uses the subscription you connected; no purchase is approved.")},
        )


def sign_in_client_id(preset, public_resource):
    """The OAuth client id: a configured registered client, else our metadata document."""
    from tinyassets.connection_oauth.flow import callback_origin

    configured = os.environ.get(preset["sign_in"].get("client_id_env") or "", "").strip()
    return configured or callback_origin(public_resource) + CLIENT_METADATA_PATH


def sign_in_action(preset, public_resource):
    """The ``connect`` ask for a sign-in source, built ONLY from installed data."""
    base = urlsplit(preset["base_url"])
    return {
        "type": "connect", "destination": "model-" + preset["id"], "auth_scheme": "bearer",
        "endpoints": [{"host": base.netloc, "path_template": base.path + "/chat/completions",
                       "methods": ["POST"]}],
        "access": "exact",
        "uses": {"model": {"wire": preset["wire"], "billing": "free", "models": [
            {"id": model, "tools": True, "context": 32768} for model in preset["models"]]}},
        "oauth": {"scopes": list(preset["sign_in"]["scopes"]),
                  "client_id": sign_in_client_id(preset, public_resource)},
    }


def _ask_body(preset):
    return " ".join((preset["offer"], preset["billing_note"],
                     "You approve at " + preset["sign_in"]["issuer_host"]
                     + "; there is no key to copy."))


def raise_sign_in_ask(*, uid, preset, public_resource):
    """The platform's own ``connect`` ask for a sign-in source; returns the row.

    The discovery root (the issuer host) is passed server-side, so an agent
    raising the same payload gets no sign-in offer and no row.
    """
    from tinyassets.api.pending_requests import request_from_user

    result = request_from_user(
        universe_id=uid, origin="platform",
        sign_in_hosts=(preset["sign_in"]["issuer_host"],),
        payload={"kind": "LLM", "title": "Use your " + preset["name"] + " models",
                 "body": _ask_body(preset), "fields": [],
                 "action": sign_in_action(preset, public_resource)},
    )
    if result.get("error") or not result.get("request_id"):
        raise HostedAuthError("sign_in_unavailable", 503)
    if not (isinstance(result.get("action"), dict) and result["action"].get("oauth")):
        raise HostedAuthError("sign_in_unavailable", 503)
    return result


def is_canonical_sign_in(row, preset, public_resource):
    """Whether a redeemed request is the platform's own ask for this source.

    Destination alone is agent-controllable; this compares the stored action
    with the one rebuilt from installed data, the offer's issuer and origin.
    """
    from tinyassets.api.pending_requests import _validated_action

    if not isinstance(row, dict) or row.get("origin") != "platform":
        return False
    expected = _validated_action(sign_in_action(preset, public_resource))
    requested = expected.pop("oauth_request")
    stored = row.get("action") or {}
    offer = stored.get("oauth") or {}
    return (all(stored.get(key) == value for key, value in expected.items())
            and offer.get("source") == "discovered"
            and offer.get("scopes") == requested.get("scopes")
            and offer.get("client_id") == requested.get("client_id")
            and offer.get("issuer") == "https://" + preset["sign_in"]["issuer_host"])


def offer_signed_in_source(*, base, uid, owner, request_id, completed, public_resource):
    """After a sign-in answered the platform's ask: offer the source to the agent.

    Returns the confirmation, or None when there is nothing to offer (not the
    platform's canonical ask, or it already serves an unpowered command center).
    """
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.onboarding.serving import _gesture_lock
    from tinyassets.providers.free_sources import sign_in_preset
    from tinyassets.shared_self import require_founder_home
    from tinyassets.storage.pending_requests import get_request

    destination = str(completed.get("destination") or "")
    preset = sign_in_preset(destination.removeprefix("model-"))
    if (preset is None or not completed.get("definition_id")
            or (completed.get("serving") or {}).get("status") != "unchanged"):
        return None
    row = get_request(_universe_dir(uid), request_id)
    if not is_canonical_sign_in(row, preset, public_resource):
        return None
    with _gesture_lock(uid):
        require_founder_home(base, uid, owner)
        return _offer_pool_access(base=base, uid=uid, owner=owner, preset=preset,
                                  definition_id=completed["definition_id"],
                                  models=list(preset["models"]))
