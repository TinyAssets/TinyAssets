"""Production provider adapter: editable file authority and atomic related records."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

import pytest

from tests.test_okf_canonical_bundle import bundle  # noqa: F401
from tinyassets.agent_loop import served_chat
from tinyassets.boxes.provider import WriteConflict
from tinyassets.okf.bundle import ROOT, InvalidBundle
from tinyassets.okf.migration import SnapshotSource, commit_migration, prepare_migration
from tinyassets.okf.records import SourceRecords


def seed(bundle, tmp_path):  # noqa: F811
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE notes (owner TEXT, id TEXT PRIMARY KEY, content TEXT, extra BLOB)"
        )
        conn.execute("CREATE TABLE history (owner TEXT, version INTEGER PRIMARY KEY, value TEXT)")
        conn.executemany(
            "INSERT INTO notes VALUES (?,?,?,?)",
            [
                ("a", "note-a", "Owner A prose", b"\x00\xff"),
                ("b", "note-b", "Owner B prose", b"peer"),
            ],
        )
        conn.execute("INSERT INTO history VALUES ('a',1,'Original history')")
    source = SnapshotSource(
        path,
        "notes",
        hashlib.sha256(path.read_bytes()).hexdigest(),
        {"notes": {"owner": ["a"]}, "history": {"owner": ["a"]}},
        {},
    )
    plan = prepare_migration(bundle, [source], operation_id="migration")
    commit_migration(bundle, plan)
    return source


def test_bound_adapter_reads_edited_prose_and_writes_related_tables_without_sqlite(
    bundle,  # noqa: F811
    tmp_path,
    monkeypatch,  # noqa: F811
):
    seed(bundle, tmp_path)
    monkeypatch.setattr(served_chat, "configured_box_provider", lambda: (bundle.provider, None))
    adapter = SourceRecords.bind(
        owner=bundle.handle.account_id, center=bundle.handle.command_center_id, source="notes"
    )
    before = adapter.snapshot()
    note = next(entry for entry in before.entries.values() if entry.metadata["table"] == "notes")
    edited = replace(
        note,
        body="Owner-edited Markdown",
        metadata={**note.metadata, "future_field": {"preserve": [1e20, None]}},
    )
    bundle.provider.write(
        bundle.handle,
        "owner-edit",
        ROOT + "/" + before.paths[note.id],
        edited.encode(),
        max_bytes=4096,
    )
    original_sqlite = sqlite3.connect

    def host_journal_only(path, *args, **kwargs):
        assert str(path) == str(bundle.provider._state._path), "legacy SQLite opened"
        return original_sqlite(path, *args, **kwargs)

    monkeypatch.setattr(sqlite3, "connect", host_journal_only)
    current = adapter.snapshot()
    rows = adapter.rows(current)
    assert rows["notes"][note.id]["content"] == "Owner-edited Markdown"
    updated = {**rows["notes"][note.id], "content": "Updated through adapter"}
    adapter.write(
        current,
        upserts={
            "notes": [updated],
            "history": [{"owner": "a", "version": 2, "value": "New history"}],
        },
        operation_id="related-write",
    )
    after = adapter.snapshot()
    assert adapter.rows(after)["notes"][note.id] == updated
    assert after.entries[note.id].metadata["future_field"] == {"preserve": [1e20, None]}
    assert len(adapter.rows(after)["history"]) == 2
    assert all(
        row["owner"] == "a" for table in adapter.rows(after).values() for row in table.values()
    )
    # Keep the fixture provider's SQLite-based test journal cleanup independent.
    monkeypatch.setattr(sqlite3, "connect", original_sqlite)


def test_source_writes_refuse_scope_changes_unknown_column_loss_and_stale_snapshot(
    bundle, tmp_path,  # noqa: F811
):
    seed(bundle, tmp_path)
    adapter = SourceRecords(bundle, "notes")
    before = adapter.snapshot()
    key, row = next(iter(adapter.rows(before)["notes"].items()))
    for invalid in ({**row, "owner": "b"}, {k: v for k, v in row.items() if k != "extra"}):
        with pytest.raises(InvalidBundle):
            adapter.write(before, upserts={"notes": [invalid]}, operation_id="invalid")
    assert adapter.snapshot().manifest["revision"] == before.manifest["revision"]
    adapter.write(before, upserts={"notes": [{**row, "content": "first"}]}, operation_id="first")
    with pytest.raises(WriteConflict):
        adapter.write(
            before, upserts={"notes": [{**row, "content": "second"}]}, operation_id="second"
        )
    assert adapter.rows(adapter.snapshot())["notes"][key]["content"] == "first"


def test_source_adapter_deletion_does_not_resurrect_from_backup(bundle, tmp_path):  # noqa: F811
    source = seed(bundle, tmp_path)
    adapter = SourceRecords(bundle, "notes")
    before = adapter.snapshot()
    key = next(iter(adapter.rows(before)["notes"]))
    adapter.write(before, deletions={"notes": [key]}, operation_id="remove")
    assert adapter.rows(adapter.snapshot())["notes"] == {}
    with sqlite3.connect(source.path) as conn:
        assert conn.execute("SELECT COUNT(*) FROM notes").fetchone()[0] == 2


def test_provider_and_migration_are_mandatory(bundle, monkeypatch):  # noqa: F811
    monkeypatch.setattr(served_chat, "configured_box_provider", lambda: (None, None))
    with pytest.raises(InvalidBundle, match="unavailable"):
        SourceRecords.bind(owner="owner-a", center="a", source="notes")
    with pytest.raises(InvalidBundle, match="migration"):
        SourceRecords(bundle, "notes").snapshot()
