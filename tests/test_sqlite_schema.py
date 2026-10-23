"""Declared table layouts recover disposable state and preserve durable state."""
import sqlite3
from contextlib import closing

import pytest


@pytest.mark.parametrize("kind", ["ephemeral", "durable"])
@pytest.mark.parametrize("layout", ["old TEXT", "value INTEGER, id TEXT",
                                    "id TEXT, value TEXT"])
def test_schema_drift(tmp_path, kind, layout):
    from tinyassets.sqlite_schema import SchemaDrift, ensure_table

    path = tmp_path / "store.db"
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(f"CREATE TABLE records ({layout})")
        conn.execute("INSERT INTO records (" + layout.split()[0] + ") VALUES ('saved')")
        conn.commit()
    with closing(sqlite3.connect(path)) as conn:
        declaration = "CREATE TABLE IF NOT EXISTS records (id TEXT, value INTEGER)"
        if kind == "durable":
            with pytest.raises(SchemaDrift, match="records"):
                ensure_table(conn, declaration, kind=kind)
            assert conn.execute("SELECT * FROM records").fetchone()[0] == "saved"
        else:
            ensure_table(conn, declaration, kind=kind)
            assert conn.execute("SELECT * FROM records").fetchall() == []
            conn.execute("INSERT INTO records VALUES ('new', 7)")
            ensure_table(conn, declaration, kind=kind)
            assert conn.execute("SELECT * FROM records").fetchall() == [("new", 7)]


@pytest.mark.parametrize("table", ["hosted_model_flows", "connection_oauth_flows",
                                    "oauth_public_clients", "inline_model_flows",
                                    "owner_sessions", "owner_login_flows",
                                    "owner_login_completions", "approval_handoffs"])
def test_flow_store_recovers_old_table(tmp_path, monkeypatch, table):
    from tinyassets.connection_oauth.pkce import _registration_table, flows_db
    from tinyassets.onboarding import approval_handoff, inline_model_connect

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    oauth = table in {"hosted_model_flows", "connection_oauth_flows", "oauth_public_clients"}
    path = tmp_path / (".hosted-model-auth.db" if oauth else ".runtime/owner-sessions.db")
    path.parent.mkdir(exist_ok=True)
    with closing(sqlite3.connect(path)) as conn:
        conn.execute(f"CREATE TABLE {table} (legacy TEXT)")
        conn.execute(f"INSERT INTO {table} VALUES ('obsolete')")
        conn.commit()
    if oauth:
        with flows_db(tmp_path) as (conn, _):
            _registration_table(conn)
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == []
    else:
        flows = (approval_handoff.flows if table == "approval_handoffs"
                 else inline_model_connect.flows)
        with flows() as conn:
            assert conn.execute(f"SELECT * FROM {table}").fetchall() == []


def test_durable_store_refuses_old_table(tmp_path):
    from tinyassets.storage import DB_FILENAME, owner_ui_prefs

    with closing(sqlite3.connect(tmp_path / DB_FILENAME)) as conn:
        conn.execute("CREATE TABLE owner_ui_prefs (legacy TEXT)")
        conn.execute("INSERT INTO owner_ui_prefs VALUES ('keep me')")
        conn.commit()
    with pytest.raises(RuntimeError, match="owner_ui_prefs"):
        owner_ui_prefs._connect(tmp_path, create=True)
    with closing(sqlite3.connect(tmp_path / DB_FILENAME)) as conn:
        assert conn.execute("SELECT * FROM owner_ui_prefs").fetchall() == [("keep me",)]


def test_rebuild_respects_outer_transaction():
    from tinyassets.sqlite_schema import ensure_table

    with closing(sqlite3.connect(":memory:")) as conn:
        conn.execute('CREATE TABLE "flow cache" (old TEXT)')
        conn.execute('INSERT INTO "flow cache" VALUES (\'keep until commit\')')
        conn.commit()
        conn.execute("BEGIN IMMEDIATE")
        ensure_table(conn, 'CREATE TABLE IF NOT EXISTS "flow cache" (new INTEGER)',
                     kind="ephemeral")
        conn.rollback()
        assert conn.execute('SELECT old FROM "flow cache"').fetchone() == ("keep until commit",)


def test_rollback_failure_preserves_original_error():
    from tinyassets.sqlite_schema import ensure_table

    original = sqlite3.OperationalError("original create failure")

    class RolledBackConnection(sqlite3.Connection):
        def execute(self, sql, *args):
            if sql.startswith("CREATE TABLE"):
                self.rollback()
                raise original
            return super().execute(sql, *args)

    with closing(sqlite3.connect(":memory:", factory=RolledBackConnection)) as conn:
        with pytest.raises(sqlite3.OperationalError) as caught:
            ensure_table(conn, "CREATE TABLE records (value TEXT)", kind="ephemeral")
        assert caught.value is original
