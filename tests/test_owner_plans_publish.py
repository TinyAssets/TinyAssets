"""Shared billing and publication through real broker/relay and a separate box."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.support.broker_double import seed_owner_admission
from tests.support.broker_ipc import broker  # noqa: F401
from tests.test_billing_boundary import SECRET, _sign, _subscription_event
from tests.test_owner_broker_egress import box  # noqa: F401
from tinyassets import onboarding, owner_runtime
from tinyassets.broker.owner_reads import PlanMissing, Plans

BOX_CODE = """
import json, sys
from pathlib import Path
from tinyassets import owner_runtime
from tinyassets.broker.owner_reads import PlanMissing
from tinyassets.commons_catalog import publish_definition
from tinyassets.owner_channel import request
from tinyassets.storage.subscription_state import get_tier
from tinyassets.universe_owner import account_type_for_universe
root, relay, mode = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
root.mkdir(exist_ok=True)
owner_runtime.OWNER_MOUNT = root
owner_runtime._is_owner_mount = lambda candidate: candidate == root
runtime = owner_runtime.start(relay)
try:
    if mode == 'tier':
        print(json.dumps({'tier': get_tier(root / 'center')}))
    elif mode == 'missing':
        for read in (get_tier, account_type_for_universe):
            try:
                read(root / 'center')
            except PlanMissing:
                pass
            else:
                raise AssertionError('missing plan read as an entitlement')
        print('{}')
    else:
        payload = {'schema_version': 1, 'name': 'Box publication',
            'components': {'identity': {'kind': 'soul', 'config': {}}},
            'bundle_id': 'bundle_' + 'a' * 32}
        if mode == 'foreign':
            payload['bundle_id'] = 'bundle_' + 'b' * 32
            try:
                publish_definition(root, author_id='other', payload=payload)
            except PermissionError:
                print('{}')
            else:
                raise AssertionError('foreign bundle overwritten')
        else:
            result = publish_definition(root, author_id='forged', payload=payload,
                                        idempotency_key='same-publication')
            assert result['author_id'] == 'owner'
            try:
                request('', {'op': 'BOX_PUBLISH', 'payload': payload,
                             'idempotency_key': '', 'owner': 'other'})
            except PermissionError:
                pass
            else:
                raise AssertionError('request-selected owner accepted')
            try:
                request('', {'op': 'BILLING_PLAN', 'action': 'apply_tier_event',
                             'args': {'owner': 'owner', 'tier': 'paid', 'event_created': 999}})
            except PermissionError:
                pass
            else:
                raise AssertionError('box changed billing state')
            print(json.dumps(result))
finally:
    runtime.tools.close()
