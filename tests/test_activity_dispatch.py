"""Durable activity dispatch settles, resumes and fences one live run."""
from __future__ import annotations

from pathlib import Path

import pytest

from tinyassets import activity_runner as runner
from tinyassets import agent_activities as acts
from tinyassets.control_plane import activities as dispatcher

OWNER = "acct_alice"


@pytest.fixture(autouse=True)
def _author_db(tmp_path):
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(tmp_path)


@pytest.fixture
def universe(tmp_path):
    path = tmp_path / "u-alpha"
    path.mkdir()
    return path


def _new(universe: Path, title="Draft the Monday report") -> str:
    return acts.create(universe, owner_principal=OWNER, title=title,
                       brief="Summarise last week into reports/monday.md",
                       origin_kind="ask")["activity_id"]


class _Runs:
    """Run states the dispatcher sees, and the runs the runner started."""

    def __init__(self, monkeypatch, universe: Path):
        self.status: dict[str, str] = {}
        self.output: dict[str, dict] = {}
        self.started: list[tuple[str, int]] = []
        self.owner_reason = ""
        base = universe.parent

        def start(base_path, universe_id, record, generation):
            run_id = f"run-{len(self.started) + 1}"
            self.started.append((record["activity_id"], generation))
            self.status[run_id] = "running"
            assert acts.bind_run(base / universe_id, record["activity_id"], generation, run_id)
            return run_id

        monkeypatch.setattr(runner, "start", start)
        monkeypatch.setattr(runner, "owner_unavailable", lambda *a: self.owner_reason)
        monkeypatch.setattr(runner, "state", lambda b, run_id: (
            runner.LIVE if self.status.get(run_id) in {"queued", "running"} else runner.ENDED))
        monkeypatch.setattr(dispatcher, "_run_outcome", lambda b, run_id: (
            self.status.get(run_id, ""), self.output.get(run_id, {}).get("result", "")))


def _dispatch(universe: Path) -> None:
    dispatcher.dispatch_universe(universe.parent, universe.name)


