"""The billing adapter boundary, fail-soft posture, and webhook verification."""

from __future__ import annotations

import hashlib
import hmac
import pathlib
import re
import time

import pytest

from tinyassets.billing import (
    BillingUnavailable,
    billing_enabled,
    create_checkout_session,
    subscription_state_from_event,
)
from tinyassets.billing.stripe_adapter import verify_webhook_signature

REPO = pathlib.Path(__file__).resolve().parents[1]


def test_nothing_outside_the_billing_package_knows_stripe_exists():
    """The boundary that keeps the processor swappable and metering independent."""
    offenders = []
    pattern = re.compile(r"^\s*(?:import\s+stripe|from\s+stripe\b)", re.MULTILINE)
    for path in (REPO / "tinyassets").rglob("*.py"):
        if "billing" in path.parts:
            continue
        if pattern.search(path.read_text(encoding="utf-8", errors="ignore")):
            offenders.append(str(path.relative_to(REPO)))
    assert offenders == [], f"Stripe imported outside the adapter: {offenders}"


def test_the_adapter_itself_uses_no_stripe_sdk():
    """Stdlib only — an SDK would put a stripe import one install away everywhere."""
    source = (REPO / "tinyassets" / "billing" / "stripe_adapter.py").read_text(
        encoding="utf-8"
    )
    assert not re.search(r"^\s*(?:import\s+stripe|from\s+stripe\b)", source, re.M)


def test_billing_is_off_without_a_key_and_says_so(monkeypatch):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    assert billing_enabled() is False


def test_billing_is_off_without_webhook_authority(monkeypatch):
    """Do not take payment when the resulting entitlement cannot be authorized."""
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)
    assert billing_enabled() is False


def test_billing_is_enabled_only_with_both_secrets(monkeypatch):
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    assert billing_enabled() is True


def test_calls_fail_soft_rather_than_crashing_when_billing_is_off(monkeypatch, checkout_anchor):
    """A missing processor must be a clear refusal, never an unhandled error."""
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    with pytest.raises(BillingUnavailable):
        create_checkout_session(
            owner_id="u-1",
            attempt_id="attempt-one",
            params={
                "price_id": "price_x",
                "success_url": "https://x/ok",
                "cancel_url": "https://x/no",
                "expires_at": int(checkout_anchor + 2100),
                "entitlement_version": "1",
                "entitlement_claim": "claim",
            },
        )


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


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", "paid"),
        ("trialing", "paid"),
        ("past_due", "free"),
        ("canceled", "free"),
        ("unpaid", "free"),
        ("incomplete_expired", "free"),
    ],
)
def test_only_an_entitling_status_yields_the_paid_tier(status, expected):
    event = _subscription_event(status)
    assert subscription_state_from_event(event, secret=SECRET) == ("u-1", expected)


def test_deletion_always_returns_the_universe_to_free():
    event = _subscription_event("active", kind="customer.subscription.deleted")
    assert subscription_state_from_event(event, secret=SECRET) == ("u-1", "free")


def test_an_unrelated_event_moves_no_tier():
    """A payment on its own does not entitle — the subscription status decides."""
    assert subscription_state_from_event({"type": "invoice.paid", "data": {}}) is None


def test_an_event_without_a_universe_is_ignored():
    event = _subscription_event(owner_id="")
    assert subscription_state_from_event(event, secret=SECRET) is None


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


def test_a_stripe_error_message_is_not_returned_to_the_caller(monkeypatch):
    """Stripe quotes request params back, including customer identifiers."""
    import io
    import urllib.error

    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")

    body = (
        b'{"error":{"message":"No such customer: cus_SECRET123 for '
        b'subscription sub_PRIVATE456"}}'
    )

    def _raise(*a, **k):
        raise urllib.error.HTTPError(
            "https://api.stripe.com/v1/x", 400, "Bad Request", {}, io.BytesIO(body)
        )

    monkeypatch.setattr(stripe_adapter.urllib.request, "urlopen", _raise)

    with pytest.raises(stripe_adapter.BillingUnavailable) as caught:
        stripe_adapter._post("customers", [])

    message = str(caught.value)
    assert "cus_SECRET123" not in message
    assert "sub_PRIVATE456" not in message
    assert "400" in message


