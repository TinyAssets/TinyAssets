from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

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


def test_history_rolls_past_transport_bound_without_losing_replay_or_forget(bundle, monkeypatch):
    import tinyassets.okf.bundle as module

    monkeypatch.setattr(module, "HISTORY_PAGE_BYTES", 100)
    monkeypatch.setattr(module, "PAGE_BYTES", 1800)
    original = bundle.snapshot()
    bundle.commit(original, {"forgotten": entry("forgotten")}, operation_id="original")
    bundle.commit(bundle.snapshot(), {"forgotten": None}, operation_id="forget-original")
    for i in range(24):
        bundle.commit(
            bundle.snapshot(), {"current": entry("current", str(i))}, operation_id=f"write-{i}"
        )
    before = bundle.snapshot()
    assert before.manifest["log_history"]
    assert before.manifest["tombstones_history"]
    assert sum(len(log) for log in bundle._history(before, "log")) > module.PAGE_BYTES
    assert len(before.log.encode()) < module.PAGE_BYTES
    with pytest.raises(InvalidBundle, match="already committed"):
        bundle.commit(before, {"current": entry("current", "bad")}, operation_id="original")
    with pytest.raises(InvalidBundle, match="forgotten"):
        bundle.commit(before, {"forgotten": entry("forgotten")}, operation_id="revive")
    assert bundle.snapshot() == before


def test_deletion_marker_retains_identity_without_deleted_payload(bundle):
    value = Entry(
        "large",
        "sqlite-record",
        "body",
        {
            "source": "test",
            "table": "rows",
            "identity": [["integer", "37"]],
            "fields": {"private": ["text", "x" * 5000]},
        },
    )
    bundle.commit(bundle.snapshot(), {value.id: value}, operation_id="large")
    bundle.commit(bundle.snapshot(), {value.id: None}, operation_id="forget-large")
    before = bundle.snapshot()
    assert "fields" not in before.manifest["tombstones"][value.id]["metadata"]
    assert before.manifest["deletion_sequences"][json.dumps(["test", "rows"])] == 37


def test_history_page_retry_and_integrity_are_fenced(bundle, monkeypatch):
    import tinyassets.okf.bundle as module

    monkeypatch.setattr(module, "HISTORY_PAGE_BYTES", 100)
    bundle.commit(bundle.snapshot(), {"note": entry()}, operation_id="seed-history")
    before = bundle.snapshot()
    real = bundle.provider.write

    def lost(handle, op, path, data, **kwargs):
        receipt = real(handle, op, path, data, **kwargs)
        if path == INDEX:
            raise OSError("lost archive reply")
        return receipt

    monkeypatch.setattr(bundle.provider, "write", lost)
    with pytest.raises(OSError, match="lost archive"):
        bundle.commit(before, {"note": entry(body="new")}, operation_id="archive")
    monkeypatch.setattr(bundle.provider, "write", real)
    bundle.commit(before, {"note": entry(body="new")}, operation_id="archive")
    current = bundle.snapshot()
    assert current.entries["note"].body == "new"
    history = ROOT + "/history/" + current.manifest["log_history"] + ".json"
    real(bundle.handle, "corrupt-history", history, b"{}", max_bytes=2)
    with pytest.raises(InvalidBundle, match="missing or changed"):
        bundle.commit(bundle.snapshot(), {"note": entry(body="bad")}, operation_id="new-op")


def test_verified_history_is_cached_and_ignores_unrelated_generations(bundle, monkeypatch):
    import tinyassets.okf.bundle as module

    monkeypatch.setattr(module, "HISTORY_PAGE_BYTES", 100)
    for i in range(12):
        bundle.commit(bundle.snapshot(), {"note": entry(body=str(i))}, operation_id=f"history-{i}")
    before = bundle.snapshot()
    bundle._history_cache.clear()
    real = bundle.provider.read
    calls = []

    def concurrent(handle, path, **kwargs):
        value = real(handle, path, **kwargs)
        if "/history/" in path:
            calls.append(path)
            bundle.provider.write(
                handle, f"concurrent-{len(calls)}", "/cc/unrelated", b"x", max_bytes=1
            )
        return value

    monkeypatch.setattr(bundle.provider, "read", concurrent)
    assert bundle.operation_committed(before, "history-0")
    count = len(calls)
    assert count > 5
    assert bundle.operation_committed(before, "history-0")
    assert len(calls) == count


