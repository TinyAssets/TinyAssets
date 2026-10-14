from __future__ import annotations

import json
import sqlite3
from dataclasses import replace

import pytest

from tinyassets.boxes.local import LocalBoxProvider
from tinyassets.boxes.provider import BoxAuthError, WriteConflict
from tinyassets.okf.bundle import INDEX, ROOT, Entry, InvalidBundle, KnowledgeBundle, parse
from tinyassets.okf.migration import read_snapshot, restore_row


@pytest.fixture
def bundle(tmp_path):
    provider = LocalBoxProvider(
        boxes_root=tmp_path / "boxes",
        state_dir=tmp_path / "state",
        owner_of={"a": "owner-a", "b": "owner-b"}.get,
        allow_unisolated=True,
    )
    handle = provider.bind("a", account_id="owner-a")
    yield KnowledgeBundle(provider, handle)
    provider.close()


def entry(key="note", body="Remember this\n"):
    return Entry(key, "note", body, {"unknown_field": {"nested": [1, None, "é"]}})


def test_commit_retry_and_source_edit_drive_search(bundle):
    before = bundle.snapshot()
    change = {"note": entry()}
    committed = bundle.commit(before, change, operation_id="first")
    assert bundle.commit(before, change, operation_id="first") == committed
    view = bundle.snapshot()
    assert view.entries == change
    assert bundle.search("remember") == [entry()]
    changed = entry(body="Owner edited the source\n\n")
    path = ROOT + "/" + view.paths["note"]
    bundle.provider.write(bundle.handle, "owner-edit", path, changed.encode(), max_bytes=10000)
    assert bundle.search("remember") == []
    assert bundle.search("edited") == [changed]


