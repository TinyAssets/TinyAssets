#!/usr/bin/env python3
"""Rehearse the C6 deploy container and pre-move restore on disposable Docker volumes.

Requires a Linux Docker engine with loop devices and XFS project quotas. The
privileged setup container mounts a sparse XFS image in the engine's mount
namespace; the migration itself uses the deployment container's limited flags.
No production paths, volume names, or services are touched.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "tinyassets-c6-rehearsal:local"
STAMP = "2026-10-15T00-00-00Z"
DATA_ARCHIVE = f"tinyassets-snapshot-{STAMP}.tar.gz"
BOX_ARCHIVE = f"tinyassets-box-store-{STAMP}.tar.gz"

FIXTURE = r'''
import hashlib, json, os, sqlite3, tarfile
from pathlib import Path

data, backup = Path('/data'), Path('/backup')
(data / '.broker/state').mkdir(parents=True)
(data / '.layout.json').write_text(json.dumps({
    'layout': 2, 'state': 'stable', 'box_layout': 'owner-split'}))
(data / '.layout.lock').touch()
with sqlite3.connect(data / '.broker/state/owner-identities.db') as db:
    db.execute('CREATE TABLE owner_identities '
               '(principal TEXT PRIMARY KEY, machine_id INTEGER UNIQUE)')
    db.execute('CREATE TABLE center_admissions '
               '(generation INTEGER PRIMARY KEY, event TEXT, principal TEXT, '
               'center TEXT, machine INTEGER)')
    for index, (principal, uid, center) in enumerate((
        ('alice', 300001, 'command-center'),
        ('bob', 300002, 'research'),
        ('carol', 300003, 'notes'))):
        db.execute('INSERT INTO owner_identities VALUES (?,?)', (principal, uid))
        db.execute('INSERT INTO center_admissions VALUES (?,?,?,?,?)',
                   (index + 1, 'admit', principal, center, uid))
        root = data / center
        root.mkdir()
        os.chown(root, 1001, uid)
        (root / 'odd name ü\nline.txt').write_text(f'owner={principal}\n')
        os.setxattr(root / 'odd name ü\nline.txt', b'user.c6', principal.encode())
        (root / 'relative-link').symlink_to('odd name ü\nline.txt')
        os.link(root / 'odd name ü\nline.txt', root / 'hard-link')
        with sqlite3.connect(root / 'state.sqlite') as owner_db:
            owner_db.execute('CREATE TABLE events (name TEXT)')
            owner_db.execute('INSERT INTO events VALUES (?)', (principal,))
        (root / 'nested').mkdir()
        (root / 'nested' / 'payload.bin').write_bytes(bytes(range(256)))
        if index == 0:
            (root / '.credential-vault.json').write_text('private credential')
        if index == 1 and os.environ['FAIL_AFTER_FIRST_OWNER'] == '1':
            (root / '.credential-vault.json').symlink_to('relative-link')

home = data / '.platform/owners/300001/agent-home'
home.mkdir(parents=True)
(home / 'session notes.txt').write_text('agent home')

with tarfile.open(backup / os.environ['DATA_ARCHIVE'], 'w:gz') as archive:
    archive.add(data, arcname='_data')
with tarfile.open(backup / os.environ['BOX_ARCHIVE'], 'w:gz') as archive:
    entry = tarfile.TarInfo('owners')
    entry.type = tarfile.DIRTYPE
    entry.mode = 0o755
    archive.addfile(entry)
for name in (os.environ['DATA_ARCHIVE'], os.environ['BOX_ARCHIVE']):
    path = backup / name
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    path.with_name(name + '.sha256').write_text(f'{digest}  {name}\n')
'''

VERIFY_COMMIT = r'''
import json, os, sqlite3
from pathlib import Path

data, store = Path('/data'), Path('/box-store')
marker = json.loads((data / '.layout.json').read_text())
assert marker['box_layout'] == 'owner-box' and marker['state'] == 'stable'
assert marker['activity_shape'] == 'v1'
with sqlite3.connect(data / '.broker/state/owner-identities.db') as db:
    records = db.execute('SELECT principal,machine,project_id FROM box_records '
                         'ORDER BY machine').fetchall()
assert records == [('alice', 300001, 300001), ('bob', 300002, 300002),
                   ('carol', 300003, 300003)]
for principal, uid, center in (
        ('alice', 300001, 'command-center'),
        ('bob', 300002, 'research'),
        ('carol', 300003, 'notes')):
    source, moved = data / center, store / str(uid) / center
    for relative in ('odd name ü\nline.txt', 'state.sqlite', 'nested/payload.bin'):
        assert (source / relative).read_bytes() == (moved / relative).read_bytes()
    assert os.getxattr(moved / 'odd name ü\nline.txt', b'user.c6') == principal.encode()
    assert (moved / 'relative-link').is_symlink()
    assert os.readlink(moved / 'relative-link') == 'odd name ü\nline.txt'
    assert (moved / 'hard-link').stat().st_ino == (moved / 'odd name ü\nline.txt').stat().st_ino
    with sqlite3.connect(moved / 'state.sqlite') as db:
        assert db.execute('SELECT name FROM events').fetchone() == (principal,)
assert (data / '.platform/credential-vaults/command-center/.credential-vault.json'
        ).read_text() == 'private credential'
assert not (store / '300001/command-center/.credential-vault.json').exists()
assert (store / '300001/.agent-home/session notes.txt').read_text() == 'agent home'
print('owner-box marker, three records, SQLite, bytes, links, credentials and agent home: PASS')
'''

VERIFY_RESTORE = r'''
import json, sqlite3
from pathlib import Path

data, store = Path('/parent/_data'), Path('/box-store')
marker = json.loads((data / '.layout.json').read_text())
assert marker['box_layout'] == 'owner-split' and marker['state'] == 'stable'
with sqlite3.connect(data / '.broker/state/owner-identities.db') as db:
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='box_records'").fetchone() is None
assert (data / 'command-center/odd name ü\nline.txt').read_text() == 'owner=alice\n'
assert not (store / '300001').exists()
retained = list(store.glob('.tinyassets-box-restore-old.*/300001/command-center/state.sqlite'))
assert len(retained) == 1, retained
print('pre-move marker and DB restored; partial boxes retained: PASS')
'''


def run(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(argv, text=True, capture_output=True)
    if check and done.returncode:
        raise RuntimeError(
            f"{' '.join(argv[:5])}: rc={done.returncode}\n{done.stdout}{done.stderr}")
    return done


def docker(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run('docker', *argv, check=check)


def volume_path(name: str) -> str:
    return docker('volume', 'inspect', '--format', '{{ .Mountpoint }}', name).stdout.strip()


def rehearsal(image: str, *, fail: bool) -> None:
    prefix = f"ta-c6-{uuid.uuid4().hex[:12]}"
    names = {kind: f"{prefix}-{kind}" for kind in ('image', 'store', 'parent', 'data', 'backup')}
    created: list[str] = []
    mounted = False
    try:
        for kind in ('image', 'store', 'parent', 'backup'):
            docker('volume', 'create', names[kind])
            created.append(names[kind])
        parent_path = volume_path(names['parent'])
        store_path = volume_path(names['store'])
        image_path = volume_path(names['image'])
        docker('run', '--rm', '--user', '0', '-v', f"{names['parent']}:/parent",
               '--entrypoint', 'mkdir', image, '-p', '/parent/_data')
        docker('volume', 'create', '--driver', 'local', '--opt', 'type=none',
               '--opt', 'o=bind', '--opt', f'device={parent_path}/_data', names['data'])
        created.append(names['data'])

        host_store = f'/proc/1/root{store_path}'
        host_image = f'/proc/1/root{image_path}/store.img'
        setup = (f'truncate -s 500M /image/store.img; '
                 f'mkfs.xfs -f /image/store.img >/dev/null; '
                 f'nsenter -t 1 -m mount -o loop,prjquota {host_image} {host_store}')
        docker('run', '--rm', '--privileged', '--pid=host', '--user', '0',
               '-v', f"{names['image']}:/image", '--entrypoint', 'sh', image,
               '-ec', setup)
        mounted = True
        source = docker('run', '--rm', '-v', f"{names['store']}:/box-store",
                        '--entrypoint', 'findmnt', image, '--mountpoint', '/box-store',
                        '-no', 'SOURCE,FSTYPE,OPTIONS').stdout.strip()
        device, fstype, options = source.split(None, 2)
        assert device.startswith('/dev/') and fstype == 'xfs' and 'prjquota' in options, source
        print(f'[rehearsal] {"failed-move restore" if fail else "commit"}: {source}')

        fixture_env = ['-e', f'DATA_ARCHIVE={DATA_ARCHIVE}', '-e', f'BOX_ARCHIVE={BOX_ARCHIVE}',
                       '-e', f'FAIL_AFTER_FIRST_OWNER={int(fail)}']
        docker('run', '--rm', '--user', '0', '--network', 'none',
               '-v', f"{names['data']}:/data", '-v', f"{names['backup']}:/backup",
               *fixture_env, '--entrypoint', 'python', image, '-c', FIXTURE)
        probe = docker('run', '--rm', '--user', '0', '--network', 'none',
                       '-v', f"{names['data']}:/data:ro", '--entrypoint', 'python', image,
                       '-B', '-m', 'tinyassets.box_layout_migration', 'check', '/data',
                       check=False)
        assert probe.returncode == 10, probe.stdout + probe.stderr

        # Keep this argv in lockstep with deploy_fail_safe.sh:box_migration_container.
        apply = docker('run', '--rm', '--cap-drop', 'ALL', '--user', '0',
                       '--cap-add', 'CHOWN', '--cap-add', 'FOWNER',
                       '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SYS_ADMIN',
                       '--security-opt', 'no-new-privileges=true', '--network', 'none',
                       '--memory=1g', '--memory-swap=1g',
                       '-v', f"{names['data']}:/data",
                       '-v', f"{names['backup']}:/backup:ro",
                       '--device', f'{device}:{device}:r',
                       '--mount', f'type=bind,src={store_path},dst=/box-store',
                       '--entrypoint', 'python', image, '-B', '-m',
                       'tinyassets.box_layout_migration', 'apply', '/data',
                       '--store', '/box-store', '--backup', f'/backup/{DATA_ARCHIVE}',
                       '--box-backup', f'/backup/{BOX_ARCHIVE}', check=False)
        if fail:
            assert apply.returncode == 3 and 'non-regular credential source' in apply.stderr, (
                apply.stdout + apply.stderr)
            print('[rehearsal] induced failure after first owner: ' + apply.stderr.strip())
            shim = r'''set -eu
mkdir -p /tmp/c6-bin
printf '#!/bin/sh\ncase "$1 $2" in\n' > /tmp/c6-bin/docker
printf '"volume inspect") echo /parent/_data;;\n' >> /tmp/c6-bin/docker
printf '"ps -q") exit 0;; *) exit 1;; esac\n' >> /tmp/c6-bin/docker
printf '#!/bin/sh\nexit 0\n' > /tmp/c6-bin/systemctl
chmod +x /tmp/c6-bin/docker /tmp/c6-bin/systemctl
export PATH=/tmp/c6-bin:$PATH
export BACKUP_FILE=/backup/''' + DATA_ARCHIVE + r'''
export BOX_BACKUP_FILE=/backup/''' + BOX_ARCHIVE + r'''
export BACKUP_VOLUME=rehearsal-data
export BOX_STORE_DIR=/box-store
export BACKUP_LOG=/tmp/c6-restore.log
bash /rehearsal-deploy/backup-restore.sh
'''
            restored = docker('run', '--rm', '--privileged', '--user', '0',
                              '--network', 'none', '-v', f"{names['parent']}:/parent",
                              '-v', f"{names['backup']}:/backup:ro",
                              '--mount', f'type=bind,src={store_path},dst=/box-store',
                              '--mount', f'type=bind,src={ROOT / "deploy"},'
                                         'dst=/rehearsal-deploy,readonly',
                              '--entrypoint', 'sh', image, '-ec', shim, check=False)
            assert restored.returncode == 0, restored.stdout + restored.stderr
            verified = docker('run', '--rm', '--user', '0', '--network', 'none',
                              '-v', f"{names['parent']}:/parent",
                              '--mount', f'type=bind,src={store_path},dst=/box-store',
                              '--entrypoint', 'python', image, '-c', VERIFY_RESTORE)
            print('[rehearsal] ' + verified.stdout.strip())
        else:
            assert apply.returncode == 0, apply.stdout + apply.stderr
            quotas = docker('run', '--rm', '--cap-drop', 'ALL', '--user', '0',
                            '--cap-add', 'SYS_ADMIN', '--device', f'{device}:{device}:r',
                            '--network', 'none',
                            '--mount', f'type=bind,src={store_path},dst=/box-store',
                            '--entrypoint', 'xfs_quota', image, '-x', '-c',
                            'report -p -n', '/box-store')
            assert not quotas.stderr, quotas.stderr
            assert all(uid in quotas.stdout for uid in ('300001', '300002', '300003')), (
                quotas.stdout)
            verified = docker('run', '--rm', '--user', '0', '--network', 'none',
                              '-v', f"{names['data']}:/data",
                              '--mount', f'type=bind,src={store_path},dst=/box-store',
                              '--entrypoint', 'python', image, '-c', VERIFY_COMMIT)
            print('[rehearsal] ' + verified.stdout.strip())
    finally:
        if mounted:
            unmount = docker('run', '--rm', '--privileged', '--pid=host', '--user', '0',
                             '--entrypoint', 'nsenter', image, '-t', '1', '-m', 'umount',
                             f'/proc/1/root{volume_path(names["store"])}', check=False)
            if unmount.returncode:
                print('[rehearsal] XFS unmount failed; preserving disposable volumes: '
                      + unmount.stderr, file=sys.stderr)
                raise RuntimeError('disposable XFS mount remains active; volumes were preserved')
        for name in reversed(created):
            docker('volume', 'rm', name)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', default=IMAGE)
    parser.add_argument('--no-build', action='store_true')
    args = parser.parse_args()
    if docker('info', '--format', '{{.OSType}}').stdout.strip() != 'linux':
        raise SystemExit('rehearsal requires a Linux Docker engine')
    if not args.no_build:
        print('[rehearsal] building the real daemon image', flush=True)
        subprocess.run(['docker', 'build', '-q', '-t', args.image, str(ROOT)], check=True)
    rehearsal(args.image, fail=False)
    rehearsal(args.image, fail=True)


if __name__ == '__main__':
    main()
