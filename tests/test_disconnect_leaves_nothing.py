"""Reconnect in one tap, and disconnect leaves nothing: the two file-level rules."""

import json
import os

import pytest


@pytest.mark.verifies("box-subscription-sources:Record-bound acceptance")
def test_interrupted_marker_write_keeps_the_last_whole_marker(tmp_path, monkeypatch):
    from tinyassets.providers.subscription_lease import write_owner_text

    directory = tmp_path / ".ta-cli" / "fake-cli"
    directory.mkdir(parents=True)
    marker = directory / "connection"
    accepted = json.dumps({"source": "digest-1", "incarnation": "inc-1"})
    write_owner_text(marker, accepted)
    assert json.loads(marker.read_text()) == {"source": "digest-1", "incarnation": "inc-1"}

    def interrupted(*args, **kwargs):
        raise OSError("interrupted before the new marker became the name")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        write_owner_text(marker, json.dumps({"source": "digest-2", "incarnation": "inc-2"}))
    monkeypatch.undo()
    # Truncated JSON here reads as "reconnect required" forever, so the only two
    # outcomes are the previous marker or the new one -- and nothing left over.
    assert marker.read_text() == accepted
    assert [child.name for child in directory.iterdir()] == ["connection"]


@pytest.mark.verifies("box-subscription-sources:Disconnect connected sources")
def test_removing_definitions_is_scoped_to_the_asking_owner(tmp_path, monkeypatch):
    from tinyassets.providers.definition import (
        _STORE_FILENAME,
        list_definitions,
        remove_definitions,
    )

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    center = tmp_path / "center"
    center.mkdir()

    def row(owner, ref, method="subscription_cli", protocol="cli_json"):
        from tinyassets.providers.definition import _definition_id

        fields = {"universe_id": "center", "owner_user_id": owner, "access_method": method,
                  "protocol": protocol, "ref": ref, "model": "default"}
        return {"id": _definition_id(**fields), **fields, "visibility": "private",
                "created_at": "2026-10-24T00:00:00+00:00"}

    rows = [row("owner", "fake-cli"), row("owner", "second-cli"),
            row("other", "fake-cli"), row("owner", "grant-1", "api_key_http", "openai_chat")]
    (center / _STORE_FILENAME).write_text(json.dumps(rows), encoding="utf-8")

    removed = remove_definitions("center", owner_user_id="owner",
                                 access_method="subscription_cli", ref="fake-cli")
    assert removed == 1
    assert {(d.owner_user_id, d.ref) for d in list_definitions("center")} == {
        ("owner", "second-cli"), ("other", "fake-cli"), ("owner", "grant-1")}
    # Idempotent: a repeated disconnect finds nothing left and rewrites nothing.
    before = (center / _STORE_FILENAME).read_bytes()
    assert remove_definitions("center", owner_user_id="owner",
                              access_method="subscription_cli", ref="fake-cli") == 0
    assert (center / _STORE_FILENAME).read_bytes() == before


@pytest.mark.verifies("box-subscription-sources:Disconnect connected sources")
def test_removing_definitions_never_creates_a_store(tmp_path, monkeypatch):
    from tinyassets.providers.definition import _STORE_FILENAME, remove_definitions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / "center").mkdir()
    assert remove_definitions("center", owner_user_id="owner",
                              access_method="subscription_cli", ref="fake-cli") == 0
    assert not (tmp_path / "center" / _STORE_FILENAME).exists()
