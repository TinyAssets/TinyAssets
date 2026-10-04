"""Explicit public instruction templates; private, additive recipient bindings.

A package's ``agents`` remains its file inventory. These stably keyed public
components instead reference the existing immutable definition store. Only
instruction-only definitions are supported: no operational binding settings,
permissions, model selection, memory or source account identifiers travel.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from typing import Any

AGENT_REF_KIND = "tinyassets.agent-ref.v1"
_FIELDS = {"kind", "name", "agent_definition_id", "content_fingerprint"}


def selection(raw: Any) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError("agent_templates must map stable component keys to owned binding ids")
    for key, binding_id in raw.items():
        if (
            not isinstance(key, str)
            or not re.fullmatch(r"[a-z][a-z0-9_-]{0,63}", key)
            or not isinstance(binding_id, str)
            or not binding_id
            or binding_id != binding_id.strip()
            or len(binding_id) > 200
        ):
            raise ValueError("agent_templates contains an invalid component key or binding id")
    if len(set(raw.values())) != len(raw):
        raise ValueError("each selected agent binding needs exactly one stable component key")
    return dict(raw)


def _definition(base: Any, definition_id: str, fingerprint: str = "", *, readers=None) -> dict:
    from tinyassets.custom_agents import get_definition

    definition = (readers.definition(definition_id) if readers is not None
                  else get_definition(base, definition_id))
    if definition is None or (fingerprint and definition["content_fingerprint"] != fingerprint):
        raise ValueError("a required public agent definition is missing or changed")
    components = definition.get("components")
    if not isinstance(components, dict) or not components:
        raise ValueError("an agent template must contain public instructions")
    # This is the exact data the addressed-agent runtime consumes. An unknown
    # runtime contract must not appear installed while being silently ignored.
    for component in components.values():
        config = component.get("config") if isinstance(component, dict) else None
        if (
            not isinstance(component, dict)
            or set(component) != {"kind", "config"}
            or not isinstance(config, dict)
            or set(config) != {"instructions"}
            or not isinstance(config["instructions"], str)
            or not config["instructions"].strip()
        ):
            raise ValueError("this agent definition is not an instruction-only public template")
    return definition


def export_templates(base: Any, uid: str, actor: str, selected: dict) -> dict[str, dict]:
    from tinyassets.addressed_agents import is_conversable
    from tinyassets.custom_agents import get_binding

    result = {}
    for key, binding_id in selection(selected).items():
        binding = get_binding(base, universe_id=uid, binding_id=binding_id)
        if binding is None or not is_conversable(binding, owner=actor, universe_id=uid):
            raise ValueError("a selected agent is not one of your conversable agents here")
        definition = _definition(base, binding["agent_definition_id"])
        result[key] = {
            "kind": AGENT_REF_KIND,
            "name": binding["configuration"]["name"],
            "agent_definition_id": definition["agent_definition_id"],
            "content_fingerprint": definition["content_fingerprint"],
        }
    return result


def templates(base: Any, components: dict, *, readers=None) -> list[dict]:
    result = []
    for key, component in components.items():
        if not isinstance(component, dict) or component.get("kind") != AGENT_REF_KIND:
            continue
        if (
            set(component) != _FIELDS
            or not isinstance(component.get("name"), str)
            or not component["name"].strip()
            or len(component["name"]) > 120
            or not isinstance(component.get("agent_definition_id"), str)
            or not component["agent_definition_id"]
            or not isinstance(component.get("content_fingerprint"), str)
            or not re.fullmatch(r"[0-9a-f]{64}", component["content_fingerprint"])
        ):
            raise ValueError("an agent template reference has unsupported or invalid fields")
        _definition(base, component["agent_definition_id"], component["content_fingerprint"],
                    readers=readers)
        result.append({"key": key, **component})
    return result


def resolve_ui_refs(ui: dict | None, included: set[str]) -> None:
    from tinyassets.custom_agents import app_ui_agent_refs

    if ui is not None and any(key not in included for key in app_ui_agent_refs(ui).values()):
        raise ValueError("agent_refs must name only included public agent templates")


def reject_nested_workflows(snapshot: Any) -> None:
    """Refuse both schema-declared child-workflow forms before copying.

    The compiler dispatches these fields whenever they are non-None, including
    a malformed empty spec. Neither live nor immutable child references can be
    safely carried without dependency planning and recipient remapping.
    """
    if isinstance(snapshot, dict):
        if any(snapshot.get(field) is not None for field in (
                "invoke_branch_spec", "invoke_branch_version_spec")):
            raise ValueError("nested workflow dependencies are not supported by this copier")
        for value in snapshot.values():
            reject_nested_workflows(value)
    elif isinstance(snapshot, list):
        for value in snapshot:
            reject_nested_workflows(value)


def validate_workflows(base: Any, workflows: list[dict], *,
                       version_field: str = "version_id", readers=None) -> None:
    from tinyassets.branch_versions import (
        branch_version_def_id,
        branch_version_is_public,
        get_branch_version,
        version_readable_by,
    )
    from tinyassets.daemon_server import get_branch_definition

    for workflow in workflows:
        # The caller names its actual copy field. Public package components may
        # carry arbitrary extra keys, which must never mask the copied version.
        version_id = workflow.get(version_field)
        if (not isinstance(version_id, str) or not version_id
                or version_id != version_id.strip()):
            raise ValueError("a required workflow version is missing or invalid")
        source_id = (readers.version_parent(version_id) if readers is not None
                     else branch_version_def_id(base, version_id))
        try:
            branch = (readers.branch(source_id) if readers is not None
                      else get_branch_definition(base, branch_def_id=source_id)
                      ) if source_id else {}
        except KeyError:
            branch = {}
        if not version_readable_by(
            None,
            author=branch.get("author"),
            visibility=branch.get("visibility"),
            public=(readers.version_public(version_id) if readers is not None
                    else branch_version_is_public(base, version_id)),
        ):
            raise ValueError("a required workflow version is missing or no longer public")
        version = (readers.version(version_id) if readers is not None
                   else get_branch_version(base, version_id))
        if version is None:
            raise ValueError("a required workflow version is missing or no longer public")
        reject_nested_workflows(version.snapshot)


def binding_id(uid: str, actor: str, pin_id: str, key: str) -> str:
    encoded = json.dumps([uid, actor, pin_id, key], separators=(",", ":")).encode()
    return "agent_binding_" + hashlib.sha256(encoded).hexdigest()


def _check_existing(row: Any, uid: str, actor: str, template: dict, configuration: dict) -> None:
    from tinyassets.custom_agents import _binding_from_row

    if row is None:
        return
    binding = _binding_from_row(row)
    if (
        binding["universe_id"] != uid
        or binding["created_by"] != actor
        or binding["updated_by"] != actor
        or binding["revision"] != 1
        or binding["status"] != "configured"
        or binding["configuration"] != configuration
        or binding["agent_definition_id"] != template["agent_definition_id"]
    ):
        raise ValueError(
            "the installed agent changed or its identity conflicts; nothing overwritten"
        )


def check_targets(base: Any, uid: str, actor: str, pin_id: str, agents: list[dict]) -> None:
    """Refuse known recipient conflicts before any other component is copied."""
    from tinyassets.custom_agents import _agent_connect

    if not agents:
        return
    with _agent_connect(base) as conn:
        for template in agents:
            intended = binding_id(uid, actor, pin_id, template["key"])
            row = conn.execute(
                "SELECT * FROM agent_bindings WHERE agent_binding_id = ?", (intended,)
            ).fetchone()
            _check_existing(
                row, uid, actor, template, {"schema_version": 1, "name": template["name"].strip()}
            )


def install(base: Any, uid: str, actor: str, pin_id: str, template: dict) -> str:
    """One atomic create/replay; never interpret an existing edited row as ours."""
    from tinyassets.custom_agents import (
        _agent_connect,
        _canonical_json,
        _normalize_binding_payload,
    )
    from tinyassets.principals import named_principal

    if not uid or not pin_id or not named_principal(actor):
        raise ValueError("agent installation needs its pinned recipient identity")
    checked = templates(base, {template["key"]: {k: v for k, v in template.items() if k != "key"}})
    if len(checked) != 1:
        raise ValueError("an agent template is required")
    intended = binding_id(uid, actor, pin_id, template["key"])
    configuration = _normalize_binding_payload({"schema_version": 1, "name": template["name"]})
    now = time.time()
    with _agent_connect(base) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM agent_bindings WHERE agent_binding_id = ?", (intended,)
        ).fetchone()
        if row is not None:
            _check_existing(row, uid, actor, template, configuration)
            return intended
        conn.execute(
            "INSERT INTO agent_bindings (agent_binding_id, universe_id, agent_definition_id, "
            "configuration_json, revision, status, created_by, updated_by, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, 'configured', ?, ?, ?, ?)",
            (
                intended,
                uid,
                template["agent_definition_id"],
                _canonical_json(configuration),
                actor,
                actor,
                now,
                now,
            ),
        )
    return intended


def consent_lines(agents: list[dict]) -> list[str]:
    from tinyassets.api.publish_requests import _shown

    if not agents:
        return ["No public chat-agent templates are included; existing agents stay yours."]
    return [
        "Chat agents, as new private bindings to these public instructions:",
        *(f"- {_shown(a['name'])}" for a in agents),
        "No model assignments, access grants, conversations or private settings are copied.",
    ]
