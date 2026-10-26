"""Reconnect in one tap, and disconnect leaves nothing: the two file-level rules."""

import contextlib
import json
import os
import sqlite3
import threading

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
@pytest.mark.parametrize("first", ["remove", "register"])
def test_registration_and_removal_cannot_lose_each_other_s_write(tmp_path, monkeypatch, first):
    """Both interleavings of the store's two writers, forced deterministically.

    ``_write`` replaces the whole file, so without one common lock the writer
    that loaded first overwrites the other: a disconnect erases an unrelated
    registration that landed after its load, or a registration writes its stale
    snapshot afterwards and brings the disconnected definition back.
    """
    from tinyassets.providers import definition as registry

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / "center").mkdir()
    # The broker publication is not what this test is about; the store is.
    monkeypatch.setattr(registry, "_publish_source", lambda definition: None)
    disconnecting = registry.register_definition(
        universe_id="center", owner_user_id="owner", access_method="subscription_cli",
        protocol="cli_json", model="default", ref="fake-cli")

    second = "register" if first == "remove" else "remove"
    events: list[tuple[str, str]] = []
    holding, release, at_lock, second_loaded = (threading.Event() for _ in range(4))
    real_load, real_write, real_lock = registry._load, registry._write, registry._store_lock

    def who():
        return threading.current_thread().name

    def load(universe_id):
        rows = real_load(universe_id)
        events.append(("load", who()))
        if who() == second:
            second_loaded.set()
        if who() == first:
            holding.set()
            release.wait(30)
        return rows

    def write(universe_id, rows):
        events.append(("write", who()))
        return real_write(universe_id, rows)

    @contextlib.contextmanager
    def lock(universe_id):
        if who() == second:
            at_lock.set()
        with real_lock(universe_id):
            yield

    monkeypatch.setattr(registry, "_load", load)
    monkeypatch.setattr(registry, "_write", write)
    monkeypatch.setattr(registry, "_store_lock", lock)

    work = {
        "remove": lambda: registry.remove_definitions(
            "center", owner_user_id="owner", access_method="subscription_cli", ref="fake-cli"),
        # An unrelated HTTP source, registered by the same owner at the same moment.
        "register": lambda: registry.register_definition(
            universe_id="center", owner_user_id="owner", access_method="api_key_http",
            protocol="openai_chat", model="reply-model", ref="grant-1"),
    }
    threads = [threading.Thread(target=work[name], name=name) for name in (first, second)]
    threads[0].start()
    assert holding.wait(30), "the first writer never reached its load"
    threads[1].start()
    assert at_lock.wait(30), "the second writer never reached the store lock"
    # The first writer is parked between its load and its write. An unlocked
    # store lets the second writer load that same stale generation right here.
    assert not second_loaded.wait(0.5), "the second writer loaded while the first held the store"
    release.set()
    for thread in threads:
        thread.join(30)
        assert not thread.is_alive()

    # Whichever went first: the disconnected definition stays gone and the
    # unrelated registration survives. Neither write was lost.
    assert {(d.access_method, d.ref) for d in registry.list_definitions("center")} == {
        ("api_key_http", "grant-1")}
    assert disconnecting.id not in {d.id for d in registry.list_definitions("center")}
    assert events.index(("load", second)) > events.index(("write", first))


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
