#!/usr/bin/env python3
"""Rehearse the final owner cutover; reports contain counts and fixed labels only."""

import re
import sys
import tarfile
from pathlib import Path

ARCHIVE_NAME = re.compile(r"tinyassets-data-\d{4}-\d{2}-\d{2}T\d{2}-\d{2}-\d{2}Z\.tar\.gz\Z")


def validate_archive(path: Path) -> int:
    """Apply backup-restore.sh's member rules before mounting or extracting."""
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("--archive requires an absolute readable non-symlink regular file")
    if not ARCHIVE_NAME.fullmatch(path.name):
        raise ValueError("--archive requires a tinyassets-data-YYYY-MM-DDTHH-MM-SSZ.tar.gz file")
    total = 0
    try:
        with tarfile.open(path, "r:gz") as archive:
            members = archive.getmembers()
            if not members:
                raise ValueError("archive is empty")
            symlinks = {member.name.rstrip("/") for member in members if member.issym()}
            for member in members:
                name = member.name.rstrip("/") if member.isdir() else member.name
                parts = name.split("/")
                if (
                    name.startswith("/")
                    or not parts
                    or parts[0] != "_data"
                    or any(part in ("", ".", "..") for part in parts)
                ):
                    raise ValueError(f"unsafe archive path: {name!r}")
                if len(parts) == 1 and not member.isdir():
                    raise ValueError(f"archive root is not a directory: {name!r}")
                if any("/".join(parts[:index]) in symlinks for index in range(1, len(parts))):
                    raise ValueError(f"archive descends through a symlink: {name!r}")
                if member.islnk():
                    target = member.linkname.split("/")
                    if (
                        member.linkname.startswith("/")
                        or not target
                        or target[0] != "_data"
                        or ".." in target
                        or member.linkname in symlinks
                    ):
                        raise ValueError(f"unsafe archive hardlink: {name!r}")
                elif not (member.isfile() or member.isdir() or member.issym()):
                    raise ValueError(f"unsupported archive member type: {name!r}")
                total += member.size if member.isfile() else 0
    except (OSError, tarfile.TarError) as exc:
        raise ValueError(f"archive unreadable: {type(exc).__name__}") from exc
    return total


def rehearsal_manifests():
    """Compare all content and authority across independent migration trials.

    C2 creates new opaque snapshots in each trial. Validate those objects and
    compare their logical bindings, preserving every other identity value and
    schema object. Snapshot publication clocks must be recent in each trial;
    their exact wall-clock values, like UUIDs, are not owner content.
    """
    import gzip
    import hashlib
    import json
    import sqlite3
    import time
    from contextlib import closing

    root = Path('/data')
    journal = json.loads((root / '.owner-cutover.json').read_text())
    shared = journal['final_manifest']
    relative = '.broker/state/owner-identities.db'
    with closing(sqlite3.connect(root / relative)) as db:
        schema = db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master '
                            'ORDER BY name').fetchall()
        bindings = db.execute('SELECT principal,uid,snapshot_id,snapshot_sha256,'
                              'work,local,asleep_at '
                              'FROM owner_volumes ORDER BY principal').fetchall()
        for owner, uid, snapshot, digest, work, local, asleep_at in bindings:
            assert work is None
            assert local == 1
            assert isinstance(asleep_at, (float, int)) and 0 <= time.time() - asleep_at < 7 * 86400
            assert journal['snapshots'][str(uid)] == snapshot
            with gzip.open(Path('/backup/owner-snapshots') / str(uid) /
                           (snapshot + '.img.gz'), 'rb') as stream:
                assert hashlib.file_digest(stream, 'sha256').hexdigest() == digest
            assert db.execute('SELECT snapshot_sha256 FROM owner_snapshots '
                              'WHERE principal=? AND snapshot_id=?',
                              (owner, snapshot)).fetchall() == [(digest,)]
        tables = {}
        for kind, name, _, _ in schema:
            if kind != 'table':
                continue
            cursor = db.execute('SELECT * FROM "' + name.replace('"', '""') + '"')
            columns = [column[0] for column in cursor.description]
            rows = cursor.fetchall()
            if name == 'owner_box_secrets':
                # C5 creates one durable random credential at admission. Each
                # independent restored trial must create its own credentials.
                import re

                owners = {row[0] for row in db.execute('SELECT principal FROM owner_identities')}
                assert {row[0] for row in rows} == owners
                assert len({row[1] for row in rows}) == len(rows)
                assert all(re.fullmatch(r'[A-Za-z0-9_-]{43}', row[1]) for row in rows)
                rows = [(owner, 'verified unique ingress credential') for owner, _ in rows]
            if name in {'owner_volumes', 'owner_snapshots'}:
                rows = [tuple('verified snapshot' if column in {'snapshot_id', 'snapshot_sha256'}
                              else 'recent snapshot publication' if column == 'asleep_at'
                              else value for column, value in zip(columns, row)) for row in rows]
            tables[name] = sorted(rows, key=repr)
    shared[relative] = {'schema': schema, 'tables': tables}
    return [shared, journal['owner_manifests']]


