"""Protected request cutover: loss, legacy writers, restart and owner isolation."""

import sqlite3
from contextlib import closing

import pytest

from tinyassets import agent_activities
from tinyassets.owner_control import ControlUnavailable, control
from tinyassets.storage import pending_requests as requests
from tinyassets.storage import request_migration as migration


def test_cutover_preserves_all_tables_and_blocks_legacy_writers(tmp_path):
    home = tmp_path / "owner"
    home.mkdir()
    with closing(sqlite3.connect(home / requests._DB_NAME)) as old:
        old.executescript(requests._SCHEMA)
        requests._ensure_columns(old)
        old.execute("INSERT INTO request_unmutes VALUES ('keep',1)")
        old.execute("INSERT INTO request_item_answers VALUES ('r','i','answered','{}','note',2)")
        old.execute("INSERT INTO request_suppressions VALUES ('d','k','t','f','declined','{}',3)")
        old.commit()
    target = migration.ensure_protected(home)
    assert target == agent_activities.store_path(home)
    with (
        closing(sqlite3.connect(target)) as new,
        closing(sqlite3.connect(home / requests._DB_NAME)) as old,
    ):
        for table in migration.TABLES:
            assert (
                new.execute(f"SELECT * FROM {table}").fetchall()
                == old.execute(f"SELECT * FROM {table}").fetchall()
            )
        with pytest.raises(sqlite3.IntegrityError, match="migration_unavailable"):
            old.execute("INSERT INTO request_unmutes VALUES ('late',4)")
        new.execute("INSERT INTO request_unmutes VALUES ('new',5)")
        new.commit()
    migration.ensure_protected(home)
    assert len(requests.list_unmutes(home)) == 2


def test_failed_copy_stays_paused_and_recovers_without_loss(tmp_path, monkeypatch):
    home = tmp_path / "owner"
    home.mkdir()
    original = migration._rows
    monkeypatch.setattr(
        migration, "_rows", lambda *a: (_ for _ in ()).throw(sqlite3.OperationalError("crash"))
    )
    with pytest.raises(migration.MigrationUnavailable):
        migration.ensure_protected(home)
    with closing(sqlite3.connect(agent_activities.store_path(home))) as conn:
        assert migration._marker(conn) == "paused"
    monkeypatch.setattr(migration, "_rows", original)
    with pytest.raises(migration.MigrationUnavailable):
        migration.ensure_protected(home)
    migration.ensure_protected(home, recover=True)
    with closing(sqlite3.connect(agent_activities.store_path(home))) as conn:
        assert migration._marker(conn) == "complete"


def test_failure_before_copy_also_leaves_a_durable_pause(tmp_path):
    home = tmp_path / "owner"
    home.mkdir()
    (home / requests._DB_NAME).write_bytes(b"invalid legacy SQLite database")
    with pytest.raises(migration.MigrationUnavailable):
        migration.ensure_protected(home)
    with closing(sqlite3.connect(agent_activities.store_path(home))) as conn:
        assert migration._marker(conn) == "paused"


def test_lock_excludes_another_thread_and_owner_stores_are_disjoint(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    homes = [tmp_path / name for name in ("alice", "bob")]
    for home in homes:
        home.mkdir()
    with control(homes[0]), ThreadPoolExecutor() as pool:
        with pytest.raises(ControlUnavailable):
            pool.submit(migration.ensure_protected, homes[0]).result()
        pool.submit(migration.ensure_protected, homes[1]).result()
    row = requests.create_request(
        homes[0],
        kind="question",
        title="Private",
        body="",
        fields=[],
        action={},
        dedupe_key="private",
    )
    assert row is not None
    assert requests.get_request(homes[1], row["request_id"]) is None


def test_paused_api_ask_is_retryable_and_creates_no_partial_request(tmp_path, monkeypatch):
    from tinyassets.api import pending_requests as api

    home = tmp_path / "owner"
    home.mkdir()
    with closing(agent_activities._connect(home, create=True)) as conn:
        migration._marker(conn)
        conn.execute("INSERT INTO request_storage_migration VALUES (1,'paused')")
    monkeypatch.setattr(api, "_owner_gate", lambda _: (home.name, home, None))
    result = api.request_from_user(
        universe_id=home.name,
        payload={"kind": "question", "title": "No partial request", "fields": []},
    )
    assert result["error"] == "request_migration_unavailable"
    assert result["retryable"] is True
    with closing(sqlite3.connect(agent_activities.store_path(home))) as conn:
        assert not conn.execute(
            "SELECT 1 FROM sqlite_master WHERE name='pending_requests'"
        ).fetchone()


def test_owner_control_excludes_another_process_and_releases_on_exit(tmp_path):
    import subprocess
    import sys

    home = tmp_path / "owner"
    home.mkdir()
    script = """
import sys
from pathlib import Path
from tinyassets.owner_control import control, ControlUnavailable
try:
    with control(Path(sys.argv[1])):
        print('acquired')
except ControlUnavailable:
    print('retryable')
"""

    def attempt():
        return subprocess.run(
            [sys.executable, "-c", script, str(home)],
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        ).stdout.strip()

    with control(home):
        assert attempt() == "retryable"
    assert attempt() == "acquired"