def test_price_lookup_rejects_a_misconfigured_plan(monkeypatch):
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(
        stripe_adapter,
        "_get",
        lambda *_a, **_k: {"data": [_plan_price(unit_amount=1)]},
    )
    with pytest.raises(stripe_adapter.BillingUnavailable, match="not the \\$20"):
        stripe_adapter.resolve_price_id()


def test_checkout_refuses_a_second_subscription_for_one_universe(monkeypatch, checkout_anchor):
    """Two completed sessions would bill the same universe twice."""
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(
        stripe_adapter, "find_active_subscription", lambda _u: "sub_existing"
    )

    with pytest.raises(stripe_adapter.AlreadySubscribed) as caught:
        stripe_adapter.create_checkout_session(
            owner_id="u-1",
            attempt_id="attempt-one",
            params={
                "price_id": "price_x",
                "success_url": "https://x/ok",
                "cancel_url": "https://x/no",
                "expires_at": int(checkout_anchor + 2100),
                "entitlement_version": "1",
                "entitlement_claim": "claim",
            },
        )
    assert caught.value.subscription_id == "sub_existing"


def test_finding_a_subscription_pages_past_the_first_hundred(monkeypatch):
    """Cancel searched only page one, so a later subscriber could not cancel."""
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    target = _subscription_object()
    target["id"] = "sub_target"
    pages = [
        {"data": [{"id": f"sub_{i}", "metadata": {}} for i in range(100)],
         "has_more": True},
        {"data": [target],
         "has_more": False},
    ]
    calls = {"n": 0}

    def _get(_path, **_k):
        out = pages[min(calls["n"], len(pages) - 1)]
        calls["n"] += 1
        return out

    monkeypatch.setattr(stripe_adapter, "_get", _get)
    assert stripe_adapter.find_active_subscription("u-1") == "sub_target"
    assert calls["n"] == 2, "must have paged"


def test_exhausting_pagination_fails_loudly_instead_of_reporting_none(monkeypatch):
    """Returning None would read as 'no subscription' and refuse a real cancel."""
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setattr(
        stripe_adapter,
        "_get",
        lambda *_a, **_k: {
            "data": [{"id": "sub_x", "metadata": {}}],
            "has_more": True,
        },
    )
    with pytest.raises(stripe_adapter.BillingUnavailable):
        stripe_adapter.find_active_subscription("u-1")


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


def test_checkout_sends_an_idempotency_key(monkeypatch, checkout_anchor):
    """A lost response must not turn a retry into a second subscription."""
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(stripe_adapter, "find_active_subscription", lambda _u: None)
    monkeypatch.setattr(stripe_adapter, "resolve_price_id", lambda: "price_x")

    seen = {}

    class _Resp:
        def read(self):
            return b'{"id":"cs_1","url":"https://checkout"}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(request, timeout=None):
        seen["key"] = request.get_header("Idempotency-key")
        seen["body"] = request.data.decode("utf-8")
        return _Resp()

    monkeypatch.setattr(stripe_adapter.urllib.request, "urlopen", _urlopen)
    stripe_adapter.create_checkout_session(
            owner_id="u-1",
            attempt_id="attempt-one",
            params={
                "price_id": "price_x",
                "success_url": "https://x/ok",
                "cancel_url": "https://x/no",
                "expires_at": int(checkout_anchor + 2100),
                "entitlement_version": "1",
                "entitlement_claim": "claim",
            },
        )
    assert seen["key"], "no idempotency key sent"
    assert seen["key"].startswith("checkout:")
    assert "tinyassets_entitlement_version" in seen["body"]
    assert "tinyassets_entitlement_claim" in seen["body"]


# --- subscription state (this branch's only durable state) -------------------


def test_a_stale_event_cannot_resurrect_a_cancelled_tier(tmp_path):
    """Stripe does not guarantee delivery order and retries deliveries."""
    from tinyassets.broker.owner_reads import Plans

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
    from tinyassets.broker.owner_reads import Plans

    def apply_tier_event(root, **kw):
        return Plans(root).apply("owner", **kw)
    def get_tier(root):
        return Plans(root).read("owner")["tier"]

    apply_tier_event(tmp_path, tier="free", event_created=500.0)
    assert apply_tier_event(tmp_path, tier="paid", event_created=500.0) is False
    assert get_tier(tmp_path) == "free"


