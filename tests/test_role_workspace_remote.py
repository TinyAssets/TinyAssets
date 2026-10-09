"""The ``workspace-remote`` cell's admission: the mapper and the image entry.

Two boundaries, both of them privileged and neither of them reachable from a
test process as itself:

* the bounded launcher's mapper (``deploy/role_owner_launcher.py``), which is
  the only thing that turns a daemon request into a cell. It is driven here
  with the descriptors a test CAN hold, so what is asserted is every refusal:
  a schema it does not know, a root that is not the admitted command center,
  a relay that is not that center's own.
* the image entry (``deploy/role_git.py``), whose bubblewrap argv IS the
  confinement. It is built here with the two loads stubbed, so the mounts, the
  seccomp profile and the namespace flags are asserted rather than assumed.

The cell's own operation is tested in tests/test_workspace_remote_cell.py; the
real mapper, the real uids and the real bwrap are the root oracle's.
"""

from __future__ import annotations

import os
import runpy
import socket
import stat
import sys
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.role_split,
    pytest.mark.skipif(sys.platform == "win32", reason="Unix descriptors and credentials"),
]

ROOT = Path(__file__).resolve().parents[1]
LAUNCHER = runpy.run_path(str(ROOT / "deploy" / "role_owner_launcher.py"))

PRINCIPAL = "alice"
CENTER = "cc-alice"
MACHINE = 300001
INNER = MACHINE - 300000


def _mapper(data_root: Path):
    """A mapper object with only the state ``_decoder`` reads."""
    value = object.__new__(LAUNCHER["OwnerLauncher"])
    value.bindings = {(PRINCIPAL, CENTER): MACHINE}
    value.delete_fences = {}
    value.jobs = {}
    value.package_jobs = set()
    value.data_root = str(data_root)
    # In production the mapper sees the daemon as the overflow identity; here
    # the "daemon" is this process, so its own ids stand in -- which is what
    # makes the authenticated-endpoint check pass and lets the tests below
    # reach the checks they are actually about.
    value.daemon_pid = os.getpid()
    value.overflow_uid = os.getuid()
    value.overflow_gid = os.getgid()
    value.launch = {"_capset": lambda bits: None, "_assert_caps": lambda bits: None,
                    "close_descriptors": lambda retained: None,
                    "status": lambda: {}}
    return value


def _request(**over):
    request = {"op": "START", "kind": "workspace-remote", "principal": PRINCIPAL,
               "command_center": CENTER, "egress": True}
    request.update(over)
    return request


@pytest.mark.parametrize("over,count", [
    ({"op": "SPAWN"}, 3),                      # a blocking spawn cannot stream
    ({"egress": "yes"}, 4),                    # the relay flag is a boolean
    ({"egress": 1}, 4),                        # ...and not a truthy number
    ({"workspace": True}, 4),                  # a field this kind does not carry
    ({}, 3),                                   # one descriptor short
    ({}, 5),                                   # one descriptor too many
    ({"egress": False}, 4),                    # no relay, but a relay was sent
    ({"principal": 7}, 4),
    ({"command_center": None}, 4),
])
def test_the_mapper_refuses_a_workspace_remote_request_outside_its_schema(
    tmp_path, over, count
):
    mapper = _mapper(tmp_path)
    request = _request(**over)
    # Deliberately invalid descriptor numbers: the schema is checked before any
    # of them is read, so a refusal here proves nothing was touched.
    with pytest.raises(ValueError, match="unsupported owner engine"):
        mapper._decoder(request, [-1] * count)


