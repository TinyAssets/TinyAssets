"""The thin loop end to end: the real writer, router, broker adapter and journal,
a scripted box and synthetic model wires (``test_interactive_http_agent``'s rig).
"""

from __future__ import annotations

import base64
import json

import pytest

from tests import test_interactive_http_agent as base
from tests.agent_loop_fakes import FakeBox
from tinyassets.agent_loop import served_chat
from tinyassets.boxes import ExecLimits
from tinyassets.engine_tool_client import EngineToolError

rig = base.rig
reader = base.reader
served = base.served
run = base.run


@pytest.fixture
def agent(base_agent, monkeypatch):
    monkeypatch.setenv(served_chat.ENV_SWITCH, served_chat.THIN)
    box = FakeBox(lambda argv, stdin: (
        b'\x1eTA1 {"ready":true}\n\x1eTA1 '
        + json.dumps({"output": base64.b64encode(b"box says hi").decode()})
        .encode() + b'\n', 0))
    monkeypatch.setattr(served_chat, "_box_provider", box)
    monkeypatch.setattr(served_chat, "_box_limits", ExecLimits())
    base_agent.box = box
    return base_agent


base_agent = base.agent


def _last_tool_text(agent):
    messages = agent.wires[-1][1]["body"]["messages"]
    return json.loads(messages[-1]["content"])["content"][0]["text"]


def test_box_tool_runs_in_the_bound_box_by_journal_op_id(agent):
    agent.tool_call = ("bash", '{"command": "echo hi"}')
    assert run(agent) == "finished exact answer"
    turn = agent.latest()
    assert turn.state == "completed"
    # Bound ONCE at turn start, to this owner and command center and turn.
    assert agent.box.binds == [(agent.served.context.universe_dir.name, "owner", turn.turn_id)]
    assert agent.box.starts == [f"{turn.turn_id}:1:1"]
    argv = agent.box.execs[f"{turn.turn_id}:1:1"].argv
    assert argv[:2] == ["python3", "-c"]
    assert json.loads(agent.box.execs[f"{turn.turn_id}:1:1"].stdin)["command"] == "echo hi"
    assert "TA_SOCKET" in argv[2]
    # Never forwarded to the engine route.
    assert agent.tools == []
    assert _last_tool_text(agent) == "box says hi\n[exit code 0]"


def test_unknown_box_outcome_holds_the_turn_and_nothing_replays(agent):
    agent.box.fail_start = 2
    agent.tool_call = ("bash", '{"command": "rm -rf build"}')
    with pytest.raises(EngineToolError, match="unknown"):
        run(agent)
    turn = agent.latest()
    assert turn.state == "held_tool_unknown"
    # One model round, the same op_id asked twice, no second inference.
    assert len(agent.wires) == 1
    assert set(agent.box.starts) == {f"{turn.turn_id}:1:1"}


def test_owner_read_is_answered_by_the_loop_and_never_reaches_the_box(agent):
    agent.tool_call = ("activity", "{}")
    assert run(agent) == "finished exact answer"
    assert agent.box.starts == [] and agent.tools == []
    assert "activity" in json.loads(_last_tool_text(agent))


def test_engine_tools_keep_their_engine_route(agent, monkeypatch):
    """Four model tools: engine capabilities are reached by ``ta`` in the box,
    over the turn's own signed engine session, never as a model tool."""
    from types import SimpleNamespace

    from tinyassets import engine_tool_client
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

    message = {"op": "call", "name": "read_graph", "arguments": {"target": "status"}}
    request, delivery = "a" * 32, "b" * 32
    agent.box.script = lambda argv, stdin: (
        b'\x1eTA1 {"ready":true}\n\x1eTA1 '
        + json.dumps({"request": request, "message": message, "delivery": delivery}).encode()
        + b'\n\x1eTA1 '
        + json.dumps({"output": base64.b64encode(b"read it").decode()}).encode() + b'\n', 0)
    opened, asked = [], []
    real_open, real_client = engine_tool_client.open_engine_tools, engine_tool_client._make_client

    def open_engine_tools(**kwargs):
        opened.append(kwargs)
        return real_open(**kwargs)

    def make_client(*args):
        client = real_client(*args)

        async def read_resource(uri):
            payload = uri.removeprefix("ta-bridge://request/")
            asked.append(json.loads(base64.urlsafe_b64decode(payload)))
            return [SimpleNamespace(text=json.dumps({"result": "status"}))]

        client.read_resource = read_resource
        return client

    monkeypatch.setattr("tinyassets.agent_loop.tool_session.open_engine_tools", open_engine_tools)
    monkeypatch.setattr(engine_tool_client, "_make_client", make_client)
    agent.tool_call = ("bash", json.dumps({"command": "ta call read_graph"}))
    assert run(agent) == "finished exact answer"
    # The model saw only the four box tools and the loop's owner reads.
    offered = {t["function"]["name"] for t in agent.wires[0][1]["body"]["tools"]}
    assert offered == {"read", "write", "edit", "bash", "history", "activity"}
    # The engine session displays bash alone but carries the turn's backend grant.
    assert [(o["enabled_tools"], o["capability_grant"]) for o in opened] == [
        (("bash",), BACKEND_ENGINE_CAPABILITIES)]
    # The box's ta request went to the engine route, verbatim, not as a tool call.
    assert asked == [message] and agent.tools == []
    turn = agent.latest()
    (sent,) = agent.box.stdin_sent
    assert sent[:2] == (f"{turn.turn_id}:1:1", delivery)
    assert json.loads(sent[2]) == {"request": request, "answer": {"result": "status"}}
    assert _last_tool_text(agent) == "read it\n[exit code 0]"


def test_no_box_provider_refuses_before_any_tool_runs(agent, monkeypatch):
    monkeypatch.setattr(served_chat, "_box_provider", None)
    agent.tool_call = ("bash", '{"command": "true"}')
    with pytest.raises(EngineToolError, match="box_unavailable"):
        run(agent)
    assert agent.wires == [] and agent.tools == []


def test_switch_off_keeps_todays_path(agent, monkeypatch):
    monkeypatch.delenv(served_chat.ENV_SWITCH)
    agent.tool_call = ("bash", '{"command": "true"}')
    assert run(agent) == "finished exact answer"
    assert agent.box.starts == [] and agent.tools == [("bash", {"command": "true"})]
