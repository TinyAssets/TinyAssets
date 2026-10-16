"""Historical project schema fixtures, never a production storage path."""

import hashlib
import json
import sqlite3
from pathlib import Path

from tinyassets.okf.migration import commit_migration
from tinyassets.okf.namespace_census import (
    NamespaceSource,
    ReviewedBinding,
    census_sources,
    prepare_reviewed_migration,
)
from tinyassets.okf.records import SourceRecords

SCHEMA = """
CREATE TABLE project_memory (
 project_id TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
 updated_at TEXT NOT NULL, updated_by TEXT NOT NULL DEFAULT '',
 version INTEGER NOT NULL DEFAULT 1, PRIMARY KEY(project_id,key));
CREATE TABLE project_memory_history (
 id INTEGER PRIMARY KEY AUTOINCREMENT, project_id TEXT NOT NULL,
 key TEXT NOT NULL, value TEXT NOT NULL, updated_at TEXT NOT NULL,
 updated_by TEXT NOT NULL DEFAULT '', version INTEGER NOT NULL);
"""


def snapshot(path, rows=()):
    with sqlite3.connect(path) as db:
        db.executescript(SCHEMA)
        for project, key, value in rows:
            values = (project, key, json.dumps(value), "2026-01-01", "", 1)
            db.execute("INSERT INTO project_memory VALUES (?,?,?,?,?,?)", values)
            db.execute("INSERT INTO project_memory_history VALUES (NULL,?,?,?,?,?,?)", values)


def records(bundle, root, namespaces=("proj1", "proj2")):
    path = Path(root) / "project-backup.db"
    snapshot(path)
    source = NamespaceSource(
        path,
        "project",
        hashlib.sha256(path.read_bytes()).hexdigest(),
        {t: "project_id" for t in ("project_memory", "project_memory_history")},
        {},
    )
    center, owner, _ = bundle.scope
    bindings = [
        ReviewedBinding(
            "project",
            table,
            value,
            owner,
            center,
            source.expected_sha256,
            "operator",
            "fixture-review",
            "f" * 64,
        )
        for table in source.namespaces
        for value in namespaces
    ]
    def verify(account, home):
        return (account, home) == (owner, center)
    census = census_sources([source], bindings, verify_authority=verify)
    plan = prepare_reviewed_migration(
        bundle, census, operation_id="project-fixture", verify_authority=verify
    )
    commit_migration(bundle, plan)
    return SourceRecords(bundle, "project")