def test_an_unreadable_shared_plan_fails_loudly(tmp_path):
    import sqlite3

    from tinyassets.broker.owner_reads import Plans

    plans = Plans(tmp_path)
    plans.initialize()
    plans.path.write_bytes(b"not a database")
    with pytest.raises(sqlite3.DatabaseError):
        plans.read("owner")



# --- 2026-08-28 billing review follow-ups ------------------------------------


@pytest.mark.parametrize("payment_status", ["unpaid", "no_payment_required", ""])
def test_a_completed_session_does_not_entitle_until_it_is_paid(payment_status):
    """Stripe emits checkout.session.completed for unpaid sessions too."""
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": "u-1",
                "mode": "subscription",
                "payment_status": payment_status,
            }
        },
    }
    assert subscription_state_from_event(event) is None


def test_a_paid_session_waits_for_the_authorized_subscription_event():
    """Expectation changed: payment proves money landed, not which plan we created."""
    event = {
        "type": "checkout.session.completed",
        "data": {
            "object": {
                "client_reference_id": "u-1",
                "mode": "subscription",
                "payment_status": "paid",
            }
        },
    }
    assert subscription_state_from_event(event, secret=SECRET) is None


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


@pytest.mark.parametrize("status", ["canceled", "incomplete_expired"])
def test_a_terminal_subscription_is_not_offered_for_cancellation(monkeypatch, status):
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    subscription = _subscription_object(status=status)
    subscription["id"] = "sub_x"
    monkeypatch.setattr(
        stripe_adapter,
        "_get",
        lambda *_a, **_k: {
            "data": [subscription],
            "has_more": False,
        },
    )
    assert stripe_adapter.find_active_subscription("u-1") is None


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


def test_webhook_refuses_a_declared_oversize_before_streaming(monkeypatch):
    import asyncio

    from tinyassets import onboarding

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)

    class _Request:
        headers = {"content-length": "262145"}
        streamed = False

        async def stream(self):
            self.streamed = True
            yield b"should not be read"

    request = _Request()
    response = asyncio.run(onboarding._handle_billing_webhook(request))
    assert response.status_code == 413
    assert request.streamed is False


def test_webhook_bounds_an_undeclared_chunked_body(monkeypatch):
    import asyncio

    from tinyassets import onboarding

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)

    class _Request:
        headers = {}

        async def stream(self):
            yield b"x" * 262_144
            yield b"y"

    response = asyncio.run(onboarding._handle_billing_webhook(_Request()))
    assert response.status_code == 413


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


def test_no_metering_machinery_is_exported():
    """Dead metering code implies a capability this branch does not have."""
    import tinyassets.billing as billing

    assert not hasattr(billing, "report_effect_usage")
    source = (
        REPO / "tinyassets" / "billing" / "stripe_adapter.py"
    ).read_text(encoding="utf-8")
    assert "meter_events" not in source


# --- 2026-08-28 live subscribe/cancel run: refusals reported as outages -------
#
# Found by driving the real webapp: subscribe worked, cancel worked, and then an
# immediate resubscribe told the user "Billing unavailable: tunnel origin returned
# 5xx". Two defects stacked -- the route answered a deliberate refusal with 503, and
# the claim guarding the finished checkout was still held.


@pytest.mark.parametrize(
    ("out", "expected"),
    [
        ({"url": "https://checkout.stripe.com/c/pay/cs_test_x"}, 200),
        ({"error": "already_subscribed"}, 409),
        ({"error": "checkout_already_in_progress"}, 409),
        ({"error": "billing_unavailable", "detail": "no key"}, 503),
    ],
)
def test_a_refusal_is_not_reported_as_a_service_outage(out, expected):
    """Only billing actually being unavailable may answer 5xx.

    This is not cosmetic. The Cloudflare Worker in front of production replaces the
    BODY of any origin 5xx with its own bad_gateway JSON, so a refusal sent as 503
    reaches the browser with our reason stripped out and the tunnel blamed.
    """
    from tinyassets.onboarding import _checkout_status

    assert _checkout_status(out) == expected


def test_an_unclassified_checkout_error_is_a_500_not_a_plausible_refusal():
    """An error string nobody classified is a bug here, and must read as one."""
    from tinyassets.onboarding import _checkout_status

    assert _checkout_status({"error": "something_new"}) == 500


