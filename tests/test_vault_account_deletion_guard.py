"""Broker withdrawal precedes custody cleanup and fences delayed writes."""
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from tinyassets.broker import vault
from tinyassets.credential_vault import custody_metadata_path

OWNER = "user-vault-deletion"
HOME = "u-vault-deletion"
RECORD = {"credential_type": "http", "service": "model:example",
          "destination": "model:example", "token": "synthetic-not-a-real-key"}


@pytest.fixture(autouse=True)
def admitted(tmp_path):
    from tests.support.broker_double import seed_owner_admission
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.broker.owner_reads import Plans

    for owner, center in ((OWNER, HOME), ("another-owner", "another")):
        seed_owner_admission(tmp_path, principal=owner, center=center)
    return OwnerIdentities(tmp_path / ".broker/state/owner-identities.db", plans=Plans(tmp_path))



def write(home, owner=OWNER):
    return vault.write_credential_vault(home, [RECORD], owner_user_id=owner,
                                      universe_id=home.name)


def delete(root):
    from tinyassets.broker.admission import local_mutate, owner_lock
    path = root / ".broker/state/owner-identities.db"
    with owner_lock(path, OWNER):
        local_mutate(path, OWNER, "erase")
        vault.erase_owner(root, OWNER)


def assert_no_credentials(home):
    assert not vault.credential_vault_path(home).exists()
    with sqlite3.connect(custody_metadata_path(home.parent)) as conn:
        assert conn.execute("SELECT * FROM llm_credential_deposit_owners "
                            "WHERE owner_user_id=?", (OWNER,)).fetchall() == []


def test_deletion_waits_for_inflight_vault_persist_then_erases(tmp_path, monkeypatch):
    home = tmp_path / HOME
    entered, release = threading.Event(), threading.Event()
    original = vault._persist_credential_vault_file

    def blocked(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)

    monkeypatch.setattr(vault, "_persist_credential_vault_file", blocked)
    with ThreadPoolExecutor(max_workers=2) as pool:
        writing = pool.submit(write, home)
        assert entered.wait(10)
        deleting = pool.submit(delete, tmp_path)
        try:
            assert not deleting.done()
        finally:
            release.set()
            writing.result(timeout=10)
            deleting.result(timeout=10)
    assert_no_credentials(home)
    with pytest.raises(PermissionError, match="deleted"):
        write(home)  # The broker tombstone also fences a writer arriving after erasure.


def test_delayed_write_after_deletion_refuses_without_key_or_owner_rows(tmp_path):
    delete(tmp_path)
    with pytest.raises(PermissionError, match="deleted"):
        write(tmp_path / HOME)
    assert_no_credentials(tmp_path / HOME)


def test_no_daemon_database_or_admission_lock_is_needed(tmp_path):
    home = tmp_path / HOME
    write(home)
    assert not home.exists()
    assert not (tmp_path / ".tinyassets.db").exists()
    assert (vault.credential_vault_path(home).parent / ".custody.lock").is_file()


def test_deletion_preserves_another_owners_vault(tmp_path):
    write(tmp_path / HOME)
    write(tmp_path / "another", owner="another-owner")
    delete(tmp_path)
    assert_no_credentials(tmp_path / HOME)
    assert vault.load_credential_vault(tmp_path / "another")[0] == RECORD


def test_malformed_admission_schema_refuses_deposit(tmp_path, admitted):
    with sqlite3.connect(admitted.path) as conn:
        conn.execute("ALTER TABLE owner_admission RENAME COLUMN state TO wrong_column")
    with pytest.raises(sqlite3.OperationalError):
        write(tmp_path / HOME)
    assert_no_credentials(tmp_path / HOME)


def test_unreadable_admission_refuses_deposit(tmp_path, monkeypatch):
    original = sqlite3.connect

    def guarded(*args, **kwargs):
        conn = original(*args, **kwargs)
        conn.set_authorizer(lambda action, table, *_:
            sqlite3.SQLITE_DENY if action == sqlite3.SQLITE_READ
            and table == "owner_admission" else sqlite3.SQLITE_OK)
        return conn

    with monkeypatch.context() as scope:
        scope.setattr(vault.sqlite3, "connect", guarded)
        with pytest.raises(sqlite3.DatabaseError):
            write(tmp_path / HOME)
    assert_no_credentials(tmp_path / HOME)
