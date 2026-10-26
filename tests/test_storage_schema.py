"""S10: schema ownership, durable layouts, and the one-shot row carry."""
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tinyassets import box_layout_migration as migration
from tinyassets.sqlite_schema import SchemaDrift
from tinyassets.storage import schema


def test_application_declarations_are_unique_and_in_census():
    source = Path(__file__).resolve().parents[1] / "tinyassets/daemon_server.py"
    assert "CREATE TABLE" not in source.read_text(encoding="utf-8")
    names = [name for store in schema.STORES for name in store.TABLES]
    assert len(names) == len(set(names)) == 26
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


@pytest.mark.parametrize("table", [*schema.TABLES, "deleted_principals"])
def test_cutover_carries_every_live_table_to_its_existing_home(tmp_path, table):
    data, owners, shared = (tmp_path / name for name in ("source", "owners", "shared"))
    data.mkdir()
    with sqlite3.connect(data / ".tinyassets.db") as db:
        if table == "deleted_principals":
            db.executescript((Path(__file__).parent / "fixtures/pre_s10_schema.sql").read_text())
        else:
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


@pytest.mark.parametrize("historical", [False, True])
def test_previous_release_opens_and_preserves_every_row(tmp_path, historical):
    # Frozen pre-PR CREATE statements, not the implementation under test.
    ddl = (Path(__file__).parent / "fixtures/pre_s10_schema.sql").read_text()
    if historical:
        ddl = ddl.replace("        skills_json TEXT NOT NULL DEFAULT '[]',\n", "")
        ddl = ddl.replace("        conformance_pack_id TEXT NOT NULL DEFAULT '',\n", "")
    with sqlite3.connect(tmp_path / ".tinyassets.db") as db:
        db.executescript(ddl)
        if historical:
            db.execute("ALTER TABLE branch_definitions ADD COLUMN skills_json "
                       "TEXT NOT NULL DEFAULT '[]'")
            db.execute("ALTER TABLE gate_claims ADD COLUMN conformance_pack_id "
                       "TEXT NOT NULL DEFAULT ''")
        # Pre-PR ADD COLUMN statements, frozen independently of schema.TABLES.
        # The fresh-release path and the accumulated release history differ.
        branch_columns = [
            "goal_id TEXT", "visibility TEXT NOT NULL DEFAULT 'public'",
            "default_llm_policy_json TEXT", "concurrency_budget INTEGER",
            "fork_from TEXT DEFAULT NULL",
        ]
        goal_columns = [
            "gate_ladder_json TEXT NOT NULL DEFAULT '[]'",
            "branch_protocol_json TEXT NOT NULL DEFAULT '[]'",
            "canonical_branch_version_id TEXT DEFAULT NULL",
            "canonical_branch_history_json TEXT NOT NULL DEFAULT '[]'",
            "auto_canonical_via_leaderboard INTEGER NOT NULL DEFAULT 0",
            "min_completed_runs_for_canonical INTEGER NOT NULL DEFAULT 5",
            "selector_branch_version_id TEXT DEFAULT NULL",
        ]
        if historical:
            branch_columns.insert(2, branch_columns.pop())
            goal_columns.append(goal_columns.pop(1))
        for table, columns in (("branch_definitions", branch_columns), ("goals", goal_columns)):
            for column in columns:
                db.execute(f"ALTER TABLE {table} ADD COLUMN {column}")
        snapshots = {}
        tables = [row[0] for row in db.execute(
            "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        for table in tables:
            columns = list(db.execute(f'PRAGMA table_info("{table}")'))
            values = {c[1]: (3 if c[1] in {"expires_at", "revoked_at"} else
                             1 if c[2] in {"INTEGER", "REAL"} else "retained:" + c[1])
                      for c in columns}
            # No canonical backfill before a real branch has been selected.
            if "canonical_branch_version_id" in values:
                values["canonical_branch_version_id"] = None
            names = ",".join('"' + name + '"' for name in values)
            db.execute(f'INSERT INTO "{table}" ({names}) VALUES (' +
                       ",".join("?" for _ in values) + ")", tuple(values.values()))
            snapshots[table] = (names, tuple(values.values()))
    for _ in range(2):
        schema._AUTHOR_SERVER_INITIALIZED.discard(str(tmp_path / ".tinyassets.db"))
        schema.initialize_author_server(tmp_path)
        with sqlite3.connect(tmp_path / ".tinyassets.db") as db:
            for table, (names, row) in snapshots.items():
                assert db.execute(f'SELECT {names} FROM "{table}"').fetchall() == [row]


def test_cutover_missing_required_column_refuses_before_publication(tmp_path):
    with sqlite3.connect(":memory:") as db:
        db.execute("CREATE TABLE universe_notes (note_id TEXT PRIMARY KEY)")
        with pytest.raises(RuntimeError, match="missing required columns"):
            migration._current_table_ddl(".tinyassets.db", "universe_notes", db,
                                         "CREATE TABLE universe_notes (note_id TEXT PRIMARY KEY)")


def test_s10_probe_follows_relocated_declarations():
    from scripts.check_shapes import ROOT, SHAPES, old_count

    assert old_count(SHAPES["S10"], ROOT) == 27


def test_owner_initialization_does_not_create_platform_tombstones(tmp_path):
    path = schema.initialize_author_server(tmp_path)
    with sqlite3.connect(path) as db:
        assert not db.execute(
            "SELECT 1 FROM sqlite_schema WHERE name='deleted_principals'").fetchall()
    source = Path(__file__).resolve().parents[1] / "tinyassets/account_deletion.py"
    assert "def write_tombstone" not in source.read_text()
    ingress = source.with_name("owner_ingress.py").read_text()
    assert "/_box/account/delete" not in ingress


def test_canonical_backfill_refuses_invalid_rows(tmp_path):
    # A corrupt legacy source must be reported, not silently skipped.
    with sqlite3.connect(tmp_path / ".tinyassets.db") as db:
        db.execute(schema.TABLES["goals"].replace("author      TEXT NOT NULL", "author      TEXT"))
        db.execute("INSERT INTO goals (goal_id, name, author, created_at, updated_at, "
                   "canonical_branch_version_id) VALUES ('goal', 'goal', NULL, 1, 1, 'branch')")
    with pytest.raises(sqlite3.IntegrityError, match="NOT NULL"):
        schema.initialize_author_server(tmp_path)
    with sqlite3.connect(tmp_path / ".tinyassets.db") as db:
        assert db.execute("SELECT goal_id, author FROM goals").fetchall() == [("goal", None)]


pytestmark = pytest.mark.verifies("owner-daemon-in-box:T2")
