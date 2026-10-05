"""Graph-handle adapter for public agent definitions and private bindings."""

from __future__ import annotations

import json
from typing import Any

from tinyassets.agent_interchange import (
    convert_export,
    get_import_stage,
    publish_import_stage,
    requires_runtime_response,
    stage_import,
)
from tinyassets.api.helpers import _base_path, _request_universe
from tinyassets.custom_agents import (
    AgentConflictError,
    AgentNotFoundError,
    AgentValidationError,
    create_binding,
    get_binding,
    get_definition,
    import_definition,
    list_bindings,
    list_definitions,
    publish_definition,
    update_binding,
)


def _not_found(resource: str) -> dict[str, Any]:
    return {"error": "not_found", "resource": resource}


def _authenticated_actor() -> str | None:
    from tinyassets.api import permissions

    if not permissions.is_authenticated_request():
        return None
    from tinyassets.principals import named_principal

    return named_principal(permissions.current_actor_id()) or None


def _binding_universe(raw_universe_id: str) -> str:
    return _request_universe(raw_universe_id)


def _binding_access(
    universe_id: str,
    *,
    write: bool,
) -> dict[str, Any] | None:
    from tinyassets.api import permissions
    from tinyassets.daemon_server import list_universe_acl

    actor = permissions.current_actor_id() if permissions.is_authenticated_request() else ""
    permission = ""
    if actor:
        try:
            permission = next(
                (
                    str(row.get("permission") or "")
                    for row in list_universe_acl(
                        _base_path(),
                        universe_id=universe_id,
                    )
                    if row.get("actor_id") == actor
                ),
                "",
            )
        except Exception:
            permission = ""
    allowed = {"write", "admin"} if write else {"read", "write", "admin"}
    if permission in allowed:
        return None
    if not write:
        return _not_found("agent_binding")
    return permissions.universe_access_error(
        universe_id=universe_id,
        write=True,
        action="write_agent_binding",
        surface="custom_agents",
    )


def _tags(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str):
        return [item.strip() for item in value.split(",") if item.strip()]
    return []


