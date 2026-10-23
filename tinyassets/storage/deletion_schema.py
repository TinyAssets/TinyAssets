"""Durable deletion tables; declarations owned by the storage layer."""

TABLES = {
    'deleted_principals': """CREATE TABLE IF NOT EXISTS deleted_principals (
        founder_sub TEXT PRIMARY KEY,
        deleted_at  REAL NOT NULL
    )""",
}

INDEXES = (
)
