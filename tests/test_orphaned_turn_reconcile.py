"""A deploy killed the turn; the row kept saying "thinking" for 35 minutes.

Founder, 2026-09-26: a deploy at 22:31:54Z recreated the daemon container and
killed the in-flight turn ``652a2f31e82546a1bb56b5d158b5490f``. Its
``agent_turns`` row still read ``native_started`` 35 minutes later, and the app's
server-driven status line (``get_status.active_turn``) rendered "Your universe is
thinking... for 34m 55s" off it while nothing was running. It would have stayed
that way until the granted-turn cap aged it out an hour in.

Two halves, with an ownership guard between them that has to be right in both
directions:

* a row an EARLIER owner generation left progressing is settled at startup and
  stops reading as activity;
* a turn the CURRENT owner is running is untouched, by the sweep and by the
  projection -- reaping a live turn would tell the founder their running turn had
  been interrupted, which is the worse of the two failures.

Ownership is the owner lease generation (change execution-owner-lease D2). A
restart is simulated the way the kernel performs one: the test's owner tree
LEAVES (its member lock is released, exactly what process death does), and the
next lease use starts a new tree, which may take the command center's key only
because the old tree is now provably dead.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_agent_native_journal import begin_native
from tests.test_agent_turn_journal import (
    begin,
    journal,
    new,
    receive,
    start,
)
from tinyassets import owner_lease
from tinyassets.agent_turn_reconcile import reconcile_orphaned_turns
from tinyassets.storage.agent_native_records import NativeTerminal
from tinyassets.storage.agent_turn_boot import BOOT, BootTurns
from tinyassets.storage.agent_turn_journal import (
    WORKING_STATES,
    ensure_schema,
    reset_blockers,
)

__all__ = ["journal"]  # Reuse the real owner/home journal fixture.

_CAP = 3630.0  # the status projection's own bound (tests/test_universe_working_turn.py)
_KILLED_AGE_S = 2100  # what the founder actually saw: 35 minutes of "thinking"


def _now():
    return datetime.now(timezone.utc)


def _age(journal, turn_id, seconds=_KILLED_AGE_S):
    """Put the row where a killed container's leftover is: older than this boot."""
    older = (_now() - timedelta(seconds=seconds)).isoformat(
        timespec="microseconds").replace("+00:00", "Z")
    with journal._ledger.connection() as conn:
        ensure_schema(conn)
        conn.execute("BEGIN IMMEDIATE")
        conn.execute("UPDATE agent_turns SET created_at = ? WHERE turn_id = ?", (older, turn_id))
        conn.commit()


def _working(journal, *, boot=BOOT):
    return journal.universe_working_turn("home", now=_now(), max_age_s=_CAP, boot=boot)


def _restart(journal):
    """The owner that created the turns dies; the next lease use is a new owner."""
    owner_lease.current_tree(journal._ledger.base_path).leave()


def _reconcile(journal):
    return reconcile_orphaned_turns(journal._ledger.base_path)


def _home(journal):
    """The universe directory. A live universe always has one; the journal
    fixture registers the home binding without creating the folder, and the
    conversation store writes INSIDE it."""
    path = journal._ledger.base_path / "home"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _killed_native_turn(journal):
    """One ``native_started`` turn, aged to when the deploy killed the real one."""
    _home(journal)
    turn = begin_native(journal, new(journal)).snapshot
    assert turn.state == "native_started"
    _age(journal, turn.turn_id)
    return turn


def _blockers(journal):
    with journal._ledger.connection() as conn:
        ensure_schema(conn)
        conn.execute("BEGIN")
        return reset_blockers(conn, "owner", "home")


def test_a_row_a_dead_boot_left_native_started_is_settled_and_stops_reading_as_active(journal):
    turn = _killed_native_turn(journal)
    # The precondition, not an assumption: this is the row the indicator painted.
    painted = _working(journal)
    assert painted is not None and painted["state"] == "native_started"
    assert painted["age_s"] > 2000

    _restart(journal)
    settled = _reconcile(journal)

    assert settled == [{
        "universe_id": "home", "turn_id": turn.turn_id,
        "was": "native_started", "settled": "held_native_unknown", "notified": True,
    }]
    stored = journal.get("owner", "home", turn.turn_id)
    assert stored.state == "held_native_unknown"
    # Uncertainty is PRESERVED, not resolved: the killed round is indeterminate,
    # never completed, and it still blocks an offline reset. A settlement that
    # claimed the turn finished would be a mock result wearing a real shape.
    assert stored.rounds[-1].reply == NativeTerminal("indeterminate")
    assert stored.rounds[-1].cost_microusd is None
    assert _blockers(journal) == ["active or ambiguous agent turn references exact home"]
    # Nothing reports it now.
    assert _working(journal) is None


