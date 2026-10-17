"""The four in-box tools and local ta keep file, cancellation and receipt behavior."""
import asyncio
import json
from dataclasses import replace

import pytest

from tests.test_c6_owner_box_callers import owner as owner  # noqa: F401
from tinyassets.agent_loop.owner_tools import OUTPUT_BYTES, OwnerTools, box_tool_definitions

pytestmark = pytest.mark.role_split


def test_local_tools_read_write_edit_and_definitions(owner):
    tools = OwnerTools(owner.home)

    async def run():
        assert "wrote 8 bytes" in await tools.write("write", "notes/a", "one\ntwo\n")
        assert await tools.read("read", "notes/a", offset=2, limit=1) == "two\n"
        assert await tools.edit("edit", "notes/a", "two", "three") == "edited notes/a"
        assert await tools.read("read2", "notes/a") == "one\nthree\n"
        assert await tools.read("folder", "notes") == "a\n"
        assert "exactly once" in await tools.edit("missing", "notes/a", "absent", "x")
        assert "error" in await tools.edit("empty", "notes/a", "", "x")
        assert "does not take" in await tools.call("read", "extra", {"path": "a", "owner": "bob"})
        assert "error" in await tools.read("missing", "absent")
        assert "error" in await tools.bash("empty", "")

    asyncio.run(run())
    definitions = box_tool_definitions(str(owner.home))
    assert tuple(definitions) == ("read", "write", "edit", "bash")
    assert definitions["write"]["inputSchema"]["required"] == ["path", "content"]
    assert str(owner.home) in definitions["bash"]["description"]
    assert (owner.home / "notes/a").read_text() == "one\nthree\n"
    assert not hasattr(tools, "_exec")
    assert not hasattr(owner.core, "start_exec")


def test_local_tools_refuse_foreign_files_and_links(owner, tmp_path):
    tools = OwnerTools(owner.home)
    victim = tmp_path / "victim"
    victim.write_text("foreign")
    (owner.home / "link").symlink_to(victim)

    async def run():
        assert "error" in await tools.call("read", "foreign", {"path": str(victim)})
        assert "error" in await tools.read("link", "link")
        assert "error" in await tools.call("write", "up", {"path": "../other", "content": "x"})

    asyncio.run(run())
    assert victim.read_text() == "foreign"
    assert not (owner.home.parent / "other").exists()


def test_local_bash_output_timeout_and_cancellation(owner):
    tools = OwnerTools(owner.home)

    async def run():
        assert await tools.bash("ok", "echo hello") == "hello\n[exit code 0]"
        orphan = await asyncio.wait_for(tools.bash("orphan", "sleep 30 & echo leader-done"), 3)
        assert orphan == "leader-done\n[exit code 0]"
        assert (await tools.bash("timeout", "sleep 30", timeout=1)).endswith(
            "[killed: ran longer than 1s]")
        loud = await tools.bash("output", "head -c 100000 /dev/zero")
        assert loud.endswith(f"[killed: output passed {OUTPUT_BYTES} bytes]")
        assert len(loud) <= OUTPUT_BYTES + 100
        task = asyncio.create_task(tools.bash("cancel", "echo started > leader; sleep 30"))
        for _ in range(200):
            if (owner.home / "leader").exists():
                break
            await asyncio.sleep(.01)
        assert (owner.home / "leader").read_text() == "started\n"
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())


