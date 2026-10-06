"""Owner-scoped advisory capacity evidence for automatic model ordering."""

from dataclasses import replace
from types import SimpleNamespace

from tinyassets.providers.model_policy import ModelRef


def with_request_capacity(base, owner, universe, connection):
    """Join existing declared caps and observed usage to the admitted catalogue.

    No guessed subscription allowance, price-as-quality inference, or model-name
    heuristic. Missing evidence leaves the catalogue's facts unchanged. The
    budget reader also corrects declared caps when successful usage exceeds them.
    """
    from tinyassets.request_budget import _source_budget_facts, request_budget

    free = tuple(m.model_id for m in connection.models
                 if m.pricing.freshness == "fresh" and m.pricing.charges
                 and not m.pricing.unknown_components
                 and all(c.confirmed and c.amount_micros == 0 for c in m.pricing.charges))
    if not free:
        return connection
    context = SimpleNamespace(
        universe_dir=universe,
        model_selection=ModelRef(connection.connection_id, free[0]),
    )
    facts = _source_budget_facts(context, owner=owner)
    preset = facts[1] if facts else None
    if not preset or type(preset.get("requests_per_day")) is not int:
        return connection
    budget = request_budget(base, owner, connection.connection_id, free[0],
                            preset=preset, zero_priced_models=free)
    remaining = max(0, budget.cap - budget.used) if budget else preset["requests_per_day"]
    return replace(connection, models=tuple(
        replace(m, remaining_requests=remaining) if m.model_id in free else m
        for m in connection.models
    ))


def cooling_sources(owner, connections):
    """Current owner-scoped observed cooldowns, without inferring other owners' health."""
    from tinyassets.providers.call import get_provider_router

    router = get_provider_router()
    if router is None:
        return ()
    return tuple(c.connection_id for c in connections
                 if not router._quota.available(c.connection_id, owner=owner))
