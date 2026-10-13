"""Engine events wake an owner's subscribed automation through the real pump.

An ``event`` automation is a subscription. The engine emits ``run_completed``
from ``runs.update_run_status`` (and the boot sweep) and
``pending_request_answered`` from ``storage.pending_requests.resolve_request``;
each match stores a ``once`` wake, which the real consumer pump fires.

Driven through the real emitters: a real run row taken terminal by the real
status writer, a real pending request answered through the connector's own
``answer_request``, the real ``register_automation``, the real store and the
real consumer. The only substitute is ``_execute``, the seam
``tinyassets.automations`` names for tests -- it fakes the graph a wake runs,
never the event, the scheduling or the checks.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

import pytest

import tinyassets.automations as automations_module
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import (
    OWNER,
    UNIVERSE,
    _consumer_with_inline_executor,
    _FakeOutcome,
    _seed_branch,
    _seed_owner,
)
from tests.test_background_budget_finalization_e2e import _seed_serving_assignment
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.automations import (
    TRIGGER_EVENT,
    TRIGGER_ONCE,
    Automation,
    AutomationStore,
    AutomationUnavailable,
    due_automations,
    register_automation,
)
from tinyassets.runs import (
    RUN_STATUS_COMPLETED,
    RUN_STATUS_FAILED,
    RUN_STATUS_RUNNING,
    create_run,
    recover_in_flight_runs,
    update_run_status,
)

pytestmark = pytest.mark.usefixtures("cloud_runtime")

FOLLOWED = "branch_followed"
FOLLOWER = "branch_follower"
BOB = "acct_bob"
BOB_UNIVERSE = "universe_bob"
BOB_BRANCH = "branch_bobs_own"


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Alice's serving home with two PRIVATE branches; Bob has his own home."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path, branch_def_id=FOLLOWED, visibility="private")
    _seed_branch(tmp_path, branch_def_id=FOLLOWER, visibility="private")
    _seed_owner(tmp_path, universe_id=BOB_UNIVERSE, owner=BOB)
    _seed_branch(tmp_path, branch_def_id=BOB_BRANCH, author=BOB, visibility="private")
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE],
    )
    return tmp_path


def _as(actor: str | None):
    return identity_context(
        None if actor is None else Identity(user_id=actor, username=actor)
    )


def _subscribe(base: Path, event_type: str, event_filter=None, **kw) -> Automation:
    owner = kw.pop("owner", OWNER)
    with _as(owner):
        return register_automation(
            base,
            universe_id=kw.pop("universe_id", UNIVERSE),
            owner_principal_id=owner,
            name="follow",
            branch_def_id=kw.pop("branch_def_id", FOLLOWER),
            event_type=event_type,
            event_filter=event_filter,
            inputs={"why": "followed"},
            **kw,
        )


def _wakes(base: Path, universe: str = UNIVERSE) -> list[Automation]:
    return [
        row for row in AutomationStore(base).list(universe_id=universe)
        if row.trigger_kind == TRIGGER_ONCE
    ]


def _start(base: Path, *, actor: str, queue_universe_id: str | None = None,
           bound: str | None = None, branch_def_id: str = FOLLOWED) -> str:
    """A running run, created with `bound` as the request identity."""
    with _as(bound):
        run_id = create_run(
            base, branch_def_id=branch_def_id, thread_id="t", inputs={},
            actor=actor, queue_universe_id=queue_universe_id,
        )
    update_run_status(base, run_id, status=RUN_STATUS_RUNNING)
    return run_id


def _finish(base: Path, *, actor: str, status: str = RUN_STATUS_COMPLETED,
            queue_universe_id: str | None = None, bound: str | None = None,
            branch_def_id: str = FOLLOWED, ended_by: str | None = "acct_stranger",
            ) -> str:
    """Create a run as `bound`, then end it with a DIFFERENT ambient identity:
    the event is stamped from the run's creation, never from who is ambient
    when it ends."""
    run_id = _start(base, actor=actor, queue_universe_id=queue_universe_id,
                    bound=bound, branch_def_id=branch_def_id)
    with _as(ended_by):
        update_run_status(base, run_id, status=status, finished_at=1.0)
    return run_id


class _Graph:
    """The `_execute` seam: records what each fired wake ran, with its inputs."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, base_path, automation, provider_call, branch, inputs,
                 on_run_started=None):
        self.calls.append((automation.branch_def_id, dict(inputs)))
        run_id = f"graph_run_{len(self.calls)}"
        if callable(on_run_started):
            on_run_started(run_id)
        return _FakeOutcome(run_id=run_id, status="completed")


def _poll(base: Path) -> None:
    consumer, _inline = _consumer_with_inline_executor(base)
    try:
        consumer.poll_once()
    finally:
        consumer.stop()


# -- run_completed ------------------------------------------------------------


def test_a_finished_run_wakes_the_branch_that_follows_it_through_the_pump(
    home: Path, monkeypatch,
) -> None:
    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    run_id = _finish(home, actor=OWNER)

    [wake] = _wakes(home)
    assert (wake.owner_principal_id, wake.branch_def_id) == (OWNER, FOLLOWER)
    assert wake.inputs == {
        "why": "followed",
        "event": {
            "type": "run_completed", "subscription_id": sub.automation_id,
            "run_id": run_id, "branch_def_id": FOLLOWED, "outcome": "completed",
        },
    }

    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _poll(home)
    assert graph.calls == [(FOLLOWER, wake.inputs)]
    assert AutomationStore(home).get(wake.automation_id).retired_at
    # The subscription itself stays, and is never due on a clock.
    assert AutomationStore(home).get(sub.automation_id).retired_at == ""
    assert _wakes(home) == []


