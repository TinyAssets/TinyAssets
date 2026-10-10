"""The first engine kind admits bytes, never executable/path/fd authority."""
import os
import runpy
import socket
from pathlib import Path

import pytest

from tinyassets import role_decoder, tool_images

# These drive the real bounded launcher and cells; no double is installed.
pytestmark = pytest.mark.role_split

LAUNCHER = runpy.run_path(
    str(Path(__file__).resolve().parents[1] / "deploy/role_owner_launcher.py"))


@pytest.mark.skipif(os.name != 'posix', reason='owner inode labels and O_PATH')
@pytest.mark.parametrize('invalid', [None, 'mount-count', 'hardlink', 'foreign-owner'])
def test_tool_mount_budget_counts_content_and_retains_inode_guards(tmp_path, invalid):
    decoder = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'deploy/role_decoder.py'))
    required = ('.agent-workspace', 'skills', 'prompts', 'extensions',
                'workflows', 'bin', 'notes', 'wiki')
    for name in required:
        (tmp_path / name).mkdir()
    for index in range(600):
        (tmp_path / f'.platform-{index}').touch()
    for name in ('owner.json', 'provider_definitions.json'):
        (tmp_path / name).touch()
    (tmp_path / 'identity.md').write_text('owner content')
    if invalid == 'mount-count':
        for index in range(256):
            (tmp_path / f'content-{index}').touch()
    elif invalid == 'hardlink':
        os.link(tmp_path / 'identity.md', tmp_path / 'alias')
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    # The decoder exec owns inherited fds. Close the descriptors opened by this
    # in-process test on success AND refusal, without touching pytest's handles.
    before = {name for name in os.listdir('/proc/self/fd')
              if os.path.exists('/proc/self/fd/' + name)}
    try:
        if invalid:
            with pytest.raises(ValueError, match='too many|exclusive owner content inode'):
                decoder['tool_mounts'](os.getuid() + (invalid == 'foreign-owner'), fd)
        else:
            mounts = decoder['tool_mounts'](os.getuid(), fd)
            destinations = [mounts[i + 2] for i, value in enumerate(mounts)
                            if value == '--bind-fd']
            assert set(destinations) == {'/center/' + name for name in (*required, 'identity.md')}
            assert mounts[-2:] == ['--remount-ro', '/center']
    finally:
        for name in set(os.listdir('/proc/self/fd')) - before:
            try:
                os.close(int(name))
            except OSError:
                pass
        os.close(fd)


def _launcher():
    return object.__new__(LAUNCHER["OwnerLauncher"])


@pytest.mark.parametrize("failure", [PermissionError, BrokenPipeError, RuntimeError, ValueError])
def test_selected_decoder_refusal_never_uses_local_subprocess(monkeypatch, failure):
    """A refused cell is a refusal line, not a decode the daemon performs."""
    def refuse(*args, **kwargs):
        raise failure("synthetic private detail")

    monkeypatch.setattr(role_decoder, "decode", refuse)
    # There is no daemon-side decoder left to fall back to.
    assert not hasattr(tool_images, "subprocess")
    assert tool_images._decode_in_child(b"image", "image/png") == (
        "could not enter the admitted image decoder cell")


def test_selected_decoder_timeout_preserves_refusal_contract(monkeypatch):
    def timeout(*args, **kwargs):
        raise TimeoutError("transport timeout")

    monkeypatch.setattr(role_decoder, "decode", timeout)
    assert tool_images._decode_in_child(b"image", "image/png") == "took longer than 30 s to decode"


@pytest.mark.parametrize("changes", [
    {"kind": "provider-cli"}, {"mime": []}, {"mime": "text/python"},
    {"argv": ["/bin/sh"]}, {"env": {"LD_PRELOAD": "/tmp/evil"}},
    {"profile": "cell-links"}, {"workspace": True},
])
def test_decoder_packet_cannot_select_executable_environment_or_foreign_path(changes):
    """The mapper reads a fixed field set. Anything else is not an engine."""
    packet = {"op": "START", "kind": "image-decoder", "principal": "alice",
              "command_center": "alice", "mime": "image/png"} | changes
    with pytest.raises(ValueError, match="unsupported owner engine"):
        _launcher()._decoder(packet, [9, 10])


@pytest.mark.parametrize("received", [[], [9], [9, 10, 11]])
def test_decoder_packet_must_carry_exactly_its_descriptors(received):
    packet = {"op": "START", "kind": "image-decoder", "principal": "alice",
              "command_center": "alice", "mime": "image/png"}
    with pytest.raises(ValueError, match="unsupported owner engine"):
        _launcher()._decoder(packet, received)


@pytest.mark.skipif(os.name != "posix", reason="Unix descriptor authority")
def test_decoder_refuses_regular_file_and_network_descriptors(tmp_path):
    """A cell's data channel is a daemon socketpair, never a host handle."""
    launcher = _launcher()
    launcher.daemon_pid = os.getpid()
    launcher.overflow_uid = os.getuid()
    launcher.overflow_gid = os.getgid()
    with open(tmp_path / "private", "wb") as handle:
        with pytest.raises(ValueError, match="daemon socketpair"):
            launcher._daemon_endpoint(handle.fileno(), socket.SOCK_STREAM)
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as network:
        with pytest.raises(ValueError, match="daemon socketpair|not daemon-owned"):
            launcher._daemon_endpoint(network.fileno(), socket.SOCK_STREAM)
    # A connected AF_UNIX pair from this process IS admitted, so the refusals
    # above are the descriptor's shape and not a check that refuses everything.
    one, other = socket.socketpair()
    with one, other:
        launcher._daemon_endpoint(one.fileno(), socket.SOCK_STREAM)


def test_daemon_rejects_foreign_owner_before_opening_launcher(tmp_path, monkeypatch):
    """Scope is checked first: a foreign center never reaches the channel."""
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker import supervisor
    from tinyassets.daemon_server import grant_universe_access

    (tmp_path / "bob").mkdir()
    grant_universe_access(tmp_path, universe_id="bob", actor_id="bob",
                          permission="admin", granted_by="bob")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(supervisor, "_protect_daemon", lambda: None)
    monkeypatch.setattr(role_decoder, "_bounded_client", None)
    with identity_context(Identity("alice", "alice")), pytest.raises(PermissionError):
        role_decoder.decode(b"image", "image/png", tmp_path / "bob")
