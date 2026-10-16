"""Offline historical SQLite fixtures for migration tests only.

Runtime consumers never accept these connections or paths. The adapter invokes
repository schema/row writers solely to construct backup input to the migration.
"""

import sqlite3
from contextlib import contextmanager

from tests.support.canonical_sources import initializers


class OfflineConnection:
    def __init__(self, db):
        self.db = db

    def __getattr__(self, name):
        return getattr(self.db, name)

    @contextmanager
    def transaction(self, _operation=None):
        with self.db:
            yield self


def offline_consumer(factory, path, universe_id="default", window_chapters=5):
    names = {
        "KnowledgeGraph": "knowledge",
        "EpisodicMemory": "episodic",
        "SeriesPromiseTracker": "promises",
        "OutputVersionStore": "versions",
        "TemporalFactStore": "temporal",
    }
    obj = object.__new__(factory)
    db = sqlite3.connect(path)
    if factory.__name__ == "KnowledgeGraph":
        db.row_factory = sqlite3.Row
    initializers()[names[factory.__name__]][0](db)
    obj._conn = OfflineConnection(db)
    obj._universe_id, obj._window_chapters = universe_id, window_chapters
    return obj


def daemon_snapshot(path, daemon_id):
    from tinyassets.daemon_brain import initialize_daemon_brain

    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as db:
        initialize_daemon_brain(db)
        db.execute(
            """INSERT INTO daemon_brain_entries
            (entry_id, daemon_id, memory_kind, content, content_fingerprint, source_type,
             source_id, reliability, temporal_bounds_json, language_type, confidence,
             importance, sensitivity_tier, visibility, promotion_state, metadata_json,
             created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                "legacy-entry",
                daemon_id,
                "semantic",
                "Preserve daemon memory",
                "legacy-fingerprint",
                "manual",
                "fixture",
                "host_observed",
                "{}",
                "literal",
                0.5,
                0.5,
                "normal",
                "host_private",
                "candidate",
                '{"unknown": [1e20, "extra"]}',
                "2026-01-01",
                "2026-01-01",
            ),
        )
        db.execute(
            """INSERT INTO daemon_memory_events
            (event_id, daemon_id, event_type, trace_id, entry_ids_json, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)""",
            ("legacy-event", daemon_id, "capture", "trace", '["legacy-entry"]', "{}", "2026-01-01"),
        )
