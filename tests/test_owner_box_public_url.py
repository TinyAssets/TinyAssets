"""Production launch environment and missing-public-URL startup refusal."""
import socket
from pathlib import Path

import pytest

from tinyassets.boxes.gvisor_runtime import GvisorRuntime
from tinyassets.boxes.provider import BoxError

pytestmark = pytest.mark.verifies('owner-daemon-in-box:T1')


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


@pytest.mark.parametrize("origin, allowed", [("https://tinyassets.io", True),
                                             ("https://foreign.example", False)])
def test_box_origin_guards_use_public_resource(monkeypatch, origin, allowed):
    from starlette.requests import Request

    from tinyassets.auth.wellknown import _server_url, protected_resource_metadata
    from tinyassets.onboarding import _same_origin_json
    from tinyassets.onboarding.file_upload import _same_origin_octet

    monkeypatch.delenv("UNIVERSE_SERVER_AUTH", raising=False)
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    resource = protected_resource_metadata()["resource"]
    assert resource == _server_url() == "https://tinyassets.io"
    scope = {"type": "http", "method": "POST", "path": "/", "scheme": "http",
             "server": ("internal-box", 8001), "query_string": b""}
    for content_type, guard in [("application/json", _same_origin_json),
                                ("application/octet-stream", _same_origin_octet)]:
        request = Request(scope | {"headers": [(b"host", b"internal-box:8001"),
            (b"origin", origin.encode()), (b"content-type", content_type.encode())]})
        assert guard(request, resource) is allowed


def test_preflight_renders_public_origin_before_install_and_restart():
    from scripts.boxhostd_install_files import ship_list

    root = Path(__file__).resolve().parents[1]
    installer = (root / "deploy/install-boxhostd-preflight.sh").read_text()
    render = installer.index('bash "${source_dir}/install-tinyassets-env.sh" render-daemon-env')
    require = installer.index("WORKOS_MCP_RESOURCE is required")
    install = installer.index('bundle_changes=')
    restart = installer.index('systemctl restart boxhostd.service')
    start = installer.index('systemctl enable --now boxhostd.service')
    assert render < require < install < restart < start
    assert '[[ "$previous_box_env" == "$rendered_box_env" ]] || changed=1' in installer
    assert "deploy/install-tinyassets-env.sh" in ship_list(root)
