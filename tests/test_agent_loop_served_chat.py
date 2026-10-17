"""The thin loop end to end: the real writer, router, broker adapter and journal,
a scripted box and synthetic model wires (``test_interactive_http_agent``'s rig).
"""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace

import pytest

from tests import test_interactive_http_agent as base
from tinyassets.agent_loop import served_chat
from tinyassets.control_plane.binding import use_binding

rig = base.rig
reader = base.reader
served = base.served
run = base.run


@pytest.fixture
def agent(base_agent, monkeypatch):
    from tinyassets.agent_loop.owner_tools import OwnerTools

    observed = SimpleNamespace(starts=[], commands=[])
    original = OwnerTools.bash

    async def bash(self, op_id, command, timeout=0, **kwargs):
        observed.starts.append(op_id)
        observed.commands.append(command)
        return await original(self, op_id, command, timeout, **kwargs)

    monkeypatch.setattr(OwnerTools, "bash", bash)
    base_agent.box = observed
    base_agent.adapter = served_chat.ThinLoopChatAdapter()
    yield base_agent


base_agent = base.agent


def _last_tool_text(agent):
    messages = agent.wires[-1][1]["body"]["messages"]
    return json.loads(messages[-1]["content"])["content"][0]["text"]


def test_thin_loop_model_inventory_is_exactly_four(agent):
    agent.tool_call = ("bash", '{"command": "true"}')
    assert run(agent) == "finished exact answer"
    offered = [t["function"]["name"] for t in agent.wires[0][1]["body"]["tools"]]
    assert offered == ["read", "write", "edit", "bash"]


def test_box_tool_runs_in_the_bound_box_by_journal_op_id(agent):
    root = agent.served.context.universe_dir
    agent.tool_call = ("bash", '{"command": "printf local > proof.txt; cat proof.txt"}')
    assert run(agent) == "finished exact answer"
    turn = agent.latest()
    assert turn.state == "completed"
    assert agent.box.starts == [f"{turn.turn_id}:1:1"]
    assert agent.box.commands == ["printf local > proof.txt; cat proof.txt"]
    assert (root / "proof.txt").read_text() == "local"
    assert _last_tool_text(agent) == "local\n[exit code 0]"
    assert agent.tools == []
    assert turn.rounds[0].tools[0].state == "completed"
    assert len(turn.rounds) == 2




@pytest.mark.parametrize("name", ["history", "activity"])
def test_direct_owner_reads_are_not_model_tools(agent, name):
    agent.tool_call = (name, "{}")
    assert run(agent) == "finished exact answer"
    assert agent.box.starts == [] and agent.tools == []
    assert all(not round_.tools for round_ in agent.latest().rounds)


@pytest.mark.parametrize("target", ["status", "conversation", "runs"])
def test_engine_tools_keep_their_engine_route(agent, monkeypatch, target):
    from tinyassets import engine_tool_client
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

    opened, asked = [], []
    real_open, real_client = engine_tool_client.open_engine_tools, engine_tool_client._make_client
    def open_engine_tools(**kwargs):
        opened.append(kwargs)
        return real_open(**kwargs)
    def make_client(*args):
        client = real_client(*args)
        async def read_resource(uri):
            payload = uri.removeprefix("ta-bridge://request/")
            message = json.loads(base64.urlsafe_b64decode(payload))
            asked.append(message)
            if message == {"deliver": "extensions"}:
                result = {"extensions": [], "undelivered": []}
            elif message == {"op": "catalog"}:
                result = {"capabilities": [{"name": "read_graph"}]}
            else:
                result = {"result": "status"}
            return [SimpleNamespace(text=json.dumps(result))]
        client.read_resource = read_resource
        return client
    monkeypatch.setattr("tinyassets.agent_loop.tool_session.open_engine_tools", open_engine_tools)
    monkeypatch.setattr(engine_tool_client, "_make_client", make_client)
    command = "ta read_graph --json '" + json.dumps({"target": target}) + "'"
    agent.tool_call = ("bash", json.dumps({"command": command}))
    assert run(agent) == "finished exact answer"
    offered = {t["function"]["name"] for t in agent.wires[0][1]["body"]["tools"]}
    assert offered == {"read", "write", "edit", "bash"}
    assert [(o["enabled_tools"], o["capability_grant"]) for o in opened] == [
        (("bash",), BACKEND_ENGINE_CAPABILITIES)]
    assert asked == [{"op": "catalog"},
                     {"op": "call", "name": "read_graph", "arguments": {"target": target}}]
    assert agent.tools == []
    assert agent.box.commands == [command]
    assert agent.box.starts == [f"{agent.latest().turn_id}:1:1"]
    assert '"status"' in _last_tool_text(agent)
    assert _last_tool_text(agent).endswith("[exit code 0]")








def test_the_served_chat_factory_always_selects_the_local_thin_loop(agent, monkeypatch):
    from tinyassets.providers import call

    built = []
    monkeypatch.setattr(call, "_router", object())
    monkeypatch.setattr(call, "register_open_providers_for", lambda _context: None)
    monkeypatch.setattr(
        "tinyassets.interactive_http_agent.InteractiveHttpAgentTurn",
        lambda **kwargs: built.append(kwargs),
    )

    def factory_adapter():
        agent.factory(
            prompt="hi", system="", universe_context=object(), config={})
        return built.pop().get("adapter")

    monkeypatch.setenv("TINYASSETS_AGENT_LOOP", "thin")
    with use_binding(None):
        adapter = factory_adapter()
    assert isinstance(adapter, served_chat.ServedChatAgentAdapter)
    assert isinstance(adapter, served_chat.ThinLoopChatAdapter)
    monkeypatch.delenv("TINYASSETS_AGENT_LOOP")
    assert isinstance(factory_adapter(), served_chat.ThinLoopChatAdapter)
