"""D9 preplanted-reader acceptance against an immutable production image.

Synthetic owners only; no mounts, network, live data or launch-policy changes.
Exit 3 with a non-empty failures summary means a reader returned the foreign
sentinel; setup/assertion failures are not that evidence. This is a reader
probe, not proof that a particular engine can plant the alias after migration.
"""
from __future__ import annotations

import argparse
import json
import subprocess

CONTAINER = r'''
import json, os, runpy, sys, tempfile
from pathlib import Path
launcher = runpy.run_path('/usr/local/libexec/ta-launch.py')
launcher['retire_migration_authority']()
launcher['retire_child']('daemon')
assert os.getresuid() == (1001, 1001, 1001)
assert os.getresgid() == (1001, 1001, 1001)
assert os.getgroups() == [1100, 1101, 1102]
sys.path.insert(0, '/app')
root = Path(tempfile.mkdtemp(prefix='role-reader-alias-'))
os.environ['TINYASSETS_DATA_DIR'] = str(root)
from tinyassets.api.helpers import _read_platform_text
from tinyassets.api.universe import _action_inspect_universe
from tinyassets.api.universe_file_reads import _read as api_file_read
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import ensure_universe_registered, grant_universe_access
from tinyassets.universe_files import read_universe_file
for owner in ('alice', 'bob'):
    directory = root / owner
    directory.mkdir()
    ensure_universe_registered(root, universe_id=owner, universe_path=directory)
    grant_universe_access(root, universe_id=owner, actor_id=owner,
                          permission='admin', granted_by=owner)
sentinel = b'BOB-PRIVATE-SYNTHETIC-SENTINEL'
foreign = root / 'bob' / 'private.txt'
foreign.write_bytes(sentinel)
foreign.chmod(0o600)
original = foreign.stat()
target = root / 'alice' / 'activity.log'
failures = []
with identity_context(Identity('alice', 'alice')):
    denied_bob = json.loads(_action_inspect_universe(universe_id='bob'))
    assert 'error' in denied_bob, 'foreign metadata preflight was not denied'
    target.write_text('ALICE-POSITIVE-CONTROL')
    own = json.loads(_action_inspect_universe(universe_id='alice'))
    assert own.get('recent_activity') == ['ALICE-POSITIVE-CONTROL'], own
    target.unlink()
    for relpath in ('activity.log', 'workspace/record.txt', 'wiki/page.md',
                    'canon/record.md', 'output/record.md', 'logs/run.log'):
        target = root / 'alice' / relpath
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text('ALICE-POSITIVE-CONTROL')
        assert read_universe_file(root / 'alice', relpath) == b'ALICE-POSITIVE-CONTROL'
        assert api_file_read(root, 'alice', relpath) == b'ALICE-POSITIVE-CONTROL'
        assert _read_platform_text(target, '', 'strict') == 'ALICE-POSITIVE-CONTROL'
        target.unlink()
        for plant in ('symlink', 'fifo', 'hardlink', 'retired-hardlink'):
            if plant == 'symlink':
                target.symlink_to(foreign)
            elif plant == 'fifo':
                os.mkfifo(target)
            else:
                os.link(foreign, target)
                if plant == 'retired-hardlink':
                    # Normal deletion/replacement of Bob's original name
                    # leaves Alice's alias with nlink=1, still Bob's bytes.
                    foreign.unlink()
            readers = {
                'universe-file': lambda: read_universe_file(root / 'alice', relpath),
                'platform-text': lambda: _read_platform_text(target, '', 'strict').encode(),
                'api-file-read': lambda: api_file_read(root, 'alice', relpath),
            }
            if relpath == 'activity.log':
                readers['inspect-universe'] = lambda: _action_inspect_universe(
                    universe_id='alice').encode()
            try:
                for name, read in readers.items():
                    try:
                        answer = read()
                    except OSError:
                        outcome = 'DENIED'
                    else:
                        outcome = 'FOREIGN_BYTES' if sentinel in answer else 'NO_FOREIGN_BYTES'
                    print(json.dumps(dict(path=relpath, plant=plant, reader=name,
                                          outcome=outcome)), flush=True)
                    if outcome == 'FOREIGN_BYTES':
                        failures.append(relpath + ':' + plant + ':' + name)
            finally:
                if plant == 'retired-hardlink':
                    os.link(target, foreign)
                target.unlink()
assert foreign.read_bytes() == sentinel
after = foreign.stat()
assert (after.st_uid, after.st_gid, after.st_mode, after.st_mtime_ns) == (
    original.st_uid, original.st_gid, original.st_mode, original.st_mtime_ns)
status = launcher['status']()
assert all(int(status[key], 16) == 0 for key in launcher['CAP_FIELDS'])
print(json.dumps(dict(uid=os.getuid(), groups=os.getgroups(), capabilities='zero',
                     nnp=int(status['NoNewPrivs']), foreign_unchanged=True,
                     positive_control=True, foreign_metadata_denied=True,
                     failures=failures)), flush=True)
raise SystemExit(3 if failures else 0)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(
        ['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print(json.dumps({'image': digest, 'command': command}), flush=True)
    return subprocess.run(command, input=CONTAINER, text=True, timeout=60).returncode


if __name__ == '__main__':
    raise SystemExit(main())