def test_a_turn_the_current_owner_is_running_is_never_reaped(journal):
    """Age is irrelevant: the row is as old as the killed one, but its generation
    is the one the live owner holds."""
    turn = _killed_native_turn(journal)

    assert _reconcile(journal) == []

    still = journal.get("owner", "home", turn.turn_id)
    assert still.state == "native_started" and still.generation == turn.generation
    assert still.rounds[-1].reply is None
    observed = _working(journal)
    assert observed is not None and observed["turn_id"] == turn.turn_id


def test_a_standby_owner_settles_nothing_while_the_live_owner_holds_the_key(journal):
    """The standby-start case (execution-owner-lease D2): a second owner process
    starts while the first still holds the command center and is running a turn.
    It cannot take the key, so it reconciles nothing -- however old the row."""
    turn = _killed_native_turn(journal)
    standby = owner_lease.OwnerTree.start(journal._ledger.base_path)
    try:
        with owner_lease.using_tree(standby):
            assert _reconcile(journal) == []
            assert journal.get("owner", "home", turn.turn_id).state == "native_started"
        # The live owner still sees its own turn as running.
        assert _working(journal)["turn_id"] == turn.turn_id
    finally:
        standby.leave()


def test_the_successor_takes_a_higher_generation_and_settles_only_older_rows(journal):
    """After the old owner dies, the successor's generation is above it: its own
    new turn survives the very sweep that settles the old owner's."""
    old = _killed_native_turn(journal)
    _restart(journal)
    assert [r["turn_id"] for r in _reconcile(journal)] == [old.turn_id]
    mine = begin_native(journal, new(journal)).snapshot
    assert _reconcile(journal) == []
    assert journal.get("owner", "home", mine.turn_id).state == "native_started"
    assert _working(journal)["turn_id"] == mine.turn_id


def test_the_projection_refuses_an_unowned_row_reconciliation_never_settled(journal):
    """Belt and braces: the guard stands on its own, with no sweep at all.

    Reconciliation can fail per row -- a rebound home raises ``CurrentHomeChanged``
    -- and the founder must still not see a phantom indicator.
    """
    turn = _killed_native_turn(journal)
    _restart(journal)

    assert _working(journal) is None
    # Proof it was the projection and not a settlement: the row is untouched.
    assert journal.get("owner", "home", turn.turn_id).state == "native_started"


def _ready(journal):
    return new(journal)


def _inference_started(journal):
    return begin(journal, new(journal))


def _native_started(journal):
    return begin_native(journal, new(journal)).snapshot


def _tools_planned(journal):
    return receive(journal, begin(journal, new(journal)))


def _tools_started(journal):
    return start(journal, _tools_planned(journal)).snapshot


@pytest.mark.parametrize(("build", "expected"), [
    (_ready, "abandoned"),
    (_inference_started, "held_transport"),
    (_native_started, "held_native_unknown"),
    (_tools_planned, "held_tool_not_sent"),
    (_tools_started, "held_tool_unknown"),
])
def test_every_progressing_shape_a_dead_boot_can_leave_is_settled(journal, build, expected):
    """Each of the four progressing states, plus both tool shapes inside one.

    ``native_started`` is what the founder hit; the others reach the same visible
    bug through a different step, and one settled state per shape is the journal's
    own label for what that step proves -- ``not_sent`` for a tool still
    ``planned`` (a call is recorded ``started`` before dispatch, so one that never
    started was never sent) against ``unknown`` for one that had started.
    """
    turn = build(journal)
    assert turn.state in WORKING_STATES
    _age(journal, turn.turn_id)
    _restart(journal)

    settled = _reconcile(journal)

    assert [record["settled"] for record in settled] == [expected]
    assert journal.get("owner", "home", turn.turn_id).state == expected
    assert expected not in WORKING_STATES
    assert _working(journal) is None


def _thread(journal):
    from tinyassets.conversation_store import load_recent

    return load_recent(journal._ledger.base_path / "home", "principal:owner")


