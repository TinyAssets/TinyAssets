"""Installed extensions run in a real remote box with local ta's fences.

The box receives only host-verified revision bytes for one launch; the engine
dispatcher still decides whether a revision is active for this owner. No skip
escape: these proofs run on the Linux oracle.
"""
from __future__ import annotations

import asyncio
import base64
import contextlib
import json
import os
import shlex
import shutil
from types import SimpleNamespace

import pytest

from tests.test_remote_box_ta import IsolatedBox, attach
from tests.test_remote_box_ta import box as box  # noqa: F401 -- fixture
from tests.test_ta_capabilities_jail import dynamic_data_root as dynamic_data_root
from tests.test_universe_tools_jail import world as world
from tinyassets.agent_loop.box_ta import TurnBridge, engine_deliver, engine_ta
from tinyassets.agent_loop.box_tools import BoxExecutor, BoxTools
from tinyassets.boxes import ExecLimits
from tinyassets.engine_tool_client import EngineToolError
from tinyassets.extension_state import ExtensionStore

pytestmark = pytest.mark.real_jail


@pytest.fixture(autouse=True)
def _real_bwrap():
    # No skip escape: a box without bubblewrap is a failed proof, not a pass.
    assert shutil.which("bwrap"), "Linux oracle must provide bubblewrap"

RUN = (b'#!/usr/bin/env python3\nimport json,os,sys\n'
       b'print(json.dumps({"entry":sys.argv[1],"value":"PINNED","cwd":os.getcwd()}))\n')


def _package(**extra):
    from tests.test_one_extension_unit import files

    content = files(hooks=[{"name": "start", "description": "Start", "arguments": {},
                            "event": "turn_start"}], cards=[], **extra)
    content["run.py"] = RUN
    return content


def _out(result, code=0):
    trailer = f"[exit code {code}]"
    assert result.endswith(trailer), result
    return result.removesuffix(trailer).strip()


@contextlib.asynccontextmanager
async def remote(server, world, tmp_path, *, turn="live"):
    """Production chain: box worker -> TurnBridge -> signed engine resource."""
    from fastmcp import Client

    from tinyassets import engine_mcp_http as routes
    from tinyassets.engine_tool_client import EngineToolSession

    routes._write_routes(world.data_root, [SimpleNamespace(
        universe_id="u-alpha", owner="actor-a", port=8790, secret="s" * 43)])
    route = routes.read_engine_mcp_route(actor_id="actor-a", graph_id="u-alpha",
                                        root=world.data_root)
    provider = IsolatedBox(boxes_root=tmp_path / "boxes", state_dir=tmp_path / "state",
                           owner_of={"u-alpha": "actor-a"}.get, allow_unisolated=True)
    client = Client(server.mcp)
    await client.__aenter__()
    engine = EngineToolSession(client, route, world.data_root, ("bash",))
    handle = provider.bind("u-alpha", account_id="actor-a", turn_id=turn)
    bridge = TurnBridge(owner="actor-a", center="u-alpha", turn=turn, handle=handle,
                        database=tmp_path / "receipts.db",
                        dispatch=lambda message, **kw: engine_ta(engine, message, **kw),
                        deliver=lambda: engine_deliver(engine))
    executor = BoxExecutor(provider, handle,
                           limits=ExecLimits(wall_seconds=60, output_bytes=2**20))
    executor.enable_ta(bridge)
    try:
        yield BoxTools(executor), engine
    finally:
        bridge.close()
        await client.__aexit__(None, None, None)
        provider.close()


def _activate(server, store, content):
    from tests.test_ta_capabilities_jail import bash

    installed = store.install(content)
    pin = {"name": "sample", "revision": installed["revision"], "expected_generation": 0}
    state = json.loads(bash(server, "ta extension:activate --json " +
                            shlex.quote(json.dumps(pin))))
    assert state["state"] == "active", state
    return installed["revision"], pin


def _owner_store(server):
    from tinyassets.api.helpers import _universe_dir

    root = _universe_dir(server._GRAPH_ID)
    return root, ExtensionStore(root.parent, owner=server._ACTOR_ID,
                                universe=root.name, agent="main")


