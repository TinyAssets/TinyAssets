"""The engine route refuses an activity's tools once it stops running.

Every provider's model-visible tools execute on this route, so this is the
one pre-tool boundary an activity's yield, pause and stop rely on.
"""

import asyncio

import pytest
from fastmcp.exceptions import ToolError

from tinyassets import activity_fence, agent_activities, engine_steering
from tinyassets.activity_fence import STOPPED, ActivityFence, activity_refusal


def _running(universe):
    universe.mkdir(exist_ok=True)
    record = agent_activities.create(
        universe, owner_principal="acct_alice", title="t", brief="b", origin_kind="ask",
    )
    aid = record["activity_id"]
    generation = agent_activities.claim(universe, aid, replaceable=lambda _: False)
    assert agent_activities.bind_run(universe, aid, generation, "run-1")
    return aid, generation


def test_a_running_activity_keeps_its_tools(tmp_path):
    aid, _ = _running(tmp_path / "u")
    assert activity_refusal(tmp_path / "u", f"activity:{aid}") is None


def test_a_yielded_activity_loses_its_tools(tmp_path):
    aid, _ = _running(tmp_path / "u")
    assert agent_activities.wait_on(tmp_path / "u", aid, "req-1", "asked")
    assert activity_refusal(tmp_path / "u", f"activity:{aid}") == STOPPED


@pytest.mark.parametrize("status", [agent_activities.PAUSED, agent_activities.COMPLETED])
def test_a_paused_or_stopped_activity_loses_its_tools(tmp_path, status):
    aid, generation = _running(tmp_path / "u")
    agent_activities.transition(tmp_path / "u", aid, status, generation=generation)
    assert activity_refusal(tmp_path / "u", f"activity:{aid}") == STOPPED


def test_an_unknown_activity_session_is_refused(tmp_path):
    (tmp_path / "u").mkdir()
    assert activity_refusal(tmp_path / "u", "activity:act_missing") == STOPPED


def test_other_sessions_are_not_fenced(tmp_path):
    (tmp_path / "u").mkdir()
    assert activity_refusal(tmp_path / "u", "chat:main") is None
    assert activity_refusal(tmp_path / "u", "") is None


def _call(fence, monkeypatch, session_key):
    monkeypatch.setattr(engine_steering, "_route_params", lambda: (session_key, "turn-1"))
    reached = []

    async def call_next(context):
        reached.append(context)
        return "ran"

    result = asyncio.run(fence.on_call_tool("ctx", call_next))
    return result, reached


def test_the_route_middleware_stops_the_call_before_its_handler(tmp_path, monkeypatch):
    aid, _ = _running(tmp_path / "u")
    fence = ActivityFence(lambda: tmp_path / "u")
    assert _call(fence, monkeypatch, f"activity:{aid}") == ("ran", ["ctx"])
    agent_activities.wait_on(tmp_path / "u", aid, "req-1", "asked")
    with pytest.raises(ToolError, match="no longer running"):
        _call(fence, monkeypatch, f"activity:{aid}")


def test_the_engine_route_installs_the_fence_outside_every_handler():
    from tinyassets import engine_mcp_server

    kinds = [type(m) for m in engine_mcp_server.mcp.middleware]
    assert activity_fence.ActivityFence in kinds
    # Outside the steering/activity/bounding layers, so nothing runs first.
    assert kinds.index(ActivityFence) < kinds.index(engine_steering.OwnerSteering)
