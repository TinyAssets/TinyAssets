"""Owned, unpowered model catalogue through the shared connector read surface."""

import sqlite3
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from tinyassets.api import permissions
from tinyassets.api.helpers import _base_path
from tinyassets.credential_vault import current_llm_subscription_custody
from tinyassets.custom_agents import serving_binding_candidates
from tinyassets.exceptions import ProviderError
from tinyassets.provider_assignment import (
    load_provider_assignment_in_transaction,
    provider_assignment_admission,
)
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import (
    _PROVIDER_SERVICE,
    ServingProviderHeld,
    _canonical_universe,
    _current_serving_authority,
)
from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.definition import list_definitions
from tinyassets.providers.discovery_snapshot import (
    ModelDiscoveryUnavailable,
    refresh_model_discovery,
)
from tinyassets.providers.model_options import model_options_document
from tinyassets.providers.model_policy import (
    Catalog,
    Ineligible,
    Interaction,
    ModelPolicy,
    ModelRef,
)
from tinyassets.providers.model_preferences import capture_preference_policy
from tinyassets.providers.native_discovery import NativeDiscoverySnapshot
from tinyassets.providers.served_model_plan import (
    ModelSourceUnavailable,
    _assert_plan_snapshot,
    _http_models,
    _native_models,
    prepare_owned_model_plan,
)
from tinyassets.storage.current_home import CurrentHomeChanged, check_current_home
from tinyassets.storage.model_preferences import _read
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore


def _scope(conn, base, owner, uid):
    from tinyassets.api.first_contact import home_is_complete

    check_current_home(conn, owner, uid)
    if not home_is_complete(base, uid) or conn.execute(
        "SELECT 1 FROM universe_acl "
        "WHERE universe_id = ? AND actor_id = ? AND permission = 'admin'",
        (uid, owner),
    ).fetchone() is None:
        raise PermissionError("model catalogue scope unavailable")


def _granted_only(connection):
    """The connection with LEARNED candidates removed.

    `_native_models` returns everything a client may SEE, which now includes ids
    the platform verified on another universe of the same source kind. Those are
    offers to grant; an admitted plan must contain only what this owner granted.
    """
    from dataclasses import replace as _replace

    from tinyassets.providers.served_model_plan import _CANDIDATE_ONLY_BASES

    return _replace(connection, models=tuple(
        model for model in connection.models
        if model.availability_basis not in _CANDIDATE_ONLY_BASES
    ))


def _native_inventory(conn, universe, owner):
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master "
        "WHERE type = 'table' AND name = 'llm_credential_deposit_owners'",
    ).fetchone()
    if exists is None:
        return {}
    aliases = {service: provider for provider, service in _PROVIDER_SERVICE.items()}
    rows = conn.execute(
        "SELECT service FROM llm_credential_deposit_owners "
        "WHERE universe_id = ? AND owner_user_id = ?",
        (universe.name, owner),
    ).fetchall()
    return {
        aliases[row[0]]: current_llm_subscription_custody(
            conn, universe_dir=universe, owner_user_id=owner,
            universe_id=universe.name, service=row[0],
        ) for row in rows if row[0] in aliases
    }


def _native_enumeration(provider):
    """Installed metadata support, not proof this account has a model list."""
    from tinyassets.providers.base import BaseProvider
    from tinyassets.providers.call import get_provider_router

    router = get_provider_router()
    executor = None if router is None else router._providers.get(provider)
    if executor is None:
        return "unavailable"
    method = getattr(executor, "enumerate_models", None)
    supported = (getattr(executor, "native_discovery_protocol", None) is not None
                 or callable(method)
                 and getattr(method, "__func__", method) is not BaseProvider.enumerate_models)
    return "supported" if supported else "unknown"


