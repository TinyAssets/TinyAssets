"""Synthetic reproductions of cutover money loss and deletion races."""
import hashlib
import json
import sqlite3
import threading
import time
from types import SimpleNamespace

import pytest

from tests.support.admission import Plans, drive, projection
from tests.test_billing_boundary import SECRET, _subscription_event
from tests.test_broker_deletion_jobs import Remote
from tests.test_owner_cutover_census import source as source
from tinyassets.billing import stripe_adapter as stripe
from tinyassets.billing.events import apply_event
from tinyassets.billing.plan_action import _start_checkout
from tinyassets.box_layout_migration import census
from tinyassets.broker.admission import local_mutate
from tinyassets.broker.deletion_jobs import DeletionJobs


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
    if mutation == "unrecorded":
        assert report["ready"], report["refusals"]
        assert report["plans"]["account-0002"]["dropped_sessionless_attempts"] == 1
        return
    assert not report["ready"]
    assert report["refusals"][reason] == 1
    assert report["billing_refusals"]["account-0002"][reason] == 1


@pytest.fixture
def billing(tmp_path, monkeypatch):

    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(tmp_path, principal="alice", center="cc-alice")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    plans = Plans(tmp_path)
    plans.set_plan("alice", "free", event_created=0)
    remote = Remote(tmp_path / "off-host")
    job = {"id": hashlib.sha256(b"alice").hexdigest(),
           "billing_cleanup": {}, "completed_steps": []}
    from tinyassets.broker.owner_identities import OwnerIdentities

    service = DeletionJobs(
        SimpleNamespace(_owner_identities=OwnerIdentities(plans.admission_path)),
        records=remote,
    )
    service.records.save(job)
    def record_request(root, operation, **args):
        assert operation == "billing_receipt"
        service.record_billing(args["owner"], args["key"], args["value"])
    monkeypatch.setattr("tinyassets.broker.deletion_jobs.request", record_request)
    value = attempt()
    with sqlite3.connect(plans.path) as db:
        db.execute("UPDATE plans SET attempt=?", (json.dumps(value),))
    plans.retain_checkout("alice", value)
    return tmp_path, plans, value


def test_expired_checkout_mapping_survives_migration_and_new_attempt(source):
    value = attempt(time.time() - 5000)
    legacy_attempt(source, value)
    inventory = {}
    report = census(source, _inventory=inventory)
    assert report["ready"], report["refusals"]
    assert inventory["billing"]["bob"]["attempt"] is None
    assert inventory["billing"]["bob"]["settlements"] == [value]
    plans = Plans(source)
    plans.set_plan("bob", "free", event_created=0)
    plans.retain_checkout("bob", value)
    plans.begin_checkout_attempt("bob", now=time.time(), attempt_id="new", mode="test",
                                 params={}, lease_seconds=60)
    assert plans.owner_for_checkout(session_id=value["session_id"]) == "bob"
    assert plans.checkout_settlements("bob") == [value]


@pytest.mark.parametrize("identity", ["session_id", "attempt_id"])
def test_expired_checkout_mappings_cannot_conflict(source, identity):
    first = attempt(time.time() - 5000)
    second = dict(first, attempt_id="another-attempt", session_id="cs_another")
    second[identity] = first[identity]
    legacy_attempt(source, first, "b1")
    legacy_attempt(source, second, "b2")
    report = census(source)
    assert not report["ready"]
    assert report["refusals"]["conflicting_checkout_attempts"] == 1


def test_deletion_cancels_every_completed_session_after_attempts_are_cleared(billing, monkeypatch):
    root, plans, first = billing
    second = attempt(time.time() - 5000)
    second.update(attempt_id="older-attempt", session_id="cs_older")
    plans.retain_checkout("alice", second)
    for value in (first, second):
        assert apply_event(root, {"type": "checkout.session.completed", "data": {"object": {
            "id": value["session_id"], "customer": "cus_same",
            "subscription": "sub_" + value["session_id"],
        }}}) == {"settled": True}
    assert plans.read("alice")["attempt"] is None
    drive(local_mutate(plans.admission_path, "alice", "erase",
        routing=projection(plans.admission_path)))
    cancelled = []

    def get(path):
        name = path.rsplit("/", 1)[-1]
        return ({"id": name, "status": "complete", "customer": "cus_same",
                 "subscription": "sub_" + name} if path.startswith("checkout/") else
                {"id": name, "status": "active"})

    def cancel(path, fields, **kwargs):
        assert kwargs["method"] == "DELETE"
        name = path.rsplit("/", 1)[-1]
        cancelled.append(name)
        return {"id": name, "status": "canceled"}

    monkeypatch.setattr(stripe, "_get", get)
    monkeypatch.setattr(stripe, "_post", cancel)
    assert stripe.cancel_account_billing(root, "alice") == "cancelled"
    assert sorted(cancelled) == ["sub_cs_older", "sub_cs_synthetic"]
    assert plans.owner_for_checkout(session_id="cs_older") == "alice"
    assert plans.owner_for_checkout(session_id="cs_synthetic") == "alice"


