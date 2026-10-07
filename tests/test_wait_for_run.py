"""#65 — wait_for_run long-poll action.

Problem: bot was calling stream_run / get_run 10+ times per polling
window, burning Claude.ai's per-turn tool budget. A 5-min run became
10-15 polls just to watch progress.

Fix: ``extensions action=wait_for_run(run_id, since_step=N,
max_wait_s=60)`` — long-polls for up to ``max_wait_s`` or until new
events land. One call covers ~60s of run wall time.

Covers the two main cost cases:
1. Run finishes during the wait → returns terminal status immediately.
2. New events land during the wait → returns events and a next cursor.
3. No events land within max_wait_s → returns "still running", caller
   polls again.
"""

from __future__ import annotations

import importlib
import threading
import time

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.runs import (
    NODE_STATUS_RAN,
    RunStepEvent,
    await_run_events,
    initialize_runs_db,
    record_event,
    update_run_status,
)

#: Runs are universe-owned: without one `run_branch` returns
#: `branch_run_requires_universe`; registered-but-ungranted returns
#: `universe_access_denied`. The ACL grant is the easy half to miss.
_UNIVERSE = "universe_wait_for_run"

pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def us_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    # Branch WRITE actions resolve the caller via `_request_branch_actor()`,
    # which is credential-derived and ignores `UNIVERSE_SERVER_USER`.
    # `run_branch` is gated on `tinyassets.extensions.costly`, which the
    # default test credential deliberately EXCLUDES so refusal suites keep
    # asserting something. This file asserts run completion, never a
    # refusal, so it opts in.
    authenticate_request(
        "tester",
        capabilities=[
            "tinyassets.extensions.read",
            "tinyassets.extensions.write",
            "tinyassets.extensions.admin",
            "tinyassets.extensions.costly",
        ],
    )
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
    )

    udir = base / _UNIVERSE
    udir.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(base, universe_id=_UNIVERSE, universe_path=udir)
    grant_universe_access(
        base,
        universe_id=_UNIVERSE,
        actor_id="tester",
        permission="write",
        granted_by="us_env",
    )

    from tinyassets import universe_server as us
    provider_calls = importlib.import_module("tinyassets.providers.call")

    importlib.reload(us)
    monkeypatch.setattr(
        provider_calls,
        "call_provider",
        lambda prompt, _system="", **_kwargs: f"fixture:{prompt}",
    )
    yield us, base
    importlib.reload(us)


# ─── unit: await_run_events ──────────────────────────────────────────────


def test_await_returns_events_immediately_when_present(tmp_path):
    base = tmp_path / "output"
    base.mkdir()
    initialize_runs_db(base)
    record_event(base, RunStepEvent(
        run_id="r1", step_index=0, node_id="n",
        status=NODE_STATUS_RAN,
        started_at="2026-04-13T00:00:00Z",
        finished_at="2026-04-13T00:00:01Z",
    ))
    start = time.monotonic()
    result = await_run_events(base, "r1", since_step=-1, max_wait_s=5.0)
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, "should return immediately if events exist"
    assert result["reason"] == "events"
    assert len(result["events"]) == 1
    assert result["next_cursor"] == 0


def test_await_returns_on_terminal_status(tmp_path):
    base = tmp_path / "output"
    base.mkdir()
    initialize_runs_db(base)
    # Insert a run row with terminal status.
    from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
    from tinyassets.runs import _prepare_run
    b = BranchDefinition(name="t", entry_point="only")
    b.node_defs = [NodeDefinition(node_id="only", display_name="Only",
                                   prompt_template="x",
                                   output_keys=["only_out"])]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [EdgeDefinition(from_node="START", to_node="only"),
               EdgeDefinition(from_node="only", to_node="END")]
    b.state_schema = [{"name": "x", "type": "str", "default": ""}]
    rid = _prepare_run(
        base, branch=b, inputs={}, run_name="t", actor="tester",
    )
    update_run_status(
        base, rid,
        status="completed",
        output={"only_out": "done"},
        finished_at="2026-04-13T00:00:01Z",
    )
    start = time.monotonic()
    # No events beyond the pending priors, but the run is terminal —
    # should return fast, not wait max_wait_s.
    result = await_run_events(
        base, rid, since_step=1_000_000, max_wait_s=5.0,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 1.0, "should bail fast on terminal status"
    assert result["reason"] == "terminal"
    assert result["status"] == "completed"


def test_await_returns_on_deadline_when_idle(tmp_path):
    base = tmp_path / "output"
    base.mkdir()
    initialize_runs_db(base)
    # No run, no events. await_run_events should hit the deadline.
    start = time.monotonic()
    result = await_run_events(
        base, "nonexistent-run",
        since_step=-1, max_wait_s=0.3, poll_interval_s=0.05,
    )
    elapsed = time.monotonic() - start
    # Allow some jitter.
    assert 0.2 < elapsed < 1.5
    assert result["reason"] == "timeout"
    assert result["events"] == []


def test_await_wakes_when_event_lands_mid_wait(tmp_path):
    base = tmp_path / "output"
    base.mkdir()
    initialize_runs_db(base)

    def _late_event():
        time.sleep(0.2)
        record_event(base, RunStepEvent(
            run_id="r2", step_index=5, node_id="late",
            status=NODE_STATUS_RAN,
            started_at="2026-04-13T00:00:00Z",
            finished_at="2026-04-13T00:00:01Z",
        ))

    threading.Thread(target=_late_event, daemon=True).start()
    start = time.monotonic()
    result = await_run_events(
        base, "r2", since_step=-1, max_wait_s=3.0,
        poll_interval_s=0.05,
    )
    elapsed = time.monotonic() - start
    assert elapsed < 1.5, "should wake shortly after event lands"
    assert result["reason"] == "events"
    assert len(result["events"]) == 1
    assert result["events"][0]["node_id"] == "late"


# ─── integration: extensions action=wait_for_run ─────────────────────────
