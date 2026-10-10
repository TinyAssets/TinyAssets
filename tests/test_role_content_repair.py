"""Online inode recovery: run as root with scripts/linux_oracle.py --as-root.

Mutation table: path-following changes an outsider; overwrite loses an open
writer; broad chown captures foreign custody; unconditional label repair changes
owner permissions on repeat; fail-to-read-WAL omits admitted owners.
"""
import json
import os
import runpy
import sqlite3
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(os.name != 'posix' or getattr(os, 'geteuid', lambda: -1)() != 0,
                              reason='owner=owner-content runs-in=linux_oracle.py --as-root: '
                                     'real inode custody')
M = runpy.run_path(str(Path(__file__).resolve().parents[1] / 'deploy/role_migrate.py'))


@pytest.fixture
def center(tmp_path):
    root = tmp_path / 'data'
    root.mkdir()
    (root / '.layout.json').write_text(json.dumps(
        {'layout': 2, 'state': 'stable', 'split': 'owner-split'}))
    state = root / '.broker/state'
    state.mkdir(parents=True)
    db = sqlite3.connect(state / 'owner-identities.db')
    db.executescript('''PRAGMA journal_mode=WAL;
        CREATE TABLE owner_identities(principal TEXT, machine_id INTEGER);
        CREATE TABLE center_admissions(generation INTEGER, event TEXT, principal TEXT, center TEXT);
        INSERT INTO owner_identities VALUES ('owner',300001);
        INSERT INTO center_admissions VALUES (1,'admit','owner','u-a');''')
    os.chown(state / 'owner-identities.db', 1002, 1101)
    path = root / 'u-a'
    path.mkdir()
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        M['_apply'](fd, (1001, 300001, 0o750, M['canonical_root_acl'](300001), None))
    finally:
        os.close(fd)
    yield path
    db.close()


def damage(path, *, directory=False):
    path.mkdir() if directory else path.write_bytes(b'keep\x00verbatim')
    os.chown(path, 1001, 1001)
    return path


def repair(center, apply=True):
    return M['repair_content_online'](center.parent, snapshot='verified-backup', apply=apply)


def test_online_repair_preserves_paths_inodes_open_writes_and_is_noop_on_repeat(center):
    history = damage(center / 'soul_versions', directory=True)
    leaf = damage(history / '0001.md')
    damage(history / '.marker')
    empty = damage(center / 'wiki', directory=True)
    platform = damage(center / '.platform')
    before = {p.relative_to(center): (p.stat().st_ino, p.read_bytes() if p.is_file() else None)
              for p in center.rglob('*')}
    with leaf.open('ab', buffering=0) as writer:
        assert repair(center, False)['changed'] == 4
        assert leaf.stat().st_uid == 1001
        assert repair(center)['changed'] == 4
        writer.write(b' appended through original descriptor')
    assert set(before) == {p.relative_to(center) for p in center.rglob('*')}
    for relative, (inode, data) in before.items():
        path = center / relative
        assert path.stat().st_ino == inode
        if path.is_file():
            assert path.read_bytes() == data + (b' appended through original descriptor'
                                                if path == leaf else b'')
    assert empty.stat().st_uid == leaf.stat().st_gid == 300001
    assert platform.stat().st_uid == 1001
    # Full stat includes ctime: a second call must perform zero label writes.
    labels = {p: p.stat() for p in center.rglob('*')}
    assert repair(center)['changed'] == 0
    assert labels == {p: p.stat() for p in center.rglob('*')}


@pytest.mark.parametrize('kind', ['symlink', 'hardlink', 'foreign', 'fifo', 'foreign-parent'])
def test_repair_refuses_unsafe_candidates_without_touching_outsider(center, kind):
    outside = damage(center.parent / 'outside')
    candidate = center / 'candidate'
    if kind == 'symlink':
        candidate.symlink_to(outside)
        os.chown(candidate, 1001, 1001, follow_symlinks=False)
    elif kind == 'hardlink':
        os.link(outside, candidate)
    elif kind == 'fifo':
        os.mkfifo(candidate)
    elif kind == 'foreign-parent':
        damage(candidate, directory=True)
        damage(candidate / 'child')
        os.chown(candidate, 300002, 300002)
    else:
        damage(candidate)
        os.chown(candidate, 300002, 300002)
    before = outside.stat()
    with pytest.raises(M['MigrationRefused']):
        repair(center)
    assert outside.stat() == before
    assert outside.read_bytes() == b'keep\x00verbatim'


def test_interrupted_repair_converges_and_preserves_owner_custom_labels(center, monkeypatch):
    a = damage(center / 'a')
    b = damage(center / 'b')
    original = M['_apply']

    def interrupted(fd, wanted):
        original(fd, wanted)
        raise InterruptedError('operator disconnected')

    namespace = M['_repair_content_tree'].__globals__
    monkeypatch.setitem(namespace, '_apply', interrupted)
    with pytest.raises(InterruptedError):
        repair(center)
    monkeypatch.setitem(namespace, '_apply', original)
    assert repair(center)['changed'] == 1
    a.chmod(0o600)
    before = a.stat()
    assert repair(center)['changed'] == 0
    assert a.stat() == before
    assert a.read_bytes() == b.read_bytes() == b'keep\x00verbatim'
