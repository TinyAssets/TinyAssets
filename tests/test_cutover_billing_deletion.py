"""Synthetic reproductions of cutover money loss and deletion races."""
import json
import sqlite3
import threading
import time

import pytest

from tests.test_billing_boundary import SECRET, _subscription_event
from tests.test_owner_cutover_census import source as source
from tinyassets.billing import stripe_adapter as stripe
from tinyassets.billing.events import apply_event
from tinyassets.billing.plan_action import _start_checkout
from tinyassets.box_layout_migration import census
from tinyassets.broker.owner_reads import Plans
from tinyassets.router_credentials import DeletedOwners, tombstone


def attempt(now=None):
    now = time.time() if now is None else now
    return dict(attempt_id="synthetic-attempt", created_at=now, expires_at=now + 2220,
                mode="test", session_id="cs_synthetic", url="https://checkout.invalid/session",
                params=dict(price_id="price_synthetic", expires_at=int(now + 2100),
                            success_url="https://tinyassets.io/ok",
                            cancel_url="https://tinyassets.io/no",
                            entitlement_version="1", entitlement_claim="synthetic-claim"))


def legacy_attempt(root, value, center="b1"):
    with sqlite3.connect(root / center / ".subscription_state.db") as db:
        db.execute("CREATE TABLE IF NOT EXISTS subscription_meta "
                   "(key TEXT PRIMARY KEY, value TEXT)")
        db.execute("INSERT OR REPLACE INTO subscription_meta VALUES ('checkout_attempt_v1',?)",
                   (value if isinstance(value, str) else json.dumps(value),))


def test_paid_legacy_without_verified_mapping_refuses_and_redacts(source):
    with sqlite3.connect(source / ".broker/plans.db") as db:
        db.execute("DELETE FROM plans")
    report = census(source)
    assert not report["ready"]
    assert report["refusals"]["paid_subscription_mapping_missing"] == 1
    assert report["billing_refusals"] == {
        "account-0001": {"paid_subscription_mapping_missing": 1}}
    assert "alice" not in json.dumps(report)


def test_verified_mapping_and_open_checkout_are_private_census_inventory(source):
    original = attempt()
    legacy_attempt(source, original)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report["ready"], report["refusals"]
    assert inventory["billing"]["alice"]["subscription_id"] == "sub_legacy"
    assert inventory["billing"]["alice"]["customer_id"] == "cus_legacy"
    assert inventory["billing"]["bob"]["attempt"] == original
    for private in ("sub_legacy", "cus_legacy", original["url"], "synthetic-claim"):
        assert private not in json.dumps(report)


@pytest.mark.parametrize("mutation,reason", [
    ("json", "malformed_checkout_attempt"),
    ("expiry", "malformed_checkout_attempt"),
    ("params", "malformed_checkout_attempt"),
    ("unrecorded", "ambiguous_checkout_attempt"),
    ("two", "conflicting_checkout_attempts"),
])
def test_legacy_checkout_refuses_ambiguity(source, mutation, reason):
    value = attempt()
    if mutation == "json":
        value = "not-json"
    elif mutation == "expiry":
        value["expires_at"] = float("nan")
    elif mutation == "params":
        value["params"] = {}
    elif mutation == "unrecorded":
        value.update(session_id=None, url=None)
    elif mutation == "two":
        other = attempt()
        other.update(attempt_id="another", session_id="cs_another")
        legacy_attempt(source, other, "b2")
    legacy_attempt(source, value)
    report = census(source)
    assert not report["ready"]
    assert report["refusals"][reason] == 1
    assert report["billing_refusals"]["account-0002"][reason] == 1


