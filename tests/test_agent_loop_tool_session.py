"""The thin loop's tool router: one place per tool name, and the owner's steering."""

from __future__ import annotations

import asyncio

import pytest

from tests.agent_loop_fakes import FakeBox
from tinyassets import agent_steering
from tinyassets.agent_loop.box_tools import BoxExecutor, BoxTools
from tinyassets.agent_loop.tool_session import open_loop_tools
from tinyassets.engine_tool_client import EngineToolError

THREAD = "thread:owner-chat"


def _open(universe, box, *, granted=("bash",), session_key=THREAD, turn="live-1"):
    def bind():
        handle = box.bind(universe.name, account="owner", turn="t1")
        return BoxTools(BoxExecutor(box, handle, limits=None)), "/cc"

    def no_engine():
        raise AssertionError("the engine route must not open for loop-only tools")

    return open_loop_tools(granted=granted, bind_box=bind, owner="owner",
                           universe_dir=universe, engine_identity=no_engine, timeout=30,
                           session_key=session_key, turn=turn)


def _call(universe, box, name, arguments, **kwargs):
    async def scenario():
        async with _open(universe, box, **kwargs) as session:
            return await session.call(name, arguments, op_id="t1:1:1")

    return asyncio.run(scenario())


def test_a_founder_message_sent_mid_turn_rides_on_the_next_box_result(tmp_path):
    agent_steering.open_turn(tmp_path, THREAD, "live-1")
    agent_steering.enqueue(tmp_path, THREAD, "also check the invoice")
    result = _call(tmp_path, FakeBox(), "bash", {"command": "true"})
    texts = [block.text for block in result.content]
    assert texts[0] == "ok\n[exit code 0]"
    assert "also check the invoice" in texts[1]
    # Delivered once.
    again = _call(tmp_path, FakeBox(), "bash", {"command": "true"})
    assert len(again.content) == 1


def test_only_the_owners_thread_of_a_live_turn_is_steered(tmp_path):
    agent_steering.open_turn(tmp_path, THREAD, "live-1")
    agent_steering.enqueue(tmp_path, THREAD, "for the chat only")
    result = _call(tmp_path, FakeBox(), "bash", {"command": "true"},
                   session_key="node:agent-7")
    assert len(result.content) == 1


def test_a_name_outside_the_inventory_is_refused(tmp_path):
    with pytest.raises(EngineToolError, match="loop_tool_not_allowed"):
        _call(tmp_path, FakeBox(), "read_graph", {})


def test_a_granted_box_tool_with_no_box_is_refused_before_anything_opens(tmp_path):
    async def scenario():
        async with open_loop_tools(granted=("bash",), bind_box=None,
                                   owner="owner", universe_dir=tmp_path,
                                   engine_identity=lambda: ("owner", tmp_path.name),
                                   timeout=30):
            pass

    with pytest.raises(EngineToolError, match="box_unavailable"):
        asyncio.run(scenario())
