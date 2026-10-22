"""Root Linux proof using synthetic live shared state and the real cutover."""
import os
import stat
import subprocess
import sys
import time
import uuid

import pytest

from tests.test_owner_cutover_apply import cutover as cutover
from tests.test_owner_cutover_apply import source as source
from tests.test_owner_cutover_census import _sql
from tinyassets import box_layout_migration as migration

pytestmark = pytest.mark.verifies('owner-daemon-in-box:C1')


def metadata(path):
    info = path.stat()
    return info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)


def live_shared(data):
    _sql(data / '.runtime/owner-sessions.db',
         'CREATE TABLE owner_sessions (session_hash TEXT, identity_json TEXT, expires_at REAL);')
    (data / '.connect').mkdir(exist_ok=True)
    (data / '.connect/client_id').write_text('synthetic')
    (data / '.runtime/app_refresh_sessions').mkdir()
    _sql(data / '.tinyassets.db',
         "CREATE TABLE author_definitions (id TEXT); "
         "INSERT INTO author_definitions VALUES ('synthetic-public-definition');")
    (data / '.published').mkdir()
    shapes = {'.runtime': (1001, 1102, 0o710),
              '.runtime/app_refresh_sessions': (1001, 1001, 0o700),
              '.broker': (1002, 1002, 0o700),
              '.broker/state': (1002, 1002, 0o700),
              '.platform': (1001, 1001, 0o711),
              '.published': (1002, 1102, 0o2750),
              '.connect': (1001, 1001, 0o700)}
    for relative, (uid, gid, mode) in shapes.items():
        os.chown(data / relative, uid, gid)
        (data / relative).chmod(mode)
    os.chown(data / '.runtime/owner-sessions.db', 1001, 1102)
    (data / '.runtime/owner-sessions.db').chmod(0o640)
    return shapes


def test_publish_keeps_live_shared_directory_metadata(cutover):
    data, *_ = cutover
    expected = live_shared(data)
    migration.apply(*cutover)
    actual = {relative: metadata(data / relative) for relative in expected}
    assert actual == expected


@pytest.mark.parametrize('tamper', ['mode', 'owner', 'missing'])
def test_publish_refuses_changed_directory_manifest(cutover, monkeypatch, tamper):
    data, store, *_ = cutover
    live_shared(data)
    publish = migration._publish_cutover

    def stopped(*_):
        raise RuntimeError('stopped before publish')

    monkeypatch.setattr(migration, '_publish_cutover', stopped)
    with pytest.raises(RuntimeError, match='stopped before publish'):
        migration.apply(*cutover)
    staged = store / '.cutover-shared/.runtime'
    if tamper == 'mode':
        staged.chmod(0o750)
    elif tamper == 'owner':
        os.chown(staged, 0, 0)
    else:
        import json

        path = data / '.owner-cutover.json'
        journal = json.loads(path.read_text())
        del journal['shared_directories']
        path.write_text(json.dumps(journal))
    monkeypatch.setattr(migration, '_publish_cutover', publish)
    with pytest.raises(RuntimeError, match='directory metadata'):
        migration.apply(*cutover)
    assert metadata(data / '.runtime') == (1001, 1102, 0o710)


def test_new_shared_directories_have_explicit_role_custody(tmp_path):
    if not hasattr(os, 'geteuid') or os.geteuid() != 0:
        pytest.skip('requires linux_oracle.py --as-root; owner=c6 runs-in=linux_oracle.py')
    live, stage = tmp_path / 'live', tmp_path / 'stage'
    live.mkdir()
    expected = {'.broker/state': (1002, 1002, 0o700),
                '.published': (1002, 1102, 0o750),
                '.connect': (1001, 1001, 0o700),
                '.runtime': (1001, 1102, 0o710),
                '.runtime/app_refresh_sessions': (1001, 1001, 0o700),
                '.platform': (1001, 1001, 0o711),
                '.platform/owners': (1002, 1002, 0o700)}
    for relative in expected:
        (stage / relative).mkdir(parents=True, exist_ok=True)
    recorded = migration._stage_shared_directories(live, stage)
    assert {key: metadata(stage / key) for key in expected} == expected
    assert recorded == migration._shared_directory_metadata(stage)


