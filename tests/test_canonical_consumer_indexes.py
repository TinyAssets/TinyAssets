"""Real consumer operations over reviewed owner-box records and disposable indexes."""

from dataclasses import replace

import pytest

from tests.support.canonical_sources import sources
from tests.test_okf_canonical_bundle import bundle  # noqa: F401
from tinyassets.boxes.provider import WriteConflict
from tinyassets.knowledge.knowledge_graph import KnowledgeGraph
from tinyassets.knowledge.models import GraphEntity
from tinyassets.memory.episodic import EpisodicMemory
from tinyassets.memory.promises import SeriesPromiseTracker
from tinyassets.memory.temporal import TemporalFact, TemporalFactStore
from tinyassets.memory.versioning import OutputVersionStore
from tinyassets.okf.bundle import INDEX, InvalidBundle


@pytest.fixture
def migrated(bundle, tmp_path):  # noqa: F811
    return sources(
        bundle,
        tmp_path,
        {
            "episodic": ["a"],
            "promises": ["a"],
            "versions": ["a"],
            "temporal": ["a"],
            "knowledge": ["a", None],
            "daemon": ["daemon-a"],
            "project": ["project-a"],
        },
    )


def test_all_five_consumers_write_canonical_history_and_reopen(migrated):
    episodes = EpisodicMemory(migrated["episodic"], universe_id="a")
    episodes.store_fact("f", "subject", "Remember this")
    episodes.store_fact("f", "subject", "Remember this")
    assert episodes.get_facts_for_entity("subject")[0]["evidence_count"] == 2
    promises = SeriesPromiseTracker(migrated["promises"], universe_id="a")
    promises.create_promise("p", "Keep promise", 1, 1)
    promises.add_evidence("p", {"source": "owner"})
    promises.resolve_promise("p", 2, 1)
    assert promises.get_all_promises()[0].status == "resolved"
    versions = OutputVersionStore(migrated["versions"], universe_id="a")
    versions.save_draft(1, 1, 1, "First")
    versions.save_draft(1, 1, 1, "Second")
    versions.rollback(1, 1, 1, 1)
    assert versions.version_count(1, 1, 1) == 2
    assert versions.get_current(1, 1, 1).prose == "First"
    temporal = TemporalFactStore(migrated["temporal"])
    temporal.assert_fact(
        TemporalFact("old", "place", "name", "Before", "2026-01-01", branch_id="a")
    )
    temporal.supersede(
        "old",
        TemporalFact(
            "new", "place", "name", "After", "2026-02-01", supersedes="old", branch_id="a"
        ),
    )
    assert len(migrated["temporal"].rows(migrated["temporal"].snapshot())["temporal_facts"]) == 2
    graph = KnowledgeGraph(migrated["knowledge"])
    graph.add_entity(
        GraphEntity(
            entity_id="subject",
            entity_type="concept",
            access_tier=0,
            public_description="Description",
            hidden_description="",
            secret_description="",
            aliases=[],
        )
    )
    assert graph.get_entity("subject")["public_description"] == "Description"
    for value in (episodes, promises, versions, temporal, graph):
        value.close()
    reopened = OutputVersionStore(migrated["versions"], universe_id="a")
    assert [v.prose for v in reopened.get_all_versions(1, 1, 1)] == ["Second", "First"]
    reopened.close()


def test_owner_edit_and_forget_rebuild_existing_consumer(migrated):
    source = migrated["promises"]
    store = SeriesPromiseTracker(source, universe_id="a")
    store.create_promise("p", "Original", 1, 1)
    before = source.snapshot()
    item = next(e for e in before.entries.values() if e.metadata.get("source") == "promises")
    source.bundle.commit(before, {item.id: replace(item, body="Owner edit")}, operation_id="edit")
    assert store.get_all_promises()[0].description == "Owner edit"
    source.bundle.commit(source.snapshot(), {item.id: None}, operation_id="forget")
    assert store.get_all_promises() == []


