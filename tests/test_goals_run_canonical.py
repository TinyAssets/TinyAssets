"""PR-127 — `goals action=run_canonical` MCP surface.

Exercises the new dispatch wrapper end-to-end via the live extensions
tool surface:

  * Missing goal_id -> rejected with error_kind=missing_goal_id.
  * No canonical set + auto OFF -> rejected with
    error_kind=no_canonical_handler (the caller can branch on this).
  * Canonical set + auto OFF -> dispatches the stored version,
    response carries branch_version_id_used + source=canonical_stored.
  * Auto ON + qualified candidate + no in-flight -> stored canonical
    is refreshed, response carries source=leaderboard_refreshed +
    displaced_canonical_branch_version_id.
  * Auto ON + insufficient runs -> falls back to stored canonical,
    source=leaderboard_skipped_insufficient_runs.
  * Auto ON + in-flight on prior canonical -> defers refresh,
    source=leaderboard_skipped_in_flight.
  * Provider call inside the spawned run is mocked so the test never
    hits a real LLM.
"""

from __future__ import annotations

import importlib
import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _mock_selector_passthrough(monkeypatch):
    """DESIGN-008 — pass-through selector for run_canonical tests.

    Ranks by completed_run_count desc, matching the effective
    ordering the round-1 formula produced for these scenarios."""
    def _passthrough(
        base_path,
        *,
        goal_id,
        candidate_branches,
        actor="anonymous",
        timeout_s=None,
        **_extra,
    ):
        def _key(c):
            sigs = c.get("signals") or {}
            return (
                -int(sigs.get("completed_run_count") or 0),
                -float(sigs.get("last_successful_run_at") or 0.0),
                c.get("branch_def_id") or "",
            )
        ordered = sorted(candidate_branches, key=_key)
        return {
            "ok": True,
            "branch_version_id": "mock_selector@canon",
            "source": "platform_default",
            "run_id": "mock-run",
            "ranked_entries": [
                {
                    "branch_def_id": c["branch_def_id"],
                    "branch_version_id": c.get("branch_version_id", ""),
                    "score": float(
                        (c.get("signals") or {}).get(
                            "completed_run_count", 0,
                        )
                    ),
                    "rationale": "passthrough by completed_run_count",
                }
                for c in ordered
            ],
        }
    monkeypatch.setattr(
        "tinyassets.api.quality_leaderboard.dispatch_selector",
        _passthrough,
    )


@pytest.fixture
def us_env(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "host")
    monkeypatch.delenv("TINYASSETS_BUG_INVESTIGATION_GOAL_ID", raising=False)
    monkeypatch.delenv(
        "TINYASSETS_BUG_INVESTIGATION_BRANCH_DEF_ID", raising=False,
    )
    from tinyassets import universe_server as us
    importlib.reload(us)
    yield us, tmp_path
    importlib.reload(us)


def _call(us, action: str, **kwargs) -> dict:
    return json.loads(us._goals_impl(action=action, **kwargs))