def _collect(base, owner, uid):
    universe = _canonical_universe(base, base / uid, uid)
    store = SQLiteProviderWorkAuthorityStore(base)
    with provider_assignment_admission().shared(universe):
        bindings = serving_binding_candidates(base, universe_id=uid, owner_user_id=owner)
        agent = bindings[0] if len(bindings) == 1 else None
        with store.connection() as conn:
            conn.execute("BEGIN")
            _scope(conn, base, owner, uid)
            preferences = _read(conn, owner, uid)
            assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
            if assignment is not None and assignment.owner_user_id != owner:
                raise PermissionError("model catalogue assignment unavailable")
            native = _native_inventory(conn, universe, owner)
            legacy = None
            if agent is not None and assignment is not None and not assignment.manifest_digest:
                try:
                    legacy = _current_serving_authority(
                        conn, store=store, universe_dir=universe, base_path=base,
                        owner_user_id=owner, universe_id=uid, agent=agent,
                    )
                except PermissionError:
                    pass
    binding_state = ("serving" if agent else "no_serving_binding" if not bindings
                     else "ambiguous_serving_binding")
    accepted = {} if assignment is None else {
        member.provider: member.access for member in assignment.candidates
    }
    prepared = None
    if agent is not None and assignment is not None and assignment.manifest_digest:
        from tinyassets.config import load_universe_config

        prepared = prepare_owned_model_plan(
            base=base, universe=universe, owner=owner, agent=agent,
            config=load_universe_config(universe), allow_empty=True,
        )
    captured = capture_preference_policy(
        saved=preferences.policy, observed_generation=preferences.generation,
    ) or (ModelPolicy(0, "automatic", ()), "automatic")
    plan = (prepared.plan if prepared else AgentModelPlan(
        Catalog(owner, uid, ()), captured[0],
        Interaction(True, frozenset({"text"}), frozenset()), captured[1],
    ))
    models = list(prepared.catalog.connections) if prepared else []
    rejected = list(prepared.ineligible) if prepared else []
    snapshots = {item.provider: item for item in prepared.snapshots} if prepared else {}
    sources = {}
    definitions = [item for item in list_definitions(uid) if item.owner_user_id == owner]
    for item in definitions:
        if item.access_method != "api_key_http":
            continue
        provider = "api_key_http:" + item.id
        sources[provider] = {"provider_ref": provider, "bind_key": item.id,
                             "access_method": "api_key_http",
                             "accepted": provider in accepted, "reasons": []}
        if prepared is not None and provider in accepted:
            continue
        try:
            snapshot = refresh_model_discovery(
                owner_user_id=owner, universe_id=uid, definition_id=item.id,
            )
            snapshots[provider] = snapshot
            models.append(snapshot.models)
            reason = "source_not_accepted" if provider not in accepted else binding_state
            rejected.append(Ineligible(ModelRef(provider, ""), reason, scope="source"))
            _, _, _, _, denied = _http_models(owner, uid, SimpleNamespace(
                provider=provider, access=accepted.get(provider, ModelAccess("discovered")),
            ), snapshot=snapshot)
            rejected.extend(denied)
        except (ModelDiscoveryUnavailable, ModelSourceUnavailable) as exc:
            rejected.append(Ineligible(ModelRef(provider, ""), exc.reason, scope="source"))
        except (ProviderError, ValueError, OSError, RuntimeError):
            rejected.append(Ineligible(ModelRef(provider, ""), "discovery_unavailable",
                                       scope="source"))
    for provider, custody in native.items():
        sources[provider] = {"provider_ref": provider, "bind_key": provider,
                             "access_method": "subscription_cli",
                             "enumeration": _native_enumeration(provider),
                             "accepted": provider in accepted, "reasons": []}
        if prepared is not None and provider in accepted:
            continue
        try:
            model = _native_models(base, universe, owner, SimpleNamespace(
                provider=provider, access=ModelAccess("explicit", ("",)),
            ))
            if custody is None:
                # Deposited but not adopted is not evidence of revocation or
                # fresh execution authority. Keep the default visible as unknown.
                models.append(replace(model, freshness="unknown"))
                rejected.append(Ineligible(ModelRef(provider, ""),
                                          "source_not_accepted" if provider not in accepted
                                          else "source_revoked", scope="source"))
                continue
            models.append(model)
            if legacy is not None and legacy[0].provider == provider and (
                preferences.policy is None or preferences.policy.mode == "automatic"
            ):
                # The LEGACY plan gets only what the owner actually granted. A
                # learned id is a candidate to grant, never an admitted one, and
                # Codex found this second caller still admitting them after the
                # served_model_plan branch was fixed (#4028 round 2) -- one reader
                # of _native_models was corrected and this one was not.
                admitted = _granted_only(model)
                plan = replace(plan, catalog=Catalog(owner, uid, (admitted,)))
                # Withheld is not enough: each excluded row needs the REASON, or the
                # picker cannot file it under "needs access" and the owner sees an
                # unexplained inert row. Codex on #4028 found this path filtering
                # correctly and reporting nothing (`reasons: []`).
                for excluded in model.models:
                    if excluded.model_id not in {m.model_id for m in admitted.models}:
                        rejected.append(Ineligible(
                            ModelRef(provider, excluded.model_id),
                            "model_access_optin_required"))
            else:
                rejected.append(Ineligible(ModelRef(provider, ""),
                                          "source_not_accepted" if provider not in accepted
                                          else binding_state, scope="source"))
        except (ModelSourceUnavailable, ServingProviderHeld) as exc:
            rejected.append(Ineligible(ModelRef(provider, ""), exc.reason, scope="source"))
        except (PermissionError, ValueError, ProviderError, OSError):
            rejected.append(Ineligible(ModelRef(provider, ""), "source_unavailable",
                                       scope="source"))
    # Accepted sources missing from registration remain visible as unavailable.
    for provider in accepted:
        sources.setdefault(provider, {"provider_ref": provider,
                                      "access_method": None,
                                      "bind_key": provider.removeprefix("api_key_http:"),
                                      "accepted": True, "reasons": []})
    failed = {}
    with provider_assignment_admission().shared(universe):
        current_bindings = serving_binding_candidates(base, universe_id=uid, owner_user_id=owner)
        with store.connection() as conn:
            conn.execute("BEGIN")
            _scope(conn, base, owner, uid)
            current_assignment = load_provider_assignment_in_transaction(conn, universe_id=uid)
            if (current_bindings != bindings or _read(conn, owner, uid) != preferences
                    or current_assignment != assignment):
                raise PermissionError("model catalogue changed during refresh")
            if prepared:
                current = prepared.recheck_display(
                    conn, store=store, base=base, universe=universe, owner=owner, agent=agent,
                )
                plan = current.plan
                rejected.extend(current.ineligible)
                retained = {item.connection_id for item in current.catalog.connections}
                for item in prepared.catalog.connections:
                    if item.connection_id not in retained:
                        failed[item.connection_id] = "source_revoked"
            if legacy is not None:
                try:
                    if _current_serving_authority(
                        conn, store=store, universe_dir=universe, base_path=base,
                        owner_user_id=owner, universe_id=uid, agent=agent,
                    ) != legacy:
                        failed[legacy[0].provider] = "source_revoked"
                except PermissionError:
                    failed[legacy[0].provider] = "source_revoked"
            current_native = _native_inventory(conn, universe, owner)
            for provider, custody in native.items():
                if provider not in current_native or current_native[provider] != custody:
                    failed[provider] = "source_revoked"
            for provider, snapshot in snapshots.items():
                try:
                    _assert_plan_snapshot(
                        snapshot,
                        display_only=(prepared is not None and prepared.display_only
                                      and provider in dict(prepared.chains)),
                    )
                    if (type(snapshot) is NativeDiscoverySnapshot
                            and current_native.get(provider) != snapshot.custody):
                        failed[provider] = "source_revoked"
                except ModelDiscoveryUnavailable as exc:
                    failed[provider] = exc.reason
                except ProviderError:
                    failed[provider] = "discovery_unavailable"
    plan = replace(plan, catalog=replace(plan.catalog, connections=tuple(
        item for item in plan.catalog.connections if item.connection_id not in failed
    )), source_policies=tuple(
        item for item in plan.source_policies if item.connection_id not in failed
    ))
    models = tuple(item for item in models if item.connection_id not in failed)
    rejected.extend(Ineligible(ModelRef(provider, ""), reason, scope="source")
                    for provider, reason in failed.items())
    for item in rejected:
        source = sources.get(item.ref.connection_id)
        if source is not None and item.reason not in source["reasons"]:
            source["reasons"].append(item.reason)
    for provider, snapshot in snapshots.items():
        if provider in sources and provider not in failed:
            sources[provider].update({
                "observed_at": snapshot.observed_at.isoformat(),
                "completed_at": snapshot.completed_at.isoformat(),
                "expires_at": (snapshot.observed_at + timedelta(minutes=5)).isoformat(),
                "warnings": ([] if type(snapshot) is NativeDiscoverySnapshot
                             else list(snapshot.warnings)),
            })
    legacy_source = None
    if legacy is not None and legacy[0].provider not in failed:
        provider = legacy[0].provider
        source = sources.get(provider)
        configured_model = ""
        if provider.startswith("api_key_http:"):
            registered = next((item for item in definitions
                               if "api_key_http:" + item.id == provider), None)
            if registered is not None:
                configured_model = registered.model
            else:
                source = None
        if source is not None:
            legacy_source = {"provider_ref": provider, "bind_key": source["bind_key"],
                             "access_method": source["access_method"],
                             "model_id": configured_model}
    return {
        "version": 1, "universe_id": uid, "advisory": True,
        "legacy_source": legacy_source,
        "preferences": preferences.document(), "binding_state": binding_state,
        "binding": None if agent is None else {
            "id": agent["agent_binding_id"], "revision": agent["revision"],
        },
        "choice_authority": ("accepted_manifest" if assignment and assignment.manifest_digest
                             else "legacy_single_provider"
                             if legacy and legacy[0].provider not in failed else "none"),
        "accepted_model_access": {provider.removeprefix("api_key_http:"): access.document()
                                  for provider, access in accepted.items()},
        "sources": list(sources.values()),
        **model_options_document(Catalog(owner, uid, models), plan, tuple(rejected)),
    }


def read_model_options(*, universe_id=""):
    """Read only the authenticated owner's complete current home; never bootstrap."""
    from tinyassets.api.first_contact import home_is_complete
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.principals import named_principal

    if not permissions.is_authenticated_request():
        return {"error": "authentication_required", "resource": "model_options"}
    owner = named_principal(permissions.current_actor_id())
    if not owner:
        return {"error": "authentication_required", "resource": "model_options"}
    try:
        base = _base_path()
        home = get_founder_home(base, owner)
        if not home or not home_is_complete(base, home):
            return {"error": "no_home_universe"}
        if universe_id and universe_id != home:
            return {"error": "not_found", "resource": "model_options"}
        return _collect(base, owner, home)
    except (PermissionError, CurrentHomeChanged):
        return {"error": "not_found", "resource": "model_options"}
    except (ValueError, RuntimeError, OSError, ProviderError, sqlite3.Error):
        return {"error": "model_options_unavailable"}
