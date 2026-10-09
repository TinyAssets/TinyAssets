"""Tests for the v1 terminal-on-restart contract surfaced by get_run.

STATUS.md Approved-bugs 2026-04-22 — in-flight run recovery surface.
``recover_in_flight_runs`` already marks restart-interrupted runs as
``interrupted`` with a descriptive error. The MCP ``get_run`` tool now
exposes this explicitly so clients don't busy-wait an interrupted run
expecting it to flip back.

Invariants:
- get_run on INTERRUPTED returns ``resumable=False`` + a
  ``resumable_reason`` string.
- The recovery error message (``"The process running this run stopped
  before it finished."``) is preserved in the ``error`` field.
- Non-interrupted runs don't carry ``resumable`` (absence = not
  applicable; presence with False = "this is terminal, rerun").
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest


@pytest.fixture
def run_env(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")

    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, Path(tmp_path)
    importlib.reload(us)


def _call(us, action, **kwargs):
    return json.loads(us._extensions_impl(action=action, **kwargs))


def _create_running_run(tmp_path: Path, us, branch_def_id: str = "b1") -> str:
    # Bring the daemon_server branch_definitions table into existence so
    # ``_compose_run_snapshot``'s ``get_branch_definition`` call hits a
    # real table (it handles missing-row KeyError but OperationalError
    # for a missing table would bubble).
    from tinyassets.daemon_server import initialize_author_server

    initialize_author_server(tmp_path)
    from tinyassets.runs import (
        RUN_STATUS_RUNNING,
        create_run,
        initialize_runs_db,
        update_run_status,
    )

    initialize_runs_db(tmp_path)
    rid = create_run(
        tmp_path, branch_def_id=branch_def_id, thread_id="",
        inputs={}, actor="tester",
    )
    update_run_status(tmp_path, rid, status=RUN_STATUS_RUNNING)
    return rid


def test_interrupted_run_get_run_surfaces_resumable_false(run_env):
    us, base = run_env
    rid = _create_running_run(base, us)

    # Simulate daemon restart recovery: the owning process died.
    from tests.run_owner_helpers import mark_owner_dead
    from tinyassets.runs import recover_in_flight_runs
    mark_owner_dead(base, rid)
    assert recover_in_flight_runs(base) == 1

    got = _call(us, "get_run", run_id=rid)
    assert got["status"] == "interrupted"
    assert got["resumable"] is False
    assert got["resumable_reason"] == "v1 terminal-on-restart"
    assert "stopped before it finished" in got["error"]


def test_non_interrupted_run_does_not_carry_resumable_field(run_env):
    us, base = run_env
    from tinyassets.daemon_server import initialize_author_server
    from tinyassets.runs import (
        RUN_STATUS_COMPLETED,
        create_run,
        initialize_runs_db,
        update_run_status,
    )

    initialize_author_server(base)
    initialize_runs_db(base)
    rid = create_run(
        base, branch_def_id="b1", thread_id="",
        inputs={}, actor="tester",
    )
    update_run_status(base, rid, status=RUN_STATUS_COMPLETED)

    got = _call(us, "get_run", run_id=rid)
    assert got["status"] == "completed"
    assert "resumable" not in got
    assert "resumable_reason" not in got


def test_interrupted_surface_across_multiple_runs(run_env):
    """Recovery + get_run is consistent across many interrupted rows."""
    us, base = run_env
    rids = [_create_running_run(base, us, f"b{i}") for i in range(3)]

    from tests.run_owner_helpers import mark_owner_dead
    from tinyassets.runs import recover_in_flight_runs
    mark_owner_dead(base, *rids)
    assert recover_in_flight_runs(base) == 3

    for rid in rids:
        got = _call(us, "get_run", run_id=rid)
        assert got["status"] == "interrupted"
        assert got["resumable"] is False
        assert got["resumable_reason"] == "v1 terminal-on-restart"
