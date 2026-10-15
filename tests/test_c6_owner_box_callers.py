"""The cutover callers carry the session's existing provider and handle."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets.box_process import run_bound
from tinyassets.boxes.provider import BoxHandle, ExecEvent, ExecLimits
from tinyassets.control_plane.binding import SessionBinding, use_binding
from tinyassets.role_decoder import decode as decode_image
from tinyassets.role_video import frames as decode_video


class _Provider:
    def __init__(self, output=b"ok"):
        self.output = output
        self.calls = []

    def start_exec(self, handle, op_id, argv, **kwargs):
        self.calls.append((handle, op_id, argv, kwargs))
        return "execution"

    def stream(self, handle, exec_id, *, from_offset, timeout):
        assert from_offset == 0
        yield ExecEvent("output", offset=0, data=self.output)
        yield ExecEvent("exit", exit_code=0)


def _binding(tmp_path: Path, output=b"ok"):
    provider = _Provider(output)
    return SessionBinding(provider, BoxHandle("center", "owner", 1, "turn"),
                          ExecLimits(), tmp_path / "center")


def test_bound_process_uses_the_exact_session_handle(tmp_path):
    binding = _binding(tmp_path)
    done = run_bound(binding, "decode", ["python", "-m", "decoder"],
                     stdin=b"bytes", wall_seconds=5, output_bytes=100)
    handle, op_id, _, kwargs = binding.provider.calls[0]
    assert handle is binding.handle
    assert op_id.startswith("session:turn:")
    assert kwargs["stdin"] == b"bytes"
    assert done.stdout == b"ok"


def test_image_and_video_use_the_admitted_box(tmp_path):
    binding = _binding(tmp_path)
    with use_binding(binding):
        decode_image(b"png", "image/png", binding.universe_dir)
    assert binding.provider.calls[0][0] is binding.handle
    assert "tinyassets.tool_images" in binding.provider.calls[0][2]
    binding.provider.output = b'{"duration":1,"sizes":[8]}\n\x89PNG\r\n\x1a\n'
    with use_binding(binding):
        duration, frames = decode_video(b"video", binding.universe_dir)
    assert duration == 1 and len(frames) == 1
    assert "tinyassets.boxes.video_codec" in binding.provider.calls[1][2]


def test_image_and_video_callers_select_box_media_on_committed_layout(tmp_path, monkeypatch):
    from tinyassets import role_decoder, role_video, storage_layout, tool_images
    from tinyassets.ingestion import video_extractor

    binding = _binding(tmp_path, b'{"mime_type":"image/png","bytes":3}\npng')
    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    monkeypatch.setattr(role_decoder, "decode", lambda *args: pytest.fail("old decoder"))
    monkeypatch.setattr(role_video, "frames", lambda *args: pytest.fail("old video cell"))
    with use_binding(binding):
        assert tool_images._decode_in_child(b"source", "image/png",
                                            universe_dir=binding.universe_dir) == (
            {"mime_type": "image/png", "bytes": 3}, b"png")
        binding.provider.output = b'{"duration":1,"sizes":[8]}\n\x89PNG\r\n\x1a\n'
        description = video_extractor.extract_video_description(
            "clip.mp4", b"video", universe_dir=binding.universe_dir,
            describe_frame=lambda *args, **kwargs: "frame description",
        )
    assert "frame description" in description
    assert "tinyassets.boxes.video_codec" in binding.provider.calls[-1][2]


def test_preview_renderer_uses_box_process(tmp_path, monkeypatch):
    from tinyassets import ui_preview

    report = {"png_base64": "cG5n", "ui_id": "sample"}
    binding = _binding(tmp_path, json.dumps(report).encode() + b"\n")
    spec = {"base_path": str(tmp_path), "owner_user_id": "owner",
            "universe_id": "center", "hashes": {}, "ui_id": "sample"}
    with use_binding(binding):
        result = ui_preview._run_child(spec, 5, binding=binding)
    assert result["png"] == b"png"
    assert "tinyassets.ui_preview" in binding.provider.calls[0][2]


def test_workspace_git_projects_selected_grant_to_box(tmp_path, monkeypatch):
    from tinyassets import workspace_worker as worker

    binding = _binding(tmp_path, b'{"ok":true,"bytes":0}\n')
    grant = SimpleNamespace(grant_id="grant")
    view = SimpleNamespace(connection_id="connection", scopes=("git_read:a/b",),
                           git_host="example.com")
    monkeypatch.setattr(worker, "_authority", lambda request, op: (grant, view, "inc"))
    request = {"op": "ls_remote", "universe_dir": str(binding.universe_dir),
               "principal": "owner", "agent": "agent", "grant_id": "grant",
               "connection_id": "connection", "host": "example.com", "owner_repo": "a/b"}
    with use_binding(binding):
        answer = worker._boxed(request, "ls_remote", binding)
    assert answer["ok"] is True
    assert binding.provider.calls[0][0] is binding.handle
    assert "TINYASSETS_BOX_GIT" in binding.provider.calls[0][3]["env"]


def test_native_cli_receives_only_broker_selected_git_grants(tmp_path, monkeypatch):
    import asyncio

    from tinyassets import storage_layout
    from tinyassets.boxes.git_bridge import environment
    from tinyassets.broker import ledger_queries
    from tinyassets.control_plane import native_launch
    from tinyassets.external_agents import home, provider, runner
    from tinyassets.providers.base import ModelConfig

    binding = _binding(tmp_path)
    grant = SimpleNamespace(grant_id="git-grant")
    view = SimpleNamespace(connection_id="git-connection", scopes=("git_read:a/b",),
                           git_host="example.com")
    selected = ((grant, view, "incarnation"),)
    calls = []
    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    monkeypatch.setattr(home, "box_custody", lambda *args: SimpleNamespace(
        owner_user_id="owner", universe_dir=binding.universe_dir))
    monkeypatch.setattr(provider, "box_custody", home.box_custody)
    monkeypatch.setattr(ledger_queries, "selected_git_connections",
                        lambda *args, **kwargs: selected)

    def launch(custody, **kwargs):
        calls.append(kwargs)
        return SimpleNamespace(git_environment=environment(
            kwargs["connections"], kwargs["agent"]))

    monkeypatch.setattr(native_launch, "launcher", launch)

    async def run_job(agent, chosen, custody, **kwargs):
        return chosen.git_environment

    monkeypatch.setattr(runner, "run_job", run_job)
    budget = SimpleNamespace(usage_id="usage", check_scope=lambda *args: None)
    config = ModelConfig(invocation_owner_user_id="owner", request_budget=budget,
                         request_attempt=0)
    with use_binding(binding):
        env = asyncio.run(provider.ExternalAgentProvider("codex").complete(
            "prompt", "system", config, universe_dir=binding.universe_dir))
    assert calls[0]["connections"] is selected
    assert json.loads(env["TINYASSETS_BOX_GIT"])["connections"][0]["grant_id"] == "git-grant"


def test_code_node_uses_box_runner(tmp_path):
    from tinyassets.role_node import _run_box

    answer = {"success": True, "output_state": {"value": 4}, "user_stdout": ""}
    binding = _binding(tmp_path, json.dumps(answer).encode() + b"\n")
    instance = SimpleNamespace(validate_source=lambda _: [], max_output_bytes=4096)
    result = _run_box(
        instance, binding, invoke=None, workspace=None, node_id="node",
        source_code="def run(state): return {'value': 4}", input_state={},
        input_keys=[], output_keys=["value"], timeout=5,
    )
    assert result.success and result.output_state == {"value": 4}
    assert binding.provider.calls[0][0] is binding.handle
    assert binding.provider.calls[0][3]["interactive_stdin"] is True


def test_code_node_public_runner_requires_admitted_box(tmp_path, monkeypatch):
    from tinyassets import role_node, storage_layout
    from tinyassets.node_sandbox import NodeSandbox

    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    monkeypatch.setattr(role_node, "run", lambda *args, **kwargs: pytest.fail("old node cell"))
    binding = _binding(tmp_path, b'{"success":true,"output_state":{"value":4}}\n')
    node = NodeSandbox(universe_dir=binding.universe_dir)
    request = dict(node_id="node", source_code="def run(state): return {'value': 4}",
                   input_state={}, input_keys=[], output_keys=["value"], timeout=5)
    with pytest.raises(PermissionError, match="admitted owner box"):
        node.run_sync(**request)
    with use_binding(binding):
        result = node.run_sync(**request)
    assert result.success and result.output_state == {"value": 4}
    assert binding.provider.calls[0][0] is binding.handle


def test_code_node_accepts_only_guest_workspace_path(tmp_path):
    from tinyassets.node_sandbox import WorkspaceLimits, WorkspaceMount
    from tinyassets.role_node import _run_box

    binding = _binding(tmp_path, b'{"success":true,"output_state":{}}\n')
    instance = SimpleNamespace(validate_source=lambda _: [], max_output_bytes=4096)
    request = dict(node_id="node", source_code="def run(state): return {}",
                   input_state={}, input_keys=[], output_keys=[], timeout=5)
    guest = WorkspaceMount("/cc/center/workspaces/repo",
                           limits=WorkspaceLimits())
    _run_box(instance, binding, invoke=None, workspace=guest, **request)
    payload = json.loads(binding.provider.calls[0][3]["stdin"])
    assert payload["workspace"]["root"] == guest.bind_source
    host = WorkspaceMount("/proc/self/fd/17", pass_fds=(17,))
    with pytest.raises(ValueError, match="box-backed lease"):
        _run_box(instance, binding, invoke=None, workspace=host, **request)
    assert len(binding.provider.calls) == 1


def test_workspace_git_refuses_the_cell_on_committed_box_layout(tmp_path, monkeypatch):
    from tinyassets import storage_layout, workspace_git

    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    with pytest.raises(workspace_git.WorkspaceGitError, match="admitted box"):
        workspace_git.run_git(
            ["status"], cwd=tmp_path, home_dir=tmp_path, path="/usr/bin",
            timeout_s=5,
        )


def test_startup_workspace_reconciliation_binds_an_owner_box(tmp_path, monkeypatch):
    from tinyassets import storage_layout, workspace_worker
    from tinyassets.agent_loop import served_chat
    from tinyassets.control_plane.binding import current_binding

    class Admitted:
        def bind(self, center, *, account_id, turn_id):
            assert center == "center" and account_id == "owner"
            return BoxHandle(center, account_id, 1, turn_id)

    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    monkeypatch.setattr(served_chat, "configured_box_provider", lambda: (Admitted(), None))

    def boxed(request, operation, binding):
        assert operation == "ls_remote"
        assert current_binding() is binding
        assert binding.handle.account_id == "owner"
        return {"ok": True, "observed_sha": ""}

    monkeypatch.setattr(workspace_worker, "_boxed", boxed)
    answer = workspace_worker.execute_workspace_operation({
        "op": "ls_remote", "universe_dir": str(tmp_path / "center"),
        "principal": "owner", "agent": "workspace-intent-test",
        "grant_id": "grant", "connection_id": "connection",
        "owner_repo": "owner/repo", "remote_ref": "refs/heads/main",
    })
    assert answer == {"ok": True, "observed_sha": ""}
    assert current_binding() is None


def test_workspace_pool_returns_guest_lease_paths(tmp_path, monkeypatch):
    from tinyassets import storage_layout
    from tinyassets.effectors import workspace

    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    center = tmp_path / "center"
    assert workspace.scratch_pool_root(center) == Path("/cc/center/workspaces/scratch")
    assert workspace.universe_workspace_root(center) == Path("/cc/center")
    assert workspace._lease_names(
        center, Path("/cc/center/workspaces/scratch/0123456789abcdef"),
    ) == (["workspaces", "scratch"], "0123456789abcdef")


def test_guest_lease_uses_center_relative_box_file_api(tmp_path, monkeypatch):
    from tinyassets import storage_layout
    from tinyassets.boxes.provider import DirEntry, DirPage, FileStat
    from tinyassets.effectors import workspace

    monkeypatch.setattr(storage_layout, "ACTIVE_BOX_LAYOUT", "owner-box")
    binding = _binding(tmp_path)
    lease = Path("/cc/center/workspaces/scratch/0123456789abcdef")
    seen = []

    def stat(handle, path):
        seen.append(path)
        return FileStat(path, "dir", 0, 0)

    def listing(handle, path, *, cursor):
        seen.append(path)
        return DirPage((DirEntry("README.md", "file", 7),), None)

    binding.provider.stat = stat
    binding.provider.list = listing
    binding.provider.committed_generation = lambda handle: 1
    with use_binding(binding):
        assert workspace._open_cell_lease(lease) == (None, None)
        assert workspace._box_tree_bytes(lease, 1024) == 7
    assert seen == ["/cc/workspaces/scratch/0123456789abcdef",
                    "/cc/workspaces/scratch/0123456789abcdef/repo",
                    "/cc/workspaces/scratch/0123456789abcdef"]
    with pytest.raises(workspace._Refused):
        workspace._box_provider_path(Path("/cc/center/../other/file"), binding.universe_dir)


def test_workspace_sweep_removes_only_admitted_guest_lease(tmp_path, monkeypatch):
    from tinyassets import universe_owner
    from tinyassets.agent_loop import served_chat
    from tinyassets.boxes.provider import FileStat
    from tinyassets.workspace_fs import BoxPoolFilesystem, UnsafePoolPath

    lease = Path("/cc/center/workspaces/scratch/0123456789abcdef")

    class Provider:
        present = True

        def bind(self, center, *, account_id, turn_id):
            assert (center, account_id) == ("center", "owner")
            assert turn_id
            return BoxHandle(center, account_id, 1, turn_id)

        def stat(self, handle, path):
            assert handle.account_id == "owner"
            assert path == "/cc/workspaces/scratch/0123456789abcdef"
            return FileStat(path, "dir", 0, 0) if self.present else None

        def remove(self, handle, op_id, path):
            assert path == "/cc/workspaces/scratch/0123456789abcdef"
            assert op_id.startswith("workspace-sweep:")
            self.present = False

    provider = Provider()
    monkeypatch.setattr(universe_owner, "owner_of", lambda root, center: "owner")
    monkeypatch.setattr(served_chat, "configured_box_provider", lambda: (provider, None))
    fs = BoxPoolFilesystem(tmp_path)
    assert fs.exists(lease)
    assert fs.owner_scoped(lease)
    fs.remove_tree_no_follow(lease)
    assert not fs.exists(lease)
    with pytest.raises(UnsafePoolPath):
        fs.remove_tree_no_follow(Path("/cc/../other/workspaces/x"))
