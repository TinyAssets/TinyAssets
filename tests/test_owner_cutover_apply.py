"""Synthetic real-file cutover and crash resume; no external data fixtures."""

import json
import os
import sqlite3
import subprocess
import sys
import tarfile
from contextlib import closing

import pytest

from tests.test_box_layout_migration import _archive
from tests.test_owner_cutover_census import _fingerprint, _sql
from tests.test_owner_cutover_census import source as source
from tinyassets import box_layout_migration as migration


@pytest.fixture
def cutover(source, tmp_path, monkeypatch):
    assert os.geteuid() == 0, "run cutover inode proof through linux_oracle --as-root"
    for center in ('a1', 'a2', 'b1', 'b2'):
        os.chown(source / center, 1001, 300001 if center[0] == 'a' else 300002)
    destination = tmp_path / 'volume/_data'
    destination.parent.mkdir()
    source.rename(destination)
    source = destination
    store = tmp_path / 'owners'
    store.mkdir()
    # XFS mount/quota only: SQLite, rsync, permissions, journal and publication
    # are the real implementations. Real-mount proof belongs to the lead.
    monkeypatch.setattr(migration, '_mounted', lambda *_: None)
    monkeypatch.setattr(migration, '_xfs_quota', lambda *_: 'synthetic quota venue')
    monkeypatch.setattr(migration, 'verify_tree', lambda *_: None)
    monkeypatch.setattr(migration, 'verify_owner', lambda *_: None)
    data_backup = tmp_path / 'tinyassets-snapshot-2026-10-17T00-00-00Z.tar.gz'
    box_backup = tmp_path / 'tinyassets-box-store-2026-10-17T00-00-00Z.tar.gz'
    for path, tree, name in ((data_backup, source, '_data'), (box_backup, store, 'owners')):
        with tarfile.open(path, 'w:gz') as archive:
            archive.add(tree, arcname=name)
        _archive(path, path.read_bytes())
    return source, store, data_backup, box_backup


