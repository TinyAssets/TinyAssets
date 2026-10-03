"""A node wakes one of its owner's branches, now or not before a time.

`invoke_mcp_action('enqueue_branch_run', ...)` stores a one-shot automation
(kind `once`) that the real automation pump fires once `not_before` has
passed. Driven end to end here: a real compiled code node calling the verb,
the real `register_automation`, the real store, the real consumer pump. The
only substitute is `_execute`, the seam `tinyassets.automations` names for
tests -- it fakes the graph a wake runs, never the scheduling or the checks.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver

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
    MAX_ONCE_ATTEMPTS,
    ONCE_RETRY_SECONDS,
    TRIGGER_ONCE,
    Automation,
    AutomationStore,
    due_automations,
    register_automation,
    run_due_automation,
)
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)
from tinyassets.graph_compiler import CompilerError, NodeEnqueueContext, compile_branch

pytestmark = pytest.mark.usefixtures("cloud_runtime")

PRIVATE = "branch_owners_private"
BOB = "acct_bob"
BOB_UNIVERSE = "universe_bob"
BOB_BRANCH = "branch_bobs_own"


@pytest.fixture(autouse=True)
def _pin_data_dir(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    # On by default: nothing in the environment turns the verb on.
    monkeypatch.delenv("TINYASSETS_NODE_ENQUEUE_ENABLED", raising=False)


@pytest.fixture
def home(tmp_path: Path, monkeypatch) -> Path:
    """Alice's serving home with a PRIVATE branch she authored; Bob next door."""
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
    _seed_serving_assignment(tmp_path)
    _seed_owner(tmp_path)
    _seed_branch(tmp_path, branch_def_id=PRIVATE, visibility="private")
    _seed_owner(tmp_path, universe_id=BOB_UNIVERSE, owner=BOB)
    _seed_branch(tmp_path, branch_def_id=BOB_BRANCH, author=BOB, visibility="private")
    monkeypatch.setattr(
        "tinyassets.provider_serving_binding.list_serving_universes",
        lambda _base: [UNIVERSE],
    )
    return tmp_path


def _waker(call: str) -> BranchDefinition:
    """A one-node graph whose code node calls the verb with `call`'s kwargs."""
    source = (
        "def run(state):\n"
        f"    r = invoke_mcp_action('enqueue_branch_run', {call})\n"
        "    return {'wake': r['automation_id']}\n"
    )
    branch = BranchDefinition(name="waker", entry_point="only")
    branch.node_defs = [NodeDefinition(
        node_id="only", display_name="Only", source_code=source,
        output_keys=["wake"], tools_allowed=["enqueue_branch_run"],
    ).mark_approved()]
    branch.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    branch.state_schema = [{"name": "wake", "type": "str"}]
    return branch


def _run_as(
    base: Path, actor: str | None, call: str, *, universe: str = UNIVERSE,
    run_actor: str | None = None,
):
    compiled = compile_branch(
        _waker(call), base_path=str(base),
        enqueue_context=NodeEnqueueContext(
            universe_id=universe,
            actor=actor or "" if run_actor is None else run_actor,
        ),
    )
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    identity = None if actor is None else Identity(user_id=actor, username=actor)
    with identity_context(identity):
        return app.invoke({}, config={"configurable": {"thread_id": call[:40]}})


def _wakes(base: Path, universe: str = UNIVERSE, **kw) -> list[Automation]:
    return [
        row for row in AutomationStore(base).list(universe_id=universe, **kw)
        if row.trigger_kind == TRIGGER_ONCE
    ]


class _Graph:
    """The `_execute` seam: records each fired wake."""

    def __init__(self, status: str = "completed", then=None) -> None:
        self.calls: list[str] = []
        self.status = status
        self.then = then

    def __call__(self, base_path, automation, provider_call, branch, inputs,
                 on_run_started=None):
        self.calls.append(automation.branch_def_id)
        if self.then is not None:
            self.then(base_path, automation)
        return _FakeOutcome(run_id=f"run_{len(self.calls)}", status=self.status)


def _poll(base: Path) -> None:
    consumer, _inline = _consumer_with_inline_executor(base)
    try:
        consumer.poll_once()
    finally:
        consumer.stop()