@pytest.mark.parametrize(
    "field,value",
    [
        ("deletion_sequences", []),
        ("deletion_sequences", {"x": "bad"}),
        ("log_history", []),
        ("tombstones_history", "bad"),
    ],
)
def test_malformed_history_manifest_refuses_as_invalid_bundle(bundle, field, value):
    bundle.commit(bundle.snapshot(), {"note": entry()}, operation_id="manifest")
    manifest = bundle.snapshot().manifest | {field: value}
    raw = json.dumps(manifest).encode()
    bundle.provider.write(bundle.handle, "malformed", INDEX, raw, max_bytes=len(raw))
    with pytest.raises(InvalidBundle):
        bundle.snapshot()


def test_bulk_forget_splits_markers_into_bounded_history_pages(bundle, monkeypatch):
    import tinyassets.okf.bundle as module

    entries = {str(i): entry(str(i)) for i in range(60)}
    bundle.commit(bundle.snapshot(), entries, operation_id="bulk-seed")
    before = bundle.snapshot()
    monkeypatch.setattr(module, "HISTORY_PAGE_BYTES", 512)
    monkeypatch.setattr(module, "PAGE_BYTES", 1800)
    bundle.commit(before, dict.fromkeys(entries), operation_id="bulk-forget")
    current = bundle.snapshot()
    pages = list(bundle._history(current, "tombstones"))
    assert len(pages) > 5
    assert sum(len(page) for page in pages) == len(entries)
    assert not current.entries
    assert bundle.forgotten(current, entries)


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


