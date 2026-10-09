"""Owner-confirmed model access, composed over existing binding publication.

The served agent can raise this request, never answer it. No secret, fallback
order or user-authored display text is used as authority. Publication and
reconnection retain their existing transactions and independent launch checks.
"""

from __future__ import annotations

import json

from tinyassets.provider_assignment_manifest import ModelAccess, parse_model_access


def validate_action(action: dict) -> dict:
    expected = {"type", "agent_binding_id", "expected_revision", "provider", "model_access"}
    # Server snapshots are never supplied by the asker.
    if set(action) != expected:
        raise ValueError("bind_model_access requires exactly " + ", ".join(sorted(expected)))
    for field in ("agent_binding_id", "provider"):
        value = action[field]
        if (not isinstance(value, str) or not value or len(value) > 200
                or value != value.strip() or not value.isprintable()):
            raise ValueError(f"invalid {field}")
    revision = action["expected_revision"]
    if type(revision) is not int or revision < 1:
        raise ValueError("expected_revision must be a positive integer")
    access = parse_model_access(action["model_access"])
    if action["provider"] not in access:
        raise ValueError("model_access must include the root provider")
    return {**action, "model_access": {name: value.document() for name, value in access.items()}}


def _scope(uid):
    from tinyassets.api import permissions
    from tinyassets.api.helpers import _base_path
    from tinyassets.principals import named_principal
    from tinyassets.shared_self import require_founder_home

    actor = named_principal(permissions.current_actor_id())
    if not permissions.is_authenticated_request() or not actor:
        raise PermissionError("authenticated owner required")
    base = _base_path()
    universe = require_founder_home(base, uid, actor)
    return base, universe, actor


def _observe(base, universe, actor, uid, binding_id):
    from tinyassets.custom_agents import get_binding
    from tinyassets.provider_assignment import (
        load_provider_assignment_in_transaction,
        provider_assignment_admission,
    )
    from tinyassets.storage.current_home import check_current_home
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    with provider_assignment_admission().shared(universe):
        binding = get_binding(base, universe_id=uid, binding_id=binding_id)
        if binding is None or binding["created_by"] != actor:
            raise PermissionError("owned agent binding required")
        with SQLiteProviderWorkAuthorityStore(base).connection() as conn:
            conn.execute("BEGIN")
            check_current_home(conn, actor, uid)
            assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
            if assignment is not None and assignment.owner_user_id != actor:
                raise PermissionError("owned provider assignment required")
    return binding, assignment


def _membership(assignment):
    if assignment is None or assignment.state == "unassigned":
        return {}
    if not assignment.candidates:
        return {assignment.provider: ModelAccess().document()}
    return {member.provider: member.access.document() for member in assignment.candidates}


def _proposal(base, actor, uid, action):
    from tinyassets.provider_serving_binding import _resolve_serving_source

    access = parse_model_access(action["model_access"])
    sources = [_resolve_serving_source(base, uid, actor, name, value)
               for name, value in access.items()]
    normalized = {source.provider: source.access.document() for source in sources}
    if len(normalized) != len(sources):
        raise ValueError("duplicate provider aliases")
    root = _resolve_serving_source(base, uid, actor, action["provider"], ModelAccess()).provider
    if root not in normalized:
        raise ValueError("root provider must be accepted")
    return root, normalized


def capture_action(uid: str, action: dict) -> dict:
    """Validate before a person is shown an ask; capture immutable baseline."""
    base, universe, actor = _scope(uid)
    binding, assignment = _observe(base, universe, actor, uid, action["agent_binding_id"])
    if binding["revision"] != action["expected_revision"]:
        raise ValueError("agent binding revision is stale; read it again")
    root, proposed = _proposal(base, actor, uid, action)
    previous = _membership(assignment)
    if not set(previous).issubset(proposed):
        raise ValueError("model setup must preserve other accepted providers")
    for name, value in proposed.items():
        old = previous.get(name)
        if old is None and value["cost_caps"] is not None:
            raise ValueError("new sources require free-only access in this request")
        if old is not None and old["cost_caps"] != value["cost_caps"]:
            raise ValueError("model setup must preserve existing spending ceilings")
        if name != root and old is not None and old != value:
            raise ValueError("model setup must preserve other providers' model scopes")
    return {**action, "consent_version": 1, "root_provider": root,
            "connection_incarnations": _connection_incarnations(base, actor, uid, proposed),
            "proposed_membership": proposed, "previous_membership": previous,
            "expected_assignment_digest": assignment.assignment_digest if assignment else "",
            "expected_assignment_generation": assignment.generation if assignment else 0}


def _connection_incarnations(base, owner, uid, membership):
    from tinyassets.broker.supervisor import broker_selected
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage.outbound_connections import ConnectionLedger

    if broker_selected():
        return _broker_connection_incarnations(base, owner, uid, membership)
    ledger = ConnectionLedger(base / "outbound.db")
    captured = {}
    for provider in membership:
        if not provider.startswith("api_key_http:"):
            continue
        definition = get_definition(uid, provider.removeprefix("api_key_http:"))
        grant = ledger.get_grant(definition.ref) if definition else None
        if (definition is None or definition.owner_user_id != owner or grant is None
                or grant.owner_user_id != owner or grant.universe_id != uid):
            raise PermissionError("model connection changed")
        incarnation = ledger.incarnation(grant.connection_id)
        if not incarnation:
            raise PermissionError("model connection changed")
        captured[provider] = incarnation
    return captured


