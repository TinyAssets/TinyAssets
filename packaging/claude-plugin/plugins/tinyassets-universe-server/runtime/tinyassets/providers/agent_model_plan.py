"""Finite advisory candidate plan; each actual attempt still needs fresh authority."""

from dataclasses import dataclass, replace

from tinyassets.providers.model_policy import (
    Catalog,
    Interaction,
    ModelPolicy,
    ModelRef,
    SourceModelPolicy,
    order_models,
)


@dataclass(frozen=True, slots=True)
class AgentModelPlan:
    catalog: Catalog
    policy: ModelPolicy
    interaction: Interaction
    policy_source: str = "unknown"
    source_policies: tuple[SourceModelPolicy, ...] = ()
    reconnect_sources: tuple[str, ...] = ()
    #: Models this owner's source recently refused (``storage.refused_models``).
    #: Ordered LAST, not removed: a refusal is often temporary, and an order that
    #: still ends in them beats a turn that finds no model at all.
    refused_models: tuple[ModelRef, ...] = ()
    cooling_sources: tuple[str, ...] = ()

    def __post_init__(self):
        if (
            type(self.catalog) is not Catalog or type(self.policy) is not ModelPolicy
            or type(self.interaction) is not Interaction
            or type(self.policy_source) is not str
            or self.policy_source not in {"unknown", "current", "saved", "automatic"}
            or type(self.source_policies) is not tuple
            or type(self.reconnect_sources) is not tuple
            or any(type(item) is not str for item in self.reconnect_sources)
            or type(self.refused_models) is not tuple
            or any(type(item) is not ModelRef for item in self.refused_models)
            or any(type(item) is not SourceModelPolicy
                   or item.interaction.needs_tools != self.interaction.needs_tools
                   for item in self.source_policies)
        ):
            raise ValueError("invalid interactive candidate plan")

    def order(self, owner, universe, exhaustion=()):
        order = self._reconnect_order(owner, universe, exhaustion)
        # Explicit orders belong to the owner; health only reorders Automatic.
        refused = (set(self.refused_models) if self.policy.mode == "automatic"
                   and self.policy.current_selection is None
                   and self.policy.saved_default is None else set())
        if not refused:
            return order
        candidates = tuple(
            replace(item, labels=item.labels + ("recently_refused",))
            if item.ref in refused else item
            for item in order.candidates
        )
        return replace(order, candidates=tuple(sorted(
            candidates, key=lambda item: item.ref in refused,
        )))

    def _reconnect_order(self, owner, universe, exhaustion):
        order = order_models(
            self.catalog, self.policy, self.interaction,
            owner_id=owner, universe_id=universe, exhaustion=exhaustion,
            source_policies=self.source_policies,
        )
        if not self.reconnect_sources and not self.cooling_sources:
            return order
        candidates = tuple(
            replace(item, labels=item.labels + ("recent_sign_in_failure",))
            if item.ref.connection_id in self.reconnect_sources else item
            for item in order.candidates
        )
        candidates = tuple(
            replace(item, labels=item.labels + ("source_cooling_down",))
            if item.ref.connection_id in self.cooling_sources else item
            for item in candidates
        )
        if (self.policy.mode == "automatic" and self.policy.current_selection is None
                and self.policy.saved_default is None):
            candidates = tuple(sorted(
                candidates, key=lambda item: item.ref.connection_id in
                (*self.reconnect_sources, *self.cooling_sources),
            ))
        return replace(order, candidates=candidates)

    def next_candidate(self, owner, universe, exhaustion=()):
        order = self.order(owner, universe, exhaustion)
        return order.candidates[0].ref if order.candidates else None

    def capacity_order(self, owner, universe, exhaustion=()):
        """Conversation recovery: a model preference is not an only-model grant.

        Keep the owner's requested order ahead of automatic alternatives. This never discovers
        credentials or widens model/cost access.
        Workflow pins continue to use ``order`` through WorkCandidateData.
        """
        preferred = self.order(owner, universe, exhaustion)
        automatic = replace(self, policy=replace(
            self.policy, mode="automatic", current_selection=None,
            saved_default=None, fallbacks=(),
        )).order(owner, universe, exhaustion)
        seen = {item.ref for item in preferred.candidates}
        candidates = preferred.candidates + tuple(
            item for item in automatic.candidates if item.ref not in seen
        )
        return replace(preferred, candidates=candidates)
