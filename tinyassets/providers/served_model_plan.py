"""Build a conversation plan from current owned bindings, never caller facts.

Discovery happens outside assignment admission and database transactions. The
result is advisory: every actual launch still validates its exact member anew.
"""

import logging
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from tinyassets.commons_catalog import get_binding
from tinyassets.exceptions import ProviderError, SelectedModelUnavailableError
from tinyassets.provider_assignment import (
    _served_request_agent,
    accepted_connected_model_access,
    load_provider_assignment_in_transaction,
    provider_assignment_admission,
)
from tinyassets.provider_serving_binding import (
    ServingProviderHeld,
    current_connected_source_custody,
)
from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.discovery_snapshot import ModelDiscoveryUnavailable
from tinyassets.providers.model_policy import (
    Catalog,
    Charge,
    Ineligible,
    Interaction,
    Model,
    ModelPolicy,
    ModelRef,
    Pricing,
    SourceModelPolicy,
    order_models,
)
from tinyassets.providers.model_preferences import ModelPreferences, capture_preference_policy
from tinyassets.providers.wire_dialects import same_dialect
from tinyassets.storage.current_home import check_plan_scope, current_home
from tinyassets.storage.learned_models import (
    OWN_VERIFIED_BASIS,
)
from tinyassets.storage.model_preferences import ModelPreferenceStore, PreferenceSnapshot
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

_LOG = logging.getLogger("universe_server.served_model_plan")

#: An id from the reviewed public list for this source kind. A public claim that the
#: id EXISTS, never permission to use it.
PUBLIC_LISTED_BASIS = "publicly_listed"

#: Evidence alone cannot grant access. Subscription history is not provider-keyed;
#: only this provider's catalogue or an explicit grant can make its ids selectable.
_CANDIDATE_ONLY_BASES = frozenset({PUBLIC_LISTED_BASIS, OWN_VERIFIED_BASIS})


def connected_sources(base: str | Path, universe: str | Path, owner: str,
                      *, conn=None) -> frozenset[str]:
    """Return this owner's live model sources for one command center.

    The set is rebuilt from custody and broker grants on each launch. A saved
    serving preference or another owner's definition never grants membership.
    """
    from tinyassets.broker.client import BrokerRefused
    from tinyassets.broker.ledger_queries import HAS_MODEL_SOURCE, query_ledger
    from tinyassets.providers.definition import list_definitions
    from tinyassets.storage.outbound_connections import GrantResolutionError, ProxyRequestError

    base, universe = Path(base), Path(universe)
    if (universe.resolve(strict=False) != (base.resolve(strict=False) / universe.name)
            or not owner):
        raise PermissionError("invalid connected-source scope")
    sources: set[str] = set()
    for definition in list_definitions(universe.name):
        if definition.owner_user_id != owner or definition.access_method != "api_key_http":
            continue
        try:
            facts = query_ledger(
                base, query=HAS_MODEL_SOURCE, principal=owner,
                command_center=universe.name, grant_id=definition.ref,
            )
        except (GrantResolutionError, BrokerRefused, ProxyRequestError, ValueError, OSError):
            # One unavailable HTTP grant cannot withhold an independent
            # subscription source. An HTTP call still fails membership below.
            continue
        if facts.get("model_source") is True:
            sources.add("api_key_http:" + definition.id)
    return frozenset(sources)



#: Prefix of the refusal when no accepted model can run. The served turn's
#: notice reads the held sources after ``HELD_SOURCES`` (universe_server).
NO_ELIGIBLE_MODEL = "no eligible model in the connected sources"
HELD_SOURCES = "not usable now: "

def _assert_plan_snapshot(snapshot, *, display_only=False):
    from tinyassets.providers.discovery_snapshot import assert_discovery_snapshot_current

    assert_discovery_snapshot_current(snapshot)


