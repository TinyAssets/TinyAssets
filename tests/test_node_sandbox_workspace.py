"""The sandbox's ``ws`` binding: one extra bind, and a capability with edges.

Two layers are under test and they fail differently. The argv layer is a pure
function, so it is asserted EXACTLY -- a jail that quietly gains a mount is the
defect this catches, and a loose assertion would not. The runner layer is driven
through a real child process with :class:`PlainSubprocessLauncher` and a temp
directory standing in for ``/workspace``: the code that runs is the code that
ships, on every platform, which is why the runner is a string with an injectable
root rather than a module the test could monkeypatch into something else.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import pytest

from tinyassets import graph_compiler as gc
from tinyassets.branches import NodeDefinition
from tinyassets.effectors import EffectChain
from tinyassets.node_sandbox import (
    WORKSPACE_MOUNT_POINT,
    NodeSandbox,
    PlainSubprocessLauncher,
    WorkspaceLimits,
    WorkspaceMount,
    _bwrap_argv,
    _launcher_for_workspace,
    _validate_workspace_bind,
)

GIT_BINARY = shutil.which("git")
BWRAP = shutil.which("bwrap")
PY = sys.executable


# ──────────────────────────────────────────────────────────────────────────────
# The argv: exactly one extra bind, or none
# ──────────────────────────────────────────────────────────────────────────────


def _argv(**kwargs) -> list[str]:
    """The jail prefix with a filesystem where only /usr and /bin exist."""
    return _bwrap_argv(
        exists=lambda p: p in ("/usr", "/bin"),
        bwrap_path="/usr/bin/bwrap",
        realpath=lambda p: p,
        **kwargs,
    )


BASE_ARGV = [
    "/usr/bin/bwrap",
    "--die-with-parent",
    "--new-session",
    "--unshare-all",
    "--clearenv",
    "--setenv", "HOME", "/tmp",
    "--setenv", "PATH", "/usr/bin:/bin",
    "--setenv", "PYTHONIOENCODING", "utf-8",
    "--setenv", "PYTHONDONTWRITEBYTECODE", "1",
    "--dev", "/dev",
    "--proc", "/proc",
    "--tmpfs", "/tmp",
]


def test_argv_without_a_workspace_is_unchanged() -> None:
    assert _argv() == [
        *BASE_ARGV,
        "--chdir", "/tmp",
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--",
    ]


def test_argv_with_a_workspace_adds_exactly_one_bind() -> None:
    argv = _argv(
        workspace_bind="/srv/pool/lease-1/gen-2/repo",
        allowed_workspace_roots=("/srv/pool",),
    )
    assert argv == [
        *BASE_ARGV,
        "--ro-bind", "/usr", "/usr",
        "--ro-bind", "/bin", "/bin",
        "--bind", "/srv/pool/lease-1/gen-2/repo", "/workspace",
        "--chdir", "/workspace",
        "--",
    ]
    # The working directory moved; /tmp is still a private tmpfs and HOME.
    assert argv.count("--chdir") == 1
    assert "/tmp" not in argv[argv.index("--chdir") + 1]
    assert argv.count("--bind") == 1
    assert "--share-net" not in argv


def test_the_workspace_bind_is_the_only_difference() -> None:
    plain = _argv()
    bound = _argv(
        workspace_bind="/srv/pool/x", allowed_workspace_roots=("/srv/pool",)
    )
    assert [item for item in plain if item not in bound] == []
    assert [item for item in bound if item not in plain] == [
        "--bind", "/srv/pool/x", "/workspace", "/workspace",
    ]


REFUSED_BINDS: list[tuple[str, str, tuple[str, ...]]] = [
    ("relative", "pool/lease/repo", ("/srv/pool",)),
    ("windows_drive", "C:/pool/repo", ("/srv/pool",)),
    ("dot_dot", "/srv/pool/../etc", ("/srv/pool",)),
    ("outside_root", "/etc", ("/srv/pool",)),
    ("root_prefix_not_boundary", "/srv/poolshop/repo", ("/srv/pool",)),
    ("never_bind_data", "/data/universes/u1", ("/srv/pool",)),
    ("no_roots_at_all", "/srv/pool/repo", ()),
    ("empty", "", ("/srv/pool",)),
    ("nul", "/srv/pool/re\x00po", ("/srv/pool",)),
]


@pytest.mark.parametrize(
    ("path", "roots"),
    [pytest.param(path, roots, id=name) for name, path, roots in REFUSED_BINDS],
)
def test_refused_workspace_binds(path: str, roots: tuple[str, ...]) -> None:
    with pytest.raises(ValueError):
        _argv(workspace_bind=path, allowed_workspace_roots=roots)


def test_a_universe_workspaces_root_under_data_is_bindable() -> None:
    """``/data`` is never bound *by default*; a root the caller vouches for is."""
    argv = _argv(
        workspace_bind="/data/universes/u1/workspaces/repo/gen-3",
        allowed_workspace_roots=("/data/universes/u1/workspaces",),
    )
    assert "--bind" in argv
    assert argv[argv.index("--bind") + 2] == WORKSPACE_MOUNT_POINT


def test_a_bind_that_only_resolves_into_a_root_still_refuses() -> None:
    """Both checks earn their keep, and in opposite directions.

    The realpath check catches a path INSIDE a root that leaves it. This one
    catches the reverse: a path outside every root that resolves into one, ie
    a symlink pointing into the pool. Neither is a lease the caller vouched
    for, and only the literal check refuses this shape.
    """
    with pytest.raises(ValueError, match="not beneath an allowed root"):
        _validate_workspace_bind(
            "/tmp/sneaky", ("/srv/pool",), lambda p: "/srv/pool/lease-1"
        )


def test_a_symlinked_bind_source_that_leaves_the_root_refuses() -> None:
    with pytest.raises(ValueError, match="resolves to"):
        _validate_workspace_bind(
            "/srv/pool/lease-1",
            ("/srv/pool",),
            lambda p: "/etc/shadow-dir",
        )


def test_a_held_directory_handle_is_an_accepted_spelling() -> None:
    """No root and no realpath: what vouches for it is the inherited fd."""
    resolved = _validate_workspace_bind(
        "/proc/self/fd/7",
        (),
        lambda p: pytest.fail("a descriptor bind must not be realpath-ed"),
        pass_fds=(7,),
    )
    assert resolved == "/proc/self/fd/7"


def test_a_held_directory_handle_needs_that_descriptor_inherited() -> None:
    """``/proc/self/fd/7`` in a process that was not given fd 7 names whatever
    that process has open at 7. The bind is only meaningful when the child
    inherits it, so the pass_fds list is what admits the spelling.
    """
    with pytest.raises(ValueError, match="does not inherit"):
        _validate_workspace_bind(
            "/proc/self/fd/7", ("/srv/pool",), lambda p: p, pass_fds=(3,)
        )
    with pytest.raises(ValueError, match="does not inherit"):
        _validate_workspace_bind("/proc/self/fd/7", (), lambda p: p)


def test_empty_roots_refuse_by_name() -> None:
    """Fail closed, and say which condition closed it."""
    with pytest.raises(ValueError, match="no allowed workspace roots"):
        _validate_workspace_bind("/srv/pool/repo", (), lambda p: p)


def test_the_default_factory_hands_the_descriptors_to_the_launcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The production factory, not a test double: the double is where the
    descriptors were being dropped unnoticed."""
    from tinyassets import node_sandbox

    monkeypatch.setattr(
        node_sandbox, "_probe", lambda: {"bwrap_available": True}
    )
    launcher = node_sandbox._default_workspace_launcher(
        WorkspaceMount(
            bind_source="/proc/self/fd/9",
            pass_fds=(9,),
            allowed_roots=("/srv/pool",),
        )
    )
    assert launcher.pass_fds == (9,)
    assert launcher.workspace_bind == "/proc/self/fd/9"
    assert launcher.allowed_workspace_roots == ("/srv/pool",)
    argv = launcher.build_argv("print(1)", ["30", "1", ""])
    assert argv[argv.index("--bind") + 1] == "/proc/self/fd/9"


