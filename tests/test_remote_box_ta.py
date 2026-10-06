"""Real isolated box processes and the production reverse-RPC transport.

No skip escape: these proofs must run on the Linux oracle. The test driver adds
a real bwrap boundary to the reference driver's real auth, writes and receipts.
"""
from __future__ import annotations

import asyncio
import json
import os
import shlex
import shutil
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from tests.test_ta_capabilities_jail import dynamic_data_root as dynamic_data_root
from tests.test_universe_tools_jail import world as world
from tinyassets.agent_loop.box_ta import TurnBridge
from tinyassets.agent_loop.box_tools import BoxExecutor, BoxTools
from tinyassets.boxes import BoxAuthError, ExecLimits
from tinyassets.boxes.local import LocalBoxProvider
from tinyassets.engine_tool_client import EngineToolError

pytestmark = pytest.mark.real_jail


class IsolatedBox(LocalBoxProvider):
    """Real box host, not a scripted exec/stream/write fake."""

    def start_exec(self, handle, op_id, argv, **kwargs):
        self.ensure_awake(handle, reason="test execution")
        bwrap = shutil.which("bwrap")
        assert bwrap, "Linux oracle must provide bubblewrap"
        # BoxExecutor starts on a short-lived thread. PDEATHSIG would kill bwrap
        # when that thread returns; LocalBoxProvider owns process-group cleanup.
        args = [bwrap, "--unshare-all", "--new-session",
                "--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp"]
        for path in ("/usr", "/bin", "/lib", "/lib64"):
            if Path(path).exists():
                args.extend(["--ro-bind", path, path])
        args.extend(["--bind", str(self._root / handle.command_center_id), "/cc",
                     "--chdir", "/cc", "--", *argv])
        return super().start_exec(handle, op_id, args, **kwargs)


@pytest.fixture
def box(tmp_path):
    provider = IsolatedBox(boxes_root=tmp_path / "boxes", state_dir=tmp_path / "state",
                           owner_of={"cc-a": "owner-a", "cc-b": "owner-b"}.get,
                           allow_unisolated=True)
    try:
        yield provider
    finally:
        provider.close()


def attach(box, tmp_path, dispatch):
    handle = box.bind("cc-a", account_id="owner-a", turn_id="turn-a")
    bridge = TurnBridge(owner="owner-a", center="cc-a", turn="turn-a", handle=handle,
                        database=tmp_path / "receipts.db", dispatch=dispatch)
    executor = BoxExecutor(box, handle, limits=ExecLimits(wall_seconds=20, output_bytes=2**20))
    executor.enable_ta(bridge)
    return bridge, BoxTools(executor)


def test_real_box_ta_roundtrip_and_credentials_absent(box, tmp_path, monkeypatch):
    secret = "synthetic-host-secret-" + os.urandom(16).hex()
    monkeypatch.setenv("HOST_CREDENTIAL", secret)
    (tmp_path / "credential").write_text(secret)
    sentinel = subprocess.Popen(
        [sys.executable, "-c", "import time; print('ready', flush=True); time.sleep(60)"],
        env={"HOST_CREDENTIAL": secret}, stdout=subprocess.PIPE)
    assert sentinel.stdout.readline() == b"ready\n"
    assert secret.encode() in Path(f"/proc/{sentinel.pid}/environ").read_bytes()
    effects = tmp_path / "effects"

    async def dispatch(message):
        if message == {"op": "catalog"}:
            return {"capabilities": [{"name": "owner.effect", "description": "owner effect"}],
                    "extension_roots": {}}
        assert message == {"op": "call", "name": "owner.effect", "arguments": {}}
        with effects.open("a") as stream:
            stream.write("owner-a/cc-a/turn-a\n")
        return {"result": {"owner": "owner-a", "center": "cc-a", "turn": "turn-a"}}

    # The marker is never passed to the box, even as a search argument.
    inspect = '''import json,os,pathlib
e=dict(os.environ)
p={}
for f in pathlib.Path('/proc').glob('[0-9]*/environ'):
 try:p[str(f)]=f.read_bytes().decode(errors='replace')
 except OSError:pass
files={}
for f in pathlib.Path('/cc').rglob('*'):
 if f.is_file():files[str(f)]=f.read_text(errors='replace')
print(json.dumps({'env':e,'proc':p,'files':files,'host_visible':pathlib.Path(%r).exists()}))
''' % str(tmp_path / "credential")

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        try:
            result = await tools.bash("turn-a:1", "ta owner.effect --json '{}'")
            assert '"owner": "owner-a"' in result
            assert result.endswith("[exit code 0]")
            result = await tools.bash("turn-a:2", "python3 -c " + shlex.quote(inspect))
            assert result.endswith("[exit code 0]"), result
            data = json.loads(result.removesuffix("[exit code 0]").strip())
            assert data["proc"] and data["env"]
            assert data["files"] == {}  # RPC leaves no durable command-center files.
            assert data["host_visible"] is False
            assert secret not in result
            assert "HOST_CREDENTIAL" not in result
            assert effects.read_text() == "owner-a/cc-a/turn-a\n"
        finally:
            bridge.close()

    try:
        asyncio.run(run())
    finally:
        sentinel.terminate()
        sentinel.wait(timeout=5)


