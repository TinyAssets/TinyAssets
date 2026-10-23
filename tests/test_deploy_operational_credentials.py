"""Normal deploys install the sole current router credential source."""
from __future__ import annotations

import json
import os
import stat
import subprocess
from pathlib import Path

import pytest

pytestmark = [
    pytest.mark.verifies("owner-daemon-in-box:C3"),
    pytest.mark.skipif(os.name != "posix" or getattr(os, "geteuid", lambda: -1)() != 0,
        reason="requires Linux root (linux_oracle.py --as-root)"),
]
HELPER = Path(__file__).resolve().parents[1] / "deploy/install-tinyassets-env.sh"
WIKI = "TINYASSETS_WIKI_CANARY_TOKEN"
DOOR = "TINYASSETS_CANARY_DOOR_KEY"
USER = "TINYASSETS_CANARY_USER_ID"
TOKEN = "normal-deploy-canary-token-" + "a" * 32


@pytest.fixture
def installer(tmp_path, monkeypatch):
    assert os.geteuid() == 0, "run with linux_oracle.py --as-root"
    volume = tmp_path / "volume"
    volume.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    docker = binaries / "docker"
    docker.write_text(
        '#!/bin/sh\n'
        'if [ "$*" = "volume ls --format {{ .Name }}" ]; then\n'
        '  test ! -d "$FAKE_VOLUME" || echo tinyassets-data\n'
        '  exit 0\n'
        'fi\n'
        'test "$*" = "volume inspect tinyassets-data --format {{ .Mountpoint }}" || exit 1\n'
        'test -d "$FAKE_VOLUME" || exit 1\n'
        'printf "%s\\n" "$FAKE_VOLUME"\n'
    )
    docker.chmod(0o755)
    source = tmp_path / "env"
    source.write_text("")
    environment = {
        **os.environ,
        "PATH": str(binaries) + os.pathsep + os.environ["PATH"],
        "FAKE_VOLUME": str(volume),
        "TINYASSETS_ENV_FILE": str(source),
        "TINYASSETS_LEGACY_ENV_FILE": str(tmp_path / "missing"),
        "TINYASSETS_ENV_OWNER": "root:root",
        "TINYASSETS_ENV_READ_USER": "",
        "TINYASSETS_DAEMON_ENV_SOURCE": str(source),
        "TINYASSETS_DAEMON_ENV_FILE": str(tmp_path / "daemon.env"),
        "TINYASSETS_RCLONE_CONFIG": str(tmp_path / "missing-rclone"),
    }
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(volume))

    def run(*arguments, value="", check=True):
        return subprocess.run(
            ["bash", str(HELPER), *arguments], input=value, text=True,
            capture_output=True, env=environment, check=check,
        )

    return run, volume / ".router-credentials.json", source


def test_set_installs_current_bearer_and_delete_revokes(installer):
    run, target, _ = installer
    run("set", WIKI, value=TOKEN)
    assert json.loads(target.read_text()) == {WIKI: TOKEN}
    metadata = target.stat()
    assert stat.S_IMODE(metadata.st_mode) == 0o600
    assert (metadata.st_uid, metadata.st_gid) == (1001, 1001)
    run("delete", WIKI)
    assert WIKI not in json.loads(target.read_text())


def test_wiki_canary_accepts_normal_sync_and_refuses_revoked_env(installer, monkeypatch):
    from tinyassets.auth.wiki_canary import wiki_canary_token_matches

    run, _, _ = installer
    monkeypatch.setenv(WIKI, TOKEN)
    assert not wiki_canary_token_matches(TOKEN)
    run("set", WIKI, value=TOKEN)
    assert wiki_canary_token_matches(TOKEN)
    assert not wiki_canary_token_matches(TOKEN + "wrong")
    run("delete", WIKI)
    assert not wiki_canary_token_matches(TOKEN)


def test_pair_sync_preserves_other_keys_and_removes_absent_names(installer):
    run, target, source = installer
    target.write_text(json.dumps({"other": "keep", WIKI: "obsolete", DOOR: "old"}))
    source.write_text(f"{WIKI}=obsolete\n{DOOR}=old\n")
    run("set-pair", DOOR, USER, value=f"{TOKEN}\nuser_canary123\n")
    assert json.loads(target.read_text()) == {
        "other": "keep", WIKI: "obsolete", DOOR: TOKEN, USER: "user_canary123",
    }
    assert USER + "=user_canary123" in source.read_text()
    run("delete", WIKI, DOOR, USER)
    assert json.loads(target.read_text()) == {"other": "keep"}
    run("delete", DOOR, USER)  # Repeating a revocation is harmless.
    assert json.loads(target.read_text()) == {"other": "keep"}


def test_sync_does_not_read_assignments_inside_multiline_values(installer):
    run, target, source = installer
    source.write_text(f'UNRELATED="first\n{WIKI}=not-a-credential\nlast"\n')
    run("sync-operational-credentials")
    assert json.loads(target.read_text()) == {}


