"""Automatic events execute pinned revision bytes in the actual Linux jail."""
import asyncio
import json
import shlex
from types import SimpleNamespace

from tests.test_extension_unit_jail import (  # noqa: F401
    _engine,
    bash,
    dynamic_data_root,
    pytestmark,
)
from tests.test_extension_unit_jail import world as world
from tinyassets.extension_state import ExtensionStore


def test_automatic_tool_hooks_use_jail_and_revoke_fences(world, monkeypatch):
    from tests.test_one_extension_unit import files
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.engine_mcp_server import ExtensionHookEvents

    server = _engine(monkeypatch, world)
    root = _universe_dir(server._GRAPH_ID)
    store = ExtensionStore(root.parent, owner=server._ACTOR_ID, universe=root.name, agent="main")
    content = files(hooks=[{"name": event, "description": event, "arguments": {}, "event": event}
                           for event in ("before_tool", "after_tool")], cards=[])
    content["run.py"] = (b'#!/usr/bin/env python3\nimport json,sys,subprocess\n'
                         b'with open("/u/hooks.log","a") as f: f.write(sys.argv[1]+"\\n")\n'
                         b'subprocess.run(["ta","read_graph","--json",'
                         b'json.dumps({"target":"connections"})], '
                         b'check=True,stdout=subprocess.PIPE)\n'
                         b'print(json.dumps({"ok":True}))\n')
    installed = store.install(content)
    pin = {"name": "sample", "revision": installed["revision"], "expected_generation": 0}
    assert json.loads(bash(server, "ta extension:activate --json " +
                           shlex.quote(json.dumps(pin))))["state"] == "active"
    from tinyassets.extension_hooks import has_hooks
    assert has_hooks(root, server._ACTOR_ID, "main", "before_tool")
    hook_key = f"extension:sample:{installed['revision']}:1:hooks:before_tool"
    described = json.loads(bash(server, "ta describe " + hook_key))
    assert described["event"] == "before_tool", described
    context = SimpleNamespace(message=SimpleNamespace(name="read_graph", arguments={}))
    calls = []
    from tinyassets.auth.middleware import current_identity_or_none

    prior_identity = current_identity_or_none()
    async def next_tool(_):
        assert current_identity_or_none() is prior_identity
        calls.append("tool")
        assert (root / ".agent-workspace" / "hooks.log").read_text() == "before_tool\n"
        return SimpleNamespace(isError=False)
    asyncio.run(ExtensionHookEvents().on_call_tool(context, next_tool))
    assert calls == ["tool"]
    assert (root / ".agent-workspace" / "hooks.log").read_text() == "before_tool\nafter_tool\n"
    store.transition("sample", installed["revision"], expected_generation=1, active=False)
    async def after_revoke(_):
        return SimpleNamespace(isError=False)
    asyncio.run(ExtensionHookEvents().on_call_tool(context, after_revoke))
    assert (root / ".agent-workspace" / "hooks.log").read_text() == "before_tool\nafter_tool\n"


def test_turn_event_runner_uses_existing_signed_tool_surface(world, monkeypatch):
    from tests.test_one_extension_unit import files
    from tinyassets import agent_turn_coordinator
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.extension_hooks import turn_event
    from tinyassets.served_tools import BACKEND_ENGINE_CAPABILITIES

    server = _engine(monkeypatch, world)
    root = _universe_dir(server._GRAPH_ID)
    store = ExtensionStore(root.parent, owner=server._ACTOR_ID, universe=root.name, agent="main")
    events = ("input", "turn_start", "context", "turn_end")
    content = files(hooks=[{"name": e, "description": e, "arguments": {}, "event": e}
                           for e in events], cards=[])
    content["run.py"] = (b'#!/usr/bin/env python3\nimport json,sys\n'
                         b'with open("/u/turn-hooks.log","a") as f: f.write(sys.argv[1]+"\\n")\n'
                         b'print(json.dumps({"ok":True}))\n')
    installed = store.install(content)
    pin = {"name": "sample", "revision": installed["revision"], "expected_generation": 0}
    assert json.loads(bash(server, "ta extension:activate --json " +
                           shlex.quote(json.dumps(pin))))["state"] == "active"
    from contextlib import asynccontextmanager

    class Session:
        async def call(self, name, arguments):
            assert name == "bash"
            text = await server.run_bash(**arguments)
            return SimpleNamespace(isError=False, content=[SimpleNamespace(text=text)])
    @asynccontextmanager
    async def open_tools(**kwargs):
        assert kwargs["actor_id"] == server._ACTOR_ID
        # The turn's signed backend grant (hooks run ta), not the four model tools.
        assert kwargs["enabled_tools"] == ("read", "write", "edit", "bash")
        assert kwargs["capability_grant"] == BACKEND_ENGINE_CAPABILITIES
        yield Session()
    monkeypatch.setattr(agent_turn_coordinator, "open_engine_tools", open_tools)
    coordinator = SimpleNamespace(
        config=SimpleNamespace(engine_tool_grant=None),
        context=SimpleNamespace(universe_dir=root, acting_agent=None), owner=server._ACTOR_ID,
        _interrupted=lambda: False, steering=lambda: {},
        adapter=SimpleNamespace(engine_identity=lambda *a: (server._ACTOR_ID, root.name)))
    coordinator._open_tools = lambda timeout: (
        agent_turn_coordinator.AgentTurnCoordinator._open_tools(coordinator, timeout))
    for event in events:
        asyncio.run(turn_event(coordinator, event, {"event": event}))
    assert (root / ".agent-workspace" / "turn-hooks.log").read_text().splitlines() == list(events)
    coordinator._interrupted = lambda: True
    asyncio.run(turn_event(coordinator, "turn_end", {}))
    assert (root / ".agent-workspace" / "turn-hooks.log").read_text().splitlines() == list(events)


def test_observational_failure_and_missing_bash_are_visible_not_replayed(world, monkeypatch):
    from tinyassets import extension_hooks
    from tinyassets.extension_hooks import read_evidence, turn_event

    root = world.universe_a
    coordinator = SimpleNamespace(context=SimpleNamespace(universe_dir=root, acting_agent=None),
                                  owner="owner")
    async def failed(*args):
        raise RuntimeError("hook_failed_after_completed_effect")
    monkeypatch.setattr(extension_hooks, "_turn_event", failed)
    assert "hook_failed_after_completed_effect" in asyncio.run(
        turn_event(coordinator, "turn_end", {"status": "completed"}))
    store = ExtensionStore(root.parent, owner="owner", universe=root.name, agent="main")
    assert read_evidence(store)[0]["event"] == "turn_end"
    async def skipped(*args):
        return "skipped: triggering turn has no bash grant"
    monkeypatch.setattr(extension_hooks, "_turn_event", skipped)
    assert "no bash grant" in asyncio.run(turn_event(coordinator, "turn_start", {}))
    assert len(read_evidence(store)) == 2
