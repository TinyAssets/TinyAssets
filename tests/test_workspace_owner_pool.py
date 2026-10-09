"""Workspace parents use owner publication, never daemon-created substitutes."""
from __future__ import annotations

import sys

import pytest

from tinyassets import role_content, workspace_owner_pool

pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX owner publication")


def test_pool_delegates_exact_owner_and_names_without_creating_daemon_paths(tmp_path, monkeypatch):
    center = tmp_path / 'center'
    center.mkdir()
    calls = []
    def publish(actual, parts, *, machine):
        assert list(center.iterdir()) == []
        calls.append((actual, parts, machine))
    monkeypatch.setattr(role_content, 'ensure_directories', publish)
    result = workspace_owner_pool.prepare(center, ('workspaces', 'scratch'), machine=300001)
    assert result == center / 'workspaces/scratch'
    assert calls == [(center, ('workspaces', 'scratch'), 300001)]
    assert list(center.iterdir()) == []


def test_pool_refusal_is_not_replaced_with_a_daemon_owned_fallback(tmp_path, monkeypatch):
    def refuse(*args, **kwargs):
        raise PermissionError('foreign owner inode')
    monkeypatch.setattr(role_content, 'ensure_directories', refuse)
    with pytest.raises(PermissionError, match='foreign owner inode'):
        workspace_owner_pool.prepare(tmp_path, ('workspaces',), machine=300001)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize('parts', [(), ('',), ('..',), ('workspaces', 'a/b'),
                                  ('a\\b',), ('nul\0',)])
def test_invalid_pool_names_refuse_before_publication(tmp_path, parts, monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail('invalid pool reached publication')
    monkeypatch.setattr(role_content, 'ensure_directories', forbidden)
    with pytest.raises(ValueError):
        workspace_owner_pool.prepare(tmp_path, parts, machine=300001)


def test_the_pool_parts_follow_the_storage_class():
    assert workspace_owner_pool.pool_parts('scratch', 'ignored') == ('workspaces', 'scratch')
    assert workspace_owner_pool.pool_parts('universe', 'h--o--n') == ('workspaces', 'h--o--n')
    with pytest.raises(ValueError):
        workspace_owner_pool.pool_parts('elsewhere', 'h--o--n')
