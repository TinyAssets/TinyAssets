"""The checkout lease: three money races that a bare timestamp could not close.

The old lock stored *when* a checkout started and nothing else, so it could not say
which Stripe Checkout Session it was guarding. Every test here is one consequence of
that, driven through the real route or the real storage rather than around them.
"""

from __future__ import annotations

import asyncio
import json
import time

import pytest

from tinyassets import onboarding
from tinyassets.billing import stripe_adapter
from tinyassets.broker.owner_reads import Plans
from tinyassets.storage.subscription_state import CHECKOUT_WINDOW_SECONDS

OWNER = "billing-owner"


@pytest.fixture(autouse=True)
def plans(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    store = Plans(tmp_path)
    store.set_plan(OWNER, "free", event_created=0)
    return store


PARAMS = {
    "price_id": "price_x",
    "success_url": "https://tinyassets.io/app?subscribed=1",
    "cancel_url": "https://tinyassets.io/app?subscribed=0",
    "expires_at": 2_000_000_000,
    "entitlement_version": "2",
    "entitlement_claim": "claim",
}


# --- storage ----------------------------------------------------------------


def test_two_concurrent_checkouts_cannot_both_begin(tmp_path):
    now = time.time()
    first = Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="a", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    )
    second = Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="b", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    )
    assert first is not None and first["attempt_id"] == "a"
    assert second is None, "a pending session is not yet a subscription to refuse"


def test_a_lease_expires_so_an_abandoned_checkout_does_not_lock_forever(tmp_path):
    now = time.time()
    Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="a", mode="test", params=PARAMS,
        lease_seconds=60,
    )
    assert Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now + 30, attempt_id="b", mode="test", params=PARAMS,
        lease_seconds=60,
    ) is None
    assert Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now + 120, attempt_id="b", mode="test", params=PARAMS,
        lease_seconds=60,
    ) is not None


def test_settling_releases_only_the_lease_it_names(tmp_path):
    """The race the old unconditional DELETE could not see.

    A delayed terminal event for an OLD session must not release the lease protecting
    a session that is pending right now.
    """
    now = time.time()
    Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="live-one", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    )
    Plans(tmp_path).record_checkout_session(OWNER, attempt_id="live-one", session_id="cs_live", url="https://c/live"
    )

    # An event for a DIFFERENT, older session arrives.
    assert Plans(tmp_path).settle_checkout_attempt(OWNER, session_id="cs_old") is False
    assert Plans(tmp_path).settle_checkout_attempt(OWNER, attempt_id="old-attempt") is False
    assert Plans(tmp_path).current_checkout_attempt(OWNER, now=now) is not None

    # Its own event does release it.
    assert Plans(tmp_path).settle_checkout_attempt(OWNER, session_id="cs_live") is True
    assert Plans(tmp_path).current_checkout_attempt(OWNER, now=now) is None


def test_a_terminal_event_can_settle_before_the_session_id_was_recorded(tmp_path):
    """Stripe can deliver `checkout.session.completed` before we write the id.

    Without the attempt id on the session's own metadata there would be nothing to
    match, the handler would 200, and the lease would sit until it expired.
    """
    now = time.time()
    Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="pending", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    )
    assert Plans(tmp_path).current_checkout_attempt(OWNER, now=now)["session_id"] is None

    assert Plans(tmp_path).settle_checkout_attempt(OWNER, session_id="", attempt_id="pending")
    assert Plans(tmp_path).current_checkout_attempt(OWNER, now=now) is None


def test_recording_a_session_onto_a_replaced_attempt_writes_nothing(tmp_path):
    """A Stripe call in flight must not revive a lease that has since been settled."""
    now = time.time()
    Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="gone", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    )
    Plans(tmp_path).settle_checkout_attempt(OWNER, attempt_id="gone")

    assert Plans(tmp_path).record_checkout_session(OWNER, attempt_id="gone", session_id="cs_x", url="https://c/x"
    ) is False
    assert Plans(tmp_path).current_checkout_attempt(OWNER, now=now) is None


def test_a_corrupt_attempt_blocks_rather_than_being_overwritten(tmp_path):
    """Whatever it was may still be payable."""
    now = time.time()
    Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="a", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    )
    import sqlite3

    with sqlite3.connect(Plans(tmp_path).path) as conn:
        conn.execute(
            "UPDATE plans SET attempt = ? WHERE owner = ?",
            ("{not json", OWNER),
        )
    assert Plans(tmp_path).current_checkout_attempt(OWNER, now=now).get("__corrupt__") is True
    assert Plans(tmp_path).begin_checkout_attempt(
        OWNER, now=now, attempt_id="b", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    ) is None


# --- through the route -------------------------------------------------------