def test_related_version_update_refuses_concurrent_edit(migrated, monkeypatch):
    source = migrated["versions"]
    store = OutputVersionStore(source, universe_id="a")
    store.save_draft(1, 1, 1, "Before")
    old = source.snapshot()
    real = source.write

    def conflict(*args, **kwargs):
        source.bundle.provider.write(
            source.bundle.handle, "owner-other", "/cc/changed", b"x", max_bytes=1
        )
        return real(*args, **kwargs)

    monkeypatch.setattr(source, "write", conflict)
    with pytest.raises(WriteConflict):
        store.save_draft(1, 1, 1, "After")
    assert source.snapshot().entries == old.entries
    monkeypatch.setattr(source, "write", real)
    store.save_draft(1, 1, 1, "After retry with a new operation")
    assert store.version_count(1, 1, 1) == 2


def test_lost_commit_reply_retries_exact_original_diff(migrated, monkeypatch):
    source = migrated["versions"]
    store = OutputVersionStore(source, universe_id="a")
    provider = source.bundle.provider
    real = provider.write

    def lost(handle, op, path, *args, **kwargs):
        result = real(handle, op, path, *args, **kwargs)
        if path == INDEX:
            raise OSError("lost canonical reply")
        return result

    monkeypatch.setattr(provider, "write", lost)
    with pytest.raises(OSError, match="lost canonical"):
        store.save_draft(1, 1, 1, "Once", operation_id="stable-write")
    monkeypatch.setattr(provider, "write", real)
    store._conn.retry_pending()
    assert store.version_count(1, 1, 1) == 1
    with pytest.raises(InvalidBundle, match="already committed"):
        store.save_draft(1, 1, 1, "Once", operation_id="stable-write")


@pytest.mark.parametrize(
    "factory",
    [EpisodicMemory, SeriesPromiseTracker, OutputVersionStore, TemporalFactStore, KnowledgeGraph],
)
def test_host_paths_are_never_a_canonical_fallback(factory, tmp_path):
    with pytest.raises(InvalidBundle, match="admitted canonical"):
        factory(tmp_path / "legacy.db")
    assert not (tmp_path / "legacy.db").exists()


def test_unreviewed_null_and_peer_namespaces_refuse(migrated):
    store = TemporalFactStore(migrated["temporal"])
    with pytest.raises(InvalidBundle, match="declared source scope"):
        store.assert_fact(TemporalFact("f", "subject", "attr", "Unassigned", "2026-01-01"))
    assert not migrated["temporal"].rows(migrated["temporal"].snapshot())["temporal_facts"]
    store.assert_fact(
        TemporalFact("valid", "subject", "attr", "Assigned", "2026-01-01", branch_id="a")
    )
    assert len(migrated["temporal"].rows(migrated["temporal"].snapshot())["temporal_facts"]) == 1


def test_reclustering_can_restore_a_derived_natural_key(migrated):
    graph = KnowledgeGraph(migrated["knowledge"])
    graph.store_communities([{"community_id": 0, "entities": ["a", "b"]}])
    graph.store_communities(
        [{"community_id": 0, "entities": ["a"]}, {"community_id": 1, "entities": ["b"]}]
    )
    graph.store_communities([{"community_id": 0, "entities": ["a", "b"]}])
    assert len(graph.get_communities()) == 1
    assert len(migrated["knowledge"].rows(migrated["knowledge"].snapshot())["communities"]) == 2


def test_reclustering_respects_an_explicit_owner_forget(migrated):
    source = migrated["knowledge"]
    graph = KnowledgeGraph(source)
    graph.store_communities([{"community_id": 0, "entities": ["a", "b"]}])
    before = source.snapshot()
    key = next(
        key for key, row in source.rows(before)["communities"].items() if row["entity_id"] == "b"
    )
    source.bundle.commit(before, {key: None}, operation_id="owner-forgets-membership")
    graph.store_communities([{"community_id": 0, "entities": ["a", "b"]}])
    assert [row["entity_id"] for row in source.rows(source.snapshot())["communities"].values()] == [
        "a"
    ]


def test_read_only_consumer_does_not_scan_history_for_a_generated_id(migrated, monkeypatch):
    source = migrated["promises"]
    store = SeriesPromiseTracker(source, universe_id="a")
    store.create_promise("p", "Remember", 1, 1)

    def unexpected(*args, **kwargs):
        raise AssertionError("A read does not need to check generated operation IDs")

    monkeypatch.setattr(source.bundle, "operation_committed", unexpected)
    assert len(store.get_all_promises()) == 1