def test_sync_failure_keeps_previous_json(installer):
    run, target, source = installer
    original = json.dumps({WIKI: TOKEN, "other": "keep"})
    target.write_text(original)
    source.write_text(f'{WIKI}="unclosed\n')
    result = run("sync-operational-credentials", check=False)
    assert result.returncode != 0
    assert target.read_text() == original
    assert TOKEN not in result.stdout + result.stderr


@pytest.mark.parametrize("command", [("set", WIKI), ("set-pair", DOOR, USER),
                                      ("delete", WIKI)])
def test_recovery_rotation_refuses_before_any_write(installer, command):
    run, target, source = installer
    run("set", WIKI, value=TOKEN)
    recovered = "recovered-secret-" + "b" * 32
    target.write_text(json.dumps({WIKI: recovered}))
    files = [source, target, source.with_name("daemon.env")]
    before = [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in files]
    result = run(*command, value=f"{TOKEN}\nuser_canary123\n"
                 if command[0] == "set-pair" else TOKEN, check=False)
    assert result.returncode != 0
    assert (WIKI + " rotated by disaster recovery: update the GitHub secret to the "
            "recovered value, or rotate both") in result.stderr
    assert TOKEN not in result.stdout + result.stderr
    assert recovered not in result.stdout + result.stderr
    assert [(p.read_bytes(), p.stat().st_ino, p.stat().st_mtime_ns) for p in files] == before


@pytest.mark.parametrize("link", ["symlink", "hardlink"])
def test_linked_router_file_refused_without_read(installer, link):
    run, target, source = installer
    victim = source.with_name("root-secret.json")
    victim.write_text('{"root-secret": "must-not-read"}')
    victim.chmod(0o600)
    if link == "symlink":
        target.symlink_to(victim)
        os.lchown(target, 1001, 1001)
    else:
        os.link(victim, target)
    os.utime(victim, ns=(1, 1))  # A read updates atime even with relatime mounts.
    before = source.read_bytes(), source.stat().st_ino
    result = run("set", WIKI, value=TOKEN, check=False)
    assert result.returncode != 0
    assert victim.stat().st_atime_ns == 1
    assert (source.read_bytes(), source.stat().st_ino) == before
    assert not source.with_name("daemon.env").exists()
    assert "must-not-read" not in result.stdout + result.stderr


def test_absent_volume_skips_sync(installer):
    run, target, source = installer
    target.parent.rmdir()
    result = run("set", WIKI, value=TOKEN, check=False)
    assert result.returncode == 0
    assert "tinyassets-data volume is absent; skipping router credential sync" in result.stderr
    assert f"{WIKI}={TOKEN}" in source.read_text()
    assert not target.exists()


def test_adopt_recovered_value_then_rotate_and_revoke(installer):
    run, target, source = installer
    run("set", WIKI, value=TOKEN)
    target.write_text(json.dumps({WIKI: "recovered-value", "other": "keep"}))
    run("set", WIKI, value="recovered-value")
    assert f"{WIKI}=recovered-value" in source.read_text()
    assert json.loads(target.read_text()) == {WIKI: "recovered-value", "other": "keep"}
    run("set", WIKI, value="COMPLETE")  # Framing sentinel is legal as a value.
    assert json.loads(target.read_text())[WIKI] == "COMPLETE"
    run("delete", WIKI)
    assert json.loads(target.read_text()) == {"other": "keep"}


@pytest.mark.parametrize("failure", ["owner", "fifo", "json", "docker", "mountpoint",
                                     "nonportable", "bootstrap"])
def test_other_sync_failures_refuse_before_env_write(installer, failure):
    run, target, source = installer
    target.write_text("{}")
    value = TOKEN
    if failure == "owner":
        os.chown(target, 1002, 1002)
    elif failure == "fifo":
        target.unlink()
        os.mkfifo(target)
    elif failure == "json":
        target.write_text("not JSON")
    elif failure == "docker":
        (source.parent / "bin/docker").write_text(
            "#!/bin/sh\necho docker-unavailable >&2\nexit 1\n"
        )
    elif failure == "mountpoint":
        (source.parent / "bin/docker").write_text(
            '#!/bin/sh\ncase "$2" in ls) echo tinyassets-data;; '
            'inspect) echo /missing-volume;; esac\n'
        )
    elif failure == "nonportable":
        value = '"quoted-value"'
    elif failure == "bootstrap":
        source.unlink()
        target.write_text(json.dumps({WIKI: "recovered-value"}))
    before = source.read_bytes() if source.exists() else None
    # Do not read the FIFO; all other targets must be untouched on refusal.
    router_before = target.read_bytes() if failure != "fifo" else None
    result = run("set", WIKI, value=value, check=False)
    assert result.returncode != 0
    assert (source.read_bytes() if source.exists() else None) == before
    assert not source.with_name("daemon.env").exists()
    if failure != "fifo":
        assert target.read_bytes() == router_before
