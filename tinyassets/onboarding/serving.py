"""Make a deposited subscription actually SERVE the founder's own command center.

``connect_llm`` is deliberately write-only (the chatbot path re-points serving
with explicit ``bind_serving_provider`` / ``set_serving`` calls). The phone
app's "Connect" must be the whole gesture: live test 2026-08-21 showed an
OpenAI credential landing in the vault while every turn still failed with
"exactly one founder serving binding is required", because nothing created
the agent binding the serving authority hangs off.

This provisions the minimal chain for the founder's OWN command center, exactly as
the served-router tests do:

    platform definition (published once, idempotent)
      -> one agent binding created by the founder ("Your agent", writer)
        -> bind_serving_provider(provider of the deposited service)
          -> set_serving(enabled=True)

The founder's DEDICATED platform binding is used (created once; reset to
canonical content at an exact revision if a collaborator edited it — never an
arbitrary rediscovered binding), re-pointed to the newly deposited provider
(users switch Claude <-> OpenAI at will); any other serving binding the
founder created is disabled so exactly one serves. A CURRENT admin ACL is
re-checked before any mutation. All authority checks inside the serving module
still run — this composes the public primitives, it does not bypass them.
Claude serving stays behind the operator opt-in (``TINYASSETS_ALLOW_CLAUDE_SERVING``);
when that refuses, the result says so.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

#: The platform's default agent. A published definition is immutable and
#: fingerprinted under its idempotency key, so the universe -> command center
#: rename (2026-10-01) publishes a NEW one rather than editing the old: changing
#: the old payload would raise AgentConflictError at every onboarding.
PLATFORM_DEFINITION_AUTHOR = "platform:command-center-default"
PLATFORM_DEFINITION_KEY = "command-center-default-v1"
_PLATFORM_DEFINITION = {
    "schema_version": 1,
    "name": "Your agent",
    "description": (
        "The default agent in a founder's own command center: it speaks on the "
        "subscription the founder connected. Published once by the platform; "
        "every founder's home binds to it."
    ),
    "tags": ["platform", "default"],
    "components": {"identity": {"kind": "soul", "config": {}}},
}
_BINDING_PAYLOAD = {"schema_version": 1, "name": "Your agent", "role": "writer"}
#: The definition every home bound to before the rename. An existing binding on
#: it is still the founder's platform binding, used as is; the cutover migration
#: re-points every one in place (design D10). Read-only: never re-published.
RETIRED_PLATFORM_DEFINITION_AUTHOR = "platform:universe-default"
RETIRED_BINDING_PAYLOAD = {"schema_version": 1, "name": "Your universe", "role": "writer"}
#: Friendly ALIASES for the two subscription CLIs, not an allowlist. Anything
#: else is passed straight through as a compute-connection id, because
#: `bind_serving_provider` already resolves one and `_open_serving_context`
#: already refuses a grant the caller does not own. Gating here as well only
#: refused legitimate users: a universe could not be pointed at any LLM its
#: owner had registered (founder, 2026-09-03: "we shouldnt have a chatgpt
#: spacific path").
_SERVICE_TO_PROVIDER = {"codex": "codex", "claude": "claude-code"}

#: One gesture at a time per universe. `_platform_binding` is check-then-create
#: and `agent_bindings` has no uniqueness constraint, so two first-time calls
#: (two open tabs healing, a paste and a phone connect) both see no binding,
#: both create one, both go serving, and their quiesce passes disable each
#: other: zero serving, two bindings, and every later call refusing them as
#: ambiguous (Codex on #2760, S3). The route and the deposit both run in this
#: process, so a process lock keyed by universe closes it.
_GESTURE_LOCKS: dict[str, threading.RLock] = {}
_GESTURE_LOCKS_GUARD = threading.Lock()


def _gesture_lock(universe_id: str) -> threading.RLock:
    with _GESTURE_LOCKS_GUARD:
        lock = _GESTURE_LOCKS.get(universe_id)
        if lock is None:
            lock = _GESTURE_LOCKS[universe_id] = threading.RLock()
        return lock


def _retired_platform_definition_ids(base: Path) -> frozenset[str]:
    """Ids of the pre-rename default definition, looked up, never created."""
    from tinyassets.custom_agents import list_definitions

    return frozenset(
        str(d["agent_definition_id"])
        for d in list_definitions(base, author_id=RETIRED_PLATFORM_DEFINITION_AUTHOR, limit=100)
    )


def _platform_definition(base: Path) -> dict[str, Any]:
    from tinyassets.custom_agents import publish_definition

    return publish_definition(
        base,
        author_id=PLATFORM_DEFINITION_AUTHOR,
        payload=dict(_PLATFORM_DEFINITION),
        idempotency_key=PLATFORM_DEFINITION_KEY,
    )


def _require_current_admin(base: Path, *, universe_id: str, owner: str) -> None:
    """The owner must hold an explicit, CURRENT ``admin`` ACL row on the command center.

    Re-checked immediately before any mutation (Codex 2026-08-21 #1): a bearer
    whose founder-home mapping survived an ACL revocation must not be able to
    create or re-point serving. Same row ``connect_llm`` requires."""
    from tinyassets.daemon_server import list_universe_acl

    rows = [
        row
        for row in list_universe_acl(base, universe_id=universe_id)
        if row.get("actor_id") == owner and row.get("permission") == "admin"
    ]
    if not rows:
        raise PermissionError("a current admin ACL on the command center is required")


def _platform_binding(base: Path, *, universe_id: str, owner: str) -> dict[str, Any]:
    """The founder's DEDICATED platform binding — never an arbitrary rediscovered one.

    Codex 2026-08-21 #2 (confused deputy): a write collaborator may update a
    founder-created binding (definition, configuration); auto-selecting "the
    founder's current binding" would then bind the founder's credential under
    collaborator-chosen content. So: the binding must be founder-created AND
    on the platform definition; if its configuration has drifted from the
    canonical payload it is reset by the founder at an exact revision before
    use; otherwise a fresh one is created. Ambiguity (several candidates) is
    refused rather than guessed.
    """
    from tinyassets.custom_agents import create_binding, list_bindings, update_binding

    definition = _platform_definition(base)
    did = definition["agent_definition_id"]
    platform_ids = {did} | _retired_platform_definition_ids(base)
    mine = [
        b
        for b in list_bindings(base, universe_id=universe_id, limit=100)
        if b.get("created_by") == owner and b.get("agent_definition_id") in platform_ids
    ]
    if len(mine) > 1:
        raise ValueError("ambiguous platform bindings; refusing to guess")
    if not mine:
        return create_binding(
            base,
            universe_id=universe_id,
            definition_id=did,
            created_by=owner,
            payload=dict(_BINDING_PAYLOAD),
        )
    binding = mine[0]
    config = binding.get("configuration") or {}
    # A binding still on the retired definition, untouched since it was made, is
    # the founder's platform binding as it stands: it is returned as is, its
    # provider_ref intact. The cutover migration re-points it in place
    # (design D10); re-pointing it here would replace its configuration and drop
    # the provider_ref before the new provider is validated (gpt-6-astra, C1).
    expected = (_BINDING_PAYLOAD if binding.get("agent_definition_id") == did
                else RETIRED_BINDING_PAYLOAD)
    canonical = {k: config.get(k) for k in expected} == expected
    extra = set(config) - set(expected) - {"provider_ref"}
    if canonical and not extra:
        return binding
    # Drifted (possibly collaborator-edited): reset to canonical content on the
    # current definition at the exact current revision; a concurrent edit makes
    # this fail closed.
    return update_binding(
        base,
        universe_id=universe_id,
        binding_id=binding["agent_binding_id"],
        expected_revision=int(binding["revision"]),
        updated_by=owner,
        payload=dict(_BINDING_PAYLOAD),
        definition_id=did,
    )


def _quiesce_other_serving(
    base: Path, *, universe_dir: Path, universe_id: str, owner: str, keep: str
) -> None:
    """Exactly one founder serving binding may exist: the app's connect is the
    founder's choice of which one. Other founder-created serving bindings are
    disabled at their exact revision; anyone else's bindings are never touched."""
    from tinyassets.custom_agents import list_bindings
    from tinyassets.provider_serving_binding import set_serving

    for b in list_bindings(base, universe_id=universe_id, limit=100):
        if (
            b.get("created_by") == owner
            and b.get("status") == "serving"
            and b.get("agent_binding_id") != keep
        ):
            set_serving(
                base_path=base,
                universe_dir=universe_dir,
                owner_user_id=owner,
                universe_id=universe_id,
                agent_binding_id=b["agent_binding_id"],
                expected_revision=int(b["revision"]),
                enabled=False,
            )


def ensure_founder_serving(
    *,
    base_path: str | Path,
    universe_dir: str | Path,
    owner_user_id: str,
    universe_id: str,
    service: str,
    model_access: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Point the founder's command center at the just-deposited ``service`` and enable it.

    ``model_access`` is the complete accepted membership for a first binding
    (``{provider: ModelAccess}``), used when the owner has just confirmed the
    exact model list on a connection request. None keeps the legacy
    single-provider binding. It never widens an existing accepted setup.

    Returns a non-secret projection: ``{"status": "serving", "provider", "agent_binding_id",
    "revision"}`` on success, or ``{"status": "held", "reason": ...}`` when the
    serving module refuses (e.g. claude serving not opted in) — never raises for
    an authority refusal, so a deposit's success is reported honestly alongside
    the serving outcome.
    """
    asked = (service or "").strip()
    if not asked:
        return {"status": "held", "reason": "no_service_named"}
    # An alias resolves to its CLI provider; anything else is a compute
    # connection the owner registered, and the binding layer authorizes it.
    provider = _SERVICE_TO_PROVIDER.get(asked.lower(), asked)
    base = Path(base_path)
    from tinyassets.principals import named_principal

    owner = named_principal(owner_user_id)
    uid = (universe_id or "").strip()
    if not owner or not uid:
        return {"status": "held", "reason": "authentication_required"}
    with _gesture_lock(uid):
        # `_ensure_founder_serving_locked` classifies and RETURNS; it does not
        # raise. The provider_not_yours / unknown_provider handlers that used to
        # sit here could never fire, because its own broad catches ran first.
        return _ensure_founder_serving_locked(
            base, universe_dir=universe_dir, owner=owner, uid=uid,
            provider=provider, model_access=model_access,
        )


def _reconnect_manifest(
    base: Path, *, universe_dir: str | Path, owner: str, uid: str, provider: str,
    expected_digest: str,
) -> dict[str, Any]:
    """Renew an accepted source without interpreting renewal as new model consent."""
    from tinyassets.credential_vault import validate_llm_subscription_deposit
    from tinyassets.custom_agents import reconnect_binding_candidates_in_transaction
    from tinyassets.provider_assignment import (
        load_provider_assignment,
        load_provider_assignment_in_transaction,
        provider_assignment_admission,
    )
    from tinyassets.provider_assignment_manifest import ModelAccess
    from tinyassets.provider_serving_binding import (
        _PROVIDER_SERVICE,
        _canonical_universe,
        _current_bound_member_authority,
        _resolve_serving_source,
        _source_custody,
        bind_serving_provider,
        set_serving,
    )
    from tinyassets.storage.current_home import check_current_home
    from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

    universe = _canonical_universe(base, universe_dir, uid)
    store = SQLiteProviderWorkAuthorityStore(base)
    with provider_assignment_admission().shared(universe):
        with store.connection() as conn:
            conn.execute("BEGIN")
            try:
                check_current_home(conn, owner, uid)
                assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
                if (assignment is None or assignment.assignment_digest != expected_digest
                        or assignment.owner_user_id != owner or assignment.state != "ready"
                        or not assignment.manifest_digest):
                    raise PermissionError(
                        "Current model setup is not ready or changed; use model-access "
                        "confirmation to recover it. Existing choices were not replaced."
                    )
                source = _resolve_serving_source(
                    base, uid, owner, provider.removeprefix("api_key_http:"), ModelAccess(),
                )
                if source.provider not in {m.provider for m in assignment.candidates}:
                    raise PermissionError(
                        "This source is not in your accepted model setup; confirm model "
                        "access before adding it. Existing choices were not replaced."
                    )
                matches = reconnect_binding_candidates_in_transaction(
                    conn, universe_id=uid, owner=owner, provider_ref=assignment.binding_id,
                )
                if len(matches) != 1:
                    raise PermissionError("Reconnect requires one unambiguous owner agent.")
                binding = matches[0]
                if (binding["updated_by"] != owner
                        or binding["configuration"].get("provider_ref") != assignment.binding_id):
                    raise PermissionError(
                        "The selected agent was changed; confirm its content before reconnecting."
                    )
                # Do not publish pending and destroy a usable independent member
                # merely because another member is currently unavailable.
                for member in assignment.candidates:
                    member_source = _resolve_serving_source(
                        base, uid, owner, member.provider.removeprefix("api_key_http:"),
                        member.access,
                    )
                    if member.provider != source.provider:
                        _current_bound_member_authority(
                            conn, store=store, universe_dir=universe, base_path=base,
                            owner_user_id=owner, universe_id=uid, assignment=assignment,
                            member=member,
                        )
                    else:
                        # Renewal may replace custody, never a revoked work grant.
                        if not store.validate_in_transaction(
                            conn, binding_id=member.binding_id,
                            binding_generation=member.binding_generation,
                            binding_digest=member.binding_digest, owner_user_id=owner,
                            universe_id=uid, provider=member.provider,
                            operation="converse", role="writer",
                        ):
                            raise PermissionError("Accepted provider binding is no longer valid.")
                        if member_source.open_grant is None:
                            validate_llm_subscription_deposit(
                                conn, universe_dir=universe, owner=owner, uid=uid,
                                service=_PROVIDER_SERVICE[member.provider],
                            )
                        else:
                            _source_custody(
                                conn, member_source, base=base, universe=universe,
                                owner=owner, uid=uid, adopt=False,
                            )
                model_access = {
                    m.provider.removeprefix("api_key_http:"): m.access
                    for m in assignment.candidates
                }
            finally:
                conn.rollback()
    bound = bind_serving_provider(
        base_path=base, universe_dir=universe, owner_user_id=owner, universe_id=uid,
        agent_binding_id=binding["agent_binding_id"], expected_revision=binding["revision"],
        provider=assignment.provider.removeprefix("api_key_http:"), model_access=model_access,
        expected_assignment_digest=expected_digest, require_current_home=True,
    )
    final = bound["agent_binding"]
    if final["status"] != "serving":
        published = load_provider_assignment(base, universe_id=uid)
        enabled = set_serving(
            base_path=base, universe_dir=universe, owner_user_id=owner, universe_id=uid,
            agent_binding_id=final["agent_binding_id"], expected_revision=final["revision"],
            enabled=True, expected_assignment_digest=published.assignment_digest,
            require_current_home=True,
        )
        final = enabled["agent_binding"]
    return {
        "status": "serving", "provider": assignment.provider,
        "agent_binding_id": final["agent_binding_id"], "revision": int(final["revision"]),
        "replayed": bool(bound.get("replayed")),
    }


def _ensure_founder_serving_locked(
    base: Path, *, universe_dir: str | Path, owner: str, uid: str, provider: str,
    model_access: dict[str, Any] | None = None,
) -> dict[str, Any]:
    from tinyassets.provider_serving_binding import (
        ServingProviderNotOwned,
        UnknownServingProvider,
        bind_serving_provider,
        set_serving,
    )

    try:
        _require_current_admin(base, universe_id=uid, owner=owner)
        from tinyassets.provider_assignment import load_provider_assignment

        assignment = load_provider_assignment(base, universe_id=uid)
        if assignment is not None and assignment.manifest_digest:
            return _reconnect_manifest(
                base, universe_dir=universe_dir, owner=owner, uid=uid, provider=provider,
                expected_digest=assignment.assignment_digest,
            )
        binding = _platform_binding(base, universe_id=uid, owner=owner)
        bound = bind_serving_provider(
            base_path=base,
            universe_dir=universe_dir,
            owner_user_id=owner,
            universe_id=uid,
            agent_binding_id=binding["agent_binding_id"],
            expected_revision=int(binding["revision"]),
            provider=provider,
            model_access=model_access,
        )
        after_bind = bound.get("agent_binding") or binding
        enabled = set_serving(
            base_path=base,
            universe_dir=universe_dir,
            owner_user_id=owner,
            universe_id=uid,
            agent_binding_id=after_bind["agent_binding_id"],
            expected_revision=int(after_bind["revision"]),
            enabled=True,
        )
        final = enabled.get("agent_binding") or after_bind
        _quiesce_other_serving(
            base,
            universe_dir=Path(universe_dir),
            universe_id=uid,
            owner=owner,
            keep=final["agent_binding_id"],
        )
        return {
            "status": "serving",
            "provider": provider,
            "agent_binding_id": final["agent_binding_id"],
            "revision": int(final["revision"]),
        }
    except ServingProviderNotOwned as exc:
        # Classified BEFORE the broad catches below: they used to swallow both of
        # these into provider_authority_denied / binding_invalid, which made the
        # documented provider_not_yours + unknown_provider contract dead code.
        return {
            "status": "held", "reason": "provider_not_yours",
            "detail": str(exc), "provider": provider,
        }
    except UnknownServingProvider as exc:
        return {
            "status": "held", "reason": "unknown_provider",
            "detail": str(exc), "provider": provider,
        }
    except PermissionError as exc:
        return {"status": "held", "reason": "provider_authority_denied", "detail": str(exc)}
    except (ValueError, LookupError) as exc:
        return {"status": "held", "reason": "binding_invalid", "detail": str(exc)}
    except Exception as exc:  # noqa: BLE001 - never let serving setup mask a successful deposit
        return {"status": "held", "reason": "serving_setup_failed", "detail": type(exc).__name__}


__all__ = ["ensure_founder_serving", "PLATFORM_DEFINITION_AUTHOR", "PLATFORM_DEFINITION_KEY"]