def test_quota_refusal_releases_a_cached_consumer(migrated, monkeypatch):
    from tinyassets.storage_accounting import StorageRefused

    source = migrated["promises"]
    store = SeriesPromiseTracker(source, universe_id="a")
    real = source.bundle.provider.write

    def refuse(*args, **kwargs):
        raise StorageRefused({"failure_class": "storage_quota_exceeded", "error": "quota"})

    monkeypatch.setattr(source.bundle.provider, "write", refuse)
    with pytest.raises(StorageRefused):
        store.create_promise("p", "Refused", 1, 1)
    monkeypatch.setattr(source.bundle.provider, "write", real)
    store.create_promise("p", "Permitted after capacity returns", 1, 1)
    assert len(store.get_all_promises()) == 1


def test_daemon_search_and_packet_do_not_write_canonical_telemetry(migrated, monkeypatch):
    from tinyassets.daemon_brain import (
        build_daemon_brain_packet,
        capture_daemon_memory,
        search_daemon_memory,
    )

    source = migrated["daemon"]
    capture_daemon_memory(
        source,
        daemon_id="daemon-a",
        content="Remember the fact",
        source_type="test",
        source_id="read-only-search",
        reliability="source-backed",
        language_type="descriptive",
    )
    before = source.snapshot()

    def unavailable(*args, **kwargs):
        raise AssertionError("A canonical read must not require storage admission")

    monkeypatch.setattr(source.bundle.provider, "write", unavailable)
    assert search_daemon_memory(source, daemon_id="daemon-a", query="fact")["count"] == 1
    assert (
        build_daemon_brain_packet(source, daemon_id="daemon-a", query="fact")["selected_count"] == 1
    )
    assert source.snapshot() == before


def test_daemon_capture_review_promote_and_query_are_canonical(migrated):
    from tinyassets.daemon_brain import (
        capture_daemon_memory,
        list_daemon_memory,
        memory_observability_status,
        promote_daemon_memory_to_wiki,
        review_daemon_memory,
        search_daemon_memory,
    )

    source = migrated["daemon"]
    first = capture_daemon_memory(
        source,
        daemon_id="daemon-a",
        content="Retain provenance",
        reliability="owner_verified",
        language_type="literal",
        embedding=[0.5, 0.8],
        metadata={"source": "owner"},
    )
    review_daemon_memory(
        source,
        daemon_id="daemon-a",
        entry_id=first["entry_id"],
        decision="accept",
        reviewer_id="owner-a",
    )
    result = promote_daemon_memory_to_wiki(
        source, daemon_id="daemon-a", entry_ids=[first["entry_id"]], summary="Reviewed summary"
    )
    assert result["promoted_count"] == 1
    assert source.bundle.provider.stat(source.bundle.handle, result["target_path"]).kind == "file"
    status = memory_observability_status(source, daemon_id="daemon-a")
    assert status["entry_count"] == 1 and status["promotion_count"] == 1
    assert status["event_count"] == 3
    assert (
        list_daemon_memory(source, daemon_id="daemon-a")["entries"][0]["metadata"]["source"]
        == "owner"
    )
    hits = search_daemon_memory(source, daemon_id="daemon-a", query="", query_embedding=[0.5, 0.8])
    assert hits["entries"][0]["entry_id"] == first["entry_id"]
    with pytest.raises(InvalidBundle, match="reviewed namespace"):
        list_daemon_memory(source, daemon_id="peer-daemon")


def test_session_scope_requires_admission_and_clears_after_exit(migrated, monkeypatch):
    from tinyassets import runtime_singletons
    from tinyassets.agent_loop import served_chat
    from tinyassets.memory.runtime import current_memory, owner_memory_scope

    provider = migrated["episodic"].bundle.provider
    monkeypatch.setattr(served_chat, "configured_box_provider", lambda: (provider, None))
    with pytest.raises(InvalidBundle, match="authenticated owner session"):
        current_memory()
    with owner_memory_scope(owner="owner-a", center="a", namespace="a") as memory:
        assert runtime_singletons.knowledge_graph is memory.get("knowledge_graph")
        runtime_singletons.promise_tracker.create_promise("scoped", "Owned", 1, 1)
        assert runtime_singletons.promise_tracker.get_all_promises()[0].description == "Owned"
    with pytest.raises(InvalidBundle, match="authenticated owner session"):
        current_memory()
    with pytest.raises(InvalidBundle, match="reviewed canonical binding"):
        with owner_memory_scope(owner="owner-a", center="a", namespace="other"):
            pytest.fail("wrong namespace admitted")