def test_tombstone_refuses_checkout_and_expires_open_session(billing, monkeypatch):
    root, plans, value = billing
    drive(local_mutate(plans.admission_path, "alice", "erase",
        routing=projection(plans.admission_path)))
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
    saved = Remote(root / "off-host").load(hashlib.sha256(b"alice").hexdigest())
    cleanup = saved["billing_cleanup"]
    assert cleanup == {"cs_synthetic": "expired"}


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
    drive(local_mutate(plans.admission_path, "alice", "erase",
        routing=projection(plans.admission_path)))
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
    saved = Remote(root / "off-host").load(hashlib.sha256(b"alice").hexdigest())
    cleanup = saved["billing_cleanup"]
    assert cleanup["sub_tinyassets"] == "cancelled"


def test_completion_before_subscription_event_retains_cancellation_authority(billing, monkeypatch):
    root, plans, value = billing
    event = {"type": "checkout.session.completed", "data": {"object": {
        "id": value["session_id"], "customer": "cus_completed", "subscription": "sub_completed"}}}
    assert apply_event(root, event) == {"settled": True}
    assert plans.read("alice")["tier"] == "free"
    assert plans.read("alice")["event_created"] == 0
    assert plans.read("alice")["subscription_id"] == "sub_completed"
    drive(local_mutate(plans.admission_path, "alice", "erase",
        routing=projection(plans.admission_path)))
    monkeypatch.setattr(stripe, "_get", lambda path: {
        "id": value["session_id"], "status": "complete", "subscription": "sub_completed",
        "customer": "cus_completed"} if path.startswith("checkout/") else {
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
        db.execute("DELETE FROM checkout_settlements")
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


def test_erasure_preserves_inflight_checkout_until_confirmed_cleanup(billing, monkeypatch):
    from tinyassets import billing as adapter

    root, plans, value = billing
    value.update(session_id=None, url=None)
    with sqlite3.connect(plans.path) as db:
        db.execute("UPDATE plans SET attempt=?", (json.dumps(value),))
        db.execute("DELETE FROM checkout_settlements")
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
        drive(local_mutate(plans.admission_path, "alice", "erase",
            routing=projection(plans.admission_path)))
        deleted.set()

    checkout.start()
    assert entered.wait(5)
    deletion = threading.Thread(target=delete)
    deletion.start()
    try:
        assert deleted.wait(2)
        monkeypatch.setattr(stripe, "_get", lambda _: {"data": [], "has_more": False})
        with pytest.raises(stripe.BillingUnavailable, match="unresolved"):
            stripe._attempt_session(value)
        assert plans.read("alice")["attempt"] is not None
    finally:
        release.set()
        checkout.join(5)
        deletion.join(5)
    assert deleted.is_set() and result[0]["id"] == "cs_synthetic"
    assert json.loads(plans.read("alice")["attempt"])["session_id"] == "cs_synthetic"
    monkeypatch.setattr(stripe, "_get", lambda _: {"id": "cs_synthetic", "status": "open"})
    monkeypatch.setattr(stripe, "_post", lambda *a, **k: {
        "id": "cs_synthetic", "status": "expired"})
    assert stripe.cancel_account_billing(root, "alice") == "attempt_expired"
    assert plans.read("alice")["attempt"] is None


@pytest.mark.parametrize("status,tier,suspended", [("active", "paid", False),
                                                  ("past_due", "free", True)])
def test_disaster_resync_reads_current_subscription_through_adapter(
        tmp_path, monkeypatch, status, tier, suspended):
    from tests.support.broker_double import seed_owner_admission
    from tests.test_billing_boundary import _subscription_object

    seed_owner_admission(tmp_path, principal="alice", center="legacy-center")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setenv("STRIPE_PRICE_ID", "price_test")
    obj = _subscription_object(owner_id="alice", status=status)
    obj["customer"] = "cus_current"
    plans = Plans(tmp_path)
    plans.set_plan("alice", "free", event_created=1)
    calls = []
    def get(path):
        calls.append(path)
        if path.startswith("subscriptions?"):
            return {"data": [obj], "has_more": False}
        assert path == "subscriptions/sub_tinyassets"
        return obj
    monkeypatch.setattr(stripe, "_get", get)
    report = drive(stripe.restore_billing(tmp_path, 100, routing=plans.routing))
    assert report["plans_synced"] == 1
    assert calls == ["subscriptions?status=all&limit=100", "subscriptions/sub_tinyassets"]
    plan = plans.read("alice")
    assert plan["tier"] == tier
    from tinyassets.broker.admission import local_read

    assert ("billing" in local_read(tmp_path, "alice").reasons) is suspended
    assert plan["subscription_id"] == "sub_tinyassets"


def test_disaster_resync_migrated_signed_universe_subscription(tmp_path, monkeypatch):
    from tests.support.broker_double import seed_owner_admission
    from tests.test_billing_boundary import _subscription_object

    seed_owner_admission(tmp_path, principal="alice", center="legacy-center")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_synthetic")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    obj = _subscription_object(owner_id="legacy-center")
    obj["metadata"]["universe_id"] = obj["metadata"].pop("owner_id")
    obj["customer"] = "cus_migrated"
    obj["id"] = "sub_current"
    cancelled = dict(obj, id="sub_backup", status="canceled")
    plans = Plans(tmp_path)
    plans.set_plan("alice", "paid", event_created=1, customer_id="cus_migrated",
                   subscription_id=cancelled["id"])
    calls = []

    def get(path):
        calls.append(path)
        if path.startswith("subscriptions?"):
            return {"data": [cancelled, obj], "has_more": False}
        return {"subscriptions/sub_backup": cancelled, "subscriptions/sub_current": obj}[path]

    monkeypatch.setattr(stripe, "_get", get)
    assert drive(stripe.restore_billing(tmp_path, 100,
                                       routing=plans.routing))["plans_synced"] == 1
    assert calls == ["subscriptions?status=all&limit=100", "subscriptions/sub_current"]
    plan = plans.read("alice")
    assert plan["tier"] == "paid"
    assert plan["subscription_id"] == "sub_current"
    assert plan["customer_id"] == "cus_migrated"


def test_disaster_discovery_refuses_signed_owner_conflicting_with_customer(billing, monkeypatch):
    from tests.test_billing_boundary import _subscription_object

    root, plans, _ = billing
    plans.set_plan("alice", "paid", event_created=1, customer_id="cus_migrated",
                   subscription_id="sub_backup")
    plans.set_plan("bob", "free", event_created=0)
    obj = _subscription_object(owner_id="bob")
    obj.update(id="sub_current", customer="cus_migrated")
    before = {owner: plans.read(owner) for owner in ("alice", "bob")}
    calls = []

    def get(path):
        calls.append(path)
        assert path.startswith("subscriptions?")
        return {"data": [obj], "has_more": False}

    monkeypatch.setattr(stripe, "_get", get)
    with pytest.raises(stripe.BillingUnavailable, match="identity mismatch"):
        drive(stripe.restore_billing(root, 100, routing=plans.routing))
    assert calls == ["subscriptions?status=all&limit=100"]
    assert {owner: plans.read(owner) for owner in before} == before


def test_disaster_discovery_never_assigns_another_legacy_owner(billing, monkeypatch):
    from tests.test_billing_boundary import _subscription_object

    _, plans, _ = billing
    plans.set_plan("bob", "paid", event_created=1, customer_id="cus_bob",
                   subscription_id="sub_bob_backup")
    obj = _subscription_object(owner_id="legacy-bob")
    obj["metadata"]["universe_id"] = obj["metadata"].pop("owner_id")
    obj.update(id="sub_bob_current", customer="cus_bob")
    monkeypatch.setattr(stripe, "_get", lambda path: {"data": [obj], "has_more": False})
    assert stripe.find_active_subscription("alice", plans=plans) is None
    assert stripe.find_active_subscription("bob", plans=plans) == "sub_bob_current"


pytestmark = pytest.mark.verifies('owner-daemon-in-box:T2')