@pytest.fixture
def route(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets.auth import middleware

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(middleware, "current_identity", lambda: SimpleNamespace(user_id=OWNER))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    return tmp_path


class _Request:
    headers = {"origin": "https://tinyassets.io", "host": "tinyassets.io"}


def _install_stripe(monkeypatch, *, create, params=None):
    import tinyassets.billing as billing

    def _params(*, owner_id, success_url, cancel_url, expires_at):
        return {**PARAMS, "expires_at": int(expires_at)}

    monkeypatch.setattr(stripe_adapter, "checkout_params", params or _params)
    monkeypatch.setattr(stripe_adapter, "create_checkout_session", create)
    monkeypatch.setattr(billing, "create_checkout_session", create)


def test_a_second_click_returns_the_SAME_session(route, monkeypatch):
    """The lockout, closed. Refusing here is what made an abandoned checkout a
    33-minute wall; the URL stays valid while the session is open, so a second click
    is just the same checkout again."""
    calls: list[str] = []

    def _create(*, owner_id, attempt_id, params):
        calls.append(attempt_id)
        return {"id": "cs_1", "url": "https://checkout.stripe.com/one"}

    _install_stripe(monkeypatch, create=_create)

    first = asyncio.run(onboarding._handle_billing_checkout(_Request()))
    second = asyncio.run(onboarding._handle_billing_checkout(_Request()))

    assert json.loads(bytes(first.body))["url"] == "https://checkout.stripe.com/one"
    body = json.loads(bytes(second.body))
    assert body["url"] == "https://checkout.stripe.com/one"
    assert body["resumed"] is True
    assert second.status_code == 200, "a resumed checkout is not a refusal"
    assert len(calls) == 1, "Stripe must not be asked for a second session"


def test_an_ambiguous_failure_keeps_the_lease_so_a_retry_replays(route, monkeypatch):
    """The lost-response path that produced two subscriptions.

    On a timeout we do not know whether Stripe made a session, so the lease must
    survive: the retry reuses the same attempt id and Stripe replays.
    """
    seen: list[str] = []

    def _create(*, owner_id, attempt_id, params):
        seen.append(attempt_id)
        if len(seen) == 1:
            raise stripe_adapter.BillingAmbiguous("Stripe unreachable: TimeoutError")
        return {"id": "cs_1", "url": "https://checkout.stripe.com/one"}

    _install_stripe(monkeypatch, create=_create)

    failed = asyncio.run(onboarding._handle_billing_checkout(_Request()))
    assert failed.status_code == 503
    assert Plans(route).current_checkout_attempt(OWNER, now=time.time()) is not None

    retried = asyncio.run(onboarding._handle_billing_checkout(_Request()))
    assert json.loads(bytes(retried.body))["url"].endswith("/one")
    assert seen[0] == seen[1], "the retry must reuse the attempt, not mint a new one"


def test_a_definite_refusal_releases_the_lease(route, monkeypatch):
    """Stripe answered, so no session exists. A misconfiguration must not lock the
    universe out for the whole lease."""

    def _create(*, owner_id, attempt_id, params):
        raise stripe_adapter.BillingUnavailable("Stripe rejected the request")

    _install_stripe(monkeypatch, create=_create)

    assert asyncio.run(onboarding._handle_billing_checkout(_Request())).status_code == 503
    assert Plans(route).current_checkout_attempt(OWNER, now=time.time()) is None


def test_a_test_mode_lease_is_never_resumed_against_a_live_key(route, monkeypatch):
    """Going live must not hand anyone a sandbox checkout URL."""
    def _create(*, owner_id, attempt_id, params):
        return {"id": "cs_test", "url": "https://checkout.stripe.com/TEST"}

    _install_stripe(monkeypatch, create=_create)
    asyncio.run(onboarding._handle_billing_checkout(_Request()))
    assert Plans(route).current_checkout_attempt(OWNER, now=time.time())["mode"] == "test"

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_x")

    def _create_live(*, owner_id, attempt_id, params):
        return {"id": "cs_live", "url": "https://checkout.stripe.com/LIVE"}

    _install_stripe(monkeypatch, create=_create_live)
    out = json.loads(bytes(
        asyncio.run(onboarding._handle_billing_checkout(_Request())).body
    ))
    assert out["url"].endswith("/LIVE"), "a test-mode URL must never be served live"


def test_a_subscription_event_no_longer_releases_the_lease(route, monkeypatch):
    """It cannot say WHICH session it belongs to, so it must not release any."""
    import hashlib
    import hmac

    secret = "whsec_test_secret"
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", secret)

    now = time.time()
    Plans(route).begin_checkout_attempt(
        OWNER, now=now, attempt_id="pending", mode="test", params=PARAMS,
        lease_seconds=CHECKOUT_WINDOW_SECONDS,
    )

    price = {
        "id": "price_x",
        "lookup_key": "tinyassets_paid_monthly",
        "unit_amount": 2_000,
        "currency": "usd",
        "recurring": {"interval": "month", "interval_count": 1,
                      "usage_type": "licensed"},
    }
    claim = stripe_adapter._entitlement_claim(
        OWNER, "price_x", secret=secret, version="1"
    )
    event = {
        "type": "customer.subscription.created",
        "livemode": False,
        "created": int(now),
        "data": {"object": {
            "id": "sub_1", "status": "active",
            "metadata": {
                "owner_id": OWNER,
                "tinyassets_entitlement_version": "1",
                "tinyassets_entitlement_claim": claim,
            },
            "items": {"data": [{"quantity": 1, "price": price}]},
        }},
    }
    payload = json.dumps(event).encode()
    ts = int(now)
    mac = hmac.new(secret.encode(), f"{ts}.".encode() + payload,
                   hashlib.sha256).hexdigest()

    class _Hook:
        headers = {"content-length": str(len(payload)),
                   "stripe-signature": f"t={ts},v1={mac}"}

        async def stream(self):
            yield payload

    assert asyncio.run(onboarding._handle_billing_webhook(_Hook())).status_code == 200
    assert Plans(route).current_checkout_attempt(OWNER, now=now) is not None, (
        "only a terminal Checkout Session event may release a lease"
    )


@pytest.mark.parametrize(
    ("kind", "settles"),
    [
        ("checkout.session.completed", True),
        ("checkout.session.expired", True),
        ("checkout.session.async_payment_failed", False),
    ],
)
def test_only_terminal_session_events_identify_a_settlement(kind, settles):
    """A non-terminal session is still payable; releasing then allows a second."""
    event = {
        "type": kind,
        "data": {"object": {"id": "cs_1", "metadata": {"tinyassets_attempt_id": "a"}}},
    }
    got = stripe_adapter.checkout_settlement_from_event(event)
    assert (got is not None) is settles
    if settles:
        assert got == ("cs_1", "a")