def test_the_universes_own_background_run_wakes_the_owner_bound_for_it(
    home: Path,
) -> None:
    """An automation run's actor is `universe:<id>`; the owner is bound for it.
    A universe run nobody was bound for wakes nothing: no principal is not a
    broadcast (Codex refute 2026-09-28, P1)."""
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    _finish(home, actor=f"universe:{UNIVERSE}", bound=OWNER, ended_by=None)
    _finish(home, actor=f"universe:{UNIVERSE}", bound=None, ended_by=OWNER)
    assert len(_wakes(home)) == 1


def test_only_the_transition_announces_a_run(home: Path) -> None:
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    run_id = _finish(home, actor=OWNER)
    # A later terminal write that only re-persists output is not a second end.
    update_run_status(home, run_id, status=RUN_STATUS_COMPLETED, output={"x": 1})
    assert len(_wakes(home)) == 1


def test_the_filter_selects_branch_and_outcome(home: Path) -> None:
    _subscribe(home, "run_completed",
               {"branch_def_id": FOLLOWED, "outcome": RUN_STATUS_FAILED})
    _finish(home, actor=OWNER)  # completed: no match
    _finish(home, actor=OWNER, status=RUN_STATUS_FAILED, branch_def_id=FOLLOWER)
    assert _wakes(home) == []
    _finish(home, actor=OWNER, status=RUN_STATUS_FAILED)
    [wake] = _wakes(home)
    assert wake.inputs["event"]["outcome"] == "failed"


def test_a_deploy_killed_run_is_announced_as_interrupted(home: Path) -> None:
    _subscribe(home, "run_completed",
               {"branch_def_id": FOLLOWED, "outcome": "interrupted"})
    run_id = _start(home, actor=f"universe:{UNIVERSE}", bound=OWNER)
    _started_before_this_process(home, run_id)
    with _as(None):  # boot: no request is bound
        assert recover_in_flight_runs(home) == 1
    [wake] = _wakes(home)
    assert wake.inputs["event"]["run_id"] == run_id


def test_a_paused_subscription_does_not_wake(home: Path) -> None:
    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    AutomationStore(home).pause_for_reason(
        sub.automation_id, reason="owner", now=__import__("datetime").datetime.now(
            __import__("datetime").timezone.utc),
    )
    _finish(home, actor=OWNER)
    assert _wakes(home) == []


# -- The cross-user floor -------------------------------------------------------


def test_another_users_run_in_the_owners_universe_wakes_nothing(home: Path) -> None:
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    # Bob's own run recorded against Alice's universe.
    _finish(home, actor=BOB, queue_universe_id=UNIVERSE)
    # Alice's universe's run with Bob bound (a visitor-driven turn).
    _finish(home, actor=f"universe:{UNIVERSE}", bound=BOB)
    # Bob's plain run of a same-named branch: his home, not hers.
    _finish(home, actor=BOB)
    assert _wakes(home) == []
    assert _wakes(home, BOB_UNIVERSE) == []


def test_a_co_admin_sharing_the_home_cannot_subscribe_on_the_owners_assignment(
    home: Path,
) -> None:
    """Carol is an admin whose home is also Alice's universe. Her run must not
    spend Alice's subscription, and the home's one accepted assignment is
    Alice's: Carol has no run authority there, so her own subscription is
    refused rather than stored to run on Alice's acceptance."""
    carol = "acct_carol"
    _seed_owner(home, owner=carol)
    _seed_branch(home, branch_def_id="branch_carols", author=carol,
                 visibility="private")
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    _finish(home, actor=carol)
    assert _wakes(home) == []
    with pytest.raises(AutomationUnavailable) as caught:
        _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED}, owner=carol,
                   branch_def_id="branch_carols")
    assert caught.value.reason == "no_serving_assignment"
    _finish(home, actor=carol)
    assert _wakes(home) == []


def test_a_recovered_run_wakes_only_its_own_owner_not_a_co_admin(
    home: Path,
) -> None:
    """Codex refute 2026-09-28, P1: Alice and Carol share a home, each trying to
    follow A. Carol's subscription is refused (the home's accepted assignment
    is Alice's); Alice's background run is recovered at boot with nobody
    bound; only Alice's subscription wakes."""
    carol = "acct_carol"
    _seed_owner(home, owner=carol)
    _seed_branch(home, branch_def_id="branch_carols", author=carol,
                 visibility="private")
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    with pytest.raises(AutomationUnavailable) as caught:
        _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED}, owner=carol,
                   branch_def_id="branch_carols")
    assert caught.value.reason == "no_serving_assignment"
    run_id = _start(home, actor=f"universe:{UNIVERSE}", bound=OWNER)
    _started_before_this_process(home, run_id)
    with _as(None):
        assert recover_in_flight_runs(home) == 1
    assert [wake.owner_principal_id for wake in _wakes(home)] == [OWNER]


def test_a_cancelled_run_announces_nothing(home: Path) -> None:
    """Codex refute 2026-09-28, P1: a collaborator's cancel of the owner's run
    must not start the owner's follow-up work. Whoever cancels caused the end."""
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    _finish(home, actor=OWNER, bound=OWNER, status="cancelled", ended_by=BOB)
    assert _wakes(home) == []
    with pytest.raises(AutomationUnavailable) as caught:
        _subscribe(home, "run_completed",
                   {"branch_def_id": FOLLOWED, "outcome": "cancelled"})
    assert caught.value.reason == "event_filter_invalid"


def test_the_owners_run_in_another_universe_wakes_nothing(home: Path) -> None:
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    _finish(home, actor=OWNER, queue_universe_id=BOB_UNIVERSE)
    _finish(home, actor=f"universe:{BOB_UNIVERSE}", bound=OWNER)
    assert _wakes(home) == []