def test_every_checkout_error_the_route_can_return_is_classified():
    """The table is only load-bearing if it actually covers the route.

    Greps the route body for the error strings it can return, so adding a new one
    without classifying it fails here instead of reaching a user as a 500.
    """
    from tinyassets.onboarding import _CHECKOUT_STATUS

    source = (REPO / "tinyassets" / "billing" / "plan_action.py").read_text(
        encoding="utf-8"
    )
    body = source.split("def _start_checkout")[1]
    returned = set(re.findall(r'"error":\s*"([a-z_]+)"', body))
    # Android never opens checkout. The action returns this before the lease.
    returned.add("unavailable_in_app")
    assert returned, "the grep found nothing - the anchor moved"
    assert returned <= set(_CHECKOUT_STATUS), (
        f"unclassified checkout errors: {sorted(returned - set(_CHECKOUT_STATUS))}"
    )


def test_the_user_facing_copy_covers_every_refusal_the_route_classifies():
    """A classified refusal with no message falls back to 'Billing unavailable'."""
    from tinyassets.onboarding import _CHECKOUT_STATUS
    from tinyassets.onboarding.app_modules import shell_source

    page = shell_source(REPO / "tinyassets" / "onboarding" / "app.html")
    block = page.split("const CHECKOUT_REFUSALS = {")[1].split("};")[0]
    named = set(re.findall(r"^\s*([a-z_]+):", block, re.M))
    refusals = {k for k, v in _CHECKOUT_STATUS.items() if v < 500}
    assert refusals <= named, f"no message for: {sorted(refusals - named)}"



# --- Codex round 1 on the fix above: the claim did not bound what it guarded --


def test_the_session_is_given_an_expiry_inside_its_claim(monkeypatch, checkout_anchor):
    """Sending no expires_at let Stripe apply its 24-HOUR default.

    The claim then expired 23h45m before the session it guarded stopped being
    payable, so a second session could be created alongside a still-completable
    first, and completing both billed one universe twice.
    """
    from tinyassets.storage.subscription_state import CHECKOUT_WINDOW_SECONDS

    anchor = checkout_anchor
    sent = _capture_checkout(monkeypatch, anchor=anchor)

    expires_at = int(sent["params"]["expires_at"])
    assert expires_at > anchor, "an expiry in the past would be rejected"
    assert expires_at - anchor >= 1800, "Stripe rejects expires_at under 30 minutes"
    assert expires_at - anchor < CHECKOUT_WINDOW_SECONDS, (
        "the session must stop being payable BEFORE its lease lapses"
    )


def test_an_aged_checkout_attempt_still_refuses(monkeypatch, checkout_anchor):
    """Fresh test setup must not relax the real remaining-expiry safety check."""
    with pytest.raises(BillingUnavailable, match="too old"):
        _capture_checkout(monkeypatch, anchor=checkout_anchor - 600)


def test_the_idempotency_key_identifies_the_attempt_not_the_clock(monkeypatch, checkout_anchor):
    """A wall-clock bucket replayed a COMPLETED session on resubscribe.

    Stripe keeps idempotency results for ~24h, so subscribing, cancelling, and
    resubscribing inside one bucket returned the original finished session -- a dead
    checkout URL. Two different attempts must be two different requests.
    """
    first = _capture_checkout(monkeypatch, anchor=checkout_anchor, attempt_id="a")
    same = _capture_checkout(monkeypatch, anchor=checkout_anchor, attempt_id="a")
    other = _capture_checkout(monkeypatch, anchor=checkout_anchor, attempt_id="b")

    assert first["key"] == same["key"], "a retry of one attempt must deduplicate"
    assert first["key"] != other["key"], "a new attempt must not replay the old one"


def test_a_retry_of_one_attempt_sends_identical_parameters(monkeypatch, checkout_anchor):
    """Stripe errors on a reused idempotency key with changed parameters.

    Deriving expires_at from the clock rather than the anchor would make every retry
    a parameter mismatch -- the retry path the key exists for would be the one that
    breaks.
    """
    first = _capture_checkout(monkeypatch, anchor=checkout_anchor, attempt_id="a")
    retry = _capture_checkout(monkeypatch, anchor=checkout_anchor, attempt_id="a")

    assert first["params"] == retry["params"]