def test_the_interrupted_turn_is_left_visible_in_the_thread(journal):
    """Settling stops the indicator; without this the turn simply VANISHES.

    The founder sent a message, watched a phantom indicator, and then — once the
    row was settled — had nothing at all in the thread saying what happened.
    """
    turn = _killed_native_turn(journal)
    assert _thread(journal) == [], "precondition: nothing was ever stored for this turn"

    _restart(journal)
    settled = _reconcile(journal)

    assert settled[0]["notified"] is True
    messages = _thread(journal)
    assert len(messages) == 1, "one platform notice, and no invented founder half"
    notice = messages[0]
    assert notice.speaker == "platform"
    # Composed by conversation_failure from this turn's ledger, not written here.
    assert "something broke on our side" in notice.text
    assert "We can't tell whether actions ran" in notice.text, (
        "a killed native round is unknown effects; claiming nothing ran would be a lie")
    assert turn.turn_id in notice.text, "the ref ties the notice to the journal row"
    # The structured record rides along, which is what a client reads as `failure`.
    assert notice.failure is not None
    assert (notice.failure.code, notice.failure.stage) == ("platform_fault", "platform")
    assert notice.failure.effects == "unknown"
    assert notice.failure.ref == turn.turn_id


def test_the_notice_never_replays_the_stored_prompt_as_the_founders_message(journal):
    """The journal's `prompt` is `history_block + founder_message` for a granted
    turn (universe_intelligence._call_writer), so writing it back as the founder
    half would re-post the rendered conversation history as if they had typed it.
    """
    _home(journal)
    turn = journal.create(
        "owner", "home",
        prompt="PREVIOUS EXCHANGES\nfounder: an older line\n\nwhat did I just ask",
        system="s",
    )
    turn = begin_native(journal, turn).snapshot
    _age(journal, turn.turn_id)
    _restart(journal)

    _reconcile(journal)

    messages = _thread(journal)
    assert [m.speaker for m in messages] == ["platform"], "no founder row is invented"
    assert all("an older line" not in m.text for m in messages)
    assert all("what did I just ask" not in m.text for m in messages)


def test_a_second_sweep_over_the_same_turn_leaves_one_notice(journal):
    """Keyed on the journal's turn id, so a restart loop cannot stack notices."""
    turn = _killed_native_turn(journal)
    _restart(journal)
    _reconcile(journal)
    assert len(_thread(journal)) == 1

    # The row is settled now, so the sweep no longer sees it -- drive `_notify`
    # directly with the settled snapshot, which is what a repeat would do.
    from tinyassets.agent_turn_reconcile import _notify

    stored = journal.get("owner", "home", turn.turn_id)
    assert _notify(journal._ledger.base_path, "owner", "home", stored) is False
    assert len(_thread(journal)) == 1


def test_a_work_invocation_turn_has_no_thread_to_interrupt(journal):
    """Background work's evidence belongs to its receipt, not a founder thread."""
    from tinyassets.agent_turn_reconcile import _notify

    _home(journal)
    turn = journal.create(
        "owner", "home", prompt="p", system="s",
        authority_kind="work_invocation", work_receipt_id="receipt-1",
    )
    assert turn.authority_kind == "work_invocation"

    assert _notify(journal._ledger.base_path, "owner", "home", turn) is False
    assert _thread(journal) == []


def test_effects_evidence_has_one_definition_for_both_callers(journal):
    """The sweep and the running coordinator answer the same ledger question."""
    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator, turn_effects

    turn = _killed_native_turn(journal)
    running = AgentTurnCoordinator.__new__(AgentTurnCoordinator)
    running.turn = turn
    assert running.effects_evidence() == turn_effects(turn)
    assert turn_effects(turn) == ("unknown", None, turn.turn_id)
    assert turn_effects(None) == ("none", None, None)


def test_a_never_dispatched_call_still_reports_that_nothing_ran(journal):
    """The one invariant that makes the ``start_tool`` hack safe.

    A ``planned`` call has to be STARTED before ``finish_tool`` will settle it, so
    the sweep writes a ``started`` row for a call that was never dispatched. That
    is only honest because the row it writes next is ``not_sent``, which the
    effects summary does not count: the settled turn must report ``none``, not
    ``unknown``. If it ever reported ``unknown``, the transient ``started`` row
    would be leaking into the founder's notice as "actions may already have
    occurred" for a call that provably never left the process (review-gate on
    #4031 probed this and found nothing asserting it).
    """
    from tinyassets.agent_turn_coordinator import turn_effects

    turn = _tools_planned(journal)
    _home(journal)
    assert [tool.state for tool in turn.rounds[-1].tools] == ["planned"]
    _age(journal, turn.turn_id)
    _restart(journal)

    _reconcile(journal)

    stored = journal.get("owner", "home", turn.turn_id)
    assert stored.state == "held_tool_not_sent"
    assert [tool.state for tool in stored.rounds[-1].tools] == ["not_sent"]
    assert turn_effects(stored)[0] == "none", (
        "a call that never left the process must not be reported as maybe-run")
    assert "Nothing ran." in _thread(journal)[0].text
    assert _thread(journal)[0].failure.effects == "none"


