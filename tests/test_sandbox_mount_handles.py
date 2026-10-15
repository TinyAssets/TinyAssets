"""The mount setup owns host handles; sandbox payloads do not."""

import errno
import json
import os
import shutil
import subprocess
import sys

import pytest

from tinyassets.node_sandbox import BwrapLauncher, WorkspaceMount


@pytest.mark.skipif(sys.platform != "linux" or not shutil.which("bwrap"),
                    reason="mount descriptor consumption requires Linux bubblewrap")
def test_workspace_handle_is_consumed_before_payload(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    (checkout / "data.txt").write_text("workspace", encoding="utf-8")
    (checkout / "sitecustomize.py").write_text(
        "raise RuntimeError('checkout startup hook must not run')", encoding="utf-8")
    descriptor = os.open(checkout, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    launcher = BwrapLauncher().for_workspace(WorkspaceMount(
        f"/proc/self/fd/{descriptor}", pass_fds=(descriptor,)))
    source = """\
from __future__ import annotations
import errno, json, os, sys
from pathlib import Path
assert __name__ == '__main__'
assert sys.argv == ['-c', 'argument']
assert sys.stdin.read() == 'standard input'
assert Path('/workspace/data.txt').read_text() == 'workspace'
Path('/workspace/result.txt').write_text('written inside mount')
assert 'sitecustomize' not in sys.modules
assert 'TA_PARENT_MARKER' not in os.environ
open_handles = []
for name in os.listdir('/proc/self/fd'):
    number = int(name)
    if number > 2:
        try:
            os.fstat(number)
        except OSError as error:
            assert error.errno == errno.EBADF
        else:
            open_handles.append(number)
assert not open_handles, open_handles
print('standard error', file=sys.stderr)
print(json.dumps({'workspace': 'usable', 'host_handles': 'closed'}))
"""
    try:
        result = subprocess.run(
            launcher.build_argv(source, ["argument"]), pass_fds=launcher.pass_fds,
            env={**launcher.env(str(tmp_path)), "TA_PARENT_MARKER": "private"},
            input="standard input", capture_output=True, text=True, timeout=15,
        )
    finally:
        os.close(descriptor)
    assert result.returncode == 0, result.stderr
    assert result.stderr == "standard error\n"
    assert json.loads(result.stdout) == {"workspace": "usable", "host_handles": "closed"}
    assert (checkout / "result.txt").read_text() == "written inside mount"


@pytest.mark.parametrize("bad", [(True,), (0,), (1,), (2,), (-1,), ("3",)])
def test_launcher_never_closes_standard_io_or_accepts_untyped_descriptors(bad):
    with pytest.raises(ValueError, match="mount descriptors"):
        BwrapLauncher(pass_fds=bad).build_argv("raise AssertionError('not run')", [])


def test_bootstrap_preserves_future_imports_and_arguments_with_already_closed_fd():
    # Portable bootstrap check. Actual mount consumption needs the Linux test.
    launcher = BwrapLauncher(pass_fds=(700,))
    source = """\
from __future__ import annotations
import os, sys
assert __name__ == '__main__'
assert sys.argv == ['-c', 'one', 'two']
assert sys.stdin.read() == 'message'
try:
    os.fstat(700)
except OSError as error:
    assert error.errno == 9
else:
    raise AssertionError('descriptor survived')
print('ok')
"""
    argv = launcher.build_argv(source, ["one", "two"])
    child_argv = argv[argv.index("--") + 1:]
    assert child_argv[1] == "-I"
    result = subprocess.run(child_argv, input="message", capture_output=True,
                            text=True, timeout=10, close_fds=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout == "ok\n"


def test_closing_bootstrap_does_not_swallow_payload_failure():
    argv = BwrapLauncher(pass_fds=(700,)).build_argv("raise SystemExit(17)", [])
    result = subprocess.run(argv[argv.index("--") + 1:],
                            capture_output=True, text=True, timeout=10, close_fds=True)
    assert result.returncode == 17


def test_closing_bootstrap_does_not_hide_non_ebadf_errors(monkeypatch):
    from tinyassets.node_sandbox import _CLOSE_MOUNT_FDS_SCRIPT

    def refuse_close(fd):
        raise OSError(errno.EIO, "test close failure")

    monkeypatch.setattr(os, "close", refuse_close)
    monkeypatch.setattr(sys, "argv", ["-c", "700", "raise AssertionError('not run')"])
    with pytest.raises(OSError) as caught:
        exec(_CLOSE_MOUNT_FDS_SCRIPT, {})
    assert caught.value.errno == errno.EIO
