"""The isolated image smoke serves owner MCP tools without a second process."""

import json
import os
import stat

import pytest

from tests.fixtures.docker_admitted_process import (
    install_canary_credential,
    install_fixture_supervisor,
    install_owner_construction,
)

pytestmark = pytest.mark.verifies("owner-daemon-in-box:C4")


def test_canary_token_is_written_for_the_credential_reader(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_WIKI_CANARY_TOKEN", "ab" * 32)
    install_canary_credential()
    path = tmp_path / ".router-credentials.json"
    assert json.loads(path.read_text(encoding="utf-8")) == {
        "TINYASSETS_WIKI_CANARY_TOKEN": "ab" * 32,
    }
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    from tinyassets.auth.wiki_canary import wiki_canary_token_matches

    assert wiki_canary_token_matches("ab" * 32)
    assert not wiki_canary_token_matches("cd" * 32)


def test_fixture_supervisor_and_owner_construction(monkeypatch):
    import tinyassets.broker.supervisor as supervisor
    import tinyassets.universe_server as universe_server
    from tinyassets import owner_runtime

    monkeypatch.setattr(supervisor, "get_supervisor", lambda root: None)
    install_fixture_supervisor()
    assert supervisor.get_supervisor(os.devnull) is not None

    seen = []

    def fake_create():
        seen.append(owner_runtime.current() is not None)
        return "owner-app"

    monkeypatch.setattr(universe_server, "create_streamable_http_app", fake_create)
    install_owner_construction()
    assert universe_server.create_streamable_http_app() == "owner-app"
    assert seen == [True]
    assert owner_runtime.current() is None
