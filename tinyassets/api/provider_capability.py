"""Configure non-secret capabilities on existing owned provider connections."""

from __future__ import annotations

import json
from typing import Any

_NOT_FOUND: dict[str, Any] = {"error": "not_found", "resource": "connection"}


def _payload(value: Any) -> dict[str, Any]:
    document = json.loads(value) if isinstance(value, str) else value
    if not isinstance(document, dict):
        raise ValueError("payload_json must be a JSON object")
    return document


def configure_provider_capability(
    *, universe_id: str = "", payload: Any = None
) -> dict[str, Any]:
    """Declare or revoke capability metadata without enlarging connection grants.

    Voice uses the current serving provider. Discovery uses a verified owned
    definition and works unpowered. Neither accepts a connection/grant identity
    as a substitute for the server's authenticated owner/universe resolution.
    """

    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path, _universe_dir
    from tinyassets.broker.capabilities import capability_operation
    from tinyassets.broker.ledger_queries import authorized_connection
    from tinyassets.daemon_server import get_founder_home, list_universe_acl
    from tinyassets.principals import named_principal
    from tinyassets.provider_serving_binding import (
        resolve_current_serving_provider_authority,
    )
    from tinyassets.storage.outbound_connections import GrantResolutionError, SsrfValidationError

    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}

    # This kind must work without a current serving assignment. Keep legacy
    # voice's home/serving resolution and error precedence unchanged.
    try:
        discovery_document = _payload(payload)
    except (TypeError, ValueError):
        discovery_document = {}
    if discovery_document.get("capability_kind") == "model_discovery":
        return _configure_model_discovery(
            universe_id=universe_id, actor=actor, document=discovery_document
        )

    base = _base_path()
    home = get_founder_home(base, actor) or ""
    if not home or not (base / home).is_dir():
        return {"error": "no_home_universe", "resource": "connection"}
    requested = (universe_id or "").strip()
    if requested and requested != home:
        return dict(_NOT_FOUND)
    if not any(
        row.get("actor_id") == actor and row.get("permission") == "admin"
        for row in list_universe_acl(base, universe_id=home)
    ):
        return dict(_NOT_FOUND)

    try:
        document = _payload(payload)
    except (TypeError, ValueError) as exc:
        return {"error": "provider_capability_invalid", "detail": str(exc)}
    enabled = document.get("enabled")
    expected_fields = (
        {"capability_kind", "enabled", "descriptor"}
        if enabled is True
        else {"capability_kind", "enabled"}
    )
    if not isinstance(enabled, bool) or set(document) != expected_fields:
        return {
            "error": "provider_capability_invalid",
            "detail": "payload fields do not match the capability operation",
        }

    try:
        authority = resolve_current_serving_provider_authority(
            base,
            universe_dir=_universe_dir(home),
            universe_id=home,
            owner_user_id=actor,
        )
    except (LookupError, PermissionError, RuntimeError, ValueError):
        return {
            "error": "provider_not_configured",
            "resource": "provider_capability",
        }
    if authority.access_method != "api_key_http":
        return {
            "error": "provider_voice_unsupported",
            "resource": "provider_capability",
        }

    try:
        authorized_connection(base, principal=actor, command_center=home,
                              grant_id=authority.grant_id, connection_id=authority.connection_id)
    except GrantResolutionError:
        return dict(_NOT_FOUND)
    try:
        capability = capability_operation(
            base, principal=actor, command_center=home, grant_id=authority.grant_id,
            action="configure",
            connection_id=authority.connection_id,
            capability_kind=document.get("capability_kind"),
            descriptor=document.get("descriptor"),
            enabled=enabled,
        )
    except GrantResolutionError:
        return dict(_NOT_FOUND)
    except (LookupError, PermissionError, SsrfValidationError, ValueError) as exc:
        return {"error": "provider_capability_invalid", "detail": str(exc)}
    response: dict[str, Any] = {
        "status": "configured" if enabled else "revoked",
        "capability_kind": document["capability_kind"],
        "provider": authority.provider,
    }
    if capability is not None:
        response["descriptor"] = capability.descriptor()
    return response


