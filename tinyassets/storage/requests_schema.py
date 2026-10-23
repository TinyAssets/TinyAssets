"""Durable requests tables; declarations owned by the storage layer."""

TABLES = {
    'user_requests': """CREATE TABLE IF NOT EXISTS user_requests (
        request_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        branch_id TEXT,
        user_id TEXT NOT NULL,
        request_type TEXT NOT NULL,
        text TEXT NOT NULL,
        preferred_author_id TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    )""",
    'vote_windows': """CREATE TABLE IF NOT EXISTS vote_windows (
        vote_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        vote_type TEXT NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        created_by TEXT NOT NULL,
        opens_at REAL NOT NULL,
        closes_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        payload_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT NOT NULL DEFAULT '{}'
    )""",
    'vote_ballots': """CREATE TABLE IF NOT EXISTS vote_ballots (
        vote_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        choice TEXT NOT NULL,
        comment TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        PRIMARY KEY(vote_id, user_id)
    )""",
    'action_records': """CREATE TABLE IF NOT EXISTS action_records (
        action_id TEXT PRIMARY KEY,
        universe_id TEXT,
        visibility TEXT NOT NULL,
        actor_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        created_at REAL NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}'
    )""",
}

INDEXES = (
)