@pytest.mark.parametrize('crash', ['none', 'copy', 'publish', 'cleanup'])
def test_apply_splits_databases_seeds_stores_and_resumes(cutover, monkeypatch, crash):
    data, store, *_ = cutover
    _sql(data / '.owner_leases.db', """
        CREATE TABLE owner_lease (fence INTEGER);
        INSERT INTO owner_lease VALUES (93);
    """)
    _sql(data / '.tinyassets.db', """
        CREATE TABLE universes (id TEXT);
        INSERT INTO universes VALUES ('a1'), ('never-admitted');
        CREATE TABLE external_agent_job_receipts (owner_user_id TEXT);
        INSERT INTO external_agent_job_receipts VALUES ('alice');
        CREATE TABLE author_definitions (id TEXT);
        INSERT INTO author_definitions VALUES ('public-definition');
        CREATE TABLE user_accounts (id TEXT);
        INSERT INTO user_accounts VALUES ('router-identity');
    """)
    (data / 'never-admitted').mkdir()
    (data / 'never-admitted/old').write_text('synthetic discarded file')
    _sql(data / '.runs.db', """
        CREATE TABLE runs (run_id TEXT, owner_user_id TEXT, queue_universe_id TEXT, thread_id TEXT);
        INSERT INTO runs VALUES ('run-a','alice','a1','thread-a'),
            ('run-b','bob','b1','thread-b'), ('legacy',NULL,NULL,'thread-old'),
            ('unadmitted',NULL,'never-admitted','thread-unadmitted');
        CREATE TABLE run_events (run_id TEXT, event TEXT);
        INSERT INTO run_events VALUES ('run-a','history-a'), ('run-b','history-b'),
                                      ('legacy','old'), ('unadmitted','old');
    """)
    _sql(data / '.langgraph_runs.db', """
        CREATE TABLE checkpoints (thread_id TEXT, payload TEXT);
        INSERT INTO checkpoints VALUES ('thread-a','a'), ('thread-b','b'), ('thread-old','old');
    """)
    _sql(data / '.runtime/owner-sessions.db', """
        CREATE TABLE approval_handoffs (id TEXT);
        INSERT INTO approval_handoffs VALUES ('router-handoff');
    """)
    (data / 'runs').mkdir()
    (data / 'runs/run-a.jsonl').write_text('owned transcript')
    (data / 'runs/legacy.jsonl').write_text('dropped transcript')
    home = data / '.platform/owners/300001/agent-home'
    home.mkdir(parents=True)
    (home / 'session').write_text('retired cli home')
    before = migration.census(data)
    assert before['ready'], before['refusals']
    if crash != 'none':
        name = {'copy': '_copy', 'publish': '_publish_cutover', 'cleanup': '_remove_tree'}[crash]
        original = getattr(migration, name)
        fired = False

        def interrupt(*args, **kwargs):
            nonlocal fired
            result = original(*args, **kwargs)
            if not fired and (crash != 'cleanup' or args[0] == data / 'a1'):
                fired = True
                raise OSError('synthetic interruption')
            return result

        monkeypatch.setattr(migration, name, interrupt)
        with pytest.raises(OSError, match='synthetic interruption'):
            migration.apply(*cutover)
        assert fired
        monkeypatch.setattr(migration, name, original)
    migration.apply(*cutover)
    marker = json.loads((data / '.layout.json').read_text())
    assert marker['owner_daemon'] == 1
    assert marker['state'] == 'stable'
    assert marker['box_layout'] == 'owner-box'
    for owner, uid in [('alice', 300001), ('bob', 300002)]:
        root = store / str(uid)
        assert root.stat().st_uid == uid
        with closing(sqlite3.connect(root / '.tinyassets.db')) as db:
            assert db.execute('SELECT DISTINCT owner_user_id FROM agent_turns').fetchall() == [
                (owner,)]
            assert db.execute('SELECT COUNT(*) FROM agent_turns').fetchone()[0] == 2
            assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
        with closing(sqlite3.connect(root / '.owner_leases.db')) as db:
            assert db.execute('SELECT COUNT(*) FROM owner_lease').fetchone()[0] == 0
        with closing(sqlite3.connect(root / '.runs.db')) as db:
            assert db.execute('SELECT owner_user_id FROM runs').fetchall() == [(owner,)]
            assert db.execute('SELECT COUNT(*) FROM run_events').fetchone()[0] == 1
        with closing(sqlite3.connect(root / '.langgraph_runs.db')) as db:
            assert db.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0] == 1
        assert len(list(root.glob('*/notes.txt'))) == 2
        assert not (root / '.agent-home').exists()
        assert all(b'secret-must-not-appear' not in p.read_bytes()
                   for p in root.rglob('*') if p.is_file())
    for center in ('a1', 'a2', 'b1', 'b2', 'never-admitted'):
        assert not (data / center).exists()
    with closing(sqlite3.connect(data / '.tinyassets.db')) as db:
        assert db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall() == [
            ('user_accounts',)]
        assert db.execute('SELECT id FROM user_accounts').fetchall() == [('router-identity',)]
    assert (store / '300001/runs/run-a.jsonl').read_text() == 'owned transcript'
    assert not (data / 'runs').exists()
    assert not home.exists()
    assert before['tables']['.runs.db/runs']['dropped'] == 2
    assert before['tables']['.langgraph_runs.db/checkpoints']['dropped'] == 1
    from tinyassets.broker.owner_reads import Plans

    assert Plans(data).read('alice')['tier'] == 'paid'
    assert Plans(data).read('bob')['tier'] == 'free'
    with closing(sqlite3.connect(data / '.hook-owners.sqlite3')) as db:
        assert db.execute('SELECT * FROM hook_owners ORDER BY owner').fetchall() == [
            ('hash-a', 'alice'), ('hash-b', 'bob')]
    with closing(sqlite3.connect(data / '.published/.tinyassets.db')) as db:
        assert db.execute('SELECT id FROM author_definitions').fetchall() == [
            ('public-definition',)]
    for path in (data / '.platform/credential-vaults').rglob('.credential-vault.json'):
        assert path.stat().st_uid == 1002
        records = json.loads(path.read_text())['credentials']
        assert [r['credential_type'] for r in records] == ['http']
    after = _fingerprint(data), _fingerprint(store)
    migration.apply(*cutover)
    assert (_fingerprint(data), _fingerprint(store)) == after


