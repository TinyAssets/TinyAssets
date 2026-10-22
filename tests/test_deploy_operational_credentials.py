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
        'test "$*" = "volume inspect tinyassets-data --format {{ .Mountpoint }}" || exit 1\n'
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
    run("set-pair", DOOR, USER, value=f"{TOKEN}\nuser_canary123\n")
    assert json.loads(target.read_text()) == {
        "other": "keep", DOOR: TOKEN, USER: "user_canary123",
    }
    assert USER + "=user_canary123" in source.read_text()
    run("delete", DOOR, USER)
    assert json.loads(target.read_text()) == {"other": "keep"}
    run("delete", DOOR, USER)  # An already-absent env key still revokes stale JSON.
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
