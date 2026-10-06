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


# --- what a single pre-call check leaves open (cross-family review 2026-10-06) --


def _fence_call(fence, session_key, body):
    """One top-level call through the fence; ``body`` stands in for the handler."""
    async def call_next(context):
        return await body()

    return fence.on_call_tool("ctx", call_next)


def test_a_call_queued_behind_the_one_that_yields_meets_the_fence_after_it(
        tmp_path, monkeypatch):
    aid, _ = _running(tmp_path / "u")
    session = f"activity:{aid}"
    monkeypatch.setattr(engine_steering, "_route_params", lambda: (session, "turn-1"))
    fence = ActivityFence(lambda: tmp_path / "u")
    ran = []

    async def ask_and_yield():
        await asyncio.sleep(0.1)          # the second call is already waiting
        agent_activities.wait_on(tmp_path / "u", aid, "req-1", "asked")
        ran.append("ask")
        return "asked"

    async def act():
        ran.append("act")
        return "acted"

    async def both():
        return await asyncio.gather(_fence_call(fence, session, ask_and_yield),
                                    _fence_call(fence, session, act),
                                    return_exceptions=True)

    first, second = asyncio.run(both())
    assert first == "asked"
    assert isinstance(second, ToolError) and "no longer running" in str(second)
    assert ran == ["ask"], "the queued call was admitted before the yield"


def test_a_ta_call_nested_in_an_activity_call_is_checked_but_not_queued(
        tmp_path, monkeypatch):
    """``ta`` platform calls re-enter the route from inside the running bash
    (a worker thread, then back onto the loop): they must not wait on the very
    call that contains them, and they still meet the fence."""
    aid, _ = _running(tmp_path / "u")
    session = f"activity:{aid}"
    monkeypatch.setattr(engine_steering, "_route_params", lambda: (session, "turn-1"))
    fence = ActivityFence(lambda: tmp_path / "u")

    async def bash_running_ta():
        loop = asyncio.get_running_loop()

        def in_the_jail_bridge(stop_first):
            if stop_first:
                agent_activities.wait_on(tmp_path / "u", aid, "req-1", "asked")
            future = asyncio.run_coroutine_threadsafe(
                _fence_call(fence, session, lambda: asyncio.sleep(0, "nested ran")), loop)
            try:
                return future.result(timeout=5)
            except ToolError as refused:
                return str(refused)

        first = await asyncio.to_thread(in_the_jail_bridge, False)
        second = await asyncio.to_thread(in_the_jail_bridge, True)
        return first, second

    first, second = asyncio.run(_fence_call(fence, session, bash_running_ta))
    assert first == "nested ran"
    assert "no longer running" in second


def test_stop_check_watches_only_an_activity_session(tmp_path):
    aid, _ = _running(tmp_path / "u")
    assert activity_fence.stop_check(tmp_path / "u", "chat:main") is None
    check = activity_fence.stop_check(tmp_path / "u", f"activity:{aid}")
    assert check() is None
    agent_activities.wait_on(tmp_path / "u", aid, "req-1", "asked")
    assert check() == STOPPED


def test_every_ta_request_is_refused_once_the_activity_stops(tmp_path):
    """Connection calls dispatch straight to the effector, never back through
    the route: the bridge checks the activity on every request itself."""
    from tinyassets.ta_capabilities import Capabilities, ExecutionContext

    root = tmp_path / "u"
    aid, _ = _running(root)
    reached = []

    async def platform(name, arguments):
        reached.append(name)
        return {"ok": True}

    service = Capabilities(root, ExecutionContext(root.name, "acct_alice", "main"),
                           [{"name": "read_graph", "description": "", "arguments": {}}],
                           platform, lambda: None,
                           stopped=activity_fence.stop_check(root, f"activity:{aid}"))
    running = asyncio.run(service.dispatch(
        {"op": "call", "name": "read_graph", "arguments": {}}))
    agent_activities.wait_on(root, aid, "req-1", "asked")
    connection = asyncio.run(service.dispatch({
        "op": "call", "name": "connection:conn-http:POST",
        "arguments": {"request": {"path": "/v1/messages"}}}))
    catalog = asyncio.run(service.dispatch({"op": "catalog"}))
    assert running == {"result": {"ok": True}} and reached == ["read_graph"]
    assert connection == {"error": STOPPED}
    assert catalog == {"error": STOPPED}


def test_a_running_command_is_killed_once_its_activity_stops(monkeypatch):
    """The rest of a bash command that yielded part-way does not keep acting:
    the jail supervisor polls the activity and kills the command."""
    import subprocess
    import sys
    import threading
    import time
    from types import SimpleNamespace

    from tinyassets import universe_tools

    monkeypatch.setattr(universe_tools, "_tree", lambda pid: (1, 0))
    monkeypatch.setattr(universe_tools, "_kill", lambda proc: proc.kill())
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    stopped_at = time.monotonic() + 0.3
    started = time.monotonic()
    try:
        killed = universe_tools._watch(
            proc, SimpleNamespace(over=threading.Event()), SimpleNamespace(breach=lambda: None),
            limits=universe_tools.DEFAULT_LIMITS, wall=30.0, process_cap=64,
            started=started,
            stop=lambda: STOPPED if time.monotonic() >= stopped_at else None)
    finally:
        proc.kill()
        proc.wait(timeout=5)
    assert killed == "activity_stopped"
    assert time.monotonic() - started < 5
    run = universe_tools.ToolRun(exit_code=-9, output=b"", killed=killed, elapsed=0.4)
    assert "activity stopped running" in universe_tools._trailer(
        run, universe_tools.DEFAULT_LIMITS, 30.0)
