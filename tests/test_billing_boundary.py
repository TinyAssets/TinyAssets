"""The billing adapter boundary, fail-soft posture, and webhook verification."""

from __future__ import annotations

import hashlib
import hmac
import pathlib
import time

import pytest

from tinyassets.billing import (
    subscription_state_from_event,
)
from tinyassets.billing.stripe_adapter import verify_webhook_signature

REPO = pathlib.Path(__file__).resolve().parents[1]














@pytest.fixture
def checkout_anchor():
    """Start each attempt at test execution, not potentially slow collection.

    Reuse this one value for every retry within a test, preserving frozen checkout
    parameters. Production's minimum remaining expiry check stays untouched.
    """
    return time.time()


# --- webhook verification ----------------------------------------------------

SECRET = "whsec_test_secret"
PRICE_ID = "price_tinyassets_monthly"


def _plan_price(*, price_id: str = PRICE_ID, **overrides):
    price = {
        "id": price_id,
        "lookup_key": "tinyassets_paid_monthly",
        "unit_amount": 2_000,
        "currency": "usd",
        "recurring": {
            "interval": "month",
            "interval_count": 1,
            "usage_type": "licensed",
        },
    }
    price.update(overrides)
    return price


def _subscription_object(
    *,
    owner_id: str = "u-1",
    status: str = "active",
    secret: str = SECRET,
    price: dict | None = None,
    version: str = "1",
) -> dict:
    from tinyassets.billing.stripe_adapter import _entitlement_claim

    selected_price = price or _plan_price()
    # Sign with the SAME version stamped in metadata. Letting the signature follow
    # the ambient env while the metadata said "1" produced an object no verifier
    # should ever accept, and the fixture's meaning changed with the environment.
    return {
        "id": "sub_tinyassets",
        "status": status,
        "metadata": {
            "owner_id": owner_id,
            "tinyassets_entitlement_version": version,
            "tinyassets_entitlement_claim": _entitlement_claim(
                owner_id, selected_price["id"], secret=secret, version=version
            ),
        },
        "items": {"data": [{"quantity": 1, "price": selected_price}]},
    }


def _subscription_event(
    status: str = "active",
    *,
    kind: str = "customer.subscription.updated",
    livemode: bool = False,
    **kwargs,
) -> dict:
    # Real Stripe events always carry `livemode`, and the webhook refuses one whose
    # mode does not match the configured key. Test mode is the default here because
    # the suite runs with an sk_test key.
    return {
        "type": kind,
        "livemode": livemode,
        "data": {"object": _subscription_object(status=status, **kwargs)},
    }


def _sign(payload: bytes, timestamp: int, secret: str = SECRET) -> str:
    mac = hmac.new(
        secret.encode(), f"{timestamp}.".encode() + payload, hashlib.sha256
    ).hexdigest()
    return f"t={timestamp},v1={mac}"


def test_a_valid_signature_verifies():
    payload = b'{"type":"customer.subscription.deleted"}'
    now = time.time()
    header = _sign(payload, int(now))
    assert verify_webhook_signature(payload, header, now=now, secret=SECRET) is True


def test_a_forged_signature_is_rejected():
    """Otherwise anyone reaching the endpoint could grant themselves the paid tier."""
    payload = b'{"type":"checkout.session.completed"}'
    now = time.time()
    forged = f"t={int(now)},v1={'0' * 64}"
    assert verify_webhook_signature(payload, forged, now=now, secret=SECRET) is False


def test_a_tampered_payload_is_rejected():
    now = time.time()
    header = _sign(b'{"amount":1}', int(now))
    assert (
        verify_webhook_signature(b'{"amount":9999}', header, now=now, secret=SECRET)
        is False
    )


def test_a_replayed_signature_is_rejected():
    """A captured webhook must not be replayable hours later."""
    payload = b'{"type":"checkout.session.completed"}'
    signed_at = time.time() - 3600
    header = _sign(payload, int(signed_at))
    assert (
        verify_webhook_signature(payload, header, now=time.time(), secret=SECRET)
        is False
    )


@pytest.mark.parametrize("header", ["", "garbage", "t=,v1=", "v1=abc", "t=abc,v1=def"])
def test_a_malformed_signature_header_is_rejected(header):
    assert (
        verify_webhook_signature(b"{}", header, now=time.time(), secret=SECRET) is False
    )


def test_verification_fails_closed_without_a_configured_secret(monkeypatch):
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    payload = b"{}"
    header = _sign(payload, int(time.time()))
    assert verify_webhook_signature(payload, header, now=time.time()) is False


# --- event -> tier mapping ---------------------------------------------------










def test_an_authentic_but_unclaimed_subscription_cannot_move_a_tier():
    """Stripe provenance alone does not authorize arbitrary metadata as entitlement."""
    obj = _subscription_object()
    obj["metadata"].pop("tinyassets_entitlement_claim")
    event = {"type": "customer.subscription.updated", "data": {"object": obj}}
    assert subscription_state_from_event(event, secret=SECRET) is None


