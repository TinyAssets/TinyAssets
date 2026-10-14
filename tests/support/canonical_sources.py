"""Explicit reviewed empty snapshots for consumer tests; never a runtime fallback."""

from __future__ import annotations

import hashlib
import sqlite3

import pytest

from tinyassets.okf.migration import commit_migration
from tinyassets.okf.namespace_census import (
    NamespaceSource,
    ReviewedBinding,
    census_sources,
    prepare_reviewed_migration,
)
from tinyassets.okf.records import SourceRecords


def initializers():
    from tests.support.project_snapshot import SCHEMA as project
    from tinyassets.daemon_brain import initialize_daemon_brain
    from tinyassets.knowledge.knowledge_graph import initialize_knowledge_index
    from tinyassets.memory.episodic import initialize_episodic_index
    from tinyassets.memory.promises import _SCHEMA as promises
    from tinyassets.memory.temporal import _TEMPORAL_SCHEMA as temporal
    from tinyassets.memory.versioning import _SCHEMA as versions

    return {
        "project": (lambda db: db.executescript(project), "project_id"),
        "daemon": (initialize_daemon_brain, "daemon_id"),
        "knowledge": (initialize_knowledge_index, "universe_id"),
        "episodic": (initialize_episodic_index, "universe_id"),
        "promises": (lambda db: db.executescript(promises), "universe_id"),
        "versions": (lambda db: db.executescript(versions), "universe_id"),
        "temporal": (lambda db: db.executescript(temporal), "branch_id"),
    }


def sources(bundle, root, namespaces, *, mutate=None):
    """namespaces maps source names to deliberately assigned legacy namespace values."""
    definitions, bindings = [], []
    center, owner, _ = bundle.scope
    for name, values in namespaces.items():
        initialize, column = initializers()[name]
        path = root / (name + "-snapshot.db")
        with sqlite3.connect(path) as db:
            db.row_factory = sqlite3.Row
            initialize(db)
            if mutate:
                mutate(name, db)
            tables = {
                row[1]: column
                for row in db.execute("PRAGMA table_list")
                if row[0] == "main" and row[2] == "table" and not row[1].startswith("sqlite_")
            }
            excluded = {
                row[1]: "Disposable full-text index"
                for row in db.execute("PRAGMA table_list")
                if row[0] == "main" and row[2] in ("virtual", "shadow")
            }
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        definitions.append(NamespaceSource(path, name, digest, tables, excluded))
        bindings.extend(
            ReviewedBinding(
                name,
                table,
                value,
                owner,
                center,
                digest,
                "fixture-operator",
                "explicit-fixture-review",
                "a" * 64,
            )
            for table in tables
            for value in values
        )

    def verify(account, home):
        return (account, home) == (owner, center)

    census = census_sources(definitions, bindings, verify_authority=verify)
    plan = prepare_reviewed_migration(
        bundle, census, operation_id="reviewed-fixture", verify_authority=verify
    )
    commit_migration(bundle, plan)
    return {name: SourceRecords(bundle, name) for name in namespaces}


@pytest.fixture
def canonical_source(tmp_path):
    """Explicit test admissions; keys share a bundle only within this test."""
    from tinyassets.boxes.local import LocalBoxProvider
    from tinyassets.okf.bundle import KnowledgeBundle

    cache, providers = {}, []

    def make(name, *, key=None, namespaces):
        identity = (name, str(key)) if key is not None and str(key) != ":memory:" else object()
        if identity not in cache:
            root = tmp_path / ("canonical-" + str(len(cache)))
            root.mkdir()
            provider = LocalBoxProvider(
                boxes_root=root / "boxes",
                state_dir=root / "state",
                owner_of={"test-center": "test-owner"}.get,
                allow_unisolated=True,
            )
            providers.append(provider)
            bundle = KnowledgeBundle(
                provider, provider.bind("test-center", account_id="test-owner")
            )
            assigned = {name: namespaces}
            if name == "episodic":
                assigned["knowledge"] = [None, *namespaces]
            cache[identity] = sources(bundle, root, assigned)[name]
        return cache[identity]

    yield make
    for provider in providers:
        provider.close()


@pytest.fixture
def canonical_daemon(canonical_source, monkeypatch):
    """Explicitly admit daemons constructed in this test's registry fixture."""
    from types import SimpleNamespace

    from tinyassets.daemon_registry import list_daemons
    from tinyassets.memory import runtime

    def make(root):
        source = canonical_source(
            "daemon",
            key=root,
            namespaces=[
                item["daemon_id"] for item in list_daemons(root) if item["soul_mode"] == "soul"
            ],
        )
        monkeypatch.setattr(
            runtime, "current_memory", lambda: SimpleNamespace(source=lambda _: source)
        )
        return source

    return make


@pytest.fixture
def canonical_runtime(tmp_path, monkeypatch):
    """Graph tests enter a real admitted memory lifetime instead of implicit globals."""
    from tinyassets.agent_loop import served_chat
    from tinyassets.boxes.local import LocalBoxProvider
    from tinyassets.memory.runtime import owner_memory_scope
    from tinyassets.okf.bundle import KnowledgeBundle

    root = tmp_path / "graph-memory"
    root.mkdir()
    provider = LocalBoxProvider(
        boxes_root=root / "boxes",
        state_dir=root / "state",
        owner_of={"test": "fixture-owner"}.get,
        allow_unisolated=True,
    )
    namespaces = ["test", "test-universe", "integ-test", "stability-test"]
    bundle = KnowledgeBundle(provider, provider.bind("test", account_id="fixture-owner"))
    sources(
        bundle,
        root,
        {
            "project": ["test-project"],
            "daemon": ["test-daemon"],
            "knowledge": [None, *namespaces],
            "episodic": namespaces,
            "promises": namespaces,
            "versions": namespaces,
            "temporal": namespaces,
        },
    )
    monkeypatch.setattr(served_chat, "configured_box_provider", lambda: (provider, None))
    try:
        with owner_memory_scope(owner="fixture-owner", center="test", namespace="test") as memory:
            yield memory
    finally:
        provider.close()