def _broker_connection_incarnations(base, owner, uid, membership):
    from tinyassets.broker.ledger_queries import granted_resource_row
    from tinyassets.providers.definition import get_definition
    from tinyassets.storage.outbound_connections import GrantResolutionError

    captured = {}
    for provider in membership:
        if not provider.startswith("api_key_http:"):
            continue
        definition = get_definition(uid, provider.removeprefix("api_key_http:"))
        if definition is None or definition.owner_user_id != owner:
            raise PermissionError("model connection changed")
        try:
            resource = granted_resource_row(base, principal=owner, command_center=uid,
                                            grant_id=definition.ref)
        except GrantResolutionError:
            raise PermissionError("model connection changed") from None
        incarnation = resource["incarnation"]
        if not incarnation:
            raise PermissionError("model connection changed")
        captured[provider] = incarnation
    return captured


def grant_sentence(action: dict) -> str:
    """What the owner is agreeing to, in their words: no internal ids.

    Agent binding ids, provider definition ids and grant refs are storage
    handles. Live 2026-09-24 the free-model approval read "Update model access
    for agent agent_binding_01m2..., root source api_key_http:provdef_ed01...".
    The exact ids stay on the stored action, which is what the answer checks.
    """
    def one(value):
        if value["model_scope"] == "explicit" and value["model_ids"]:
            models = "the models " + ", ".join(
                str(m) if m else "its default model" for m in value["model_ids"])
        else:
            models = "the models this connection offers"
        return models + (
            ", free models only" if value["cost_caps"] is None
            else ", within spending limits you already set "
            + json.dumps(value["cost_caps"], sort_keys=True)
        )

    def describe(members):
        values = [value for _, value in sorted(members.items())]
        if not values:
            return "nothing yet"
        if len(values) == 1:
            return one(values[0])
        return "; ".join(f"source {n}: {one(v)}" for n, v in enumerate(values, 1))

    before = action["previous_membership"]
    after = describe(action["proposed_membership"])
    return (
        f"Your command center will think with {after}. "
        + (f"Until now it could use {describe(before)}. " if before else "")
        + "No paid-model spending or credit purchase is approved beyond that. "
        "If it is running, it reconnects with this access; if that fails it stays "
        "off until you try again. Your other sources and saved choices stay as they are."
    )


def execute_action(uid: str, action: dict) -> dict:
    """Complete or resume exactly this approved setup; never infer new access."""
    from tinyassets.provider_serving_binding import bind_serving_provider, set_serving

    if type(action.get("consent_version")) is not int or action["consent_version"] != 1:
        raise ValueError("this request lacks a model-access disclosure; ask again")
    base, universe, actor = _scope(uid)
    root, proposed = _proposal(base, actor, uid, action)
    if action.get("connection_incarnations", {}) != _connection_incarnations(
            base, actor, uid, proposed):
        raise PermissionError("model connection changed; review a fresh request")
    if root != action["root_provider"] or proposed != action["proposed_membership"]:
        raise PermissionError("model access changed since the request was shown")
    binding, assignment = _observe(base, universe, actor, uid, action["agent_binding_id"])
    revision, generation = action["expected_revision"], action["expected_assignment_generation"]
    current_digest = assignment.assignment_digest if assignment else ""
    matching = (assignment is not None and assignment.provider == root
                and _membership(assignment) == proposed)
    untouched = (binding["revision"] == revision
                 and current_digest == action["expected_assignment_digest"])
    prior_failed = (binding["revision"] == revision and matching
                    and assignment.generation > generation
                    and assignment.state in {"pending", "failed"})
    bound = (matching and assignment.state == "ready"
             and binding["configuration"].get("provider_ref") == assignment.binding_id
             and (untouched or (binding["revision"] == revision + 1
                                and assignment.generation > generation)))
    scope = dict(base_path=base, universe_dir=universe, owner_user_id=actor,
                 universe_id=uid, agent_binding_id=action["agent_binding_id"],
                 require_current_home=True)
    if not bound:
        if not (untouched or prior_failed):
            raise PermissionError("model setup was superseded; review a fresh request")
        published = bind_serving_provider(
            **scope, expected_revision=revision, provider=action["provider"],
            model_access=parse_model_access(action["model_access"]),
            expected_assignment_digest=current_digest,
        )
        if published.get("status") != "ready":
            raise PermissionError("model access publication was not confirmed")
        binding, assignment = _observe(base, universe, actor, uid, action["agent_binding_id"])
        if (assignment is None or assignment.state != "ready"
                or assignment.provider != root or _membership(assignment) != proposed
                or binding["revision"] != published["agent_binding"]["revision"]
                or binding["configuration"].get("provider_ref") != assignment.binding_id):
            raise PermissionError("model setup changed before reconnect")
    if binding["status"] != "serving":
        result = set_serving(
            **scope, expected_revision=binding["revision"], enabled=True,
            expected_assignment_digest=assignment.assignment_digest,
        )
        if result.get("status") != "serving":
            raise PermissionError("reconnection was not confirmed")
    return {"status": "serving", "agent_binding_id": action["agent_binding_id"],
            "assignment_generation": assignment.generation}
