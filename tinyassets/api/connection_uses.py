"""What a connection is USED for: one connector for platforms and for models.

A universe connection is one object whatever it reaches. ``uses.call`` means
workflow nodes may call it (``authenticated_external_call``); ``uses.model``
means the universe may run its model on it, and says how:

    uses.model = {"wire": "<bundled dialect>", "models": [{"id", "tools", "context"}],
                  "billing": "free" | "flat"}

``constant_headers`` are non-secret headers the broker adds to every call on the
connection, so a node never retypes a service's version header.

Nothing here names a vendor. A provider nobody has written code for connects by
naming the wire structure it speaks and the models it serves. The secret is
never here either: it reaches the vault only through the request rail
(``connect`` / ``connect_http``), and these functions read and write non-secret
metadata on a connection the caller already owns.

Two entry points share one writer:

* :func:`apply_connection_uses` - called when the owner answers a ``connect``
  request, right after the deposit, so connection + grant + model use land in
  one answer;
* :func:`configure_connection` - ``write_graph target=connection
  operation=configure``, for the owner's agent to edit those fields later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

_NOT_FOUND: dict[str, Any] = {"error": "not_found", "resource": "connection"}
_USE_KINDS = frozenset({"call", "model"})


class ConnectionUseError(ValueError):
    """A ``uses`` / ``constant_headers`` declaration the platform cannot honour."""


def validate_uses(raw: Any) -> dict[str, Any]:
    """Normalize ``uses``. Absent means ``{"call": {}}``: a plain platform connection."""
    from tinyassets.storage.outbound_connections import _validate_model_use_capability

    if raw is None:
        return {"call": {}}
    if not isinstance(raw, dict) or not raw or set(raw) - _USE_KINDS:
        raise ConnectionUseError('uses must be an object with "call" and/or "model"')
    uses: dict[str, Any] = {}
    if "call" in raw:
        if raw["call"] not in ({}, None, True):
            raise ConnectionUseError('uses.call takes no settings; send {"call": {}}')
        uses["call"] = {}
    if "model" in raw:
        try:
            capability = _validate_model_use_capability("request", raw["model"])
        except ValueError as exc:
            raise ConnectionUseError(f"uses.model: {exc}") from None
        uses["model"] = capability.descriptor()
    return uses


def validate_constant_headers(raw: Any) -> dict[str, str]:
    from tinyassets.storage.outbound_connections import _validate_constant_headers_capability

    if raw in (None, {}):
        return {}
    try:
        return dict(_validate_constant_headers_capability("request", {"headers": raw}).headers)
    except ValueError as exc:
        raise ConnectionUseError(f"constant_headers: {exc}") from None


def _definition_for(uid: str, actor: str, grant_id: str, wire: str, first_model: str):
    """Reuse this grant's registered model source in the same wire, else register one.

    Registration creates a candidate only. The model on the definition is an
    inert seed; which models may serve comes from the model use and the
    owner's accepted access, never from this field.
    """
    from tinyassets.providers import definition as pd
    from tinyassets.providers.wire_dialects import same_dialect

    existing = sorted(
        (d for d in pd.list_definitions(uid)
         if d.ref == grant_id and d.owner_user_id == actor
         and d.access_method == "api_key_http" and same_dialect(d.protocol, wire)),
        key=lambda d: (d.created_at, d.id),
    )
    if existing:
        return existing[0]
    return pd.register_definition(
        universe_id=uid, owner_user_id=actor, access_method="api_key_http",
        protocol=wire, model=first_model, ref=grant_id,
    )


#: Money floor (review 2026-09-24): what a declared model use may NOT do.
MODEL_USE_NEEDS_OWNER = (
    "declaring or changing a model use (its models or billing) needs the owner's "
    "confirmation: raise a connect ask with uses.model instead; configure only "
    "edits constant headers"
)
NON_FREE_ACCESS_CONFLICT = (
    "the owner accepted spending on this connection's models; a declared "
    "free/flat model list cannot replace those spend limits"
)


def model_use_refusal(
    *, base: Path, uid: str, actor: str, connection_id: str, grant_id: str,
) -> dict[str, Any] | None:
    """Why a declared model use may not describe this connection, or None.

    A declared list's billing is the requester's word. It may only describe a
    connection with no priced source: no ``model_discovery`` catalogue on the
    connection, and no accepted access with spending caps on a model source
    registered for its grant. Unreadable state refuses (fail closed).
    """
    from tinyassets.broker.ledger_queries import HAS_PRICED_SOURCE, query_ledger
    from tinyassets.providers.definition import list_definitions
    from tinyassets.storage.outbound_connections import (
        MODEL_USE_PRICED_CONFLICT,
    )

    try:
        facts = query_ledger(Path(base), query=HAS_PRICED_SOURCE, principal=actor,
                             command_center=uid, grant_id=grant_id, connection_id=connection_id)
        if type(facts.get("priced")) is not bool:
            raise ValueError("invalid priced-source response")
        if facts["priced"]:
            return {"error": "connection_setup_invalid", "detail": MODEL_USE_PRICED_CONFLICT}
        sources = {
            f"api_key_http:{d.id}" for d in list_definitions(uid)
            if d.ref == grant_id and d.owner_user_id == actor
        }
        if sources:
            from tinyassets.provider_assignment import load_provider_assignment

            assignment = load_provider_assignment(Path(base), universe_id=uid)
            members = () if assignment is None else assignment.candidates
            if any(m.provider in sources and m.access.cost_caps is not None
                   for m in members):
                return {"error": "connection_setup_invalid",
                        "detail": NON_FREE_ACCESS_CONFLICT}
    except Exception:  # noqa: BLE001 - money floor: unknown state refuses
        return {"error": "connection_setup_invalid",
                "detail": "could not confirm this connection has no priced source"}
    return None


def apply_connection_uses(
    *, base: Path, uid: str, actor: str, grant_id: str,
    uses: dict[str, Any], constant_headers: dict[str, str],
    owner_confirmed: bool = False,
) -> dict[str, Any]:
    """Write a connection's uses and constant headers. Idempotent.

    The grant must be a live http grant bound to ``uid`` and owned by
    ``actor``; anything else is the uniform not-found. Returns the projection
    the caller shows, including ``provider`` when a model use exists.

    ``owner_confirmed`` is True only on the owner's answer to a ``connect``
    ask. Without it (``configure``, the agent's own write) a model use may not
    be created or changed: its billing is a spend claim the owner must see.
    A declared model use is refused on any connection with a priced source.
    """
    from tinyassets.api.compute_connection import _validate_http_grant
    from tinyassets.broker.capabilities import capability_operation
    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.providers.definition import ProviderDefinitionError
    from tinyassets.storage.outbound_connections import GrantResolutionError, SsrfValidationError

    gate = _validate_http_grant(base=base, universe_id=uid, actor=actor, grant_id=grant_id)
    if gate is not None:
        return gate
    try:
        connection_id = granted_resource_row(
            base, principal=actor, command_center=uid, grant_id=grant_id)["connection_id"]
    except GrantResolutionError:
        return dict(_NOT_FOUND)

    def capability(capability_kind, **kwargs):
        return capability_operation(base, principal=actor, command_center=uid,
                                    grant_id=grant_id, connection_id=connection_id,
                                    capability_kind=capability_kind, **kwargs)
    model = uses.get("model")
    if model is not None:
        try:
            current = capability("model_use")
        except (PermissionError, GrantResolutionError):
            return dict(_NOT_FOUND)
        except (LookupError, ValueError):
            current = None
        if not owner_confirmed and (current is None or current.descriptor() != model):
            return {"error": "connection_setup_invalid", "detail": MODEL_USE_NEEDS_OWNER}
        refused = model_use_refusal(base=base, uid=uid, actor=actor,
                                    connection_id=connection_id, grant_id=grant_id)
        if refused is not None:
            return refused
    result: dict[str, Any] = {"connection_id": connection_id, "grant_id": grant_id,
                              "uses": sorted(uses)}
    try:
        if constant_headers:
            capability(
                "constant_headers", action="configure",
                descriptor={"headers": constant_headers}, enabled=True,
            )
            result["constant_headers"] = dict(constant_headers)
        if model is not None:
            definition = _definition_for(
                uid, actor, grant_id, model["wire"], model["models"][0]["id"],
            )
            capability(
                "model_use", action="configure",
                descriptor=model, enabled=True,
            )
            result.update({
                "definition_id": definition.id,
                "provider": f"api_key_http:{definition.id}",
                "model": model,
            })
    except (LookupError, PermissionError, GrantResolutionError):
        return dict(_NOT_FOUND)
    except (ProviderDefinitionError, SsrfValidationError, ValueError) as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}
    return result


def select_model_if_unpowered(
    *, base: Path, uid: str, actor: str, definition_id: str, model: dict[str, Any],
) -> dict[str, Any]:
    """If nothing powers this universe, serve it on this connection's models.

    Deterministic: the owner's universe is unpowered, and they just confirmed
    this exact model list on the request, so this connection becomes the
    serving source with explicit access to those models and no spending
    (free-only caps). A powered universe accepts the additional source while
    retaining its serving root and every existing member's limits.
    """
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.api.pending_requests import _serving_llm_bound
    from tinyassets.onboarding.serving import ensure_founder_serving
    from tinyassets.provider_assignment_manifest import ModelAccess

    if _serving_llm_bound(base, uid, actor):
        _accept_additional_model_source(
            base=base, uid=uid, actor=actor, definition_id=definition_id, model=model,
        )
        return {"status": "unchanged", "reason": "already_powered"}
    access = ModelAccess(
        "explicit", tuple(entry["id"] for entry in model["models"]), None,
    )
    return ensure_founder_serving(
        base_path=base, universe_dir=_universe_dir(uid), owner_user_id=actor,
        universe_id=uid, service=definition_id, model_access={definition_id: access},
    )


def _accept_additional_model_source(*, base, uid, actor, definition_id, model):
    """Complete the owner's model-use confirmation without replacing their root."""
    from tinyassets.custom_agents import serving_binding_candidates
    from tinyassets.exceptions import ProviderError
    from tinyassets.onboarding.serving import _gesture_lock, _require_current_admin
    from tinyassets.provider_assignment import load_provider_assignment
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving
    from tinyassets.shared_self import require_founder_home

    with _gesture_lock(uid):
        require_founder_home(base, uid, actor)
        _require_current_admin(base, universe_id=uid, owner=actor)
        assignment = load_provider_assignment(base, universe_id=uid)
        if (assignment is None or assignment.owner_user_id != actor
                or not assignment.manifest_digest):
            raise PermissionError("confirm model access for the existing legacy source first")
        bindings = serving_binding_candidates(base, universe_id=uid, owner_user_id=actor)
        if len(bindings) != 1:
            raise PermissionError("one owned serving agent required to accept model source")
        binding = bindings[0]
        access = {m.provider.removeprefix("api_key_http:"): m.access
                  for m in assignment.candidates}
        if definition_id in access:
            return  # Reconnecting never broadens the owner's existing limits.
        access[definition_id] = ModelAccess(
            "explicit", tuple(entry["id"] for entry in model["models"]), None,
        )
        bound = bind_serving_provider(
            base_path=base, universe_dir=base / uid, owner_user_id=actor, universe_id=uid,
            agent_binding_id=binding["agent_binding_id"], expected_revision=binding["revision"],
            expected_binding_updated_at=binding["updated_at"],
            provider=assignment.provider.removeprefix("api_key_http:"), model_access=access,
            expected_assignment_digest=assignment.assignment_digest, require_current_home=True,
        )["agent_binding"]
        if binding["status"] == "serving" and bound["status"] != "serving":
            published = load_provider_assignment(base, universe_id=uid)
            try:
                set_serving(
                    base_path=base, universe_dir=base / uid, owner_user_id=actor, universe_id=uid,
                    agent_binding_id=bound["agent_binding_id"], expected_revision=bound["revision"],
                    expected_binding_updated_at=bound["updated_at"],
                    enabled=True, expected_assignment_digest=published.assignment_digest,
                    require_current_home=True,
                )
            except (PermissionError, ValueError, LookupError, ProviderError, OSError):
                # Both fences must still match: recovery cannot overwrite an
                # intervening owner edit, revocation or another connection.
                try:
                    restored = bind_serving_provider(
                        base_path=base, universe_dir=base / uid, owner_user_id=actor,
                        universe_id=uid, agent_binding_id=bound["agent_binding_id"],
                        expected_revision=bound["revision"],
                        expected_binding_updated_at=bound["updated_at"],
                        provider=assignment.provider.removeprefix("api_key_http:"),
                        model_access={m.provider.removeprefix("api_key_http:"): m.access
                                      for m in assignment.candidates},
                        expected_assignment_digest=published.assignment_digest,
                        require_current_home=True,
                    )["agent_binding"]
                    prior = load_provider_assignment(base, universe_id=uid)
                    set_serving(
                        base_path=base, universe_dir=base / uid, owner_user_id=actor,
                        universe_id=uid, agent_binding_id=restored["agent_binding_id"],
                        expected_revision=restored["revision"], enabled=True,
                        expected_binding_updated_at=restored["updated_at"],
                        expected_assignment_digest=prior.assignment_digest,
                        require_current_home=True,
                    )
                except (PermissionError, ValueError, LookupError, ProviderError, OSError):
                    raise PermissionError(
                        "Model source acceptance and serving recovery failed; review model setup"
                    ) from None
                raise PermissionError(
                    "Model source acceptance failed; previous serving model setup restored"
                ) from None


def configure_connection(*, universe_id: str = "", payload: Any = None) -> dict[str, Any]:
    """``write_graph target=connection operation=configure``: edit non-secret fields.

    Payload: ``{"destination": "<the connection's name>" | "connection_id": "...",
    "uses": {...}, "constant_headers": {...}}``. The connection must already be
    granted to this universe by its owner (deposited through the request rail).
    This never adds endpoints, never touches the secret, and never selects
    serving. It never creates or changes a model use either: a model list and
    its billing are a spend claim, so they go through the owner's answer to a
    ``connect`` ask (money floor, review 2026-09-24).
    """
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path, _request_universe
    from tinyassets.broker.ledger_queries import CONNECTION_GRANTS, query_ledger
    from tinyassets.daemon_server import list_universe_acl
    from tinyassets.principals import named_principal

    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "connection"}
    actor = named_principal(permissions.current_actor_id())
    if not actor:
        return {"error": "authentication_required", "resource": "connection"}
    uid = _request_universe(universe_id)
    base = _base_path()
    if not any(row.get("actor_id") == actor and row.get("permission") == "admin"
               for row in list_universe_acl(base, universe_id=uid)):
        return dict(_NOT_FOUND)
    try:
        document = json.loads(payload) if isinstance(payload, str) else payload
        if not isinstance(document, dict):
            raise ConnectionUseError("payload_json must be a JSON object")
        if set(document) - {"destination", "connection_id", "uses", "constant_headers"}:
            raise ConnectionUseError(
                "configure takes destination or connection_id, uses and constant_headers"
            )
        if "uses" not in document and "constant_headers" not in document:
            raise ConnectionUseError("send uses and/or constant_headers to configure")
        uses = validate_uses(document.get("uses")) if "uses" in document else {}
        headers = validate_constant_headers(document.get("constant_headers"))
    except (ConnectionUseError, ValueError) as exc:
        return {"error": "connection_setup_invalid", "detail": str(exc)}

    connection_id = str(document.get("connection_id") or "").strip()
    destination = str(document.get("destination") or "").strip().lower()
    if not connection_id and destination:
        from tinyassets.api.http_connection import _ids

        connection_id, _ = _ids(universe_id=uid, destination=destination)
    if not connection_id:
        return {"error": "connection_setup_invalid",
                "detail": "name the connection by destination or connection_id"}
    grants = query_ledger(base, query=CONNECTION_GRANTS, principal=actor,
                          command_center=uid, grant_id="lookup",
                          connection_id=connection_id)["grant_ids"]
    if len(grants) != 1:
        return dict(_NOT_FOUND)
    applied = apply_connection_uses(
        base=Path(base), uid=uid, actor=actor, grant_id=grants[0],
        uses=uses, constant_headers=headers,
    )
    if applied.get("error"):
        return applied
    return {"status": "configured", **applied}


def connection_uses_view(ledger: Any, connection_id: str, *, data_root=None,
                         principal=None, command_center=None, grant_id=None) -> dict[str, Any]:
    """The uses and constant headers a connection declares, for read surfaces."""
    view: dict[str, Any] = {"uses": {"call": {}}}
    try:
        if data_root is not None:
            from tinyassets.broker.capabilities import capability_operation

            scope = dict(principal=principal, command_center=command_center, grant_id=grant_id,
                         connection_id=connection_id)
            model = capability_operation(data_root, capability_kind="model_use", **scope)
            headers = capability_operation(data_root, capability_kind="constant_headers", **scope)
        else:
            model = ledger.get_connection_capability(connection_id, "model_use")
            headers = ledger.get_connection_capability(connection_id, "constant_headers")
    except (LookupError, ValueError):
        return {**view, "uses_unreadable": True}
    if model is not None:
        view["uses"]["model"] = model.descriptor()
    if headers is not None:
        view["constant_headers"] = dict(headers.headers)
    return view


__all__ = [
    "ConnectionUseError",
    "apply_connection_uses",
    "configure_connection",
    "connection_uses_view",
    "select_model_if_unpowered",
    "validate_constant_headers",
    "validate_uses",
]