def _configure_model_discovery(
    *, universe_id: str, actor: str, document: dict[str, Any]
) -> dict[str, Any]:
    """Use an owned verified definition, never require a powered serving LLM."""
    from tinyassets.api.compute_connection import _validate_http_grant
    from tinyassets.api.helpers import _base_path, _request_universe
    from tinyassets.broker.capabilities import capability_operation
    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.daemon_server import list_universe_acl
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage.outbound_connections import GrantResolutionError, SsrfValidationError

    base = _base_path()
    uid = _request_universe(universe_id)
    if not any(
        row.get("actor_id") == actor and row.get("permission") == "admin"
        for row in list_universe_acl(base, universe_id=uid)
    ):
        return dict(_NOT_FOUND)
    enabled = document.get("enabled")
    expected_fields = {"capability_kind", "definition_id", "enabled"}
    preview = document.get("preview", False)
    if enabled is True:
        expected_fields.add("descriptor")
        if "preview" in document:
            expected_fields.add("preview")
    if (not isinstance(enabled, bool) or type(preview) is not bool
            or set(document) != expected_fields):
        return {
            "error": "provider_capability_invalid",
            "detail": "payload fields do not match the discovery operation",
        }
    definition_id = document.get("definition_id")
    if not isinstance(definition_id, str) or not definition_id or len(definition_id) > 200:
        return {"error": "provider_capability_invalid", "detail": "definition_id is invalid"}
    try:
        definition = get_definition(uid, definition_id)
    except (LookupError, OSError, TypeError, ValueError):
        return dict(_NOT_FOUND)
    if definition is None or definition.owner_user_id != actor:
        return dict(_NOT_FOUND)
    if definition.access_method != "api_key_http":
        return {"error": "provider_discovery_unsupported", "resource": "provider_capability"}
    gate = _validate_http_grant(base=base, universe_id=uid, actor=actor, grant_id=definition.ref)
    if gate is not None:
        return gate
    try:
        row = granted_resource_row(base, principal=actor, command_center=uid,
                                   grant_id=definition.ref)
        capability = capability_operation(
            base, principal=actor, command_center=uid, grant_id=definition.ref,
            action="configure", connection_id=row["connection_id"],
            capability_kind="model_discovery",
            descriptor=document.get("descriptor"),
            enabled=enabled,
            preview=preview,
        )
    except (LookupError, PermissionError, GrantResolutionError):
        return dict(_NOT_FOUND)
    except (SsrfValidationError, ValueError):
        return {
            "error": "provider_capability_invalid",
            "detail": "discovery descriptor is invalid or its URLs are not permitted",
        }
    response: dict[str, Any] = {
        "status": "preview" if preview else ("configured" if enabled else "revoked"),
        "capability_kind": "model_discovery",
        "provider": f"api_key_http:{definition.id}",
        "scope": "connection",
    }
    if capability is not None:
        response["descriptor"] = capability.descriptor()
        if capability.contract_json:
            import hashlib

            response["descriptor_digest"] = hashlib.sha256(json.dumps(
                capability.descriptor(), sort_keys=True, separators=(",", ":"),
                allow_nan=False,
            ).encode()).hexdigest()
            response["source_semantics"] = {
                "basis": "owner_configured_contract",
                "independently_verified": False,
                "grants_inference": False,
                "grants_spending": False,
                "price_bound_basis": "source_request_caps",
                "scope": "connection",
                "notice": "Availability, privacy and charging behavior are declared by the "
                          "configured source, not independently verified by TinyAssets. "
                          "Configuration alone does not establish agent readiness.",
            }
    return response


__all__ = ["configure_provider_capability"]
