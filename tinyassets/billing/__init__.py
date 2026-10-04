"""Billing adapter boundary.

Everything Stripe-shaped lives under this package and nowhere else. This slice owns
only a flat subscription and cancellation path; usage metering, quotas, and
enforcement are deliberately absent. Two consequences are deliberate:

* Stripe being absent or unreachable cannot accidentally grant paid entitlement.
* The processor stays swappable, because no caller outside here knows it exists.

`tests/test_billing_boundary.py` asserts that property rather than trusting it.
"""

from tinyassets.billing.stripe_adapter import (
    SECRET_ENV_NAMES,
    BillingUnavailable,
    billing_enabled,
    cancel_subscription,
    create_checkout_session,
    event_mode_matches_key,
    subscription_end_from_event,
    subscription_state_from_event,
)

__all__ = [
    "SECRET_ENV_NAMES",
    "BillingUnavailable",
    "billing_enabled",
    "cancel_subscription",
    "create_checkout_session",
    "subscription_end_from_event",
    "event_mode_matches_key",
    "subscription_state_from_event",
]
