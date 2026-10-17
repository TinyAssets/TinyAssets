"""Broker-owned vault publication never grants access to another uid."""
import os
import stat
from pathlib import Path

import pytest

from tinyassets.boxes import layout as role_modes
from tinyassets.broker import vault
from tinyassets.credential_vault import http_credential_record

# These pin the real role_modes numbers and owner labels; no double is installed.
pytestmark = [
    pytest.mark.role_split,
    pytest.mark.skipif(os.name != "posix", reason="POSIX role permissions"),
]


@pytest.fixture
def universe(tmp_path, monkeypatch):
    monkeypatch.setattr(role_modes, "BROKER_READ_GID", os.getgid())
    return tmp_path


def persist(universe, token):
    vault._persist_credential_vault_file(universe, [
        http_credential_record(destination="fixture", token=token)])


def test_modes_are_set_before_every_replacement(universe, monkeypatch):
    original = Path.replace
    publications = []

    def replace(source, destination):
        info = source.stat()
        assert (info.st_gid, stat.S_IMODE(info.st_mode)) == (os.getgid(), 0o600)
        publications.append(info.st_ino)
        return original(source, destination)

    monkeypatch.setattr(Path, "replace", replace)
    persist(universe, "first")
    persist(universe, "rotated")
    assert len(publications) == 2 and publications[0] != publications[1]
    assert vault.load_credential_vault(universe)[0]["token"] == "rotated"
    assert stat.S_IMODE(vault.credential_vault_path(universe).stat().st_mode) == 0o600


@pytest.mark.parametrize("failure", ["chmod", "fsync", "replace"])
def test_prepublication_failure_preserves_vault_and_cleans_temp(universe, monkeypatch, failure):
    persist(universe, "retained")
    path = vault.credential_vault_path(universe)
    before = path.read_bytes(), path.stat()

    def fail(*args):
        raise OSError("injected publication failure")

    monkeypatch.setattr(Path if failure == "replace" else os, failure, fail)
    with pytest.raises(OSError):
        persist(universe, "unpublished")
    assert path.read_bytes() == before[0]
    assert os.path.samestat(path.stat(), before[1])
    assert list(path.parent.iterdir()) == [path]


def test_postpublication_flush_failure_does_not_undo_commit(universe, monkeypatch):
    persist(universe, "previous")

    def fail(*args):
        raise OSError("injected postcommit flush failure")

    monkeypatch.setattr(vault, "_post_commit_durability", fail)
    persist(universe, "committed")
    assert vault.load_credential_vault(universe)[0]["token"] == "committed"


def test_publication_has_no_unsplit_mode(universe):
    """There is one publication path with broker-only read/write permissions."""
    persist(universe, "only")
    assert stat.S_IMODE(vault.credential_vault_path(universe).stat().st_mode) == 0o600
    assert not hasattr(vault, "_persist_unsplit_vault_file")