def test_a_queued_activity_gets_one_run_and_a_live_run_is_left_alone(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    aid = _new(universe)
    _dispatch(universe)
    _dispatch(universe)
    assert runs.started == [(aid, 1)]
    record = acts.get(universe, aid)
    assert record["status"] == acts.IN_PROGRESS and record["runner_token"] == "run-1"


@pytest.mark.parametrize("answer_first", [False, True])
@pytest.mark.parametrize("resolution", ["answered", "dismissed"])
def test_dispatch_reconciles_a_durable_answer_after_a_missed_wake(
    monkeypatch, universe, answer_first, resolution,
):
    from tinyassets.storage import pending_requests

    runs = _Runs(monkeypatch, universe)
    aid = _new(universe)
    _dispatch(universe)
    request = pending_requests.create_request(
        universe, kind="API", title="Proceed?", body="b", fields=[],
        action={"type": "answer"}, dedupe_key="activity-decision",
    )
    request_id = request["request_id"]

    def failed_hook(*args):
        raise OSError("the activity store was unavailable after the answer committed")

    monkeypatch.setattr(acts, "answered_request", failed_hook)
    if not answer_first:
        assert acts.wait_on(universe, aid, request_id)
    assert pending_requests.resolve_request(universe, request_id, status=resolution, answer={})
    if answer_first:
        assert acts.wait_on(universe, aid, request_id)
    assert acts.get(universe, aid)["status"] == acts.WAITING_ON_YOU
    # A repeated answer returns early; durable reconciliation must still repair it.
    assert not pending_requests.resolve_request(universe, request_id, status=resolution)
    _dispatch(universe)
    record = acts.get(universe, aid)
    assert record["status"] == acts.SCHEDULED and record["retiring_token"] == "run-1"
    assert runs.started == [(aid, 1)], "the retiring run must end before another can start"
    revision = record["revision"]
    _dispatch(universe)
    assert acts.get(universe, aid)["revision"] == revision
    runs.status["run-1"] = "completed"
    _dispatch(universe)
    _dispatch(universe)
    assert runs.started == [(aid, 1), (aid, 2)]


def test_answer_reconciliation_leaves_pending_and_paused_activities_alone(monkeypatch, universe):
    from tinyassets.storage import pending_requests

    runs = _Runs(monkeypatch, universe)
    aid = _new(universe)
    _dispatch(universe)
    request = pending_requests.create_request(
        universe, kind="API", title="Proceed?", body="b", fields=[],
        action={"type": "answer"}, dedupe_key="still-pending",
    )
    assert acts.wait_on(universe, aid, request["request_id"])
    _dispatch(universe)
    assert acts.get(universe, aid)["status"] == acts.WAITING_ON_YOU
    acts.transition(universe, aid, acts.PAUSED)
    assert pending_requests.resolve_request(universe, request["request_id"], status="answered")
    _dispatch(universe)
    assert acts.get(universe, aid)["status"] == acts.PAUSED
    assert runs.started == [(aid, 1)]


def test_a_completed_run_completes_the_activity_with_its_result(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    aid = _new(universe)
    _dispatch(universe)
    runs.status["run-1"] = "completed"
    runs.output["run-1"] = {"result": "Wrote reports/monday.md"}
    _dispatch(universe)
    record = acts.get(universe, aid)
    assert record["status"] == acts.COMPLETED and record["outcome"] == "done"
    assert record["result_summary"] == "Wrote reports/monday.md"
    assert len(runs.started) == 1


def test_a_failed_run_fails_the_activity(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    aid = _new(universe)
    _dispatch(universe)
    runs.status["run-1"] = "failed"
    _dispatch(universe)
    assert acts.get(universe, aid)["outcome"] == "failed:run_failed"


def test_an_interrupted_run_is_resumed_by_a_new_run(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    aid = _new(universe)
    _dispatch(universe)
    runs.status["run-1"] = "interrupted"
    _dispatch(universe)
    assert runs.started == [(aid, 1), (aid, 2)]
    record = acts.get(universe, aid)
    assert record["runner_token"] == "run-2" and record["runner_generation"] == 2
    assert acts.events_page(universe, aid)["events"][-1]["kind"] in {"resumed", acts.IN_PROGRESS}


def test_a_lost_owner_fails_and_no_model_waits(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    waiting, lost = _new(universe, "waits"), None
    runs.owner_reason = "no_serving_assignment"
    _dispatch(universe)
    assert acts.get(universe, waiting)["status"] == acts.SCHEDULED and runs.started == []
    assert acts.events_page(universe, waiting)["events"][-1]["kind"] == "waiting_for_seat"
    runs.owner_reason = "owner_lost_admin"
    lost = waiting
    _dispatch(universe)
    assert acts.get(universe, lost)["outcome"] == "failed:owner_lost"


def test_one_activity_failing_does_not_stop_the_others(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    first, second = _new(universe, "one"), _new(universe, "two")
    real_start = runner.start

    def flaky(base_path, universe_id, record, generation):
        if record["activity_id"] == first:
            raise RuntimeError("boom")
        return real_start(base_path, universe_id, record, generation)

    monkeypatch.setattr(runner, "start", flaky)
    _dispatch(universe)
    assert acts.get(universe, second)["runner_token"] == "run-1"
    assert runs.started == [(second, 1)]


def test_dispatch_all_reaches_every_universe_with_a_store(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(dispatcher, "dispatch_universe",
                        lambda b, uid, **kw: seen.append(uid))
    for name in ("u-a", "u-b"):
        (tmp_path / name).mkdir()
        acts.create(tmp_path / name, owner_principal=OWNER, title="t", brief="b",
                    origin_kind="ask")
    (tmp_path / "u-c").mkdir()
    dispatcher.dispatch_all(tmp_path)
    assert seen == ["u-a", "u-b"]


# -- the start barrier -------------------------------------------------------------


def test_a_tick_starts_at_most_its_budget(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    for i in range(5):
        _new(universe, f"t{i}")
    dispatcher.dispatch_all(universe.parent, starts=2)
    assert len(runs.started) == 2
    dispatcher.dispatch_all(universe.parent, starts=2)
    assert len(runs.started) == 4


def test_a_second_dispatcher_in_the_process_waits_its_turn(monkeypatch, universe):
    runs = _Runs(monkeypatch, universe)
    _new(universe)
    lock = dispatcher._universe_lock(universe.name)
    with lock:
        _dispatch(universe)
    assert runs.started == []
    _dispatch(universe)
    assert len(runs.started) == 1


def test_a_run_that_will_not_start_is_retried_with_backoff(monkeypatch, universe):
    _Runs(monkeypatch, universe)

    def refuse(*_a):
        raise acts.ActivityRefused("no executor", kind="run_refused")

    monkeypatch.setattr(runner, "start", refuse)
    aid = _new(universe)
    _dispatch(universe)
    record = acts.get(universe, aid)
    assert record["status"] == acts.SCHEDULED and record["start_failures"] == 1
    _dispatch(universe)
    assert acts.get(universe, aid)["start_failures"] == 1, "backing off, not retried at once"