def test_published_router_connects_across_compose_containers(cutover):
    from tests.test_compose_role_identity import ROOT, _flags, _stanza

    data, *_ = cutover
    live_shared(data)
    migration.apply(*cutover)
    base = os.environ['TA_REHEARSAL_BASE_IMAGE']
    name = 'ta-publish-meta-' + uuid.uuid4().hex[:12]
    image, volume, seed, daemon = name + ':proof', name + '-data', name + '-seed', name + '-daemon'

    def docker(*args, **kwargs):
        result = subprocess.run(['docker', *args], capture_output=True, text=True,
                                timeout=600, **kwargs)
        assert result.returncode == 0, result.stderr
        return result.stdout.strip()

    try:
        docker('build', '-q', '-t', image, '-f', '-', str(ROOT),
               input=f'FROM {base}\nCOPY tinyassets /app/tinyassets\nENV PYTHONPATH=/app\n')
        docker('volume', 'create', volume)
        docker('create', '--name', seed, '-v', volume + ':/data', image)
        docker('cp', '-a', str(data) + '/.', seed + ':/data')
        docker('run', '-d', '--name', daemon, '--network', 'none',
               *_flags(_stanza('daemon')), '-v', volume + ':/data',
               '--entrypoint', '/usr/local/bin/python', image, '-u', '-c', '''
import asyncio
from pathlib import Path
from tinyassets.router_credentials import serving
async def main():
    async with serving(Path('/data')):
        print('ready', flush=True)
        await asyncio.sleep(300)
asyncio.run(main())
''')
        for _ in range(150):
            if 'ready' in docker('logs', daemon):
                break
            time.sleep(0.1)
        else:
            pytest.fail(docker('logs', daemon))
        docker('run', '--rm', '--network', 'none', *_flags(_stanza('broker')),
               '-v', volume + ':/data', '--entrypoint', '/usr/local/bin/python', image, '-c', '''
from pathlib import Path
from tinyassets.router_credentials import routing_request
reply = routing_request(Path('/data'), {'operation': 'routing_instance'})
assert reply['op'] == 'ROUTING_SNAPSHOT_IS', reply
assert reply['result']['revision'] == 0, reply
''')
    finally:
        for command in (['rm', '-f', daemon, seed], ['volume', 'rm', volume],
                        ['image', 'rm', image]):
            subprocess.run(['docker', *command], capture_output=True, timeout=60)


def test_published_router_serves_compose_broker(cutover):
    data, *_ = cutover
    live_shared(data)
    migration.apply(*cutover)
    # Permit the service identities to traverse pytest's enclosing directories.
    for parent in data.parents:
        if parent == parent.parent:
            break
        parent.chmod(parent.stat().st_mode | 0o111)
    server = subprocess.Popen([sys.executable, '-u', '-c', '''
import asyncio, sys
from pathlib import Path
from tinyassets.router_credentials import serving
async def main():
    async with serving(Path(sys.argv[1])):
        print('ready', flush=True)
        await asyncio.to_thread(sys.stdin.read)
asyncio.run(main())
''', str(data)], user=1001, group=1001, extra_groups=[1100, 1101, 1102],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
    try:
        assert server.stdout.readline().strip() == 'ready', server.stderr.read()
        result = subprocess.run([sys.executable, '-c', '''
import sys
from pathlib import Path
from tinyassets.router_credentials import routing_request
answer = routing_request(Path(sys.argv[1]), {'operation': 'routing_instance'})
assert answer['op'] == 'ROUTING_SNAPSHOT_IS', answer
assert answer['result']['revision'] == 0, answer
''', str(data)], user=1002, group=1002, extra_groups=[1102],
            capture_output=True, text=True, timeout=30)
        assert result.returncode == 0, result.stderr
    finally:
        server.communicate('', timeout=30)


def test_partial_publish_resumes_recorded_directory_custody(cutover, monkeypatch):
    data, *_ = cutover
    expected = live_shared(data)
    replace = os.replace

    def interrupted(source, target):
        replace(source, target)
        if str(source).endswith('.cutover'):
            raise RuntimeError('partial publish')

    with monkeypatch.context() as patch:
        patch.setattr(os, 'replace', interrupted)
        with pytest.raises(RuntimeError, match='partial publish'):
            migration.apply(*cutover)
    # Resume must not infer custody from the now-partially-published live tree.
    os.chown(data / '.runtime', 0, 0)
    (data / '.runtime').chmod(0o755)
    migration.apply(*cutover)
    assert {key: metadata(data / key) for key in expected} == expected
    migration.verify(data, cutover[1])