def _age(base: Path, automation_id: str, seconds: int) -> None:
    """Move `not_before` back, as if `seconds` had passed."""
    row = AutomationStore(base).get(automation_id)
    earlier = datetime.fromisoformat(row.not_before) - timedelta(seconds=seconds)
    with sqlite3.connect(AutomationStore(base).db_path) as conn:
        conn.execute(
            "UPDATE automations SET not_before = ? WHERE automation_id = ?",
            (earlier.replace(microsecond=0).isoformat(), automation_id),
        )


# -- The verb -------------------------------------------------------------------


def test_a_node_wakes_its_owners_private_branch_later(home: Path) -> None:
    before = datetime.now(timezone.utc).replace(microsecond=0)
    result = _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}, delay_seconds=600,"
                                  " inputs={'why': 'check back'}")

    [wake] = _wakes(home)
    assert result["wake"] == wake.automation_id
    assert (wake.universe_id, wake.owner_principal_id, wake.branch_def_id) == (
        UNIVERSE, OWNER, PRIVATE,
    )
    assert wake.inputs == {"why": "check back"}
    at = datetime.fromisoformat(wake.not_before)
    assert before + timedelta(seconds=599) <= at <= before + timedelta(seconds=602)
    assert due_automations(home, universe_id=UNIVERSE, now=at - timedelta(seconds=1)) == []
    assert [a.automation_id for a, _d in due_automations(
        home, universe_id=UNIVERSE, now=at)] == [wake.automation_id]


def test_a_background_run_wakes_as_its_bound_owner_not_its_run_actor(
    home: Path,
) -> None:
    """An automation's run actor is `universe:<id>`, which owns nothing."""
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}", run_actor=f"universe:{UNIVERSE}")
    [wake] = _wakes(home)
    assert wake.owner_principal_id == OWNER


def test_not_before_is_accepted_and_a_past_one_means_now(home: Path) -> None:
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}, not_before='2026-01-01T00:00:00Z'")
    [wake] = _wakes(home)
    assert datetime.fromisoformat(wake.not_before) > datetime(2026, 9, 1, tzinfo=timezone.utc)


def test_a_wake_can_be_scheduled_more_than_a_year_ahead(home: Path) -> None:
    before = datetime.now(timezone.utc).replace(microsecond=0)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}, delay_seconds=400*86400")
    after = datetime.now(timezone.utc)
    [wake] = _wakes(home)
    scheduled = datetime.fromisoformat(wake.not_before)
    assert before + timedelta(days=400) <= scheduled <= after + timedelta(days=400)


@pytest.mark.parametrize(
    ("call", "needle"),
    [
        (f"branch_def_id={PRIVATE!r}, delay_seconds=5, not_before='2027-01-01T00:00:00Z'",
         "not both"),
        (f"branch_def_id={PRIVATE!r}, delay_seconds=-1", ">= 0"),
        (f"branch_def_id={PRIVATE!r}, delay_seconds=True", "a number"),
        (f"branch_def_id={PRIVATE!r}, not_before='soon'", "trigger_invalid"),
    ],
)
def test_malformed_timing_is_refused_and_nothing_is_stored(
    home: Path, call: str, needle: str
) -> None:
    with pytest.raises(CompilerError) as refused:
        _run_as(home, OWNER, call)
    assert needle in str(refused.value)
    assert _wakes(home) == []


# -- The cross-user floor --------------------------------------------------------


