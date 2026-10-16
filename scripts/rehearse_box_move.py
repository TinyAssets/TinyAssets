#!/usr/bin/env python3
"""Rehearse the C6 deploy container and pre-move restore on disposable Docker volumes.

Requires a Linux Docker engine with loop devices and XFS project quotas. The
privileged setup container mounts a sparse XFS image in the engine's mount
namespace; the migration itself uses the deployment container's limited flags.
No production paths, volume names, or services are touched.

For a local full backup (the lead runs this with the private copy):
    python scripts/rehearse_box_move.py --archive /tmp/tinyassets-data-2026-10-15T00-00-00Z.tar.gz
"""

from __future__ import annotations

import argparse
import json
import re
import signal
import subprocess
import sys
import tarfile
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
IMAGE = "tinyassets-c6-rehearsal:local"
STAMP = "2026-10-15T00-00-00Z"
DATA_ARCHIVE = f"tinyassets-snapshot-{STAMP}.tar.gz"
BOX_ARCHIVE = f"tinyassets-box-store-{STAMP}.tar.gz"
PAIR_MANIFEST = f"tinyassets-pair-{STAMP}.json"
ARCHIVE_NAME = re.compile(r"tinyassets-data-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.tar\.gz\Z")

FIXTURE = r'''
import hashlib, json, os, sqlite3, tarfile
from pathlib import Path
from tinyassets.knowledge.knowledge_graph import initialize_knowledge_index
from tinyassets.memory.project import PROJECT_SCHEMA

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
        with sqlite3.connect(root / '.project_memory.db') as knowledge_db:
            knowledge_db.executescript(PROJECT_SCHEMA)
            knowledge_db.execute('INSERT INTO project_memory VALUES (?,?,?,?,?,?)',
                                 (f'project-{principal}', 'fixture',
                                  json.dumps({'owner': principal}), 'now', principal, 1))
        with sqlite3.connect(root / 'knowledge.db') as knowledge_db:
            knowledge_db.row_factory = sqlite3.Row
            initialize_knowledge_index(knowledge_db)
            knowledge_db.execute('INSERT INTO entities(entity_id, universe_id) VALUES (?,?)',
                                 (f'entity-{principal}', center))
        (root / 'nested').mkdir()
        (root / 'nested' / 'payload.bin').write_bytes(bytes(range(256)))
        special = root / '.agent-workspace' / '.tmp' / 'pytest-of-unknown'
        special.mkdir(parents=True)
        if index == 0:
            (root / '.agent-workspace' / 'knowledge.db').touch()
        liveness = special / '.consumer_liveness'
        liveness.mkdir()
        os.chmod(liveness, 0o2750)
        sticky = root / 'sticky'
        sticky.mkdir()
        os.chmod(sticky, 0o1777)
        setuid = root / 'setuid-tool'
        setuid.write_bytes(b'fixture, never executed\n')
        os.chmod(setuid, 0o4750)
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
assert marker['knowledge_shape'] == 'v1'
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
    assert not source.exists(), 'committed host owner copy remains'
    for relative in ('odd name ü\nline.txt', 'state.sqlite', 'nested/payload.bin'):
        assert (moved / relative).exists()
    assert os.getxattr(moved / 'odd name ü\nline.txt', b'user.c6') == principal.encode()
    assert (moved / 'relative-link').is_symlink()
    assert os.readlink(moved / 'relative-link') == 'odd name ü\nline.txt'
    assert (moved / 'hard-link').stat().st_ino == (moved / 'odd name ü\nline.txt').stat().st_ino
    for relative, expected in (
            ('.agent-workspace/.tmp/pytest-of-unknown/.consumer_liveness', 0o2750),
            ('sticky', 0o1777), ('setuid-tool', 0o4750)):
        assert (moved / relative).stat().st_mode & 0o7777 == expected
    with sqlite3.connect(moved / 'state.sqlite') as db:
        assert db.execute('SELECT name FROM events').fetchone() == (principal,)
    if principal == 'alice':
        assert (moved / '.agent-workspace/knowledge.db').stat().st_size == 0
        with sqlite3.connect(moved / 'knowledge.db') as db:
            assert db.execute('SELECT entity_id FROM entities').fetchone() == ('entity-alice',)
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
assert 'knowledge_shape' not in marker
with sqlite3.connect(data / '.broker/state/owner-identities.db') as db:
    assert db.execute("SELECT 1 FROM sqlite_master WHERE name='box_records'").fetchone() is None
assert (data / 'command-center/odd name ü\nline.txt').read_text() == 'owner=alice\n'
with sqlite3.connect(data / 'command-center/.project_memory.db') as db:
    assert db.execute('SELECT count(*) FROM project_memory').fetchone() == (1,)
with sqlite3.connect(data / 'command-center/knowledge.db') as db:
    assert db.execute('SELECT count(*) FROM entities').fetchone() == (1,)
assert not (store / '300001').exists()
retained = list(store.glob('.tinyassets-box-restore-old.*/300001/command-center/state.sqlite'))
assert len(retained) == 1, retained
print('pre-move marker and DB restored; partial boxes retained: PASS')
'''