def test_temporal_index_forgets_owner_deleted_facts(migrated):
    from tinyassets.memory.temporal import TemporalIndex

    source = migrated["temporal"]
    store = TemporalFactStore(source)
    store.assert_fact(TemporalFact("f", "subject", "attr", "value", "2026-01-01", branch_id="a"))
    index = TemporalIndex()
    index.rebuild_from_store(store)
    assert index.entities_changed_since("") == {"subject"}
    before = source.snapshot()
    item = next(e for e in before.entries.values() if e.metadata.get("source") == "temporal")
    source.bundle.commit(before, {item.id: None}, operation_id="forget-fact")
    assert index.entities_changed_since("") == set()
    index.rebuild_from_store(store)
    assert not index._entity_timeline


def test_read_refuses_lossy_sql_affinity_without_changing_owner_value(migrated):
    source = migrated["promises"]
    store = SeriesPromiseTracker(source, universe_id="a")
    store.create_promise("p", "Original", 1, 1)
    before = source.snapshot()
    row = next(iter(source.rows(before)["series_promises"].values()))
    source.write(
        before,
        upserts={"series_promises": [{**row, "created_book": "001"}]},
        operation_id="typed-owner-edit",
    )
    edited = source.snapshot()
    with pytest.raises(InvalidBundle, match="typed change"):
        store.get_all_promises()
    assert source.snapshot() == edited


def test_unknown_columns_and_metadata_survive_consumer_update(bundle, tmp_path):  # noqa: F811
    def seed(name, db):
        db.execute("ALTER TABLE series_promises ADD COLUMN future BLOB")
        db.execute(
            """INSERT INTO series_promises
            (universe_id, promise_id, description, created_book, created_chapter, future)
            VALUES ('a', 'p', 'Original', 1, 1, ?)""",
            (b"\x00\xff",),
        )

    source = sources(bundle, tmp_path, {"promises": ["a"]}, mutate=seed)["promises"]
    before = source.snapshot()
    item = next(iter(before.entries.values()))
    source.bundle.commit(
        before,
        {
            item.id: replace(
                item,
                metadata={
                    **item.metadata,
                    "future_provenance": {"evidence": [1, None, "retained"]},
                },
            )
        },
        operation_id="future-field",
    )
    tracker = SeriesPromiseTracker(source, universe_id="a")
    tracker.add_evidence("p", {"source": "review"})
    after = source.snapshot()
    row = next(iter(source.rows(after)["series_promises"].values()))
    assert row["future"] == b"\x00\xff"
    assert after.entries[item.id].metadata["future_provenance"] == {
        "evidence": [1, None, "retained"]
    }


def test_owner_sessions_do_not_share_objects_or_survive_scope_exit(migrated, tmp_path, monkeypatch):
    from tinyassets.agent_loop import served_chat
    from tinyassets.memory.runtime import current_memory, owner_memory_scope
    from tinyassets.okf.bundle import KnowledgeBundle

    provider = migrated["episodic"].bundle.provider
    peer_root = tmp_path / "peer"
    peer_root.mkdir()
    sources(
        KnowledgeBundle(provider, provider.bind("b", account_id="owner-b")),
        peer_root,
        {
            "episodic": ["b"],
            "promises": ["b"],
            "versions": ["b"],
            "temporal": ["b"],
            "knowledge": ["b", None],
            "daemon": ["daemon-b"],
            "project": ["project-b"],
        },
    )
    monkeypatch.setattr(served_chat, "configured_box_provider", lambda: (provider, None))
    with owner_memory_scope(owner="owner-a", center="a", namespace="a") as first:
        retained = first.get("promise_tracker")
        retained_source = first.source("promises")
        retained.create_promise("private", "Owner A", 1, 1)
        with owner_memory_scope(owner="owner-b", center="b", namespace="b") as second:
            assert current_memory() is second
            with pytest.raises(InvalidBundle, match="current owner session"):
                retained.get_all_promises()
            assert second.get("promise_tracker").get_all_promises() == []
            second.get("promise_tracker").create_promise("private", "Owner B", 1, 1)
        assert current_memory() is first
        assert first.get("promise_tracker").get_all_promises()[0].description == "Owner A"
    with pytest.raises(InvalidBundle, match="session has ended"):
        retained_source.snapshot()
    with pytest.raises(InvalidBundle, match="session has ended"):
        retained.get_all_promises()
    with pytest.raises(InvalidBundle, match="session has ended"):
        retained.create_promise("late", "Must refuse", 1, 1)
    with pytest.raises(InvalidBundle, match="session has ended"):
        retained._conn.retry_pending()
    with pytest.raises(InvalidBundle, match="session has ended"):
        first.get("promise_tracker")
    with pytest.raises(InvalidBundle, match="session has ended"):
        second.source("daemon")