def test_the_default_factory_refuses_without_a_sandbox(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tinyassets import node_sandbox
    from tinyassets.providers.base import SandboxUnavailableError

    monkeypatch.setattr(
        node_sandbox, "_probe", lambda: {"bwrap_available": False, "reason": "no bwrap"}
    )
    with pytest.raises(SandboxUnavailableError):
        node_sandbox._default_workspace_launcher(
            WorkspaceMount(bind_source="/srv/pool/x", allowed_roots=("/srv/pool",))
        )


def test_a_descriptor_bind_reaches_both_the_argv_and_the_child() -> None:
    """The bind names an fd; the child must actually be given that fd."""
    from tinyassets.node_sandbox import BwrapLauncher

    mount = WorkspaceMount(bind_source="/proc/self/fd/9", pass_fds=(9,))
    launcher = BwrapLauncher(bwrap_path="/usr/bin/bwrap").for_workspace(mount)
    assert launcher.pass_fds == (9,)
    argv = launcher.build_argv("print(1)", ["30", "1", ""])
    index = argv.index("--bind")
    assert argv[index : index + 3] == ["--bind", "/proc/self/fd/9", "/workspace"]
    assert argv[argv.index("--chdir") + 1] == "/workspace"


def test_a_descriptor_bind_the_child_does_not_inherit_refuses() -> None:
    from tinyassets.node_sandbox import BwrapLauncher

    mount = WorkspaceMount(bind_source="/proc/self/fd/9", pass_fds=())
    launcher = BwrapLauncher(bwrap_path="/usr/bin/bwrap").for_workspace(mount)
    with pytest.raises(ValueError, match="does not inherit"):
        launcher.build_argv("print(1)", ["30", "1", ""])


def test_the_mount_reaches_the_launcher_not_just_the_runner() -> None:
    """A default-resolved jail used to bind NOTHING: the mount reached the
    runner (told its root is /workspace) but never the launcher, so the node
    found an empty mount point. run_sync specialises the launcher now.
    """
    from tinyassets.node_sandbox import BwrapLauncher

    plain = BwrapLauncher(bwrap_path="/usr/bin/bwrap")
    assert "--bind" not in plain.build_argv("x", ["30", "1", ""])

    # The path form, checked on the launcher rather than through argv: a POSIX
    # bind path does not survive a Windows host's realpath, and what is under
    # test here is that the mount reaches the launcher at all.
    path_mount = WorkspaceMount(
        bind_source="/srv/pool/lease-1", allowed_roots=("/srv/pool",)
    )
    bound = _launcher_for_workspace(plain, path_mount)
    assert bound.workspace_bind == "/srv/pool/lease-1"
    assert bound.allowed_workspace_roots == ("/srv/pool",)
    assert bound is not plain

    # The descriptor form all the way to argv: it never touches realpath.
    fd_bound = _launcher_for_workspace(
        plain, WorkspaceMount(bind_source="/proc/self/fd/9", pass_fds=(9,))
    )
    argv = fd_bound.build_argv("x", ["30", "1", ""])
    assert argv[argv.index("--bind") + 1] == "/proc/self/fd/9"


def test_the_plain_launcher_refuses_a_descriptor_it_will_not_inherit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The refusal is about INHERITANCE, not about descriptors.

    `/proc/self/fd/<n>` in a process that was not given `n` names whatever that
    process happens to hold there, which is some other directory entirely. On
    Windows it is refused whatever the descriptors say: no /proc, no inheritance.
    """
    from tinyassets import node_sandbox
    from tinyassets.providers.base import SandboxUnavailableError

    with pytest.raises(SandboxUnavailableError, match="does not inherit"):
        PlainSubprocessLauncher().for_workspace(
            WorkspaceMount(bind_source="/proc/self/fd/9", pass_fds=())
        )
    with pytest.raises(SandboxUnavailableError, match="does not inherit"):
        PlainSubprocessLauncher().for_workspace(
            WorkspaceMount(bind_source="/proc/self/fd/9", pass_fds=(3,))
        )

    monkeypatch.setattr(node_sandbox.sys, "platform", "win32")
    with pytest.raises(SandboxUnavailableError, match="Windows"):
        PlainSubprocessLauncher().for_workspace(
            WorkspaceMount(bind_source="/proc/self/fd/9", pass_fds=(9,))
        )


@pytest.mark.skipif(os.name != "posix", reason="/proc and fd inheritance are POSIX")
def test_the_plain_launcher_honours_a_descriptor_the_child_inherits(
    workspace: Path,
) -> None:
    """No mount is needed: the CHILD holds the descriptor, and Popen chdirs
    after the fork, so `/proc/self/fd/<n>` names the directory the checkout
    opened. Asserted by inode, not by string, because the point is that it is
    the same directory rather than the same spelling.
    """
    handle = os.open(str(workspace), os.O_RDONLY)
    try:
        mount = WorkspaceMount(
            bind_source=f"/proc/self/fd/{handle}", pass_fds=(handle,)
        )
        launcher = PlainSubprocessLauncher().for_workspace(mount)
        assert launcher.pass_fds == (handle,)
        assert launcher.workspace_root() == f"/proc/self/fd/{handle}"

        result = NodeSandbox(launcher=launcher, timeout=30).run_sync(
            node_id="ws-node",
            source_code=(
                "def run(state):\n"
                "    return {'result': ws.read('README.md')}\n"
            ),
            input_state={},
            input_keys=[],
            output_keys=["result"],
            timeout=30,
            workspace=mount,
        )
        assert result.success is True, result.error
        assert result.output_state["result"].startswith("hello workspace")

        wanted = os.stat(str(workspace))
        seen = os.stat(f"/proc/self/fd/{handle}")
        assert (seen.st_dev, seen.st_ino) == (wanted.st_dev, wanted.st_ino)
    finally:
        os.close(handle)


def test_run_sync_specialises_the_launcher_for_the_mount(workspace: Path) -> None:
    """Without this the bind never leaves the mount: argv would carry none."""
    seen: dict[str, object] = {}

    class _Capture(PlainSubprocessLauncher):
        def build_argv(self, runner_script: str, args: list[str]) -> list[str]:
            seen["workspace_bind"] = self.workspace_bind
            return [PY, "-c", "raise SystemExit(0)"]

    NodeSandbox(launcher=_Capture(), timeout=5).run_sync(
        node_id="n",
        source_code="def run(state):\n    return {}\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=5,
        workspace=WorkspaceMount(bind_source=str(workspace)),
    )
    assert seen["workspace_bind"] == str(workspace)


def test_the_inherited_descriptors_reach_popen(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """argv naming /proc/self/fd/N is meaningless unless the child is given N."""
    from tinyassets import node_sandbox

    captured: dict[str, object] = {}
    real_popen = subprocess.Popen

    class _Launcher(PlainSubprocessLauncher):
        def __init__(self, workspace_bind: str | None = None) -> None:
            super().__init__(workspace_bind=workspace_bind)
            self.pass_fds = (7,)

        def build_argv(self, runner_script: str, args: list[str]) -> list[str]:
            return [PY, "-c", "raise SystemExit(0)"]

    def fake_popen(argv, **kwargs):
        captured.update(kwargs)
        kwargs.pop("pass_fds", None)
        return real_popen(argv, **kwargs)

    monkeypatch.setattr(node_sandbox.sys, "platform", "linux")
    monkeypatch.setattr(node_sandbox.subprocess, "Popen", fake_popen)
    NodeSandbox(launcher=_Launcher(), timeout=5).run_sync(
        node_id="n",
        source_code="def run(state):\n    return {}\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=5,
        workspace=WorkspaceMount(bind_source=str(workspace), pass_fds=(7,)),
    )
    assert captured["pass_fds"] == (7,)
    assert captured["close_fds"] is True


def test_inherited_descriptors_are_refused_on_windows(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Popen cannot pass a descriptor there; dropping it silently would leave a
    bind naming an fd the child never got."""
    from tinyassets import node_sandbox
    from tinyassets.providers.base import SandboxUnavailableError

    class _Launcher(PlainSubprocessLauncher):
        def __init__(self, workspace_bind: str | None = None) -> None:
            super().__init__(workspace_bind=workspace_bind)
            self.pass_fds = (7,)

        def build_argv(self, runner_script: str, args: list[str]) -> list[str]:
            return [PY, "-c", "raise SystemExit(0)"]

    monkeypatch.setattr(node_sandbox.sys, "platform", "win32")
    with pytest.raises(SandboxUnavailableError, match="POSIX-only"):
        NodeSandbox(launcher=_Launcher(), timeout=5).run_sync(
            node_id="n",
            source_code="def run(state):\n    return {}\n",
            input_state={},
            input_keys=[],
            output_keys=[],
            timeout=5,
            workspace=WorkspaceMount(bind_source=str(workspace), pass_fds=(7,)),
        )


def test_the_rlimit_profile_rides_in_the_third_argv_slot() -> None:
    limits = WorkspaceLimits()
    captured: dict[str, list[str]] = {}

    class _Capture(PlainSubprocessLauncher):
        def build_argv(self, runner_script: str, args: list[str]) -> list[str]:
            captured["args"] = list(args)
            return [PY, "-c", "raise SystemExit(0)"]

    sandbox = NodeSandbox(launcher=_Capture(workspace_bind="/tmp"), timeout=5)
    sandbox.run_sync(
        node_id="n",
        source_code="def run(state):\n    return {}\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=5,
        workspace=WorkspaceMount(bind_source="/tmp", limits=limits),
    )
    assert captured["args"][0] == "5"
    assert json.loads(captured["args"][2]) == {
        "RLIMIT_AS": 1536 * 1024 * 1024,
        "RLIMIT_CORE": 0,
        "RLIMIT_FSIZE": 512 * 1024 * 1024,
        "RLIMIT_NOFILE": 1024,
        "RLIMIT_NPROC": 1024,
    }


def test_the_workspace_profile_is_the_one_the_design_names() -> None:
    limits = WorkspaceLimits()
    assert limits.rlimit_profile() == {
        "RLIMIT_AS": 1536 * 1024 * 1024,
        "RLIMIT_CORE": 0,
        "RLIMIT_FSIZE": 512 * 1024 * 1024,
        "RLIMIT_NOFILE": 1024,
        "RLIMIT_NPROC": 1024,
    }
    # There is no command COUNT any more: MAX_WORKSPACE_COMMANDS bounded the
    # length of a loop an author wrote (founder, 2026-09-30). The SIZE bounds,
    # which bound what one node hands back, stay.
    import pathlib as _pl

    from tinyassets import node_sandbox as _ns

    assert not hasattr(_ns, "MAX_WORKSPACE_COMMANDS")
    assert not hasattr(limits, "max_commands")
    src = _pl.Path(_ns.__file__).read_text(encoding="utf-8")
    assert "max_commands" not in src
    assert limits.max_output_bytes == 1024 * 1024
    assert limits.command_timeout_s is None


def test_a_node_without_a_workspace_carries_no_profile() -> None:
    captured: dict[str, list[str]] = {}

    class _Capture(PlainSubprocessLauncher):
        def build_argv(self, runner_script: str, args: list[str]) -> list[str]:
            captured["args"] = list(args)
            return [PY, "-c", "raise SystemExit(0)"]

    NodeSandbox(launcher=_Capture(), timeout=5).run_sync(
        node_id="n",
        source_code="def run(state):\n    return {}\n",
        input_state={},
        input_keys=[],
        output_keys=[],
        timeout=5,
    )
    assert captured["args"] == ["5", "0", ""]


# ──────────────────────────────────────────────────────────────────────────────
# The runner: ws, driven through a real child
# ──────────────────────────────────────────────────────────────────────────────


def _run_node(
    workspace: Path | None,
    source: str,
    *,
    limits: WorkspaceLimits | None = None,
    output_keys: tuple[str, ...] = ("result",),
    timeout: float = 60.0,
):
    bind = None if workspace is None else str(workspace)
    sandbox = NodeSandbox(
        launcher=PlainSubprocessLauncher(workspace_bind=bind), timeout=timeout
    )
    mount = (
        None
        if workspace is None
        else WorkspaceMount(bind_source=bind, limits=limits or WorkspaceLimits())
    )
    return sandbox.run_sync(
        node_id="ws-node",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=list(output_keys),
        timeout=timeout,
        workspace=mount,
    )


PROBE = '''
def probe(fn):
    try:
        return {"outcome": "ADMITTED", "value": repr(fn())[:120]}
    except Exception as exc:
        return {"outcome": type(exc).__name__, "message": str(exc)[:160]}
'''


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    root = tmp_path / "generation"
    root.mkdir()
    (root / "README.md").write_text("hello workspace\n", encoding="utf-8")
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("VALUE = 1\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("do not read me\n", encoding="utf-8")
    return root


def test_ws_run_returns_the_code_and_bounded_tails(workspace: Path) -> None:
    source = (
        "def run(state):\n"
        f"    out = ws.run([{PY!r}, '-c', "
        "'import sys; print(\"on out\"); sys.stderr.write(\"on err\"); "
        "raise SystemExit(3)'])\n"
        "    return {'result': out}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["returncode"] == 3
    assert "on out" in payload["stdout_tail"]
    assert "on err" in payload["stderr_tail"]
    assert payload["truncated"] is False


def test_ws_run_tails_are_capped_and_flagged(workspace: Path) -> None:
    source = (
        "def run(state):\n"
        f"    out = ws.run([{PY!r}, '-c', 'print(\"x\" * 400000)'])\n"
        "    return {'result': {'len': len(out['stdout_tail']), "
        "'truncated': out['truncated']}}\n"
    )
    limits = WorkspaceLimits(tail_bytes=4096, max_output_bytes=1024 * 1024)
    result = _run_node(workspace, source, limits=limits)
    assert result.success is True, result.error
    assert result.output_state["result"]["truncated"] is True
    assert result.output_state["result"]["len"] <= 4096


def test_a_loop_of_commands_is_never_refused_for_its_length(workspace: Path) -> None:
    """``MAX_WORKSPACE_COMMANDS`` is gone; a loop runs as long as the author wrote.

    It refused the 1001st command with "workspace limit: at most 1000 commands
    per node" -- and ``WorkspaceLimits(max_commands=N)`` let a caller make that
    smaller still. The bound on a workspace node is its wall clock and its seat.
    The SIZE bounds (output, read, glob) are untouched and tested below.
    """
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    seen = []\n"
        "    for _ in range(12):\n"
        f"        seen.append(probe(lambda: ws.run([{PY!r}, '-c', 'pass'])['returncode']))\n"
        "    return {'result': seen}\n"
    )
    result = _run_node(workspace, source, limits=WorkspaceLimits())
    assert result.success is True, result.error
    outcomes = [step["outcome"] for step in result.output_state["result"]]
    assert outcomes == ["ADMITTED"] * 12, outcomes


def test_the_cumulative_byte_cap_refuses(workspace: Path) -> None:
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    seen = []\n"
        "    for _ in range(3):\n"
        f"        seen.append(probe(lambda: ws.run([{PY!r}, '-c', 'print(\"y\" * 500)'])))\n"
        "    return {'result': [s['outcome'] for s in seen], 'why': seen[-1].get('message', '')}\n"
    )
    result = _run_node(
        workspace,
        source,
        limits=WorkspaceLimits(max_output_bytes=600),
        output_keys=("result", "why"),
    )
    assert result.success is True, result.error
    assert result.output_state["result"][0] == "ADMITTED"
    assert result.output_state["result"][-1] == "RuntimeError"
    assert "workspace limit" in result.output_state["why"]


def test_write_then_read_round_trips_and_makes_parents(workspace: Path) -> None:
    source = (
        "def run(state):\n"
        "    written = ws.write('out/nested/report.md', 'a line\\n')\n"
        "    return {'result': {'written': written, 'back': ws.read('out/nested/report.md'),\n"
        "                       'existing': ws.read('README.md')}}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["written"] == 7
    assert payload["back"] == "a line\n"
    assert payload["existing"].startswith("hello workspace")
    assert (workspace / "out" / "nested" / "report.md").is_file()


def test_ws_read_refuses_a_file_over_the_cap(workspace: Path) -> None:
    (workspace / "big.txt").write_text("z" * 5000, encoding="utf-8")
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': probe(lambda: ws.read('big.txt', max_bytes=100))}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    assert result.output_state["result"]["outcome"] == "RuntimeError"
    assert "workspace limit" in result.output_state["result"]["message"]


def test_ws_read_max_bytes_is_clamped_to_the_cap_not_replaced(workspace: Path) -> None:
    """Asking for more than the limits allow does not raise the node's cap."""
    (workspace / "big.txt").write_text("z" * 5000, encoding="utf-8")
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': probe(lambda: ws.read('big.txt', max_bytes=10 ** 9))}\n"
    )
    result = _run_node(workspace, source, limits=WorkspaceLimits(max_read_bytes=100))
    assert result.success is True, result.error
    outcome = result.output_state["result"]
    assert outcome["outcome"] == "RuntimeError"
    assert "larger than 100 bytes" in outcome["message"]


def test_ws_read_max_bytes_below_the_cap_is_honoured(workspace: Path) -> None:
    (workspace / "big.txt").write_text("z" * 5000, encoding="utf-8")
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': probe(lambda: ws.read('big.txt', max_bytes=50))}\n"
    )
    result = _run_node(workspace, source, limits=WorkspaceLimits(max_read_bytes=100000))
    assert result.success is True, result.error
    assert "larger than 50 bytes" in result.output_state["result"]["message"]