def test_a_subscription_whose_owner_lost_admin_records_why_and_stores_nothing(
    home: Path,
) -> None:
    from tinyassets.daemon_server import grant_universe_access

    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    grant_universe_access(home, universe_id=UNIVERSE, actor_id=OWNER,
                          permission="write", granted_by=OWNER)
    _finish(home, actor=f"universe:{UNIVERSE}", bound=OWNER)
    assert _wakes(home) == []
    from tinyassets.storage.assigned_queue_refusals import AssignedQueueRefusalStore

    reasons = AssignedQueueRefusalStore(home).fresh_reasons(
        universe_id=UNIVERSE, max_age_seconds=3600,
    )
    assert reasons[f"automation:{sub.automation_id}"] == (
        "event_wake_refused:owner_not_admin"
    )


# -- pending_request_answered -------------------------------------------------


def _owner_request():
    return identity_context(Identity(
        user_id=OWNER, username=OWNER, capabilities=["tinyassets.universe.write"],
    ))


def _ask(base: Path, title: str = "Which city?") -> str:
    """The agent raises a question in Alice's universe, as the connector does."""
    from tinyassets.api.pending_requests import request_from_user

    with _owner_request():
        out = request_from_user(universe_id=UNIVERSE, payload=json.dumps({
            "kind": "question", "title": title, "body": "Pick one.",
            "action": {"type": "answer"},
            "fields": [{"name": "city", "type": "text", "label": "City"}],
        }))
    assert out.get("request_id"), out
    return out["request_id"]


def test_answering_through_the_connector_wakes_the_subscribed_branch(
    home: Path, monkeypatch,
) -> None:
    from tinyassets.api.pending_requests import answer_request

    _subscribe(home, "pending_request_answered")
    request_id = _ask(home)
    with _owner_request():
        out = answer_request(universe_id=UNIVERSE, payload=json.dumps({
            "request_id": request_id, "values": {"city": "Lisbon"},
        }))
    assert out.get("status") == "answered", out

    [wake] = _wakes(home)
    assert wake.inputs["event"] == {
        "type": "pending_request_answered",
        "subscription_id": wake.inputs["event"]["subscription_id"],
        "request_id": request_id, "kind": "question", "status": "answered",
    }
    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _poll(home)
    assert [branch for branch, _inputs in graph.calls] == [FOLLOWER]


def test_a_dismissal_by_the_owner_wakes_and_one_with_nobody_bound_does_not(
    home: Path,
) -> None:
    from tinyassets.storage.pending_requests import resolve_request

    _subscribe(home, "pending_request_answered", {"status": "dismissed"})
    first, second = _ask(home), _ask(home, title="Which country?")
    with _as(None):
        assert resolve_request(home / UNIVERSE, first, status="dismissed")
    assert _wakes(home) == []
    with _as(OWNER):
        assert resolve_request(home / UNIVERSE, second, status="dismissed")
    [wake] = _wakes(home)
    assert wake.owner_principal_id == OWNER


def test_an_answer_by_someone_else_wakes_nothing(home: Path) -> None:
    from tinyassets.storage.pending_requests import resolve_request

    _subscribe(home, "pending_request_answered")
    request_id = _ask(home)
    with _as(BOB):
        assert resolve_request(home / UNIVERSE, request_id, status="answered")
    assert _wakes(home) == []


def test_an_already_resolved_request_does_not_wake_twice(home: Path) -> None:
    from tinyassets.storage.pending_requests import resolve_request

    _subscribe(home, "pending_request_answered")
    request_id = _ask(home)
    with _as(OWNER):
        assert resolve_request(home / UNIVERSE, request_id, status="answered")
        assert not resolve_request(home / UNIVERSE, request_id, status="answered")
    assert len(_wakes(home)) == 1


# -- pending_request_answered, one item at a time -----------------------------


def _ask_with_items(base: Path, *item_ids: str) -> str:
    """One request holding several answerable items, raised as the connector."""
    from tinyassets.api.pending_requests import request_from_user

    with _owner_request():
        out = request_from_user(universe_id=UNIVERSE, payload=json.dumps({
            "kind": "TODO", "title": "Today", "body": "A few things.",
            "action": {"type": "answer"}, "fields": [],
            "items": [
                {"item_id": i, "title": f"Do {i}",
                 "fields": [{"name": "note", "type": "text", "label": "Reply"}]}
                for i in item_ids
            ],
        }))
    assert out.get("request_id"), out
    return out["request_id"]


def test_an_item_answer_wakes_only_the_subscription_naming_that_item(
    home: Path,
) -> None:
    from tinyassets.api.pending_requests import answer_request

    follows_first = _subscribe(
        home, "pending_request_answered", {"item_id": "first"},
    )
    follows_second = _subscribe(
        home, "pending_request_answered", {"item_id": "second"},
        branch_def_id=FOLLOWED,
    )
    request_id = _ask_with_items(home, "first", "second")

    with _owner_request():
        out = answer_request(universe_id=UNIVERSE, payload=json.dumps({
            "request_id": request_id, "item_id": "first",
            "values": {"note": "done"},
        }))
    assert out.get("status") == "answered", out
    assert out["request_status"] == "pending"

    [wake] = _wakes(home)
    assert wake.branch_def_id == follows_first.branch_def_id
    assert wake.inputs["event"]["item_id"] == "first"
    # The request is still open, and the event says so, so a follower can tell
    # "one task is done" from "the whole note is finished".
    assert wake.inputs["event"]["status"] == "pending"
    assert follows_second.automation_id != follows_first.automation_id


def test_a_whole_request_answer_does_not_wake_an_item_subscription(
    home: Path,
) -> None:
    """The pre-items payload is unchanged -- it carries no ``item_id`` at all --
    so a filter naming one does not match every answer."""
    from tinyassets.api.pending_requests import answer_request

    _subscribe(home, "pending_request_answered", {"item_id": "first"})
    request_id = _ask_with_items(home, "first", "second")

    with _owner_request():
        answer_request(universe_id=UNIVERSE, payload=json.dumps({
            "request_id": request_id, "values": {},
        }))

    assert _wakes(home) == []


