"""The four in-box tools and local ta keep file, cancellation and receipt behavior."""
import asyncio
import json
from dataclasses import replace

import pytest

from tests.test_c6_owner_box_callers import owner as owner  # noqa: F401
from tinyassets.agent_loop.owner_tools import OwnerTools, box_tool_definitions

pytestmark = pytest.mark.role_split


@pytest.mark.verifies('owner-daemon-in-box:C2')
@pytest.mark.parametrize("producer", ["write", "edit", "bash", "engine_hook", "preview",
                                     "workspace", "binding", "turn", "turn_hook"])
def test_operation_producers_mint_current_generation_and_refuse_old(owner, monkeypatch, producer):
    from types import SimpleNamespace

    from tinyassets import universe_tools
    from tinyassets.boxes.provider import BoxOperationRefused

    captured = []
    begin = owner.core._begin

    def record(cc, op, kind, payload):
        captured.append(op)
        return begin(cc, op, kind, payload)

    monkeypatch.setattr(owner.core, "_begin", record)

    def invoke():
        if producer == "write":
            universe_tools.write_file(owner.home, "note", "one", agent_id="main")
        elif producer == "edit":
            (owner.home / "note").write_text("one")
            universe_tools.edit_file(owner.home, "note", "one", "two", agent_id="main")
        elif producer == "bash":
            assert "[exit code 0]" in universe_tools.bash(owner.home, "true", agent_id="main")
        elif producer == "engine_hook":
            from tinyassets import extension_hooks

            monkeypatch.setattr(extension_hooks, "has_hooks", lambda *a: True)
            monkeypatch.setattr(extension_hooks, "command", lambda *a: "echo hook")
            monkeypatch.setattr("tinyassets.api.helpers._universe_dir", lambda *a: owner.home)

            async def dispatch(*args):
                return lambda message: {}

            monkeypatch.setattr("tinyassets.ta_capabilities.engine_dispatch", dispatch)
            server = SimpleNamespace(_GRAPH_ID="center", _ACTOR_ID="alice",
                                     _acting_agent=lambda: "main")
            asyncio.run(extension_hooks._engine_event(server, "after_tool", {}))
        elif producer == "preview":
            from tinyassets.ui_preview import write_preview

            assert write_preview(owner.home, "screen", b"png") == "/u/previews/screen.png"
        elif producer == "workspace":
            from tinyassets.workspace_fs import BoxPoolFilesystem

            path = owner.home / ".agent-workspace/lease"
            path.mkdir(parents=True, exist_ok=True)
            BoxPoolFilesystem(owner.runtime.root).remove_tree_no_follow(path)
            assert not path.exists()
        elif producer == "binding":
            from tinyassets.control_plane.binding import SessionBinding

            binding = SessionBinding(owner.core, SimpleNamespace(turn_id="turn"), None, owner.home)
            owner.core.consume_operation(owner.core.bind("alice"), binding.operation_id("bash"),
                                         "bash", {})
        else:
            from tinyassets.daemon_server import set_founder_home
            from tinyassets.storage.agent_turn_journal import AgentTurnJournal

            set_founder_home(owner.runtime.root, founder_sub="alice", universe_id="center")
            journal = AgentTurnJournal(owner.runtime.root)
            turn = journal.create("alice", "center", prompt="hello", system="fixture")
            # Coordinator tool positions and turn hooks derive from the
            # durable turn identity; replay must never prefix it again.
            suffix = ":1:1" if producer == "turn" else ":hook:after_tool"
            owner.core.consume_operation(owner.core.bind("alice"), turn.turn_id + suffix,
                                         "bash", {})
        return captured[-1]

    old = invoke()
    owner.core._volume_generation += 1
    fresh = invoke()  # real admission and effect, not just a prefix comparison
    assert fresh.startswith(owner.core.operation_prefix())
    with pytest.raises(BoxOperationRefused, match="not re-run"):
        owner.core.consume_operation(owner.core.bind("alice"), old, "bash", {})


@pytest.mark.verifies('owner-daemon-in-box:C2')
def test_bash_consumes_operation_before_effect_and_refuses_replay(owner):
    from tinyassets.boxes.provider import BoxOperationRefused

    tools = OwnerTools(owner.home)
    prefix = owner.core.operation_prefix()

    async def run():
        await tools.bash(prefix + "once", "echo effect >> effects")
        with pytest.raises(BoxOperationRefused, match="not re-run"):
            await tools.bash(prefix + "once", "echo effect >> effects")

    asyncio.run(run())
    assert (owner.home / "effects").read_text() == "effect\n"
    assert owner.core._state.lookup("alice", prefix + "once") is not None


