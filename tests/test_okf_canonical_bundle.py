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

    from tinyassets.daemon_brain import capture_daemon_memory, daemon_brain_db_path
    from tinyassets.daemon_registry import create_daemon
    from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
    from tinyassets.knowledge.models import FactWithContext, GraphEntity, SourceType
    from tinyassets.memory.episodic import EpisodicMemory
    from tinyassets.memory.project import project_memory_set
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
    capture_daemon_memory(
        legacy,
        daemon_id=daemon["daemon_id"],
        content="Preserve daemon memory",
        reliability="host_observed",
        language_type="literal",
        metadata={"unknown": [1e20, "extra"]},
    )
    project_memory_set(
        legacy, project_id="project-a", key="note", value={"text": "Project content"}
    )
    project_memory_set(legacy, project_id="project-b", key="note", value="Other project")
    episodic_path = legacy / "episodic.db"
    for owner in ("owner-a", "owner-b"):
        episodic = EpisodicMemory(episodic_path, universe_id=owner)
        episodic.store_fact("fact", "subject", f"Preserve {owner} episode")
        episodic.store_episode_summary(
            "episode", 1, f"Summary for {owner}", domain_payload={"unknown": [1, None]}
        )
        episodic.close()
    graph_path = legacy / "knowledge.db"
    graph = KnowledgeGraph(graph_path)
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
        promises = SeriesPromiseTracker(legacy / "promises.db", universe_id=owner)
        promises.create_promise("promise", f"Promise for {owner}", 1, 1)
        promises.add_evidence("promise", {"source": "chapter", "unknown": [None, 1e20]})
        promises.resolve_promise("promise", 2, 3)
        promises.close()
        versions = OutputVersionStore(legacy / "versions.db", universe_id=owner)
        versions.save_draft(1, 1, 1, f"Earlier draft for {owner}")
        versions.save_draft(1, 1, 1, f"Current draft for {owner}", metadata={"unknown": True})
        versions.close()
    temporal = TemporalFactStore(legacy / "temporal.db")
    for owner in ("owner-a", "owner-b"):
        temporal.assert_fact(TemporalFact(f"{owner}-old", "place", "location", "Earlier value",
                                         "2026-01-01", branch_id=owner))
        temporal.supersede(f"{owner}-old", TemporalFact(
            f"{owner}-new", "place", "location", "Current value", "2026-02-01",
            valid_from="2026-02-01", supersedes=f"{owner}-old", branch_id=owner,
        ))
    temporal.close()
    definitions = [
        ("promises", legacy / "promises.db",
         {"series_promises": {"universe_id": ["owner-a"]}}, {}),
        ("versions", legacy / "versions.db",
         {"draft_versions": {"universe_id": ["owner-a"]}}, {}),
        ("temporal", legacy / "temporal.db",
         {"temporal_facts": {"branch_id": ["owner-a"]}}, {}),
        (
            "daemon",
            daemon_brain_db_path(legacy),
            {
                "daemon_brain_entries": {"daemon_id": [daemon["daemon_id"]]},
                "daemon_memory_promotions": {"daemon_id": [daemon["daemon_id"]]},
            },
            {
                "daemon_memory_events": "Retained transactional event history",
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
    assert receipt["entries"] == 12
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
    for path, original in originals.items():
        assert path.read_bytes() == original
        assert not Path(str(path) + "-wal").exists()
        assert not Path(str(path) + "-shm").exists()


@pytest.mark.parametrize("header", [
    "id: &shared one\ntype: note\nother: *shared",
    "id: one\ntype: note\nvalue: " + "[" * 70 + "x" + "]" * 70,
    'id: one\ntype: note\nvalue: !!python/object:builtins.object {}',
    'id: one\ntype: note\nvalue: .nan',
])
def test_owner_frontmatter_refuses_expansion_and_nonportable_values(header):
    with pytest.raises(InvalidBundle):
        parse(("---\n" + header + "\n---\nBody").encode())


def test_owner_yaml_frontmatter_is_bounded_before_constructing_values():
    with pytest.raises(InvalidBundle, match="exceeds"):
        parse(b"---\nid: one\ntype: note\nvalue: " + b"x" * (1024 * 1024) + b"\n---\n")