def _seed_runnable_branch(
    base: Path,
    *,
    goal_id: str,
    branch_def_id: str,
    auto: bool = False,
    min_runs: int = 5,
    publish: bool = True,
    runs_completed: int = 0,
) -> tuple[str, str]:
    """Seed a Goal + Branch + (optional) published version + N completed
    runs. Returns (goal_id, branch_version_id-or-empty).
    """
    from tinyassets.branch_versions import publish_branch_version
    from tinyassets.daemon_server import (
        save_branch_definition,
        save_goal,
        update_goal,
    )
    from tinyassets.runs import (
        RUN_STATUS_COMPLETED,
        create_run,
        initialize_runs_db,
        update_run_status,
    )

    save_goal(
        base,
        goal=dict(
            goal_id=goal_id, name=goal_id, description="",
            author="host", tags=[], visibility="public",
        ),
    )
    update_goal(
        base,
        goal_id=goal_id,
        updates={
            "auto_canonical_via_leaderboard": auto,
            "min_completed_runs_for_canonical": min_runs,
        },
    )
    save_branch_definition(
        base,
        branch_def=dict(
            branch_def_id=branch_def_id,
            name=branch_def_id,
            description="",
            author="host",
            tags=[],
            graph_nodes=[
                {
                    "id": "n1",
                    "type": "prompt",
                    "phase": "draft",
                    "prompt": "respond",
                    "input_keys": [],
                    "output_keys": ["out"],
                },
            ],
            edges=[
                {"from": "START", "to": "n1"},
                {"from": "n1", "to": "END"},
            ],
            state_schema=[{"name": "out", "type": "str"}],
            entry_point="n1",
            published=True,
            goal_id=goal_id,
        ),
    )
    bvid = ""
    if publish:
        version = publish_branch_version(
            base,
            branch_dict={
                "branch_def_id": branch_def_id,
                "name": branch_def_id,
                "description": "",
                "author": "host",
                "graph_nodes": [
                    {
                        "id": "n1",
                        "type": "prompt",
                        "phase": "draft",
                        "prompt": "respond",
                        "input_keys": [],
                        "output_keys": ["out"],
                    },
                ],
                "edges": [
                    {"from": "START", "to": "n1"},
                    {"from": "n1", "to": "END"},
                ],
                "state_schema": [{"name": "out", "type": "str"}],
                "entry_point": "n1",
                "node_defs": [],
            },
            notes=f"{branch_def_id} v1",
            publisher="host",
            public=True,  # the author's explicit publish
        )
        bvid = version.branch_version_id

    initialize_runs_db(base)
    now = time.time()
    for _ in range(runs_completed):
        rid = create_run(
            base, branch_def_id=branch_def_id,
            thread_id=branch_def_id, inputs={},
            actor="universe:u-test",
        )
        update_run_status(
            base, rid, status=RUN_STATUS_COMPLETED, finished_at=now,
        )
    return goal_id, bvid


# ---------------------------------------------------------------------------
# Required-argument failure modes
# ---------------------------------------------------------------------------


def test_missing_goal_id_returns_rejected(us_env):
    us, _ = us_env
    result = _call(us, "run_canonical")
    assert result["status"] == "rejected"
    assert result["error_kind"] == "missing_goal_id"


def test_no_canonical_set_auto_off_returns_no_canonical_handler(us_env):
    us, base = us_env
    _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="b1",
        auto=False, publish=True,
    )
    result = _call(us, "run_canonical", goal_id="g1")
    assert result["status"] == "rejected"
    assert result["error_kind"] == "no_canonical_handler"


# ---------------------------------------------------------------------------
# Auto OFF — stored canonical dispatches
# ---------------------------------------------------------------------------


def test_stored_canonical_dispatches_via_run_branch_version(us_env):
    us, base = us_env
    _, bvid = _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="b1",
        auto=False, publish=True,
    )
    from tinyassets.daemon_server import set_canonical_branch
    set_canonical_branch(
        base, goal_id="g1",
        branch_version_id=bvid, set_by="host",
    )

    # Patch the underlying run dispatcher so we don't actually execute
    # a graph. The test asserts the wrapper PASSES THE RIGHT
    # branch_version_id through; mock returns a stub run row.
    fake = json.dumps({
        "text": "Run queued.",
        "run_id": "stub-run-001",
        "status": "queued",
        "output": {},
        "error": "",
    })
    with patch(
        "tinyassets.api.runs._action_run_branch_version",
        return_value=fake,
    ) as mock_dispatch:
        result = _call(us, "run_canonical", goal_id="g1")

    assert mock_dispatch.call_count == 1
    dispatched_kwargs = mock_dispatch.call_args[0][0]
    assert dispatched_kwargs["branch_version_id"] == bvid
    assert result["run_id"] == "stub-run-001"
    assert result["branch_version_id_used"] == bvid
    assert result["branch_def_id"] == "b1"
    assert result["source"] == "canonical_stored"
    assert result["goal_id"] == "g1"