@pytest.mark.parametrize("field,value", [
    ("account_id", "owner-b"), ("command_center_id", "cc-b"), ("turn_id", "turn-b"),
])
def test_cross_owner_center_turn_refused(box, tmp_path, field, value):
    async def dispatch(message):
        raise AssertionError("foreign authority must never reach dispatch")

    async def run():
        bridge, _ = attach(box, tmp_path, dispatch)
        foreign = replace(bridge.handle, **{field: value})
        try:
            answer = await asyncio.to_thread(bridge.request, foreign, "exec", "a" * 32,
                                            {"op": "catalog"})
            assert "refused" in answer["error"]
            with pytest.raises(EngineToolError, match="binding_refused"):
                TurnBridge(owner="owner-a", center="cc-a", turn="turn-a", handle=foreign,
                           database=tmp_path / "other.db", dispatch=dispatch)
            if field != "turn_id":
                with pytest.raises(BoxAuthError):
                    box.ensure_awake(foreign, reason="foreign")
        finally:
            bridge.close()

    asyncio.run(run())


def test_expiry_lost_reply_retry_and_restart_do_not_duplicate(box, tmp_path):
    effects = []

    async def dispatch(message):
        effects.append(message)
        return {"result": "committed"}

    async def run():
        bridge, _ = attach(box, tmp_path, dispatch)
        args = (bridge.handle, "exec", "b" * 32, {"op": "call"})
        first = await asyncio.to_thread(bridge.request, *args)
        assert first == {"result": "committed"}
        assert await asyncio.to_thread(bridge.request, *args) == first
        mismatch = await asyncio.to_thread(bridge.request, *args[:-1], {"op": "catalog"})
        assert "reused" in mismatch["error"]
        bridge.close()
        assert "expired" in (await asyncio.to_thread(bridge.request, *args))["error"]
        restarted, _ = attach(box, tmp_path, dispatch)
        try:
            assert await asyncio.to_thread(restarted.request, *args) == first
            assert len(effects) == 1
        finally:
            restarted.close()

    asyncio.run(run())


def test_cancellation_mid_call_real_box_keeps_unknown_receipt(box, tmp_path):
    entered = asyncio.Event()
    cancelled = asyncio.Event()
    effects = []

    async def dispatch(message):
        effects.append(message)
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            cancelled.set()

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        task = asyncio.create_task(tools.bash("cancel-exec", "ta search"))
        await asyncio.wait_for(entered.wait(), 10)
        status = box.exec_status(bridge.handle, "cancel-exec")
        for field, value in (("account_id", "owner-b"), ("command_center_id", "cc-b"),
                             ("turn_id", "turn-b")):
            with pytest.raises(BoxAuthError):
                box.send_stdin(replace(bridge.handle, **{field: value}),
                               status.exec_id, "d" * 32, b"{}\n")
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await asyncio.wait_for(cancelled.wait(), 2)
        bridge.close()
        import sqlite3
        with sqlite3.connect(tmp_path / "receipts.db") as db:
            request, answer = db.execute("SELECT request,answer FROM ta_receipts").fetchone()
        assert answer is None
        reopened, _ = attach(box, tmp_path, dispatch)
        try:
            result = await asyncio.to_thread(reopened.request, reopened.handle, "cancel-exec",
                                             request, {"op": "catalog"})
            assert "unknown" in result["error"]
            assert len(effects) == 1
        finally:
            reopened.close()

    asyncio.run(run())