KNOWLEDGE_VERIFY = r'''
import hashlib, json, tempfile, sqlite3
from pathlib import Path
from tinyassets.box_layout_migration import _owner_plan
from types import SimpleNamespace
from tinyassets.boxes.local import LocalBoxProvider
from tinyassets.okf.box_cutover import _FILES
from tinyassets.okf.bundle import KnowledgeBundle
from tinyassets.okf.records import SourceRecords, source_census

data, store = Path('/data'), Path('/box-store')
marker = json.loads((data / '.layout.json').read_text())
assert marker.get('box_layout') == 'owner-box' and marker.get('state') == 'stable'
assert marker.get('knowledge_shape') == 'v1'
with sqlite3.connect(data / '.broker/state/owner-identities.db') as db:
    records = db.execute('SELECT principal, machine FROM box_records').fetchall()
owners = [SimpleNamespace(principal=principal, uid=machine,
                          centers=tuple(path.name for path in
                                        (store / str(machine)).iterdir()
                                        if path.is_dir() and not path.name.startswith('.')))
          for principal, machine in records]
for owner in owners:
    owner_rows = 0
    for center in owner.centers:
        print(f'owner={owner.uid} path=/box-store/{owner.uid}/{center}/brain/knowledge VERIFY',
              flush=True)
        with tempfile.TemporaryDirectory(prefix='knowledge-rehearsal-') as scratch:
            root = Path(scratch)
            (root / 'snapshots').mkdir()
            (root / 'state').mkdir()
            provider = LocalBoxProvider(
                boxes_root=store / str(owner.uid), state_dir=root / 'state',
                owner_of=lambda candidate: owner.principal if candidate == center else None,
                allow_unisolated=True,
            )
            try:
                handle = provider.bind(center, account_id=owner.principal)
                bundle = KnowledgeBundle(provider, handle)
                snapshot = bundle.snapshot()
                operation = 'owner-box-knowledge-' + hashlib.sha256(
                    (owner.principal + '\0' + center).encode()).hexdigest()
                assert snapshot.manifest.get('operation_id') == operation
                actual_rows = 0
                expected_rows = 0
                for source in _FILES:
                    census = source_census(snapshot, source)
                    expected_rows += sum(table['count'] for table in census['tables'].values())
                    rows = SourceRecords(bundle, source).rows(snapshot)
                    actual_rows += sum(map(len, rows.values()))
                assert actual_rows == expected_rows
                vectors = sum(entry.type == 'vector-record'
                              for entry in snapshot.entries.values())
                owner_rows += actual_rows + vectors
            finally:
                provider.close()
        print(f'owner={owner.uid} path=/box-store/{owner.uid}/{center}/brain/knowledge '
              f'consumers={len(_FILES)} rows={actual_rows} vectors={vectors} PASS')
    print(f'owner={owner.uid} knowledge_centers={len(owner.centers)} '
          f'knowledge_records={owner_rows} PASS')
print(f'knowledge_shape={marker["knowledge_shape"]} owners={len(owners)} PASS')
'''

ARCHIVE_PLAN = r'''
import json
from pathlib import Path
from tinyassets.box_layout_migration import _owner_plan

print(json.dumps([{'uid': owner.uid, 'centers': list(owner.centers), 'quota': owner.quota}
                  for owner in _owner_plan(Path('/data'))]))
'''

QUOTA_READBACK = r'''
import json, sqlite3, sys
from pathlib import Path
from deploy.box_project_readback import verify_owner, verify_tree

store = Path('/box-store')
if sys.argv[1] == 'set':
    with sqlite3.connect('/data/.broker/state/owner-identities.db') as db:
        rows = db.execute('SELECT machine,project_id,bhard_bytes FROM box_records').fetchall()
    assert rows, 'no owner box records'
    for uid, project, limit in rows:
        verify_owner(store, store / str(uid), project, limit)
        print(f'owner={uid} inode_project_and_hard_limit=PASS')
else:
    retained = sorted(store.glob('.tinyassets-box-restore-old.*/[0-9]*'))
    assert retained, 'no retained owner trees'
    if sys.argv[1] == 'retained':
        plan = json.loads(sys.argv[2])
    for tree in retained:
        if sys.argv[1] == 'retained':
            assert not (store / tree.name).exists(), 'pre-move restore installed a box'
            verify_owner(store, tree, int(tree.name), plan[tree.name])
            print(f'owner={tree.name} retained_project_and_hard_limit=PASS')
        else:
            verify_tree(tree, 0, False)
            print(f'owner={tree.name} retained_project_cleared=PASS')
'''

