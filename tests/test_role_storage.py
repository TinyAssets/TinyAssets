"""Read-only owner storage counts preserve admission accounting semantics."""
import os
from pathlib import Path

import pytest

from tinyassets import role_storage as storage

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='descriptor walks need Linux')


def sample(root, scope='jail', **kwargs):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        return storage.scan(fd, scope, uid=os.getuid(), **kwargs)
    finally:
        os.close(fd)


def test_private_venv_scopes_and_no_follow(tmp_path):
    private = tmp_path / 'workspaces' / 'scratch' / 'lease' / '.venv'
    private.mkdir(parents=True, mode=0o700)
    (private / 'installed').write_bytes(b'a' * 19)
    permanent = tmp_path / 'workspaces' / 'published'
    permanent.mkdir()
    (permanent / 'file').write_bytes(b'b' * 7)
    (tmp_path / 'brain.md').write_bytes(b'c' * 3)
    (tmp_path / 'provider_definitions.json').write_bytes(b'd' * 101)
    (tmp_path / '.credentials').mkdir()
    (tmp_path / '.credentials' / 'secret').write_bytes(b'e' * 103)
    (private / 'outside').symlink_to('/etc')
    os.link(private / 'installed', private / 'alias')
    assert sample(tmp_path) == 29
    assert sample(tmp_path, 'universe') == 3
    assert sample(tmp_path, 'workspaces') == 7
    assert (private.stat().st_mode & 0o777) == 0o700


def test_incomplete_walk_refuses(tmp_path):
    (tmp_path / 'file').write_bytes(b'x')
    with pytest.raises(OSError, match='bound'):
        sample(tmp_path, max_entries=0)
    with pytest.raises(OSError, match='bound'):
        sample(tmp_path, timeout=0)
    with pytest.raises(ValueError):
        sample(tmp_path, '../other')


def test_foreign_entry_refuses(tmp_path, monkeypatch):
    (tmp_path / 'file').write_bytes(b'x')
    fd = os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(PermissionError, match='admitted owner'):
            storage.scan(fd, 'jail', uid=os.getuid() + 1)
    finally:
        os.close(fd)


def test_platform_walk_does_not_recurse_owner(tmp_path, monkeypatch):
    from tinyassets import storage_accounting as accounting

    (tmp_path / 'workspaces').mkdir()
    (tmp_path / 'workspaces' / 'private').write_bytes(b'x' * 99)
    (tmp_path / 'provider_definitions.json').write_bytes(b'p' * 5)
    real = os.scandir
    def guarded(path):
        assert Path(path).name != 'workspaces'
        return real(path)
    monkeypatch.setattr(os, 'scandir', guarded)
    assert accounting._walk_bytes(tmp_path, _exclude_owner=True) == 5


def test_configured_center_never_falls_back_on_unlabelled_root(tmp_path, monkeypatch):
    from tinyassets import storage_accounting as accounting

    center = tmp_path / 'center'
    center.mkdir()
    (center / 'file').write_bytes(b'private')
    monkeypatch.setenv('TINYASSETS_DATA_DIR', str(tmp_path))
    calls = []
    def refusing(root, scope):
        calls.append((root, scope))
        raise PermissionError('invalid canonical owner label')
    monkeypatch.setattr(storage, 'measure', refusing)
    with pytest.raises(PermissionError, match='canonical owner label'):
        accounting._center_bytes(center, scope='jail')
    assert calls == [(center, 'jail')]


def test_unreadable_owner_directory_is_not_skipped(tmp_path):
    if os.getuid() == 0:
        pytest.skip('requires the Linux oracle unprivileged identity')
    private = tmp_path / 'private'
    private.mkdir()
    (private / 'bytes').write_bytes(b'charged')
    private.chmod(0)
    try:
        with pytest.raises(PermissionError):
            sample(tmp_path)
        assert private.stat().st_mode & 0o777 == 0
    finally:
        private.chmod(0o700)