def test_refused_census_makes_no_cutover_writes(cutover):
    data, store, *_ = cutover
    _sql(data / 'orphan/.subscription_state.db', """
        CREATE TABLE subscription_meta (key TEXT, value TEXT);
        INSERT INTO subscription_meta VALUES ('tier','paid');
    """)
    before = _fingerprint(data), _fingerprint(store)
    with pytest.raises(RuntimeError, match='census refused'):
        migration.apply(*cutover)
    assert (_fingerprint(data), _fingerprint(store)) == before


def test_completed_apply_restores_paired_owner_split(cutover, tmp_path):
    from tests.test_owner_cutover_restore import REPO, _shim

    data, store, data_archive, box_archive = cutover
    before = _fingerprint(data)
    migration.apply(*cutover)
    assert not (data / 'a1').exists()
    binary = tmp_path / 'bin'
    binary.mkdir()
    _shim(binary, 'docker', 'case "$1 $2" in\n"volume inspect") echo "$TEST_VOLUME" ;;\n'
          '"ps -q") : ;;\n*) exit 79 ;;\nesac\n')
    _shim(binary, 'systemctl', ':\n')
    _shim(binary, 'findmnt', 'if [[ "${@: -1}" == FSTYPE ]]; then echo xfs; '
          'else echo rw,prjquota,nodiscard; fi\n')
    _shim(binary, 'xfs_quota', 'exit 79\n')
    # Only the absent host runtime is modeled: the real restore census still
    # checks all receipt identities, tables and modes before asking for its list.
    wrapper = binary / 'python3'
    wrapper.write_text(f"#!{sys.executable}\n" + """import os, runpy, subprocess, sys
if len(sys.argv) > 1 and sys.argv[1].endswith('/box-store-quotas.py'):
    real_run = subprocess.run
    def run(argv, **kwargs):
        if argv[0] == '/opt/tinyassets/boxhostd/current/runsc':
            assert argv[-3:] == ['list', '--format=json'] or argv[-2:] == ['list', '--format=json']
            return subprocess.CompletedProcess(argv, 0, '[]', '')
        return real_run(argv, **kwargs)
    subprocess.run = run
    sys.argv = sys.argv[1:]
    runpy.run_path(sys.argv[0], run_name='__main__')
else:
    os.execv(sys.executable, [sys.executable, *sys.argv[1:]])
""")
    wrapper.chmod(0o755)
    result = subprocess.run(
        ['bash', str(REPO / 'deploy/backup-restore.sh')],
        env={**os.environ, 'PATH': f"{binary}:{os.environ['PATH']}",
             'BACKUP_FILE': str(data_archive), 'BOX_BACKUP_FILE': str(box_archive),
             'BACKUP_VOLUME': 'synthetic-cutover', 'BOX_STORE_DIR': str(store),
             'BACKUP_LOG': str(tmp_path / 'restore.log'), 'TEST_VOLUME': str(data)},
        text=True, capture_output=True, check=False)
    assert result.returncode == 0, result.stdout + result.stderr
    assert _fingerprint(data) == before
    assert json.loads((data / '.layout.json').read_text())['split'] == 'owner-split'
    assert not (store / '300001').exists()
    assert not (store / '300002').exists()
    assert list(store.glob('.tinyassets-box-restore-old.*/300001/a1/notes.txt'))


def test_crash_resume_rejects_a_different_snapshot_pair(cutover, monkeypatch):
    original = migration._copy
    def crash(*args):
        original(*args)
        raise OSError('interrupted copy')
    monkeypatch.setattr(migration, '_copy', crash)
    with pytest.raises(OSError, match='interrupted copy'):
        migration.apply(*cutover)
    _archive(cutover[2], b'a different synthetic snapshot')
    monkeypatch.setattr(migration, '_copy', original)
    with pytest.raises(RuntimeError, match='resume journal'):
        migration.apply(*cutover)
    assert json.loads((cutover[0] / '.layout.json').read_text())['state'] == 'migrating'
    assert (cutover[0] / 'a1').is_dir()


