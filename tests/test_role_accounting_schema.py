"""Privileged migration schema stays identical to the runtime's four tables."""
import runpy
import sqlite3
from pathlib import Path

import pytest

from tinyassets.storage.agent_request_usage import _SCHEMA

MIGRATION = runpy.run_path(str(Path(__file__).parents[1] / "deploy/role_migrate.py"))


def test_accounting_migration_schema_matches_runtime():
    with sqlite3.connect(":memory:") as runtime, sqlite3.connect(":memory:") as migration:
        for sql in _SCHEMA:
            runtime.execute(sql)
        for sql in (*MIGRATION["ACCOUNTING_SCHEMA"], MIGRATION["ACCOUNTING_INDEX"]):
            migration.execute(sql)
        assert MIGRATION["_accounting_facts"](runtime) == MIGRATION["_accounting_facts"](migration)
        query = "SELECT name,sql FROM sqlite_master WHERE type='index' ORDER BY name"
        assert runtime.execute(query).fetchall() == migration.execute(query).fetchall()


@pytest.mark.parametrize("change", ["column", "trigger", "view"])
def test_accounting_schema_drift_refuses_before_copy(change):
    with sqlite3.connect(":memory:") as conn:
        for sql in _SCHEMA:
            conn.execute(sql)
        if change == "column":
            conn.execute("ALTER TABLE agent_request_usage ADD COLUMN unexpected TEXT")
        elif change == "trigger":
            conn.execute("CREATE TRIGGER unexpected AFTER DELETE ON agent_request_usage "
                         "BEGIN DELETE FROM agent_request_attempts; END")
        else:
            conn.execute("DROP TABLE agent_request_usage")
            conn.execute("CREATE VIEW agent_request_usage AS SELECT * FROM agent_request_attempts")
        with pytest.raises(MIGRATION["MigrationRefused"]):
            MIGRATION["_accounting_facts"](conn)


@pytest.mark.parametrize("ddl", [
    "CREATE TABLE AGENT_REQUEST_USAGE (value TEXT)",
    "CREATE TABLE agent_request_attempt_day (value TEXT)",
    "CREATE VIEW AGENT_REQUEST_USAGE AS SELECT 1",
])
def test_accounting_reserved_name_collisions_refuse(ddl):
    with sqlite3.connect(":memory:") as conn:
        conn.execute(ddl)
        with pytest.raises(MIGRATION["MigrationRefused"]):
            MIGRATION["_accounting_facts"](conn)
