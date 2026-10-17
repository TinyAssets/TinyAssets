"""Owned, unpowered model catalogue through the shared connector read surface."""

import sqlite3
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

from tinyassets.api import permissions
from tinyassets.api.helpers import _base_path
from tinyassets.commons_catalog import serving_binding_candidates
from tinyassets.exceptions import ProviderError
from tinyassets.provider_assignment import (
    accepted_connected_model_access,
    load_provider_assignment_in_transaction,
    provider_assignment_admission,
)
from tinyassets.provider_assignment_manifest import ModelAccess
from tinyassets.provider_serving_binding import (
    _canonical_universe,
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
from tinyassets.providers.served_model_plan import (
    ModelSourceUnavailable,
    _assert_plan_snapshot,
    _http_models,
    connected_sources,
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
            live_sources = connected_sources(base, universe, owner, conn=conn)
            legacy_provider = None
            if agent is not None and assignment is not None and not assignment.manifest_digest:
                if assignment.provider in live_sources:
                    legacy_provider = assignment.provider
    binding_state = ("serving" if agent else "no_serving_binding" if not bindings
                     else "ambiguous_serving_binding")
    accepted = {} if assignment is None else {
        member.provider: member.access for member in assignment.candidates
    }
    # What may run: the confirmed manifest, or the legacy single-provider lane
    # (its configured model, free-only). `accepted_model_access` below stays the
    # manifest alone: the legacy lane is reported as `legacy_source`, not as a
    # confirmed model setup.
    runnable = dict(accepted)
    if legacy_provider is not None:
        try:
            runnable[legacy_provider] = accepted_connected_model_access(
                assignment, owner, legacy_provider,
            )
        except PermissionError:
            pass
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
    planned_sources = {item.connection_id for item in models}
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
                             "accepted": provider in runnable, "reasons": []}
        if provider in planned_sources:
            continue
        try:
            snapshot = refresh_model_discovery(
                owner_user_id=owner, universe_id=uid, definition_id=item.id,
            )
            snapshots[provider] = snapshot
            models.append(snapshot.models)
            reason = "source_not_accepted" if provider not in runnable else binding_state
            rejected.append(Ineligible(ModelRef(provider, ""), reason, scope="source"))
            _, _, _, _, denied = _http_models(owner, uid, SimpleNamespace(
                provider=provider, access=runnable.get(provider, ModelAccess("discovered")),
            ), snapshot=snapshot)
            rejected.extend(denied)
        except (ModelDiscoveryUnavailable, ModelSourceUnavailable) as exc:
            rejected.append(Ineligible(ModelRef(provider, ""), exc.reason, scope="source"))
        except (ProviderError, ValueError, OSError, RuntimeError):
            rejected.append(Ineligible(ModelRef(provider, ""), "discovery_unavailable",
                                       scope="source"))
    # Accepted sources missing from registration remain visible as unavailable.
    for provider in accepted:
        sources.setdefault(provider, {"provider_ref": provider,
                                      "access_method": None,
                                      "bind_key": provider.removeprefix("api_key_http:"),
                                      "accepted": provider in runnable, "reasons": []})
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
            if (legacy_provider is not None and legacy_provider not in
                    connected_sources(base, universe, owner, conn=conn)):
                failed[legacy_provider] = "source_revoked"
            for provider, snapshot in snapshots.items():
                try:
                    _assert_plan_snapshot(
                        snapshot,
                        display_only=(prepared is not None and prepared.display_only
                                      and provider in dict(prepared.chains)),
                    )
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
                "warnings": list(snapshot.warnings),
            })

    legacy_source = None
    if legacy_provider is not None and legacy_provider not in failed:
        provider = legacy_provider
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
                             if legacy_provider and legacy_provider not in failed else "none"),
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
