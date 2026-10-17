"""Owner features execute in the startup-admitted owner daemon."""
import os
import subprocess
from types import SimpleNamespace

import pytest

from tinyassets import owner_runtime
from tinyassets.boxes.boxd import OwnerBoxd
from tinyassets.boxes.provider import BoxError
from tinyassets.owner_process import OwnerProcessCancelled

pytestmark = pytest.mark.role_split


@pytest.fixture
def owner(tmp_path, monkeypatch):
    root = tmp_path / "owner"
    home = root / "center"
    home.mkdir(parents=True)
    core = OwnerBoxd(owner_id="alice", owner_root=root, state_dir=tmp_path / "state")
    runtime = owner_runtime.OwnerRuntime("alice", os.getuid(), root, os.getpid(), core)
    monkeypatch.setattr(owner_runtime, "_runtime", runtime)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root))
    try:
        yield SimpleNamespace(runtime=runtime, home=home, core=core)
    finally:
        core.close()


def test_local_process_uses_owner_cwd_and_input(owner):
    assert owner_runtime.require(owner.home, "alice") is owner.runtime
    done = owner.core.run(["sh", "-c", "pwd; cat"], cwd=owner.home,
                          stdin=b"bytes", wall_seconds=5, output_bytes=1000)
    assert done.returncode == 0
    assert done.stdout == str(owner.home).encode() + b"\nbytes"
    assert not hasattr(owner.core, "start_exec")
    assert not owner.core._running
    with pytest.raises(PermissionError):
        owner_runtime.require(owner.home, "bob")
    with pytest.raises(ValueError):
        owner.core.run(["true"], cwd=owner.home.parent.parent,
                       wall_seconds=5, output_bytes=100)


@pytest.mark.parametrize("command,reason", [("sleep 30", "timeout"),
                                           ("head -c 100000 /dev/zero", "output_limit")])
def test_process_bounds_kill_and_reap(owner, command, reason):
    with pytest.raises(BoxError, match=reason):
        owner.core.run(["sh", "-c", command], cwd=owner.home,
                       wall_seconds=.15, output_bytes=100)
    assert not owner.core._running
    assert owner.core._state.pending("alice") == 0


def test_interactive_frames_and_cancel(owner):
    seen = []

    def answer(line):
        seen.append(line)
        return b"reply" if line == b"rpc" else None

    done = owner.core.run(["sh", "-c", "echo rpc; read value; echo $value"],
                          cwd=owner.home, wall_seconds=5, output_bytes=100, line_handler=answer)
    assert done.stdout == b"reply\n"
    assert seen == [b"rpc", b"reply"]
    with pytest.raises(OwnerProcessCancelled):
        owner.core.run(["sleep", "30"], cwd=owner.home, wall_seconds=5,
                       output_bytes=100, should_cancel=lambda: True)
    assert not owner.core._running


def test_media_uses_local_core(owner, monkeypatch):
    from tinyassets.boxes import media
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        output = (b'{"duration":1,"sizes":[8]}\n\x89PNG\r\n\x1a\n'
                  if "tinyassets.boxes.video_codec" in argv else b"image")
        return subprocess.CompletedProcess(argv, 0, output, b"")

    monkeypatch.setattr(owner.core, "run", run)
    assert media.decode_image(b"source", "image/png", owner.home).stdout == b"image"
    duration, frames = media.frames(b"video", owner.home)
    assert duration == 1 and frames == [b"\x89PNG\r\n\x1a\n"]
    assert all(call[1]["cwd"] == owner.home for call in calls)
    assert calls[0][1]["stdin"] == b"source"
    assert calls[1][1]["stdin"] == b"video"
    assert all(call[1]["output_bytes"] > 0 for call in calls)


def test_preview_uses_local_owner_process(owner, monkeypatch):
    from tinyassets import ui_preview
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, b'{"png_base64":"cG5n"}\n', b"")

    monkeypatch.setattr(owner.core, "run", run)
    spec = {"base_path": str(owner.home.parent), "owner_user_id": "alice",
            "universe_id": "center", "hashes": {}, "ui_id": "sample"}
    result = ui_preview._run_child(spec, 5, runtime=owner.runtime, universe_dir=owner.home)
    assert result["png"] == b"png"
    assert "tinyassets.ui_preview" in calls[0][0]
    assert calls[0][1]["cwd"] == owner.home
    with pytest.raises(ui_preview.PreviewUnavailable, match="scope"):
        ui_preview._run_child(dict(spec, owner_user_id="bob"), 5,
                              runtime=owner.runtime, universe_dir=owner.home)
    assert len(calls) == 1