@pytest.mark.parametrize(
    ("actor", "call", "universe", "needle"),
    [
        # A branch-named universe other than the run's own.
        (OWNER, f"branch_def_id={PRIVATE!r}, universe_id={BOB_UNIVERSE!r}", UNIVERSE,
         "cannot target command center"),
        # Nobody bound: there is no one to run the wake as.
        (None, f"branch_def_id={PRIVATE!r}", UNIVERSE, "no owner is bound"),
        # Bob bound inside Alice's universe: not an admin there.
        (BOB, f"branch_def_id={BOB_BRANCH!r}", UNIVERSE, "owner_not_admin"),
        # Alice waking Bob's branch in her own universe.
        (OWNER, f"branch_def_id={BOB_BRANCH!r}", UNIVERSE, "branch_not"),
        # Alice's run executing in Bob's universe (a trusted context she lacks).
        (OWNER, f"branch_def_id={PRIVATE!r}", BOB_UNIVERSE, "owner_not_admin"),
    ],
)
def test_the_floor_is_cross_user(home: Path, actor, call, universe, needle) -> None:
    with pytest.raises(CompilerError) as refused:
        _run_as(home, actor, call, universe=universe)
    assert needle in str(refused.value)
    assert _wakes(home, UNIVERSE, include_retired=True) == []
    assert _wakes(home, BOB_UNIVERSE, include_retired=True) == []


# -- The pump -------------------------------------------------------------------


def test_a_wake_fires_once_after_its_time_and_retires(home: Path, monkeypatch) -> None:
    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}, delay_seconds=600")
    [wake] = _wakes(home)

    _poll(home)
    assert graph.calls == []  # not yet
    _age(home, wake.automation_id, 600)
    _poll(home)
    _poll(home)
    assert graph.calls == [PRIVATE]  # exactly once
    spent = AutomationStore(home).get(wake.automation_id)
    assert spent.retired_at and spent.pause_reason == "ran"


def test_a_failed_run_still_spends_the_wake(home: Path, monkeypatch) -> None:
    graph = _Graph(status="failed")
    monkeypatch.setattr(automations_module, "_execute", graph)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    _poll(home)
    _poll(home)
    assert graph.calls == [PRIVATE]
    assert _wakes(home) == []


def test_a_branch_can_wake_itself_past_any_old_depth_cap(
    home: Path, monkeypatch
) -> None:
    """Each fired run re-wakes the same branch, as its node would, as the owner."""

    def rewake(base, automation):
        from tinyassets.api.permissions import (
            current_request_actor_id,
            owner_run_identity,
        )

        # The seam replaces `_execute`, which binds the owner for the run's
        # nodes; bind as it does, then register as the verb does.
        with owner_run_identity(base, automation.universe_id,
                                automation.owner_principal_id):
            register_automation(
                base, universe_id=automation.universe_id,
                owner_principal_id=current_request_actor_id(),
                name="again", branch_def_id=automation.branch_def_id,
                not_before=datetime.now(timezone.utc).isoformat(),
            )

    graph = _Graph(then=rewake)
    monkeypatch.setattr(automations_module, "_execute", graph)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    for _hop in range(6):
        _poll(home)
    assert graph.calls == [PRIVATE] * 6  # the retired cap stopped at hop 2
    assert len(_wakes(home)) == 1  # one pending row at a time


def test_the_real_execute_binds_the_owner_for_the_runs_nodes(
    home: Path, monkeypatch
) -> None:
    """A wake's own nodes can wake again only if the owner is bound in its run."""
    from tinyassets import runs
    from tinyassets.api.permissions import current_request_actor_id

    seen: list[str] = []

    def launch(*_a, **_k):
        seen.append(current_request_actor_id())
        return runs.RunOutcome(run_id="", status=runs.RUN_STATUS_FAILED, output={})

    monkeypatch.setattr(runs, "execute_branch_async", launch)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    _poll(home)
    assert seen == [OWNER]


# -- Deploys, retries and usage ----------------------------------------------------


def _register_cadence(base: Path, created: datetime) -> Automation:
    """A 10-minute cadence on the private branch, registered as its owner."""
    with identity_context(Identity(user_id=OWNER, username=OWNER)):
        return register_automation(
            base, universe_id=UNIVERSE, owner_principal_id=OWNER, name="c",
            branch_def_id=PRIVATE, interval_seconds=600, now=created,
        )


def _claim_and_die(base: Path, now: datetime) -> tuple[Automation, str]:
    """What a process killed before its run started leaves: a claim, nothing else."""
    [(wake, key)] = due_automations(base, universe_id=UNIVERSE, now=now)
    assert AutomationStore(base).claim_attempt(wake.automation_id, key, now=now)
    return wake, key