def test_installed_tool_command_and_hook_run_in_remote_box(world, tmp_path, monkeypatch):
    from tests.test_ta_capabilities_jail import _engine, bash

    secret = "synthetic-host-secret-" + os.urandom(16).hex()
    monkeypatch.setenv("HOST_CREDENTIAL", secret)
    server = _engine(monkeypatch, world)
    _root, store = _owner_store(server)
    revision, _ = _activate(server, store, _package())
    prefix = f"extension:sample:{revision}:1"
    local_search = json.loads(bash(server, "ta search"))
    local_describe = json.loads(bash(server, f"ta describe {prefix}:tools:hello"))
    assert local_describe["availability"] == "jailed"
    inspect = ('import json,os,pathlib; r=pathlib.Path(os.environ["TA_EXTENSION_ROOT"]); '
               'print(json.dumps({str(p.relative_to(r)): [oct(p.stat().st_mode & 0o777), '
               'p.read_bytes().decode(errors="replace") if p.is_file() else None] '
               'for p in sorted(r.rglob("*"))}))')

    async def run():
        async with remote(server, world, tmp_path) as (tools, _):
            # Before delivery this was extension_runtime_unavailable.
            result = json.loads(_out(await tools.bash(
                "tool", f"ta {prefix}:tools:hello --json '{{}}'")))
            assert result["entry"] == "hello" and result["value"] == "PINNED"
            assert result["cwd"].endswith(f"/extensions/sample/{revision}")
            assert result["cwd"].startswith("/tmp/")  # box-local, never a host path
            command = json.loads(_out(await tools.bash(
                "command", f"ta {prefix}:commands:greet --json '{{}}'")))
            assert command["entry"] == "greet"
            event = json.dumps({"version": 1, "event": "turn_start", "payload": {}})
            hooks = json.loads(_out(await tools.bash(
                "hook", "ta extension:event --json " + shlex.quote(event))))
            assert [x["result"]["entry"] for x in hooks["results"]] == ["start"]
            # Parity with local ta after install: same names and same contract.
            assert json.loads(_out(await tools.bash("search", "ta search"))) == local_search
            assert json.loads(_out(await tools.bash(
                "describe", f"ta describe {prefix}:tools:hello"))) == local_describe
            staged = json.loads(_out(await tools.bash("inspect", "python3 -c " +
                                                      shlex.quote(inspect))))
            refused = await tools.bash(
                "write", f'echo changed > "$TA_EXTENSION_ROOT/sample/{revision}/run.py"; '
                f'touch "$TA_EXTENSION_ROOT/sample/new"')
            again = json.loads(_out(await tools.bash(
                "again", f"ta {prefix}:tools:hello --json '{{}}'")))
            return staged, refused, again

    staged, refused, again = asyncio.run(run())
    assert os.getuid() != 0, "read-only proof requires the oracle's unprivileged user"
    files = {path: body for path, (_mode, body) in staged.items() if body is not None}
    assert set(files) == {f"sample/{revision}/{name}"
                          for name in ("extension.json", "run.py", "view.html", "helper.txt")}
    assert all(mode == "0o555" for mode, _ in staged.values()), staged
    assert files[f"sample/{revision}/run.py"] == RUN.decode()
    blob = json.dumps(staged)
    for forbidden in (secret, "HOST_CREDENTIAL", str(world.data_root), str(tmp_path),
                      "grant_id", "connection_id"):
        assert forbidden not in blob
    assert "Permission denied" in refused and not refused.endswith("[exit code 0]")
    assert again["value"] == "PINNED"


def test_revoked_extension_disappears_from_remote_box(world, tmp_path, monkeypatch):
    from tests.test_ta_capabilities_jail import _engine

    server = _engine(monkeypatch, world)
    _root, store = _owner_store(server)
    revision, pin = _activate(server, store, _package())
    name = f"extension:sample:{revision}:1:tools:hello"

    async def run():
        async with remote(server, world, tmp_path) as (tools, _):
            assert "PINNED" in _out(await tools.bash("before", f"ta {name} --json '{{}}'"))
            pin["expected_generation"] = 1
            revoked = json.loads(_out(await tools.bash(
                "revoke", "ta extension:revoke --json " + shlex.quote(json.dumps(pin)))))
            assert revoked["state"] == "revoked", revoked
            called = await tools.bash("after", f"ta {name} --json '{{}}'")
            search = json.loads(_out(await tools.bash("search", "ta search sample")))
            absent = _out(await tools.bash(
                "absent", 'test ! -e "$TA_EXTENSION_ROOT/sample" && echo ABSENT'))
            return called, search, absent

    called, search, absent = asyncio.run(run())
    assert called.endswith("[exit code 1]") and "PINNED" not in called
    assert "unknown capability" in called
    assert search == []
    assert absent == "ABSENT"