@dataclass(frozen=True, slots=True)
class PreparedPlan:
    plan: AgentModelPlan
    catalog: Catalog  # Unfiltered choices for display, not execution permission.
    ineligible: tuple[Ineligible, ...]
    assignment: object
    chains: tuple
    agent: dict
    preferences: object
    snapshots: tuple
    display_only: bool = False
    #: Captured for the owner's current home, where saved preferences apply.
    home_scoped: bool = True

    def recheck(self, conn, *, store, base, universe, owner, agent, check_preferences=False):
        if self.display_only:
            raise ValueError("display catalogue cannot authorize activation")
        self.recheck_scope(conn, universe=universe, owner=owner, agent=agent,
                           check_preferences=check_preferences)
        for provider, expected in self.chains:
            actual = current_connected_source_custody(
                conn, base=base, universe=universe, owner=owner, provider=provider,
            )
            if actual != expected[1]:
                raise PermissionError("model source changed during discovery")
        for snapshot in self.snapshots:
            _assert_plan_snapshot(snapshot)

    def recheck_scope(self, conn, *, universe, owner, agent, check_preferences=False):
        """A changed home or agent invalidates the captured plan."""
        from tinyassets.storage.model_preferences import _read

        check_plan_scope(conn, owner, universe.name, home_scoped=self.home_scoped)
        if agent != self.agent:
            raise PermissionError("model agent changed during discovery")
        if (check_preferences and self.home_scoped
                and _read(conn, owner, universe.name) != self.preferences):
            raise PermissionError("model preferences changed during activation")
    def recheck_display(self, conn, *, store, base, universe, owner, agent):
        """Demote only failed sources; never let them hide independent choices."""
        self.recheck_scope(conn, universe=universe, owner=owner, agent=agent,
                           check_preferences=True)
        failed = {}
        for provider, expected in self.chains:
            try:
                actual = current_connected_source_custody(
                    conn, base=base, universe=universe, owner=owner, provider=provider,
                )
                if actual != expected[1]:
                    failed[provider] = "source_revoked"
            except PermissionError:
                failed[provider] = "source_revoked"
        for snapshot in self.snapshots:
            try:
                _assert_plan_snapshot(snapshot, display_only=self.display_only)
            except ModelDiscoveryUnavailable as exc:
                failed[snapshot.provider] = exc.reason
            except ProviderError:
                failed[snapshot.provider] = "discovery_unavailable"
        if not failed:
            return self

        def retained(catalog):
            return replace(catalog, connections=tuple(
                item for item in catalog.connections if item.connection_id not in failed
            ))

        return replace(
            self, catalog=retained(self.catalog),
            plan=replace(self.plan, catalog=retained(self.plan.catalog), source_policies=tuple(
                item for item in self.plan.source_policies if item.connection_id not in failed
            )),
            ineligible=self.ineligible + tuple(
                Ineligible(ModelRef(provider, ""), reason, scope="source")
                for provider, reason in failed.items()
            ),
            chains=tuple(item for item in self.chains if item[0] not in failed),
            snapshots=tuple(item for item in self.snapshots if item.provider not in failed),
        )


class ModelSourceUnavailable(PermissionError):
    """Non-executable source with a fixed reason, never upstream error prose."""

    def __init__(self, reason):
        if reason not in {
            "executor_unavailable", "protocol_mismatch", "price_components_unenforceable",
            "price_contract_incompatible",
            "model_access_optin_required",
        }:
            raise ValueError("invalid model source reason")
        super().__init__(reason)
        self.reason = reason




def _own_verified_candidates(base, source_kind, provider_ref, owner, *, already,
                             excluded=frozenset()):
    """Ids THIS owner has already made work on this source.

    Their own history, not anyone else's: read from the private evidence table
    scoped to this owner, so nothing here depends on the promotion threshold. A
    solo user keeps every id they have ever used even though none of it is public.

    Carries its own basis so the list distinguishes owner history from reviewed
    public ids. Like the published rows, these do not grant access: the owner may
    have narrowed their scope since. Display
    retention requires a non-hidden row in this provider's last catalogue under
    current custody, while refresh is pending or failed. Such rows are already
    included as executor_enumerated, so history-only rows remain offers. A fresh
    catalogue is authoritative; execution always needs fresh discovery.
    """
    from tinyassets.storage.learned_models import OwnModelHistory

    have = {model.model_id for model in already} | set(excluded)
    try:
        rows = OwnModelHistory(base).ids_for(source_kind, provider_ref, owner)
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        _LOG.warning("own verified model evidence unreadable: %s", type(exc).__name__)
        return ()
    return tuple(
        Model(row.model_id, True, frozenset({"text"}),
              pricing=Pricing("fresh", unmetered=True),
              availability_basis=OWN_VERIFIED_BASIS)
        for row in rows if row.model_id and row.model_id not in have
    )