def test_an_entitlement_claim_cannot_be_moved_to_another_universe():
    obj = _subscription_object(owner_id="u-1")
    obj["metadata"]["owner_id"] = "u-2"
    event = {"type": "customer.subscription.updated", "data": {"object": obj}}
    assert subscription_state_from_event(event, secret=SECRET) is None


@pytest.mark.parametrize(
    "price",
    [
        _plan_price(lookup_key="some_other_plan"),
        _plan_price(unit_amount=1),
        _plan_price(currency="eur"),
        _plan_price(recurring={"interval": "year", "interval_count": 1}),
    ],
)
def test_a_claimed_subscription_for_the_wrong_plan_cannot_move_a_tier(price):
    event = _subscription_event(price=price)
    assert subscription_state_from_event(event, secret=SECRET) is None


# --- Codex REJECT 2026-08-28 follow-ups --------------------------------------












def _capture_checkout(
    monkeypatch, *, anchor: float, owner_id: str = "u-1",
    attempt_id: str = "attempt-one",
):
    """Run create_checkout_session against a stub Stripe and return what it sent."""
    import urllib.parse

    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(stripe_adapter, "find_active_subscription", lambda _u: None)
    monkeypatch.setattr(stripe_adapter, "resolve_price_id", lambda: "price_x")

    seen: dict = {}

    class _Resp:
        def read(self):
            return b'{"id":"cs_1","url":"https://checkout"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(request, timeout=None):
        seen["key"] = request.get_header("Idempotency-key")
        seen["api_version"] = request.get_header("Stripe-version")
        seen["params"] = dict(
            urllib.parse.parse_qsl(request.data.decode("utf-8"))
        )
        return _Resp()

    monkeypatch.setattr(stripe_adapter.urllib.request, "urlopen", _urlopen)
    params = stripe_adapter.checkout_params(
        owner_id=owner_id,
        success_url="https://x/ok",
        cancel_url="https://x/no",
        expires_at=int(anchor + 2100),
    )
    stripe_adapter.create_checkout_session(
        owner_id=owner_id,
        attempt_id=attempt_id,
        params=params,
    )
    return seen




# --- subscription state (this branch's only durable state) -------------------


def test_a_stale_event_cannot_resurrect_a_cancelled_tier(tmp_path):
    """Stripe does not guarantee delivery order and retries deliveries."""
    from tests.support.admission import Plans

    def apply_tier_event(root, **kw):
        return Plans(root).apply("owner", **kw)
    def get_tier(root):
        return Plans(root).read("owner")["tier"]

    assert apply_tier_event(tmp_path, tier="free", event_created=200.0) is True
    assert apply_tier_event(tmp_path, tier="paid", event_created=100.0) is False
    assert get_tier(tmp_path) == "free", "a stale event must not re-entitle"
    assert apply_tier_event(tmp_path, tier="paid", event_created=300.0) is True
    assert get_tier(tmp_path) == "paid"


def test_a_same_second_cancel_beats_a_same_second_activate(tmp_path):
    """Stripe timestamps are second-granularity, so ties are ordinary."""
    from tests.support.admission import Plans

    def apply_tier_event(root, **kw):
        return Plans(root).apply("owner", **kw)
    def get_tier(root):
        return Plans(root).read("owner")["tier"]

    apply_tier_event(tmp_path, tier="free", event_created=500.0)
    assert apply_tier_event(tmp_path, tier="paid", event_created=500.0) is False
    assert get_tier(tmp_path) == "free"


def test_an_unreadable_shared_plan_fails_loudly(tmp_path):
    import sqlite3

    from tests.support.admission import Plans

    plans = Plans(tmp_path)
    plans.initialize()
    plans.path.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        plans.read("owner")



# --- 2026-08-28 billing review follow-ups ------------------------------------






@pytest.mark.parametrize("status", ["trialing", "past_due", "unpaid", "active"])
def test_cancel_finds_every_billable_status_not_only_active(monkeypatch, status):
    """trialing is treated as PAID, and past_due/unpaid still bill.

    Searching only status=active made cancel report "no subscription" while the
    customer kept being charged.
    """
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    subscription = _subscription_object(status=status)
    subscription["id"] = "sub_t"
    monkeypatch.setattr(
        stripe_adapter,
        "_get",
        lambda *_a, **_k: {
            "data": [subscription],
            "has_more": False,
        },
    )
    assert stripe_adapter.find_active_subscription("u-1") == "sub_t"




def test_cancel_ignores_an_unclaimed_subscription(monkeypatch):
    """Cancellation uses the same service-created plan authority as entitlement."""
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    subscription = _subscription_object()
    subscription["metadata"].pop("tinyassets_entitlement_claim")
    monkeypatch.setattr(
        stripe_adapter,
        "_get",
        lambda *_a, **_k: {"data": [subscription], "has_more": False},
    )
    assert stripe_adapter.find_active_subscription("u-1") is None






