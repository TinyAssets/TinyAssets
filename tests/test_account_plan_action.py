"""The shared plan/checkout action.

One function, every surface. The surface may change the label, the return URL,
and whether a purchase can open. It must not change the plan, the limits, the
usage, or whether an upgrade exists.
"""
from __future__ import annotations

import time

import pytest

from tinyassets.billing.plan_action import (
    CAN_PURCHASE_ON_SURFACE,
    Surface,
    account_plan,
)
from tinyassets.usage_policy import limits_for

PARAMS = {
    "price_id": "price_x",
    "entitlement_version": "1",
    "entitlement_claim": "claim",
}


@pytest.fixture
def home(tmp_path, monkeypatch, _signed_in_operator):
    from tinyassets.broker.owner_reads import Plans

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test")
    plans = Plans(tmp_path)
    plans.set_plan(_signed_in_operator.user_id, "free", event_created=0)
    return plans


def _plans(identity):
    return {surface: account_plan(identity, surface) for surface in Surface}


def test_every_surface_gets_the_same_plan_limits_and_eligibility(home, _signed_in_operator):
    plans = _plans(_signed_in_operator)
    expected = limits_for("free")
    first = plans[Surface.WEB].eligibility()

    assert first["tier"] == "free"
    assert first["upgrade_available"] is True
    assert first["upgrade_unavailable_reason"] is None
    assert first["limits"]["seats"] == expected.seats
    assert first["limits"]["storage_bytes"] == int(expected.storage_bytes)
    assert "usage" not in first
    for surface, plan in plans.items():
        assert plan.eligibility() == first, surface.value


def test_only_the_purchase_flag_and_label_follow_the_surface(home, _signed_in_operator):
    plans = _plans(_signed_in_operator)

    for surface, plan in plans.items():
        assert plan.can_purchase_on_surface is CAN_PURCHASE_ON_SURFACE[surface]
    android = plans[Surface.ANDROID]
    assert android.can_purchase_on_surface is False
    assert android.purchase_unavailable_reason == "unavailable_in_app"
    assert android.checkout_mode == "unavailable_in_app"
    assert android.checkout_url is None
    assert android.label == "Free"
    assert plans[Surface.WEB].label == "Upgrade"
    assert plans[Surface.WEB].checkout_mode == "ready"
    assert plans[Surface.API].can_purchase_on_surface is True
    assert plans[Surface.AGENT].can_purchase_on_surface is True


def test_billing_off_is_the_same_refusal_on_every_surface(home, monkeypatch, _signed_in_operator):
    monkeypatch.delenv("STRIPE_SECRET_KEY", raising=False)
    monkeypatch.delenv("STRIPE_WEBHOOK_SECRET", raising=False)

    plans = _plans(_signed_in_operator)
    first = plans[Surface.WEB].eligibility()
    assert first["upgrade_available"] is False
    assert first["upgrade_unavailable_reason"] == "billing_disabled"
    assert first["billing_enabled"] is False
    for surface, plan in plans.items():
        assert plan.eligibility() == first, surface.value
    assert plans[Surface.ANDROID].checkout_mode == "unavailable_in_app"
    assert plans[Surface.WEB].can_purchase_on_surface is True
    assert plans[Surface.WEB].checkout_mode == "unavailable"


def test_a_paid_plan_is_not_an_upgrade_on_any_surface(home, monkeypatch, _signed_in_operator):
    home.set_plan(_signed_in_operator.user_id, "paid", event_created=1,
                  ends_at=1_700_000_000.0)
    plans = _plans(_signed_in_operator)
    first = plans[Surface.WEB].eligibility()
    expected = limits_for("paid")
    assert first["tier"] == "paid"
    assert first["ends_at"] == 1_700_000_000.0
    assert first["upgrade_available"] is False
    assert first["upgrade_unavailable_reason"] == "already_subscribed"
    assert first["limits"]["seats"] == expected.seats
    assert first["upgrade_url"] is None
    for surface, plan in plans.items():
        assert plan.eligibility() == first, surface.value
        assert plan.label == "Paid plan"


def test_android_checkout_does_not_open_a_session(home, monkeypatch, _signed_in_operator):
    def _forbidden(**_kwargs):
        raise AssertionError("Android must not start a Stripe session")

    monkeypatch.setattr(
        "tinyassets.billing.stripe_adapter.create_checkout_session", _forbidden
    )
    monkeypatch.setattr("tinyassets.billing.create_checkout_session", _forbidden)

    read = account_plan(_signed_in_operator, Surface.ANDROID)
    started = account_plan(
        _signed_in_operator,
        Surface.ANDROID,
        intent="checkout",
        requested_origin="https://evil.example",
    )
    assert started.eligibility() == read.eligibility()
    assert started.checkout_body() == {"error": "unavailable_in_app"}
    assert started.checkout_url is None


