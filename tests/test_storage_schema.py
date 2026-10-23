"""S10: schema ownership, durable layouts, and the one-shot row carry."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tinyassets import box_layout_migration as migration
from tinyassets.sqlite_schema import SchemaDrift
from tinyassets.storage import schema


def test_daemon_owns_no_tables_and_each_declaration_has_one_home():
    source = Path(__file__).resolve().parents[1] / "tinyassets/daemon_server.py"
    assert "CREATE TABLE" not in source.read_text(encoding="utf-8")
    names = [name for store in schema.STORES for name in store.TABLES]
    assert len(names) == len(set(names)) == 27
    assert set(names) <= migration.CENSUS_RULES[".tinyassets.db"].keys()


def test_concurrent_initialization_preserves_durable_rows(tmp_path):
    with ThreadPoolExecutor(max_workers=8) as pool:
        paths = list(pool.map(schema.initialize_author_server, [tmp_path] * 8))
    assert len(set(paths)) == 1
    with sqlite3.connect(paths[0]) as db:
        db.execute("INSERT INTO universe_notes "
                   "(note_id, universe_id, source, text, category, status, timestamp, updated_at) "
                   "VALUES ('note', 'center', 'owner', 'keep verbatim', 'note', 'open', 1, 1)")
    schema._AUTHOR_SERVER_INITIALIZED.discard(str(paths[0]))
    schema.initialize_author_server(tmp_path)
    with sqlite3.connect(paths[0]) as db:
        assert db.execute("SELECT text FROM universe_notes").fetchone()[0] == "keep verbatim"


def test_durable_drift_refuses_without_deleting_rows(tmp_path):
    path = schema.initialize_author_server(tmp_path)
    with sqlite3.connect(path) as db:
        db.execute("ALTER TABLE universe_notes ADD COLUMN future_fact TEXT")
        db.execute("INSERT INTO universe_notes "
                   "(note_id, universe_id, source, text, category, status, timestamp, updated_at, "
                   "future_fact) VALUES ('note', 'center', 'owner', 'keep', "
                   "'note', 'open', 1, 1, 'new')")
    schema._AUTHOR_SERVER_INITIALIZED.discard(str(path))
    with pytest.raises(SchemaDrift, match="universe_notes"):
        schema.initialize_author_server(tmp_path)
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT text, future_fact FROM universe_notes").fetchall() == [
            ("keep", "new")]


@pytest.mark.parametrize("table", schema.TABLES)
def test_cutover_carries_every_live_table_to_its_existing_home(tmp_path, table):
    data, owners, shared = (tmp_path / name for name in ("source", "owners", "shared"))
    data.mkdir()
    with sqlite3.connect(data / ".tinyassets.db") as db:
        db.execute(schema.TABLES[table])
        columns = list(db.execute(f'PRAGMA table_info("{table}")'))
        values = {col[1]: (3 if col[1] in {"expires_at", "revoked_at"} else
                          1 if col[2] in {"INTEGER", "REAL"} else "preserved:" + col[1])
                  for col in columns}
        names = ",".join('"' + name + '"' for name in values)
        placeholders = ",".join("?" for _ in values)
        db.execute(f'INSERT INTO "{table}" ({names}) VALUES ({placeholders})',
                   tuple(values.values()))
    placement = migration.CENSUS_RULES[".tinyassets.db"][table].placement
    target = ("owner", "alice") if placement == "owner" else placement
    inventory = {"owners": {"alice": 300001}, "tables": {(".tinyassets.db", table): [values]},
                 "assignments": {(".tinyassets.db", table): [target]}, "published_owner_copies": {}}
    counts = migration._build_databases(data, owners, shared, inventory)
    assert sum(counts.values()) == 1
    destination = (owners / "300001/.tinyassets.db" if placement == "owner" else
                   shared / migration._database_target(".tinyassets.db", table, placement))
    with sqlite3.connect(destination) as db:
        if table == "deleted_principals":
            assert db.execute("SELECT digest FROM deleted_principals").fetchall() == [
                (values["founder_sub"],)]
        else:
            assert db.execute(f'SELECT {names} FROM "{table}"').fetchall() == [
                tuple(values.values())]
    with sqlite3.connect(data / ".tinyassets.db") as db:
        assert db.execute(f'SELECT {names} FROM "{table}"').fetchall() == [
                tuple(values.values())]


def test_cutover_refuses_unrecognized_columns_without_changing_source(tmp_path):
    with sqlite3.connect(tmp_path / "source.db") as db:
        db.execute(schema.TABLES["universe_notes"])
        db.execute("ALTER TABLE universe_notes ADD COLUMN future_fact TEXT")
        ddl = db.execute("SELECT sql FROM sqlite_schema WHERE name='universe_notes'").fetchone()[0]
        with pytest.raises(RuntimeError, match="would lose columns"):
            migration._current_table_ddl(".tinyassets.db", "universe_notes", db, ddl)
        assert db.execute("SELECT sql FROM sqlite_schema "
                          "WHERE name='universe_notes'").fetchone()[0] == ddl


def test_cutover_normalizes_historical_column_order_and_preserves_values(tmp_path):
    data = tmp_path / "source"
    data.mkdir()
    ddl = schema.TABLES["gate_claims"].replace(
        "        conformance_pack_id TEXT NOT NULL DEFAULT '',\n", "")
    with sqlite3.connect(data / ".tinyassets.db") as db:
        db.execute(ddl)
        db.execute("ALTER TABLE gate_claims ADD COLUMN conformance_pack_id "
                   "TEXT NOT NULL DEFAULT ''")
        db.execute("INSERT INTO gate_claims "
                   "(claim_id, branch_def_id, goal_id, rung_key, evidence_url, claimed_by, "
                   "claimed_at, conformance_pack_id) VALUES "
                   "('claim', 'branch', 'goal', 'rung', 'evidence', 'alice', 'then', 'pack')")
    inventory = {"owners": {"alice": 300001},
                 "tables": {(".tinyassets.db", "gate_claims"): [{"claim_id": "claim"}]},
                 "assignments": {(".tinyassets.db", "gate_claims"): [("owner", "alice")]},
                 "published_owner_copies": {}}
    migration._build_databases(data, tmp_path / "owners", tmp_path / "shared", inventory)
    from tinyassets.sqlite_schema import ensure_table

    with sqlite3.connect(tmp_path / "owners/300001/.tinyassets.db") as db:
        ensure_table(db, schema.TABLES["gate_claims"], kind="durable")
        assert db.execute("SELECT claim_id, conformance_pack_id FROM gate_claims").fetchall() == [
            ("claim", "pack")]


pytestmark = pytest.mark.verifies("owner-daemon-in-box:T2")