# --- the return URLs we hand to Stripe ---------------------------------------


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, "https://tinyassets.io"),
        ({"origin": "https://tinyassets.io"}, "https://tinyassets.io"),
        ({"origin": "https://evil.example"}, "https://tinyassets.io"),
        ({"origin": "javascript:alert(1)"}, "https://tinyassets.io"),
        # A matching Host does NOT make an origin ours. Nothing enforces an
        # allowed-hosts list, so Host is attacker-controlled: an allowlist that
        # accepts whatever the client claims to be is not an allowlist. The first
        # version of this test asserted the OPPOSITE and so could not see the
        # bypass (Codex, 2026-08-28).
        (
            {"origin": "https://attacker.example", "host": "attacker.example"},
            "https://tinyassets.io",
        ),
        # Scheme is part of the identity: this origin is only ever served over HTTPS.
        ({"origin": "http://tinyassets.io"}, "https://tinyassets.io"),
        # A malformed authority must not raise out of a billing route.
        ({"origin": "https://["}, "https://tinyassets.io"),
        ({"origin": "https://tinyassets.io/"}, "https://tinyassets.io"),
        ({"origin": "HTTPS://TinyAssets.IO"}, "https://tinyassets.io"),
    ],
)
def test_checkout_return_urls_only_use_an_origin_we_serve(headers, expected):
    """These URLs leave our control the moment Stripe has them."""
    from tinyassets.onboarding import _checkout_return_origin

    class _Request:
        pass

    request = _Request()
    request.headers = headers
    assert _checkout_return_origin(request) == expected


def test_a_configured_public_resource_is_honoured(monkeypatch):
    """A deployment on another hostname configures it; it does not claim it."""
    from tinyassets import onboarding

    monkeypatch.setattr(
        onboarding, "app_config", lambda: {"resource": "https://staging.example/mcp"}
    )

    class _Request:
        headers = {"origin": "https://staging.example", "host": "staging.example"}

    assert onboarding._checkout_return_origin(_Request()) == "https://staging.example"


def test_a_malformed_configured_resource_does_not_raise(monkeypatch):
    from tinyassets import onboarding

    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://["})

    class _Request:
        headers = {"origin": "https://evil.example"}

    assert onboarding._checkout_return_origin(_Request()) == "https://tinyassets.io"



def test_the_checkout_route_actually_applies_the_origin_allowlist(
    tmp_path, monkeypatch
):
    """Testing the helper alone passes while the route stops calling it.

    Mutation-checked: replacing the route's call with the old raw-header read leaves
    the helper's own test green, so this one drives the route and reads the URL that
    reached Stripe.
    """
    import asyncio

    import tinyassets.api.helpers as helpers
    from tinyassets import onboarding
    from tinyassets.billing import stripe_adapter

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "current_identity", lambda: None, raising=False)
    monkeypatch.setattr(onboarding, "_read_home", lambda _i: "u-origin")
    monkeypatch.setattr(helpers, "_universe_dir", lambda u: tmp_path / u)

    from tinyassets.auth.middleware import current_identity
    from tinyassets.broker.owner_reads import Plans

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    Plans(tmp_path).set_plan(current_identity().user_id, "free", event_created=0)
    sent: dict = {}

    def _params(*, owner_id, success_url, cancel_url, expires_at):
        # The return URLs are frozen HERE, which is the point: whatever the route
        # decided the origin was is what gets persisted and later replayed.
        sent["success"] = success_url
        sent["cancel"] = cancel_url
        return {
            "price_id": "price_x",
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": int(expires_at),
            "entitlement_version": "1",
            "entitlement_claim": "claim",
        }

    def _create(*, owner_id, attempt_id, params):
        return {"id": "cs_1", "url": "https://checkout.stripe.com/x"}

    import tinyassets.billing as billing

    monkeypatch.setattr(stripe_adapter, "checkout_params", _params)
    monkeypatch.setattr(stripe_adapter, "create_checkout_session", _create)
    monkeypatch.setattr(billing, "create_checkout_session", _create)

    class _Request:
        headers = {"origin": "https://evil.example", "host": "tinyassets.io"}

    response = asyncio.run(onboarding._handle_billing_checkout(_Request()))
    assert response.status_code == 200
    assert sent["success"].startswith("https://tinyassets.io/"), sent
    assert "evil.example" not in sent["success"] + sent["cancel"]


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


