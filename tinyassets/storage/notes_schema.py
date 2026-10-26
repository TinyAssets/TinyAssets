"""Durable notes tables; declarations owned by the storage layer."""

TABLES = {
    'universe_notes': """CREATE TABLE IF NOT EXISTS universe_notes (
        note_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        source TEXT NOT NULL,
        text TEXT NOT NULL,
        category TEXT NOT NULL,
        status TEXT NOT NULL,
        target TEXT,
        clearly_wrong INTEGER NOT NULL DEFAULT 0,
        quoted_passage TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        timestamp REAL NOT NULL,
        updated_at REAL NOT NULL
    )""",
    'universe_work_targets': """CREATE TABLE IF NOT EXISTS universe_work_targets (
        universe_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(universe_id, target_id)
    )""",
    'universe_hard_priorities': """CREATE TABLE IF NOT EXISTS universe_hard_priorities (
        universe_id TEXT NOT NULL,
        priority_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(universe_id, priority_id)
    )""",
}

INDEXES = (
)