def test_web_checkout_returns_a_session_and_api_returns_to_the_canonical_app(
    home, monkeypatch, _signed_in_operator
):
    import tinyassets.billing as billing
    from tinyassets.billing import stripe_adapter

    seen: dict[str, str] = {}

    def _params(*, owner_id, success_url, cancel_url, expires_at):
        seen["success"] = success_url
        seen["cancel"] = cancel_url
        seen["universe"] = owner_id
        return {
            **PARAMS,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": int(expires_at),
        }

    def _create(*, owner_id, attempt_id, params):
        return {"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"}

    monkeypatch.setattr(stripe_adapter, "checkout_params", _params)
    monkeypatch.setattr(stripe_adapter, "create_checkout_session", _create)
    monkeypatch.setattr(billing, "create_checkout_session", _create)

    web = account_plan(
        _signed_in_operator,
        Surface.WEB,
        intent="checkout",
        requested_origin="https://evil.example",
    )
    assert web.checkout_url == "https://checkout.stripe.com/c/pay/cs_test_1"
    assert web.checkout_mode == "session"
    assert web.session_id == "cs_test_1"
    assert seen["success"] == "https://tinyassets.io/app?subscribed=1"
    assert seen["cancel"] == "https://tinyassets.io/app?subscribed=0"
    assert web.eligibility()["tier"] == "free"
    assert web.eligibility()["upgrade_available"] is True

    # A second web checkout resumes the same session rather than creating another.
    again = account_plan(_signed_in_operator, Surface.WEB, intent="checkout")
    assert again.checkout_body()["resumed"] is True
    assert again.checkout_url == web.checkout_url

    seen.clear()
    attempt = home.current_checkout_attempt(_signed_in_operator.user_id, now=time.time())
    home.settle_checkout_attempt(_signed_in_operator.user_id, attempt_id=str(attempt["attempt_id"]))

    api = account_plan(
        _signed_in_operator,
        Surface.API,
        intent="checkout",
        requested_origin="https://evil.example",
    )
    assert api.checkout_url == "https://checkout.stripe.com/c/pay/cs_test_1"
    assert seen["success"] == "https://tinyassets.io/app?subscribed=1"
    assert api.eligibility() == web.eligibility()


def test_a_configured_web_origin_is_the_only_non_canonical_return(
    home, monkeypatch, _signed_in_operator
):
    import tinyassets.billing as billing
    from tinyassets.billing import stripe_adapter

    seen: dict[str, str] = {}

    def _params(*, owner_id, success_url, cancel_url, expires_at):
        seen["success"] = success_url
        return {
            **PARAMS,
            "success_url": success_url,
            "cancel_url": cancel_url,
            "expires_at": int(expires_at),
        }

    monkeypatch.setattr(stripe_adapter, "checkout_params", _params)
    monkeypatch.setattr(
        stripe_adapter,
        "create_checkout_session",
        lambda **_kwargs: {"id": "cs_2", "url": "https://checkout.stripe.com/c/pay/cs_2"},
    )
    monkeypatch.setattr(
        billing,
        "create_checkout_session",
        lambda **_kwargs: {"id": "cs_2", "url": "https://checkout.stripe.com/c/pay/cs_2"},
    )

    account_plan(
        _signed_in_operator,
        Surface.WEB,
        intent="checkout",
        requested_origin="https://staging.example",
        configured_resource="https://staging.example/mcp",
    )
    assert seen["success"] == "https://staging.example/app?subscribed=1"

    attempt = home.current_checkout_attempt(_signed_in_operator.user_id, now=time.time())
    home.settle_checkout_attempt(_signed_in_operator.user_id, attempt_id=str(attempt["attempt_id"]))
    seen.clear()
    account_plan(
        _signed_in_operator,
        Surface.AGENT,
        intent="checkout",
        requested_origin="https://staging.example",
        configured_resource="https://staging.example/mcp",
    )
    assert seen["success"] == "https://tinyassets.io/app?subscribed=1"


def test_missing_plan_fails_on_every_surface(tmp_path, monkeypatch, _signed_in_operator):
    from tinyassets.broker.owner_reads import PlanMissing

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    for surface in Surface:
        with pytest.raises(PlanMissing):
            account_plan(_signed_in_operator, surface)
