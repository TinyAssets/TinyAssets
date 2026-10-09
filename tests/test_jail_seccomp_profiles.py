"""The named cell seccomp profiles, loaded by real bubblewrap (D9).

The decoder loads one of these into every owner cell; this proves each
profile's syscall answers in a real filter.
"""
import json
import os
import shutil
import subprocess
import sys

import pytest

pytestmark = [
    pytest.mark.skipif(sys.platform != "linux" or shutil.which("bwrap") is None,
                       reason="real bubblewrap is Linux-only"),
    pytest.mark.real_jail,
]

ROLE_PROFILE_PROBE = r'''
import ctypes, errno, json, os
libc = ctypes.CDLL(None, use_errno=True)
out = {}
for name, operation in (
    ("symlink", lambda: os.symlink("target", "/tmp/link")),
    ("fifo", lambda: os.mkfifo("/tmp/fifo")),
):
    try:
        operation()
        out[name] = 0
    except OSError as exc:
        out[name] = exc.errno
for name, nr in (("io_uring", 425), ("clone3", 435)):
    ctypes.set_errno(0)
    result = libc.syscall(nr, 0, 88 if name == "clone3" else 0, 0, 0, 0, 0)
    out[name] = ctypes.get_errno() if result == -1 else 0
ctypes.set_errno(0)
result = libc.unshare(0x10000000)
out["newuser"] = ctypes.get_errno() if result == -1 else 0
print(json.dumps(out))
'''


@pytest.mark.parametrize("profile,link_errno,newuser_errno,clone_errno", [
    ("cell-deny", 1, 1, 38),
    ("cell-links", 0, 1, 38),
    ("cell-nested", 0, 0, 14),
])
def test_named_profile_in_real_bubblewrap(profile, link_errno, newuser_errno, clone_errno):
    from tinyassets.providers.jail_seccomp import program_fd

    fd = program_fd(profile=profile)
    try:
        result = subprocess.run([
            "bwrap", "--die-with-parent", "--unshare-user", "--unshare-pid",
            "--unshare-net", "--ro-bind", "/", "/", "--tmpfs", "/tmp",
            "--proc", "/proc", "--dev", "/dev", "--chdir", "/tmp",
            "--seccomp", str(fd), sys.executable, "-I", "-c", ROLE_PROFILE_PROBE,
        ], pass_fds=(fd,), capture_output=True, text=True, timeout=15)
    finally:
        os.close(fd)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "symlink": link_errno, "fifo": 1, "io_uring": 1,
        "newuser": newuser_errno, "clone3": clone_errno,
    }