def _retry_at(base: Path, automation_id: str) -> datetime:
    row = AutomationStore(base).get(automation_id)
    return datetime.fromisoformat(row.last_claimed_at) + timedelta(
        seconds=ONCE_RETRY_SECONDS
    )


def test_a_wake_killed_before_its_run_is_retried_under_a_new_key(
    home: Path, monkeypatch
) -> None:
    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    wake, first_key = _claim_and_die(home, datetime.now(timezone.utc))
    _poll(home)
    assert graph.calls == []  # the retry key is ONCE_RETRY_SECONDS after the claim
    retry_at = _retry_at(home, wake.automation_id)
    [(again, retry_key)] = due_automations(home, universe_id=UNIVERSE, now=retry_at)
    assert retry_key > first_key
    assert run_due_automation(home, again, retry_key, now=retry_at).startswith("ok:ran:")
    assert graph.calls == [PRIVATE]
    assert AutomationStore(home).get(wake.automation_id).retired_at


def test_a_wake_is_spent_the_moment_its_run_exists(home: Path, monkeypatch) -> None:
    """Refute P1 #8: a process killed MID-run must not repeat the work later."""
    seen: list[str] = []

    def running(base_path, automation, provider_call, branch, inputs,
                on_run_started=None):
        on_run_started("run_live")
        seen.append(AutomationStore(base_path).get(automation.automation_id).retired_at)
        return _FakeOutcome(run_id="run_live")

    monkeypatch.setattr(automations_module, "_execute", running)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    _poll(home)
    assert len(seen) == 1 and seen[0]  # retired while the run was still going


def test_a_stale_snapshot_of_a_spent_or_paused_row_does_not_run(
    home: Path, monkeypatch
) -> None:
    """Refute P1 #9: a due scan's row can be retired or paused before its run."""
    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    store = AutomationStore(home)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    # Read the clock AFTER the wake exists: its not_before is its creation second.
    now = datetime.now(timezone.utc)
    [(wake, key)] = due_automations(home, universe_id=UNIVERSE, now=now)
    store.retire_for_reason(wake.automation_id, reason="ran", now=now)
    assert run_due_automation(home, wake, key, now=now) == "not_active"

    cadence = _register_cadence(home, now - timedelta(hours=1))
    [(snapshot, cadence_key)] = [
        pair for pair in due_automations(home, universe_id=UNIVERSE, now=now)
        if pair[0].automation_id == cadence.automation_id
    ]
    store.set_desired_state(
        cadence.automation_id, "paused", expected_revision=1, now=now
    )
    assert run_due_automation(home, snapshot, cadence_key, now=now) == "not_active"
    assert graph.calls == []


def test_five_killed_claims_retire_the_wake(home: Path) -> None:
    """Refute P2 #11: attempts that never reach `_retire_once` still end it."""
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    now = datetime.now(timezone.utc)
    wake, _key = _claim_and_die(home, now)
    for _attempt in range(MAX_ONCE_ATTEMPTS - 1):
        _claim_and_die(home, _retry_at(home, wake.automation_id))
    at = _retry_at(home, wake.automation_id)
    [(spent, key)] = due_automations(home, universe_id=UNIVERSE, now=at)
    assert run_due_automation(home, spent, key, now=at) == "gave_up"
    row = AutomationStore(home).get(wake.automation_id)
    assert row.retired_at and row.pause_reason == "gave_up"


def test_a_late_resumed_wake_does_not_find_every_retry_already_due(
    home: Path,
) -> None:
    """Refute P2 #10: retries step from the latest claim, not from not_before."""
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    [wake] = _wakes(home)
    _age(home, wake.automation_id, 3600)  # an hour overdue
    now = datetime.now(timezone.utc)
    _claim_and_die(home, now)
    assert due_automations(home, universe_id=UNIVERSE, now=now) == []


def test_a_cadence_does_not_count_its_attempt_history(home: Path) -> None:
    """Refute P2 #13: only wakes pay for the attempt subqueries."""
    now = datetime.now(timezone.utc)
    cadence = _register_cadence(home, now)
    store = AutomationStore(home)
    for minute in range(5):
        store.claim_attempt(
            cadence.automation_id, f"2026-01-01T00:0{minute}:00+00:00", now=now
        )
    row = store.get(cadence.automation_id)
    assert (row.attempt_count, row.last_claimed_at) == (0, "")