def test_a_scheduled_cancellation_is_reported():
    from tinyassets.billing import subscription_end_from_event

    assert subscription_end_from_event(_cancelling_event(), secret=SECRET) == (
        1_790_000_000.0
    )


def test_a_renewing_subscription_has_no_end_to_show():
    from tinyassets.billing import subscription_end_from_event

    assert subscription_end_from_event(_subscription_event(), secret=SECRET) is None


def test_a_deleted_subscription_reports_no_end():
    """It is gone; the tier says so. A date would imply it is still coming."""
    from tinyassets.billing import subscription_end_from_event

    event = _cancelling_event(kind="customer.subscription.deleted")
    assert subscription_end_from_event(event, secret=SECRET) is None


def test_an_unclaimed_subscription_cannot_inject_an_end_date():
    """The date goes through the same authorization as entitlement does."""
    from tinyassets.billing import subscription_end_from_event

    event = _cancelling_event()
    event["data"]["object"]["metadata"]["tinyassets_entitlement_claim"] = "forged"
    assert subscription_end_from_event(event, secret=SECRET) is None


def test_the_end_date_is_stored_and_cleared_with_the_tier(tmp_path):
    from tinyassets.broker.owner_reads import Plans

    plans = Plans(tmp_path)
    plans.set_plan("owner", "paid", event_created=100, ends_at=500)
    assert plans.read("owner")["ends_at"] == 500
    plans.set_plan("owner", "paid", event_created=200)
    assert plans.read("owner")["ends_at"] is None


def test_a_rejected_event_cannot_leave_its_end_date_behind(tmp_path):
    """The date must never outlive the tier decision it belonged to."""
    from tinyassets.broker.owner_reads import Plans

    def apply_tier_event(root, **kw):
        return Plans(root).apply("owner", **kw)
    def get_plan(root):
        return {k: Plans(root).read("owner")[k] for k in ("tier", "ends_at")}

    apply_tier_event(tmp_path, tier="free", event_created=300.0)
    assert apply_tier_event(
        tmp_path, tier="paid", event_created=100.0, ends_at=999.0
    ) is False
    assert get_plan(tmp_path) == {"tier": "free", "ends_at": None}


def test_the_webhook_persists_the_end_date_through_the_route(tmp_path, monkeypatch):
    """Driven through the route: the wiring is what regressed before."""
    import asyncio
    import json as _json

    import tinyassets.api.helpers as helpers
    from tinyassets import onboarding
    from tinyassets.broker.owner_reads import Plans

    monkeypatch.setattr(onboarding, "onboarding_enabled", lambda: True)
    monkeypatch.setattr(helpers, "_universe_dir", lambda u: tmp_path / u)
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")

    universe = "u-ending"
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    Plans(tmp_path).set_plan(universe, "free", event_created=0)
    event = _cancelling_event(owner_id=universe)
    event["created"] = 1_000
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
    ).status_code == 200
    assert {k: Plans(tmp_path).read(universe)[k] for k in ("tier", "ends_at")} == {
        "tier": "paid",
        "ends_at": 1_790_000_000.0,
    }


def test_the_page_shows_the_end_date_and_stops_offering_a_second_cancel():
    """The UI half. A stored date nobody renders fixes nothing."""
    from tinyassets.onboarding.app_modules import shell_source

    page = shell_source(REPO / "tinyassets" / "onboarding" / "app.html")
    assert "planEndsOn" in page
    render = page.split("function renderPlan()")[1].split("\n  }")[0]
    assert "ends " in render, "the chip must name the date"
    click = page.split("function onPlanClick()")[1].split("\n  }")[0]
    assert "already cancelled" in click, (
        "clicking an already-cancelled plan must not offer to cancel again"
    )



# --- the test -> live switch -------------------------------------------------
#
# The one guard that only matters once, and matters absolutely then: a TEST-mode
# subscription is free for anyone to create with card 4242. If a test endpoint's
# signing secret were left configured on a live deployment, those would entitle.


def test_an_event_from_the_wrong_stripe_mode_is_refused(tmp_path, monkeypatch):
    import asyncio
    import json as _json

    import tinyassets.api.helpers as helpers
    from tinyassets import onboarding
    from tinyassets.broker.owner_reads import Plans

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
    from tinyassets import onboarding
    from tinyassets.broker.owner_reads import Plans

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


