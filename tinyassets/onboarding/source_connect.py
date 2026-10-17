"""Source definitions and owner-side model-access composition."""

import os
from urllib.parse import urlsplit

from tinyassets.onboarding.hosted_model_auth import HostedAuthError

#: The public Client ID Metadata Document a sign-in source names as its client
#: id when no registered client is configured (``onboarding.handle_client_metadata``).
CLIENT_METADATA_PATH = "/app/oauth/client-metadata.json"
_PAID_USAGE_NOTE = ("Use a free/trial account with paid usage disabled. "
                    "TinyAssets cannot enforce the provider's billing settings.")


def _offer_pool_access(*, base, uid, owner, preset, definition_id, models):
    """Raise the owner's explicit confirmation adding this source to their agent.

    Caller holds the gesture lock and has checked the founder home.
    """
    from tinyassets.api.pending_requests import request_from_user
    from tinyassets.commons_catalog import list_bindings
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