def test_an_unfiltered_subscription_wakes_once_when_the_last_item_closes(
    home: Path,
) -> None:
    """A closing item is ONE act: it must not wake an unfiltered follower twice
    (once for the item, once for the request)."""
    from tinyassets.api.pending_requests import answer_request

    _subscribe(home, "pending_request_answered")
    request_id = _ask_with_items(home, "only")

    with _owner_request():
        out = answer_request(universe_id=UNIVERSE, payload=json.dumps({
            "request_id": request_id, "item_id": "only", "values": {"note": "x"},
        }))
    assert out["request_status"] == "answered"

    [wake] = _wakes(home)
    assert wake.inputs["event"]["item_id"] == "only"
    assert wake.inputs["event"]["status"] == "answered"


def test_an_item_answer_by_someone_else_wakes_nothing(home: Path) -> None:
    from tinyassets.storage.pending_requests import resolve_item

    _subscribe(home, "pending_request_answered")
    request_id = _ask_with_items(home, "first")
    with _as(BOB):
        assert not resolve_item(
            home / UNIVERSE, request_id, "first", status="answered",
        ).get("error")
    assert _wakes(home) == []


# -- Registration ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("event_type", "event_filter", "extra", "reason"),
    [
        ("canon_change", None, {}, "event_type_unknown"),
        ("file_changed", None, {}, "event_type_unknown"),
        # A run_completed subscription must name the branch it follows.
        ("run_completed", None, {}, "event_filter_invalid"),
        ("run_completed", {"branch_def_id": FOLLOWED, "error": "x"}, {},
         "event_filter_invalid"),
        ("run_completed", {"branch_def_id": 3}, {}, "event_filter_invalid"),
        ("run_completed", {"branch_def_id": FOLLOWED}, {"interval_seconds": 600},
         "trigger_invalid"),
        ("pending_request_answered", None, {"not_before": "2026-10-01T00:00:00Z"},
         "trigger_invalid"),
    ],
)
def test_a_subscription_that_could_not_fire_is_refused(
    home: Path, event_type, event_filter, extra, reason,
) -> None:
    with pytest.raises(AutomationUnavailable) as caught:
        _subscribe(home, event_type, event_filter, **extra)
    assert caught.value.reason == reason
    assert AutomationStore(home).list(universe_id=UNIVERSE) == []


def test_a_subscription_is_never_due_on_a_clock(home: Path) -> None:
    from datetime import datetime, timedelta, timezone

    sub = _subscribe(home, "pending_request_answered")
    assert sub.trigger_kind == TRIGGER_EVENT
    later = datetime.now(timezone.utc) + timedelta(days=30)
    assert due_automations(home, universe_id=UNIVERSE, now=later) == []


def test_a_database_from_the_previous_build_gains_the_event_kind(
    tmp_path: Path, home: Path,
) -> None:
    """The stored CHECK predates `event`; connecting rebuilds it, keeping rows."""
    db = AutomationStore(home).db_path
    db.unlink(missing_ok=True)
    old_table = automations_module._AUTOMATIONS_TABLE.replace(
        ",'event'", ""
    ).replace("__TABLE__", "automations")
    assert "'event'" not in old_table
    with sqlite3.connect(db) as conn:
        conn.executescript(old_table)
        conn.execute(
            "INSERT INTO automations (automation_id, universe_id, "
            "owner_principal_id, name, branch_def_id, trigger_kind, "
            "desired_state, created_at, updated_at) VALUES "
            "('old', ?, ?, 'n', ?, 'interval', 'active', 't', 't')",
            (UNIVERSE, OWNER, FOLLOWER),
        )
    sub = _subscribe(home, "pending_request_answered")
    ids = {row.automation_id for row in AutomationStore(home).list(universe_id=UNIVERSE)}
    assert ids == {"old", sub.automation_id}


def test_the_connector_surface_creates_and_projects_a_subscription(home: Path) -> None:
    from tinyassets.api.automations import automations

    with identity_context(Identity(
        user_id=OWNER, username=OWNER,
        capabilities=["tinyassets.universe.write", "tinyassets.universe.admin"],
    )):
        out = automations(action="create", universe_id=UNIVERSE, payload=json.dumps({
            "name": "after research", "branch_def_id": FOLLOWER,
            "event_type": "run_completed",
            "event_filter": {"branch_def_id": FOLLOWED},
        }))
        refused = automations(action="create", universe_id=UNIVERSE, payload=json.dumps({
            "name": "x", "branch_def_id": FOLLOWER, "event_type": "run_completed",
        }))
    assert out["status"] == "automation_created", out
    trigger = out["automation"]["trigger"]
    assert (trigger["kind"], trigger["event_type"], trigger["event_filter"]) == (
        "event", "run_completed", {"branch_def_id": FOLLOWED},
    )
    assert out["automation"]["next_due_at"] == ""
    assert refused["reason"] == "event_filter_invalid"
    assert "branch_def_id (required)" in refused["detail"]


# -- the owner sees when a subscription last fired and what it produced -------


def _read(home: Path, action: str, **kw) -> dict:
    from tinyassets.api.automations import automations

    with identity_context(Identity(
        user_id=OWNER, username=OWNER,
        capabilities=["tinyassets.universe.write", "tinyassets.universe.admin"],
    )):
        return automations(action=action, universe_id=UNIVERSE, **kw)