def _listed_candidates(source_kind, *, source_key, already, excluded=frozenset()):
    """Newest-per-class ids from the reviewed public list for one source kind.

    A UNION, never a filter: an id this universe already has keeps its own row and its
    own basis, whatever the list says. A user happily running an older model does not
    lose it because a newer one was listed.

    A malformed list file RAISES rather than reading as empty -- a corrupted list must
    not silently shrink every user's picker with no signal -- and the caller's own
    except clause turns that into a source-level reason the repair screen can show.
    """
    from tinyassets.providers.public_model_lists import newest_listed_cached

    have = {model.model_id for model in already} | set(excluded)
    return tuple(
        Model(model_id, True, frozenset({"text"}),
              pricing=Pricing("fresh", unmetered=True),
              availability_basis=PUBLIC_LISTED_BASIS)
        for model_id in newest_listed_cached(source_kind, source_key=source_key)
        if model_id and model_id not in have
    )


def _http_models(owner, uid, member, *, snapshot=None, needs_tools=True):
    from tinyassets.providers.definition import get_definition
    from tinyassets.providers.discovery_snapshot import refresh_model_discovery

    if not member.provider.startswith("api_key_http:") or member.access.model_scope == "legacy":
        raise ModelSourceUnavailable("model_access_optin_required")
    if snapshot is None:
        snapshot = refresh_model_discovery(
            owner_user_id=owner, universe_id=uid,
            definition_id=member.provider.removeprefix("api_key_http:"),
        )
    contract = snapshot.contract()
    definition = get_definition(uid, member.provider.removeprefix("api_key_http:"))
    if (definition is None or definition.owner_user_id != owner
            or not same_dialect(definition.protocol, contract.inference_protocol)):
        raise ModelSourceUnavailable("protocol_mismatch")
    caps = member.access.cost_caps
    if caps is None:
        caps = tuple((name, 0) for name in sorted(contract.price_components))
    if {name for name, _ in caps} != contract.price_components:
        raise ModelSourceUnavailable("price_components_unenforceable")
    interaction = replace(contract.text_interaction, needs_tools=needs_tools)
    order = order_models(
        Catalog(owner, uid, (snapshot.models,)),
        ModelPolicy(0, "automatic", (), cost_caps=tuple(Charge(k, v, True) for k, v in caps)),
        interaction, owner_id=owner, universe_id=uid,
    )
    eligible = {candidate.ref.model_id for candidate in order.candidates}
    rejected = list(order.ineligible)
    models = []
    for model in snapshot.models.models:
        ref = ModelRef(member.provider, model.model_id)
        if (member.access.model_scope == "explicit"
                and model.model_id not in member.access.model_ids):
            rejected.append(Ineligible(ref, "outside_accepted_model_scope"))
            continue
        if model.model_id not in eligible:
            continue
        try:
            contract.constrain_inference({"model": model.model_id, "messages": []}, caps)
        except ValueError:
            rejected.append(Ineligible(ref, "unsupported_model_indirection"))
            continue
        models.append(model)
    return snapshot, replace(snapshot.models, models=tuple(models)), interaction, caps, rejected


def _reconnect_sources(base, owner, uid, chains):
    from tinyassets.providers.source_health import SOURCE_HEALTH, source_key

    return tuple(
        provider for provider, chain in chains
        if SOURCE_HEALTH.needs_reconnect(source_key(
            base, owner, uid, chain[0],
        ))
    )


def _refused_models(base, owner, sources):
    """This owner's recently refused models on the sources in this plan."""
    from tinyassets.storage.refused_models import active_refused_models

    providers = set(sources)
    return tuple(
        ModelRef(mark.connection_id, mark.model_id)
        for mark in active_refused_models(base, owner_user_id=owner)
        if mark.connection_id in providers
    )