@pytest.mark.verifies('owner-daemon-in-box:T8')
def test_local_tools_read_write_edit_and_definitions(owner):
    tools = OwnerTools(owner.home)
    prefix = owner.core.operation_prefix()

    async def run():
        assert "wrote 8 bytes" in await tools.write(prefix + "write", "notes/a", "one\ntwo\n")
        assert await tools.read("read", "notes/a", offset=2, limit=1) == "two\n"
        assert await tools.edit(prefix + "edit", "notes/a", "two", "three") == "edited notes/a"
        assert await tools.read("read2", "notes/a") == "one\nthree\n"
        assert await tools.read("folder", "notes") == "a\n"
        assert "exactly once" in await tools.edit(prefix + "missing", "notes/a", "absent", "x")
        assert "error" in await tools.edit(prefix + "empty", "notes/a", "", "x")
        assert "does not take" in await tools.call("read", "extra", {"path": "a", "owner": "bob"})
        assert "error" in await tools.read("missing", "absent")
        assert "error" in await tools.bash(prefix + "empty", "")

    asyncio.run(run())
    definitions = box_tool_definitions(str(owner.home))
    assert tuple(definitions) == ("read", "write", "edit", "bash")
    assert definitions["write"]["inputSchema"]["required"] == ["path", "content"]
    assert str(owner.home) in definitions["bash"]["description"]
    assert (owner.home / "notes/a").read_text() == "one\nthree\n"
    assert not hasattr(tools, "_exec")
    assert not hasattr(owner.core, "start_exec")






@pytest.mark.verifies('owner-daemon-in-box:T8')
def test_local_ta_roundtrip_receipts_fences_and_changed_payload(owner):
    tools = OwnerTools(owner.home)
    prefix = owner.core.operation_prefix()
    effects = []

    async def dispatch(message):
        if message == {"op": "catalog"}:
            return {"capabilities": [{"name": "owner.effect", "description": "effect"}],
                    "extension_roots": {}}
        effects.append(message)
        return {"result": {"owner": "alice", "value": "committed"}}

    async def run():
        bridge = tools.enable_ta(dispatch=dispatch, turn="turn")
        try:
            result = await tools.bash(prefix + "exec", "ta owner.effect --json '{}' > answer")
            assert result.endswith("[exit code 0]"), result
            assert json.loads((owner.home / "answer").read_text())["owner"] == "alice"
            assert len(effects) == 1
            args = (bridge.handle, prefix + "retry", "b" * 32, {"op": "call"})
            first = await asyncio.to_thread(bridge.request, *args)
            assert first == {"result": {"owner": "alice", "value": "committed"}}
            assert await asyncio.to_thread(bridge.request, *args) == first
            changed = await asyncio.to_thread(bridge.request, *args[:-1], {"op": "catalog"})
            assert "reused" in changed["error"]
            assert len(effects) == 2
            for field, value in (("account_id", "bob"), ("command_center_id", "foreign"),
                                 ("turn_id", "foreign")):
                foreign = replace(bridge.handle, **{field: value})
                answer = await asyncio.to_thread(bridge.request, foreign, prefix + "x", "c" * 32,
                                                {"op": "catalog"})
                assert "refused" in answer["error"]
            assert len(effects) == 2
        finally:
            bridge.close()
        assert "expired" in (await asyncio.to_thread(bridge.request, *args))["error"]
        reopened = tools.enable_ta(dispatch=dispatch, turn="turn")
        try:
            assert await asyncio.to_thread(reopened.request, *args) == first
            assert len(effects) == 2
        finally:
            reopened.close()

    asyncio.run(run())






@pytest.mark.verifies('owner-daemon-in-box:T8')
@pytest.mark.parametrize("failure", ["lost", "invalid"])
def test_extension_delivery_failure_prevents_local_command(owner, failure):
    from tinyassets.engine_tool_client import EngineToolError

    tools = OwnerTools(owner.home)
    prefix = owner.core.operation_prefix()

    async def dispatch(message):
        raise AssertionError("failed delivery must never dispatch")

    async def deliver():
        if failure == "lost":
            raise ConnectionError("lost delivery")
        return {"invalid": "package"}

    async def run():
        bridge = tools.enable_ta(dispatch=dispatch, deliver=deliver, turn="delivery")
        try:
            with pytest.raises(EngineToolError, match="extension_delivery_failed"):
                await tools.bash(prefix + "delivery", "touch should-not-exist")
            assert not (owner.home / "should-not-exist").exists()
            assert not bridge._executions
        finally:
            bridge.close()

    asyncio.run(run())
