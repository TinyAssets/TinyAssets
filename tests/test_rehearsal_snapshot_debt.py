"""Restore rehearsals must select a newly published snapshot, never sleep debt."""

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from scripts.rehearsal_launch import published_sleep


@pytest.mark.verifies('owner-daemon-in-box:C15')
@pytest.mark.parametrize("busy", [False, True])
def test_rehearsal_waits_for_new_verified_reference(tmp_path, monkeypatch, busy):
    path = tmp_path / "identities.db"
    connect = sqlite3.connect
    with connect(path) as db:
        db.execute("CREATE TABLE owner_volumes(principal,snapshot_id,work)")
        db.execute("INSERT INTO owner_volumes VALUES('alice',?,NULL)", ("a" * 32,))
    requested = threading.Event()

    def sleep(operation, **fields):
        assert operation == "sleep" and fields == {"owner": "alice"}
        with connect(path) as db:
            db.execute('UPDATE owner_volumes SET work=?', ('{"kind":"snapshot"}',))
        requested.set()
        if busy:
            raise RuntimeError("drain pending")
        return {"snapshot_id": "a" * 32, "work": {"kind": "snapshot"}}

    def metadata(database, *args, **kwargs):
        assert database == "/data/.broker/state/owner-identities.db"
        return connect(path, *args, **kwargs)

    monkeypatch.setattr("tinyassets.broker.lifecycle_control.request", sleep)
    monkeypatch.setattr(sqlite3, "connect", metadata)
    with ThreadPoolExecutor(max_workers=1) as pool:
        waiting = pool.submit(published_sleep, "alice")
        try:
            assert requested.wait(2)
            assert not waiting.done()
        finally:
            with connect(path) as db:
                db.execute("UPDATE owner_volumes SET snapshot_id=?,work=NULL", ("b" * 32,))
        assert waiting.result(timeout=2) == "b" * 32
