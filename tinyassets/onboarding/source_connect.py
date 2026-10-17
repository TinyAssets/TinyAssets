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
    proposed = (ModelAccess("explicit", tuple(models), None)
                if models is not None else ModelAccess("discovered"))
    access.setdefault(definition_id, proposed.document())
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
        "uses": {"model": {"wire": preset["wire"], "billing": "free",
                           "models": _installed_models(preset)}},
        "oauth": {"scopes": list(preset["sign_in"]["scopes"]),
                  "client_id": sign_in_client_id(preset, public_resource)},
    }


def _installed_models(preset):
    return [{"id": model, "tools": True, "context": 32768} for model in preset["models"]]


def _ask_body(preset):
    return " ".join((preset["offer"], preset["billing_note"],
                     "You approve at " + preset["sign_in"]["issuer_host"]
                     + "; there is no key to copy."))


def model_source(endpoints):
    """Recognize installed model data only when its full policy is granted."""
    from tinyassets.api.http_connection import _canonical_endpoint_set
    from tinyassets.onboarding.connection_definition import preset_definition
    from tinyassets.onboarding.hosted_model_auth import load_preset
    from tinyassets.providers.discovery_presets import bundled_discovery_documents
    from tinyassets.providers.free_sources import sign_in_cards, sign_in_preset, source_cards

    actual = _canonical_endpoint_set(endpoints)
    for ident in bundled_discovery_documents():
        try:
            preset = load_preset(ident)
        except HostedAuthError:
            continue
        if _canonical_endpoint_set(preset_definition(ident)["policy"]["endpoints"]) <= actual:
            return preset
    for preset in source_cards() + [sign_in_preset(p["id"]) for p in sign_in_cards()]:
        base = urlsplit(preset["base_url"])
        paths = [("/chat/completions", "POST")]
        if "sign_in" not in preset:
            paths.append(("/models", "GET"))
        required = [{"host": base.netloc, "path_template": base.path + path,
                     "methods": [method]}
                    for path, method in paths]
        if _canonical_endpoint_set(required) <= actual:
            return preset
    return None


def offer_connection_access(*, base, uid, owner, grant_id):
    """Protected owner action in the box; consumes no credential or flow state."""
    import json

    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.onboarding.hosted_model_auth import AcquisitionPreset
    from tinyassets.onboarding.serving import _gesture_lock, _require_current_admin
    from tinyassets.shared_self import require_founder_home

    with _gesture_lock(uid):
        require_founder_home(base, uid, owner)
        _require_current_admin(base, universe_id=uid, owner=owner)
        resource = granted_resource_row(base, principal=owner, command_center=uid,
                                       grant_id=grant_id)
        if resource.get("committed_via") != "door":
            raise PermissionError("model confirmation requires a door connection")
        preset = model_source(json.loads(resource["allowed_endpoints_json"]))
        if preset is None:
            raise HostedAuthError("unknown_model_connection", 404)
        if isinstance(preset, AcquisitionPreset):
            from tinyassets.onboarding.model_bootstrap_candidate import prepare_candidate

            definition_id = prepare_candidate(base=base, uid=uid, owner=owner,
                                              grant_id=grant_id, preset=preset)
            card = {"name": preset.display_name,
                    "offer": "Use the free models this account offers.",
                    "billing_note": "Only free models are approved."}
            models = None
        else:
            from tinyassets.api.connection_uses import _definition_for
            from tinyassets.broker.capabilities import capability_operation
            from tinyassets.providers.discovery_http import read_granted_discovery_document
            from tinyassets.providers.free_sources import discovered_agent_models

            if "sign_in" in preset:
                # Sign-in sources declare their models without granting a catalogue endpoint.
                entries = _installed_models(preset)
            else:
                payload = read_granted_discovery_document(
                    db_path=base / "outbound.db", grant_id=grant_id, owner_user_id=owner,
                    universe_id=uid, url=preset["base_url"].rstrip("/") + "/models")
                entries = discovered_agent_models(preset, payload)
            definition_id = _definition_for(uid, owner, grant_id, preset["wire"],
                                            entries[0]["id"]).id
            descriptor = {"wire": preset["wire"], "models": entries, "billing": "free"}
            args = dict(principal=owner, command_center=uid, grant_id=grant_id,
                        connection_id=resource["connection_id"], capability_kind="model_use")
            existing = capability_operation(base, **args)
            if existing is not None and existing.descriptor() != descriptor:
                raise HostedAuthError("model_candidate_requires_review", 409)
            if existing is None:
                capability_operation(base, **args, action="configure", enabled=True,
                                     descriptor=descriptor)
            card, models = preset, [entry["id"] for entry in entries]
        return _offer_pool_access(base=base, uid=uid, owner=owner, preset=card,
                                  definition_id=definition_id, models=models)