def test_remote_box_uses_real_engine_ta_capabilities(world, tmp_path, monkeypatch):
    from types import SimpleNamespace

    from fastmcp import Client

    from tests.test_ta_capabilities_jail import _engine
    from tinyassets import engine_mcp_http as routes
    from tinyassets.agent_loop.box_ta import engine_ta
    from tinyassets.engine_tool_client import EngineToolSession

    server = _engine(monkeypatch, world)
    routes._write_routes(world.data_root, [SimpleNamespace(
        universe_id="u-alpha", owner="actor-a", port=8790, secret="s" * 43)])
    route = routes.read_engine_mcp_route(actor_id="actor-a", graph_id="u-alpha",
                                        root=world.data_root)
    assert route is not None

    provider = IsolatedBox(boxes_root=tmp_path / "boxes", state_dir=tmp_path / "state",
                           owner_of={"u-alpha": "actor-a"}.get, allow_unisolated=True)

    async def run():
        client = Client(server.mcp)
        await client.__aenter__()
        engine = EngineToolSession(client, route, world.data_root, ("bash",))
        handle = provider.bind("u-alpha", account_id="actor-a", turn_id="live")
        bridge = TurnBridge(owner="actor-a", center="u-alpha", turn="live", handle=handle,
                            database=tmp_path / "receipts.db",
                            dispatch=lambda message: engine_ta(engine, message))
        executor = BoxExecutor(provider, handle,
                               limits=ExecLimits(wall_seconds=60, output_bytes=2**20))
        executor.enable_ta(bridge)
        tools = BoxTools(executor)
        try:
            local = await engine_ta(engine, {"op": "catalog"})
            remote = await tools.bash("catalog", "ta search", timeout=60)
            assert remote.endswith("[exit code 0]"), remote
            found = json.loads(remote.removesuffix("[exit code 0]").strip())
            assert {x["name"] for x in found} == {x["name"] for x in local["capabilities"]}
            assert "read_graph" in {x["name"] for x in found}
            answer = await tools.bash("owner-read",
                'ta read_graph --json \'{"target":"connections"}\'', timeout=60)
            assert answer.endswith("[exit code 0]"), answer
            body = json.loads(answer.removesuffix("[exit code 0]").strip())
            assert not body.get("error"), body
        finally:
            bridge.close()
            await client.__aexit__(None, None, None)

    try:
        asyncio.run(run())
    finally:
        provider.close()


@pytest.mark.parametrize("lost", ["start", "stream", "send_stdin"])
def test_real_box_lost_replies_never_repeat_effect(box, tmp_path, monkeypatch, lost):
    effects = []

    async def dispatch(message):
        if message == {"op": "catalog"}:
            return {"capabilities": [{"name": "effect", "description": "effect"}],
                    "extension_roots": {}}
        effects.append(message)
        return {"result": "once"}

    original = getattr(box, "start_exec" if lost == "start" else lost)
    dropped = []
    if lost == "stream":
        def interrupted(*args, **kwargs):
            for event in original(*args, **kwargs):
                yield event
                if event.kind == "output" and not dropped:
                    dropped.append(True)
                    raise ConnectionError("lost stream after delivered frame")
        monkeypatch.setattr(box, lost, interrupted)
    else:
        def interrupted(*args, **kwargs):
            result = original(*args, **kwargs)
            if not dropped:
                dropped.append(True)
                raise ConnectionError("lost reply after real commit")
            return result
        monkeypatch.setattr(box, "start_exec" if lost == "start" else lost, interrupted)

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        try:
            first = await tools.bash("same-execution", "ta effect --json '{}'")
            assert first == '"once"\n[exit code 0]'
            again = await tools.bash("same-execution", "ta effect --json '{}'")
            assert again == first
            assert dropped == [True]
            assert len(effects) == 1
        finally:
            bridge.close()

    asyncio.run(run())


