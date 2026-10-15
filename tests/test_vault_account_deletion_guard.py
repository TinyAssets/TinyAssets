"""Deletion fences an admitted credential write without holding rename locks."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager

import pytest

from tinyassets import account_deletion as deletion
from tinyassets import credential_vault as vault
from tinyassets.daemon_server import (
    ensure_universe_registered,
    grant_universe_access,
    set_founder_home,
)
from tinyassets.provider_assignment import provider_assignment_admission
from tinyassets.storage import db_path
from tinyassets.storage.current_home import CurrentHomeChanged

OWNER = "user-vault-deletion"
HOME = "u-vault-deletion"
RECORD = {"credential_type": "http", "service": "model:openrouter",
          "token": "synthetic-not-a-real-key"}


@pytest.fixture
def home(tmp_path, monkeypatch):
    # Production deletes on the one data root; the owner box binds there.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    universe = tmp_path / HOME
    universe.mkdir()
    set_founder_home(tmp_path, founder_sub=OWNER, universe_id=HOME, platform_generated=True)
    ensure_universe_registered(tmp_path, universe_id=HOME, universe_path=universe)
    grant_universe_access(tmp_path, universe_id=HOME, actor_id=OWNER,
                          permission="admin", granted_by=OWNER)
    return universe


def write(home, owner=OWNER):
    return vault.write_credential_vault(home, [RECORD], owner_user_id=owner,
                                         universe_id=home.name)


def erase(home):
    return deletion.delete_account(home.parent, founder_sub=OWNER,
                                   cancel_billing=lambda _: "not_configured",
                                   delete_identity=lambda _: "not_configured")


def assert_no_credentials(home):
    assert not vault.credential_vault_path(home).exists()
    with sqlite3.connect(db_path(home.parent)) as conn:
        exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' "
                              "AND name='llm_credential_deposit_owners'").fetchone()
        if exists:
            assert conn.execute("SELECT * FROM llm_credential_deposit_owners "
                                "WHERE owner_user_id=?", (OWNER,)).fetchall() == []


def test_deletion_waits_for_inflight_vault_persist_then_erases(home, monkeypatch):
    entered, release, tombstone_entered = (threading.Event() for _ in range(3))
    original_persist = vault._persist_credential_vault_file
    original_tombstone = deletion.write_tombstone

    def blocked_persist(*args):
        entered.set()
        assert release.wait(10), "test did not release writer"
        return original_persist(*args)

    def observed_tombstone(*args):
        tombstone_entered.set()
        return original_tombstone(*args)

    monkeypatch.setattr(vault, "_persist_credential_vault_file", blocked_persist)
    monkeypatch.setattr(deletion, "write_tombstone", observed_tombstone)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(write, home)
        assert entered.wait(10)
        deleting = pool.submit(erase, home)
        try:
            assert not tombstone_entered.wait(0.5), "deletion overtook admitted vault write"
            assert not deleting.done()
        finally:
            release.set()
            writing.result(timeout=10)
            deleting.result(timeout=10)
    assert_no_credentials(home)


def test_delayed_write_after_deletion_refuses_without_key_or_owner_rows(home):
    erase(home)
    with pytest.raises(CurrentHomeChanged):
        write(home)
    assert_no_credentials(home)


def test_tombstone_is_locked_but_staging_is_not(home, monkeypatch):
    admission = provider_assignment_admission()
    original_exclusive = admission.exclusive
    original_tombstone, original_stage = deletion.write_tombstone, deletion._stage_home
    inside = []
    observed = []

    @contextmanager
    def tracked_exclusive(universe):
        with original_exclusive(universe):
            inside.append(universe)
            try:
                yield
            finally:
                inside.pop()

    def tombstone(*args):
        assert inside == [home], "tombstone lacks vault admission lock"
        observed.append("tombstone")
        return original_tombstone(*args)

    def stage(*args):
        assert not inside, "Windows rename must not hold the admission file open"
        observed.append("stage")
        return original_stage(*args)

    monkeypatch.setattr(admission, "exclusive", tracked_exclusive)
    monkeypatch.setattr(deletion, "write_tombstone", tombstone)
    monkeypatch.setattr(deletion, "_stage_home", stage)
    erase(home)
    assert observed == ["tombstone", "stage"]


def test_nondeleted_nonhome_admin_deposit_remains_supported(home):
    other = "user-nonhome-admin"
    grant_universe_access(home.parent, universe_id=HOME, actor_id=other,
                          permission="admin", granted_by=OWNER)
    write(home, owner=other)
    assert vault.load_credential_vault(home)[0]["token"] == RECORD["token"]


def test_legacy_vault_without_account_schema_remains_supported(tmp_path):
    universe = tmp_path / HOME
    write(universe)
    assert vault.load_credential_vault(universe)[0]["token"] == RECORD["token"]
    with sqlite3.connect(db_path(tmp_path)) as conn:
        assert conn.execute("SELECT 1 FROM sqlite_master "
                            "WHERE name='deleted_principals'").fetchone() is None


def test_malformed_tombstone_schema_cannot_allow_a_deposit(tmp_path):
    universe = tmp_path / HOME
    with sqlite3.connect(db_path(tmp_path)) as conn:
        conn.execute("CREATE TABLE deleted_principals (wrong_column TEXT)")
    with pytest.raises(sqlite3.OperationalError):
        write(universe)
    assert_no_credentials(universe)


def test_unreadable_tombstone_rows_cannot_allow_a_deposit(home, monkeypatch):
    original_connect = sqlite3.connect

    def guarded_connect(*args, **kwargs):
        conn = original_connect(*args, **kwargs)
        conn.set_authorizer(lambda action, table, *_:
                            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ
                            and table == "deleted_principals" else sqlite3.SQLITE_OK)
        return conn

    with monkeypatch.context() as scope:
        scope.setattr(vault.sqlite3, "connect", guarded_connect)
        with pytest.raises(sqlite3.DatabaseError):
            write(home)
    assert_no_credentials(home)
