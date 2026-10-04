"""Build a conversation plan from current owned bindings, never caller facts.

Discovery happens outside assignment admission and database transactions. The
result is advisory: every actual launch still validates its exact member anew.
"""

import logging
import sqlite3
from dataclasses import dataclass, replace
from pathlib import Path

from tinyassets.custom_agents import get_binding
from tinyassets.exceptions import ProviderError
from tinyassets.provider_assignment import (
    _served_request_agent,
    load_provider_assignment_in_transaction,
    provider_assignment_admission,
)
from tinyassets.provider_serving_binding import (
    _PROVIDER_SERVICE,
    ServingProviderHeld,
    _current_selected_member_authority,
    _resolve_serving_source,
)
from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.discovery_snapshot import ModelDiscoveryUnavailable
from tinyassets.providers.model_policy import (
    Catalog,
    Charge,
    ConnectionModels,
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
from tinyassets.storage.current_home import check_current_home
from tinyassets.storage.learned_models import (
    LEARNED_SOURCE_KIND,
    OWN_VERIFIED_BASIS,
)
from tinyassets.storage.model_preferences import ModelPreferenceStore
from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

_LOG = logging.getLogger("universe_server.served_model_plan")

#: An id from the reviewed public list for this source kind. A public claim that the
#: id EXISTS, never permission to use it.
PUBLIC_LISTED_BASIS = "publicly_listed"

#: Bases that are OFFERS to grant, never admitted candidates. One set, read by the
#: split below and by api/model_options' legacy plan, so a third basis cannot be added
#: in one place and admitted in the other.
_CANDIDATE_ONLY_BASES = frozenset({PUBLIC_LISTED_BASIS, OWN_VERIFIED_BASIS})



#: Prefix of the refusal when no accepted model can run. The served turn's
#: notice reads the held sources after ``HELD_SOURCES`` (universe_server).
NO_ELIGIBLE_MODEL = "no eligible model in the accepted assignment"
HELD_SOURCES = "not usable now: "

def _assert_plan_snapshot(snapshot):
    from tinyassets.providers.discovery_snapshot import assert_discovery_snapshot_current
    from tinyassets.providers.native_discovery import NativeDiscoverySnapshot

    if type(snapshot) is NativeDiscoverySnapshot:
        # The caller has just rechecked every exact member/custody chain in its
        # transaction. Do not open another SQLite connection inside that fence.
        snapshot.assert_fresh()
    else:
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

    def recheck(self, conn, *, store, base, universe, owner, agent, check_preferences=False):
        if self.display_only:
            raise ValueError("display catalogue cannot authorize activation")
        self.recheck_scope(conn, universe=universe, owner=owner, agent=agent,
                           check_preferences=check_preferences)
        for provider, expected in self.chains:
            actual = _current_selected_member_authority(
                conn, store=store, universe_dir=universe, base_path=base,
                owner_user_id=owner, universe_id=universe.name, agent=agent, provider=provider,
            )
            if actual != expected:
                raise PermissionError("model member changed during discovery")
        for snapshot in self.snapshots:
            _assert_plan_snapshot(snapshot)

    def recheck_scope(self, conn, *, universe, owner, agent, check_preferences=False):
        """A changed home/agent/assignment invalidates the entire display too."""
        from tinyassets.storage.model_preferences import _read

        check_current_home(conn, owner, universe.name)
        if agent != self.agent or load_provider_assignment_in_transaction(
            conn, universe_id=universe.name,
        ) != self.assignment:
            raise PermissionError("model assignment changed during discovery")
        if check_preferences and _read(conn, owner, universe.name) != self.preferences:
            raise PermissionError("model preferences changed during activation")
    def recheck_display(self, conn, *, store, base, universe, owner, agent):
        """Demote only failed sources; never let them hide independent choices."""
        self.recheck_scope(conn, universe=universe, owner=owner, agent=agent,
                           check_preferences=True)
        failed = {}
        for provider, expected in self.chains:
            try:
                actual = _current_selected_member_authority(
                    conn, store=store, universe_dir=universe, base_path=base,
                    owner_user_id=owner, universe_id=universe.name, agent=agent, provider=provider,
                )
                if actual != expected:
                    failed[provider] = "source_revoked"
            except PermissionError:
                failed[provider] = "source_revoked"
        for snapshot in self.snapshots:
            try:
                _assert_plan_snapshot(snapshot)
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


def _native_models(base, universe, owner, member, *, native_snapshot=None):
    from tinyassets.providers.call import get_provider_router
    from tinyassets.providers.model_selection import _native_default

    _resolve_serving_source(base, universe.name, owner, member.provider, member.access)
    from tinyassets.providers.native_model_selection import accepted_native_selection

    declared = member.access.model_ids if member.access.model_scope == "explicit" else ("",)
    models = []
    #: Ids the executor advertised AND marked unselectable. Withheld from the
    #: choices, and also withheld from the candidate sources below: Codex found
    #: that filtering them out of enumeration alone let the same id return as a
    #: learned or reviewed-list candidate, because dedupe only saw the rows that
    #: were kept. A source saying "not this one" outranks our own history of it.
    withdrawn = set()
    for model_id in declared:
        if model_id:
            accepted_native_selection(member.provider, model_id, member.access)
        else:
            _native_default(member.provider, model_id, member.access)
        models.append(Model(
            model_id, True, frozenset({"text"}), pricing=Pricing("fresh", unmetered=True),
            availability_basis="owner_declared" if model_id else "executor_default",
        ))
    if native_snapshot is not None:
        if member.access.model_scope != "discovered":
            raise PermissionError("native discovery cannot widen an explicit model scope")
        native_snapshot.assert_fresh()
        if (native_snapshot.provider != member.provider or native_snapshot.owner_id != owner
                or native_snapshot.universe != Path(universe).resolve()
                or native_snapshot.custody.reference_digest != member.credential_reference_digest):
            raise PermissionError("native catalogue is outside current member custody")
        # A row the executor itself marked unselectable must not become a normal
        # choice. Offering it would hand the owner a pick that fails at launch,
        # which is worse than not listing it (see the learned-id split below).
        withdrawn.update(model.model_id for model in native_snapshot.catalogue.models
                         if model.hidden)
        models.extend(Model(
            model.model_id, True, model.input_modalities,
            pricing=Pricing("fresh", unmetered=True), availability_basis="executor_enumerated",
            effort_levels=model.effort_levels,
        ) for model in native_snapshot.catalogue.models if not model.hidden)
    # What the PLATFORM has seen work on this KIND of source, newest of each class.
    # Without this the list is only what this owner typed into their own access
    # grant, so a newly released model was invisible until someone shipped a patch
    # (founder, 2026-09-26: "i cant seem to select fable as a user for the llm").
    #
    # These are NOT put through accepted_native_selection: they are candidates the
    # platform can vouch exist, not ids this universe has granted access to. The
    # existing access machinery marks them outside the accepted scope, so they
    # surface as "needs access" and become selectable when the owner grants it.
    # A catalog row is evidence, never permission.
    # THIS OWNER's own verified ids first. The founder's rule is that an id which
    # worked for one owner "stays on that user's own list forever" even when it
    # never becomes public -- and Codex round 3 found the store was keeping it while
    # nothing ever listed it, so a solo user's ARN silently vanished from their own
    # picker as soon as they stopped declaring it. Keeping a row nobody reads is not
    # keeping it.
    models.extend(_own_verified_candidates(base, LEARNED_SOURCE_KIND, owner,
                                          already=models, excluded=withdrawn))
    # ...then the reviewed public list for this kind of source, which is how a newly
    # released model reaches everyone without a patch: a PR adds the id, review is the
    # moderation, and every universe on that source kind sees it next read.
    models.extend(_listed_candidates(LEARNED_SOURCE_KIND, already=models,
                                     excluded=withdrawn))
    router = get_provider_router()
    provider = None if router is None else router._providers.get(member.provider)
    if provider is None or not provider.is_available():
        raise ModelSourceUnavailable("executor_unavailable")
    return ConnectionModels(
        member.provider, "native-subscription:" + member.provider, "subscription", "fresh",
        True, True,
        tuple(models), default_model_id="" if "" in declared else None,
    )


def _own_verified_candidates(base, source_kind, owner, *, already, excluded=frozenset()):
    """Ids THIS owner has already made work on this kind of source.

    Their own history, not anyone else's: read from the private evidence table
    scoped to this owner, so nothing here depends on the promotion threshold. A
    solo user keeps every id they have ever used even though none of it is public.

    Carries its own basis so the list can be honest about the difference between
    "you have run this" and "two owners elsewhere have run this". Like the
    published rows, these are candidates and NOT admitted: the owner may have
    narrowed their model access since, and re-granting is the existing one-tap path.
    """
    from tinyassets.storage.learned_models import OwnModelHistory

    have = {model.model_id for model in already} | set(excluded)
    try:
        rows = OwnModelHistory(base).ids_for(source_kind, owner)
    except (OSError, sqlite3.DatabaseError, ValueError) as exc:
        _LOG.warning("own verified model evidence unreadable: %s", type(exc).__name__)
        return ()
    return tuple(
        Model(row.model_id, True, frozenset({"text"}),
              pricing=Pricing("fresh", unmetered=True),
              availability_basis=OWN_VERIFIED_BASIS)
        for row in rows if row.model_id and row.model_id not in have
    )


def _listed_candidates(source_kind, *, already, excluded=frozenset()):
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
        for model_id in newest_listed_cached(source_kind)
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
            base, owner, uid, next(m for m in chain[0].candidates if m.provider == provider),
        ))
    )


