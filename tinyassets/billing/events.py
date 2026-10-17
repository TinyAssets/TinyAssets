"""Verified billing deliveries write only the shared owner plan store."""
from __future__ import annotations

from tinyassets.billing.plan_store import BillingPlans
from tinyassets.broker.owner_reads import PlanMissing


def apply_event(root, event):
    from tinyassets.billing.stripe_adapter import (
        checkout_settlement_from_event,
        subscription_end_from_event,
        subscription_state_from_event,
    )

    plans = BillingPlans(root)
    obj = (event.get("data") or {}).get("object") or {}
    settlement = checkout_settlement_from_event(event)
    if settlement is not None:
        # Resolve through the stored checkout identity, never a routing hint.
        session_id, attempt_id = settlement
        try:
            owner = plans.owner_for_checkout(session_id=session_id, attempt_id=attempt_id)
            settled = plans.settle_checkout_attempt(
                owner, session_id=session_id, attempt_id=attempt_id)
        except PlanMissing:
            settled = False
        return {"settled": settled}
    mapped = subscription_state_from_event(event)
    if mapped is None:
        return {"applied": False}
    claimed_owner, tier = mapped
    customer = str(obj.get("customer") or "")
    subscription = str(obj.get("id") or "")
    try:
        owner = plans.owner_for(customer_id=customer, subscription_id=subscription)
    except PlanMissing:
        # The first service-signed subscription establishes the durable mapping.
        # The signed owner is an account, never a path or a home reference.
        if not (obj.get("metadata") or {}).get("owner_id"):
            raise PlanMissing("legacy billing delivery requires its cutover mapping")
        plans.read(claimed_owner)
        owner = claimed_owner
    if (obj.get("metadata") or {}).get("owner_id") and owner != claimed_owner:
        raise PermissionError("billing mapping disagrees with signed account")
    applied = plans.apply_tier_event(
        owner, tier=tier, event_created=float(event.get("created") or 0),
        ends_at=subscription_end_from_event(event),
        customer_id=customer or None, subscription_id=subscription)
    return {"applied": applied, "tier": tier if applied else None}
