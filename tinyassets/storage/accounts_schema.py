"""Durable accounts tables; declarations owned by the storage layer."""

TABLES = {
    'user_accounts': """CREATE TABLE IF NOT EXISTS user_accounts (
        user_id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
    'user_sessions': """CREATE TABLE IF NOT EXISTS user_sessions (
        session_token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        expires_at REAL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(user_id) REFERENCES user_accounts(user_id) ON DELETE CASCADE
    )""",
    'capability_grants': """CREATE TABLE IF NOT EXISTS capability_grants (
        user_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT '*',
        granted_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL,
        revoked_at REAL,
        generation INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
        PRIMARY KEY(user_id, capability, scope, generation),
        CHECK (expires_at IS NULL OR expires_at > created_at),
        CHECK (revoked_at IS NULL OR revoked_at >= created_at),
        FOREIGN KEY(user_id) REFERENCES user_accounts(user_id) ON DELETE CASCADE
    )""",
}

INDEXES = (
)
