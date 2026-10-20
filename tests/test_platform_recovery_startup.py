"""C3/C14: current off-host jobs and Stripe precede every work admission."""
import copy
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tinyassets import restore_recovery as recovery
from tinyassets.billing import stripe_adapter as stripe
from tinyassets.broker import admission
from tinyassets.broker.deletion_jobs import STEPS, DeletionJobs, new_job
from tinyassets.broker.owner_identities import OwnerIdentities
from tinyassets.broker.owner_reads import Plans
from tinyassets.broker.routing import RoutingAuthority


class Remote:
    def __init__(self):
        self.rows = {}

    def save(self, job):
        self.rows[job["id"]] = copy.deepcopy(job)

    def records(self):
        return list(copy.deepcopy(self.rows).values())


@pytest.fixture
def startup(tmp_path, monkeypatch):
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.broker.box_admission import provision

    for owner in ("alice", "bob"):
        seed_owner_admission(tmp_path, principal=owner, center=owner)
    identities = OwnerIdentities(tmp_path / ".broker/state/owner-identities.db",
                                 plans=Plans(tmp_path))
    with sqlite3.connect(identities.path) as db:
        db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, machine INT, "
                   "project_id INT, bhard_bytes INT)")
    for owner in ("alice", "bob"):
        provision(identities, tmp_path, owner, owner)
    (tmp_path / ".layout.lock").touch()
    (tmp_path / ".layout.json").write_text(json.dumps(
        {"layout": 2, "box_layout": "owner-box", "state": "stable"}))
    remote, events = Remote(), []
    server = SimpleNamespace(_data_root=tmp_path, _owner_identities=identities)
    server.routing = RoutingAuthority(server)
    server.deletion_jobs = DeletionJobs(server, records=remote)
    def changed(owner):
        if admission.read(identities, owner).state == "erased":
            events.append("withdrawal")
    monkeypatch.setattr(server.routing, "changed", changed)
    admission.register_authority(identities.path, server.routing)
    for step in STEPS[1:]:
        def execute(self, job, step=step):
            assert not identities.work_ready
            assert admission.read(identities, job["owner"]).state == "erased"
            events.append(step)
        monkeypatch.setattr(DeletionJobs, "_" + step, execute)
    monkeypatch.setattr("os.chown", lambda *args: None)
    monkeypatch.setenv("STRIPE_SECRET_KEY", "synthetic-key")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", "whsec_test_secret")
    def get(path):
        assert not identities.work_ready
        events.append("stripe")
        return {"data": [], "has_more": False}
    monkeypatch.setattr(stripe, "_get", get)
    monkeypatch.setattr("tinyassets.router_account_deletion.workos_user_exists",
                        lambda owner: events.append("identity:" + owner) or True)
    monkeypatch.setattr("tinyassets.router_account_deletion.revoke_workos_sessions",
                        lambda owner: events.append("revoke:" + owner))
    recovery.prepare_platform(tmp_path, backup_time=1, now=2)
    identities.work_ready = False
    return SimpleNamespace(root=tmp_path, identities=identities, server=server,
                           remote=remote, events=events)


@pytest.mark.parametrize("complete", [False, True])
def test_jobs_newer_than_backup_finish_before_any_owner_is_admitted(startup, complete):
    from tinyassets.router_traffic import admits

    a = startup
    job = new_job("alice", a.identities.owner_machine("alice"), clock=lambda: 3)
    if complete:
        job["completed_steps"] = list(STEPS)
    a.remote.save(job)
    # Neither the erased fact nor the newer job exists in the restored DB.
    assert a.server.deletion_jobs.records.records() == []
    assert admission.read(a.identities, "alice").state == "admitted"
    for owner in ("alice", "bob", "canary"):
        assert not admits(a.root, owner)
        with pytest.raises(admission.OwnerUnadmitted):
            admission.require_admitted(a.identities, owner)
    a.server.routing.tick()
    assert admission.read(a.identities, "alice").state == "erased"
    assert a.server.deletion_jobs.records.load(job["id"])["completed_steps"] == list(STEPS)
    assert a.events[0] == "withdrawal"
    if complete:
        assert not set(STEPS[1:]) & set(a.events)
    else:
        assert a.events.index("receipt") < a.events.index("stripe")
    assert "identity:alice" not in a.events
    assert "identity:bob" in a.events
    assert not admits(a.root, "bob")
    assert not a.identities.work_ready
    recovery.finish_platform(a.root)
    assert admits(a.root, "bob")
    # Publish and volume work are already separately covered; prove this tick
    # only opens work after root consumed the matching completion receipt.
    a.server.routing.publish = lambda **kwargs: None
    a.server.routing.volumes.tick = lambda wake: None
    a.server.routing.tick()
    admission.require_admitted(a.identities, "bob")
    with pytest.raises(admission.OwnerErased):
        admission.require_admitted(a.identities, "alice")
    assert a.events.count("volume") == (0 if complete else 1)


@pytest.mark.parametrize("failure", ["stripe", "unconfigured", "offhost", "missing_job"])
def test_unavailable_authority_keeps_every_owner_closed(startup, monkeypatch, failure):
    a = startup
    def unavailable(*args, **kwargs):
        raise OSError("synthetic unavailable")
    if failure == "stripe":
        monkeypatch.setattr(stripe, "_get", unavailable)
    elif failure == "unconfigured":
        monkeypatch.setenv("STRIPE_SECRET_KEY", "")
    elif failure == "offhost":
        monkeypatch.setattr(a.remote, "records", unavailable)
    else:
        job = new_job("alice", a.identities.owner_machine("alice"))
        a.server.deletion_jobs.records.save(job)
        a.remote.rows.clear()
    with pytest.raises((OSError, stripe.BillingUnavailable, recovery.RecoveryUnavailable)):
        a.server.routing.tick()
    with pytest.raises(recovery.RecoveryUnavailable):
        recovery.finish_platform(a.root)
    assert not a.identities.work_ready
    for owner in ("alice", "bob", "canary"):
        with pytest.raises(admission.OwnerUnadmitted):
            admission.require_admitted(a.identities, owner)


@pytest.mark.parametrize("billing", [False, True])
def test_billing_follows_stripe_and_abuse_survives(startup, monkeypatch, billing):
    from tests.test_billing_boundary import _subscription_object

    a = startup
    # Seed migration-style reasons through the sole admission writer.
    for reason in ("billing", "abuse"):
        admission.local_mutate(a.identities.path, "bob", "suspend",
                               reason=reason, suspended=True)
    obj = _subscription_object(owner_id="bob", status="past_due" if billing else "active")
    monkeypatch.setattr(stripe, "find_active_subscription",
                        lambda owner, **kwargs: obj["id"] if owner == "bob" else None)
    monkeypatch.setattr(stripe, "_get", lambda path: obj)
    a.server.routing.tick()
    assert admission.read(a.identities, "bob").reasons == (
        frozenset({"abuse", "billing"}) if billing else frozenset({"abuse"}))
    recovery.finish_platform(a.root)
    a.server.routing.publish = lambda **kwargs: None
    a.server.routing.volumes.tick = lambda wake: None
    a.server.routing.tick()
    admission.require_admitted(a.identities, "alice")
    with pytest.raises(admission.OwnerSuspended):
        admission.require_admitted(a.identities, "bob")
