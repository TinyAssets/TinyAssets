"""Runtime relay creators keep the declared group without following planted entries."""
import os
import socket
import stat
import sys

import pytest

from tinyassets import role_modes, role_relays, universe_egress

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="Linux role relay descriptors")


@pytest.fixture
def root(tmp_path, monkeypatch):
    monkeypatch.setattr(role_modes, "WORK_GID", os.getgid())
    monkeypatch.setattr(role_modes, "DAEMON_UID", os.getuid())
    (tmp_path / "alice").mkdir()
    return tmp_path


def test_actual_proxy_and_engine_relay_publish_declared_modes(root, monkeypatch):
    monkeypatch.setattr(universe_egress, "_route_port", lambda *args: 12345)
    paths = [universe_egress.ensure_proxy(root / "alice"),
             universe_egress.ensure_engine_relay(root / "alice", actor_id="alice", graph_id="g")[0]]
    for path in paths:
        assert stat.S_IMODE(path.stat().st_mode) == 0o660
        assert path.stat().st_gid == os.getgid()
        assert stat.S_IMODE(path.parent.stat().st_mode) == 0o2710
        assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o711
        assert role_relays.identity(path) == (path.stat().st_dev, path.stat().st_ino)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(2)
        client.connect(str(paths[0]))
        client.sendall(b"CONNECT 127.0.0.1:443 HTTP/1.1\r\n\r\n")
        assert b"403 Forbidden" in client.recv(4096)


@pytest.mark.parametrize("attack", ["symlink", "fifo", "hardlink", "regular"])
def test_planted_socket_entry_is_never_removed_or_chmodded(root, attack):
    parent = root / ".universe-sidecars/alice"
    parent.mkdir(parents=True)
    path = parent / f"egress-{os.getpid()}.sock"
    outside = root / "peer-secret"
    outside.write_bytes(b"retained")
    if attack == "symlink":
        path.symlink_to(outside)
    elif attack == "fifo":
        os.mkfifo(path)
    elif attack == "hardlink":
        os.link(outside, path)
    else:
        path.write_bytes(b"retained entry")
    before = outside.stat()
    with pytest.raises(PermissionError):
        universe_egress.ensure_proxy(root / "alice")
    assert path.lstat()
    after = outside.stat()
    assert outside.read_bytes() == b"retained"
    assert (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns) == (
        before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns)


def test_symlink_sidecar_parent_refuses_before_touching_peer(root):
    outside = root / "peer"
    outside.mkdir(mode=0o700)
    (root / ".universe-sidecars").symlink_to(outside, target_is_directory=True)
    before = outside.stat()
    with pytest.raises(OSError):
        universe_egress.ensure_proxy(root / "alice")
    assert not list(outside.iterdir())
    assert outside.stat().st_mode == before.st_mode


def test_proxy_liveness_refuses_substituted_socket_entry(root):
    path = universe_egress.ensure_proxy(root / "alice")
    path.unlink()
    path.write_bytes(b"not a socket")
    with pytest.raises(PermissionError):
        universe_egress.ensure_proxy(root / "alice")
    assert path.read_bytes() == b"not a socket"