# ──────────────────────────────────────────────────────────────────────────────
# ws.read_bytes / ws.write_bytes: the door binary artifacts leave by
#
# `ws.read` decodes UTF-8 and node code cannot call `open()`, so before these a
# node could produce a video, a PNG or a zip inside the workspace and have no
# way to hand it on -- the founder's own "make videos and post to tiktok"
# example stopped there.
# ──────────────────────────────────────────────────────────────────────────────

#: Run by `ws.run` as a separate process, so the bytes under test are produced
#: the way a real node produces them (ffmpeg, git, a build) rather than by the
#: API being tested. `pathlib`, not `open()`: the node SOURCE carrying these
#: strings is scanned by the denylist, which refuses the substring "open(".
_MAKE_BLOB = (
    "import os, pathlib, sys\n"
    # A deliberate invalid-UTF-8 prefix ahead of the random tail: the point of
    # this door is bytes that `ws.read` would mangle, and 0xff 0xfe is never
    # valid UTF-8 whatever the random draw does.
    "pathlib.Path(sys.argv[1]).write_bytes(bytes([255, 254, 0, 1]) + os.urandom(2044))\n"
)
_SHA_OF = (
    "import hashlib, pathlib, sys\n"
    "print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())\n"
)
_BLOB_BYTES = 2048


def test_bytes_survive_a_round_trip_a_command_can_vouch_for(workspace: Path) -> None:
    """The whole feature in one node, and the digest is the witness.

    Comparing what `read_bytes` returned against what `write_bytes` wrote would
    only prove base64 is its own inverse. So the sha256 comes from a SEPARATE
    process reading the file off the filesystem, and the parent re-computes it
    a third time outside the jail: three witnesses to the same bytes, only one
    of which went through the encoding.
    """
    source = (
        "import base64, hashlib\n"
        "def run(state):\n"
        f"    made = ws.run([{PY!r}, '-c', {_MAKE_BLOB!r}, 'clip.bin'])\n"
        f"    told = ws.run([{PY!r}, '-c', {_SHA_OF!r}, 'clip.bin'])\n"
        "    blob = base64.b64decode(ws.read_bytes('clip.bin'))\n"
        "    written = ws.write_bytes(\n"
        "        'out/copy.bin', base64.b64encode(blob).decode('ascii')\n"
        "    )\n"
        f"    copy = ws.run([{PY!r}, '-c', {_SHA_OF!r}, 'out/copy.bin'])\n"
        "    return {'result': {\n"
        "        'made_rc': made['returncode'],\n"
        "        'told': told['stdout_tail'].strip(),\n"
        "        'seen': hashlib.sha256(blob).hexdigest(),\n"
        "        'copy_told': copy['stdout_tail'].strip(),\n"
        "        'raw_len': len(blob),\n"
        "        'written': written,\n"
        "        'encoded_len': len(ws.read_bytes('clip.bin')),\n"
        "        'text_would_mangle': ws.read('clip.bin').encode('utf-8') != blob,\n"
        "    }}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["made_rc"] == 0, payload

    on_disk = (workspace / "clip.bin").read_bytes()
    copy_on_disk = (workspace / "out" / "copy.bin").read_bytes()
    outside_witness = hashlib.sha256(on_disk).hexdigest()

    assert payload["told"] == payload["seen"] == outside_witness, payload
    assert payload["copy_told"] == outside_witness, payload
    assert copy_on_disk == on_disk, "write_bytes did not reproduce the bytes"
    assert payload["raw_len"] == payload["written"] == _BLOB_BYTES
    # The counts are the contract: read_bytes returns the ENCODED string and
    # write_bytes reports the RAW count, and they are not the same number.
    assert payload["encoded_len"] == 4 * ((_BLOB_BYTES + 2) // 3)
    # And the reason the door exists: the text door cannot carry these bytes.
    assert payload["text_would_mangle"] is True


@pytest.mark.real_jail
@pytest.mark.skipif(
    sys.platform != "linux" or BWRAP is None,
    reason="the byte doors inside the real jail need Linux and bwrap",
)
def test_bytes_leave_the_real_jail_with_their_digest_intact(workspace: Path) -> None:
    """The same round trip, but through bubblewrap rather than the test launcher.

    The plain launcher performs no mount, so it proves the API and not the
    JAIL: in production the file is written inside a pid/mount namespace with
    no network and a private tmpfs, and the bytes have to cross that boundary
    as base64 on one JSON line. This is the only place that boundary is real,
    which is why it runs on the Linux oracle.
    """
    from tinyassets.node_sandbox import BwrapLauncher

    source = (
        "import base64, hashlib\n"
        "def run(state):\n"
        f"    made = ws.run([{PY!r}, '-c', {_MAKE_BLOB!r}, 'clip.bin'])\n"
        f"    told = ws.run([{PY!r}, '-c', {_SHA_OF!r}, 'clip.bin'])\n"
        "    blob = base64.b64decode(ws.read_bytes('clip.bin'))\n"
        "    ws.write_bytes('out/copy.bin', base64.b64encode(blob).decode('ascii'))\n"
        "    return {'result': {\n"
        "        'made_rc': made['returncode'],\n"
        "        'told': told['stdout_tail'].strip(),\n"
        "        'seen': hashlib.sha256(blob).hexdigest(),\n"
        "        'where': ws.path,\n"
        "    }}\n"
    )
    # Built the way the compiler builds it: a PATH bind must name the root it
    # is allowed to come from, and `for_workspace` is what carries that over.
    mount = WorkspaceMount(
        bind_source=str(workspace), allowed_roots=(str(workspace.parent),)
    )
    result = NodeSandbox(
        launcher=BwrapLauncher().for_workspace(mount), timeout=120
    ).run_sync(
        node_id="ws-node",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["result"],
        timeout=120,
        workspace=mount,
    )
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["made_rc"] == 0, payload
    assert payload["where"] == "/workspace", payload

    # The bind is this directory, so the parent reads what the jail produced.
    on_disk = (workspace / "clip.bin").read_bytes()
    witness = hashlib.sha256(on_disk).hexdigest()
    assert payload["told"] == payload["seen"] == witness, payload
    assert (workspace / "out" / "copy.bin").read_bytes() == on_disk
    assert len(on_disk) == _BLOB_BYTES


def test_the_read_bytes_cap_is_the_raw_size_not_the_encoded_length(
    workspace: Path,
) -> None:
    """A 2048-byte file encodes to 2732. With the cap on the encoded string a
    node would be refused a file well inside its own limit, and every caller
    would have to know base64's expansion to use its own configuration."""
    (workspace / "blob.bin").write_bytes(bytes(range(256)) * 8)
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': {\n"
        "        'inside': probe(lambda: len(ws.read_bytes('blob.bin'))),\n"
        "        'over': probe(lambda: ws.read_bytes('blob.bin', max_bytes=100)),\n"
        "    }}\n"
    )
    result = _run_node(
        workspace, source, limits=WorkspaceLimits(max_read_bytes=2500)
    )
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["inside"]["outcome"] == "ADMITTED", payload["inside"]
    assert payload["inside"]["value"] == "2732"
    assert payload["over"]["outcome"] == "RuntimeError"
    assert "larger than 100 bytes" in payload["over"]["message"]


def test_read_bytes_max_bytes_is_clamped_to_the_cap_not_replaced(
    workspace: Path,
) -> None:
    (workspace / "blob.bin").write_bytes(b"z" * 5000)
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': probe(lambda: ws.read_bytes('blob.bin', max_bytes=10 ** 9))}\n"
    )
    result = _run_node(workspace, source, limits=WorkspaceLimits(max_read_bytes=100))
    assert result.success is True, result.error
    assert "larger than 100 bytes" in result.output_state["result"]["message"]


