"""Pure, advisory model ordering; never an inference grant or dispatch path.

Discovery adapters normalize evidence before entering here. Admission MUST refresh
authority, privacy, capabilities and pricing before every actual attempt. A saved
choice or an advisory candidate cannot authorize inference. No vendor names,
model-release list, credentials, storage, clock or network belong in this module.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Literal

Freshness = Literal["fresh", "stale", "missing"]
SourceKind = Literal["subscription", "local", "http"]


@dataclass(frozen=True, slots=True)
class ModelRef:
    connection_id: str  # Opaque selector source (exact provider ref), not an HTTP ledger id.
    model_id: str  # Opaque; empty may represent the connection's native default.


@dataclass(frozen=True, slots=True)
class Charge:
    component: str  # Includes the unit, e.g. input_million_tokens_usd.
    amount_micros: int
    confirmed: bool = False

    def __post_init__(self) -> None:
        if type(self.amount_micros) is not int or self.amount_micros < 0:
            raise ValueError("charge must be an exact nonnegative integer")


@dataclass(frozen=True, slots=True)
class Pricing:
    freshness: Freshness = "missing"
    charges: tuple[Charge, ...] = ()
    unmetered: bool = False  # Confirmed non-metered inference, not unknown cost.
    unknown_components: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if len({charge.component for charge in self.charges}) != len(self.charges):
            raise ValueError("duplicate charge component")
        if self.unmetered and self.charges:
            raise ValueError("unmetered pricing cannot also declare metered charges")


@dataclass(frozen=True, slots=True)
class Scores:
    source: str
    freshness: Freshness
    agentic: int | None = None
    general: int | None = None

    def __post_init__(self) -> None:
        for score in (self.agentic, self.general):
            if score is not None and type(score) is not int:
                raise ValueError("score must be an exact integer or unknown")


@dataclass(frozen=True, slots=True)
class Model:
    model_id: str
    tools: bool | None
    modalities: frozenset[str]
    context_tokens: int | None = None
    pricing: Pricing = Pricing()
    scores: Scores | None = None
    output_modalities: frozenset[str] = frozenset({"text"})
    availability_basis: str | None = None
    #: Effort/reasoning levels the SOURCE advertised for this model, in its own
    #: order. Empty means no effort control exists for it, which is a real and
    #: common answer -- a live Claude Code catalogue reports levels for Opus and
    #: none for Haiku. Per-model rather than per-source for that reason: the
    #: same source's models disagree, and 4.6 lacks a level 5.x has.
    effort_levels: tuple[str, ...] = ()
    # Remaining daily requests, observed or declared; None means no known cap.
    remaining_requests: int | None = None


@dataclass(frozen=True, slots=True)
class ConnectionModels:
    connection_id: str  # Preserve separate accepted bindings even when physical custody is shared.
    provider_scope: str  # Trusted adapter scope, not remote model metadata.
    source_kind: SourceKind
    freshness: Freshness
    owner_filtered: bool
    executor_tools: bool
    models: tuple[Model, ...]
    default_model_id: str | None = None
    authenticated_account_id: str | None = None
    # Derived only by authenticated publication + exact-endpoint refresh. This
    # admits declared-source candidates, not independently verified privacy.
    availability_basis: str | None = None


@dataclass(frozen=True, slots=True)
class Catalog:
    owner_id: str
    universe_id: str
    connections: tuple[ConnectionModels, ...]


@dataclass(frozen=True, slots=True)
class ModelPolicy:
    generation: int
    # Behaviour when neither current nor saved primary is present. A primary
    # always wins; clearing it is an explicit caller action, not a mode side effect.
    mode: Literal["automatic", "explicit"]
    fallbacks: tuple[ModelRef, ...]  # Required: an empty tuple is meaningful.
    current_selection: ModelRef | None = None
    saved_default: ModelRef | None = None
    ranking_source: str | None = None
    stable_preference: ModelRef | None = None
    # None is free-only. A cap must name every required charge component.
    cost_caps: tuple[Charge, ...] | None = None
    # The owner's saved effort level per model, as ``(ref, level)`` pairs.
    # Independent of mode on purpose: an automatic plan still runs whichever
    # model it lands on at the level the owner set for that model.
    efforts: tuple[tuple[ModelRef, str], ...] = ()

    def __post_init__(self) -> None:
        if type(self.generation) is not int or self.generation < 0:
            raise ValueError("invalid policy generation")
        if self.mode not in ("automatic", "explicit"):
            raise ValueError("invalid selection mode")
        if self.cost_caps is not None:
            _charges(self.cost_caps)
        if (
            self.mode == "automatic"
            and self.current_selection is None
            and self.saved_default is None
            and self.fallbacks
        ):
            raise ValueError("accepted fallbacks require an explicit primary or explicit mode")
        if type(self.efforts) is not tuple or any(
            type(item) is not tuple or len(item) != 2
            or type(item[0]) is not ModelRef or type(item[1]) is not str or not item[1]
            for item in self.efforts
        ):
            raise ValueError("invalid saved effort levels")
        if len({ref for ref, _ in self.efforts}) != len(self.efforts):
            raise ValueError("duplicate effort level for one model")

    def effort_for(self, ref: ModelRef | None) -> str:
        """The owner's saved level for exactly this model, or empty for none.

        Empty is "whatever the executor does by default", never a level chosen
        here. Callers that invoke a provider must still hold the level to the
        set that model ADVERTISED; this is storage, not admission.
        """
        for item, level in self.efforts:
            if item == ref:
                return level
        return ""


@dataclass(frozen=True, slots=True)
class Interaction:
    needs_tools: bool
    modalities: frozenset[str]
    charge_components: frozenset[str]
    min_context: int | None = None
    output_modalities: frozenset[str] = frozenset({"text"})
    # Declared by the exact encoder contract, never inferred from missing prices.
    ceiling_components: frozenset[str] = frozenset()
    excluded_components: frozenset[str] = frozenset()
    extra_price_bounds: tuple[tuple[str, str | None], ...] = ()
    output_price_components: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class Exhaustion:
    scope: Literal["model", "account"]
    ref: ModelRef

    def __post_init__(self) -> None:
        if self.scope not in ("model", "account"):
            raise ValueError("invalid exhaustion scope")


@dataclass(frozen=True, slots=True)
class Candidate:
    ref: ModelRef
    basis: str
    labels: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class Ineligible:
    ref: ModelRef
    reason: str
    component: str = ""
    scope: Literal["model", "source"] = "model"


@dataclass(frozen=True, slots=True)
class AdvisoryOrder:
    generation: int
    candidates: tuple[Candidate, ...]
    ineligible: tuple[Ineligible, ...]
    kind: Literal["advisory_order"] = "advisory_order"


def _charges(charges: tuple[Charge, ...]) -> dict[str, Charge]:
    result = {charge.component: charge for charge in charges}
    if len(result) != len(charges):
        raise ValueError("duplicate charge component")
    return result


def _capacity_identity(connection: ConnectionModels) -> tuple[str, str, str]:
    if connection.authenticated_account_id:
        return (connection.provider_scope, "account", connection.authenticated_account_id)
    return (connection.provider_scope, "connection", connection.connection_id)


def _ineligibility(
    connection: ConnectionModels,
    model: Model,
    policy: ModelPolicy,
    interaction: Interaction,
    *,
    explicit: bool,
) -> tuple[str, str] | None:
    if (not connection.owner_filtered
            and connection.availability_basis != "owner_configured_contract"):
        return "privacy_unverified", ""
    if interaction.needs_tools and not connection.executor_tools:
        return "executor_unsupported", ""
    if interaction.needs_tools and model.tools is not True:
        return ("capability_unknown" if model.tools is None else "capability_unsupported"), ""
    if not interaction.modalities <= model.modalities:
        return "capability_unsupported", ""
    if not interaction.output_modalities <= model.output_modalities:
        return "capability_unsupported", ""
    if interaction.min_context is not None:
        if model.context_tokens is None:
            return "capability_unknown", ""
        if model.context_tokens < interaction.min_context:
            return "capability_unsupported", ""
    if not explicit and connection.freshness != "fresh":
        return "stale_capability", ""
    pricing = model.pricing
    if pricing.unknown_components:
        return "unknown_price_component", sorted(pricing.unknown_components)[0]
    if not explicit and pricing.freshness != "fresh":
        return "stale_price", ""
    if pricing.unmetered:
        return None
    if not interaction.charge_components:
        return "missing_price_component", "required_charge_components"
    charges = _charges(pricing.charges)
    caps = None if policy.cost_caps is None else _charges(policy.cost_caps)
    for component in sorted(interaction.charge_components | interaction.ceiling_components):
        charge = charges.get(component)
        if charge is None and component in interaction.ceiling_components:
            if component not in interaction.charge_components:
                if caps is not None:
                    cap = caps.get(component)
                    if cap is None or not cap.confirmed:
                        return "exceeds_cost_cap", component
                # Advisory only: real dispatch must materialize and enforce all
                # ceilings (including zero for free-only) through this protocol.
                continue  # No synthetic advertisement.
        if charge is None or not charge.confirmed:
            return "missing_price_component", component
        if caps is None:
            if charge.amount_micros != 0:
                return "not_confirmed_free", component
        else:
            cap = caps.get(component)
            if cap is None or not cap.confirmed or charge.amount_micros > cap.amount_micros:
                return "exceeds_cost_cap", component
    if not interaction.ceiling_components:
        undeclared = charges.keys() - interaction.charge_components
        if undeclared:
            return "unknown_price_component", sorted(undeclared)[0]
    if interaction.ceiling_components:
        described_outputs = interaction.output_modalities | {
            modality for modality, _ in interaction.output_price_components
        }
        if model.output_modalities - described_outputs:
            return "capability_unsupported", "output_price_shape"
        for modality, component in interaction.output_price_components:
            if modality in model.output_modalities and component not in charges:
                return "missing_price_component", component
        bounds = dict(interaction.extra_price_bounds)
        for component, charge in sorted(charges.items()):
            if component in interaction.charge_components | interaction.ceiling_components:
                continue
            if component in interaction.excluded_components:
                continue
            if component not in bounds:
                return "unknown_price_component", component
            if not charge.confirmed:
                return "missing_price_component", component
            bound_component = bounds[component]
            cap = None if caps is None or bound_component is None else caps.get(bound_component)
            if (
                bound_component is not None
                and caps is not None
                and (cap is None or not cap.confirmed)
            ):
                return "exceeds_cost_cap", component
            maximum = 0 if cap is None else cap.amount_micros
            if charge.amount_micros > maximum:
                return "unenforceable_price_component", component
    return None


@dataclass(frozen=True, slots=True)
class SourceModelPolicy:
    """Private per-source requirements and accepted caps, never remote authority."""

    connection_id: str
    interaction: Interaction
    cost_caps: tuple[Charge, ...] | None = None

    def __post_init__(self):
        if (type(self.connection_id) is not str or not self.connection_id
                or type(self.interaction) is not Interaction
                or (self.cost_caps is not None and (
                    type(self.cost_caps) is not tuple
                    or any(type(item) is not Charge for item in self.cost_caps)))):
            raise ValueError("invalid source model policy")
        if self.cost_caps is not None:
            _charges(self.cost_caps)


def order_models(
    catalog: Catalog,
    policy: ModelPolicy,
    interaction: Interaction,
    *,
    owner_id: str,
    universe_id: str,
    exhaustion: tuple[Exhaustion, ...] = (),
    source_policies: tuple[SourceModelPolicy, ...] = (),
) -> AdvisoryOrder:
    """Return a finite advisory order from normalized, explicitly scoped evidence.

    Current choice overrides the saved primary, never the accepted fallback list.
    Unavailable references stay visible in `ineligible`; they do not turn into an
    automatic choice. Explicit stale evidence is labelled for required refresh.
    """
    if (
        not owner_id
        or not universe_id
        or (owner_id, universe_id)
        != (
            catalog.owner_id,
            catalog.universe_id,
        )
    ):
        raise ValueError("catalogue scope mismatch")
    connections = {c.connection_id: c for c in catalog.connections}
    if len(connections) != len(catalog.connections):
        raise ValueError("duplicate connection")
    if type(source_policies) is not tuple or any(
        type(item) is not SourceModelPolicy for item in source_policies
    ):
        raise ValueError("invalid source model policies")
    per_source = {item.connection_id: item for item in source_policies}
    if source_policies and (len(per_source) != len(source_policies)
                            or per_source.keys() != connections.keys()):
        raise ValueError("source model policies must match the complete catalogue")
    entries: dict[ModelRef, tuple[ConnectionModels, Model]] = {}
    for connection in catalog.connections:
        if connection.source_kind not in ("subscription", "local", "http"):
            raise ValueError("invalid source kind")
        if not connection.connection_id or not connection.provider_scope:
            raise ValueError("missing connection identity")
        for model in connection.models:
            ref = ModelRef(connection.connection_id, model.model_id)
            if ref in entries:
                raise ValueError("duplicate model reference")
            entries[ref] = (connection, model)

    primary = policy.current_selection or policy.saved_default
    explicit = primary is not None or policy.mode == "explicit"
    rejected: list[Ineligible] = []
    if explicit:
        refs = ([] if primary is None else [primary]) + list(policy.fallbacks)
    else:
        # Every admitted model competes, independent of access method.
        refs = [ModelRef(c.connection_id, m.model_id)
                for c in catalog.connections for m in c.models]

    exhausted_models: set[tuple[tuple[str, str, str], str]] = set()
    model_failures: list[tuple[ConnectionModels, str]] = []
    exhausted_accounts: list[ConnectionModels] = []
    for failure in exhaustion:
        connection = connections.get(failure.ref.connection_id)
        if connection is None:
            # Unknown old evidence cannot prove any remaining source independent.
            raise ValueError("exhaustion connection absent from catalogue")
        if failure.scope == "account":
            exhausted_accounts.append(connection)
        else:
            exhausted_models.add((_capacity_identity(connection), failure.ref.model_id))
            model_failures.append((connection, failure.ref.model_id))

    eligible: list[Candidate] = []
    seen: set[tuple[tuple[str, str, str], str]] = set()
    for ref in refs:
        entry = entries.get(ref)
        if entry is None:
            rejected.append(Ineligible(ref, "absent_from_catalogue"))
            continue
        connection, model = entry
        identity = (_capacity_identity(connection), model.model_id)
        reason: tuple[str, str] | None = None
        for failed in exhausted_accounts:
            if failed.provider_scope != connection.provider_scope:
                continue
            if _capacity_identity(failed) == _capacity_identity(connection):
                reason = "account_exhausted", ""
            elif not failed.authenticated_account_id or not connection.authenticated_account_id:
                reason = "capacity_identity_unverified", ""
            if reason:
                break
        if reason is None and identity in exhausted_models:
            reason = "model_exhausted", ""
        if reason is None:
            for failed, failed_model in model_failures:
                if (
                    failed.provider_scope == connection.provider_scope
                    and failed_model == model.model_id
                    and (
                        not failed.authenticated_account_id
                        or not connection.authenticated_account_id
                    )
                ):
                    reason = "capacity_identity_unverified", ""
                    break
        if reason is None:
            source = per_source.get(connection.connection_id)
            checked_policy = (policy if source is None
                              else replace(policy, cost_caps=source.cost_caps))
            required = interaction if source is None else source.interaction
            reason = _ineligibility(connection, model, checked_policy, required, explicit=explicit)
        if reason is None and identity in seen:
            reason = "duplicate", ""
        if reason is not None:
            rejected.append(Ineligible(ref, *reason))
            continue
        seen.add(identity)
        labels = tuple(
            label
            for stale, label in (
                (connection.freshness != "fresh", "refresh_capabilities"),
                (model.pricing.freshness != "fresh", "refresh_price"),
                (connection.availability_basis == "owner_configured_contract",
                 "source_claims_not_independently_verified"),
            )
            if stale
        )
        eligible.append(Candidate(ref, "explicit" if explicit else "automatic", labels))

    if not explicit:

        def ranking(candidate: Candidate) -> tuple:
            connection, model = entries[candidate.ref]
            stable = int(candidate.ref != policy.stable_preference)
            # A known finite allowance is a constraint, not a quality score.
            # Unknown is no known constraint, never an invented unlimited grant.
            capacity = (model.remaining_requests is not None, -(model.remaining_requests or 0))
            scores = model.scores
            if (
                scores is not None
                and policy.ranking_source is not None
                and scores.source == policy.ranking_source
                and scores.freshness == "fresh"
                and scores.agentic is not None
            ):
                # General score only breaks equal agentic scores. Unknown is not
                # zero (which could itself be a real benchmark score).
                return (
                    *capacity, 0,
                    -scores.agentic,
                    int(scores.general is None),
                    -(scores.general or 0),
                    stable,
                )
            return (*capacity, 1, 0, 0, 0, 0)  # Unranked: stable discovery order.

        eligible.sort(key=ranking)
    return AdvisoryOrder(policy.generation, tuple(eligible), tuple(rejected))