def test_local_ta_roundtrip_receipts_fences_and_changed_payload(owner):
    tools = OwnerTools(owner.home)
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
            result = await tools.bash("exec", "ta owner.effect --json '{}' > answer")
            assert result.endswith("[exit code 0]"), result
            assert json.loads((owner.home / "answer").read_text())["owner"] == "alice"
            assert len(effects) == 1
            args = (bridge.handle, "retry", "b" * 32, {"op": "call"})
            first = await asyncio.to_thread(bridge.request, *args)
            assert first == {"result": {"owner": "alice", "value": "committed"}}
            assert await asyncio.to_thread(bridge.request, *args) == first
            changed = await asyncio.to_thread(bridge.request, *args[:-1], {"op": "catalog"})
            assert "reused" in changed["error"]
            assert len(effects) == 2
            for field, value in (("account_id", "bob"), ("command_center_id", "foreign"),
                                 ("turn_id", "foreign")):
                foreign = replace(bridge.handle, **{field: value})
                answer = await asyncio.to_thread(bridge.request, foreign, "x", "c" * 32,
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


def test_local_ta_cancellation_preserves_unknown_receipt(owner):
    tools = OwnerTools(owner.home)
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    effects = []

    async def dispatch(message):
        entered.set()
        effects.append(message)
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def run():
        bridge = tools.enable_ta(dispatch=dispatch, turn="cancel-turn")
        task = asyncio.create_task(tools.bash("cancel", "ta search"))
        await asyncio.wait_for(entered.wait(), 10)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 2)
        bridge.close()
        import sqlite3
        with sqlite3.connect(owner.runtime.root / ".remote-ta-receipts.sqlite3") as db:
            request, answer = db.execute("SELECT request, answer FROM ta_receipts").fetchone()
        assert answer is None
        restarted = tools.enable_ta(dispatch=dispatch, turn="cancel-turn")
        try:
            result = await asyncio.to_thread(restarted.request, restarted.handle, "cancel",
                                             request, {"op": "catalog"})
            assert "unknown" in result["error"]
            assert len(effects) == 1
        finally:
            restarted.close()

    asyncio.run(run())


def test_local_ta_large_results_stay_off_bash_output(owner):
    tools = OwnerTools(owner.home)
    body = "x" * (OUTPUT_BYTES * 2)

    async def dispatch(message):
        if message == {"op": "catalog"}:
            return {"capabilities": [{"name": "owner.value", "description": "value"}],
                    "extension_roots": {}}
        return {"result": {"body": body}}

    async def run():
        bridge = tools.enable_ta(dispatch=dispatch, turn="large")
        try:
            result = await tools.bash("large", "ta owner.value --json '{}' > large.json")
            assert result.endswith("[exit code 0]"), result
            assert len(result) < OUTPUT_BYTES
            answer = json.loads((owner.home / "large.json").read_text())
            assert answer["body"] == body
        finally:
            bridge.close()

    asyncio.run(run())


@pytest.mark.parametrize("failure", ["lost", "invalid"])
def test_extension_delivery_failure_prevents_local_command(owner, failure):
    from tinyassets.engine_tool_client import EngineToolError

    tools = OwnerTools(owner.home)

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
                await tools.bash("delivery", "touch should-not-exist")
            assert not (owner.home / "should-not-exist").exists()
            assert not bridge._executions
        finally:
            bridge.close()

    asyncio.run(run())



def test_local_bash_fences_file_cas_until_the_child_stops(owner):
    from tinyassets.boxes.provider import WriteConflict, WriteMode

    tools = OwnerTools(owner.home)
    tools.provider.write(tools.handle, "seed", "/cc/center/file", b"before", max_bytes=100)
    generation = tools.provider.committed_generation(tools.handle)

    async def run():
        task = asyncio.create_task(tools.bash("writer", "echo ready > started; sleep 30"))
        try:
            for _ in range(200):
                if (owner.home / "started").exists():
                    break
                await asyncio.sleep(.01)
            assert (owner.home / "started").read_text() == "ready\n"
            assert owner.core._state.pending("alice") == 1
            with pytest.raises(WriteConflict):
                tools.provider.write(tools.handle, "conflict", "/cc/center/file", b"stale",
                                     max_bytes=100, mode=WriteMode.CAS,
                                     expect_generation=generation)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert owner.core._state.pending("alice") == 0
        assert tools.provider.committed_generation(tools.handle) > generation
        assert (owner.home / "file").read_bytes() == b"before"

    asyncio.run(run())