def test_the_claim_outlives_the_session_it_guards():
    """The invariant the old constants only APPEARED to state.

    They asserted that two of our own numbers matched each other, which says nothing
    about how long Stripe keeps a session payable. Nothing sent `expires_at`, so
    Stripe applied its 24-hour default and the session outlived its guard by nearly a
    day. What has to hold is that the claim is the LONGER of the two.
    """
    from tinyassets.storage.subscription_state import (
        CHECKOUT_SESSION_SECONDS,
        CHECKOUT_WINDOW_SECONDS,
    )

    assert CHECKOUT_WINDOW_SECONDS > CHECKOUT_SESSION_SECONDS
    assert CHECKOUT_SESSION_SECONDS >= 1800, "Stripe rejects expires_at under 30 min"




# --- 2026-08-28 live subscribe/cancel run: refusals reported as outages -------
#
# Found by driving the real webapp: subscribe worked, cancel worked, and then an
# immediate resubscribe told the user "Billing unavailable: tunnel origin returned
# 5xx". Two defects stacked -- the route answered a deliberate refusal with 503, and
# the claim guarding the finished checkout was still held.











# --- Codex round 1 on the fix above: the claim did not bound what it guarded --












# --- the return URLs we hand to Stripe ---------------------------------------











# --- a cancellation the user can actually see --------------------------------
#
# Cancelling leaves the subscription ACTIVE until the period ends, so "paid" alone
# cannot distinguish a user who cancelled from one who did not. On reload the chip
# read "Paid plan" exactly as before and offered to cancel again, which invited the
# conclusion that the first cancellation had not worked.


def _cancelling_event(**kwargs):
    event = _subscription_event("active", **kwargs)
    event["data"]["object"]["cancel_at_period_end"] = True
    event["data"]["object"]["current_period_end"] = 1_790_000_000
    return event



















# --- the test -> live switch -------------------------------------------------
#
# The one guard that only matters once, and matters absolutely then: a TEST-mode
# subscription is free for anyone to create with card 4242. If a test endpoint's
# signing secret were left configured on a live deployment, those would entitle.


def test_an_event_from_the_wrong_stripe_mode_is_refused(tmp_path, monkeypatch):
    import asyncio
    import json as _json

    import tinyassets.api.helpers as helpers
    from tests.support.admission import Plans
    from tinyassets import onboarding

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(helpers, "_universe_dir", lambda u: tmp_path / u)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")

    universe = "u-mode"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    Plans(tmp_path).set_plan(universe, "free", event_created=0)
    # A LIVE-mode event arriving at a TEST-mode deployment.
    event = _subscription_event(owner_id=universe, livemode=True)
    payload = _json.dumps(event).encode()

    class _Request:
        headers = {
            "content-length": str(len(payload)),
            "stripe-signature": _sign(payload, int(time.time())),
        }

        async def stream(self):
            yield payload

    response = asyncio.run(onboarding._handle_billing_webhook(_Request()))
    assert response.status_code == 400
    assert Plans(tmp_path).read(universe)["tier"] == "free", "no cross-mode entitlement"


def test_a_test_mode_event_cannot_entitle_a_live_deployment(tmp_path, monkeypatch):
    """The direction that actually costs money."""
    import asyncio
    import json as _json

    import tinyassets.api.helpers as helpers
    from tests.support.admission import Plans
    from tinyassets import onboarding

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(helpers, "_universe_dir", lambda u: tmp_path / u)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_pretend")

    universe = "u-livemode"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    Plans(tmp_path).set_plan(universe, "free", event_created=0)
    event = _subscription_event(owner_id=universe, livemode=False)
    payload = _json.dumps(event).encode()

    class _Request:
        headers = {
            "content-length": str(len(payload)),
            "stripe-signature": _sign(payload, int(time.time())),
        }

        async def stream(self):
            yield payload

    assert asyncio.run(
        onboarding._handle_billing_webhook(_Request())
    ).status_code == 400
    assert Plans(tmp_path).read(universe)["tier"] == "free"


@pytest.mark.parametrize("livemode", [None, "true", 1, "", {}])
def test_a_missing_or_non_boolean_livemode_is_a_mismatch(livemode, monkeypatch):
    """Stripe always sends a real boolean. Anything else is not something to guess."""
    from tinyassets.billing import event_mode_matches_key

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    event = {"type": "customer.subscription.updated"}
    if livemode is not None:
        event["livemode"] = livemode
    assert event_mode_matches_key(event) is False




# --- the entitlement key must not be Stripe's to rotate ----------------------
#
# v1 signed the claim with the STRIPE WEBHOOK SECRET. Stripe tells you to roll that
# periodically, and you must roll it the moment it leaks — which nearly happened on
# 2026-08-28. Rolling it invalidates the claim on every subscription already sold, and
# those subscriptions can then never move a tier again.


ENTITLEMENT_KEY = "tinyassets_entitlement_key_for_tests"


def _v2_subscription(monkeypatch, *, owner_id="u-1"):
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("TINYASSETS_BILLING_ENTITLEMENT_KEY", ENTITLEMENT_KEY)
    assert stripe_adapter.entitlement_key() == ENTITLEMENT_KEY
    return _subscription_object(owner_id=owner_id, version="2")
















pytestmark = pytest.mark.verifies('owner-daemon-in-box:T2')