def test_matching_modes_pass(monkeypatch):
    from tinyassets.billing import event_mode_matches_key

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    assert event_mode_matches_key({"livemode": False}) is True
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_live_x")
    assert event_mode_matches_key({"livemode": True}) is True


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


def test_a_v2_claim_survives_rolling_the_stripe_webhook_secret(monkeypatch):
    """The property the whole change exists for."""
    from tinyassets.billing.stripe_adapter import _authorized_subscription_owner

    obj = _v2_subscription(monkeypatch)
    assert _authorized_subscription_owner(obj, secret=SECRET) == "u-1"

    # Stripe's signing secret is rolled. The subscription must still authorize.
    assert _authorized_subscription_owner(obj, secret="whsec_rolled") == "u-1"


def test_a_v1_claim_does_not_survive_it(monkeypatch):
    """Stated, not assumed. This is exactly why v1 had to be replaced."""
    from tinyassets.billing.stripe_adapter import _authorized_subscription_owner

    obj = _subscription_object()  # v1, signed with SECRET
    assert _authorized_subscription_owner(obj, secret=SECRET) == "u-1"
    assert _authorized_subscription_owner(obj, secret="whsec_rolled") == ""


def test_v1_subscriptions_keep_working_after_the_upgrade(monkeypatch):
    """Upgrading the scheme must not orphan what is already sold."""
    from tinyassets.billing.stripe_adapter import _authorized_subscription_owner

    monkeypatch.setenv("TINYASSETS_BILLING_ENTITLEMENT_KEY", ENTITLEMENT_KEY)
    obj = _subscription_object()  # sold under v1
    assert _authorized_subscription_owner(obj, secret=SECRET) == "u-1"


def test_new_checkouts_issue_v2_once_the_key_exists(monkeypatch, checkout_anchor):
    sent = _capture_checkout(monkeypatch, anchor=checkout_anchor)
    assert sent["params"][
        "subscription_data[metadata][tinyassets_entitlement_version]"
    ] == "1", "without the key, the old scheme"

    monkeypatch.setenv("TINYASSETS_BILLING_ENTITLEMENT_KEY", ENTITLEMENT_KEY)
    sent = _capture_checkout(monkeypatch, anchor=checkout_anchor)
    assert sent["params"][
        "subscription_data[metadata][tinyassets_entitlement_version]"
    ] == "2"


@pytest.mark.parametrize("version", ["", "0", "3", "v2", None])
def test_an_unrecognised_entitlement_version_authorizes_nothing(version, monkeypatch):
    from tinyassets.billing.stripe_adapter import _authorized_subscription_owner

    monkeypatch.setenv("TINYASSETS_BILLING_ENTITLEMENT_KEY", ENTITLEMENT_KEY)
    obj = _v2_subscription(monkeypatch)
    if version is None:
        obj["metadata"].pop("tinyassets_entitlement_version")
    else:
        obj["metadata"]["tinyassets_entitlement_version"] = version
    assert _authorized_subscription_owner(obj, secret=SECRET) == ""


def test_a_v2_claim_cannot_be_verified_with_the_v1_key(monkeypatch):
    """The version selects the key; a mismatched pairing must not validate."""
    from tinyassets.billing.stripe_adapter import _authorized_subscription_owner

    obj = _v2_subscription(monkeypatch)
    obj["metadata"]["tinyassets_entitlement_version"] = "1"
    assert _authorized_subscription_owner(obj, secret=SECRET) == ""


def test_every_stripe_request_pins_the_api_version(monkeypatch, checkout_anchor):
    """Unversioned requests inherit a DASHBOARD setting we do not control."""
    from tinyassets.billing import stripe_adapter

    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    seen: dict = {}

    class _Resp:
        def read(self):
            return b'{"data": []}'

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    def _urlopen(request, timeout=None):
        seen["version"] = request.get_header("Stripe-version")
        return _Resp()

    monkeypatch.setattr(stripe_adapter.urllib.request, "urlopen", _urlopen)
    stripe_adapter._get("prices?limit=1")
    assert seen["version"] == stripe_adapter.STRIPE_API_VERSION

    sent = _capture_checkout(monkeypatch, anchor=checkout_anchor)
    assert sent.get("api_version") == stripe_adapter.STRIPE_API_VERSION