def platform_recovery_probe():
    """Recover shared credentials only; C3's missing deletion authority stays closed."""
    import hashlib
    import json
    import os
    import shutil
    import sqlite3
    import subprocess
    import tempfile
    import time
    from contextlib import closing

    from tinyassets.api_keys import _KEY_RE, ApiKeyStore
    from tinyassets.onboarding import owner_sessions
    from tinyassets.restore_recovery import RecoveryUnavailable, platform_recovery
    from tinyassets.router_credentials import HookOwners
    from tinyassets.router_traffic import admits

    root, store = Path('/data'), Path('/box-store')
    os.environ['TINYASSETS_DATA_DIR'] = str(root)
    owner = json.loads((root / '.owner-cutover.json').read_text())['plan'][0]['principal']
    # Seed through the service UID that owns these live databases. Root's
    # SQLite O_CREAT is correctly refused for service files under a sticky root.
    seed = """
import hashlib, json, sys
from pathlib import Path
from tinyassets.api_keys import ApiKeyStore
from tinyassets.broker.supervisor import BrokerSupervisor
from tinyassets.onboarding import owner_sessions
from tinyassets.router_credentials import HookOwners
root, owner = Path('/data'), sys.argv[1]
supervisor = BrokerSupervisor.from_boxhost(
    root, socket_path=Path(sys.argv[2]), proof='synthetic-rehearsal-proof' * 2)
supervisor.start()
sys.path.insert(0, '/support')
from rehearsal_serve import router_snapshot_call

def execute():
    key = ApiKeyStore(root).mint(owner, name='synthetic platform recovery',
                               scopes=[], authority=None)['key']
    HookOwners(root).publish(hashlib.sha256(b'rehearsal-hook').hexdigest(), owner)
    with owner_sessions.store() as db:
        db.execute('INSERT INTO owner_sessions VALUES (?,?,?)',
                   (owner_sessions.hashed('rehearsal-session'),
                    json.dumps({'user_id': owner}), 4000000000))
    assert ApiKeyStore(root).resolve(key) is not None
    assert HookOwners(root).resolve('rehearsal-hook') == owner
    assert owner_sessions.lookup('rehearsal-session') is not None
    return key
key = router_snapshot_call(owner, execute)
print(json.dumps(key))
"""
    with tempfile.TemporaryDirectory(prefix='/tmp/s6-recovery-') as directory:
        state = Path(directory)
        state.chmod(0o755)
        os.chown(state, 1002, 1002)
        with tempfile.TemporaryFile() as output:
            broker = subprocess.Popen(
                [sys.executable, '/support/rehearsal_serve.py', 'broker', str(state)],
                stdout=output, stderr=output)
            try:
                deadline = time.monotonic() + 30
                while not (state / 'broker.sock').exists():
                    if broker.poll() is not None or time.monotonic() >= deadline:
                        output.seek(0)
                        raise RuntimeError(output.read().decode(errors='replace'))
                    time.sleep(.05)
                result = subprocess.run(
                    ['setpriv', '--reuid=1001', '--regid=1001',
                     '--groups=1100,1101,1102', '--bounding-set=-all', '--no-new-privs',
                     sys.executable, '-c', seed, owner, str(state / 'broker.sock')],
                    capture_output=True, text=True)
                assert result.returncode == 0, result.stderr
                key = json.loads(result.stdout)
            finally:
                broker.terminate()
                broker.wait(15)
    key_id, secret = _KEY_RE.fullmatch(key).groups()
    with closing(sqlite3.connect((root / '.api-keys.sqlite3').as_uri() + '?mode=ro')) as db:
        assert db.execute('SELECT owner,secret_sha256,revoked_at FROM api_keys WHERE key_id=?',
                          (key_id,)).fetchone() == (
                              owner, hashlib.sha256(secret.encode()).hexdigest(), None)

    def images():
        # Opaque volume objects are the lifecycle unit; never attach or inspect content.
        hashes = {}
        for path in store.glob('*.img'):
            with path.open('rb') as stream:
                hashes[path.name] = hashlib.file_digest(stream, 'sha256').hexdigest()
        return hashes

    before = images()
    staged = Path('/backup/platform-stage')
    shutil.copytree(root, staged)
    os.environ['TINYASSETS_DATA_DIR'] = str(staged)
    try:
        platform_recovery(staged, backup_time=1)
    except RecoveryUnavailable as error:
        assert 'deletion-job recovery is not implemented' in str(error)
    else:
        raise AssertionError('missing lane-b authority reopened platform')
    assert not admits(staged, owner)
    assert json.loads((staged / '.layout.json').read_text())['state'] == 'recovering'
    assert ApiKeyStore(staged).resolve(key) is None
    assert HookOwners(staged).resolve('rehearsal-hook') is None
    assert owner_sessions.lookup('rehearsal-session') is None
    # An unreadable store must never masquerade as successful invalidation.
    for relative, table in (('.api-keys.sqlite3', 'api_keys'),
                            ('.hook-owners.sqlite3', 'hook_owners'),
                            ('.runtime/owner-sessions.db', 'owner_sessions')):
        with closing(sqlite3.connect((staged / relative).as_uri() + '?mode=ro')) as db:
            assert db.execute('SELECT COUNT(*) FROM ' + table).fetchone() == (0,)
    assert images() == before
    return {'unchanged_volumes': len(before), 'stale_credentials': 3,
            'admission_closed': 1, 'lane_b_pending_refusals': 1}


def archive_rehearsal(image, archive, *, store_bytes):
    from scripts.archive_rehearsal import archive_rehearsal as rehearse

    return rehearse(image, archive, store_bytes=store_bytes)


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from scripts.archive_rehearsal import main

    main()
