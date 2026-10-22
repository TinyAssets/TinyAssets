"""Production launch environment and missing-public-URL startup refusal."""
import socket
from pathlib import Path

import pytest

from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.provider import BoxError


def test_box_config_carries_only_explicit_public_url(tmp_path, monkeypatch):
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    monkeypatch.setenv("UNRELATED_SECRET", "must-not-be-forwarded")
    runtime = object.__new__(GvisorRuntime)
    runtime.rootfs = tmp_path
    runtime.cgroup_parent = Path("/sys/fs/cgroup/public-url-test")
    relay = tmp_path / "relay.sock"
    with socket.socket(socket.AF_UNIX) as listener:
        listener.bind(str(relay))
        config = runtime._config(
            {"process": {}}, uid=300001, gid=300001, volume_mount={},
            volume_generation=1, relay=relay, memory_bytes=1024**3,
        )
    env = dict(entry.split("=", 1) for entry in config["process"]["env"])
    assert env["UNIVERSE_SERVER_URL"] == "https://tinyassets.io"
    assert "UNRELATED_SECRET" not in env


@pytest.mark.parametrize("value", [None, "", "   "])
def test_box_start_refuses_missing_public_url_before_runsc(tmp_path, monkeypatch, value):
    if value is None:
        monkeypatch.delenv("UNIVERSE_SERVER_URL", raising=False)
    else:
        monkeypatch.setenv("UNIVERSE_SERVER_URL", value)
    runtime = object.__new__(GvisorRuntime)
    runtime.bundle_root = tmp_path
    with pytest.raises(BoxError, match="UNIVERSE_SERVER_URL is required"):
        runtime.start(300001, 300001, relay=tmp_path / "relay.sock",
                      memory_bytes=1024**3, volume_mount={}, volume_generation=1)
    assert list(tmp_path.iterdir()) == []