def test_the_byte_doors_charge_the_node_budget_by_raw_size(workspace: Path) -> None:
    """Charged RAW, and charged at all.

    RAW is what the two numbers here separate: 2048 raw against a 2500-byte
    budget is admitted, 2732 encoded would not be. Charging at all is what the
    second call proves -- otherwise reading a file as bytes is a way around a
    cap that reading it as text would hit.
    """
    (workspace / "blob.bin").write_bytes(bytes(range(256)) * 8)
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    first = probe(lambda: len(ws.read_bytes('blob.bin')))\n"
        "    second = probe(lambda: len(ws.read_bytes('blob.bin')))\n"
        "    return {'result': {'first': first, 'second': second}}\n"
    )
    result = _run_node(
        workspace, source, limits=WorkspaceLimits(max_output_bytes=2500)
    )
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["first"]["outcome"] == "ADMITTED", payload["first"]
    assert payload["second"]["outcome"] == "RuntimeError"
    assert "workspace limit" in payload["second"]["message"]
    assert "read_bytes" in payload["second"]["message"]


def test_a_write_bytes_over_the_budget_leaves_no_partial_file(
    workspace: Path,
) -> None:
    """The charge happens before the open, so the refusal is not a half-written
    artifact a later node would treat as finished."""
    source = (
        PROBE + "\n"
        "import base64\n"
        "def run(state):\n"
        "    payload = base64.b64encode(bytes(3000)).decode('ascii')\n"
        "    return {'result': probe(lambda: ws.write_bytes('out/too-big.bin', payload))}\n"
    )
    result = _run_node(
        workspace, source, limits=WorkspaceLimits(max_output_bytes=2500)
    )
    assert result.success is True, result.error
    outcome = result.output_state["result"]
    assert outcome["outcome"] == "RuntimeError"
    assert "write_bytes" in outcome["message"]
    assert not (workspace / "out" / "too-big.bin").exists()


NOT_BASE64 = [
    ("plain_text", "hello, this is not base64"),
    ("bad_alphabet", "AAAA****AAAA"),
    ("bad_padding", "AAAAA"),
    ("whitespace", "AAAA AAAA"),
    ("non_ascii", "AAAAéAAA"),
]


@pytest.mark.parametrize(
    "payload", [pytest.param(text, id=name) for name, text in NOT_BASE64]
)
def test_write_bytes_refuses_anything_but_strict_base64(
    workspace: Path, payload: str
) -> None:
    """`b64decode` without `validate=True` DISCARDS characters outside the
    alphabet, so "AAAA****AAAA" decodes happily to the wrong bytes and writes a
    corrupt file that looks like a successful one."""
    source = (
        PROBE + "\n"
        "def run(state):\n"
        f"    return {{'result': probe(lambda: ws.write_bytes('blob.bin', {payload!r}))}}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    outcome = result.output_state["result"]
    assert outcome["outcome"] == "ValueError", outcome
    assert "strict base64" in outcome["message"], outcome
    assert not (workspace / "blob.bin").exists()


def test_the_byte_doors_refuse_the_types_they_are_not_given(workspace: Path) -> None:
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': {\n"
        "        'bytes_payload': probe(lambda: ws.write_bytes('b.bin', b'AAAA')),\n"
        "        'int_payload': probe(lambda: ws.write_bytes('b.bin', 3)),\n"
        "        'str_cap': probe(lambda: ws.read_bytes('README.md', max_bytes='10')),\n"
        "        'zero_cap': probe(lambda: ws.read_bytes('README.md', max_bytes=0)),\n"
        "    }}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["bytes_payload"]["outcome"] == "TypeError"
    assert "must be a str" in payload["bytes_payload"]["message"]
    assert payload["int_payload"]["outcome"] == "TypeError"
    assert payload["str_cap"]["outcome"] == "TypeError"
    assert "must be an int" in payload["str_cap"]["message"]
    assert payload["zero_cap"]["outcome"] == "ValueError"
    assert "must be positive" in payload["zero_cap"]["message"]


def test_a_symlinked_leaf_cannot_be_written_through_by_write_bytes(
    workspace: Path,
) -> None:
    """The same rule `ws.write` has, at the same place: the leaf is opened
    O_NOFOLLOW, so a link planted inside the workspace is not a way to write
    outside it."""
    target = workspace.parent / "outside" / "secret.txt"
    link = workspace / "secret-link.bin"
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError, AttributeError) as exc:
        pytest.skip(f"this host cannot create a file symlink: {exc}")

    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': {\n"
        "        'write': probe(lambda: ws.write_bytes('secret-link.bin', 'cGxhbnRlZA==')),\n"
        "        'read': probe(lambda: ws.read_bytes('secret-link.bin')),\n"
        "    }}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["write"]["outcome"] != "ADMITTED", payload["write"]
    assert payload["read"]["outcome"] != "ADMITTED", payload["read"]
    assert target.read_text(encoding="utf-8") == "do not read me\n"


@pytest.mark.skipif(os.name != "posix", reason="dir_fd resolution is POSIX-only")
def test_a_symlinked_parent_cannot_be_walked_through(workspace: Path) -> None:
    """Codex #9: resolution opens each component NOFOLLOW from the previous
    descriptor, so a symlinked parent is not a door -- there is no name left to
    swap after the first open."""
    outside = workspace.parent / "outside"
    (workspace / "escape").symlink_to(outside, target_is_directory=True)
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': probe(lambda: ws.read('escape/secret.txt'))}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    assert result.output_state["result"]["outcome"] == "ValueError"


