"""C8 rehearsal on a SQLite backup of the current agent schema and owner files."""

from __future__ import annotations

import shutil
import sqlite3

import pytest

from tinyassets.addressed_agents import memory_session
from tinyassets.agent_files import list_agents, migrate_all_owners, migrate_owner
from tinyassets.conversation_store import load_recent_readonly, record_turn
from tinyassets.custom_agents import create_binding, db_path, publish_definition
from tinyassets.universe_owner import _TABLE_DDL


def _definition(base, owner, name):
    return publish_definition(base, author_id=owner, payload={
        "schema_version": 1, "name": name,
        "components": {"identity": {"kind": "soul", "config": {
            "instructions": f"Answer as {name}."}}},
    })["agent_definition_id"]


def _binding(base, owner, universe, definition, name):
    return create_binding(
        base, universe_id=universe, definition_id=definition,
        created_by=owner, payload={"schema_version": 1, "name": name},
    )["agent_binding_id"]


def _copy_database(source, target):
    target.mkdir()
    with sqlite3.connect(db_path(source)) as old, sqlite3.connect(db_path(target)) as new:
        old.backup(new)
    shutil.copytree(source / "u-owner", target / "u-owner")
    shutil.copytree(source / "u-bob", target / "u-bob")


def test_migration_preserves_binding_addresses_serving_and_conversation(tmp_path):
    source, target = tmp_path / "current", tmp_path / "migration-copy"
    (source / "u-owner").mkdir(parents=True)
    (source / "u-bob").mkdir()
    owner, universe = "alice", "u-owner"
    addressed_definition = _definition(source, owner, "Research Agent")
    addressed = _binding(source, owner, universe, addressed_definition, "Research Agent")
    serving_definition = _definition(source, owner, "Main Source")
    serving = _binding(source, owner, universe, serving_definition, "Main Source")
    unbound = _definition(source, owner, "Unbound Agent")
    bob = _binding(source, "bob", "u-bob", _definition(source, "bob", "Private"),
                   "Private")
    with sqlite3.connect(db_path(source)) as conn:
        conn.execute("UPDATE agent_bindings SET status = 'serving' WHERE agent_binding_id = ?",
                     (serving,))
        conn.execute("CREATE TABLE IF NOT EXISTS founder_home (founder_sub TEXT PRIMARY KEY, "
                     "universe_id TEXT NOT NULL)")
        conn.execute("INSERT INTO founder_home VALUES (?, ?)", (owner, universe))
        conn.execute("INSERT INTO founder_home VALUES (?, ?)", ("bob", "u-bob"))
        for statement in _TABLE_DDL:
            conn.execute(statement)
        conn.execute("INSERT INTO universe_owner VALUES (?, ?, 1, 'creation')",
                     (universe, owner))
        conn.execute("INSERT INTO universe_owner VALUES (?, ?, 1, 'creation')",
                     ("u-bob", "bob"))
        binding_schema = "SELECT sql FROM sqlite_master WHERE name = 'agent_bindings'"
        old_schema = conn.execute(binding_schema).fetchone()[0]
    session = memory_session(owner, addressed)
    record_turn(source / universe, session, "founder", "Earlier question", ts=1)
    record_turn(source / universe, session, "universe", "Earlier answer", ts=2)
    _copy_database(source, target)

    written = migrate_all_owners(target)[owner]
    agents = {agent["agent_id"]: agent for agent in list_agents(
        target, universe_id=universe, owner_user_id=owner)}
    assert len(written) == len(agents) == 3
    assert set(agents) == {addressed, serving, unbound}
    assert [a["agent_id"] for a in list_agents(
        target, universe_id="u-bob", owner_user_id="bob")] == [bob]
    assert agents[addressed]["binding"]["agent_definition_id"] == addressed_definition
    assert agents[addressed]["instructions"] == "## identity (soul)\n\nAnswer as Research Agent.\n"
    assert agents[serving]["binding"]["status"] == "serving"
    assert agents[unbound]["binding"] is None
    assert [message.text for message in load_recent_readonly(target / universe, session)] == [
        "Earlier question", "Earlier answer"]
    with sqlite3.connect(db_path(target)) as conn:
        assert conn.execute(binding_schema).fetchone()[0] == old_schema
        assert conn.execute("SELECT count(*) FROM agent_file_migrations").fetchone()[0] == 2

    # A finished owner is never recopied over an edit to an agent's files.
    instructions = (target / universe / agents[addressed]["directory"]
                    / "instructions" / "identity.md")
    instructions.write_text("Owner-edited instructions\n", encoding="utf-8")
    assert migrate_owner(target, owner) == []
    assert {a["agent_id"]: a for a in list_agents(
        target, universe_id=universe, owner_user_id=owner)}[addressed]["instructions"] == (
            "## identity (soul)\n\nOwner-edited instructions\n")


def test_conflicting_existing_file_refuses_owner_transaction(tmp_path):
    base = tmp_path
    (base / "u").mkdir()
    did = _definition(base, "alice", "Agent")
    bid = _binding(base, "alice", "u", did, "Agent")
    with sqlite3.connect(db_path(base)) as conn:
        for statement in _TABLE_DDL:
            conn.execute(statement)
        conn.execute("INSERT INTO universe_owner VALUES (?, ?, 1, 'creation')",
                     ("u", "alice"))
    from tinyassets.agent_files import agent_directory

    path = base / "u" / agent_directory("Agent", bid) / "instructions" / "identity.md"
    path.parent.mkdir(parents=True)
    path.write_text("pre-existing owner file", encoding="utf-8")
    with pytest.raises(ValueError, match="already edited or conflicts"):
        migrate_owner(base, "alice")
    with sqlite3.connect(db_path(base)) as conn:
        marker = conn.execute("SELECT name FROM sqlite_master WHERE "
                              "name = 'agent_file_migrations'").fetchone()
        assert marker is None or conn.execute(
            "SELECT count(*) FROM agent_file_migrations").fetchone()[0] == 0


def test_binding_in_another_owners_space_is_not_migrated(tmp_path):
    (tmp_path / "alice-space").mkdir()
    did = _definition(tmp_path, "bob", "Shared Agent")
    _binding(tmp_path, "bob", "alice-space", did, "Shared Agent")
    with sqlite3.connect(db_path(tmp_path)) as conn:
        for statement in _TABLE_DDL:
            conn.execute(statement)
        conn.execute("INSERT INTO universe_owner VALUES (?, ?, 1, 'creation')",
                     ("alice-space", "alice"))
    with pytest.raises(PermissionError, match="owner's own space"):
        migrate_owner(tmp_path, "bob")
    assert not (tmp_path / "alice-space" / "agents").exists()
