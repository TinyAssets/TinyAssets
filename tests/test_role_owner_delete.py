"""Deletion mutation boundary and authenticated two-pass admission fencing."""
import os
import runpy
from pathlib import Path

import pytest

from tinyassets import role_owner_delete, role_owner_delete_cell


def run(root, **kwargs):
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        return role_owner_delete_cell.remove_owned(
            fd, classify_daemon=kwargs.pop('classify_daemon', lambda fd: True), **kwargs)
    finally:
        os.close(fd)


def test_owned_restrictive_tree_and_links_removed_without_target_changes(tmp_path):
    center = tmp_path / 'center'
    center.mkdir()
    outside = tmp_path / 'outside'
    outside.write_bytes(b'never mutate this inode')
    before = outside.stat()
    tree = center / 'work'
    tree.mkdir()
    (tree / 'value').write_bytes(b'owner bytes')
    (tree / 'value').chmod(0)
    os.link(outside, tree / 'hardlink')
    os.symlink(outside, tree / 'symlink')
    os.mkfifo(tree / 'pipe')
    tree.chmod(0)
    answer = run(center)
    assert answer == dict(visited=5, removed=5, retained=0)
    assert list(center.iterdir()) == []
    assert outside.read_bytes() == b'never mutate this inode'
    after = outside.stat()
    assert (after.st_mode, after.st_uid, after.st_gid, after.st_mtime_ns) == (
        before.st_mode, before.st_uid, before.st_gid, before.st_mtime_ns)


@pytest.mark.parametrize('kind', ['file', 'directory', 'symlink'])
def test_foreign_entries_fail_loudly_without_mutation(tmp_path, monkeypatch, kind):
    target = tmp_path / 'foreign'
    if kind == 'directory':
        target.mkdir()
        (target / 'value').write_bytes(b'foreign')
    elif kind == 'symlink':
        os.symlink('/outside', target)
    else:
        target.write_bytes(b'foreign')
    original = os.fstat
    inode = target.lstat().st_ino

    def foreign(fd):
        info = original(fd)
        if info.st_ino == inode:
            fields = list(info)
            fields[4] = fields[5] = os.getuid() + 100
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, 'fstat', foreign)
    with pytest.raises(RuntimeError, match='foreign'):
        run(tmp_path, overflow_uid=os.getuid() + 200)
    assert target.lstat().st_ino == inode
    if kind != 'symlink':
        assert (target / 'value' if kind == 'directory' else target).read_bytes() == b'foreign'


def test_daemon_entries_retained_but_nested_owner_work_removed(tmp_path, monkeypatch):
    parent = tmp_path / 'daemon'
    parent.mkdir()
    marker = parent / 'daemon-file'
    marker.write_bytes(b'daemon')
    (parent / 'owner-work').write_bytes(b'owner')
    original = os.fstat
    daemon_inodes = {parent.stat().st_ino, marker.stat().st_ino}
    overflow = os.getuid() + 200

    def daemon(fd):
        info = original(fd)
        if info.st_ino in daemon_inodes:
            fields = list(info)
            fields[4] = fields[5] = overflow
            return os.stat_result(fields)
        return info

    monkeypatch.setattr(os, 'fstat', daemon)
    assert run(tmp_path, overflow_uid=overflow) == dict(visited=3, removed=1, retained=2)
    assert marker.read_bytes() == b'daemon'
    assert list(parent.iterdir()) == [marker]


def test_overflow_is_not_daemon_identity_evidence(tmp_path, monkeypatch):
    target = tmp_path / 'foreign'
    target.write_bytes(b'foreign')
    original = os.fstat
    inode = target.stat().st_ino
    overflow = os.getuid() + 200
    def other(fd):
        info = original(fd)
        if info.st_ino == inode:
            fields = list(info)
            fields[4] = fields[5] = overflow
            return os.stat_result(fields)
        return info
    monkeypatch.setattr(os, 'fstat', other)
    with pytest.raises(RuntimeError, match='foreign'):
        run(tmp_path, overflow_uid=overflow, classify_daemon=lambda fd: False)
    assert target.read_bytes() == b'foreign'