def test_the_owner_sees_when_a_subscription_fired_and_what_its_wake_ran(
    home: Path, monkeypatch,
) -> None:
    """Live 2026-09-28: the founder's run_completed subscription fired nine
    times and its row still read last_* = '' -- a live subscription looked
    exactly like a dead one."""
    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    _finish(home, actor=OWNER)
    [wake] = _wakes(home)
    monkeypatch.setattr(automations_module, "_execute", _Graph())
    _poll(home)

    row = AutomationStore(home).get(sub.automation_id)
    assert row.last_reason == f"woke:{wake.automation_id}"
    assert row.last_due_at, "when it last fired"
    assert row.revision == sub.revision, "a runtime record, not an owner edit"
    assert due_automations(home, universe_id=UNIVERSE, now=datetime.now(timezone.utc)) == []

    for out in (
        _read(home, "get", automation_id=sub.automation_id)["automation"],
        next(a for a in _read(home, "list", payload="{}")["automations"]
             if a["automation_id"] == sub.automation_id),
    ):
        assert out["last_reason"] == f"woke:{wake.automation_id}", out
        assert out["next_due_at"] == ""
        last = out["last_wake"]
        assert last["automation_id"] == wake.automation_id
        assert last["last_run_id"] == "graph_run_1"
        assert last["last_finished_at"] and last["retired_at"], last


def test_a_refused_wake_is_recorded_on_the_subscription_row(home: Path) -> None:
    from tinyassets.daemon_server import grant_universe_access

    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    grant_universe_access(home, universe_id=UNIVERSE, actor_id=OWNER,
                          permission="write", granted_by=OWNER)
    _finish(home, actor=f"universe:{UNIVERSE}", bound=OWNER)
    row = AutomationStore(home).get(sub.automation_id)
    assert row.last_reason == "event_wake_refused:owner_not_admin"
    assert row.last_due_at


def test_a_last_wake_is_read_only_from_the_subscriptions_own_universe(
    home: Path,
) -> None:
    """The projection follows the recorded id, and only inside the universe."""
    _seed_owner(home, universe_id=BOB_UNIVERSE, owner=BOB)
    _seed_branch(home, branch_def_id=BOB_BRANCH, author=BOB)
    from tests.test_automations import _copy_assignment_to

    _copy_assignment_to(home, universe_id=BOB_UNIVERSE, owner=BOB)
    with _as(BOB):
        bobs = register_automation(
            home, universe_id=BOB_UNIVERSE, owner_principal_id=BOB, name="bob",
            branch_def_id=BOB_BRANCH, not_before="2026-01-01T00:00:00Z",
        )
    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    AutomationStore(home).record_event_fire(
        sub.automation_id, reason=f"woke:{bobs.automation_id}",
        now=datetime.now(timezone.utc),
    )
    out = _read(home, "get", automation_id=sub.automation_id)["automation"]
    assert "last_wake" not in out, out
    # And the record only lands on an event subscription.
    AutomationStore(home).record_event_fire(
        bobs.automation_id, reason="woke:x", now=datetime.now(timezone.utc),
    )
    assert AutomationStore(home).get(bobs.automation_id).last_reason == ""


def test_a_late_older_fire_never_replaces_the_latest(home: Path) -> None:
    """Two events race; the older one's record lands last (refute P2)."""
    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    store = AutomationStore(home)
    newer = datetime(2026, 9, 30, 12, 0, 5, tzinfo=timezone.utc)
    store.record_event_fire(sub.automation_id, reason="woke:newer", now=newer)
    store.record_event_fire(sub.automation_id, reason="event_wake_refused:x",
                            now=newer.replace(second=1))
    row = store.get(sub.automation_id)
    assert (row.last_reason, row.last_due_at) == ("woke:newer", "2026-09-30T12:00:05+00:00")
    assert row.updated_at >= "2026-09-30T12:00:05+00:00"


def test_last_wake_is_only_a_wake_this_subscription_stored(home: Path) -> None:
    """Same universe is not enough: another wake of the universe is not this
    subscription's output (refute concern)."""
    sub = _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    with _as(OWNER):
        other = register_automation(
            home, universe_id=UNIVERSE, owner_principal_id=OWNER, name="manual",
            branch_def_id=FOLLOWER, not_before="2026-01-01T00:00:00Z",
            inputs={"event": {"subscription_id": "another_subscription"}},
        )
    AutomationStore(home).record_event_fire(
        sub.automation_id, reason=f"woke:{other.automation_id}",
        now=datetime.now(timezone.utc),
    )
    out = _read(home, "get", automation_id=sub.automation_id)["automation"]
    assert "last_wake" not in out, out


# -- the recovery sweep interrupts only what a dead process left --------------


@pytest.fixture
def recovery(home: Path, monkeypatch):
    """A fresh process's once-only recovery, pointed at `home`."""
    import os

    from tinyassets.api import runs as api_runs

    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_DONE", False)
    monkeypatch.setattr(api_runs, "_RUNS_RECOVERY_LOCK", None)
    monkeypatch.setattr(api_runs, "_base_path", lambda: home)
    yield api_runs
    held = api_runs._RUNS_RECOVERY_LOCK
    if held is not None and held.fd is not None:
        os.close(held.fd)


def _started_before_this_process(base: Path, run_id: str) -> None:
    """The run was owned by the process a deploy killed."""
    from tests.run_owner_helpers import mark_owner_dead

    mark_owner_dead(base, run_id)


def _status(base: Path, run_id: str) -> str:
    from tinyassets.runs import get_run

    return get_run(base, run_id)["status"]


def test_recovery_interrupts_a_dead_processes_run_but_never_a_live_one(
    home: Path, recovery,
) -> None:
    """Live 2026-09-30 03:19:22: the first run tool used in an engine MCP child
    swept every queued/running row. It marked the founder's live background run
    interrupted, announced that, and the loop's subscription stored a wake while
    the run was still going; its real completion was then never announced."""
    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    dead = _start(home, actor=OWNER)
    _started_before_this_process(home, dead)
    live = _start(home, actor=OWNER)

    with _as(None):  # boot: no request is bound
        recovery._ensure_runs_recovery()

    assert _status(home, dead) == "interrupted"
    assert _status(home, live) == RUN_STATUS_RUNNING
    [wake] = _wakes(home)
    assert (wake.inputs["event"]["run_id"], wake.inputs["event"]["outcome"]) == (
        dead, "interrupted",
    ), "a deploy-killed run is announced, so the owner's loop survives the deploy"

    with _as("acct_stranger"):
        update_run_status(home, live, status=RUN_STATUS_COMPLETED, finished_at=1.0)
    announced = sorted((w.inputs["event"]["run_id"], w.inputs["event"]["outcome"])
                       for w in _wakes(home))
    assert announced == sorted([(dead, "interrupted"), (live, "completed")])