@pytest.mark.parametrize("kind", ["capture", "metadata", "promotion"])
def test_daemon_provider_refusal_is_visible_and_cannot_publish_partial_rows(
    migrated, monkeypatch, kind
):
    from tinyassets.daemon_brain import capture_daemon_memory, promote_daemon_memory_to_wiki
    from tinyassets.storage_accounting import StorageRefused

    source = migrated["daemon"]
    first = capture_daemon_memory(
        source,
        daemon_id="daemon-a",
        content="Retain",
        reliability="owner_verified",
        language_type="literal",
    )
    before = source.snapshot()

    def refuse(*args, **kwargs):
        raise StorageRefused({"failure_class": "storage_quota_exceeded", "error": "quota"})

    monkeypatch.setattr(source.bundle.provider, "write", refuse)
    with pytest.raises(StorageRefused):
        if kind == "promotion":
            promote_daemon_memory_to_wiki(
                source,
                daemon_id="daemon-a",
                entry_ids=[first["entry_id"]],
                summary="Review",
                metadata={"blob": "x" * 20000},
            )
        else:
            capture_daemon_memory(
                source,
                daemon_id="daemon-a",
                content="New" if kind == "metadata" else "x" * 20000,
                source_path="x" * 20000 if kind == "metadata" else "",
                reliability="owner_verified",
                language_type="literal",
            )
    assert source.snapshot() == before


def test_daemon_request_replay_cannot_duplicate_events(migrated):
    from tinyassets.daemon_brain import capture_daemon_memory, memory_observability_status

    source = migrated["daemon"]
    capture_daemon_memory(
        source,
        daemon_id="daemon-a",
        content="Once",
        reliability="owner_verified",
        language_type="literal",
        operation_id="stable-daemon-request",
    )
    with pytest.raises(InvalidBundle, match="already committed"):
        capture_daemon_memory(
            source,
            daemon_id="daemon-a",
            content="Once",
            reliability="owner_verified",
            language_type="literal",
            operation_id="stable-daemon-request",
        )
    assert memory_observability_status(source, daemon_id="daemon-a")["event_count"] == 1


def test_forget_reserves_identity_but_allows_new_rows(migrated):
    source = migrated["promises"]
    store = SeriesPromiseTracker(source, universe_id="a")
    store.create_promise("old", "Forget this", 1, 1)
    before = source.snapshot()
    item = next(iter(before.entries.values()))
    source.bundle.commit(before, {item.id: None}, operation_id="forget-identity")
    store.close()
    store = SeriesPromiseTracker(source, universe_id="a")
    store.create_promise("new", "Retain this", 1, 1)
    after = source.snapshot()
    assert item.id in after.manifest["tombstones"]
    assert item.id not in after.entries
    assert next(iter(after.entries.values())).metadata["identity"] == [["integer", "2"]]
    with pytest.raises(InvalidBundle, match="forgotten canonical identity"):
        source.bundle.commit(after, {item.id: item}, operation_id="stale-restore")
    assert [p.description for p in store.get_all_promises()] == ["Retain this"]


def test_migrated_integer_counter_survives_deleted_legacy_rows(bundle, tmp_path):  # noqa: F811
    def seed(name, db):
        db.execute("""INSERT INTO series_promises
            (id, universe_id, promise_id, description, created_book, created_chapter)
            VALUES (40, 'a', 'forgotten', 'Prior deletion', 1, 1)""")
        db.execute("DELETE FROM series_promises")

    source = sources(bundle, tmp_path, {"promises": ["a"]}, mutate=seed)["promises"]
    SeriesPromiseTracker(source, universe_id="a").create_promise("new", "New row", 1, 1)
    assert next(iter(source.snapshot().entries.values())).metadata["identity"] == [
        ["integer", "41"]
    ]
