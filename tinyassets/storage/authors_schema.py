"""Durable authors tables; declarations owned by the storage layer."""

TABLES = {
    'author_definitions': """CREATE TABLE IF NOT EXISTS author_definitions (
        author_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        soul_text TEXT NOT NULL,
        soul_hash TEXT NOT NULL,
        lineage_parent_id TEXT,
        reputation_score REAL NOT NULL DEFAULT 0.0,
        created_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
    'author_forks': """CREATE TABLE IF NOT EXISTS author_forks (
        fork_id TEXT PRIMARY KEY,
        parent_author_id TEXT NOT NULL,
        child_author_id TEXT NOT NULL,
        proposed_by TEXT NOT NULL,
        vote_id TEXT,
        reason TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
    'author_runtime_instances': """CREATE TABLE IF NOT EXISTS author_runtime_instances (
        instance_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        model_name TEXT NOT NULL,
        branch_id TEXT,
        status TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
}

INDEXES = (
)