def test_failed_staging_never_changes_visible_knowledge(bundle, monkeypatch):
    bundle.commit(bundle.snapshot(), {"note": entry()}, operation_id="initial")
    before = bundle.snapshot()
    write = bundle.provider.write

    def interrupted(handle, operation, path, data, **kwargs):
        if path == INDEX:
            raise OSError("interrupted before commit")
        return write(handle, operation, path, data, **kwargs)

    monkeypatch.setattr(bundle.provider, "write", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        bundle.commit(
            before,
            {"note": entry(body="new"), "second": entry("second")},
            operation_id="interrupted",
        )
    assert bundle.snapshot().entries == before.entries
    monkeypatch.setattr(bundle.provider, "write", write)
    bundle.commit(
        before, {"note": entry(body="new"), "second": entry("second")}, operation_id="interrupted"
    )
    assert set(bundle.snapshot().entries) == {"note", "second"}


def test_concurrent_owner_edit_refuses_stale_commit(bundle):
    before = bundle.snapshot()
    bundle.provider.write(bundle.handle, "owner", "/cc/owner.txt", b"edit", max_bytes=4)
    with pytest.raises(WriteConflict):
        bundle.commit(before, {"note": entry()}, operation_id="stale")
    assert not bundle.snapshot().entries


def test_delete_does_not_resurrect_from_staged_or_historical_files(bundle):
    bundle.commit(bundle.snapshot(), {"note": entry()}, operation_id="create")
    bundle.commit(bundle.snapshot(), {"note": None}, operation_id="delete")
    assert bundle.snapshot().entries == {}
    assert bundle.search("remember") == []


def test_foreign_handle_is_rejected_by_existing_box_boundary(bundle):
    foreign = KnowledgeBundle(bundle.provider, replace(bundle.handle, account_id="owner-b"))
    with pytest.raises(BoxAuthError):
        foreign.snapshot()


def test_missing_source_is_visible_failure_not_stale_index(bundle):
    bundle.commit(bundle.snapshot(), {"note": entry()}, operation_id="create")
    path = ROOT + "/" + bundle.snapshot().paths["note"]
    bundle.provider.remove(bundle.handle, "remove-source", path)
    with pytest.raises(InvalidBundle, match="missing"):
        bundle.search("remember")


@pytest.mark.parametrize(
    "path",
    [
        "../secret",
        "records/../secret.md",
        "/cc/other.md",
        "records/a/../../secret.md",
        "records/a/x\\y.md",
    ],
)
def test_manifest_cannot_redirect_into_other_box_files(bundle, path):
    body = json.dumps(
        {
            "okf_version": "0.1",
            "format": "knowledge-v1",
            "revision": "a" * 64,
            "entries": {"n": path},
        }
    ).encode()
    bundle.provider.write(bundle.handle, "bad-manifest", INDEX, body, max_bytes=len(body))
    with pytest.raises(InvalidBundle):
        bundle.snapshot()


def test_sqlite_snapshot_lossless_scoped_census_and_bundle_commit(bundle, tmp_path):
    source = tmp_path / "backup.db"
    with sqlite3.connect(source) as conn:
        conn.execute(
            "CREATE TABLE memories (id INTEGER PRIMARY KEY, owner TEXT, content TEXT, "
            "unknown BLOB, count INTEGER, weight REAL, absent TEXT)"
        )
        conn.execute(
            "INSERT INTO memories VALUES (1, 'owner-a', ?, ?, ?, ?, NULL)",
            ("  Exact é\n\n", b"\x00\xff", 2**62, float("inf")),
        )
        conn.execute("INSERT INTO memories VALUES (2, 'owner-b', 'private', NULL, 0, 1, NULL)")
        conn.execute("CREATE TABLE events (payload TEXT)")
    original = source.read_bytes()
    records, census = read_snapshot(
        source,
        source="brain",
        scopes={"memories": {"owner": ["owner-a"]}},
        excluded={"events": "transactional events"},
    )
    assert len(records) == 1
    assert restore_row(records[0]) == {
        "id": 1,
        "owner": "owner-a",
        "content": "  Exact é\n\n",
        "unknown": b"\x00\xff",
        "count": 2**62,
        "weight": float("inf"),
        "absent": None,
    }
    assert census["tables"]["memories"]["count"] == 1
    before = bundle.snapshot()
    bundle.commit(before, {record.id: record for record in records}, operation_id="migration")
    loaded = next(iter(bundle.snapshot().entries.values()))
    assert restore_row(loaded) == restore_row(records[0])
    assert source.read_bytes() == original
    assert not (source.parent / (source.name + "-journal")).exists()


def test_unclassified_source_table_stops_migration(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE known (owner TEXT)")
        conn.execute("CREATE TABLE unexpected (value TEXT)")
    with pytest.raises(InvalidBundle, match="census"):
        read_snapshot(path, source="brain", scopes={"known": {"owner": ["a"]}}, excluded={})


def test_unscoped_table_is_never_migrated(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE records (owner TEXT)")
    with pytest.raises(InvalidBundle, match="scope"):
        read_snapshot(path, source="brain", scopes={"records": {}}, excluded={})


def test_committed_revision_is_okf_and_snapshot_cannot_cross_owner(bundle):
    bundle.commit(bundle.snapshot(), {"note": entry()}, operation_id="create")
    view = bundle.snapshot()
    root = ROOT + "/revisions/" + view.manifest["revision"]
    files = bundle.provider.read_many(
        bundle.handle, [root + "/index.md", root + "/log.md"], max_total=10000
    )
    assert parse(files.files[root + "/index.md"])[0] == {"okf_version": "0.1"}
    assert not files.files[root + "/log.md"].startswith(b"---")
    other = KnowledgeBundle(bundle.provider, bundle.provider.bind("b", account_id="owner-b"))
    with pytest.raises(InvalidBundle, match="snapshot belongs"):
        other.commit(view, {}, operation_id="copy")
    assert other.snapshot().entries == {}


def test_migration_checks_backup_before_commit_and_retries_exact_plan(bundle, tmp_path):
    import hashlib

    from tinyassets.okf.migration import SnapshotSource, commit_migration, prepare_migration

    path = tmp_path / "verified.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, owner TEXT, content TEXT)")
        conn.execute("INSERT INTO notes VALUES (1, 'owner-a', 'Preserve this')")
    original = path.read_bytes()
    source = SnapshotSource(
        path, "brain", hashlib.sha256(original).hexdigest(), {"notes": {"owner": ["owner-a"]}}, {}
    )
    with pytest.raises(InvalidBundle, match="checksum"):
        prepare_migration(bundle, [replace(source, expected_sha256="wrong")], operation_id="bad")
    assert not bundle.snapshot().entries
    plan = prepare_migration(bundle, [source], operation_id="migration")
    first = commit_migration(bundle, plan)
    assert commit_migration(bundle, plan) == first
    assert first["entries"] == 1
    assert first["census"][0]["tables"]["notes"]["count"] == 1
    assert path.read_bytes() == original
    with pytest.raises(InvalidBundle, match="uninitialized"):
        prepare_migration(bundle, [source], operation_id="duplicate")


def test_changed_backup_refuses_before_staging(bundle, tmp_path):
    import hashlib

    from tinyassets.okf.migration import SnapshotSource, commit_migration, prepare_migration

    path = tmp_path / "verified.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (id INTEGER PRIMARY KEY, owner TEXT, content TEXT)")
        conn.execute("INSERT INTO notes VALUES (1, 'owner-a', 'Before')")
    source = SnapshotSource(
        path,
        "brain",
        hashlib.sha256(path.read_bytes()).hexdigest(),
        {"notes": {"owner": ["owner-a"]}},
        {},
    )
    plan = prepare_migration(bundle, [source], operation_id="migration")
    with sqlite3.connect(path) as conn:
        conn.execute("UPDATE notes SET content='changed'")
    with pytest.raises(InvalidBundle, match="checksum"):
        commit_migration(bundle, plan)
    assert not bundle.snapshot().entries


def test_unknown_json_metadata_preserves_numeric_types_and_rejects_duplicate_keys():
    value = Entry(
        "metadata", "unknown-type", "Exact body", {"unknown": [1e20, 1e-6, -0.0, False, None]}
    )
    assert Entry.decode(value.encode()) == value
    assert isinstance(Entry.decode(value.encode()).metadata["unknown"][0], float)
    with pytest.raises(InvalidBundle, match="unique"):
        Entry.decode(b'---\n{"id":"a","type":"note","extra":1,"extra":2}\n---\n')


def test_null_scope_is_counted_and_sqlite_prefix_is_literal(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as conn:
        conn.execute("CREATE TABLE notes (owner TEXT, content TEXT)")
        conn.execute("INSERT INTO notes VALUES (NULL, 'Explicit null scope')")
        conn.execute("INSERT INTO notes VALUES ('owner-a', 'Named scope')")
        conn.execute("INSERT INTO notes VALUES ('owner-b', 'Other scope')")
        conn.execute("CREATE TABLE sqlitebackup (content TEXT)")
    with pytest.raises(InvalidBundle, match="sqlitebackup"):
        read_snapshot(path, source="brain", scopes={"notes": {"owner": [None]}}, excluded={})
    records, census = read_snapshot(
        path,
        source="brain",
        scopes={"notes": {"owner": [None, "owner-a"]}},
        excluded={"sqlitebackup": "separate backup inventory"},
    )
    assert len(records) == census["tables"]["notes"]["count"] == 2
    assert {restore_row(entry)["owner"] for entry in records} == {None, "owner-a"}


def test_reserved_rowid_alias_is_case_insensitive_and_shadowed_rowid_is_preserved(tmp_path):
    path = tmp_path / "source.db"
    with sqlite3.connect(path) as conn:
        conn.execute('CREATE TABLE notes (owner TEXT, "__MIGRATION_ROWID__" TEXT)')
        conn.execute("INSERT INTO notes VALUES ('a', 'Original field data')")
    with pytest.raises(InvalidBundle, match="Reserved"):
        read_snapshot(path, source="brain", scopes={"notes": {"owner": ["a"]}}, excluded={})
    with sqlite3.connect(path) as conn:
        conn.execute("DROP TABLE notes")
        conn.execute('CREATE TABLE notes (owner TEXT, "ROWID" TEXT)')
        conn.execute("INSERT INTO notes VALUES ('a', 'Original field data')")
    entries, _ = read_snapshot(
        path, source="brain", scopes={"notes": {"owner": ["a"]}}, excluded={}
    )
    assert restore_row(entries[0]) == {
        "__migration_rowid__": 1,
        "owner": "a",
        "ROWID": "Original field data",
    }