def test_cross_owner_extension_never_reaches_remote_box(world, tmp_path, monkeypatch):
    from tests.test_ta_capabilities_jail import _engine
    from tinyassets.agent_loop.box_ta import engine_resource

    server = _engine(monkeypatch, world)
    root, _store = _owner_store(server)
    foreign = ExtensionStore(root.parent, owner="actor-b", universe=root.name, agent="main")
    installed = foreign.install(_package())
    foreign.transition("sample", installed["revision"], expected_generation=0, active=True)
    name = f"extension:sample:{installed['revision']}:1:tools:hello"

    async def run():
        async with remote(server, world, tmp_path) as (tools, _):
            search = json.loads(_out(await tools.bash("search", "ta search sample")))
            called = await tools.bash("call", f"ta {name} --json '{{}}'")
            absent = _out(await tools.bash(
                "absent", 'test ! -e "$TA_EXTENSION_ROOT/sample" && echo ABSENT'))
            # Even a forged host claim of the foreign revision is re-checked
            # against this owner's own store by the engine dispatcher.
            payload = base64.urlsafe_b64encode(json.dumps({
                "ta": {"op": "call", "name": name, "arguments": {}},
                "mounts": [["sample", installed["revision"], 1]]}).encode()).decode()
            forged = json.loads(await engine_resource(server, payload))
            # A box message can never carry the host envelope's mounts field.
            nested = base64.urlsafe_b64encode(json.dumps({
                "ta": {"ta": {"op": "catalog"}, "mounts": []}, "mounts": []
            }).encode()).decode()
            return search, called, absent, forged, json.loads(
                await engine_resource(server, nested))

    search, called, absent, forged, nested = asyncio.run(run())
    assert search == []
    assert called.endswith("[exit code 1]") and "PINNED" not in called
    assert absent == "ABSENT"
    assert "unknown or inactive extension capability" in forged["error"]
    assert nested == {"error": "invalid ta request"}


@pytest.mark.parametrize("fault", ["digest", "transport", "lost_reply"])
def test_failed_delivery_fails_closed_before_command_runs(box, tmp_path, fault):  # noqa: F811
    from tests.test_one_extension_unit import files
    from tinyassets.extension_manifest import build_revision

    revision = build_revision(files())

    async def dispatch(message, **_):
        raise AssertionError("no command may run after a failed delivery")

    async def deliver():
        if fault == "transport":
            raise ConnectionError("engine unreachable")
        if fault == "lost_reply":
            # What call_ta really raises: a read with no effect is still not_sent.
            raise EngineToolError("remote_ta_outcome_unknown", outcome="unknown")
        tampered = revision.blob[:-1] + bytes([revision.blob[-1] ^ 1])
        return {"extensions": [{"name": "sample", "revision": revision.digest, "generation": 1,
                                "blob": base64.b64encode(tampered).decode()}],
                "undelivered": []}

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        bridge.deliver = deliver
        try:
            with pytest.raises(EngineToolError, match="remote_extension_delivery_failed") as err:
                await tools.bash("refused", "touch /cc/should-not-exist")
            assert err.value.outcome == "not_sent"
            assert box.stat(bridge.handle, "/cc/should-not-exist") is None
        finally:
            bridge.close()

    asyncio.run(run())


def test_turn_hooks_run_in_the_turns_remote_box(world, tmp_path, monkeypatch):
    """Turn lifecycle hooks use the turn's own route: on the thin loop, its box."""
    from tests.test_ta_capabilities_jail import _engine
    from tinyassets.agent_loop import tool_session
    from tinyassets.extension_hooks import turn_event

    server = _engine(monkeypatch, world)
    root, store = _owner_store(server)
    content = _package()
    content["run.py"] = (b'#!/usr/bin/env python3\nimport json,sys\n'
                         b'open("/cc/turn-hook.log","a").write(sys.argv[1]+"\\n")\n'
                         b'print(json.dumps({"ok":True}))\n')
    _activate(server, store, content)

    async def run():
        async with remote(server, world, tmp_path, turn="turn-1") as (tools, engine):
            @contextlib.asynccontextmanager
            async def engine_route(**_):
                yield engine

            monkeypatch.setattr(tool_session, "open_engine_tools", engine_route)
            coordinator = SimpleNamespace(
                config=SimpleNamespace(engine_tool_grant=None),
                context=SimpleNamespace(universe_dir=root, acting_agent=None),
                owner=server._ACTOR_ID, turn=SimpleNamespace(turn_id="turn-1"),
                _interrupted=lambda: False)
            coordinator._open_tools = lambda timeout: tool_session.open_loop_tools(
                granted=("bash",), loop_reads=(), bind_box=lambda: (tools, "/cc"),
                owner=server._ACTOR_ID, universe_dir=root,
                engine_identity=lambda: (server._ACTOR_ID, root.name),
                timeout=timeout, ta_turn="turn-1")
            assert await turn_event(coordinator, "turn_start", {"turn_id": "turn-1"}) is None

    asyncio.run(run())
    assert (tmp_path / "boxes" / "u-alpha" / "turn-hook.log").read_text() == "start\n"
    # The hook ran in the box, not the platform's local jail.
    assert not (root / ".agent-workspace" / "turn-hook.log").exists()
