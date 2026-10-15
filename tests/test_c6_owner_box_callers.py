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
    assert "tinyassets.role_video_codec" in binding.provider.calls[1][2]


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


def test_code_node_accepts_only_guest_workspace_path(tmp_path):
    from tinyassets.node_sandbox import WorkspaceLimits, WorkspaceMount
    from tinyassets.role_node import _run_box

    class GuestHandle(BoxHandle):
        @property
        def root(self):
            return "/home/owner/center"

    binding = _binding(tmp_path, b'{"success":true,"output_state":{}}\n')
    binding.handle = GuestHandle("center", "owner", 1, "turn")
    instance = SimpleNamespace(validate_source=lambda _: [], max_output_bytes=4096)
    request = dict(node_id="node", source_code="def run(state): return {}",
                   input_state={}, input_keys=[], output_keys=[], timeout=5)
    guest = WorkspaceMount("/home/owner/center/workspaces/repo",
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
