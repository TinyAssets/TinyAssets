"""Verified offline copies restore edits/deletions and preserve peer records."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest

from tests.test_okf_canonical_bundle import bundle  # noqa: F401
from tests.test_okf_source_records import seed
from tinyassets.okf.bundle import InvalidBundle
from tinyassets.okf.migration import SnapshotSource, commit_migration, prepare_migration
from tinyassets.okf.records import SourceRecords
from tinyassets.okf.restore import _preserved_rows, restore_copy


def test_preservation_check_includes_legal_sqlite_prefix_table():
    with sqlite3.connect(":memory:") as conn:
        conn.execute("CREATE TABLE sqliteXaudit (value TEXT)")
        conn.executemany("INSERT INTO sqliteXaudit VALUES (?)", [(None,), (None,)])
        census = {"tables": {}}
        before = _preserved_rows(conn, census)
        assert set(before) == {"sqliteXaudit"}
        assert sum(before["sqliteXaudit"].values()) == 2
        conn.execute("DELETE FROM sqliteXaudit WHERE rowid=1")
        assert _preserved_rows(conn, census) != before


def test_restore_actual_database_keeps_edits_history_binary_and_peer_rows(bundle, tmp_path):  # noqa: F811
    source = seed(bundle, tmp_path)
    adapter = SourceRecords(bundle, "notes")
    before = adapter.snapshot()
    key, row = next(iter(adapter.rows(before)["notes"].items()))
    adapter.write(
        before,
        upserts={
            "notes": [{**row, "content": "Canonical edit"}],
            "history": [{"owner": "a", "version": 2, "value": "Added history"}],
        },
        operation_id="edit",
    )
    target = tmp_path / "restored.db"
    receipt = restore_copy(adapter.snapshot(), source, target)
    assert receipt["rows"] == 3 and receipt["activated"] is False
    assert hashlib.sha256(source.path.read_bytes()).hexdigest() == source.expected_sha256
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT * FROM notes ORDER BY id").fetchall() == [
            ("a", "note-a", "Canonical edit", b"\x00\xff"),
            ("b", "note-b", "Owner B prose", b"peer"),
        ]
        assert conn.execute("SELECT value FROM history ORDER BY version").fetchall() == [
            ("Original history",),
            ("Added history",),
        ]
    adapter.write(adapter.snapshot(), deletions={"notes": [key]}, operation_id="delete")
    another = tmp_path / "deleted.db"
    restore_copy(adapter.snapshot(), source, another)
    with sqlite3.connect(another) as conn:
        assert conn.execute("SELECT id FROM notes").fetchall() == [("note-b",)]


def test_restore_refuses_changed_backup_wrong_scope_and_existing_destination(bundle, tmp_path):  # noqa: F811
    source = seed(bundle, tmp_path)
    snapshot = SourceRecords(bundle, "notes").snapshot()
    existing = tmp_path / "exists.db"
    existing.write_bytes(b"untouched")
    with pytest.raises(FileExistsError):
        restore_copy(snapshot, source, existing)
    assert existing.read_bytes() == b"untouched"
    wrong = replace(source, scopes={"notes": {"owner": ["b"]}, "history": {"owner": ["b"]}})
    with pytest.raises(InvalidBundle, match="original"):
        restore_copy(snapshot, wrong, tmp_path / "wrong.db")
    assert not (tmp_path / "wrong.db").exists()
    with sqlite3.connect(source.path) as conn:
        conn.execute("UPDATE notes SET content='Changed backup'")
    with pytest.raises(InvalidBundle, match="checksum"):
        restore_copy(snapshot, source, tmp_path / "changed.db")
    assert not (tmp_path / "changed.db").exists()


def test_restore_preserves_rowids_and_disables_peer_mutating_triggers(bundle, tmp_path):  # noqa: F811
    path = tmp_path / "rowids.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (owner TEXT, content TEXT)")
        conn.execute("CREATE TABLE audit (n INTEGER)")
        conn.execute("INSERT INTO audit VALUES (0)")
        conn.executemany(
            "INSERT INTO notes(rowid,owner,content) VALUES (?,?,?)",
            [(7, "a", "Original"), (11, "b", "Peer")],
        )
        conn.execute(
            "CREATE TRIGGER side_effect AFTER DELETE ON notes BEGIN "
            "UPDATE notes SET content='Mutated peer' WHERE owner='b'; "
            "UPDATE audit SET n=n+1; END"
        )
    source = SnapshotSource(
        path,
        "notes",
        hashlib.sha256(path.read_bytes()).hexdigest(),
        {"notes": {"owner": ["a"]}},
        {"audit": "Transactional history"},
    )
    commit_migration(bundle, prepare_migration(bundle, [source], operation_id="seed"))
    target = tmp_path / "restored.db"
    receipt = restore_copy(bundle.snapshot(), source, target)
    assert receipt["excluded_stores_require_review"] == {"audit": "Transactional history"}
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT rowid,* FROM notes ORDER BY rowid").fetchall() == [
            (7, "a", "Original"),
            (11, "b", "Peer"),
        ]
        assert conn.execute("SELECT n FROM audit").fetchone() == (0,)
        assert conn.execute("SELECT name FROM sqlite_master WHERE type='trigger'").fetchall() == [
            ("side_effect",)
        ]


@pytest.mark.parametrize("conflict", ["REPLACE", "IGNORE"])
def test_restore_aborts_schema_conflicts_without_losing_peer_rows(bundle, tmp_path, conflict):  # noqa: F811
    path = tmp_path / "conflicts.db"
    original = [("a-id", "a", "Original"), ("b-id", "b", "Peer")]
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE notes (id TEXT PRIMARY KEY ON CONFLICT "
            + conflict
            + ", owner TEXT, content TEXT)"
        )
        conn.executemany("INSERT INTO notes VALUES (?,?,?)", original)
    source = SnapshotSource(
        path,
        "notes",
        hashlib.sha256(path.read_bytes()).hexdigest(),
        {"notes": {"owner": ["a"]}},
        {},
    )
    commit_migration(bundle, prepare_migration(bundle, [source], operation_id="seed"))
    adapter = SourceRecords(bundle, "notes")
    before = adapter.snapshot()
    adapter.write(
        before,
        deletions={"notes": list(adapter.rows(before)["notes"])},
        upserts={"notes": [{"id": "b-id", "owner": "a", "content": "Collision"}]},
        operation_id="collision",
    )
    target = tmp_path / "failed-restore.db"
    with pytest.raises(sqlite3.IntegrityError):
        restore_copy(adapter.snapshot(), source, target)
    with sqlite3.connect(target) as conn:
        assert conn.execute("SELECT * FROM notes ORDER BY id").fetchall() == original
    assert hashlib.sha256(path.read_bytes()).hexdigest() == source.expected_sha256
