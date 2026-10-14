"""Adversarial tests for source-event dispatch on the scheduler bus (Floor 3).

A ``source:<id>`` subscription owned by ``universe:<uid>`` must fire its branch AS that
universe (the correct branch-run actor), pass the webhook inputs through, and fire at most
once per delivery id.
"""

from __future__ import annotations

import pytest

from tinyassets.control_plane.events import (
    Scheduler,
    SchedulerEvent,
    _is_valid_event_type,
    register_subscription,
)
from tinyassets.runs import initialize_runs_db


@pytest.fixture()
def base_path(tmp_path):
    initialize_runs_db(tmp_path)
    return tmp_path


def _scheduler(base_path, calls):
    def run_fn(branch_def_id, actor, inputs, run_name):
        calls.append({"branch_def_id": branch_def_id, "actor": actor,
                      "inputs": inputs, "run_name": run_name})

    return Scheduler(base_path, run_fn)


def test_source_event_types_are_admitted_by_prefix():
    assert _is_valid_event_type("source:abc123")
    assert not _is_valid_event_type("source:")          # empty id refused
    assert not _is_valid_event_type("not_a_known_type")


def test_a_source_event_fires_the_bound_branch_as_the_owning_universe(base_path):
    calls: list = []
    register_subscription(
        base_path,
        branch_def_id="b-1",
        owner_actor="universe:u-a",
        event_type="source:src-1",
    )
    s = _scheduler(base_path, calls)
    s._dispatch_event(SchedulerEvent(
        event_type="source:src-1",
        event_id="deliv-1",
        payload={"universe_id": "u-a", "inputs": {"webhook": {"payload": {"x": 1}}}},
    ))
    assert len(calls) == 1
    # Actor is the universe, NOT wrapped as subscriber: — the correct branch-run identity.
    assert calls[0]["actor"] == "universe:u-a"
    assert calls[0]["branch_def_id"] == "b-1"
    # Inputs pass through verbatim so the branch sees the webhook body.
    assert calls[0]["inputs"] == {"webhook": {"payload": {"x": 1}}}


def test_a_duplicate_delivery_id_fires_once(base_path):
    calls: list = []
    register_subscription(
        base_path, branch_def_id="b-1", owner_actor="universe:u-a", event_type="source:src-1",
    )
    s = _scheduler(base_path, calls)
    event = SchedulerEvent(
        event_type="source:src-1", event_id="same-delivery",
        payload={"universe_id": "u-a", "inputs": {}},
    )
    s._dispatch_event(event)
    s._dispatch_event(event)   # channel retry with the same delivery id
    assert len(calls) == 1


def test_a_source_event_with_no_subscription_fires_nothing(base_path):
    calls: list = []
    s = _scheduler(base_path, calls)
    s._dispatch_event(SchedulerEvent(
        event_type="source:orphan", event_id="d-1", payload={"universe_id": "u-a"},
    ))
    assert calls == []


def test_a_deactivated_source_subscription_does_not_fire(base_path):
    from tinyassets.control_plane.events import (
        list_scheduler_subscriptions,
        unregister_subscription,
    )

    calls: list = []
    register_subscription(
        base_path, branch_def_id="b-1", owner_actor="universe:u-a", event_type="source:src-1",
    )
    sub = list_scheduler_subscriptions(base_path, owner_actor="universe:u-a")[0]
    unregister_subscription(base_path, sub["subscription_id"], requesting_actor="universe:u-a")
    s = _scheduler(base_path, calls)
    s._dispatch_event(SchedulerEvent(
        event_type="source:src-1", event_id="d-1", payload={"universe_id": "u-a", "inputs": {}},
    ))
    assert calls == []


def test_subscription_active_reflects_a_deactivation(base_path):
    from tinyassets.control_plane.events import (
        list_scheduler_subscriptions,
        unregister_subscription,
    )

    register_subscription(
        base_path, branch_def_id="b-1", owner_actor="universe:u-a", event_type="source:src-1",
    )
    sub = list_scheduler_subscriptions(base_path, owner_actor="universe:u-a")[0]
    s = _scheduler(base_path, [])
    assert s._subscription_active(sub["subscription_id"]) is True
    unregister_subscription(base_path, sub["subscription_id"], requesting_actor="universe:u-a")
    assert s._subscription_active(sub["subscription_id"]) is False


def test_dispatch_skips_firing_when_the_recheck_says_revoked(base_path, monkeypatch):
    # Codex #3 race: the subscription was active at the snapshot SELECT but revoked before
    # the fire — the pre-fire re-check must stop the run.
    calls: list = []
    register_subscription(
        base_path, branch_def_id="b-1", owner_actor="universe:u-a", event_type="source:src-1",
    )
    s = _scheduler(base_path, calls)
    monkeypatch.setattr(s, "_subscription_active", lambda _sub_id: False)
    s._dispatch_event(SchedulerEvent(
        event_type="source:src-1", event_id="d-1", payload={"universe_id": "u-a", "inputs": {}},
    ))
    assert calls == []              # re-check gated the fire


def test_a_legacy_non_universe_owner_keeps_the_subscriber_prefix(base_path):
    # The universe-actor passthrough must not change the existing (dormant) event types.
    calls: list = []
    register_subscription(
        base_path, branch_def_id="b-1", owner_actor="alice", event_type="source:s1",
    )
    s = _scheduler(base_path, calls)
    s._dispatch_event(SchedulerEvent(event_type="source:s1", payload={}))
    assert calls[0]["actor"] == "subscriber:alice"
