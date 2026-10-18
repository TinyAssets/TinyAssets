"""Verified billing deliveries write only the shared owner plan store."""
from __future__ import annotations

from tinyassets.billing.lifecycle import serialized
from tinyassets.billing.plan_store import BillingPlans
from tinyassets.broker.owner_reads import PlanMissing


def subscription_owner(plans, obj, claimed_owner):
    """Resolve a verified signed identity through its permanent billing mapping."""
    try:
        owner = plans.owner_for(customer_id=str(obj.get("customer") or ""),
                                subscription_id=str(obj.get("id") or ""))
    except PlanMissing:
        if not (obj.get("metadata") or {}).get("owner_id"):
            raise PlanMissing("legacy billing delivery requires its cutover mapping")
        plans.read(claimed_owner)
        owner = claimed_owner
    if (obj.get("metadata") or {}).get("owner_id") and owner != claimed_owner:
        raise PermissionError("billing mapping disagrees with signed account")
    return owner


@serialized
def apply_event(root, event):
    from tinyassets.billing.stripe_adapter import (
        cancel_account_billing,
        cancel_deleted_subscription,
        checkout_settlement_from_event,
        subscription_end_from_event,
        subscription_state_from_event,
    )
    from tinyassets.router_credentials import DeletedOwners

    plans = BillingPlans(root)
    obj = (event.get("data") or {}).get("object") or {}
    settlement = checkout_settlement_from_event(event)
    if settlement is not None:
        # Resolve through the stored checkout identity, never a routing hint.
        session_id, attempt_id = settlement
        try:
            owner = plans.owner_for_checkout(session_id=session_id, attempt_id=attempt_id)
            if DeletedOwners(root).contains(owner):
                return {"settled": True, "billing": cancel_account_billing(root, owner)}
            if event["type"] == "checkout.session.completed":
                plans.bind_checkout_subscription(owner, customer_id=obj.get("customer"),
                                                  subscription_id=obj.get("subscription"))
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
    owner = subscription_owner(plans, obj, claimed_owner)
    if DeletedOwners(root).contains(owner):
        cancel_deleted_subscription(root, owner, subscription)
        plan = plans.read(owner)
        plans.apply_tier_event(owner, tier="free",
                              event_created=max(float(event.get("created") or 0),
                                                plan["event_created"]), ends_at=None,
                              customer_id=customer or None, subscription_id=subscription)
        return {"applied": False, "cancelled": True}
    applied = plans.apply_tier_event(
        owner, tier=tier, event_created=float(event.get("created") or 0),
        ends_at=subscription_end_from_event(event),
        customer_id=customer or None, subscription_id=subscription)
    return {"applied": applied, "tier": tier if applied else None}