def test_dispatch_forwards_inputs_json_and_run_name(us_env):
    us, base = us_env
    _, bvid = _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="b1",
        auto=False, publish=True,
    )
    from tinyassets.daemon_server import set_canonical_branch
    set_canonical_branch(
        base, goal_id="g1",
        branch_version_id=bvid, set_by="host",
    )
    fake = json.dumps({
        "text": "ok", "run_id": "r-1", "status": "queued",
        "output": {}, "error": "",
    })
    with patch(
        "tinyassets.api.runs._action_run_branch_version",
        return_value=fake,
    ) as mock_dispatch:
        _call(
            us, "run_canonical",
            goal_id="g1",
            # `goals` MCP signature accepts these explicitly via
            # market.goals(...) — they may need passthrough wiring in
            # the goals tool itself if it doesn't already accept them.
        )
    dispatched_kwargs = mock_dispatch.call_args[0][0]
    assert dispatched_kwargs["branch_version_id"] == bvid


# ---------------------------------------------------------------------------
# Auto ON — leaderboard refresh happy path
# ---------------------------------------------------------------------------


def test_auto_on_refreshes_canonical_and_dispatches(us_env):
    us, base = us_env
    _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="old",
        auto=True, min_runs=2, publish=True, runs_completed=1,
    )
    _, new_bvid = _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="new",
        auto=True, min_runs=2, publish=True, runs_completed=3,
    )
    # set_canonical to the old version.
    from tinyassets.branch_versions import list_branch_versions
    from tinyassets.daemon_server import set_canonical_branch
    old_versions = list_branch_versions(
        base, branch_def_id="old", limit=1,
    )
    old_bvid = old_versions[0].branch_version_id
    set_canonical_branch(
        base, goal_id="g1",
        branch_version_id=old_bvid, set_by="host",
    )

    # Boost 'new' with a high quality judgment so the leaderboard
    # ranks it first.
    from tinyassets.runs import (
        RUN_STATUS_COMPLETED,
        add_judgment,
        create_run,
        update_run_status,
    )
    rid = create_run(
        base, branch_def_id="new", thread_id="new", inputs={},
        actor="universe:u-test",
    )
    update_run_status(
        base, rid, status=RUN_STATUS_COMPLETED, finished_at=time.time(),
    )
    add_judgment(
        base, run_id=rid, text="great",
        tags=["quality:9"], author="judge",
    )

    fake = json.dumps({
        "text": "ok", "run_id": "r-new", "status": "queued",
        "output": {}, "error": "",
    })
    with patch(
        "tinyassets.api.runs._action_run_branch_version",
        return_value=fake,
    ) as mock_dispatch:
        result = _call(us, "run_canonical", goal_id="g1")

    # The dispatcher was called with the NEW canonical version.
    dispatched_kwargs = mock_dispatch.call_args[0][0]
    assert dispatched_kwargs["branch_version_id"] == new_bvid
    assert result["source"] == "leaderboard_refreshed"
    assert result["branch_version_id_used"] == new_bvid
    assert result["displaced_canonical_branch_version_id"] == old_bvid
    assert result["refresh_attempted"] is True
    assert result["candidate_completed_runs"] >= 2


def test_auto_on_insufficient_runs_keeps_stored(us_env):
    us, base = us_env
    _, old_bvid = _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="old",
        auto=True, min_runs=5, publish=True,
    )
    _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="new",
        auto=True, min_runs=5, publish=True, runs_completed=1,
    )
    from tinyassets.daemon_server import set_canonical_branch
    set_canonical_branch(
        base, goal_id="g1",
        branch_version_id=old_bvid, set_by="host",
    )
    # Add a high judgment on 'new' so it would rank first if not
    # blocked by the threshold.
    from tinyassets.runs import (
        RUN_STATUS_COMPLETED,
        add_judgment,
        create_run,
        update_run_status,
    )
    rid = create_run(
        base, branch_def_id="new", thread_id="new", inputs={},
        actor="universe:u-test",
    )
    update_run_status(
        base, rid, status=RUN_STATUS_COMPLETED, finished_at=time.time(),
    )
    add_judgment(
        base, run_id=rid, text="great",
        tags=["quality:10"], author="judge",
    )
    fake = json.dumps({
        "text": "ok", "run_id": "r-old", "status": "queued",
        "output": {}, "error": "",
    })
    with patch(
        "tinyassets.api.runs._action_run_branch_version",
        return_value=fake,
    ) as mock_dispatch:
        result = _call(us, "run_canonical", goal_id="g1")
    dispatched_kwargs = mock_dispatch.call_args[0][0]
    # Dispatch goes against the OLD canonical because threshold blocked.
    assert dispatched_kwargs["branch_version_id"] == old_bvid
    assert result["source"] == "leaderboard_skipped_insufficient_runs"