def test_remote_payload_cannot_select_foreign_authority(world, tmp_path, monkeypatch):
    from tests.test_ta_capabilities_jail import _engine
    from tinyassets.ta_capabilities import engine_dispatch

    server = _engine(monkeypatch, world)

    async def run():
        dispatch = await engine_dispatch(server)
        provider = IsolatedBox(boxes_root=tmp_path / "boxes", state_dir=tmp_path / "state",
                               owner_of={"u-alpha": "actor-a"}.get, allow_unisolated=True)
        handle = provider.bind("u-alpha", account_id="actor-a", turn_id="live")
        bridge = TurnBridge(owner="actor-a", center="u-alpha", turn="live", handle=handle,
                            database=tmp_path / "receipts.db",
                            dispatch=lambda message: asyncio.to_thread(dispatch, message))
        executor = BoxExecutor(provider, handle, limits=ExecLimits())
        executor.enable_ta(bridge)
        tools = BoxTools(executor)
        try:
            for field, value in (("owner", "actor-b"), ("universe", "u-bravo"),
                                 ("turn", "expired")):
                # Invoke the shipped CLI transport inside the REAL box. Extra
                # context must be refused by the actual capability dispatcher.
                code = ("import runpy; import shutil; "
                        "m=runpy.run_path(shutil.which('ta')); "
                        "print(m['remote'](" + repr({"op": "catalog", field: value}) + "))")
                result = await tools.bash("foreign-" + field, "python3 -c " + shlex.quote(code))
                assert "invalid ta request" in result
                assert result.endswith("[exit code 1]")
        finally:
            bridge.close()
            provider.close()

    asyncio.run(run())


def test_rpc_request_budget_is_separate_from_user_stdout(box, tmp_path):
    payload = "x" * 100_000

    async def dispatch(message):
        if message == {"op": "catalog"}:
            return {"capabilities": [{"name": "large", "description": "large request"}],
                    "extension_roots": {}}
        assert message["arguments"] == {"data": payload}
        return {"result": "accepted"}

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        tools._exec._limits = ExecLimits()  # real default: 64 KiB user output
        try:
            command = "ta large --json " + shlex.quote(json.dumps({"data": payload}))
            result = await tools.bash("large-request", command)
            assert result == '"accepted"\n[exit code 0]'
        finally:
            bridge.close()

    asyncio.run(run())


def test_large_rpc_values_cross_no_argv_or_bash_output_limit(box, tmp_path):
    async def dispatch(message):
        assert len(message["arguments"]["value"]) == 300_000
        return {"result": message["arguments"]["value"]}

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        code = ("import runpy,shutil; m=runpy.run_path(shutil.which('ta')); "
                "r=m['remote']({'op':'call','name':'echo','arguments':{'value':'x'*300000}}); "
                "print(len(r['result']))")
        try:
            result = await tools.bash("large-values", "python3 -c " + shlex.quote(code))
            assert result == "300000\n[exit code 0]"
        finally:
            bridge.close()

    asyncio.run(run())


def test_worker_startup_failure_is_distinct_and_runs_no_command(box, tmp_path, monkeypatch):
    original = box.start_exec

    def missing_python(handle, op_id, argv, **kwargs):
        return original(handle, op_id, ["/no-such-python", *argv[1:]], **kwargs)

    monkeypatch.setattr(box, "start_exec", missing_python)

    async def dispatch(message):
        raise AssertionError("startup failure cannot dispatch")

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        try:
            with pytest.raises(EngineToolError, match="remote_ta_worker_unavailable"):
                await tools.bash("no-worker", "touch /cc/should-not-exist")
            assert box.stat(bridge.handle, "/cc/should-not-exist") is None
        finally:
            bridge.close()

    asyncio.run(run())


def test_closed_turn_is_refused_from_real_box(box, tmp_path):
    async def dispatch(message):
        raise AssertionError("expired turn must never dispatch")

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        bridge.close()
        result = await tools.bash("expired", "ta search")
        assert "turn expired" in result
        assert result.endswith("[exit code 1]")

    asyncio.run(run())


def test_same_request_reconnect_and_changed_payload(box, tmp_path):
    effects = []

    async def dispatch(message):
        effects.append(message)
        return {"result": "once"}

    code = '''import json,os,socket
address='\\0'+os.environ['TA_SOCKET'][1:]
wire={'request':'a'*32,'message':{'op':'call','name':'effect','arguments':{}}}
answers=[]
for i in range(3):
 if i==2:wire['message']['arguments']={'different':True}
 with socket.socket(socket.AF_UNIX) as s:
  s.connect(address)
  s.sendall(json.dumps(wire).encode()+b'\\n')
  answers.append(json.loads(s.makefile('rb').readline()))
print(json.dumps(answers))
'''

    async def run():
        bridge, tools = attach(box, tmp_path, dispatch)
        try:
            result = await tools.bash("reconnect", "python3 -c " + shlex.quote(code))
            assert result.endswith("[exit code 0]"), result
            answers = json.loads(result.removesuffix("[exit code 0]").strip())
            assert answers[:2] == [{"result": "once"}] * 2
            assert "reused" in answers[2]["error"]
            assert len(effects) == 1
        finally:
            bridge.close()

    asyncio.run(run())
