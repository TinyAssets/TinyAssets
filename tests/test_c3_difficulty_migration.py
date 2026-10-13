"""M1 removes only retired policy keys after an offline backup."""

import json
import sqlite3
import tarfile

import pytest

from scripts.migrate_c3_difficulty_override import migrate


def _database(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    database = root / ".tinyassets.db"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE branch_definitions (branch_def_id TEXT PRIMARY KEY, "
                     "node_defs_json TEXT NOT NULL, default_llm_policy_json TEXT)")
        conn.executemany("INSERT INTO branch_definitions VALUES (?, ?, ?)", [
            ("changed", json.dumps([{"id": "node", "llm_policy": {
                "preferred": {"provider": "codex"}, "difficulty_override": []}}]),
             json.dumps({"fallback_chain": [], "difficulty_override": ["old"]})),
            ("untouched", '[{"id":"other"}]', None),
        ])
    archive = tmp_path / "full-backup.tar.gz"
    with tarfile.open(archive, "w:gz") as output:
        output.add(database, arcname=".tinyassets.db")
        output.add(root, arcname="data", recursive=False)
    return root, archive


def test_migration_requires_backup_and_stopped_writers_and_is_idempotent(tmp_path):
    root, archive = _database(tmp_path)
    assert migrate(root) == {"branches": ["changed"], "removed": 2, "applied": False}
    with pytest.raises(PermissionError):
        migrate(root, apply=True, writers_stopped=True)
    with pytest.raises(PermissionError):
        migrate(root, apply=True, archive=archive)
    with sqlite3.connect(root / ".tinyassets.db") as conn:
        untouched_before = conn.execute(
            "SELECT node_defs_json FROM branch_definitions WHERE branch_def_id='untouched'"
        ).fetchone()[0]
    assert migrate(root, archive=archive, apply=True, writers_stopped=True) == {
        "branches": ["changed"], "removed": 2, "applied": True,
    }
    with sqlite3.connect(root / ".tinyassets.db") as conn:
        nodes, default = conn.execute(
            "SELECT node_defs_json, default_llm_policy_json FROM branch_definitions "
            "WHERE branch_def_id='changed'"
        ).fetchone()
        untouched_after = conn.execute(
            "SELECT node_defs_json FROM branch_definitions WHERE branch_def_id='untouched'"
        ).fetchone()[0]
    assert json.loads(nodes)[0]["llm_policy"] == {"preferred": {"provider": "codex"}}
    assert json.loads(default) == {"fallback_chain": []}
    assert untouched_after == untouched_before
    assert migrate(root, archive=archive, apply=True, writers_stopped=True)["removed"] == 0


def test_migration_rolls_back_if_any_branch_is_malformed(tmp_path):
    root, archive = _database(tmp_path)
    with sqlite3.connect(root / ".tinyassets.db") as conn:
        conn.execute("UPDATE branch_definitions SET node_defs_json='{' "
                     "WHERE branch_def_id='untouched'")
    with pytest.raises(json.JSONDecodeError):
        migrate(root, archive=archive, apply=True, writers_stopped=True)
    with sqlite3.connect(root / ".tinyassets.db") as conn:
        nodes = conn.execute("SELECT node_defs_json FROM branch_definitions "
                             "WHERE branch_def_id='changed'").fetchone()[0]
    assert "difficulty_override" in nodes


def test_migration_cannot_apply_while_a_writer_holds_the_database(tmp_path):
    root, archive = _database(tmp_path)
    with sqlite3.connect(root / ".tinyassets.db") as writer:
        writer.execute("BEGIN IMMEDIATE")
        with pytest.raises(sqlite3.OperationalError, match="locked"):
            migrate(root, archive=archive, apply=True, writers_stopped=True)
        writer.rollback()
    assert migrate(root)["removed"] == 2