def test_publication_refuses_changed_verified_owner_tree(cutover, monkeypatch):
    publish = migration._publish_cutover
    def crash(*args):
        raise OSError('before publication')
    monkeypatch.setattr(migration, '_publish_cutover', crash)
    with pytest.raises(OSError, match='before publication'):
        migration.apply(*cutover)
    monkeypatch.setattr(migration, '_publish_cutover', publish)
    (cutover[1] / '300001/a1/notes.txt').write_text('changed after verification')
    with pytest.raises(RuntimeError, match='owner manifest changed'):
        migration.apply(*cutover)
    assert (cutover[0] / 'a1/notes.txt').read_text() == 'private history for a1'
    assert json.loads((cutover[0] / '.layout.json').read_text())['state'] == 'migrating'


def test_shared_doors_authenticate_migrated_keys_and_hooks(cutover, monkeypatch):
    import hashlib

    from tinyassets import api_keys
    from tinyassets.auth.middleware import router_authentication
    from tinyassets.router_credentials import HookOwners

    data = cutover[0]
    keys = {owner: api_keys.ApiKeyStore(data).mint(
        owner, name='synthetic migration key', scopes=[], authority=None)['key']
        for owner in ('alice', 'bob')}
    hooks = {'alice': 'synthetic-hook-a', 'bob': 'synthetic-hook-b'}
    with closing(sqlite3.connect(data / '.tinyassets.db')) as db, db:
        db.execute('ALTER TABLE webhook_hooks ADD COLUMN revoked_at REAL')
        db.execute('INSERT INTO webhook_hooks VALUES (?,?,?,0)',
                   (hashlib.sha256(b'revoked-hook').hexdigest(), 'a1', 'alice'))
        for owner, token in hooks.items():
            db.execute('UPDATE webhook_hooks SET token_hash=? WHERE owner_principal_id=? '
                       'AND revoked_at IS NULL',
                       (hashlib.sha256(token.encode()).hexdigest(), owner))
    migration.apply(*cutover)
    monkeypatch.setattr(api_keys, 'current_store', lambda: api_keys.ApiKeyStore(data))
    context = router_authentication.set(True)
    try:
        for owner, key in keys.items():
            assert api_keys.resolve_identity(key).user_id == owner
            assert HookOwners(data).resolve(hooks[owner]) == owner
        assert api_keys.resolve_identity('invalid-key') is None
        assert HookOwners(data).resolve('invalid-hook') is None
        assert HookOwners(data).resolve('revoked-hook') is None
    finally:
        router_authentication.reset(context)


@pytest.mark.parametrize('mutation,reason', [
    ('shared', 'shared writes'), ('owner', 'owner writes'),
    ('source', 'verified cutover source'), ('new_owner', 'inventory changed'),
    ('receipt_mode', 'root-private'),
])
def test_cutover_restore_guard_refuses_any_changed_generation(cutover, tmp_path, mutation, reason):
    import runpy

    from tests.test_owner_cutover_restore import REPO

    data, store, archive, _ = cutover
    restored = tmp_path / 'restored'
    restored.mkdir()
    with tarfile.open(archive, 'r:gz') as source_archive:
        source_archive.extractall(restored, filter='data')
    staged = restored / '_data'
    migration.apply(*cutover)
    helper = runpy.run_path(str(REPO / 'deploy/box-store-quotas.py'))
    fence = helper['fence_restore']
    fence(data, staged, cutover_store=store)
    if mutation == 'shared':
        _sql(data / '.broker/plans.db', "UPDATE plans SET tier='paid' WHERE owner='bob'")
    elif mutation == 'owner':
        (store / '300001/a1/notes.txt').write_text('post-cutover owner write')
    elif mutation == 'source':
        (staged / 'a1/notes.txt').write_text('different source')
    elif mutation == 'new_owner':
        (store / '300003').mkdir()
    else:
        (data / '.owner-cutover.json').chmod(0o644)
    with pytest.raises(ValueError, match=reason):
        fence(data, staged, cutover_store=store)
    assert (store / '300001/a1').is_dir()
    assert not (data / 'a1').exists()