def prepare_owned_model_plan(
    *, base, universe, owner, agent, current=None, config=None, allow_empty=False,
    preference_snapshot=None, needs_tools=True,
):
    """Private composition for authenticated ingress and serving readiness.

    Caller must verify its principal and exact agent first. No principal is
    inferred from preferences. The plan is advisory; dispatch checks live
    connected custody again. allow_empty permits advisory display.
    """
    if type(needs_tools) is not bool:
        raise ValueError("invalid conversation tool mode")
    base, universe = Path(base), Path(universe)
    store = SQLiteProviderWorkAuthorityStore(base)
    # Saved preferences, model choice and a confirmed model setup all belong to
    # the owner's current home: `apply_served_model_preferences` refuses a
    # choice anywhere else, the preference writers require the home, and model
    # access is confirmed on the home-only surface, so a manifest stays bound to
    # the home it was accepted in. A command center that is not the home and
    # has no manifest -- a second one the owner created, or a first connect
    # before any home row exists -- plans automatically over the owner's
    # connected sources with no preference read, which is what enabling serving
    # there always did. Read here, not through the serving-side home helper:
    # planning is discovery configuration, which must not need a powered agent.
    with store.connection() as conn:
        conn.execute("BEGIN")
        accepted = load_provider_assignment_in_transaction(conn, universe_id=universe.name)
        home_scoped = (current_home(conn, owner) == universe.name
                       or bool(accepted is not None and accepted.manifest_digest))
        conn.rollback()
    if preference_snapshot is None:
        preferences = ModelPreferenceStore(base).get(
            owner, universe.name, require_current_home=True,
        ) if home_scoped else PreferenceSnapshot()
    else:
        if type(preference_snapshot) is not PreferenceSnapshot or allow_empty:
            raise ValueError("invalid captured model preferences")
        preferences = preference_snapshot
    captured = capture_preference_policy(
        saved=preferences.policy, observed_generation=preferences.generation, current=current,
    )
    with provider_assignment_admission().shared(universe):
        observed_agent = get_binding(
            base, universe_id=universe.name, binding_id=agent["agent_binding_id"],
        )
        if observed_agent != agent or agent["created_by"] != owner:
            raise PermissionError("agent binding changed")
        with store.connection() as conn:
            conn.execute("BEGIN")
            check_plan_scope(conn, owner, universe.name, home_scoped=home_scoped)
            assignment = load_provider_assignment_in_transaction(conn, universe_id=universe.name)
            from types import SimpleNamespace

            chains, rejected = [], []
            for provider in sorted(connected_sources(base, universe, owner, conn=conn)):
                try:
                    custody = current_connected_source_custody(
                        conn, base=base, universe=universe, owner=owner, provider=provider,
                    )
                except PermissionError:
                    rejected.append(Ineligible(ModelRef(provider, ""), "source_revoked",
                                               scope="source"))
                else:
                    try:
                        access = accepted_connected_model_access(assignment, owner, provider)
                    except PermissionError:
                        rejected.append(Ineligible(ModelRef(provider, ""), "source_not_accepted",
                                                   scope="source"))
                        continue
                    member = SimpleNamespace(
                        provider=provider, access=access,
                        credential_reference_id=custody.reference_id,
                        credential_reference_generation=custody.generation,
                        credential_reference_digest=custody.reference_digest,
                    )
                    chains.append((provider, (member, custody)))
    if captured is None:
        captured = ModelPolicy(0, "automatic", ()), "automatic"
    policy, source = captured
    all_models, admitted, snapshots, source_policies = [], [], [], []
    interaction = Interaction(needs_tools, frozenset({"text"}), frozenset())
    ranking_sources = set()
    for provider, chain in chains:
        member = chain[0]
        try:
            from tinyassets.providers.discovery_snapshot import refresh_model_discovery

            snapshot = refresh_model_discovery(
                owner_user_id=owner, universe_id=universe.name,
                definition_id=provider.removeprefix("api_key_http:"),
            )
            # Discovery facts remain useful to a repair screen even when
            # authority, pricing or the agent's contract excludes execution.
            all_models.append(snapshot.models)
            snapshots.append(snapshot)
            snapshot, filtered, required, caps, denied = _http_models(
                owner, universe.name, member, snapshot=snapshot, needs_tools=needs_tools,
            )
            from tinyassets.universe_intelligence import _engine_mcp_enabled

            if needs_tools and not _engine_mcp_enabled():
                denied.extend(Ineligible(ModelRef(provider, model.model_id),
                                         "engine_tools_unavailable")
                              for model in filtered.models)
                filtered = replace(filtered, models=())
            rejected.extend(denied)
            benchmark = snapshot.contract().ranking_source
            if benchmark is not None:
                ranking_sources.add(benchmark)
        except (ModelDiscoveryUnavailable, ModelSourceUnavailable, ServingProviderHeld) as exc:
            rejected.append(Ineligible(ModelRef(provider, ""), exc.reason, scope="source"))
            continue
        except (PermissionError, ValueError, RuntimeError, OSError, ProviderError):
            rejected.append(Ineligible(ModelRef(provider, ""), "discovery_unavailable",
                                       scope="source"))
            continue
        admitted.append(filtered)
        source_policies.append(SourceModelPolicy(
            provider, required,
            None if caps is None else tuple(Charge(k, v, True) for k, v in caps),
        ))
    from tinyassets.providers.default_model_evidence import cooling_sources, with_request_capacity

    admitted = [with_request_capacity(base, owner, universe, c) for c in admitted]
    policy = replace(
        policy,
        ranking_source=next(iter(ranking_sources)) if len(ranking_sources) == 1 else None,
    )
    plan = AgentModelPlan(
        Catalog(owner, universe.name, tuple(admitted)), policy, interaction, source,
        tuple(source_policies),
        _reconnect_sources(base, owner, universe.name, chains),
        _refused_models(base, owner, (provider for provider, _chain in chains)),
        cooling_sources(owner, admitted),
    )
    if not allow_empty and plan.next_candidate(owner, universe.name) is None:
        pinned = policy.current_selection or policy.saved_default
        # Name what is held and why: "no model connected" was wrong for an owner
        # whose accepted sources exist but cannot run (live 2026-09-28).
        held = [
            f"{item.ref.connection_id.removeprefix('api_key_http:')} "
            f"({item.reason.replace('_', ' ')})"
            for item in rejected if item.scope == "source"
        ]
        pin_detail = ""
        missing_on_usable_source = False
        if pinned is not None:
            reason = next((item.reason for item in plan.order(owner, universe.name).ineligible
                           if item.ref == pinned), "not_connected")
            missing_on_usable_source = (
                reason == "absent_from_catalogue"
                and any(connection.connection_id == pinned.connection_id and connection.models
                        for connection in admitted)
            )
            pin_detail = (f"; model {pinned.connection_id}/{pinned.model_id} unavailable: "
                          f"{reason.replace('_', ' ')}")
        detail = (NO_ELIGIBLE_MODEL + (f"; {HELD_SOURCES}{', '.join(held)}" if held else "")
                  + pin_detail)
        if missing_on_usable_source:
            raise SelectedModelUnavailableError(detail)
        raise PermissionError(detail)
    result = PreparedPlan(
        plan, Catalog(owner, universe.name, tuple(all_models)), tuple(rejected),
        assignment, tuple(chains), agent, preferences, tuple(snapshots), display_only=allow_empty,
        home_scoped=home_scoped,
    )
    with provider_assignment_admission().shared(universe):
        current_agent = get_binding(
            base, universe_id=universe.name, binding_id=agent["agent_binding_id"],
        )
        with store.connection() as conn:
            conn.execute("BEGIN")
            if allow_empty:
                result = result.recheck_display(
                    conn, store=store, base=base, universe=universe,
                    owner=owner, agent=current_agent,
                )
            else:
                result.recheck(
                    conn, store=store, base=base, universe=universe,
                    owner=owner, agent=current_agent,
                )
    return result


