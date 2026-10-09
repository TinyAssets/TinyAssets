"""The first engine kind admits bytes, never executable/path/fd authority."""
import os
import runpy
import socket
from pathlib import Path
from types import SimpleNamespace

import pytest

from tinyassets import role_decoder, tool_images

LAUNCHER = runpy.run_path(str(Path(__file__).resolve().parents[1] / "deploy/role_launcher.py"))


@pytest.mark.parametrize("failure", [PermissionError, BrokenPipeError, RuntimeError, ValueError])
def test_selected_decoder_refusal_never_uses_local_subprocess(monkeypatch, failure):
    monkeypatch.setenv("TINYASSETS_CREDENTIAL_BROKER", "process")
    def refuse(*args):
        raise failure("synthetic private detail")
    def forbidden(*args, **kwargs):
        pytest.fail("selected decoder fell back to daemon subprocess")
    monkeypatch.setattr(role_decoder, "decode", refuse)
    monkeypatch.setattr(tool_images.subprocess, "run", forbidden)
    assert tool_images._decode_in_child(b"image", "image/png") == (
        "could not enter the admitted image decoder cell")


def test_selected_decoder_timeout_preserves_refusal_contract(monkeypatch):
    monkeypatch.setenv("TINYASSETS_CREDENTIAL_BROKER", "process")
    def timeout(*args):
        raise TimeoutError("transport timeout")
    monkeypatch.setattr(role_decoder, "decode", timeout)
    assert tool_images._decode_in_child(b"image", "image/png") == "took longer than 30 s to decode"


@pytest.mark.parametrize("changes", [
    {"kind": "provider-cli"}, {"mime": []}, {"mime": "text/python"},
    {"principal": ""}, {"command_center": "../bob"}, {"command_center": ".broker"},
    {"argv": ["/bin/sh"]}, {"env": {"LD_PRELOAD": "/tmp/evil"}},
])
def test_decoder_packet_cannot_select_executable_environment_or_foreign_path(tmp_path, changes):
    launcher = SimpleNamespace(data_root=tmp_path)
    packet = {"op": "SPAWN", "kind": "image-decoder", "principal": "alice",
              "command_center": "alice", "mime": "image/png"} | changes
    with pytest.raises(LAUNCHER["Refused"]):
        LAUNCHER["BrokerLauncher"]._decoder(launcher, None, packet, [9])


@pytest.mark.skipif(os.name != "posix", reason="Unix descriptor authority")
def test_decoder_refuses_regular_file_and_network_descriptors(tmp_path, monkeypatch):
    universe = tmp_path / "alice"
    universe.mkdir()
    # The unit oracle uses uid1001, as does the daemon. No mutation of identity.
    assert universe.stat().st_uid == 1001
    launcher = SimpleNamespace(data_root=tmp_path, daemon_pid=os.getpid(), engines={})
    packet = {"op": "SPAWN", "kind": "image-decoder", "principal": "alice",
              "command_center": "alice", "mime": "image/png"}
    def forbidden():
        pytest.fail("host descriptor admitted to an engine")
    monkeypatch.setattr(os, "fork", forbidden)
    with open(tmp_path / "private", "wb") as handle:
        with pytest.raises(LAUNCHER["Refused"]):
            LAUNCHER["BrokerLauncher"]._decoder(launcher, None, packet, [handle.fileno()])
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as network:
        with pytest.raises(LAUNCHER["Refused"]):
            LAUNCHER["BrokerLauncher"]._decoder(launcher, None, packet, [network.fileno()])


def test_daemon_rejects_foreign_owner_before_opening_launcher(tmp_path, monkeypatch):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker import supervisor
    from tinyassets.daemon_server import grant_universe_access

    (tmp_path / "bob").mkdir()
    grant_universe_access(tmp_path, universe_id="bob", actor_id="bob",
                          permission="admin", granted_by="bob")
    monkeypatch.setenv("TINYASSETS_CREDENTIAL_BROKER", "process")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_protect_daemon", lambda: None)
    with identity_context(Identity("alice", "alice")), pytest.raises(PermissionError):
        role_decoder.decode(b"image", "image/png", tmp_path / "bob")
