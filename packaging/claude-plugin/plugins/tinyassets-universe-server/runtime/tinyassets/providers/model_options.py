"""Credential-blind advisory data for a model picker, not a public endpoint.

Collection and authentication belong to the caller. Unlike execution, display
must work with zero eligible models and preserve missing explicit choices.
"""

from tinyassets.providers.agent_model_plan import AgentModelPlan
from tinyassets.providers.model_policy import Catalog, Ineligible, ModelRef


def model_options_document(
    available: Catalog, plan: AgentModelPlan, rejected: tuple[Ineligible, ...] = (),
) -> dict:
    """Project allowlisted model facts; never serialize custody or account ids.

    ``available`` is the unfiltered owner catalogue, while ``plan.catalog`` has
    already been restricted by each connection's own authority and cost limits.
    Selection order is distinct from eligibility: an eligible model outside an
    explicit fallback order is still a choice, not an automatic fallback.
    """
    if (available.owner_id, available.universe_id) != (
        plan.catalog.owner_id, plan.catalog.universe_id,
    ):
        raise ValueError("model option scopes do not match")
    order = plan.order(available.owner_id, available.universe_id)
    positions = {item.ref: index for index, item in enumerate(order.candidates)}
    candidates = {item.ref: item for item in order.candidates}
    admitted = {
        ModelRef(connection.connection_id, model.model_id)
        for connection in plan.catalog.connections for model in connection.models
    }
    reasons: dict[ModelRef, list[dict[str, str]]] = {}
    source_reasons: dict[str, list[dict[str, str]]] = {}
    for item in (*rejected, *order.ineligible):
        reason = {"reason": item.reason, "component": item.component}
        entries = (source_reasons.setdefault(item.ref.connection_id, []) if item.scope == "source"
                   else reasons.setdefault(item.ref, []))
        if reason not in entries:
            entries.append(reason)

    rows, seen = [], set()
    for connection in available.connections:
        for model in connection.models:
            ref = ModelRef(connection.connection_id, model.model_id)
            if ref in seen:
                raise ValueError("duplicate model option")
            seen.add(ref)
            candidate = candidates.get(ref)
            labels = [] if candidate is None else list(candidate.labels)
            if (ref.connection_id in plan.reconnect_sources
                    and "recent_sign_in_failure" not in labels):
                labels.append("recent_sign_in_failure")
            row_reasons = list(reasons.get(ref, []))
            for reason in source_reasons.get(ref.connection_id, []):
                # Missing enumeration is a source diagnostic, not a refusal of
                # an independently admitted provider-default invocation.
                #
                # `catalogue_refresh_pending` belongs in this set for the same
                # reason and is easy to miss: the client treats ANY reason on a
                # row as "not pickable", so leaving it on would make the
                # provider's own default unselectable while a background
                # refresh runs -- for a lane that never needed enumeration at
                # all. A warming catalogue must cost the enumerated rows, not
                # the source.
                if (model.model_id == "" and ref in admitted
                        and reason["reason"] in {
                            "native_catalogue_unavailable", "native_enumeration_unsupported",
                            "catalogue_refresh_pending", "catalogue_refresh_unavailable",
                        }):
                    continue
                if reason not in row_reasons:
                    row_reasons.append(reason)
            rows.append({
                "reference": {"provider_ref": ref.connection_id, "model_id": ref.model_id},
                "source_kind": connection.source_kind,
                "availability_basis": model.availability_basis or connection.availability_basis,
                "freshness": connection.freshness,
                "provider_default": connection.default_model_id == model.model_id,
                "tools": model.tools,
                "context_tokens": model.context_tokens,
                "input_modalities": sorted(model.modalities),
                "output_modalities": sorted(model.output_modalities),
                "pricing": {
                    "freshness": model.pricing.freshness,
                    "unmetered": model.pricing.unmetered,
                    "charges": [{"component": charge.component,
                                 "amount_micros": charge.amount_micros,
                                 "confirmed": charge.confirmed}
                                for charge in model.pricing.charges],
                    "unknown_components": sorted(model.pricing.unknown_components),
                },
                "scores": None if model.scores is None else {
                    "source": model.scores.source, "freshness": model.scores.freshness,
                    "agentic": model.scores.agentic, "general": model.scores.general,
                },
                "in_candidate_catalog": ref in admitted,
                "order_index": positions.get(ref),
                "basis": None if candidate is None else candidate.basis,
                "labels": labels,
                "reasons": row_reasons,
            })
    primary = plan.policy.current_selection or plan.policy.saved_default
    referenced = (() if primary is None else (primary,)) + plan.policy.fallbacks
    # Refused connections and absent saved ids are visible even when refresh
    # returned no model row. They never acquire an invented price or capability.
    missing = []
    for ref in (*referenced, *reasons):
        if ref in seen:
            continue
        seen.add(ref)
        missing.append({
            "reference": {"provider_ref": ref.connection_id, "model_id": ref.model_id},
            "reasons": reasons.get(ref) or [{"reason": "not_in_catalog", "component": ""}],
        })
    return {
        "kind": "advisory_model_options", "generation": plan.policy.generation,
        "policy_source": plan.policy_source, "mode": plan.policy.mode,
        "options": rows, "unavailable": missing,
        "source_failures": [{"provider_ref": provider, "reasons": entries}
                            for provider, entries in source_reasons.items()],
        "order": [{"provider_ref": item.ref.connection_id, "model_id": item.ref.model_id}
                  for item in order.candidates],
    }
