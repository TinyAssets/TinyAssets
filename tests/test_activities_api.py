"""Harness D2a: the one activity contract, through the agent's served tools.

The owner and universe come from the engine's verified pin, never the payload;
reads are complete by cursor; stop keeps the result; an activity cannot start
another (design D8).
"""
from __future__ import annotations

import json

import pytest

from tests.engine_authority_helpers import mock_engine_admission
from tinyassets import activity_dispatcher, activity_runner
from tinyassets import agent_activities as acts
from tinyassets import engine_mcp_server as engine
from tinyassets.api import activities as api


@pytest.fixture
def pinned(tmp_path, monkeypatch):
    universe = tmp_path / "u-alpha"
    universe.mkdir()
    woken, stopped = [], []
    monkeypatch.setattr(engine, "_ACTOR_ID", "acct_alice")
    monkeypatch.setattr(engine, "_GRAPH_ID", "u-alpha")
    mock_engine_admission(monkeypatch, {"u-alpha"})
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
    monkeypatch.setattr(activity_dispatcher, "dispatch_universe",
                        lambda base, uid: woken.append(uid))
    monkeypatch.setattr(activity_runner, "stop", lambda base, run_id: stopped.append(run_id))
    monkeypatch.setattr(engine, "_calling_session", lambda: "thread:principal:acct_alice")
    return universe, woken, stopped


def _write(operation, **payload):
    return json.loads(engine.write_graph(target="activity", operation=operation,
                                         payload_json=json.dumps(payload)))


def _read(target, **kwargs):
    return json.loads(engine.read_graph(target=target, **kwargs))


def test_start_records_the_pinned_owner_and_wakes_the_dispatcher(pinned):
    universe, woken, _ = pinned
    started = _write("start", title="Monday report", brief="Summarise last week",
                     owner_principal="acct_mallory")
    record = acts.get(universe, started["activity_id"])
    assert record["owner_principal"] == "acct_alice", "the payload never names the owner"
    assert started["status"] == acts.SCHEDULED and woken == ["u-alpha"]


def test_reads_are_complete_and_hide_runner_bookkeeping(pinned):
    for i in range(acts.PAGE + 3):
        _write("start", title=f"task {i}", brief="b")
    first = _read("activities")
    assert len(first["activities"]) == acts.PAGE and first["next_cursor"]
    second = _read("activities", query=first["next_cursor"])
    assert len(second["activities"]) == 3 and second["next_cursor"] is None
    row = first["activities"][0]
    assert "runner_token" not in row and "owner_principal" not in row
    one = _read("activity", query=row["activity_id"])
    assert one["activity"]["activity_id"] == row["activity_id"]
    assert [e["kind"] for e in one["events"]] == ["created"]


def test_stop_keeps_the_result_and_cancels_a_running_run(pinned):
    universe, _, stopped = pinned
    aid = _write("start", title="t", brief="b")["activity_id"]
    generation = acts.claim(universe, aid, replaceable=lambda r: False)
    acts.bind_run(universe, aid, generation, "run-1")
    acts.note_progress(universe, aid, generation, result_summary="half done")
    assert _write("stop", activity_id=aid)["status"] == acts.COMPLETED
    record = acts.get(universe, aid)
    assert record["outcome"] == "stopped" and record["result_summary"] == "half done"
    assert stopped == ["run-1"], "the run retired by this very transition is cancelled"


def test_pause_then_resume_queues_it_again(pinned):
    universe, woken, _ = pinned
    aid = _write("start", title="t", brief="b")["activity_id"]
    assert _write("pause", activity_id=aid)["status"] == acts.PAUSED
    assert _write("resume", activity_id=aid)["status"] == acts.SCHEDULED
    assert woken == ["u-alpha", "u-alpha"]


def test_refusals_are_named(pinned, monkeypatch):
    assert _write("dance")["error"] == "unknown_activity_operation"
    assert _write("stop", activity_id="act_0000000000000000")["error"] == "not_found"
    assert _write("start", title="", brief="b")["error"] == "activity_refused"
    monkeypatch.setattr(engine, "_calling_session", lambda: "activity:act_1234567890abcdef")
    assert _write("start", title="t", brief="b")["error"] == "nested_activity_unavailable"


def test_a_stale_revision_is_refused(pinned):
    aid = _write("start", title="t", brief="b")["activity_id"]
    revision = _read("activity", query=aid)["activity"]["revision"]
    _write("pause", activity_id=aid, expected_revision=revision)
    assert _write("resume", activity_id=aid,
                  expected_revision=revision)["error"] == "revision_conflict"


def test_the_contract_never_reaches_another_universe(tmp_path):
    with pytest.raises(acts.ActivityRefused):
        api.read(tmp_path, universe_id="../u-beta")


def test_an_owner_request_inside_an_activity_is_its_yield(pinned, monkeypatch):
    universe, _, _ = pinned
    aid = _write("start", title="t", brief="b")["activity_id"]
    generation = acts.claim(universe, aid, replaceable=lambda r: False)
    acts.bind_run(universe, aid, generation, "run-1")
    import tinyassets.api.pending_requests as pending

    monkeypatch.setattr(pending, "request_from_user",
                        lambda **kw: {"request_id": "req-1", "title": "Send the invoice?"})
    monkeypatch.setattr(engine, "_calling_session", lambda: f"activity:{aid}")
    out = json.loads(engine.write_graph(target="pending_request", operation="ask",
                                        payload_json="{}"))
    assert out["activity_waiting"] is True and "resumes when they answer" in out["hint"]
    record = acts.get(universe, aid)
    assert record["status"] == acts.WAITING_ON_YOU and record["waiting_request_id"] == "req-1"
    # Outside an activity nothing changes.
    monkeypatch.setattr(engine, "_calling_session", lambda: "thread:principal:acct_alice")
    plain = json.loads(engine.write_graph(target="pending_request", operation="ask",
                                          payload_json="{}"))
    assert "activity_waiting" not in plain


def test_answering_the_request_requeues_the_activity(tmp_path, monkeypatch):
    from tinyassets import activity_dispatcher
    from tinyassets.storage import pending_requests as pr

    universe = tmp_path / "u-alpha"
    universe.mkdir()
    ticks = []
    monkeypatch.setattr(activity_dispatcher, "tick_in_background", lambda base: ticks.append(base))
    aid = acts.create(universe, owner_principal="acct_alice", title="t", brief="b",
                      origin_kind="ask")["activity_id"]
    generation = acts.claim(universe, aid, replaceable=lambda r: False)
    acts.bind_run(universe, aid, generation, "run-1")
    row = pr.create_request(universe, kind="API", title="ok?", body="b", fields=[],
                            action={"type": "answer"}, dedupe_key="k")
    acts.wait_on(universe, aid, row["request_id"])
    assert pr.resolve_request(universe, row["request_id"], status="answered", answer={})
    assert acts.get(universe, aid)["status"] == acts.SCHEDULED
    assert ticks == [tmp_path]