def _payload(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        def unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
            document: dict[str, Any] = {}
            for key, nested in pairs:
                if key in document:
                    raise AgentValidationError(f"duplicate object key: {key}")
                document[key] = nested
            return document

        try:
            parsed = json.loads(value, object_pairs_hook=unique_object)
        except json.JSONDecodeError as exc:
            raise AgentValidationError(f"payload_json is invalid JSON: {exc}") from exc
        if isinstance(parsed, dict):
            return parsed
    raise AgentValidationError("payload must be a JSON object")


def custom_agents(
    *,
    action: str,
    definition_id: str = "",
    binding_id: str = "",
    stage_id: str = "",
    universe_id: str = "",
    payload: Any = None,
    query: str = "",
    tags: Any = None,
    author_id: str = "",
    limit: int = 30,
    idempotency_key: str = "",
    expected_revision: int = 0,
) -> dict[str, Any]:
    """Execute one custom-agent operation and return a structured envelope."""

    normalized = (action or "").strip().lower()
    base = _base_path()
    try:
        if normalized == "list_agents":
            agents = list_definitions(
                base,
                query=query,
                tags=_tags(tags),
                author_id=author_id,
                limit=limit,
            )
            return {"agents": agents, "count": len(agents)}

        if normalized == "get_agent":
            agent = get_definition(base, definition_id, include_catalogue=True)
            return {"agent": agent} if agent is not None else _not_found("agent_definition")

        if normalized == "get_import_stage":
            actor = _authenticated_actor()
            if actor is None:
                return _not_found("agent_import_stage")
            stage = get_import_stage(base, actor_id=actor, stage_id=stage_id)
            return {"stage": stage} if stage is not None else _not_found("agent_import_stage")

        if normalized in {"stage_import", "publish_stage", "convert_export"}:
            actor = _authenticated_actor()
            if actor is None:
                return {
                    "error": "authentication_required",
                    "resource": "agent_import_stage",
                }
            if normalized == "stage_import":
                document = _payload(payload)
                source_json = document.get("source_json")
                source_base64 = document.get("source_base64")
                source_locator = document.get("source_locator")
                adapter = document.get("adapter")
                if not isinstance(adapter, dict):
                    raise AgentValidationError("stage import requires an adapter object")
                if isinstance(source_json, dict) and isinstance(adapter.get("rules"), list):
                    stage = stage_import(
                        base,
                        actor_id=actor,
                        source_json=source_json,
                        adapter=adapter,
                        idempotency_key=idempotency_key,
                    )
                    return {"status": "staged", "stage": stage}
                if source_json is not None and not isinstance(source_json, dict):
                    raise AgentValidationError("source_json must be an object")
                if source_base64 is not None and not isinstance(source_base64, str):
                    raise AgentValidationError("source_base64 must be a string")
                if source_locator is not None and not isinstance(source_locator, str):
                    raise AgentValidationError("source_locator must be a string")
                return requires_runtime_response(
                    adapter,
                    direction="import",
                    source_json=source_json,
                    source_base64=source_base64,
                    source_locator=source_locator,
                )
            if normalized == "convert_export":
                document = _payload(payload)
                adapter = document.get("adapter")
                if not isinstance(adapter, dict):
                    raise AgentValidationError("convert export requires an adapter object")
                return convert_export(
                    base,
                    actor_id=actor,
                    definition_id=definition_id,
                    adapter=adapter,
                    idempotency_key=idempotency_key,
                )
            agent = publish_import_stage(
                base,
                actor_id=actor,
                stage_id=stage_id,
                idempotency_key=idempotency_key,
            )
            return {"status": "published", "agent": agent}

        if normalized in {"publish_agent", "import_agent"}:
            actor = _authenticated_actor()
            if actor is None:
                return {
                    "error": "authentication_required",
                    "resource": "agent_definition",
                }
            document = _payload(payload)
            if normalized == "import_agent":
                agent = import_definition(
                    base,
                    author_id=actor,
                    portable_definition=document,
                    idempotency_key=idempotency_key,
                )
            else:
                agent = publish_definition(
                    base,
                    author_id=actor,
                    payload=document,
                    idempotency_key=idempotency_key,
                )
            return {"status": "published", "agent": agent}

        if normalized in {
            "list_bindings",
            "get_binding",
            "create_binding",
            "update_binding",
            "bind_serving_provider",
            "set_serving",
        }:
            uid = _binding_universe(universe_id)
            write = normalized in {
                "create_binding",
                "update_binding",
                "bind_serving_provider",
                "set_serving",
            }
            denial = _binding_access(uid, write=write)
            if denial is not None:
                return denial

            if normalized == "list_bindings":
                bindings = list_bindings(base, universe_id=uid, limit=limit)
                return {
                    "universe_id": uid,
                    "bindings": bindings,
                    "count": len(bindings),
                }
            if normalized == "get_binding":
                binding = get_binding(
                    base,
                    universe_id=uid,
                    binding_id=binding_id,
                )
                return {"binding": binding} if binding is not None else _not_found("agent_binding")

            actor = _authenticated_actor()
            if actor is None:
                return {
                    "error": "authentication_required",
                    "resource": "agent_binding",
                }
            document = _payload(payload)
            if normalized in {"bind_serving_provider", "set_serving"}:
                from tinyassets.api.helpers import _universe_dir
                from tinyassets.exceptions import ProviderError
                from tinyassets.provider_serving_binding import (
                    bind_serving_provider,
                    set_serving,
                )
                from tinyassets.storage.current_home import CurrentHomeChanged
                from tinyassets.storage.model_preferences import PreferenceStoreUnavailable

                expected_fields = (
                    {"provider"}
                    if normalized == "bind_serving_provider"
                    else {"enabled"}
                )
                if normalized == "bind_serving_provider" and "model_access" in document:
                    expected_fields = expected_fields | {"model_access"}
                if set(document) != expected_fields:
                    raise AgentValidationError(
                        f"{normalized} payload must contain exactly "
                        f"{sorted(expected_fields)}"
                    )
                if normalized == "bind_serving_provider":
                    provider = document["provider"]
                    if not isinstance(provider, str):
                        raise AgentValidationError("provider must be a string")
                    try:
                        model_access = None
                        if "model_access" in document:
                            from tinyassets.provider_assignment_manifest import parse_model_access

                            model_access = parse_model_access(document["model_access"])
                        return bind_serving_provider(
                            base_path=base,
                            universe_dir=_universe_dir(uid),
                            owner_user_id=actor,
                            universe_id=uid,
                            agent_binding_id=binding_id,
                            expected_revision=expected_revision,
                            provider=provider,
                            **({} if model_access is None else {"model_access": model_access}),
                        )
                    except (PermissionError, ValueError, LookupError) as exc:
                        return {
                            "error": "provider_authority_denied",
                            "detail": str(exc),
                        }
                enabled = document["enabled"]
                if not isinstance(enabled, bool):
                    raise AgentValidationError("enabled must be a boolean")
                try:
                    return set_serving(
                        base_path=base,
                        universe_dir=_universe_dir(uid),
                        owner_user_id=actor,
                        universe_id=uid,
                        agent_binding_id=binding_id,
                        expected_revision=expected_revision,
                        enabled=enabled,
                    )
                except (
                    PermissionError, ValueError, LookupError,
                    CurrentHomeChanged, PreferenceStoreUnavailable, ProviderError,
                ) as exc:
                    return {"error": "provider_authority_denied", "detail": str(exc)}
            if normalized == "create_binding":
                binding = create_binding(
                    base,
                    universe_id=uid,
                    definition_id=definition_id,
                    created_by=actor,
                    payload=document,
                )
                return {"status": "configured", "binding": binding}
            binding = update_binding(
                base,
                universe_id=uid,
                binding_id=binding_id,
                expected_revision=expected_revision,
                updated_by=actor,
                payload=document,
                definition_id=definition_id,
            )
            return {"status": "configured", "binding": binding}

        return {
            "error": "unknown_agent_action",
            "action": action,
            "allowed_actions": [
                "list_agents",
                "get_agent",
                "get_import_stage",
                "publish_agent",
                "import_agent",
                "stage_import",
                "publish_stage",
                "convert_export",
                "list_bindings",
                "get_binding",
                "create_binding",
                "update_binding",
                "bind_serving_provider",
                "set_serving",
            ],
        }
    except AgentConflictError as exc:
        return {"error": "agent_conflict", "detail": str(exc)}
    except AgentNotFoundError:
        if normalized in {"get_import_stage", "publish_stage"}:
            resource = "agent_import_stage"
        else:
            resource = "agent_binding" if "binding" in normalized else "agent_definition"
        return _not_found(resource)
    except AgentValidationError as exc:
        return {"error": "agent_validation_error", "detail": str(exc)}


__all__ = ["custom_agents"]