@pytest.fixture
def billing(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    plans = Plans(tmp_path)
    plans.set_plan("alice", "free", event_created=0)
    value = attempt()
    with sqlite3.connect(plans.path) as db:
        db.execute("UPDATE plans SET attempt=?", (json.dumps(value),))
    return tmp_path, plans, value


def test_tombstone_refuses_checkout_and_expires_open_session(billing, monkeypatch):
    root, plans, value = billing
    tombstone(root, "alice", {})
    calls = []
    monkeypatch.setattr(stripe, "_get", lambda path: {
        "id": value["session_id"], "status": "open"})

    def post(path, fields, **kwargs):
        calls.append(path)
        assert path == "checkout/sessions/cs_synthetic/expire"
        return {"id": value["session_id"], "status": "expired"}

    monkeypatch.setattr(stripe, "_post", post)
    assert _start_checkout(owner="alice", origin="https://tinyassets.io") == {
        "error": "account_deleted"}
    assert stripe.cancel_account_billing(root, "alice") == "attempt_expired"
    assert calls == ["checkout/sessions/cs_synthetic/expire"]
    assert plans.read("alice")["attempt"] is None
    assert DeletedOwners(root).receipt("alice")["billing_cleanup"] == {"cs_synthetic": "expired"}


@pytest.mark.parametrize("status", ["open", "complete", "wrong_id", "outage"])
def test_deletion_never_drops_an_unconfirmed_payable_attempt(billing, monkeypatch, status):
    root, plans, value = billing
    monkeypatch.setattr(stripe, "_get", lambda path: {
        "id": value["session_id"], "status": "open"})

    def post(*args, **kwargs):
        if status == "outage":
            raise stripe.BillingUnavailable("synthetic outage")
        return {"id": "wrong" if status == "wrong_id" else value["session_id"], "status": status}

    monkeypatch.setattr(stripe, "_post", post)
    with pytest.raises(stripe.BillingUnavailable):
        stripe.cancel_account_billing(root, "alice")
    assert plans.read("alice")["attempt"] is not None


def test_completed_checkout_cancels_subscription_before_settling(billing, monkeypatch):
    root, plans, _ = billing
    calls = []

    def get(path):
        calls.append(path)
        return ({"id": "cs_synthetic", "status": "complete", "subscription": "sub_result",
                 "customer": "cus_result"}
                if path.startswith("checkout/") else {"id": "sub_result", "status": "active"})

    monkeypatch.setattr(stripe, "_get", get)
    monkeypatch.setattr(stripe, "_post", lambda path, fields, **kw: (
        calls.append((path, kw["method"])) or {"id": "sub_result", "status": "canceled"}))
    assert stripe.cancel_account_billing(root, "alice") == "cancelled"
    assert calls[-1] == ("subscriptions/sub_result", "DELETE")
    assert plans.read("alice")["attempt"] is None
    assert plans.read("alice")["subscription_id"] == "sub_result"


def test_late_signed_subscription_is_cancelled_and_recorded(billing, monkeypatch):
    root, plans, _ = billing
    tombstone(root, "alice", {"completed_phases": ["billing"]})
    event = _subscription_event(owner_id="alice")
    event["created"] = 90
    event["data"]["object"]["customer"] = "cus_late"
    calls = []
    monkeypatch.setattr(stripe, "_get", lambda path: {
        "id": "sub_tinyassets", "status": "active"})
    monkeypatch.setattr(stripe, "_post", lambda path, fields, **kw: (
        calls.append((path, kw["method"])) or {"id": "sub_tinyassets", "status": "canceled"}))
    assert apply_event(root, event) == {"applied": False, "cancelled": True}
    assert calls == [("subscriptions/sub_tinyassets", "DELETE")]
    assert plans.read("alice")["tier"] == "free"
    assert DeletedOwners(root).receipt("alice")["billing_cleanup"]["sub_tinyassets"] == "cancelled"


def test_completion_before_subscription_event_retains_cancellation_authority(billing, monkeypatch):
    root, plans, value = billing
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": value["session_id"], "customer": "cus_completed", "subscription": "sub_completed"}}}
    assert apply_event(root, event) == {"settled": True}
    assert plans.read("alice")["tier"] == "free"
    assert plans.read("alice")["event_created"] == 0
    assert plans.read("alice")["subscription_id"] == "sub_completed"
    tombstone(root, "alice", {})
    monkeypatch.setattr(stripe, "_get", lambda path: {
        "id": "sub_completed", "status": "active"})
    calls = []
    monkeypatch.setattr(stripe, "_post", lambda path, fields, **kw: (
        calls.append((path, kw["method"])) or {"id": "sub_completed", "status": "canceled"}))
    assert stripe.cancel_account_billing(root, "alice") == "cancelled"
    assert calls == [("subscriptions/sub_completed", "DELETE")]


def test_lost_session_response_is_reconciled_before_deletion(billing, monkeypatch):
    root, plans, value = billing
    value.update(session_id=None, url=None)
    with sqlite3.connect(plans.path) as db:
        db.execute("UPDATE plans SET attempt=?", (json.dumps(value),))
    monkeypatch.setattr(stripe, "_get", lambda path: {"has_more": False, "data": [{
        "id": "cs_recovered", "status": "open",
        "metadata": {stripe.ATTEMPT_ID_KEY: value["attempt_id"]}}]})
    calls = []
    monkeypatch.setattr(stripe, "_post", lambda path, fields: (
        calls.append(path) or {"id": "cs_recovered", "status": "expired"}))
    assert stripe.cancel_account_billing(root, "alice") == "attempt_expired"
    assert calls == ["checkout/sessions/cs_recovered/expire"]
    assert plans.read("alice")["attempt"] is None


@pytest.mark.parametrize("expiry", [0, -1, float("nan"), float("inf")])
def test_malformed_attempt_never_disappears_during_deletion(billing, monkeypatch, expiry):
    root, plans, value = billing
    value["expires_at"] = expiry
    with sqlite3.connect(plans.path) as db:
        db.execute("UPDATE plans SET attempt=?", (json.dumps(value),))
    monkeypatch.setattr(stripe, "_get",
                        lambda path: pytest.fail("malformed attempt reached billing"))
    with pytest.raises(stripe.BillingUnavailable, match="malformed"):
        stripe.cancel_account_billing(root, "alice")
    assert plans.read("alice")["attempt"] is not None


def test_tombstone_waits_for_inflight_checkout_then_cleanup_sees_it(billing, monkeypatch):
    from tinyassets import billing as adapter

    root, plans, value = billing
    value.update(session_id=None, url=None)
    with sqlite3.connect(plans.path) as db:
        db.execute("UPDATE plans SET attempt=?", (json.dumps(value),))
    entered, release, deleted = threading.Event(), threading.Event(), threading.Event()
    result = []

    def create(**kwargs):
        entered.set()
        assert release.wait(5)
        return {"id": "cs_synthetic", "url": "https://checkout.invalid/session"}

    monkeypatch.setattr(adapter, "create_checkout_session", create)
    checkout = threading.Thread(target=lambda: result.append(
        _start_checkout(owner="alice", origin="https://tinyassets.io")))

    def delete():
        tombstone(root, "alice", {})
        deleted.set()

    checkout.start()
    assert entered.wait(5)
    deletion = threading.Thread(target=delete)
    deletion.start()
    try:
        assert not deleted.wait(.1)
    finally:
        release.set()
        checkout.join(5)
        deletion.join(5)
    assert deleted.is_set() and result[0]["id"] == "cs_synthetic"
    assert json.loads(plans.read("alice")["attempt"])["session_id"] == "cs_synthetic"