def _refused_models(base, owner, chains):
    """This owner's recently refused models on the sources in this plan."""
    from tinyassets.storage.refused_models import active_refused_models

    providers = {provider for provider, _chain in chains}
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
    inferred from preferences. A manifest is the explicit opt-in to discovery;
    absent preferences on an existing legacy assignment leave its path unchanged.
    allow_empty permits advisory display, never invocation without a candidate.
    """
    if type(needs_tools) is not bool:
        raise ValueError("invalid conversation tool mode")
    base, universe = Path(base), Path(universe)
    store = SQLiteProviderWorkAuthorityStore(base)
    if preference_snapshot is None:
        preferences = ModelPreferenceStore(base).get(
            owner, universe.name, require_current_home=True,
        )
    else:
        from tinyassets.storage.model_preferences import PreferenceSnapshot

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
            check_current_home(conn, owner, universe.name)
            assignment = load_provider_assignment_in_transaction(conn, universe_id=universe.name)
            if assignment is None or not assignment.manifest_digest:
                if captured is None or (
                    current is None and preferences.policy is not None
                    and preferences.policy.mode == "automatic"
                ):
                    # Saving an unpowered preference cannot brick an existing
                    # native binding. Without model-access opt-in, saved auto
                    # retains that provider's own default and grants nothing.
                    # Explicit saved/current choices still require a manifest.
                    return None
                raise PermissionError("model choice requires an accepted model assignment")
            if (assignment.owner_user_id != owner or assignment.universe_id != universe.name
                    or agent["configuration"].get("provider_ref") != assignment.binding_id):
                raise PermissionError("model assignment does not match the current owned agent")
            chains, rejected = [], []
            for member in assignment.candidates:
                try:
                    chain = _current_selected_member_authority(
                        conn, store=store, universe_dir=universe, base_path=base,
                        owner_user_id=owner, universe_id=universe.name,
                        agent=agent, provider=member.provider,
                    )
                except PermissionError:
                    rejected.append(Ineligible(ModelRef(member.provider, ""), "source_revoked",
                                               scope="source"))
                else:
                    chains.append((member.provider, chain))
    if captured is None:
        captured = ModelPolicy(0, "automatic", ()), "automatic"
    policy, source = captured
    all_models, admitted, snapshots, source_policies = [], [], [], []
    interaction = Interaction(needs_tools, frozenset({"text"}), frozenset())
    ranking_sources = set()
    from tinyassets.provider_authority import current as current_authority

    config = current_authority(universe, config)
    allowed = None if config is None else config.allowed_providers
    for provider, chain in chains:
        member = next(m for m in chain[0].candidates if m.provider == provider)
        try:
            if provider in _PROVIDER_SERVICE:
                native_snapshot = None
                if member.access.model_scope == "discovered":
                    # A DISPLAY read takes whatever is warm and asks for a
                    # refresh in the background; it never runs discovery. That
                    # is what stops one slow or expired source delaying the
                    # selection of a different accepted one
                    # (concerns/2026-09-16-model-picker-global-discovery-delay).
                    #
                    # A served turn still discovers inline: it is about to
                    # launch, and a plan built from a catalogue it has not
                    # confirmed is not a saving. Execution's own fresh check in
                    # `prepare_selected_model` is unchanged either way.
                    if allow_empty:
                        from tinyassets.providers.shortlist_refresh import SHORTLIST_CACHE

                        native_snapshot, pending = SHORTLIST_CACHE.get(
                            base=base, owner=owner, universe_id=universe.name,
                            provider=provider,
                        )
                        if native_snapshot is None:
                            rejected.append(Ineligible(ModelRef(provider, ""), pending,
                                                       scope="source"))
                    else:
                        from tinyassets.providers.native_discovery import (
                            discover_native_models_sync,
                        )

                        try:
                            native_snapshot = discover_native_models_sync(
                                base_path=base, owner_user_id=owner,
                                universe_id=universe.name, provider=provider,
                            )
                            if native_snapshot is None:
                                rejected.append(Ineligible(
                                    ModelRef(provider, ""), "native_enumeration_unsupported",
                                    scope="source",
                                ))
                        except ProviderError:
                            # Enumeration is not necessary to run the provider's
                            # own default. Preserve that lane and expose the gap.
                            rejected.append(Ineligible(
                                ModelRef(provider, ""), "native_catalogue_unavailable",
                                scope="source"))
                catalog = _native_models(
                    base, universe, owner, member, native_snapshot=native_snapshot,
                )
                # A LEARNED id is a candidate to GRANT, never an admitted one.
                # `catalog` is what a client may SEE; `filtered` is what may be
                # selected and executed, and the two are deliberately different
                # here -- the same split the HTTP branch below already makes.
                #
                # Codex on #4028 found these identical: one object went to both, so
                # a learned id arrived with in_candidate_catalog=true, the dropdown
                # offered it, selection succeeded and only EXECUTION refused it.
                # Selectable choices that fail are worse than absent ones.
                contributed = tuple(
                    model for model in catalog.models
                    if model.availability_basis in _CANDIDATE_ONLY_BASES
                )
                filtered = replace(catalog, models=tuple(
                    model for model in catalog.models
                    if model.availability_basis not in _CANDIDATE_ONLY_BASES
                ))
                # Said, not merely withheld: the reason is what puts it under the
                # dropdown's "Needs access" group with the one-tap grant, so the
                # owner can turn a learned id into a real choice.
                rejected.extend(
                    Ineligible(ModelRef(provider, model.model_id),
                               "model_access_optin_required")
                    for model in contributed
                )
                if native_snapshot is not None:
                    snapshots.append(native_snapshot)
                required, caps = interaction, member.access.cost_caps
            else:
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
                catalog = snapshot.models
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
        if provider in _PROVIDER_SERVICE:
            all_models.append(catalog)
        if allowed is not None and provider not in allowed:
            rejected.extend(Ineligible(ModelRef(provider, m.model_id), "provider_not_allowed")
                            for m in catalog.models)
        else:
            admitted.append(filtered)
            source_policies.append(SourceModelPolicy(
                provider, required,
                None if caps is None else tuple(Charge(k, v, True) for k, v in caps),
            ))
    policy = replace(
        policy,
        ranking_source=next(iter(ranking_sources)) if len(ranking_sources) == 1 else None,
    )
    plan = AgentModelPlan(
        Catalog(owner, universe.name, tuple(admitted)), policy, interaction, source,
        tuple(source_policies),
        _reconnect_sources(base, owner, universe.name, chains),
        _refused_models(base, owner, chains),
    )
    if not allow_empty and plan.next_candidate(owner, universe.name) is None:
        # Name what is held and why: "no model connected" was wrong for an owner
        # whose accepted sources exist but cannot run (live 2026-09-28).
        held = [
            f"{item.ref.connection_id.removeprefix('api_key_http:')} "
            f"({item.reason.replace('_', ' ')})"
            for item in rejected if item.scope == "source"
        ]
        raise PermissionError(
            NO_ELIGIBLE_MODEL + (f"; {HELD_SOURCES}{', '.join(held)}" if held else "")
        )
    result = PreparedPlan(
        plan, Catalog(owner, universe.name, tuple(all_models)), tuple(rejected),
        assignment, tuple(chains), agent, preferences, tuple(snapshots), display_only=allow_empty,
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
    from tinyassets.daemon_server import get_founder_home
    from tinyassets.exceptions import ProviderAuthorityHeldError

    try:
        current = None if model_choice is None else ModelPreferences.from_document(model_choice)
        if context.provider_request is None:
            if current is not None:
                raise PermissionError("model choice requires an authenticated conversation")
            return context
        universe = context.universe_dir
        capability, agent = _served_request_agent(
            universe.parent, universe, context.provider_request, "writer", "converse",
        )
        if get_founder_home(universe.parent, capability.principal_id) != universe.name:
            if current is not None:
                raise PermissionError("model choice is not supported outside the current home")
            return context
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