"""


def run_box(box, mode):  # noqa: F811
    from tinyassets.platform_secrets import child_env

    result = subprocess.run(
        [sys.executable, "-c", BOX_CODE, str(box.root / "proof"), str(box.runtime.relay), mode],
        env=child_env(os.environ), capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def door(monkeypatch, root, owner):
    from tinyassets.auth import middleware
    from tinyassets.owner_router import OwnerRouter

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_x")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    monkeypatch.setattr(middleware, "current_identity", lambda: SimpleNamespace(user_id=owner))
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)

    def forbidden(*args, **kwargs):
        raise AssertionError("router read an owner store or tried to forward billing")

    monkeypatch.setattr(onboarding, "_read_home", forbidden)
    return TestClient(OwnerRouter(Starlette(routes=onboarding.onboarding_routes()),
                                 resolver=forbidden))


def test_signed_webhook_then_box_plan_and_router_status_cancel(box, monkeypatch):  # noqa: F811
    from tinyassets import billing
    from tinyassets.broker import supervisor

    monkeypatch.setattr(owner_runtime, "_runtime", None)
    box.broker.server._roles[os.getuid()] = "owner"
    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: SimpleNamespace(
        socket_path=box.broker.path, fence=lambda: (box.broker.state["generation"],
        box.broker.state["token"]), verify_broker=lambda channel: None))
    Plans(box.shared).set_plan("other", "free", event_created=0)
    with door(monkeypatch, box.shared, "owner") as client:
        event = _subscription_event(owner_id="owner")
        event["created"] = 100
        event["data"]["object"]["customer"] = "cus_owner"
        payload = json.dumps(event).encode()
        response = client.post("/app/billing/webhook", content=payload,
            headers={"stripe-signature": _sign(payload, int(time.time()))})
        assert response.status_code == 200 and response.json()["applied"]
        assert Plans(box.shared).read("owner")["tier"] == "paid"
        assert Plans(box.shared).read("other")["tier"] == "free"
        status = client.get("/app/billing/status").json()
        assert status["tier"] == "paid" and "usage" not in status
        cancelled = []
        monkeypatch.setattr(billing, "cancel_subscription", cancelled.append)
        assert client.post("/app/billing/cancel").json()["cancelled"]
        assert cancelled == ["sub_tinyassets"]
    from tinyassets.broker.server import BOX_HOST

    box.broker.server._roles[os.getuid()] = BOX_HOST
    assert run_box(box, "tier") == {"tier": "paid"}
    with sqlite3.connect(Plans(box.shared).path) as db:
        db.execute("DELETE FROM plans WHERE owner='owner'")
    run_box(box, "missing")
    assert not (box.shared / "center").exists()
    assert not list(box.root.rglob("plans.db"))
    assert not list(box.root.rglob(".subscription_state.db"))


def test_checkout_at_door_has_only_shared_account_state(tmp_path, monkeypatch):
    from tinyassets import billing
    from tinyassets.billing import stripe_adapter

    Plans(tmp_path).set_plan("owner", "free", event_created=0)
    seen = []
    monkeypatch.setattr(stripe_adapter, "checkout_params", lambda **kw: kw)
    def create(**kwargs):
        seen.append(kwargs)
        return {"id": "cs_owner", "url": "https://checkout.example/session"}
    monkeypatch.setattr(billing, "create_checkout_session", create)
    with door(monkeypatch, tmp_path, "owner") as client:
        assert client.get("/app/billing/status").json()["tier"] == "free"
        first = client.post("/app/billing/checkout").json()
        second = client.post("/app/billing/checkout").json()
        assert first["url"] == second["url"] and second["resumed"]
        assert seen[0]["owner_id"] == "owner" and len(seen) == 1
        event = {"type": "checkout.session.completed", "livemode": False,
                 "data": {"object": {"id": "cs_owner", "client_reference_id": "other",
                 "metadata": {"tinyassets_attempt_id": seen[0]["attempt_id"]}}}}
        payload = json.dumps(event).encode()
        settled = client.post("/app/billing/webhook", content=payload,
            headers={"stripe-signature": _sign(payload, int(time.time()))})
        assert settled.json() == {"settled": True}
        assert Plans(tmp_path).current_checkout_attempt("owner", now=time.time()) is None
    assert not list(tmp_path.rglob(".tinyassets.db"))
    assert not list(tmp_path.rglob(".subscription_state.db"))


def test_admission_creates_free_atomically_and_never_repairs_missing_existing_plan(tmp_path):
    from tinyassets.broker.owner_identities import OwnerIdentities

    seed_owner_admission(tmp_path, principal="owner", center="first")
    plans = Plans(tmp_path)
    assert plans.read("owner")["tier"] == "free"
    identities = OwnerIdentities(tmp_path / ".broker/owner-identities.db")
    plans.set_plan("owner", "paid", event_created=5)
    identities.admission("admit", "owner", "second")
    assert plans.read("owner")["tier"] == "paid"
    with sqlite3.connect(plans.path) as db:
        db.execute("DELETE FROM plans WHERE owner='owner'")
    with pytest.raises(PlanMissing):
        identities.admission("admit", "owner", "third")
    assert identities.center_state("third") == "unadmitted"


def test_box_publication_author_and_router_listing(box, monkeypatch):  # noqa: F811
    from tinyassets.commons_catalog import _list_definitions, publish_definition

    monkeypatch.setattr(owner_runtime, "_runtime", None)
    publish_definition(box.shared / ".published", author_id="other", payload={
        "schema_version": 1, "name": "Other owner", "bundle_id": "bundle_" + "b" * 32,
        "components": {"identity": {"kind": "soul", "config": {}}}})
    published = run_box(box, "publish")
    run_box(box, "foreign")
    rows = _list_definitions(box.shared / ".published", author_id="owner")
    assert len(rows) == 1 and rows[0]["agent_definition_id"] == published["agent_definition_id"]
    with door(monkeypatch, box.shared, "owner") as client:
        response = client.get("/app/run/" + published["agent_definition_id"])
        assert response.status_code == 200 and "Box publication" in response.text
    assert not list(box.root.rglob(".tinyassets.db"))
    assert not (box.root / ".published").exists()


def test_admission_failure_rolls_back_the_free_row(tmp_path):
    from tinyassets.broker.owner_identities import OwnerIdentities

    seed_owner_admission(tmp_path, principal="owner", center="first")
    identities = OwnerIdentities(tmp_path / ".broker/owner-identities.db")
    identities.resolve("new-owner", allocate=True)
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TRIGGER refuse_new_center BEFORE INSERT ON center_admissions "
                   "WHEN NEW.principal='new-owner' BEGIN SELECT RAISE(ABORT, 'fixture'); END")
    with pytest.raises(sqlite3.IntegrityError, match="fixture"):
        identities.admission("admit", "new-owner", "new-center")
    with pytest.raises(PlanMissing):
        Plans(tmp_path).read("new-owner")
    assert identities.center_state("new-center") == "unadmitted"


def test_mapped_legacy_events_and_conflicting_owner_claims(tmp_path, monkeypatch):
    from tinyassets.billing.events import apply_event

    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", SECRET)
    plans = Plans(tmp_path)
    plans.set_plan("owner", "paid", event_created=10,
                   customer_id="cus_owner", subscription_id="sub_tinyassets")
    plans.set_plan("other", "free", event_created=0)
    event = _subscription_event(owner_id="old-center", status="canceled")
    event["created"] = 20
    obj = event["data"]["object"]
    obj["metadata"]["universe_id"] = obj["metadata"].pop("owner_id")
    obj["customer"] = "cus_owner"
    assert apply_event(tmp_path, event)["applied"]
    assert plans.read("owner")["tier"] == "free"
    forged = _subscription_event(owner_id="other")
    forged["created"] = 30
    forged["data"]["object"]["customer"] = "cus_owner"
    with pytest.raises(PermissionError, match="mapping disagrees"):
        apply_event(tmp_path, forged)
    assert plans.read("other")["tier"] == "free"
    assert plans.read("owner")["tier"] == "free"