def test_exactly_one_process_may_write_the_turn_journal(journal):
    """Today's deploy shape: one owner tree, one writable mount.

    The owner lease now makes a second writer SAFE (it cannot commit beside the
    first, change execution-owner-lease), but a second owner process is slice C's
    deliberate topology change, not something a ``workers=N`` or a writable
    sibling mount should introduce by accident. These two facts pin today's shape
    until then (review-gate on #4031).
    """
    import ast
    import pathlib

    import yaml

    from tinyassets import universe_server

    source = pathlib.Path(universe_server.__file__).read_text(encoding="utf-8")
    calls = [
        node for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "run"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "uvicorn"
    ]
    assert calls, "the ASGI launch moved; re-point this assertion at it"
    for call in calls:
        keywords = {keyword.arg for keyword in call.keywords}
        assert "workers" not in keywords, (
            "a multi-worker daemon makes two writers of agent_turns; see "
            "tinyassets/storage/agent_turn_boot.py")

    compose = yaml.safe_load(
        (pathlib.Path(universe_server.__file__).parents[1] / "deploy" / "compose.yml")
        .read_text(encoding="utf-8")
    )
    writable = {
        name for name, service in (compose.get("services") or {}).items()
        for volume in (service.get("volumes") or [])
        if "tinyassets-data" in str(volume) and not str(volume).endswith(":ro")
    }
    assert writable == {"daemon"}, (
        f"{sorted(writable)} can write the data volume; only the daemon may")


def test_a_missing_database_is_no_work_and_creates_nothing(tmp_path):
    absent = tmp_path / "never-served"
    assert reconcile_orphaned_turns(absent) == []
    assert not absent.exists(), "startup hygiene must not bring a database into being"