def test_code_node_executes_in_local_owner_box(owner):
    from tinyassets.boxes.node import NodeSandbox
    node = NodeSandbox(universe_dir=owner.home)
    result = node.run_sync(node_id="node", source_code="def run(state): return {'value': 4}",
                           input_state={}, input_keys=[], output_keys=["value"], timeout=5)
    assert result.success, result.error
    assert result.output_state == {"value": 4}
    assert result.undeclared == []
    assert not owner.core._running


def test_workspace_paths_measure_and_sweep(owner):
    from tinyassets.effectors import workspace
    from tinyassets.workspace_fs import BoxPoolFilesystem, UnsafePoolPath
    lease = owner.home / "workspaces/scratch/0123456789abcdef"
    (lease / "repo").mkdir(parents=True)
    (lease / "repo/README.md").write_text("content")
    assert workspace.scratch_pool_root(owner.home) == owner.home / "workspaces/scratch"
    assert workspace.universe_workspace_root(owner.home) == owner.home
    assert workspace._lease_names(owner.home, lease) == (
        ["workspaces", "scratch"], "0123456789abcdef")
    assert workspace._box_provider_path(lease, owner.home) == (
        "/cc/center/workspaces/scratch/0123456789abcdef")
    assert workspace._open_cell_lease(lease) == (None, None)
    assert workspace._box_tree_bytes(lease, 1024) == 7
    fs = BoxPoolFilesystem(owner.home.parent)
    assert fs.exists(lease)
    assert fs.owner_scoped(lease)
    fs.remove_tree_no_follow(lease)
    assert not fs.exists(lease)
    with pytest.raises(UnsafePoolPath):
        fs.remove_tree_no_follow(owner.home.parent / "../foreign/workspaces/x")
    with pytest.raises(workspace._Refused):
        workspace._box_provider_path(owner.home / "../other/file", owner.home)


def test_wake_probe_has_owner_scope_and_limits(owner):
    from tinyassets.wake_conditions import run_probe
    assert run_probe(owner.home, {"owner": "alice"}, {"command": "true"}) is True
    assert run_probe(owner.home, {"owner": "alice"}, {"command": "false"}) is False
    assert run_probe(owner.home, {"owner": "alice"}, {
        "command": "sleep 30", "timeout_seconds": .05}) is False
    with pytest.raises(PermissionError):
        run_probe(owner.home, {"owner": "bob"}, {"command": "touch foreign"})
    assert not (owner.home / "foreign").exists()


def test_startup_workspace_operation_uses_runtime(owner, monkeypatch):
    from tinyassets import workspace_worker
    seen = []

    def boxed(request, operation, runtime):
        seen.append(runtime)
        assert operation == "create"
        assert request["principal"] == "alice"
        return {"ok": True, "bytes": 0}

    monkeypatch.setattr(workspace_worker, "_boxed", boxed)
    request = {"op": "create", "universe_dir": str(owner.home), "principal": "alice"}
    assert workspace_worker.execute_workspace_operation(request) == {"ok": True, "bytes": 0}
    assert seen == [owner.runtime]
    monkeypatch.setattr(owner_runtime, "_runtime", None)
    answer = workspace_worker.execute_workspace_operation(request)
    assert answer["ok"] is False
    assert "startup-admitted" in answer["error"]
    assert len(seen) == 1


@pytest.mark.parametrize("principal", ["provider|opaque:subject", "opaque/subject@example.test"])
def test_owner_boxd_principal_is_a_journal_key_not_a_directory(tmp_path, principal):
    root = tmp_path / "owner"
    root.mkdir()
    core = OwnerBoxd(owner_id=principal, owner_root=root, state_dir=tmp_path / "state")
    try:
        handle = core.bind(principal, session_id="local")
        core.write(handle, "write", "/cc/notes.txt", b"own", max_bytes=10)
        assert (root / "notes.txt").read_bytes() == b"own"
        assert core.read(handle, "/cc/notes.txt", max_bytes=10).data == b"own"
        assert handle.account_id == principal
        assert core.committed_generation(handle) == 1
        with pytest.raises(BoxError):
            core.bind("foreign", session_id="foreign")
        assert [p.name for p in root.iterdir()] == ["notes.txt"]
    finally:
        core.close()