def test_a_process_without_the_recovery_lock_sweeps_nothing(
    home: Path, recovery,
) -> None:
    """The server holds the lock for its life; its engine children never sweep."""
    import subprocess
    import sys
    import textwrap

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    dead = _start(home, actor=OWNER)
    _started_before_this_process(home, dead)
    holder = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-c", textwrap.dedent(f"""
            import sys
            from pathlib import Path
            from tinyassets.singleton_lock import acquire_singleton_lock
            lock = acquire_singleton_lock(Path({str(home)!r}) / ".run_recovery.lock")
            print("held" if lock.acquired else "refused", flush=True)
            sys.stdin.readline()
        """)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with _as(None):
            recovery._ensure_runs_recovery()
    finally:
        holder.stdin.write("\n")
        holder.stdin.flush()
        holder.wait(timeout=30)
    assert _status(home, dead) == RUN_STATUS_RUNNING
    assert _wakes(home) == []


def test_a_failed_recovery_is_retried_on_the_next_run_tool(
    home: Path, recovery, monkeypatch,
) -> None:
    import tinyassets.runs as runs_module

    calls: list[float | None] = []

    def flaky(base, *, started_before=None):
        calls.append(started_before)
        if len(calls) == 1:
            raise sqlite3.OperationalError("database is locked")
        return 0

    monkeypatch.setattr(runs_module, "recover_in_flight_runs", flaky)
    recovery._ensure_runs_recovery()
    assert recovery._RUNS_RECOVERY_DONE is False
    recovery._ensure_runs_recovery()
    assert recovery._RUNS_RECOVERY_DONE is True and len(calls) == 2


# -- run-owner-proof: recovery on proven death, and the durable outbox --------


def _run_owned_by_child(home: Path, *, die: bool):
    """A real process that creates a running run of FOLLOWED, then dies (its
    liveness lock released by the kernel) or stays alive and silent."""
    import subprocess
    import sys
    import textwrap

    script = textwrap.dedent(f"""
        import os, sys
        from tinyassets.runs import RUN_STATUS_RUNNING, create_run, update_run_status
        base = {str(home)!r}
        run_id = create_run(base, branch_def_id={FOLLOWED!r}, thread_id="t",
                            inputs={{}}, actor={OWNER!r})
        update_run_status(base, run_id, status=RUN_STATUS_RUNNING)
        print(run_id, flush=True)
        if {die!r}:
            os._exit(0)  # no release, no terminal status: what a crash leaves
        sys.stdin.readline()
    """)
    child = subprocess.Popen(  # noqa: S603 - fixed argv
        [sys.executable, "-c", script], cwd=str(Path(__file__).resolve().parents[1]),
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
    )
    run_id = child.stdout.readline().strip()
    assert run_id, "the child never created its run"
    if die:
        child.wait(timeout=30)
    return child, run_id


def test_a_crashed_processes_run_is_recovered_and_announced_once(home: Path) -> None:
    """The P1 from #4125: an engine child dies mid-run while the server lives.
    Its liveness lock is gone, so recovery has proof, interrupts the run and
    announces it; a second delivery of the same event stores no second wake."""
    from tinyassets.runs import deliver_terminal_events, runs_db_path

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    _child, run_id = _run_owned_by_child(home, die=True)

    with _as(None):
        assert recover_in_flight_runs(home) == 1
    assert _status(home, run_id) == "interrupted"
    [wake] = _wakes(home)
    assert (wake.inputs["event"]["run_id"], wake.inputs["event"]["outcome"]) == (
        run_id, "interrupted")

    # At-least-once delivery: the event is owed again (a crash after the emit,
    # before the mark) -- and the idempotent wake keeps it to one chain.
    with sqlite3.connect(runs_db_path(home)) as conn:
        conn.execute("UPDATE run_terminal_outbox SET delivered_at = NULL")
    with _as(None):
        assert deliver_terminal_events(home, older_than=0) == 1
    assert [w.automation_id for w in _wakes(home)] == [wake.automation_id]


def test_a_live_silent_processes_run_is_never_recovered(home: Path) -> None:
    """However old and quiet, a run whose owner still holds its lock stands."""
    from tinyassets.runs import runs_db_path

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    child, run_id = _run_owned_by_child(home, die=False)
    try:
        with sqlite3.connect(runs_db_path(home)) as conn:
            conn.execute("UPDATE runs SET started_at = 1 WHERE run_id = ?", (run_id,))
            conn.execute("DELETE FROM run_events WHERE run_id = ?", (run_id,))
        with _as(None):
            assert recover_in_flight_runs(home) == 0
        assert _status(home, run_id) == RUN_STATUS_RUNNING
        assert _wakes(home) == []
    finally:
        child.stdin.write("\n")
        child.stdin.flush()
        child.wait(timeout=30)


def test_a_terminal_event_lost_between_commit_and_emit_is_redelivered(
    home: Path, monkeypatch,
) -> None:
    import tinyassets.runs as runs_module

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    real = runs_module.deliver_terminal_events
    monkeypatch.setattr(runs_module, "deliver_terminal_events", lambda *a, **k: 0)
    run_id = _finish(home, actor=OWNER)  # the process "dies" before emitting
    assert _wakes(home) == []
    monkeypatch.setattr(runs_module, "deliver_terminal_events", real)
    with _as(None):
        assert runs_module.deliver_terminal_events(home, older_than=0) == 1
    [wake] = _wakes(home)
    assert wake.inputs["event"]["run_id"] == run_id
    with _as(None):
        assert runs_module.deliver_terminal_events(home, older_than=0) == 0


def test_a_dead_owners_liveness_file_outlives_its_unrecovered_run(home: Path) -> None:
    """The consumer's cleanup of dead holders' files must not delete the proof
    a run still needs to be recovered."""
    from tinyassets import process_liveness
    from tinyassets.runtime.assigned_queue_consumer import AssignedQueueConsumer

    _child, run_id = _run_owned_by_child(home, die=True)
    token = _owner_token(home, run_id)
    consumer = AssignedQueueConsumer(home)
    try:
        consumer._hold_liveness()
    finally:
        consumer._release_liveness()
    assert process_liveness.owner_state(home, token) == process_liveness.DEAD


def _owner_token(base: Path, run_id: str) -> str:
    from tinyassets.runs import runs_db_path

    with sqlite3.connect(runs_db_path(base)) as conn:
        return str(conn.execute(
            "SELECT owner_token FROM runs WHERE run_id = ?", (run_id,)).fetchone()[0])


def test_only_the_recovery_lock_holder_recovers(home: Path, recovery) -> None:
    _child, run_id = _run_owned_by_child(home, die=True)
    assert recovery.recover_dead_owner_runs_now() == 0, "no lock, no recovery"
    assert _status(home, run_id) == RUN_STATUS_RUNNING
    with _as(None):
        recovery._ensure_runs_recovery()
    assert _status(home, run_id) == "interrupted"


def test_the_process_that_takes_a_run_forward_owns_it(home: Path) -> None:
    """Queued by one live process, taken running by another that then dies:
    the run is the dead executor's, so it is recovered."""
    import subprocess
    import sys
    import textwrap

    run_id = _start(home, actor=OWNER)  # created here: this live process owns it
    with sqlite3.connect(str(home / ".runs.db")) as conn:
        conn.execute("UPDATE runs SET status = 'queued' WHERE run_id = ?", (run_id,))
    script = textwrap.dedent(f"""
        import os
        from tinyassets.runs import RUN_STATUS_RUNNING, update_run_status
        update_run_status({str(home)!r}, {run_id!r}, status=RUN_STATUS_RUNNING)
        os._exit(0)
    """)
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", script], cwd=str(Path(__file__).resolve().parents[1]),
        check=True, timeout=60,
    )
    with _as(None):
        assert recover_in_flight_runs(home) == 1
    assert _status(home, run_id) == "interrupted"