def test_the_mapper_refuses_a_root_that_is_not_the_admitted_command_center(tmp_path):
    """The cell is bound to ONE command center, resolved from the binding.

    The request names a principal and a center; which directory that is, is
    the mapper's to check, not the caller's to supply.
    """
    mapper = _mapper(tmp_path)
    (tmp_path / "elsewhere").mkdir()
    data, child = socket.socketpair()
    status, child_status = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    relay, relay_peer = socket.socketpair()
    root_fd = os.open(tmp_path / "elsewhere", os.O_RDONLY | os.O_DIRECTORY)
    with data, child, status, child_status, relay, relay_peer:
        try:
            with pytest.raises(ValueError, match="tool root does not match admitted center"):
                mapper._decoder(_request(), [child.fileno(), root_fd, relay.fileno(),
                                            child_status.fileno()])
        finally:
            os.close(root_fd)


def test_the_mapper_refuses_a_relay_that_is_not_this_centers_own(tmp_path):
    """The egress socket must be the one the daemon bound for this center.

    A socket from anywhere else would be another center's network, or the
    caller's -- which is the whole point of pinning it by path and inode.
    """
    mapper = _mapper(tmp_path)
    center = tmp_path / CENTER
    center.mkdir()
    # The binding is what decides the owner's inner gid; here it is this
    # process's, so the ROOT passes and only the relay is in question.
    mapper.bindings = {(PRINCIPAL, CENTER): os.getgid() + 300000}
    data, child = socket.socketpair()
    status, child_status = socket.socketpair(socket.AF_UNIX, socket.SOCK_SEQPACKET)
    relay, relay_peer = socket.socketpair()
    root_fd = os.open(center, os.O_RDONLY | os.O_DIRECTORY)
    with data, child, status, child_status, relay, relay_peer:
        try:
            with pytest.raises(ValueError, match="does not match admitted center"):
                mapper._decoder(_request(), [child.fileno(), root_fd, relay.fileno(),
                                            child_status.fileno()])
        finally:
            os.close(root_fd)


def test_the_kind_is_mounted_streaming_and_carries_exactly_one_relay():
    """The mapper's own tables, read rather than re-derived.

    A kind that is not in ``mounted`` gets no directory descriptor, and one
    whose socket count is wrong gets the wrong fixed slot -- both silent.
    """
    source = (ROOT / "deploy" / "role_owner_launcher.py").read_text(encoding="utf-8")
    assert "'workspace-remote', 'preview-write'" in source, "the kind must be mounted"
    assert "'tool-jail', 'package', 'workspace-remote'" in source, "one relay slot: egress"
    assert "'node-sandbox', 'workspace-remote'" in source, "a clone needs the long deadline"
    assert "'enter-remote', str(inner)" in source, "the entry is the image's, by fixed path"


# --------------------------------------------------------------------------- #
# the image entry
# --------------------------------------------------------------------------- #


def _entry(monkeypatch, *, mode: str, center=(1, 2), relay=(3, 4)):
    """``enter_remote``'s bubblewrap argv, with only its two loads stubbed."""
    module = runpy.run_path(str(ROOT / "deploy" / "role_git.py"))
    captured: list[list[str]] = []

    def fake_run_path(path):
        if path.endswith("ta-decoder.py"):
            return {"identity": lambda uid: None,
                    "namespaces": lambda: {"mnt": "m", "pid": "p", "ipc": "i", "net": "n"}}
        if path.endswith("jail_seccomp.py"):
            return {"program_fd": lambda profile: ("seccomp", profile)}
        raise AssertionError(f"the entry loaded {path!r}")

    class Info:
        def __init__(self, mode_bits, dev, ino, nlink=1):
            self.st_mode = mode_bits
            self.st_dev, self.st_ino, self.st_nlink = dev, ino, nlink
            self.st_gid = INNER
            self.st_uid = 65534

    def fake_fstat(fd):
        if fd == 3:
            return Info(stat.S_IFDIR | 0o750, *center)
        if fd == 4:
            return Info(stat.S_IFSOCK | 0o660, *relay)
        raise AssertionError(f"the entry stat'ed fd {fd}")

    monkeypatch.setattr(module["runpy"], "run_path", fake_run_path)
    monkeypatch.setattr(module["os"], "fstat", fake_fstat)
    monkeypatch.setattr(module["os"], "set_inheritable", lambda fd, value: None)
    monkeypatch.setattr(module["os"], "execv",
                        lambda path, argv: captured.append(argv) or None)
    module["enter_remote"](INNER, "/data", mode)
    assert captured, "the entry did not exec bubblewrap"
    return captured[0]


