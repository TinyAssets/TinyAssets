"""A persistent Linux owner process keeps local tools inside one OS boundary."""
import json
import select
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = [pytest.mark.real_jail, pytest.mark.role_split]

SERVER = r'''
import asyncio, json, os, sys
from pathlib import Path
from tinyassets import owner_runtime
from tinyassets.agent_loop.owner_tools import OwnerTools
from tinyassets.boxes.boxd import OwnerBoxd
root = Path("/home/owner")
core = OwnerBoxd(owner_id="alice", owner_root=root, state_dir=root / ".owner-boxd")
owner_runtime._runtime = owner_runtime.OwnerRuntime("alice", os.getuid(), root, os.getpid(), core)
tools = OwnerTools(root / "center")
async def dispatch(message):
    if message == {"op": "catalog"}:
        return {"capabilities": [{"name": "owner.value", "description": "value"}],
                "extension_roots": {}}
    return {"result": {"owner": "alice", "daemon_pid": os.getpid()}}
async def command(doc):
    owner_runtime.require(root / "center", doc.get("owner", "alice"))
    bridge = tools.enable_ta(dispatch=dispatch, turn=doc["turn"])
    try:
        return await tools.bash(doc["turn"], doc["command"], timeout=5)
    finally:
        bridge.close()
try:
    for line in sys.stdin:
        try:
            result = asyncio.run(command(json.loads(line)))
            print(json.dumps({"pid": os.getpid(), "output": result}), flush=True)
        except Exception as exc:
            print(json.dumps({"error": type(exc).__name__}), flush=True)
finally:
    core.close()
'''


@pytest.fixture
def persistent_owner(tmp_path, monkeypatch):
    bwrap = shutil.which("bwrap")
    assert bwrap, "the Linux oracle must provide bubblewrap"
    root = tmp_path / "alice"
    (root / "center").mkdir(parents=True)
    foreign = tmp_path / "bob-secret"
    foreign.write_text("FOREIGN-OWNER-MARKER")
    argv = [bwrap, "--die-with-parent", "--new-session", "--unshare-all", "--clearenv"]
    for path in ("/usr", "/bin", "/lib", "/lib64"):
        if Path(path).exists():
            argv += ["--ro-bind", path, path]
    argv += ["--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/home",
             "--bind", str(root), "/home/owner", "--ro-bind", str(Path.cwd()), "/app",
             "--chdir", "/home/owner", "--setenv", "PYTHONPATH", "/app",
             "--setenv", "PATH", "/usr/local/bin:/usr/bin:/bin",
             "--setenv", "HOME", "/home/owner", "--", sys.executable, "-u", "-c", SERVER]
    monkeypatch.setenv("OWNER_PROOF_SECRET", "synthetic-platform-only-value")
    process = subprocess.Popen(argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                               stderr=subprocess.PIPE, text=True)
    ordinal = 0

    def call(command, **fields):
        nonlocal ordinal
        ordinal += 1
        process.stdin.write(json.dumps({"command": command, "turn": str(ordinal), **fields}) + "\n")
        process.stdin.flush()
        assert select.select([process.stdout], [], [], 15)[0], "persistent owner did not reply"
        line = process.stdout.readline()
        assert line, process.stderr.read() if process.poll() is not None else "owner closed output"
        return json.loads(line)

    try:
        yield call, process, root, foreign
    finally:
        process.stdin.close()
        try:
            process.wait(5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(5)
        error = process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        assert process.returncode == 0, error


def test_local_commands_share_one_persistent_owner_daemon(persistent_owner):
    call, process, root, foreign = persistent_owner
    first = call("echo own > marker; pwd")
    second = call("cat marker; test ! -e " + str(foreign))
    assert first["pid"] == second["pid"]
    assert first["output"] == "/home/owner/center\n[exit code 0]"
    assert second["output"] == "own\n[exit code 0]"
    assert (root / "center/marker").read_text() == "own\n"
    assert foreign.read_text() == "FOREIGN-OWNER-MARKER"
    assert process.poll() is None


def test_local_ta_runs_under_the_same_daemon_and_foreign_owner_is_refused(persistent_owner):
    call, process, root, foreign = persistent_owner
    reply = call("ta owner.value --json '{}' > value.json")
    value = json.loads((root / "center/value.json").read_text())
    assert reply["output"].endswith("[exit code 0]")
    assert value == {"owner": "alice", "daemon_pid": reply["pid"]}
    assert call("touch forbidden", owner="bob") == {"error": "PermissionError"}
    assert not (root / "center/forbidden").exists()
    assert foreign.read_text() == "FOREIGN-OWNER-MARKER"
    assert process.poll() is None


def test_platform_environment_is_absent_from_persistent_owner(persistent_owner):
    call, process, root, foreign = persistent_owner
    reply = call("env; ls /tmp; test ! -e " + str(foreign))
    assert "PYTHONPATH=/app" in reply["output"]
    assert "HOME=/home/owner" in reply["output"]
    assert "FOREIGN-OWNER-MARKER" not in reply["output"]
    assert "TINYASSETS_DATA_DIR" not in reply["output"]
    assert "OWNER_PROOF_SECRET" not in reply["output"]
    assert "synthetic-platform-only-value" not in reply["output"]
    assert reply["output"].endswith("[exit code 0]")
    assert process.poll() is None