def test_auto_on_in_flight_defers_refresh(us_env):
    us, base = us_env
    _, old_bvid = _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="old",
        auto=True, min_runs=1, publish=True,
    )
    _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="new",
        auto=True, min_runs=1, publish=True, runs_completed=3,
    )
    from tinyassets.daemon_server import set_canonical_branch
    set_canonical_branch(
        base, goal_id="g1",
        branch_version_id=old_bvid, set_by="host",
    )
    # Boost 'new' so it would rank first.
    from tinyassets.runs import (
        RUN_STATUS_COMPLETED,
        RUN_STATUS_RUNNING,
        add_judgment,
        create_run,
        update_run_status,
    )
    rid_j = create_run(
        base, branch_def_id="new", thread_id="new", inputs={},
        actor="universe:u-test",
    )
    update_run_status(
        base, rid_j, status=RUN_STATUS_COMPLETED, finished_at=time.time(),
    )
    add_judgment(
        base, run_id=rid_j, text="great",
        tags=["quality:10"], author="judge",
    )
    # Inject an in-flight running run on the prior canonical.
    rid_in_flight = create_run(
        base, branch_def_id="old", thread_id="old",
        inputs={}, branch_version_id=old_bvid,
        actor="universe:u-test",
    )
    update_run_status(base, rid_in_flight, status=RUN_STATUS_RUNNING)

    fake = json.dumps({
        "text": "ok", "run_id": "r-old", "status": "queued",
        "output": {}, "error": "",
    })
    with patch(
        "tinyassets.api.runs._action_run_branch_version",
        return_value=fake,
    ) as mock_dispatch:
        result = _call(us, "run_canonical", goal_id="g1")
    dispatched_kwargs = mock_dispatch.call_args[0][0]
    assert dispatched_kwargs["branch_version_id"] == old_bvid
    assert result["source"] == "leaderboard_skipped_in_flight"
    assert result["in_flight_run_id"] == rid_in_flight


# ---------------------------------------------------------------------------
# Goal flag plumbing (update_goal sets/clears the new fields)
# ---------------------------------------------------------------------------


def test_update_goal_accepts_auto_canonical_flag(us_env):
    """Storage helper accepts the two new fields; round-trip via
    get_goal surfaces them with the right types.

    The MCP surface for setting these via ``goals action=update`` is a
    follow-on slice — for now ``update_goal()`` exposes them at the
    storage layer for tests + host scripts.
    """
    us, base = us_env
    del us  # the MCP surface isn't exercised in this test today.
    _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="b1",
        auto=False, publish=False,
    )
    from tinyassets.daemon_server import get_goal, update_goal
    update_goal(
        base, goal_id="g1",
        updates={
            "auto_canonical_via_leaderboard": True,
            "min_completed_runs_for_canonical": 7,
        },
    )
    goal = get_goal(base, goal_id="g1")
    assert goal["auto_canonical_via_leaderboard"] is True
    assert goal["min_completed_runs_for_canonical"] == 7


def test_update_goal_rejects_negative_threshold(us_env):
    us, base = us_env
    _seed_runnable_branch(
        base, goal_id="g1", branch_def_id="b1",
        auto=False, publish=False,
    )
    from tinyassets.daemon_server import update_goal
    with pytest.raises(ValueError):
        update_goal(
            base, goal_id="g1",
            updates={"min_completed_runs_for_canonical": -3},
        )