def mapper():
    module = runpy.run_path(str(Path(__file__).resolve().parents[1]
                               / 'deploy/role_owner_launcher.py'))
    value = object.__new__(module['OwnerLauncher'])
    value.bindings = {('alice', 'alice'): 300001, ('alice', 'second'): 300001,
                      ('bob', 'bob'): 300002}
    value.jobs, value.delete_fences = {}, {}
    value.channel = type('Channel', (), {'sendall': lambda self, value: None})()
    return value


def test_fence_requires_quiescence_exact_scope_token_and_explicit_finish():
    value = mapper()
    request = dict(kind='owner-delete', principal='alice', command_center='alice',
                   delete_token='a' * 32)
    value.jobs[7] = (1, 300001, 0, None)
    with pytest.raises(ValueError, match='quiescent'):
        value._check_delete_fence(300001, request)
    value.jobs.clear()
    value._check_delete_fence(300001, request)
    value.delete_fences[300001] = ('alice', 'alice', 'a' * 32)
    value._check_delete_fence(300001, request)  # resumable exact retry
    value._check_delete_fence(300002, {'kind': 'tool-jail'})
    for other in (dict(request, delete_token='b' * 32),
                  dict(request, command_center='second'), {'kind': 'tool-jail'}):
        with pytest.raises(ValueError):
            value._check_delete_fence(300001, other)
    finish = dict(op='DELETE_DONE', principal='alice', command_center='alice',
                  delete_token='a' * 32)
    for bad in (dict(finish, delete_token='b' * 32), dict(finish, principal='bob',
                                                       command_center='bob')):
        with pytest.raises(ValueError):
            value._finish_delete(bad, [])
    assert value.delete_fences
    value._finish_delete(finish, [])
    assert not value.delete_fences
    with pytest.raises(ValueError):
        value._finish_delete(finish, [])  # no stale successful finish


def test_delete_schema_refuses_paths_programs_numeric_ids_and_blocking_spawn():
    value = mapper()
    request = dict(op='START', kind='owner-delete', principal='alice',
                   command_center='alice', delete_token='a' * 32)
    for override in ({'path': '../bob'}, {'uid': 300002}, {'argv': ['/bin/sh']},
                     {'op': 'SPAWN'}, {'delete_token': '../escape'}, {'ta': True}):
        with pytest.raises(ValueError, match='unsupported'):
            value._decoder(request | override, [])


def test_no_unconfined_deletion_fallback(tmp_path, monkeypatch):
    from tinyassets import role_decoder

    monkeypatch.setattr(role_decoder, '_bounded_client', None)
    with pytest.raises(PermissionError, match='bounded launcher'):
        role_owner_delete.begin(tmp_path, token='a' * 32)


def test_finish_scope_survives_removed_center_and_identity_record(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from tinyassets import role_decoder, storage
    from tinyassets.auth import middleware
    from tinyassets.broker import owner_identities, supervisor

    client = object()
    monkeypatch.setattr(role_decoder, '_bounded_client', client)
    monkeypatch.setattr(storage, 'data_dir', lambda: tmp_path)
    monkeypatch.setattr(middleware, 'current_identity', lambda: SimpleNamespace(user_id='alice'))
    monkeypatch.setattr(supervisor, 'broker_selected', lambda: True)
    monkeypatch.setattr(supervisor, '_protect_daemon', lambda: None)
    def removed(*args, **kwargs):
        raise AssertionError('finished deletion must not resolve an erased identity')
    monkeypatch.setattr(owner_identities, 'owner_identity', removed)
    assert role_owner_delete._scope(tmp_path / 'deleted', 'a' * 32, finishing=True) == (
        client, 'alice', tmp_path / 'deleted', None)
