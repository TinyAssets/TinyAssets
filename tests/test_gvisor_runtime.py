"""The OCI shape handed to runsc is the one G4 measured."""

from __future__ import annotations

import os
import socket
from pathlib import Path

import pytest

from tinyassets.boxes.gvisor_runtime import OVERLAY, TMP_BYTES, GvisorRuntime
from tinyassets.boxes.provider import BoxError

pytestmark = pytest.mark.skipif(os.name != "posix", reason="runsc is Linux-only")


def _runtime(tmp_path: Path) -> GvisorRuntime:
    runsc = tmp_path / "runsc"
    runsc.write_text("#!/bin/sh\nexit 0\n")
    runsc.chmod(0o700)
    for name in ("rootfs", "owners"):
        (tmp_path / name).mkdir()
    return GvisorRuntime(
        runsc=runsc, rootfs=tmp_path / "rootfs", owner_store=tmp_path / "owners",
        state_root=tmp_path / "state", bundle_root=tmp_path / "bundles",
        cgroup_parent=Path("/sys/fs/cgroup/system.slice/boxhostd.service"),
    )


def test_oci_bundle_exposes_only_owner_tree_relay_and_bounded_tmp(tmp_path):
    runtime = _runtime(tmp_path)
    relay = tmp_path / "relay.sock"
    with socket.socket(socket.AF_UNIX) as sock:
        sock.bind(str(relay))
        config = runtime._config(
            {"process": {}, "mounts": [
                {"type": "bind", "destination": "/data", "source": "/data"},
                {"type": "proc", "destination": "/proc", "source": "proc"},
            ]},
            uid=300001, gid=300001, tree=tmp_path / "owners" / "300001",
            relay=relay, memory_bytes=1024**3,
        )
    assert config["root"] == {"path": str(tmp_path / "rootfs"), "readonly": True}
    assert [mount["destination"] for mount in config["mounts"]] == [
        "/proc", "/home/owner", "/run/boxhostd/relay.sock", "/tmp",
    ]
    assert config["mounts"][-1]["options"][-1] == f"size={TMP_BYTES}"
    assert config["process"]["env"] == [
        "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/home/owner",
        "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright",
    ]
    assert config["process"]["noNewPrivileges"] is True
    assert all(not grants for grants in config["process"]["capabilities"].values())
    assert config["linux"]["resources"]["pids"]["limit"] == 1024
    assert config["linux"]["cgroupsPath"].endswith("/boxhostd.service/owner-300001")


def test_every_runsc_invocation_uses_memory_overlay_and_no_network(tmp_path, monkeypatch):
    runtime = _runtime(tmp_path)
    calls = []

    def record(argv, **kwargs):
        calls.append(argv)
        return object()

    monkeypatch.setattr("tinyassets.boxes.gvisor_runtime.subprocess.run", record)
    runtime._command("state", "owner-300001")
    assert calls[0][1:10] == [
        "--root", str(tmp_path / "state"), "--systemd-cgroup=false",
        "--network=none", "--platform=systrap", "--host-uds=all", f"--overlay2={OVERLAY}",
        "state", "owner-300001",
    ]


def test_persistent_daemon_launch_has_mount_secret_socket_static_env_and_memory_cap(tmp_path):
    runtime = _runtime(tmp_path)
    relay = tmp_path / "relay.sock"
    secret = tmp_path / "secret"
    secret.write_text("s" * 43)
    secret.chmod(0o400)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(relay))
        config = runtime._config(
            {"process": {}, "mounts": [], "linux": {}}, uid=300001, gid=300001,
            tree=tmp_path / "owners/300001", relay=relay, memory_bytes=1024**3,
            app_dir=tmp_path / "300001", secret_file=secret)
    assert config["process"]["args"] == [
        "/opt/venv/bin/python", "-B", "-m", "tinyassets.owner_daemon",
        "--socket", "/run/owner/app.sock", "--relay", "/run/boxhostd/relay.sock",
        "--secret-file", "/run/boxhostd/secret"]
    assert config["process"]["user"] == {"uid": 300001, "gid": 300001}
    assert config["linux"]["resources"]["memory"]["limit"] == 1024**3
    mounts = {item["destination"]: item for item in config["mounts"]}
    assert "ro" in mounts["/run/boxhostd/secret"]["options"]
    assert mounts["/run/owner"]["source"] == str(tmp_path / "300001")
    assert mounts["/home/owner"]["source"] == str(tmp_path / "owners/300001")
    assert config["process"]["env"] == [
        "PATH=/usr/local/bin:/usr/bin:/bin", "HOME=/home/owner",
        "PLAYWRIGHT_BROWSERS_PATH=/opt/ms-playwright"]


@pytest.mark.skipif(os.name == "posix" and os.geteuid() != 0,
                    reason="quota ownership guard requires --as-root; "
                           "owner=c6-owner-box runs-in=linux_oracle.py")
def test_owner_tree_must_match_permanent_uid_and_private_mode(tmp_path):
    runtime = _runtime(tmp_path)
    tree = tmp_path / "owners" / "300001"
    tree.mkdir(mode=0o700)
    os.chown(tree, 300001, 300001)
    assert runtime._owner_tree(300001, 300001) == tree
    tree.chmod(0o755)
    with pytest.raises(BoxError, match="0700"):
        runtime._owner_tree(300001, 300001)
    tree.chmod(0o700)
    with pytest.raises(BoxError, match="UID/GID"):
        runtime._owner_tree(300001, 300002)
