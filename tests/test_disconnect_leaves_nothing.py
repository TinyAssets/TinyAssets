"""Reconnect in one tap, and disconnect leaves nothing: the two file-level rules."""

import json
import os
import sqlite3

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


class _Ledger:
    """Only ``_connect`` is what ``retract`` uses; the SQL and file are real."""

    def __init__(self, path):
        self._db_path = str(path)

    def _connect(self):
        return sqlite3.connect(self._db_path)


def _descriptor(conn, owner, ref, method="subscription_cli"):
    document = {"id": f"provdef_{owner}_{ref}_{method}", "owner_user_id": owner,
                "universe_id": "center", "ref": ref, "access_method": method}
    conn.execute("INSERT INTO box_model_sources VALUES (?, ?, ?)",
                 (owner, document["id"], json.dumps(document)))
    return document["id"]


@pytest.mark.verifies("box-subscription-sources:Disconnect connected sources")
def test_retract_withdraws_only_the_asking_owner_s_native_descriptor(tmp_path):
    from tinyassets.broker.owner_sources import retract

    ledger = _Ledger(tmp_path / "outbound.db")
    # No table yet is nothing to withdraw, not a failure: this owner never
    # registered a definition, so the broker holds no descriptor for them.
    assert retract(ledger, "owner", "fake-cli") == {"retracted": 0}
    with sqlite3.connect(ledger._db_path) as conn:
        conn.execute("CREATE TABLE box_model_sources (owner TEXT NOT NULL, id TEXT NOT NULL, "
                     "definition TEXT NOT NULL, PRIMARY KEY(owner, id))")
        mine = _descriptor(conn, "owner", "fake-cli")
        kept = [_descriptor(conn, "other", "fake-cli"),
                _descriptor(conn, "owner", "second-cli"),
                _descriptor(conn, "owner", "fake-cli", "api_key_http")]

    assert retract(ledger, "owner", "fake-cli") == {"retracted": 1}
    with sqlite3.connect(ledger._db_path) as conn:
        remaining = [row[0] for row in conn.execute("SELECT id FROM box_model_sources")]
    assert mine not in remaining
    assert sorted(remaining) == sorted(kept)
    assert retract(ledger, "owner", "fake-cli") == {"retracted": 0}


@pytest.mark.verifies("box-subscription-sources:Disconnect connected sources")
@pytest.mark.parametrize("ref", ["", "Fake-CLI", "a" * 300, "fake cli", None])
def test_retract_refuses_a_reference_outside_the_grammar(tmp_path, ref):
    from tinyassets.broker.owner_sources import retract

    with pytest.raises(ValueError):
        retract(_Ledger(tmp_path / "outbound.db"), "owner", ref)


@pytest.mark.verifies("box-subscription-sources:Disconnect connected sources")
def test_removing_definitions_never_creates_a_store(tmp_path, monkeypatch):
    from tinyassets.providers.definition import _STORE_FILENAME, remove_definitions

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / "center").mkdir()
    assert remove_definitions("center", owner_user_id="owner",
                              access_method="subscription_cli", ref="fake-cli") == 0
    assert not (tmp_path / "center" / _STORE_FILENAME).exists()