def test_actual_legacy_schemas_project_losslessly_without_wal_side_effects(bundle, tmp_path):
    import hashlib

    from tests.support.legacy_memory_snapshot import daemon_snapshot, offline_consumer
    from tests.support.project_snapshot import snapshot as project_snapshot
    from tinyassets.daemon_brain import daemon_brain_db_path
    from tinyassets.daemon_registry import create_daemon
    from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
    from tinyassets.knowledge.models import FactWithContext, GraphEntity, SourceType
    from tinyassets.memory.episodic import EpisodicMemory
    from tinyassets.memory.promises import SeriesPromiseTracker
    from tinyassets.memory.scoping import MemoryScope
    from tinyassets.memory.temporal import TemporalFact, TemporalFactStore
    from tinyassets.memory.versioning import OutputVersionStore
    from tinyassets.okf.migration import SnapshotSource, commit_migration, prepare_migration

    legacy = tmp_path / "legacy"
    legacy.mkdir()
    daemon = create_daemon(
        legacy,
        display_name="Migration fixture",
        created_by="fixture",
        soul_mode="soul",
        soul_text="Preserve evidence",
    )
    daemon_snapshot(daemon_brain_db_path(legacy), daemon["daemon_id"])
    project_snapshot(
        legacy / ".project_memory.db",
        [
            ("project-a", "note", {"text": "Project content"}),
            ("project-b", "note", "Other project"),
        ],
    )
    episodic_path = legacy / "episodic.db"
    for owner in ("owner-a", "owner-b"):
        episodic = offline_consumer(EpisodicMemory, episodic_path, universe_id=owner)
        episodic.store_fact("fact", "subject", f"Preserve {owner} episode")
        episodic.store_episode_summary(
            "episode", 1, f"Summary for {owner}", domain_payload={"unknown": [1, None]}
        )
        episodic.close()
    graph_path = legacy / "knowledge.db"
    graph = offline_consumer(KnowledgeGraph, graph_path)
    for owner in ("owner-a", "owner-b"):
        graph.add_entity(
            GraphEntity(
                entity_id=owner,
                entity_type="concept",
                access_tier=0,
                public_description=f"Entity {owner}",
                hidden_description="",
                secret_description="",
                aliases=[],
            ),
            scope=MemoryScope(universe_id=owner),
        )
        graph.add_facts(
            [
                FactWithContext(
                    fact_id=owner, text=f"Fact {owner}", source_type=SourceType.AUTHOR_FACT
                )
            ],
            scope=MemoryScope(universe_id=owner),
        )
    graph.close()
    for owner in ("owner-a", "owner-b"):
        promises = offline_consumer(SeriesPromiseTracker, legacy / "promises.db", universe_id=owner)
        promises.create_promise("promise", f"Promise for {owner}", 1, 1)
        promises.add_evidence("promise", {"source": "chapter", "unknown": [None, 1e20]})
        promises.resolve_promise("promise", 2, 3)
        promises.close()
        versions = offline_consumer(OutputVersionStore, legacy / "versions.db", universe_id=owner)
        versions.save_draft(1, 1, 1, f"Earlier draft for {owner}")
        versions.save_draft(1, 1, 1, f"Current draft for {owner}", metadata={"unknown": True})
        versions.close()
    temporal = offline_consumer(TemporalFactStore, legacy / "temporal.db")
    for owner in ("owner-a", "owner-b"):
        temporal.assert_fact(
            TemporalFact(
                f"{owner}-old", "place", "location", "Earlier value", "2026-01-01", branch_id=owner
            )
        )
        temporal.supersede(
            f"{owner}-old",
            TemporalFact(
                f"{owner}-new",
                "place",
                "location",
                "Current value",
                "2026-02-01",
                valid_from="2026-02-01",
                supersedes=f"{owner}-old",
                branch_id=owner,
            ),
        )
    temporal.close()
    definitions = [
        ("promises", legacy / "promises.db", {"series_promises": {"universe_id": ["owner-a"]}}, {}),
        ("versions", legacy / "versions.db", {"draft_versions": {"universe_id": ["owner-a"]}}, {}),
        ("temporal", legacy / "temporal.db", {"temporal_facts": {"branch_id": ["owner-a"]}}, {}),
        (
            "daemon",
            daemon_brain_db_path(legacy),
            {
                "daemon_brain_entries": {"daemon_id": [daemon["daemon_id"]]},
                "daemon_memory_promotions": {"daemon_id": [daemon["daemon_id"]]},
                "daemon_memory_events": {"daemon_id": [daemon["daemon_id"]]},
            },
            {
                **{
                    f"daemon_brain_entries_fts{suffix}": "Derived full-text index"
                    for suffix in ("", "_data", "_idx", "_content", "_docsize", "_config")
                },
            },
        ),
        (
            "project",
            legacy / ".project_memory.db",
            {
                t: {"project_id": ["project-a"]}
                for t in ("project_memory", "project_memory_history")
            },
            {},
        ),
        (
            "episodic",
            episodic_path,
            {
                t: {"universe_id": ["owner-a"]}
                for t in ("scene_summaries", "episodic_facts", "style_observations", "reflections")
            },
            {},
        ),
        (
            "knowledge",
            graph_path,
            {
                t: {"universe_id": ["owner-a"]}
                for t in ("entities", "edges", "facts", "communities")
            },
            {},
        ),
    ]
    sources, originals = [], {}
    for name, path, scopes, excluded in definitions:
        backup = tmp_path / (name + "-backup.db")
        with sqlite3.connect(path) as live, sqlite3.connect(backup) as target:
            live.backup(target)
        data = backup.read_bytes()
        originals[backup] = data
        sources.append(
            SnapshotSource(backup, name, hashlib.sha256(data).hexdigest(), scopes, excluded)
        )
    plan = prepare_migration(bundle, sources, operation_id="real-schemas")
    receipt = commit_migration(bundle, plan)
    assert receipt["entries"] == 13
    by_source = {source.source: source for source in sources}
    for entry in bundle.snapshot().entries.values():
        row = restore_row(entry)
        source = by_source[entry.metadata["source"]]
        table = entry.metadata["table"]
        with sqlite3.connect(source.path.as_uri() + "?mode=ro&immutable=1", uri=True) as db:
            db.row_factory = sqlite3.Row
            expected = [dict(r) for r in db.execute(f'SELECT * FROM "{table}"')]
        assert row in expected
        if entry.metadata["source"] == "knowledge" and table == "facts":
            assert entry.body == "Fact owner-a"
        if entry.metadata["source"] == "promises":
            assert entry.body == "Promise for owner-a"
            assert row["status"] == "resolved" and row["resolved_book"] == 2
        if entry.metadata["source"] == "versions":
            assert entry.body in {"Earlier draft for owner-a", "Current draft for owner-a"}
            assert bool(row["is_current"]) == entry.body.startswith("Current")
        if entry.metadata["source"] == "temporal" and row["fact_id"].endswith("-old"):
            assert row["valid_until"] is not None
        assert "owner-b" not in entry.body
    from tinyassets.okf.records import SourceRecords
    from tinyassets.okf.restore import restore_copy

    # Exercise the actual seven schemas through the canonical production adapter,
    # then restore real standalone databases from the verified backup snapshots.
    for source in sources:
        adapter = SourceRecords(bundle, source.source)
        before = adapter.snapshot()
        rows = adapter.rows(before)
        entry = next(
            item
            for item in before.entries.values()
            if item.metadata.get("source") == source.source and item.metadata["body_field"]
        )
        row = dict(rows[entry.metadata["table"]][entry.id])
        row[entry.metadata["body_field"]] += " [canonical owner edit]"
        adapter.write(
            before, upserts={entry.metadata["table"]: [row]}, operation_id="edit-" + source.source
        )
        assert adapter.rows(adapter.snapshot())[entry.metadata["table"]][entry.id] == row
        restored = restore_copy(
            adapter.snapshot(), source, tmp_path / (source.source + "-restore.db")
        )
        assert restored["rows"] == sum(len(table) for table in rows.values())
        assert restored["activated"] is False
    for path, original in originals.items():
        assert path.read_bytes() == original
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()


