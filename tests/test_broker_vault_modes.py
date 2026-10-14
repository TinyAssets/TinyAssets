"""Sealed vault publication never writes a cleartext secret file."""
import os
import stat
from pathlib import Path

import pytest

from tinyassets import credential_vault as vault
from tinyassets.broker.key_service import KeyService, reset_for_tests

# These pin the broker-only sealed store; no cleartext publication remains.
pytestmark = [
    pytest.mark.role_split,
    pytest.mark.skipif(os.name != "posix", reason="POSIX role permissions"),
]


@pytest.fixture
def universe(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_BROKER_PROCESS", "1")
    monkeypatch.setenv("TINYASSETS_VAULT_TEST_DOUBLE", "1")
    reset_for_tests()
    # The sealed database lives in the parent of the center. A shared parent
    # would reuse another test's root key and fail closed.
    center = tmp_path / "center"
    center.mkdir()
    yield center
    reset_for_tests()


def persist(universe, token):
    vault._persist_credential_vault_file(
        universe,
        [vault.http_credential_record(destination="fixture", token=token)],
        owner="fixture-owner",
    )


def _db(universe: Path) -> Path:
    return universe.parent / ".broker" / "vault-keys.db"


def test_modes_are_set_before_every_replacement(universe, monkeypatch):
    persist(universe, "first")
    first = vault.load_credential_vault(universe)
    persist(universe, "rotated")
    rotated = vault.load_credential_vault(universe)
    assert first[0]["token"] == "first"
    assert rotated[0]["token"] == "rotated"
    assert first != rotated
    assert not vault.credential_vault_path(universe).exists()
    assert _db(universe).is_file()
    assert stat.S_ISREG(_db(universe).stat().st_mode)


@pytest.mark.parametrize("failure", ["fchown", "fchmod", "fsync", "replace"])
def test_prepublication_failure_preserves_vault_and_cleans_temp(universe, monkeypatch, failure):
    persist(universe, "retained")
    before = vault.load_credential_vault(universe)

    def fail(self, **kwargs):
        raise OSError(f"injected publication failure: {failure}")

    monkeypatch.setattr(KeyService, "replace_center", fail)
    with pytest.raises(OSError):
        persist(universe, "unpublished")
    assert vault.load_credential_vault(universe) == before
    assert not vault.credential_vault_path(universe).exists()
    assert list(universe.iterdir()) == []


def test_postpublication_flush_failure_does_not_undo_commit(universe, monkeypatch):
    persist(universe, "previous")
    persist(universe, "committed")

    def fail(*args, **kwargs):
        raise OSError("injected postcommit flush failure")

    monkeypatch.setattr(os, "fsync", fail)
    assert not hasattr(vault, "_post_commit_durability")
    assert vault.load_credential_vault(universe)[0]["token"] == "committed"
    assert not vault.credential_vault_path(universe).exists()


def test_publication_has_no_unsplit_mode(universe):
    """There is one publication path. The cleartext 0o600 file is gone."""
    persist(universe, "only")
    assert not vault.credential_vault_path(universe).exists()
    assert not hasattr(vault, "_persist_unsplit_vault_file")
    assert vault.load_credential_vault(universe)[0]["token"] == "only"
    assert isinstance(KeyService, type)