def test_a_wake_waiting_for_a_seat_spends_none_of_its_attempts(home: Path, monkeypatch) -> None:
    """A wake has `MAX_ONCE_ATTEMPTS` tries, and a seat wait is not one of them: it
    waits before it claims, so however long its account is busy, the wake is
    still owed, unretired and unfailed -- and runs when a seat frees."""
    from tinyassets import universe_seats as seats

    graph = _Graph()
    monkeypatch.setattr(automations_module, "_execute", graph)
    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    [wake] = _wakes(home)
    key = seats.account_key(UNIVERSE, root=home)
    db = seats.ledger_path(home)
    blockers = [seats.acquire(key, db=db) for _ in range(2)]  # free: 2 background
    at = datetime.now(timezone.utc)
    try:
        for _poll in range(MAX_ONCE_ATTEMPTS + 3):
            [(due, due_at)] = due_automations(home, universe_id=UNIVERSE, now=at)
            assert run_due_automation(home, due, due_at, now=at) == "waiting_for_seat"
        row = AutomationStore(home).get(wake.automation_id)
        assert row.attempt_count == 0
        assert row.consecutive_failures == 0
        assert not row.retired_at
        assert graph.calls == []
    finally:
        for held in blockers:
            seats.release(held.seat_id, db=db)
    [(due, due_at)] = due_automations(home, universe_id=UNIVERSE, now=at)
    assert run_due_automation(home, due, due_at, now=at).startswith("ok:ran:")
    assert len(graph.calls) == 1
    seats.stop_refresher()

# -- Owner surface and storage ---------------------------------------------------


def test_the_owner_sees_a_pending_wake_with_its_time(home: Path) -> None:
    from tinyassets.api.automations import _projection

    _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}, delay_seconds=60")
    [wake] = _wakes(home)
    projected = _projection(wake, actor=OWNER)
    assert projected["trigger"]["kind"] == TRIGGER_ONCE
    assert projected["trigger"]["not_before"] == wake.not_before
    assert projected["next_due_at"] == wake.not_before


def test_a_database_from_the_previous_build_accepts_a_once_row(tmp_path: Path) -> None:
    old = automations_module._AUTOMATIONS_TABLE.replace("__TABLE__", "automations")
    old = old.replace(",'once'", "")
    assert "'once'" not in old
    with sqlite3.connect(AutomationStore(tmp_path).db_path) as conn:
        conn.executescript(old)
        conn.execute(
            "ALTER TABLE automations ADD COLUMN consecutive_failures "
            "INTEGER NOT NULL DEFAULT 0"
        )
        conn.execute(
            "INSERT INTO automations (automation_id, universe_id, "
            "owner_principal_id, name, branch_def_id, trigger_kind, "
            "interval_seconds, desired_state, created_at, updated_at, "
            "consecutive_failures) VALUES ('old', ?, ?, 'Nightly', ?, 'interval', "
            "600, 'active', '2026-08-29T12:00:00+00:00', "
            "'2026-08-29T12:00:00+00:00', 2)",
            (UNIVERSE, OWNER, PRIVATE),
        )
    store = AutomationStore(tmp_path)
    kept = store.get("old")
    assert (kept.trigger_kind, kept.interval_seconds, kept.consecutive_failures) == (
        "interval", 600, 2,
    )
    store.insert(replace(
        kept, automation_id="new", trigger_kind=TRIGGER_ONCE,
        not_before="2026-09-27T00:00:00+00:00", created_at="2026-08-30T00:00:00+00:00",
    ))
    assert store.get("new").trigger_kind == TRIGGER_ONCE
    assert [row.automation_id for row in store.list(universe_id=UNIVERSE)] == [
        "old", "new",
    ]


def test_the_verb_result_is_json(home: Path) -> None:
    """The node receives a structured receipt it can act on."""
    compiled_result = _run_as(home, OWNER, f"branch_def_id={PRIVATE!r}")
    assert json.loads(json.dumps(compiled_result))["wake"]