@pytest.mark.parametrize(
    "header",
    [
        "id: &shared one\ntype: note\nother: *shared",
        "id: one\ntype: note\nvalue: " + "[" * 70 + "x" + "]" * 70,
        "id: one\ntype: note\nvalue: !!python/object:builtins.object {}",
        "id: one\ntype: note\nvalue: .nan",
    ],
)
def test_owner_frontmatter_refuses_expansion_and_nonportable_values(header):
    with pytest.raises(InvalidBundle):
        parse(("---\n" + header + "\n---\nBody").encode())


def test_owner_yaml_frontmatter_is_bounded_before_constructing_values():
    with pytest.raises(InvalidBundle, match="exceeds"):
        parse(b"---\nid: one\ntype: note\nvalue: " + b"x" * (1024 * 1024) + b"\n---\n")


def test_owner_note_identity_is_not_a_sql_sequence(bundle):  # noqa: F811
    note = Entry("owner-note", "note", "Private", {"identity": [["integer", 5]]})
    bundle.commit(bundle.snapshot(), {note.id: note}, operation_id="note-write")
    bundle.commit(bundle.snapshot(), {note.id: None}, operation_id="note-forget")
    bundle.commit(bundle.snapshot(), {"next": entry("next")}, operation_id="after-forget")
    assert note.id not in bundle.snapshot().entries
    assert bundle.snapshot().manifest.get("deletion_sequences", {}) == {}


def test_content_addressed_invalid_history_json_refuses_as_bundle_error(bundle):  # noqa: F811
    import hashlib

    raw = b"{broken"
    digest = hashlib.sha256(raw).hexdigest()
    bundle.provider.write(bundle.handle, "invalid-page", ROOT + "/history/" + digest + ".json",
                          raw, max_bytes=1000)
    before = bundle.snapshot()
    corrupt = replace(before, manifest={**before.manifest, "log_history": digest})
    with pytest.raises(InvalidBundle, match="history page JSON"):
        list(bundle._history(corrupt, "log"))
    assert digest not in bundle._history_cache