ARCHIVE_REPORT = r'''
import json, os, sqlite3, subprocess, sys
from pathlib import Path
from types import SimpleNamespace
from tinyassets.box_layout_migration import _owner_plan

data, store = Path('/data'), Path('/box-store')
mode = sys.argv[1]
plan = _owner_plan(data) if mode != 'commit' else []
success = True
if mode == 'commit':
    marker = json.loads((data / '.layout.json').read_text())
    with sqlite3.connect(data / '.broker/state/owner-identities.db') as db:
        records = {row[0]: row[1:] for row in db.execute(
            'SELECT principal, machine, project_id FROM box_records')}
    plan = [SimpleNamespace(principal=principal, uid=machine,
                            centers=tuple(path.name for path in
                                          (store / str(machine)).iterdir()
                                          if path.is_dir() and not path.name.startswith('.')))
            for principal, (machine, _project) in records.items()]
    if (marker.get('box_layout') != 'owner-box' or marker.get('state') != 'stable'
            or marker.get('activity_shape') != 'v1'
            or marker.get('knowledge_shape') != 'v1'):
        print('layout: FAIL')
        success = False
else:
    records = {}
for owner in plan:
    differences = []
    count = 0
    for center in owner.centers:
        source, target = data / center, store / str(owner.uid) / center
        if mode == 'commit':
            if source.exists() or source.is_symlink():
                differences.append(f'committed host copy remains: {source}')
            source = target
        count += sum(len(files) + len(dirs) for _, dirs, files in os.walk(source))
        compare_target = target
        if not target.is_dir():
            differences.append(f'missing path={str(target)!r}')
            compare_target = Path('/tmp/c6-empty-target')
            compare_target.mkdir(exist_ok=True)
        # --delete itemizes target-only paths as well as changed source paths.
        args = ['rsync', '-aH', '--xattrs', '--one-file-system', '--numeric-ids',
                '--chown', f'{owner.uid}:{owner.uid}', '--checksum', '--dry-run',
                '--delete',
                '--itemize-changes', '--out-format=%i %n%L',
                '--exclude=/.credential-vault.json', '--exclude=/.credentials',
                '--exclude=/.credentials.json', '--exclude=/.oauth-refresh',
                '--exclude=/.runtime']
        if mode == 'commit':
            # The copy is verified before C12 writes its canonical projection.
            # Compare every other path against that pre-conversion copy.
            args.append('--exclude=/brain/knowledge/***')
        args.extend((f'{source}/', f'{compare_target}/'))
        result = subprocess.run(args, capture_output=True, text=True)
        source_brain = source / 'brain'
        brain_was_absent = not source_brain.exists() and not source_brain.is_symlink()
        knowledge_was_absent = (not (source_brain / 'knowledge').exists()
                                and not (source_brain / 'knowledge').is_symlink())
        for line in result.stdout.splitlines():
            # Creating brain/knowledge changes its parents' mtimes. Rsync also
            # cannot delete a new brain parent while knowledge is protected;
            # the explicit child check below accounts for that one message.
            if mode == 'commit' and (
                    (brain_was_absent and line == '.d..t...... ./')
                    or (brain_was_absent
                        and line == 'cannot delete non-empty directory: brain')
                    or (not brain_was_absent and knowledge_was_absent
                        and line == '.d..t...... brain/')):
                continue
            differences.append(f'path={str(target)!r} attributes={line!r}')
        if mode == 'commit' and brain_was_absent:
            # Rsync protects the excluded projection and so cannot report an
            # otherwise extra brain directory. Inspect that parent explicitly.
            brain = target / 'brain'
            if brain.is_symlink() or not brain.is_dir():
                differences.append(f'missing canonical parent path={str(brain)!r}')
            else:
                differences.extend(
                    f'unexpected path={str(child)!r}' for child in brain.iterdir()
                    if child.name != 'knowledge')
        if result.returncode:
            differences.append(f'rsync rc={result.returncode} path={str(source)!r}')
    if mode == 'commit':
        if records.get(owner.principal) != (owner.uid, owner.uid):
            differences.append('box record: FAIL')
        if not (store / str(owner.uid)).is_dir():
            differences.append(f'missing path={str(store / str(owner.uid))!r}')
        home = data / '.platform' / 'owners' / str(owner.uid) / 'agent-home'
        if home.is_dir() and not (store / str(owner.uid) / '.agent-home').is_dir():
            differences.append(f'missing path={str(store / str(owner.uid) / ".agent-home")!r}')
    status = 'PASS' if not differences else 'FAIL'
    print(f'owner={owner.uid} centers={len(owner.centers)} entries={count} '
          f'differences={len(differences)} {status}')
    if differences:
        success = False
        for difference in differences:
            print(f'  rsync {json.dumps(difference, ensure_ascii=True)}')
if len(records) != len(plan) and mode == 'commit':
    print(f'box records count={len(records)} expected={len(plan)} FAIL')
    success = False
raise SystemExit(0 if success else 1)
'''

ARCHIVE_FAILURE = r'''
import os, sys
from pathlib import Path

data = Path('/data')
uid, center = sys.argv[1:]
source = data / center / '.oauth-refresh'
if source.exists() or source.is_symlink():
    saved = source.with_name(source.name + '.c6-rehearsal-original')
    if saved.exists() or saved.is_symlink():
        raise SystemExit(f'failure fixture save path already exists: {saved}')
    source.rename(saved)
source.symlink_to('rehearsal-invalid-credential')
print(f'failure fixture path={source}')
'''