def apply_served_model_preferences(context, *, model_choice=None, needs_tools=True):
    """Capture preferences only from a genuine current owned conversation."""
    from tinyassets.addressed_agents import MAIN_AGENT, resolve
    from tinyassets.agent_definition import read_settings
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.exceptions import ProviderAuthorityHeldError

    try:
        if context.provider_request is None:
            if model_choice is not None:
                raise PermissionError("model choice requires an authenticated conversation")
            return context
        universe = context.universe_dir
        capability, agent = _served_request_agent(
            universe.parent, universe, context.provider_request, "writer", "converse",
        )
        if get_founder_home(universe.parent, capability.principal_id) != universe.name:
            if model_choice is not None:
                raise PermissionError("model choice is not supported outside the current home")
            return context
        slug = None
        if context.agent_id != MAIN_AGENT:
            addressed = resolve(universe.parent, universe_id=universe.name,
                                owner=capability.principal_id, agent_id=context.agent_id)
            slug = addressed.agent_slug if addressed is not None else None
        if context.agent_id == MAIN_AGENT or slug is not None:
            model_choice = read_settings(universe, agent_slug=slug).model_choice(model_choice)
        current = None if model_choice is None else ModelPreferences.from_document(model_choice)
        prepared = prepare_owned_model_plan(
            base=universe.parent, universe=universe, owner=capability.principal_id,
            agent=agent, current=current, config=context.config, needs_tools=needs_tools,
        )
        if prepared is None:
            return context
        return replace(
            context, agent_model_plan=prepared.plan,
            model_selection=prepared.plan.next_candidate(capability.principal_id, universe.name),
        )
    except (PermissionError, ValueError, RuntimeError, ProviderError) as exc:
        raise ProviderAuthorityHeldError(str(exc)) from exc