# -- round 1 (gpt-6-astra): every terminal path, resumes, failed emits --------


def test_a_failed_emit_stays_owed_and_is_delivered_later(home: Path, monkeypatch) -> None:
    import tinyassets.automation_events as events
    from tinyassets.runs import deliver_terminal_events

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    real = events._emit

    def broken(*a, **k):
        raise sqlite3.OperationalError("database is locked")

    monkeypatch.setattr(events, "_emit", broken)
    _finish(home, actor=OWNER)
    assert _wakes(home) == []
    monkeypatch.setattr(events, "_emit", real)
    with _as(None):
        assert deliver_terminal_events(home, older_than=0) == 1
    assert len(_wakes(home)) == 1


def test_a_terminal_status_written_by_any_path_is_announced(home: Path) -> None:
    """terminalize_unstarted_run, delivery recovery and any direct write reach
    the outbox through the status trigger, not through update_run_status."""
    from tinyassets.runs import deliver_terminal_events, runs_db_path

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    run_id = _start(home, actor=OWNER)
    with sqlite3.connect(runs_db_path(home)) as conn:
        conn.execute("UPDATE runs SET status = 'failed' WHERE run_id = ?", (run_id,))
    with _as(None):
        assert deliver_terminal_events(home, older_than=0) == 1
    [wake] = _wakes(home)
    assert (wake.inputs["event"]["run_id"], wake.inputs["event"]["outcome"]) == (
        run_id, "failed")


def test_a_resumed_run_that_ends_again_is_announced_again(home: Path) -> None:
    from tinyassets.runs import RUN_STATUS_RESUMED

    _subscribe(home, "run_completed",
               {"branch_def_id": FOLLOWED, "outcome": "completed"})
    run_id = _start(home, actor=OWNER)
    with _as("acct_stranger"):
        update_run_status(home, run_id, status="interrupted", finished_at=1.0)
    assert _wakes(home) == [], "the filter wants completed"
    update_run_status(home, run_id, status=RUN_STATUS_RESUMED)
    update_run_status(home, run_id, status=RUN_STATUS_RUNNING)
    with _as("acct_stranger"):
        update_run_status(home, run_id, status=RUN_STATUS_COMPLETED, finished_at=2.0)
    [wake] = _wakes(home)
    assert (wake.inputs["event"]["run_id"], wake.inputs["event"]["outcome"]) == (
        run_id, "completed")


def test_a_run_whose_resumer_died_before_it_ran_is_recovered(home: Path) -> None:
    """Resumed by another process, which died before the run went running:
    the resumer owns it, so it is recovered rather than stuck 'resumed'."""
    import subprocess
    import sys
    import textwrap

    run_id = _start(home, actor=OWNER)  # this live process created it
    with _as("acct_stranger"):
        update_run_status(home, run_id, status="interrupted", finished_at=1.0)
    script = textwrap.dedent(f"""
        import os
        from tinyassets.runs import RUN_STATUS_RESUMED, update_run_status
        update_run_status({str(home)!r}, {run_id!r}, status=RUN_STATUS_RESUMED)
        os._exit(0)
    """)
    subprocess.run(  # noqa: S603 - fixed argv
        [sys.executable, "-c", script], cwd=str(Path(__file__).resolve().parents[1]),
        check=True, timeout=60,
    )
    with _as(None):
        assert recover_in_flight_runs(home) == 1
    assert _status(home, run_id) == "interrupted"