ARCHIVE_RESTORE_CHECK = r'''
import json, sqlite3, sys
from pathlib import Path

data, store = Path('/parent/_data'), Path('/box-store')
marker = json.loads((data / '.layout.json').read_text())
with sqlite3.connect(data / '.broker/state/owner-identities.db') as db:
    box_records = db.execute("SELECT 1 FROM sqlite_master WHERE name='box_records'").fetchone()
retained = [path for path in store.glob('.tinyassets-box-restore-old.*/[0-9]*')
            if path.is_dir()]
good = (marker.get('box_layout', 'owner-split') == 'owner-split'
        and marker.get('state') == 'stable' and 'knowledge_shape' not in marker
        and box_records is None
        and bool(retained))
print(f'restore marker={marker.get("state")} box_records={int(box_records is not None)} '
      f'retained_owners={len(retained)} {"PASS" if good else "FAIL"}')
raise SystemExit(0 if good else 1)
'''


def run(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    done = subprocess.run(argv, text=True, capture_output=True)
    if check and done.returncode:
        raise RuntimeError(
            f"{' '.join(argv[:5])}: FAIL (rc={done.returncode}) "
            f"{(done.stderr or done.stdout).strip()[:2000]}")
    return done


def docker(*argv: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return run('docker', *argv, check=check)


def volume_path(name: str) -> str:
    return docker('volume', 'inspect', '--format', '{{ .Mountpoint }}', name).stdout.strip()


def validate_archive(path: Path) -> int:
    """Apply backup-restore.sh's member rules before mounting or extracting."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError('--archive requires an absolute readable non-symlink regular file')
    if not ARCHIVE_NAME.fullmatch(path.name):
        raise ValueError('--archive requires a tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz file')
    total = 0
    try:
        with tarfile.open(path, 'r:gz') as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError('archive is empty')
            symlinks = {member.name.rstrip('/') for member in members if member.issym()}
            for member in members:
                name = member.name.rstrip('/') if member.isdir() else member.name
                parts = name.split('/')
                if (name.startswith('/') or not parts or parts[0] != '_data'
                        or any(part in ('', '.', '..') for part in parts)):
                    raise ValueError(f'unsafe archive path: {name!r}')
                if len(parts) == 1 and not member.isdir():
                    raise ValueError(f'archive root is not a directory: {name!r}')
                if any('/'.join(parts[:index]) in symlinks
                       for index in range(1, len(parts))):
                    raise ValueError(f'archive descends through a symlink: {name!r}')
                if member.islnk():
                    target = member.linkname.split('/')
                    if (member.linkname.startswith('/') or not target
                            or target[0] != '_data' or '..' in target
                            or member.linkname in symlinks):
                        raise ValueError(f'unsafe archive hardlink: {name!r}')
                elif not (member.isfile() or member.isdir() or member.issym()):
                    raise ValueError(f'unsupported archive member type: {name!r}')
                total += member.size if member.isfile() else 0
    except (OSError, tarfile.TarError) as exc:
        raise ValueError(f'archive unreadable: {type(exc).__name__}') from exc
    return total


def archive_rehearsal(image: str, archive: Path, *, fail: bool, store_bytes: int) -> None:
    prefix = f'ta-c6-{uuid.uuid4().hex[:12]}'
    names = {kind: f'{prefix}-{kind}' for kind in ('image', 'store', 'parent', 'data', 'backup')}
    created: list[str] = []
    containers: list[str] = []
    mounted = False
    device = ''

    def container(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        name = f'{prefix}-run-{len(containers)}'
        containers.append(name)
        return docker('run', '--name', name, *args, check=check)

    try:
        for kind in ('image', 'store', 'parent', 'backup'):
            docker('volume', 'create', names[kind])
            created.append(names[kind])
        parent_path = volume_path(names['parent'])
        store_path = volume_path(names['store'])
        image_path = volume_path(names['image'])
        container('--rm', '--user', '0', '-v', f"{names['parent']}:/parent",
                  '--entrypoint', 'mkdir', image, '-p', '/parent/_data')
        docker('volume', 'create', '--driver', 'local', '--opt', 'type=none',
               '--opt', 'o=bind', '--opt', f'device={parent_path}/_data', names['data'])
        created.append(names['data'])

        host_store = f'/proc/1/root{store_path}'
        host_image = f'/proc/1/root{image_path}/store.img'
        setup = (f'truncate -s {store_bytes} /image/store.img; '
                 'mkfs.xfs -f /image/store.img >/dev/null; '
                 'nsenter -t 1 -m mount -o loop,prjquota "$1" "$2"')
        setup_result = container('--rm', '--privileged', '--pid=host', '--user', '0',
                                 '-v', f"{names['image']}:/image", '--entrypoint', 'sh',
                                 image, '-ec', setup, 'sh', host_image, host_store,
                                 check=False)
        if setup_result.returncode:
            raise RuntimeError(f'XFS loop setup: FAIL (rc={setup_result.returncode})')
        mounted = True
        source = container('--rm', '-v', f"{names['store']}:/box-store",
                           '--entrypoint', 'findmnt', image, '--mountpoint', '/box-store',
                           '-no', 'SOURCE,FSTYPE,OPTIONS').stdout.strip()
        fields = source.split(None, 2)
        if len(fields) != 3 or fields[1] != 'xfs' or 'prjquota' not in fields[2]:
            raise RuntimeError(f'XFS project quota mount: FAIL ({source})')
        device = fields[0]
        if not re.fullmatch(r'/dev/[A-Za-z0-9_.-]+', device):
            raise RuntimeError(f'XFS block device: FAIL ({device})')

        stage = ('set -eu; '
                 'tar -tzf /input/archive.tar.gz >/dev/null; '
                 'tar -xzf /input/archive.tar.gz -C /parent/_data '
                 '--strip-components=1 --numeric-owner --acls --xattrs; '
                 'cp -- /input/archive.tar.gz "/backup/$1"; '
                 'mkdir -p /tmp/empty/owners; '
                 'tar -czf "/backup/$2" -C /tmp/empty owners; '
                 'cd /backup; sha256sum "$1" > "$1.sha256"; '
                 'sha256sum "$2" > "$2.sha256"')
        staged = container('--rm', '--user', '0', '--network', 'none',
                           '-v', f'{archive}:/input/archive.tar.gz:ro',
                           '-v', f"{names['parent']}:/parent", '-v', f"{names['backup']}:/backup",
                           '--entrypoint', 'sh', image, '-ec', stage, 'sh',
                           DATA_ARCHIVE, BOX_ARCHIVE, check=False)
        if staged.returncode:
            raise RuntimeError(f'archive staging: FAIL (rc={staged.returncode})')
        probe = container('--rm', '--user', '0', '--network', 'none',
                          '-v', f"{names['data']}:/data:ro", '--entrypoint', 'python', image,
                          '-B', '-m', 'tinyassets.box_layout_migration', 'check', '/data',
                          check=False)
        if probe.returncode != 10:
            raise RuntimeError(f'archive owner-split census: FAIL (rc={probe.returncode})')
        planned = container('--rm', '--user', '0', '--network', 'none',
                            '-v', f"{names['data']}:/data:ro", '--entrypoint', 'python',
                            image, '-c', ARCHIVE_PLAN, check=False)
        if planned.returncode:
            raise RuntimeError(f'archive owner plan: FAIL (rc={planned.returncode})')
        owners = json.loads(planned.stdout)
        if not owners:
            raise RuntimeError('archive has zero admitted owners; move cannot be rehearsed')
        print(f'archive={archive} owners={len(owners)} {"failed-move" if fail else "commit"}: PASS')

        if fail:
            target = next((owner for owner in owners[1:] if owner['centers']), None)
            if target is None:
                target = next((owner for owner in owners if owner['centers']), None)
            if target is None:
                raise RuntimeError('archive has no admitted centers for a failed-move fixture')
            center = target['centers'][0]
            injected = container('--rm', '--user', '0', '--network', 'none',
                                 '-v', f"{names['data']}:/data", '--entrypoint', 'python',
                                 image, '-c', ARCHIVE_FAILURE, str(target['uid']), center,
                                 check=False)
            if injected.returncode:
                raise RuntimeError(f'failure fixture path=/data/{center}/.oauth-refresh: FAIL')

        # Exactly deploy_fail_safe.sh:box_migration_container apply, with its
        # block-device mapping and the same resource/security posture.
        apply = container('--rm', '--cap-drop', 'ALL', '--user', '0',
                          '--cap-add', 'CHOWN', '--cap-add', 'FOWNER', '--cap-add', 'FSETID',
                          '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SYS_ADMIN',
                          '--security-opt', 'no-new-privileges=true', '--network', 'none',
                          '--memory=1g', '--memory-swap=1g',
                          '-v', f"{names['data']}:/data", '-v', f"{names['backup']}:/backup:ro",
                          '--device', f'{device}:{device}:r',
                          '--mount', f'type=bind,src={store_path},dst=/box-store',
                          '--entrypoint', 'python', image, '-B', '-m',
                          'tinyassets.box_layout_migration', 'apply', '/data',
                          '--store', '/box-store', '--backup', f'/backup/{DATA_ARCHIVE}',
                          '--box-backup', f'/backup/{BOX_ARCHIVE}', check=False)
        refused = apply.returncode != 0
        expected = 3 if fail else 0
        print(f'apply rc={apply.returncode} {"PASS" if apply.returncode == expected else "FAIL"}')
        if refused:
            reason = apply.stderr.strip()
            if reason.startswith('box layout migration refused: ') and '\n' not in reason:
                print(f'apply refusal={json.dumps(reason, ensure_ascii=True)}')
        report = container('--rm', '--user', '0', '--network', 'none',
                           '-v', f"{names['data']}:/data:ro",
                           '--mount', f'type=bind,src={store_path},dst=/box-store,readonly',
                           '--entrypoint', 'python', image, '-c', ARCHIVE_REPORT,
                           'failed' if refused else 'commit', check=False)
        print(report.stdout, end='')
        if apply.returncode != expected:
            raise RuntimeError(f'apply: FAIL (rc={apply.returncode}, expected={expected})')
        if fail:
            fixture_error = f'non-regular credential source: /data/{center}/.oauth-refresh'
            if fixture_error not in apply.stderr:
                raise RuntimeError(
                    'apply: FAIL (refusal did not reach the injected credential path)')
        if not fail and report.returncode:
            raise RuntimeError(f'owner commit verification: FAIL (rc={report.returncode})')
        if not fail:
            knowledge = container('--rm', '--user', '0', '--network', 'none',
                                  '-v', f"{names['data']}:/data:ro",
                                  '--mount', f'type=bind,src={store_path},dst=/box-store,readonly',
                                  '--entrypoint', 'python', image, '-c', KNOWLEDGE_VERIFY,
                                  check=False)
            print(knowledge.stdout, end='')
            if knowledge.returncode:
                raise RuntimeError(
                    f'owner knowledge verification: FAIL (rc={knowledge.returncode})')
            quotas = container('--rm', '--cap-drop', 'ALL', '--user', '0',
                               '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SYS_ADMIN',
                               '--device', f'{device}:{device}:r',
                               '--network', 'none', '-v', f"{names['data']}:/data:ro",
                               '--mount', f'type=bind,src={store_path},dst=/box-store',
                               '--entrypoint', 'python', image, '-c', QUOTA_READBACK, 'set',
                               check=False)
            print(quotas.stdout, end='')
            if quotas.returncode:
                raise RuntimeError(f'owner project read-back: FAIL (rc={quotas.returncode}) '
                                   + quotas.stderr)

        if fail:
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
export BACKUP_VOLUME=rehearsal-data BOX_STORE_DIR=/box-store
export BACKUP_LOG=/tmp/c6-restore.log
bash /rehearsal-deploy/backup-restore.sh
'''
            restored = container('--rm', '--privileged', '--user', '0', '--network', 'none',
                                 '-v', f"{names['parent']}:/parent",
                                 '-v', f"{names['backup']}:/backup:ro",
                                 '--mount', f'type=bind,src={store_path},dst=/box-store',
                                 '--mount', f'type=bind,src={ROOT / "deploy"},'
                                            'dst=/rehearsal-deploy,readonly',
                                 '--entrypoint', 'sh', image, '-ec', shim, check=False)
            if restored.returncode:
                raise RuntimeError(f'backup restore: FAIL (rc={restored.returncode})')
            checked = container('--rm', '--user', '0', '--network', 'none',
                                '-v', f"{names['parent']}:/parent:ro",
                                '--mount', f'type=bind,src={store_path},dst=/box-store,readonly',
                                '--entrypoint', 'python', image, '-c', ARCHIVE_RESTORE_CHECK,
                                check=False)
            print(checked.stdout, end='')
            if checked.returncode:
                raise RuntimeError(f'restore verification: FAIL (rc={checked.returncode})')
            retained_quotas = json.dumps({str(row['uid']): row['quota'] for row in owners})
            cleared = container('--rm', '--cap-drop', 'ALL', '--user', '0',
                                '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SYS_ADMIN',
                                '--device', f'{device}:{device}:r', '--network', 'none',
                                '--mount', f'type=bind,src={store_path},dst=/box-store',
                                '--entrypoint', 'python', image, '-c', QUOTA_READBACK, 'retained',
                                retained_quotas,
                                check=False)
            print(cleared.stdout, end='')
            if cleared.returncode:
                raise RuntimeError(f'retained project read-back: FAIL (rc={cleared.returncode}) '
                                   + cleared.stderr)
            compared = container('--rm', '--user', '0', '--network', 'none',
                                 '-v', f"{names['parent']}:/parent:ro",
                                 '-v', f"{names['backup']}:/backup:ro",
                                 '--entrypoint', 'tar', image, '-dzf', f'/backup/{DATA_ARCHIVE}',
                                 '-C', '/parent', '--numeric-owner', '--acls', '--xattrs',
                                 check=False)
            differences = compared.stdout.splitlines() + compared.stderr.splitlines()
            # backup-restore.sh deliberately gives the staged _data root the
            # live volume root's mode and ownership after extraction.
            root_attributes = re.compile(r'^_data: (?:Mode|Uid|Gid) differs$')
            unexpected = [line for line in differences if not root_attributes.fullmatch(line)]
            if unexpected or (compared.returncode and not differences):
                for line in unexpected:
                    print(f'  archive difference={json.dumps(line, ensure_ascii=True)}')
                raise RuntimeError('restored archive comparison: FAIL')
            print('restored archive comparison: PASS')
    finally:
        cleanup_errors = []
        for name in reversed(containers):
            removed = docker('rm', '-f', name, check=False)
            if removed.returncode and 'No such container' not in removed.stderr:
                cleanup_errors.append(f'container={name}')
        if mounted:
            unmount = docker('run', '--rm', '--privileged', '--pid=host', '--user', '0',
                             '--entrypoint', 'sh', image, '-ec',
                             'nsenter -t 1 -m umount "$1"; '
                             'nsenter -t 1 -m losetup -d "$2" 2>/dev/null || true; '
                             'test -z "$(nsenter -t 1 -m losetup -j "$3")"',
                             'sh', host_store, device, host_image, check=False)
            if unmount.returncode:
                cleanup_errors.append(f'loop device={device}')
        for name in reversed(created):
            removed = docker('volume', 'rm', name, check=False)
            if removed.returncode:
                cleanup_errors.append(f'volume={name}')
        if cleanup_errors:
            raise RuntimeError(f'cleanup: FAIL ({", ".join(cleanup_errors)})')


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
        planned = docker('run', '--rm', '--user', '0', '--network', 'none',
                         '-v', f"{names['data']}:/data:ro", '--entrypoint', 'python',
                         image, '-c', ARCHIVE_PLAN)
        owners = json.loads(planned.stdout)

        # Keep this argv in lockstep with deploy_fail_safe.sh:box_migration_container.
        apply = docker('run', '--rm', '--cap-drop', 'ALL', '--user', '0',
                       '--cap-add', 'CHOWN', '--cap-add', 'FOWNER', '--cap-add', 'FSETID',
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
            report = json.loads(apply.stdout.strip().splitlines()[-1])
            assert report['knowledge_owners']['alice']['status'] == 'converted', report
            assert report['knowledge_centers']['command-center']['status'] == 'converted', report
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
            retained_quotas = json.dumps({str(row['uid']): row['quota'] for row in owners})
            cleared = docker('run', '--rm', '--cap-drop', 'ALL', '--user', '0',
                             '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SYS_ADMIN',
                             '--device', f'{device}:{device}:r', '--network', 'none',
                             '--mount', f'type=bind,src={store_path},dst=/box-store',
                             '--entrypoint', 'python', image, '-c', QUOTA_READBACK, 'retained',
                             retained_quotas)
            print('[rehearsal] ' + cleared.stdout.strip())
        else:
            assert apply.returncode == 0, apply.stdout + apply.stderr
            report = docker('run', '--rm', '--user', '0', '--network', 'none',
                            '-v', f"{names['data']}:/data:ro",
                            '--mount', f'type=bind,src={store_path},dst=/box-store,readonly',
                            '--entrypoint', 'python', image, '-c', ARCHIVE_REPORT,
                            'commit', check=False)
            print('[rehearsal] ' + report.stdout.strip())
            assert report.returncode == 0, report.stderr
            quotas = docker('run', '--rm', '--cap-drop', 'ALL', '--user', '0',
                            '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SYS_ADMIN',
                            '--device', f'{device}:{device}:r',
                            '--network', 'none', '-v', f"{names['data']}:/data:ro",
                            '--mount', f'type=bind,src={store_path},dst=/box-store',
                            '--entrypoint', 'python', image, '-c', QUOTA_READBACK, 'set')
            print('[rehearsal] ' + quotas.stdout.strip())
            verified = docker('run', '--rm', '--user', '0', '--network', 'none',
                              '-v', f"{names['data']}:/data",
                              '--mount', f'type=bind,src={store_path},dst=/box-store',
                              '--entrypoint', 'python', image, '-c', VERIFY_COMMIT)
            print('[rehearsal] ' + verified.stdout.strip())
            knowledge = docker('run', '--rm', '--user', '0', '--network', 'none',
                               '-v', f"{names['data']}:/data:ro",
                               '--mount', f'type=bind,src={store_path},dst=/box-store,readonly',
                               '--entrypoint', 'python', image, '-c', KNOWLEDGE_VERIFY)
            print('[rehearsal] ' + knowledge.stdout.strip())
            # Take a committed owner-box generation and restore it over the
            # same live boxes. This exercises project clearing on retained
            # trees and reapplication on every restored owner.
            docker('run', '--rm', '--user', '0', '--network', 'none',
                   '-v', f"{names['parent']}:/parent:ro",
                   '-v', f"{names['backup']}:/backup", '--entrypoint', 'tar', image,
                   '--numeric-owner', '--acls', '--xattrs', '-czf',
                   f'/backup/{DATA_ARCHIVE}', '-C', '/parent', '_data')
            docker('run', '--rm', '--user', '0', '--network', 'none',
                   '-v', f"{names['backup']}:/backup",
                   '--mount', f'type=bind,src={store_path},dst=/box-store,readonly',
                   '--entrypoint', 'tar', image,
                   '--numeric-owner', '--acls', '--xattrs', '-czf',
                   f'/backup/{BOX_ARCHIVE}', '-C', '/box-store',
                   '--transform=flags=rh;s,^,owners/,',
                   *(str(row['uid']) for row in owners))
            docker('run', '--rm', '--user', '0', '--network', 'none',
                   '-v', f"{names['backup']}:/backup",
                   '--mount', f'type=bind,src={ROOT / "deploy"},'
                              'dst=/rehearsal-deploy,readonly',
                   '--entrypoint', 'python', image, '/rehearsal-deploy/backup-pair.py',
                   'create', f'/backup/{DATA_ARCHIVE}', f'/backup/{BOX_ARCHIVE}',
                   f'/backup/{PAIR_MANIFEST}')
            shim = r'''set -eu
mkdir -p /tmp/c6-bin
printf '#!/bin/sh\ncase "$1 $2" in\n' > /tmp/c6-bin/docker
printf '"volume inspect") echo /parent/_data;;\n' >> /tmp/c6-bin/docker
printf '"ps -q") exit 0;; *) exit 1;; esac\n' >> /tmp/c6-bin/docker
printf '#!/bin/sh\nexit 0\n' > /tmp/c6-bin/systemctl
chmod +x /tmp/c6-bin/docker /tmp/c6-bin/systemctl
mkdir -p /opt/tinyassets/boxhostd/current
printf '#!/bin/sh\necho "[]"\n' > /opt/tinyassets/boxhostd/current/runsc
chmod +x /opt/tinyassets/boxhostd/current/runsc
export PATH=/tmp/c6-bin:$PATH
export BACKUP_FILE=/backup/''' + DATA_ARCHIVE + r'''
export BOX_BACKUP_FILE=/backup/''' + BOX_ARCHIVE + r'''
export BACKUP_VOLUME=rehearsal-data BOX_STORE_DIR=/box-store
export BACKUP_LOG=/tmp/c6-restore.log
bash /rehearsal-deploy/backup-restore.sh
'''
            restored = docker('run', '--rm', '--privileged', '--user', '0',
                              '--network', 'none', '-v', f"{names['parent']}:/parent",
                              '-v', f"{names['backup']}:/backup:ro",
                              '--mount', f'type=bind,src={store_path},dst=/box-store',
                              '--mount', f'type=bind,src={ROOT / "deploy"},'
                                         'dst=/rehearsal-deploy,readonly',
                              '--entrypoint', 'sh', image, '-ec', shim)
            print('[rehearsal] committed owner-box restore: '
                  + restored.stdout.strip().splitlines()[-1])
            restored_quotas = docker('run', '--rm', '--cap-drop', 'ALL', '--user', '0',
                                    '--cap-add', 'DAC_OVERRIDE', '--cap-add', 'SYS_ADMIN',
                                    '--device', f'{device}:{device}:r', '--network', 'none',
                                    '-v', f"{names['data']}:/data:ro",
                                    '--mount', f'type=bind,src={store_path},dst=/box-store',
                                    '--entrypoint', 'python', image, '-c', QUOTA_READBACK, 'set')
            print('[rehearsal] restored ' + restored_quotas.stdout.strip())
            restored_modes = docker('run', '--rm', '--user', '0', '--network', 'none',
                                    '-v', f"{names['data']}:/data:ro",
                                    '--mount', f'type=bind,src={store_path},'
                                               'dst=/box-store,readonly',
                                    '--entrypoint', 'python', image, '-c', VERIFY_COMMIT)
            print('[rehearsal] restored ' + restored_modes.stdout.strip())
            restored_knowledge = docker('run', '--rm', '--user', '0', '--network', 'none',
                                        '-v', f"{names['data']}:/data:ro",
                                        '--mount', f'type=bind,src={store_path},'
                                                   'dst=/box-store,readonly',
                                        '--entrypoint', 'python', image,
                                        '-c', KNOWLEDGE_VERIFY)
            print('[rehearsal] restored ' + restored_knowledge.stdout.strip())
            cleared = docker('run', '--rm', '--user', '0', '--network', 'none',
                             '--mount', f'type=bind,src={store_path},dst=/box-store,readonly',
                             '--entrypoint', 'python', image, '-c', QUOTA_READBACK, 'clear')
            print('[rehearsal] ' + cleared.stdout.strip())
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
    parser.add_argument('--archive', type=Path,
                        help='absolute path to a tinyassets-data-*.tar.gz full backup')
    args = parser.parse_args()
    archive_bytes = None
    if args.archive is not None:
        try:
            archive_bytes = validate_archive(args.archive)
        except ValueError as exc:
            parser.error(str(exc))
    if docker('info', '--format', '{{.OSType}}').stdout.strip() != 'linux':
        raise SystemExit('rehearsal requires a Linux Docker engine')
    if not args.no_build:
        build = subprocess.run(['docker', 'build', '-q', '-t', args.image, str(ROOT)],
                               capture_output=True, text=True)
        if build.returncode:
            raise RuntimeError(f'daemon image build: FAIL (rc={build.returncode})')
    if args.archive is None:
        rehearsal(args.image, fail=False)
        rehearsal(args.image, fail=True)
    else:
        assert archive_bytes is not None
        store_bytes = max(1024**3, int(archive_bytes * 1.25) + 512 * 1024**2)
        archive_rehearsal(args.image, args.archive, fail=False, store_bytes=store_bytes)
        archive_rehearsal(args.image, args.archive, fail=True, store_bytes=store_bytes)


def _terminate(_signum: int, _frame: object) -> None:
    raise KeyboardInterrupt


if __name__ == '__main__':
    signal.signal(signal.SIGTERM, _terminate)
    try:
        main()
    except KeyboardInterrupt:
        print('rehearsal: FAIL (interrupted)', file=sys.stderr)
        raise SystemExit(130) from None
    except (RuntimeError, OSError, subprocess.CalledProcessError) as exc:
        print(f'rehearsal: FAIL ({exc})', file=sys.stderr)
        raise SystemExit(1) from None