def test_the_cell_is_an_unshared_capability_free_bubblewrap(monkeypatch):
    argv = _entry(monkeypatch, mode="e")
    assert argv[0] == "/usr/bin/bwrap"
    for flag in ("--die-with-parent", "--new-session", "--unshare-all", "--clearenv"):
        assert flag in argv, flag
    assert argv[argv.index("--cap-drop") + 1] == "ALL"
    assert argv[argv.index("--seccomp") + 1] == str(("seccomp", "cell-links"))
    assert "--tmpfs" in argv and argv[argv.index("--tmpfs") + 1] == "/tmp"
    assert argv[argv.index("--chdir") + 1] == "/tmp"


def test_the_cell_mounts_the_center_and_its_relay_and_nothing_else_writable(monkeypatch):
    argv = _entry(monkeypatch, mode="e")
    binds = [(argv[i + 1], argv[i + 2]) for i, flag in enumerate(argv)
             if flag == "--bind-fd"]
    assert binds == [("3", "/center"), ("4", "/remote-egress.sock")]
    read_only = [argv[i + 1] for i, flag in enumerate(argv) if flag == "--ro-bind"]
    # Only paths that exist are bound, and the oracle's checkout is not the
    # image's /app, so the invariant is what must NOT be there.
    assert "/usr" in read_only
    assert "/etc/ssl/certs" not in read_only, "the broker owns the TLS session"
    assert "/data" not in read_only and "/data" not in [b[1] for b in binds]
    assert all(flag != "--bind" for flag in argv), "no path-named writable mount"


def test_a_routeless_cell_binds_no_socket_at_all(monkeypatch):
    """Making an empty workspace reaches nothing, so it gets no network path."""
    argv = _entry(monkeypatch, mode="-")
    binds = [(argv[i + 1], argv[i + 2]) for i, flag in enumerate(argv)
             if flag == "--bind-fd"]
    assert binds == [("3", "/center")]
    assert "/remote-egress.sock" not in argv
    assert argv[-5:-1] == ["inside-remote", str(INNER), "/data", "-"]


def test_the_entry_refuses_a_center_that_is_not_the_owners(monkeypatch):
    module = runpy.run_path(str(ROOT / "deploy" / "role_git.py"))

    class Info:
        st_mode = stat.S_IFDIR | 0o750
        st_dev = st_ino = 1
        st_gid = INNER + 7          # another owner's group
        st_uid = 65534
        st_nlink = 1

    monkeypatch.setattr(module["runpy"], "run_path",
                        lambda path: {"identity": lambda uid: None})
    monkeypatch.setattr(module["os"], "fstat", lambda fd: Info())
    with pytest.raises(RuntimeError, match="not labelled for the admitted identity"):
        module["enter_remote"](INNER, "/data", "-")


def test_the_entry_cli_accepts_only_its_two_documented_shapes():
    """The image runs this file by fixed path with fixed arguments; anything
    else is a bootstrap nobody wrote."""
    import subprocess

    for argv in (["enter-remote"], ["enter-remote", str(INNER)],
                 ["enter-remote", str(INNER), "/data", "x"],
                 ["enter-remote", "0", "/data", "e"],
                 ["inside-remote", str(INNER), "/data", "e"],
                 ["inside-remote", str(INNER), "/data", "x", "{}"]):
        done = subprocess.run(
            [sys.executable, "-I", "-B", str(ROOT / "deploy" / "role_git.py"), *argv],
            capture_output=True, text=True,
        )
        assert done.returncode != 0, argv
        assert "unsupported workspace-git bootstrap" in done.stderr, (argv, done.stderr)