def test_ws_glob_returns_relative_paths(workspace: Path) -> None:
    source = (
        "def run(state):\n"
        "    return {'result': {'py': ws.glob('**/*.py'), 'top': ws.glob('*.md')}}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    assert result.output_state["result"]["py"] == ["pkg/mod.py"]
    assert result.output_state["result"]["top"] == ["README.md"]


def test_ws_glob_is_capped(workspace: Path) -> None:
    for index in range(12):
        (workspace / f"file{index}.dat").write_text("x", encoding="utf-8")
    source = (
        "def run(state):\n"
        "    return {'result': len(ws.glob('*.dat'))}\n"
    )
    result = _run_node(workspace, source, limits=WorkspaceLimits(max_glob_results=5))
    assert result.success is True, result.error
    assert result.output_state["result"] == 5


# Each case names the rule that must refuse it. Asserting only the exception
# TYPE let two different rules stand in for each other, so removing either
# one left the table green.
ESCAPES = [
    ("read_parent", "ws.read('../outside/secret.txt')", "may not contain"),
    ("read_absolute", "ws.read('/etc/passwd')", "must be relative, not absolute"),
    ("read_drive", "ws.read('C:/Windows/win.ini')", "not a drive path"),
    (
        "read_nested_parent",
        "ws.read('pkg/../../outside/secret.txt')",
        "may not contain",
    ),
    (
        "write_parent",
        "ws.write('../outside/planted.txt', 'x')",
        "may not contain",
    ),
    (
        "write_absolute",
        "ws.write('/tmp/planted.txt', 'x')",
        "must be relative, not absolute",
    ),
    ("glob_parent", "ws.glob('../outside/*')", "may not contain"),
    ("glob_absolute", "ws.glob('/etc/*')", "must be relative"),
    ("cwd_parent", "ws.run(['echo'], cwd='../outside')", "may not contain"),
    (
        "cwd_absolute",
        "ws.run(['echo'], cwd='/etc')",
        "must be relative, not absolute",
    ),
    ("read_empty", "ws.read('')", "non-empty string"),
    ("write_root", "ws.write('.', 'x')", "names the workspace root"),
    # The byte doors answer to the same rules as the text doors. Rows, not a
    # comment claiming so: read_bytes/write_bytes resolve through the same
    # helpers, and a row is what notices if one day they do not.
    (
        "read_bytes_parent",
        "ws.read_bytes('../outside/secret.txt')",
        "may not contain",
    ),
    (
        "read_bytes_absolute",
        "ws.read_bytes('/etc/passwd')",
        "must be relative, not absolute",
    ),
    ("read_bytes_drive", "ws.read_bytes('C:/Windows/win.ini')", "not a drive path"),
    ("read_bytes_empty", "ws.read_bytes('')", "non-empty string"),
    (
        "write_bytes_parent",
        "ws.write_bytes('../outside/planted.txt', 'eA==')",
        "may not contain",
    ),
    (
        "write_bytes_absolute",
        "ws.write_bytes('/tmp/planted.txt', 'eA==')",
        "must be relative, not absolute",
    ),
    (
        "write_bytes_nested_parent",
        "ws.write_bytes('pkg/../../outside/planted.txt', 'eA==')",
        "may not contain",
    ),
    ("write_bytes_root", "ws.write_bytes('.', 'eA==')", "names the workspace root"),
]


@pytest.mark.parametrize(
    ("expression", "because"),
    [pytest.param(expr, why, id=name) for name, expr, why in ESCAPES],
)
def test_paths_that_leave_the_workspace_refuse(
    workspace: Path, expression: str, because: str
) -> None:
    source = (
        PROBE + "\n"
        "def run(state):\n"
        f"    return {{'result': probe(lambda: {expression})}}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    outcome = result.output_state["result"]
    assert outcome["outcome"] in ("ValueError", "TypeError"), outcome
    assert because in outcome["message"], outcome
    assert not (workspace.parent / "outside" / "planted.txt").exists()


def test_a_symlink_out_of_the_workspace_is_not_a_way_out(workspace: Path) -> None:
    link = workspace / "escape"
    try:
        os.symlink(str(workspace.parent / "outside"), str(link), target_is_directory=True)
    except (OSError, NotImplementedError, AttributeError) as exc:
        pytest.skip(f"this host cannot create a directory symlink: {exc}")

    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': {\n"
        "        'read': probe(lambda: ws.read('escape/secret.txt')),\n"
        "        'write': probe(lambda: ws.write('escape/planted.txt', 'x')),\n"
        "        'cwd': probe(lambda: ws.run(['echo'], cwd='escape')),\n"
        "        'glob': ws.glob('escape/*'),\n"
        "    }}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["read"]["outcome"] == "ValueError"
    assert payload["write"]["outcome"] == "ValueError"
    assert payload["cwd"]["outcome"] == "ValueError"
    # The glob does not refuse; it simply does not report what is not inside.
    assert payload["glob"] == []
    assert not (workspace.parent / "outside" / "planted.txt").exists()


def test_a_symlinked_leaf_cannot_be_written_through(workspace: Path) -> None:
    target = workspace.parent / "outside" / "secret.txt"
    link = workspace / "secret-link.txt"
    try:
        os.symlink(str(target), str(link))
    except (OSError, NotImplementedError, AttributeError) as exc:
        pytest.skip(f"this host cannot create a file symlink: {exc}")

    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': probe(lambda: ws.write('secret-link.txt', 'planted'))}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    assert result.output_state["result"]["outcome"] != "ADMITTED"
    assert target.read_text(encoding="utf-8") == "do not read me\n"


def test_ws_run_refuses_a_shell_string_and_a_bad_env(workspace: Path) -> None:
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': {\n"
        "        'string': probe(lambda: ws.run('echo hi')),\n"
        "        'empty': probe(lambda: ws.run([])),\n"
        "        'nonstr': probe(lambda: ws.run(['echo', 3])),\n"
        f"        'badenv': probe(lambda: ws.run([{PY!r}, '-c', 'pass'], "
        "env={'lower': 'x'})),\n"
        "    }}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    # A shell string and an empty list are shape errors; a non-str argv element
    # and a non-str env value are TYPE errors, refused before any method on the
    # caller's object is called (Codex #8).
    outcomes = result.output_state["result"]
    assert outcomes["string"]["outcome"] == "ValueError", outcomes["string"]
    assert outcomes["empty"]["outcome"] == "ValueError", outcomes["empty"]
    assert outcomes["nonstr"]["outcome"] == "TypeError", outcomes["nonstr"]
    assert outcomes["badenv"]["outcome"] == "ValueError", outcomes["badenv"]


def test_ws_run_env_is_a_fixed_base_plus_screened_keys(workspace: Path) -> None:
    source = (
        "def run(state):\n"
        f"    out = ws.run([{PY!r}, '-c', "
        "'import os,json; print(json.dumps(dict(os.environ)))'], "
        "env={'MY_FLAG': 'on'})\n"
        "    return {'result': out['stdout_tail']}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    child_env = json.loads(result.output_state["result"])
    assert child_env.get("MY_FLAG") == "on"
    assert child_env.get("LANG") == "C.UTF-8"
    assert child_env.get("PATH") == "/usr/bin:/bin"
    assert "TINYASSETS_DATA_DIR" not in child_env


def test_the_child_env_turns_off_background_git_maintenance(
    workspace: Path,
) -> None:
    """`git commit`/`fetch` fork a detached `gc --auto` that keeps
    `.git/objects/pack` open after the node returns, which is what made the
    lease wipe fail. Turned off through GIT_CONFIG_* rather than a config file:
    the workspace's own `.git` is writable by node code.
    """
    source = (
        "def run(state):\n"
        f"    out = ws.run([{PY!r}, '-c', "
        "'import os,json; print(json.dumps(dict(os.environ)))'])\n"
        "    return {'result': out['stdout_tail']}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    child_env = json.loads(result.output_state["result"])
    assert child_env["GIT_CONFIG_COUNT"] == "2"
    assert child_env["GIT_CONFIG_KEY_0"] == "gc.auto"
    assert child_env["GIT_CONFIG_VALUE_0"] == "0"
    assert child_env["GIT_CONFIG_KEY_1"] == "maintenance.auto"
    assert child_env["GIT_CONFIG_VALUE_1"] == "false"


@pytest.mark.parametrize(
    "key",
    ["GIT_CONFIG_COUNT", "GIT_CONFIG_KEY_0", "GIT_CONFIG_VALUE_1", "GIT_CONFIG_GLOBAL"],
)
def test_a_node_cannot_override_the_git_configuration(
    workspace: Path, key: str
) -> None:
    """Refused, not ignored: dropping it silently would let a node believe it
    had turned maintenance back on."""
    source = (
        PROBE + "\n"
        "def run(state):\n"
        f"    return {{'result': probe(lambda: ws.run([{PY!r}, '-c', 'pass'], "
        f"env={{{key!r}: '9'}}))}}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    outcome = result.output_state["result"]
    assert outcome["outcome"] == "ValueError", outcome
    assert "git configuration is fixed" in outcome["message"]


def test_the_bundle_commands_disable_maintenance_explicitly() -> None:
    """Belt as well as the environment: the bundle's output crosses the jail."""
    from tinyassets import node_sandbox

    runner = node_sandbox._RUNNER_SCRIPT
    assert '"-c", "gc.auto=0",' in runner
    assert '"-c", "maintenance.auto=false",' in runner


def test_a_node_without_a_workspace_has_no_ws_name(workspace: Path) -> None:
    # `dir()` inside a function lists locals, and `ws` is a global of the
    # node's own namespace: the honest probe is whether the NAME resolves.
    source = (
        "def run(state):\n"
        "    try:\n"
        "        ws\n"
        "    except NameError:\n"
        "        return {'result': False}\n"
        "    return {'result': True}\n"
    )
    assert _run_node(None, source).output_state["result"] is False
    assert _run_node(workspace, source).output_state["result"] is True


def test_the_import_allowlist_is_unaffected_by_what_ws_uses(workspace: Path) -> None:
    """``ws`` needs os/glob/threading; node code must still be refused them.

    They are imported at the top of the runner, before the allowlist replaces
    ``__import__``, and they live in the RUNNER's globals -- the node executes in
    its own namespace dict, so it cannot reach them through ``ws`` either.
    """
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    def _os():\n"
        "        import os\n"
        "        return os\n"
        "    def _glob():\n"
        "        import glob\n"
        "        return glob\n"
        "    def _threading():\n"
        "        import threading\n"
        "        return threading\n"
        "    return {'result': {\n"
        "        'os': probe(_os), 'glob': probe(_glob), 'threading': probe(_threading),\n"
        "        'reachable': [n for n in dir(ws) if not n.startswith('_')],\n"
        "    }}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    for name in ("os", "glob", "threading"):
        assert payload[name]["outcome"] == "ImportError", payload[name]
    # The whole public surface, pinned. The byte doors are here; the helpers
    # they share with the text doors (`_read_raw`, `_write_raw`, `_charge`,
    # `_cap_for`) are underscored precisely so this list stays the API.
    assert sorted(payload["reachable"]) == [
        "bundle", "glob", "path", "read", "read_bytes", "run", "write",
        "write_bytes",
    ]


def test_a_str_subclass_cannot_run_node_code_inside_path_validation(
    workspace: Path,
) -> None:
    """Codex #8, the property: no method of a caller's object is ever called.

    The old shape raised the import depth around ws internals so that
    `subprocess`'s lazy imports would not be checked -- and `_ws_resolve` then
    called `.replace()` on the caller's argument INSIDE that window, so a `str`
    subclass overriding `replace` ran node code with the allowlist suspended.
    Codex imported `os` that way. Arguments are now checked to the EXACT builtin
    type first, so `replace` is never reached, and the depth escape is gone
    entirely (the modules are preloaded before the allowlist is installed).
    """
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    seen = []\n"
        "    class Sneaky(str):\n"
        "        def replace(self, *a, **k):\n"
        "            seen.append('replace')\n"
        "            try:\n"
        "                import os\n"
        "                seen.append('IMPORTED')\n"
        "            except ImportError:\n"
        "                seen.append('blocked')\n"
        "            return str.replace(self, *a, **k)\n"
        "    outcomes = [\n"
        "        probe(lambda: ws.read(Sneaky('README.md')))['outcome'],\n"
        "        probe(lambda: ws.write(Sneaky('x.txt'), 'y'))['outcome'],\n"
        "        probe(lambda: ws.glob(Sneaky('*')))['outcome'],\n"
        "    ]\n"
        "    return {'result': {'outcomes': outcomes, 'seen': seen}}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    payload = result.output_state["result"]
    assert payload["outcomes"] == ["TypeError", "TypeError", "TypeError"]
    assert payload["seen"] == [], "a method of the caller's object was called"


def test_the_runner_holds_no_import_depth_counter_at_all() -> None:
    """A counter says how deep the stack is, not whose code is running.

    Anything that called into user code while it was raised handed node code an
    unchecked import, which is Codex #8. The allowlist now asks whether the
    importing frame's globals ARE the node's namespace -- a fact no call can
    hold open.
    """
    from tinyassets import node_sandbox

    assert "_ws_internals" not in node_sandbox._RUNNER_SCRIPT
    assert "_import_depth" not in node_sandbox._RUNNER_SCRIPT
    assert "f_globals is namespace" in node_sandbox._RUNNER_SCRIPT


def test_ws_run_works_when_the_node_allowlist_is_minimal(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The regression for the lazy-import bug, driven through a real child."""
    from tinyassets import node_sandbox

    monkeypatch.setattr(node_sandbox, "ALLOWED_IMPORTS", {"json"})
    source = (
        "def run(state):\n"
        f"    out = ws.run([{PY!r}, '-c', 'print(1)'])\n"
        "    ws.write('probe.txt', 'x')\n"
        "    return {'result': [out['returncode'], ws.read('probe.txt'),"
        " ws.glob('*.txt')]}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    assert result.output_state["result"] == [0, "x", ["probe.txt"]]


def test_the_source_denylist_still_refuses_a_node_that_names_subprocess(
    workspace: Path,
) -> None:
    result = _run_node(
        workspace,
        "def run(state):\n    import subprocess\n    return {'result': 1}\n",
    )
    assert result.success is False
    assert "subprocess" in result.error


# ──────────────────────────────────────────────────────────────────────────────
# ws.bundle
# ──────────────────────────────────────────────────────────────────────────────


def test_bundle_refuses_anything_that_is_not_a_commit_sha(workspace: Path) -> None:
    source = (
        PROBE + "\n"
        "def run(state):\n"
        "    return {'result': [\n"
        "        probe(lambda: ws.bundle('HEAD'))['outcome'],\n"
        "        probe(lambda: ws.bundle('z' * 40))['outcome'],\n"
        "        probe(lambda: ws.bundle('abc'))['outcome'],\n"
        "        probe(lambda: ws.bundle('A' * 40))['outcome'],\n"
        "        probe(lambda: ws.bundle(7))['outcome'],\n"
        "    ]}\n"
    )
    result = _run_node(workspace, source)
    assert result.success is True, result.error
    # The last is a TYPE error: `7` is not a str, and that is checked before
    # anything is asked of it.
    assert result.output_state["result"] == ["ValueError"] * 4 + ["TypeError"]


@pytest.mark.skipif(
    GIT_BINARY is None or sys.platform == "win32",
    reason=(
        "needs git resolvable from the sandbox child; the tests-only launcher "
        "gives the runner no PATH, and on Windows CreateProcess searches the "
        "child's own environment rather than the jail's fixed /usr/bin:/bin"
    ),
)
def test_bundle_produces_a_prerequisite_free_bundle(tmp_path: Path) -> None:
    from tinyassets.workspace_git import git_environment, verify_bundle

    repo = tmp_path / "generation"
    repo.mkdir()
    home = tmp_path / "githome"
    home.mkdir()
    env = git_environment(home, path=str(Path(GIT_BINARY).parent))
    if sys.platform == "win32":
        for key in ("SystemRoot", "COMSPEC"):
            value = os.environ.get(key)
            if value:
                env[key] = value

    def git(*args: str) -> str:
        done = subprocess.run(
            [GIT_BINARY, *args],
            cwd=str(repo),
            env=env,
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            timeout=120,
            check=True,
        )
        return done.stdout.strip()

    git("init", "--quiet", ".")
    (repo / "README.md").write_text("workspace\n", encoding="utf-8")
    git("add", "README.md")
    git(
        "-c", "user.name=Tiny",
        "-c", "user.email=tiny@universes.tinyassets.io",
        "commit", "--quiet", "-m", "first",
    )
    sha = git("rev-parse", "HEAD")

    source = (
        "def run(state):\n"
        f"    return {{'result': ws.bundle({sha!r})}}\n"
    )
    result = _run_node(repo, source)
    assert result.success is True, result.error
    relative = result.output_state["result"]
    assert relative == f".tiny-export/{sha}.bundle"

    bundle = repo / ".tiny-export" / f"{sha}.bundle"
    assert bundle.is_file()
    scratch = tmp_path / "verify"
    scratch.mkdir()
    refs = verify_bundle(
        bundle,
        max_bytes=10_000_000,
        scratch_dir=scratch,
        home_dir=home,
        path=str(Path(GIT_BINARY).parent),
    )
    assert refs == ["refs/tiny/export"]

    # The synthetic ref is deleted: the workspace is left as it was found.
    listed = subprocess.run(
        [GIT_BINARY, "show-ref"],
        cwd=str(repo), env=env, capture_output=True, text=True, timeout=60,
    )
    assert "refs/tiny/export" not in listed.stdout


# ──────────────────────────────────────────────────────────────────────────────
# The compiler: a workspace is a graph fact
# ──────────────────────────────────────────────────────────────────────────────


def _code_node(**overrides) -> NodeDefinition:
    fields = {
        "node_id": "build",
        "display_name": "Build",
        "phase": "draft",
        "source_code": "def run(state):\n    return {'ok': True}\n",
        "output_keys": ["ok"],
    }
    fields.update(overrides)
    return NodeDefinition(**fields)


def test_a_workspace_naming_a_non_ancestor_fails_at_compile() -> None:
    node = _code_node(workspace="checkout")
    with pytest.raises(gc.CodeNodeError, match="not one of its ancestors"):
        gc._build_source_code_node(
            node, event_sink=None, effect_chain=EffectChain(), ancestors={"other"}
        )


def test_a_workspace_with_no_ancestry_at_all_fails_at_compile() -> None:
    with pytest.raises(gc.CodeNodeError, match="not one of its ancestors"):
        gc._build_source_code_node(
            _code_node(workspace="checkout"),
            event_sink=None,
            effect_chain=EffectChain(),
            ancestors=None,
        )


def test_a_node_without_a_workspace_compiles_with_no_ancestry() -> None:
    fn = gc._build_source_code_node(
        _code_node(), event_sink=None, effect_chain=EffectChain(), ancestors=None
    )
    assert callable(fn)


def test_an_ancestor_workspace_compiles() -> None:
    fn = gc._build_source_code_node(
        _code_node(workspace="checkout"),
        event_sink=None,
        effect_chain=EffectChain(),
        ancestors={"checkout", "plan"},
    )
    assert callable(fn)


def test_a_missing_mount_fails_the_node_with_the_not_available_message() -> None:
    chain = EffectChain()
    fn = gc._build_source_code_node(
        _code_node(workspace="checkout"),
        event_sink=None,
        effect_chain=chain,
        ancestors={"checkout"},
    )
    with pytest.raises(gc.CodeNodeError, match="workspace not available"):
        fn({})


def test_a_revoked_mount_fails_the_node_the_same_way() -> None:
    chain = EffectChain()
    chain.register_workspace("checkout", WorkspaceMount(bind_source="/srv/pool/x"))
    fn = gc._build_source_code_node(
        _code_node(workspace="checkout"),
        event_sink=None,
        effect_chain=chain,
        ancestors={"checkout"},
    )
    chain.revoke_workspace("checkout")
    with pytest.raises(gc.CodeNodeError, match="was discarded"):
        fn({})


def test_a_run_with_no_effect_chain_cannot_supply_a_workspace() -> None:
    fn = gc._build_source_code_node(
        _code_node(workspace="checkout"),
        event_sink=None,
        effect_chain=None,
        ancestors={"checkout"},
    )
    with pytest.raises(gc.CodeNodeError, match="workspace not available"):
        fn({})


def test_the_registry_refuses_a_junk_registration() -> None:
    chain = EffectChain()
    with pytest.raises(ValueError):
        chain.register_workspace("", WorkspaceMount(bind_source="/srv/pool/x"))
    with pytest.raises(ValueError):
        chain.register_workspace("checkout", None)


def test_the_registry_hands_back_exactly_what_was_registered() -> None:
    chain = EffectChain()
    mount = WorkspaceMount(bind_source="/srv/pool/x")
    chain.register_workspace("checkout", mount)
    assert chain.workspace_mount("checkout") is mount


def test_the_workspace_is_not_serialised_into_the_node_dict() -> None:
    """It travels as a capability; a branch may only NAME an ancestor."""
    node = _code_node(workspace="checkout")
    assert node.to_dict()["workspace"] == "checkout"
    assert NodeDefinition.from_dict(node.to_dict()).workspace == "checkout"


class _ChainMount:
    """Stands in for the sink's capability.

    ``repo_fd`` is the descriptor of the REPOSITORY directory, which is what
    gets bound. ``lease_fd`` names the lease one level up and is carried here
    only to pin that it is never used for the bind: binding through it would
    mount the wrong tree.
    """

    def __init__(
        self,
        bind_source: str,
        repo_fd: int | None = None,
        lease_fd: int | None = None,
        pass_fds: tuple[int, ...] = (),
    ) -> None:
        self.bind_source = bind_source
        self.repo_fd = repo_fd
        self.lease_fd = lease_fd
        self.pass_fds = pass_fds
        self.limits = WorkspaceLimits()
        self.allowed_roots: tuple[str, ...] = ()


def test_without_a_descriptor_the_compiler_uses_the_path(workspace: Path) -> None:
    built = gc._sandbox_workspace_mount(_ChainMount(str(workspace)), "build")
    assert built.bind_source == str(workspace)
    assert built.pass_fds == ()


def test_a_host_that_cannot_bind_a_descriptor_uses_the_path(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gc, "WORKSPACE_FD_BIND_SUPPORTED", False)
    built = gc._sandbox_workspace_mount(_ChainMount(str(workspace), repo_fd=9), "build")
    assert built.bind_source == str(workspace)
    assert built.pass_fds == ()


def test_a_dead_lease_descriptor_fails_the_node_loudly(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Falling back to the path here would silently reintroduce the swap."""
    monkeypatch.setattr(gc, "WORKSPACE_FD_BIND_SUPPORTED", True)
    with pytest.raises(gc.CodeNodeError, match="lease descriptor is unusable"):
        gc._sandbox_workspace_mount(_ChainMount(str(workspace), repo_fd=9999), "build")


def test_a_capability_naming_no_directory_fails_the_node() -> None:
    with pytest.raises(gc.CodeNodeError, match="names no directory"):
        gc._sandbox_workspace_mount(_ChainMount(""), "build")


@pytest.mark.skipif(os.name != "posix", reason="a directory descriptor is POSIX-only")
def test_the_compiler_prefers_the_held_descriptor(workspace: Path) -> None:
    handle = os.open(str(workspace), os.O_RDONLY)
    try:
        built = gc._sandbox_workspace_mount(
            _ChainMount(str(workspace), repo_fd=handle), "build"
        )
        assert built.bind_source == f"/proc/self/fd/{handle}"
        assert built.pass_fds == (handle,)
    finally:
        os.close(handle)


@pytest.mark.skipif(
    sys.platform != "linux" or BWRAP is None,
    reason="binding through a descriptor needs Linux and bwrap",
)
def test_a_rename_mid_run_cannot_swap_the_bound_directory(tmp_path: Path) -> None:
    """The bind resolves through the fd, so the path it came from may move.

    A path bind would follow the name; this one follows the descriptor, which is
    the whole reason production uses it.
    """
    import threading

    from tinyassets.node_sandbox import BwrapLauncher

    lease = tmp_path / "lease-1"
    lease.mkdir()
    (lease / "README.md").write_text("bound through the fd\n", encoding="utf-8")
    decoy = tmp_path / "lease-2"
    decoy.mkdir()
    (decoy / "README.md").write_text("the wrong directory\n", encoding="utf-8")

    handle = os.open(str(lease), os.O_RDONLY)
    renamed = threading.Event()

    def rename_after_a_moment() -> None:
        time.sleep(1.0)
        os.rename(str(lease), str(tmp_path / "lease-1-moved"))
        os.rename(str(decoy), str(lease))
        renamed.set()

    mover = threading.Thread(target=rename_after_a_moment, daemon=True)
    source = (
        "def run(state):\n"
        f"    ws.run([{PY!r}, '-c', 'import time; time.sleep(3)'])\n"
        "    return {'result': ws.read('README.md')}\n"
    )
    try:
        mover.start()
        launcher = BwrapLauncher(
            workspace_bind=f"/proc/self/fd/{handle}", pass_fds=(handle,)
        )
        result = NodeSandbox(launcher=launcher, timeout=60).run_sync(
            node_id="ws-node",
            source_code=source,
            input_state={},
            input_keys=[],
            output_keys=["result"],
            timeout=60,
            workspace=WorkspaceMount(
                bind_source=f"/proc/self/fd/{handle}", pass_fds=(handle,)
            ),
        )
    finally:
        os.close(handle)
    assert renamed.is_set(), "the rename never happened; the test proves nothing"
    assert result.success is True, result.error
    assert result.output_state["result"] == "bound through the fd\n"


def test_the_descriptor_reaches_both_the_launcher_and_the_child(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The real compiler path, end to end: capability -> mount -> launcher -> Popen.

    The regression this pins (Codex #14b): the compiler built the sandbox mount
    by hand from `bind_source` alone and called the launcher factory without the
    descriptors, so a `/proc/self/fd/N` bind was neither admitted nor inherited
    and the jail fell back to binding a path a rename could swap.
    """
    from tinyassets import node_sandbox
    from tinyassets.node_sandbox import BwrapLauncher

    captured: dict[str, object] = {}
    real_popen = subprocess.Popen

    def factory(sandbox_mount):
        captured["factory_pass_fds"] = tuple(sandbox_mount.pass_fds or ())
        captured["factory_bind"] = sandbox_mount.bind_source
        launcher = BwrapLauncher(bwrap_path=PY).for_workspace(sandbox_mount)
        captured["argv"] = launcher.build_argv("print(1)", ["30", "1", ""])
        return launcher

    def fake_popen(argv, **kwargs):
        captured["popen_pass_fds"] = kwargs.get("pass_fds")
        kwargs.pop("pass_fds", None)
        return real_popen([PY, "-c", "raise SystemExit(0)"], **kwargs)

    monkeypatch.setattr(node_sandbox, "WORKSPACE_LAUNCHER_FACTORY", factory)
    monkeypatch.setattr(node_sandbox.sys, "platform", "linux")
    monkeypatch.setattr(node_sandbox.subprocess, "Popen", fake_popen)
    monkeypatch.setattr(gc, "WORKSPACE_FD_BIND_SUPPORTED", True)
    # Descriptor 9 is not open in THIS process; whether a descriptor is a live
    # directory has its own tests. What is under test here is the plumbing.
    monkeypatch.setattr(gc, "_require_live_directory", lambda fd, node_id: int(fd))

    # REAL descriptors, not chosen numbers: the compiler ACQUIRES the
    # capability now (Codex R3, P0 #2), which os.dup()s what it was given, and
    # a dup of a number nobody opened is EBADF. What the plumbing carries is
    # therefore the DUP - which is the point of the acquisition, since the
    # original's number is what a parallel discard would free.
    repo_fd = os.open(str(workspace / "README.md"), os.O_RDONLY)
    lease_fd = os.open(str(workspace / "README.md"), os.O_RDONLY)
    chain = EffectChain()
    chain.register_workspace(
        "checkout",
        _ChainMount(
            f"/proc/self/fd/{repo_fd}",
            repo_fd=repo_fd,
            lease_fd=lease_fd,
            pass_fds=(repo_fd,),
        ),
    )
    try:
        fn = gc._build_source_code_node(
            _code_node(workspace="checkout"),
            event_sink=None,
            effect_chain=chain,
            ancestors={"checkout"},
        )
        try:
            fn({})
        except Exception as exc:  # noqa: BLE001 - the child is a stub; see below
            # Recorded, not swallowed: this test asserts on a dict, and an
            # exception before the factory turns into a KeyError three lines
            # down that says nothing about what actually went wrong.
            captured["error"] = f"{type(exc).__name__}: {exc}"
    finally:
        chain.close_workspaces()

    assert "factory_bind" in captured, captured.get("error")
    bound = str(captured["factory_bind"])
    assert bound.startswith("/proc/self/fd/")
    bound_fd = int(bound.rsplit("/", 1)[-1])
    assert bound_fd != repo_fd, "the compiler bound the registry's own descriptor"
    assert captured["factory_pass_fds"] == (bound_fd,)
    argv = captured["argv"]
    index = argv.index("--bind")
    assert argv[index : index + 3] == ["--bind", bound, "/workspace"]
    assert captured["popen_pass_fds"] == (bound_fd,)


def test_a_lease_descriptor_is_never_used_for_the_bind(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """It names the LEASE; the bind source is the repository inside it.

    The flag is forced on so this discriminates on a host that cannot bind a
    descriptor at all -- otherwise every capability takes the path form and the
    test would pass whatever the code did with `lease_fd`.
    """
    monkeypatch.setattr(gc, "WORKSPACE_FD_BIND_SUPPORTED", True)
    monkeypatch.setattr(gc, "_require_live_directory", lambda fd, node_id: int(fd))
    built = gc._sandbox_workspace_mount(
        _ChainMount(str(workspace), lease_fd=7), "build"
    )
    assert built.pass_fds == ()
    assert built.bind_source == str(workspace)


def test_a_published_descriptor_that_is_dead_fails_the_node(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Carrying the sink's choice through does not mean carrying it unchecked.

    A closed descriptor makes `/proc/self/fd/N` name whatever this process has
    open at N, so the bind would mount something else entirely.
    """
    monkeypatch.setattr(gc, "WORKSPACE_FD_BIND_SUPPORTED", True)
    with pytest.raises(gc.CodeNodeError, match="descriptor is unusable"):
        gc._sandbox_workspace_mount(
            _ChainMount("/proc/self/fd/9999", repo_fd=9999, pass_fds=(9999,)),
            "build",
        )


def test_a_capability_that_already_chose_the_descriptor_is_carried_through(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(gc, "WORKSPACE_FD_BIND_SUPPORTED", True)
    monkeypatch.setattr(gc, "_require_live_directory", lambda fd, node_id: int(fd))
    built = gc._sandbox_workspace_mount(
        _ChainMount("/proc/self/fd/11", repo_fd=11, pass_fds=(11,)), "build"
    )
    assert built.bind_source == "/proc/self/fd/11"
    assert built.pass_fds == (11,)


def test_workspace_command_timeout_is_a_node_timeout() -> None:
    assert issubclass(gc.WorkspaceCommandTimeout, gc.NodeTimeoutError)
    error = gc.WorkspaceCommandTimeout("boom", node_id="build")
    assert error.node_id == "build"


def test_a_workspace_command_timeout_reaches_the_compiler_as_its_own_class(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The flag on the result, not a phrase in the message, is what classifies."""
    from tinyassets import node_sandbox

    class _TimedOut:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def run_sync(self, **kwargs):
            return node_sandbox.SandboxResult(
                node_id="build",
                success=False,
                error="workspace command timeout: ['sleep'] exceeded 1.000s",
                workspace_timeout=True,
            )

    monkeypatch.setattr(node_sandbox, "NodeSandbox", _TimedOut)
    # The compiler resolves the workspace launcher BEFORE it builds the sandbox
    # (a bind-less launcher would report /workspace as its root and emit no
    # --bind for it), so a fake NodeSandbox is no longer enough on a host
    # without bwrap.
    monkeypatch.setattr(
        node_sandbox,
        "WORKSPACE_LAUNCHER_FACTORY",
        lambda sandbox_mount: PlainSubprocessLauncher(
            workspace_bind=sandbox_mount.bind_source
        ),
    )
    chain = EffectChain()
    chain.register_workspace("checkout", WorkspaceMount(bind_source=str(workspace)))
    fn = gc._build_source_code_node(
        _code_node(workspace="checkout"),
        event_sink=None,
        effect_chain=chain,
        ancestors={"checkout"},
    )
    with pytest.raises(gc.WorkspaceCommandTimeout, match="workspace command timeout"):
        fn({})


def test_a_workspace_command_timeout_ends_the_run_and_the_jail(workspace: Path) -> None:
    """End to end through a real child: the runner leaves and the frame says why."""
    source = (
        "def run(state):\n"
        f"    ws.run([{PY!r}, '-c', 'import time; time.sleep(30)'], timeout=1)\n"
        "    return {'result': 'never'}\n"
    )
    started = time.monotonic()
    result = _run_node(workspace, source, timeout=30)
    assert result.success is False
    assert result.workspace_timeout is True
    assert "workspace command timeout" in result.error
    # It failed on the COMMAND budget, not by burning the node's 30s.
    assert time.monotonic() - started < 25


# ──────────────────────────────────────────────────────────────────────────────
# The bounds the design promised
# ──────────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("seconds", [1801, 3600, 0, -5])
def test_a_workspace_node_outside_the_timeout_bound_fails_at_compile(
    seconds: int,
) -> None:
    """It holds the universe's job lock and the host-wide slot for its whole run,
    so an unbounded one is a denial of service on every other universe."""
    # Built valid, then mutated: the dataclass refuses this at construction
    # (its own test below), so reaching the COMPILER's check needs a node that
    # was legal when it was made. Both gates exist because a persisted row and a
    # mutated object arrive by different doors.
    node = _code_node(workspace="checkout", timeout_seconds=300)
    node.timeout_seconds = seconds
    with pytest.raises(gc.CodeNodeError, match="outside the bound"):
        gc._build_source_code_node(
            node, event_sink=None, effect_chain=EffectChain(), ancestors={"checkout"}
        )


@pytest.mark.parametrize("seconds", [1, 300, 1800])
def test_a_workspace_node_inside_the_timeout_bound_compiles(seconds: int) -> None:
    fn = gc._build_source_code_node(
        _code_node(workspace="checkout", timeout_seconds=seconds),
        event_sink=None,
        effect_chain=EffectChain(),
        ancestors={"checkout"},
    )
    assert callable(fn)


def test_a_node_without_a_workspace_keeps_its_own_timeout() -> None:
    """The bound is the workspace's, not every code node's."""
    fn = gc._build_source_code_node(
        _code_node(timeout_seconds=7200),
        event_sink=None,
        effect_chain=EffectChain(),
        ancestors=None,
    )
    assert callable(fn)


def test_the_bound_is_enforced_when_a_persisted_node_loads() -> None:
    """A row written before the bound must fail to load, not compile into a node
    that holds the host-wide slot for as long as it likes."""
    from tinyassets.branches import NodeDefinition, NodeDefinitionValidationError

    good = NodeDefinition(
        node_id="build",
        display_name="Build",
        phase="draft",
        workspace="checkout",
        timeout_seconds=1800,
    )
    assert NodeDefinition.from_dict(good.to_dict()).timeout_seconds == 1800
    payload = dict(good.to_dict())
    payload["timeout_seconds"] = 5400
    with pytest.raises(NodeDefinitionValidationError):
        NodeDefinition.from_dict(payload)


def test_the_rss_watchdog_kills_the_jail_when_the_tree_passes_the_cap(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """RLIMIT_AS bounds each process; nothing bounds their SUM, and ws.run may
    start 128 of them. The reader is injected so the tree need not be real."""
    from tinyassets import node_sandbox

    monkeypatch.setattr(
        node_sandbox, "PROCESS_TREE_RSS_READER", lambda pid: 8 * 1024 * 1024 * 1024
    )
    monkeypatch.setattr(node_sandbox, "WORKSPACE_RSS_INTERVAL_SECONDS", 0.01)
    source = (
        "def run(state):\n"
        "    import time\n"
        "    time.sleep(20)\n"
        "    return {'result': 'never'}\n"
    )
    started = time.monotonic()
    result = _run_node(workspace, source, timeout=60)
    assert result.success is False
    assert "workspace memory cap 2 GiB exceeded" in result.error
    assert time.monotonic() - started < 30, "the watchdog did not cut it short"


def test_the_watchdog_does_not_fire_below_the_cap(workspace: Path, monkeypatch) -> None:
    from tinyassets import node_sandbox

    monkeypatch.setattr(node_sandbox, "PROCESS_TREE_RSS_READER", lambda pid: 1024)
    monkeypatch.setattr(node_sandbox, "WORKSPACE_RSS_INTERVAL_SECONDS", 0.01)
    result = _run_node(
        workspace, "def run(state):\n    return {'result': 'ok'}\n"
    )
    assert result.success is True, result.error
    assert result.output_state["result"] == "ok"


def test_an_unmeasurable_tree_does_not_kill_the_node(workspace, monkeypatch) -> None:
    """Killing a node because a measurement failed is worse than not measuring."""
    from tinyassets import node_sandbox

    monkeypatch.setattr(node_sandbox, "PROCESS_TREE_RSS_READER", lambda pid: -1)
    monkeypatch.setattr(node_sandbox, "WORKSPACE_RSS_INTERVAL_SECONDS", 0.01)
    result = _run_node(
        workspace, "def run(state):\n    return {'result': 'ok'}\n"
    )
    assert result.success is True, result.error


def test_a_node_without_a_workspace_has_no_watchdog(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The cap belongs to the workspace profile; a plain code node keeps its own."""
    from tinyassets import node_sandbox

    seen: list[int] = []

    def reader(pid: int) -> int:
        seen.append(pid)
        return 8 * 1024 * 1024 * 1024

    monkeypatch.setattr(node_sandbox, "PROCESS_TREE_RSS_READER", reader)
    monkeypatch.setattr(node_sandbox, "WORKSPACE_RSS_INTERVAL_SECONDS", 0.01)
    result = _run_node(
        None, "def run(state):\n    return {'result': 'ok'}\n"
    )
    assert result.success is True, result.error
    assert seen == []


@pytest.mark.skipif(os.name != "posix", reason="/proc is POSIX")
def test_the_real_reader_sees_this_process_tree() -> None:
    """The shipped reader, against a real tree: it must be positive and finite."""
    from tinyassets.node_sandbox import _read_process_tree_rss

    used = _read_process_tree_rss(os.getpid())
    assert used > 0
    assert used < 1024 * 1024 * 1024 * 1024


def test_the_workspace_profile_carries_the_rss_cap() -> None:
    assert WorkspaceLimits().rss_cap_bytes == 2 * 1024 * 1024 * 1024


# ──────────────────────────────────────────────────────────────────────────────
# Whole-jail termination (Linux + bwrap only)
# ──────────────────────────────────────────────────────────────────────────────


#: Process states that mean DEAD. `Z` is the one that matters: a killed
#: descendant whose parent never reaps it keeps its `/proc` entry forever, and
#: in a container whose PID 1 does not reap that is the normal end state — so
#: "the directory still exists" is not "the process is still running".
_DEAD_PROCESS_STATES = frozenset({"Z", "X", "x"})


def _marker_is_running(marker: str) -> bool:
    """Is a process carrying *marker* still ALIVE?

    The marker identifies the process — that part matters and is kept, because
    a pid can be reused and a pid comparison would then answer about somebody
    else. What the marker cannot tell us is whether the thing it names is still
    running, which is what the state letter in `/proc/<pid>/stat` is for. It
    sits after the LAST `)`, because the command field is parenthesised and may
    itself contain spaces and brackets.

    An entry that vanishes between the two reads is dead. An unrecognised state
    counts as ALIVE, so an unfamiliar kernel makes this test fail loudly rather
    than pass quietly: it exists to prove a descendant died.
    """
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        if marker.encode() not in cmdline:
            continue
        try:
            raw = (entry / "stat").read_bytes()
        except OSError:
            continue
        close = raw.rfind(b")")
        if close < 0:
            continue
        fields = raw[close + 2 :].split()
        if not fields:
            continue
        state = fields[0].decode("ascii", "replace")
        if state not in _DEAD_PROCESS_STATES:
            return True
    return False


@pytest.mark.skipif(
    sys.platform != "linux" or BWRAP is None,
    reason="the jail cascade needs Linux and bwrap",
)
def test_a_double_forked_sleeper_dies_with_the_jail(tmp_path: Path) -> None:
    """A `setsid` double-fork survives a process-group kill; it must not survive
    the jail. The sleeper's pid is namespace-local and means nothing on the host,
    so the host-side check is for a unique marker in any live command line.
    """
    from tinyassets.node_sandbox import BwrapLauncher

    root = tmp_path / "generation"
    root.mkdir()
    marker = "ta-jail-canary-" + os.urandom(6).hex()
    # The helper is written into the workspace by the TEST, not embedded in the
    # node's source. Two reasons, and both had bitten this test:
    #
    #   * the source denylist scans the node's own text, and a spawner spelled
    #     inline puts the literal `subprocess` in it -- the node would have been
    #     refused before it ran, so nothing this test asserts was ever exercised;
    #   * `sleep 300 <marker>` is refused by GNU sleep (a second operand), so
    #     the descendant this test is about exited instantly and "it is gone"
    #     was true for the wrong reason.
    #
    # The sleeper announces itself by writing a file, which is what makes the
    # "it died" assertion mean anything at all.
    (root / "spawn.py").write_text(
        "import subprocess, sys\n"
        "SLEEPER = (\n"
        "    \"import sys, time\\n\"\n"
        "    \"handle = open(sys.argv[1], 'w')\\n\"\n"
        "    \"handle.write('up')\\n\"\n"
        "    \"handle.close()\\n\"\n"
        "    \"time.sleep(300)\\n\"\n"
        ")\n"
        "subprocess.Popen(\n"
        "    [sys.executable, '-c', SLEEPER, sys.argv[1], sys.argv[2]],\n"
        "    start_new_session=True,\n"
        ")\n",
        encoding="utf-8",
    )
    source = (
        "def run(state):\n"
        f"    ws.run([{PY!r}, '/workspace/spawn.py', '/workspace/sleeper-up', "
        f"{marker!r}])\n"
        f"    ws.run([{PY!r}, '-c', 'import time; time.sleep(120)'], timeout=2)\n"
        "    return {'result': 'never'}\n"
    )
    launcher = BwrapLauncher(
        workspace_bind=str(root), allowed_workspace_roots=(str(tmp_path),)
    )
    sandbox = NodeSandbox(launcher=launcher, timeout=30)
    result = sandbox.run_sync(
        node_id="ws-node",
        source_code=source,
        input_state={},
        input_keys=[],
        output_keys=["result"],
        timeout=30,
        workspace=WorkspaceMount(bind_source=str(root)),
    )
    assert result.success is False
    assert result.workspace_timeout is True
    assert (root / "sleeper-up").is_file(), (
        "the detached sleeper never started, so proving it died proves nothing"
    )

    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if not _marker_is_running(marker):
            break
        time.sleep(0.2)
    assert not _marker_is_running(marker), (
        f"a double-forked descendant carrying {marker} is still RUNNING after "
        "the jail died; a zombie would be fine, this is not"
    )