def test_each_terminal_transition_of_a_resumed_run_wakes_once(home: Path) -> None:
    from tinyassets.runs import RUN_STATUS_RESUMED, deliver_terminal_events

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    run_id = _start(home, actor=OWNER)
    with _as("acct_stranger"):
        update_run_status(home, run_id, status="interrupted", finished_at=1.0)
    update_run_status(home, run_id, status=RUN_STATUS_RESUMED)
    update_run_status(home, run_id, status=RUN_STATUS_RUNNING)
    with _as("acct_stranger"):
        update_run_status(home, run_id, status=RUN_STATUS_COMPLETED, finished_at=2.0)
    with _as(None):
        deliver_terminal_events(home, older_than=0)  # nothing left owed, no dupes
    assert sorted(w.inputs["event"]["outcome"] for w in _wakes(home)) == [
        "completed", "interrupted"]


def test_two_answers_on_one_request_are_two_events() -> None:
    from types import SimpleNamespace

    from tinyassets.automation_events import _event_key

    sub = SimpleNamespace(automation_id="sub_1")
    first = {"request_id": "r", "kind": "choice", "status": "open", "item_id": "a"}
    second = {**first, "item_id": "b", "status": "answered"}
    assert _event_key(sub, "pending_request_answered", first, "") != _event_key(
        sub, "pending_request_answered", second, "")
    assert _event_key(sub, "run_completed", {"run_id": "x"}, "x#1") != _event_key(
        sub, "run_completed", {"run_id": "x"}, "x#2")


def test_cleanup_reads_references_after_proving_death(home: Path, monkeypatch) -> None:
    """A run committed just before its process died must keep that process's
    lock file: the references are read AFTER the probe proves death."""
    from tinyassets import process_liveness
    from tinyassets.runs import runs_db_path
    from tinyassets.runtime.assigned_queue_consumer import AssignedQueueConsumer

    token = "proc_dead_race"
    path = process_liveness.liveness_path(home, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()
    run_id = _start(home, actor=OWNER)
    real = process_liveness.owner_state

    def probe_then_commit(base, holder):
        state = real(base, holder)
        if holder == token:  # the dying process's last commit lands now
            with sqlite3.connect(runs_db_path(home)) as conn:
                conn.execute("UPDATE runs SET owner_token = ? WHERE run_id = ?",
                             (token, run_id))
        return state

    monkeypatch.setattr(process_liveness, "owner_state", probe_then_commit)
    consumer = AssignedQueueConsumer(home)
    try:
        consumer._hold_liveness()
    finally:
        consumer._release_liveness()
    assert path.is_file(), "the run's proof of death was deleted"


# -- round 2 (gpt-6-astra) ------------------------------------------------------


def test_a_transient_refusal_keeps_the_event_owed(home: Path, monkeypatch) -> None:
    """No connected source at the moment the run ends leaves its event owed."""
    from tinyassets.providers import served_model_plan
    from tinyassets.runs import deliver_terminal_events

    _subscribe(home, "run_completed", {"branch_def_id": FOLLOWED})
    real = served_model_plan.connected_sources
    assert real(home, home / UNIVERSE, OWNER)
    monkeypatch.setattr(served_model_plan, "connected_sources", lambda *a, **k: frozenset())
    _finish(home, actor=OWNER)
    with _as(None):
        assert deliver_terminal_events(home, older_than=0) == 0
    assert _wakes(home) == []
    monkeypatch.setattr(served_model_plan, "connected_sources", real)
    with _as(None):
        assert deliver_terminal_events(home, older_than=0) == 1
    assert len(_wakes(home)) == 1


@pytest.mark.skipif(__import__("os").name != "posix",
                    reason="Windows refuses to unlink a file another handle holds open")
def test_a_registrant_whose_file_was_cleaned_up_locks_a_new_one(
    tmp_path: Path, monkeypatch,
) -> None:
    """Cleanup unlinked a fresh registrant's file between its creation and its
    lock: the registrant notices and locks a file that is really at the path."""
    from tinyassets import process_liveness, singleton_lock

    real = singleton_lock.acquire_singleton_lock
    calls: list[int] = []

    def racing(path):
        held = real(path)
        if not calls:
            Path(path).unlink()  # cleanup's unlink lands after our lock
        calls.append(1)
        return held

    monkeypatch.setattr(singleton_lock, "acquire_singleton_lock", racing)
    held = process_liveness.hold_liveness(tmp_path, "proc_racer")
    assert held.acquired and len(calls) == 2
    assert process_liveness.owner_state(tmp_path, "proc_racer") == process_liveness.ALIVE


@pytest.mark.skipif(__import__("os").name != "posix",
                    reason="Windows refuses to unlink a file another handle holds open")
def test_cleanup_decides_and_deletes_under_the_files_lock(tmp_path: Path) -> None:
    """Round 3: a registrant that locks the file while cleanup is deciding must
    not end up holding a deleted file. Cleanup holds the lock from its decision
    through the delete, so the registrant waits and then locks a live file."""
    import threading

    from tinyassets import process_liveness

    token = "proc_fresh_registrant"
    path = process_liveness.liveness_path(tmp_path, token)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()  # created, not yet locked: reads as dead
    registered: list = []

    def still_named(_token: str) -> bool:
        # The registrant arrives while cleanup is deciding.
        worker = threading.Thread(
            target=lambda: registered.append(
                process_liveness.hold_liveness(tmp_path, token)),
        )
        worker.start()
        worker.join(0.3)
        registered.append(worker)
        return False

    assert process_liveness.remove_if_dead(tmp_path, token, still_named)
    registered[0].join(10)
    held = registered[-1]
    assert held.acquired
    assert process_liveness.owner_state(tmp_path, token) == process_liveness.ALIVE