def test_a_database_without_the_turn_table_is_no_work(tmp_path):
    import sqlite3

    from tinyassets.storage import db_path

    base = tmp_path / "bare"
    base.mkdir()
    with sqlite3.connect(db_path(base)) as seed:
        seed.execute("CREATE TABLE sentinel (only_this TEXT)")

    assert reconcile_orphaned_turns(base) == []

    after = {row[0] for row in sqlite3.connect(db_path(base)).execute(
        "SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert after == {"sentinel"}, f"the scan created {sorted(after - {'sentinel'})}"


def test_one_unsettleable_turn_does_not_stop_the_sweep(journal, monkeypatch):
    """A rebound home must not leave every other universe's orphan thinking."""
    from tinyassets.storage.agent_turn_journal import AgentTurnJournal
    from tinyassets.storage.current_home import CurrentHomeChanged

    doomed = _killed_native_turn(journal)
    other = _killed_native_turn(journal)
    real = AgentTurnJournal.finish_native

    def refuse_one(self, owner, universe, turn_id, **kwargs):
        if turn_id == doomed.turn_id:
            raise CurrentHomeChanged("current universe home changed")
        return real(self, owner, universe, turn_id, **kwargs)

    _restart(journal)
    monkeypatch.setattr(AgentTurnJournal, "finish_native", refuse_one)
    settled = _reconcile(journal)
    monkeypatch.undo()

    by_turn = {record["turn_id"]: record for record in settled}
    assert by_turn[doomed.turn_id]["error"] == "CurrentHomeChanged"
    assert "notified" not in by_turn[doomed.turn_id], (
        "a row that was not settled must not tell the founder it was interrupted")
    assert by_turn[other.turn_id]["settled"] == "held_native_unknown"
    assert by_turn[other.turn_id]["notified"] is True
    assert journal.get("owner", "home", doomed.turn_id).state == "native_started"
    assert journal.get("owner", "home", other.turn_id).state == "held_native_unknown"


class _Coordinator:
    """The real ``run`` wrapper over a body that ends the way a deploy ends one."""

    from tinyassets.agent_turn_coordinator import AgentTurnCoordinator

    run = AgentTurnCoordinator.run
    _release_turn = AgentTurnCoordinator._release_turn
    effects_evidence = AgentTurnCoordinator.effects_evidence
    _carry_spent_attempts = AgentTurnCoordinator._carry_spent_attempts

    def __init__(self, turn, universe_dir, outcome):
        self.turn = turn
        self.context = SimpleNamespace(universe_dir=universe_dir)
        self.spent_attempts = []
        self._outcome = outcome

    async def _run(self):
        if isinstance(self._outcome, BaseException):
            raise self._outcome
        return self._outcome


@pytest.mark.parametrize("outcome", ["answered", RuntimeError("container went away")])
def test_the_coordinator_releases_its_turn_however_the_run_ends(journal, tmp_path, outcome):
    """Release is keyed on "nothing is executing it", not on a terminal state.

    A cancelled task leaves a progressing row behind with no process on it, and
    that row is exactly the one a surface must stop painting.
    """
    turn = _killed_native_turn(journal)
    assert _working(journal) is not None

    coordinator = _Coordinator(turn, tmp_path / "home", outcome)
    if isinstance(outcome, BaseException):
        with pytest.raises(RuntimeError):
            asyncio.run(coordinator.run())
    else:
        assert asyncio.run(coordinator.run()) == outcome

    assert journal.get("owner", "home", turn.turn_id).state == "native_started", (
        "release is bookkeeping; it must not touch the row")
    assert _working(journal) is None, (
        "no task in this process is running this turn any more")


@pytest.mark.usefixtures("cloud_runtime")  # the real lifespan admits the process
def test_the_serving_lifespan_settles_the_orphan_before_anything_can_read_it(
    journal, tmp_path, monkeypatch,
):
    """End to end through the REAL serving lifespan, not a call to the function.

    The row is put in exactly the state the founder's was after the 22:31:54Z
    deploy, using only production paths: a turn this process created, released the
    way the coordinator releases one when its run ends, and older than the boot
    that is now starting.
    """
    from starlette.testclient import TestClient

    from tinyassets import universe_server as us

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    # The lifespan advertises its owner tree in the environment; keep that local.
    monkeypatch.setenv(owner_lease.TREE_ENV, "")
    turn = _killed_native_turn(journal)
    _restart(journal)  # the container that ran it is gone
    monkeypatch.setattr(us, "start_scheduler_for_serving", lambda: True)
    monkeypatch.setattr(us, "stop_scheduler_for_serving", lambda: None)

    with TestClient(us.create_streamable_http_app()):
        pass

    assert journal.get("owner", "home", turn.turn_id).state == "held_native_unknown"
    assert _working(journal) is None, (
        "the indicator the founder watched for 35 minutes")
    # The whole chain, not just the half that stops the indicator: the founder
    # opens the app and the interrupted turn is THERE.
    thread = _thread(journal)
    assert [m.speaker for m in thread] == ["platform"]
    assert "something broke on our side" in thread[0].text


def test_this_process_remembers_only_which_of_its_turns_it_stopped_running():
    boot = BootTurns()
    assert boot.holds("home", "t") is False and boot.stopped("home", "t") is False
    boot.release("home", "t")  # never claimed here: ignored
    assert boot.stopped("home", "t") is False
    boot.claim("home", "t")
    assert boot.holds("home", "t") is True
    assert boot.holds("other", "t") is False, "claims are per universe"
    boot.release("home", "t")
    assert boot.holds("home", "t") is False and boot.stopped("home", "t") is True
    boot.release("home", "t")  # idempotent; a turn released twice is not an error
    assert boot.stopped("home", "t") is True


def test_the_first_leased_boot_settles_a_pre_lease_leftover(journal):
    """A row written before B1 deployed has no owner generation. The first boot
    that runs with leases must still settle it -- the boot rule this replaces
    did -- so pre-lease rows are generation 0, below every real generation."""
    turn = _killed_native_turn(journal)
    with journal._ledger.connection() as conn:
        # The pre-B1 schema: no owner_generation column at all. The next schema
        # check adds it with its backfill default, which is what production gets.
        conn.execute("ALTER TABLE agent_turns DROP COLUMN owner_generation")
        conn.commit()
    with journal._ledger.connection() as conn:
        ensure_schema(conn)  # the first B1 writer re-adds it, backfilled
    with owner_lease.lease_db(journal._ledger.base_path) as conn:
        conn.execute("DELETE FROM owner_lease")  # no owner has ever held the key
        conn.execute("DELETE FROM fence_high_water")
    _restart(journal)

    settled = _reconcile(journal)

    assert [r["turn_id"] for r in settled] == [turn.turn_id]
    assert owner_lease.held_generation(journal._ledger.base_path, "cc:home")[0] == 1


def test_a_stopped_turn_stays_stopped_until_its_row_settles(journal):
    """Finding 7: no count-based eviction can paint a cancelled turn as activity."""
    turn = _killed_native_turn(journal)
    BOOT.release("home", turn.turn_id)
    for i in range(5000):
        BOOT.claim("other", f"t{i}")
        BOOT.release("other", f"t{i}")
    assert _working(journal) is None
    for i in range(5000):
        BOOT.forget("other", f"t{i}")
