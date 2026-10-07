"""DA7 through the REAL U2 startup coordinator: restart matrix, reverse, legacy forward.

Every boot is a fresh production-image container on one named volume whose
PID1 runs ``ta-role-start.py``'s own ``boot()`` (the ``start`` mode minus
serving): the forward migration with the admission contract (staging sweep,
log read through a retired broker child, reconcile, adopt/seed, alarms,
``volume.json``), the volume/metadata/owner phases, then D60's bootstrap with
the reconciled bindings and generation. PID1 then continues as the
capability-free daemon and drives real runtime admission (admit_center,
ensure_founder_home), real decoder cells and D218's real two-pass deletion
through the mapper (pass one, daemon pass, retire, finish).

After every boot a separate root container runs a D59-style matrix as each
owner's real host identity against every other owner's paths: zero foreign
bytes and zero mutations. Reverse runs the real ``reverse`` mode; the legacy
step runs the OLD production image's own code as UID1001 on the reversed
volume. Synthetic state only; no network; startup stays OFF by default.

    python scripts/role_admission_startup_probe.py --image IMAGE --old-image OLD
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid

from role_startup_probe import ENVIRONMENT, LEGACY

CAPS = ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'KILL', 'SETGID', 'SETUID', 'SETPCAP')
SECURITY = ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
            'systempaths=unconfined')

BOOT = r'''
import io, json, os, runpy, shutil, sys, traceback
from pathlib import Path
sys.path.insert(0, '/app')
BOOT = os.environ['BOOT']
data = Path('/data'); STATE = data / '.role-owner-migration'
startup = runpy.run_path('/usr/local/libexec/ta-role-start.py')
assert startup['enabled']()
launch = startup['_load']('launch')
concerns = sorted(p.name for p in (STATE / 'admission-concerns').iterdir()) if (
    STATE / 'admission-concerns').is_dir() else []
PRE
try:
    report, bindings, (supervisor, client) = startup['boot'](launch)
except Exception as exc:  # still root: report and exit before anything else runs
    print(json.dumps(dict(boot=BOOT, refused=str(exc))), flush=True)
    EXPECT_REFUSAL(str(exc))
    os._exit(0)
boot = dict(boot=BOOT, principals=report['principals'], missing=report['missing'],
            generation=report['generation'], alarms=report['alarms'], admits=report['admits'],
            swept=report['swept'], bindings=report['bindings'], concerns=concerns)
os.environ['TINYASSETS_DATA_DIR'] = str(data)
os.environ['TINYASSETS_CREDENTIAL_BROKER'] = 'process'
assert os.getpid() == 1 and os.getuid() == 1001
assert all(int(launch['status']()[k], 16) == 0 for k in launch['CAP_FIELDS'])
assert sorted(bindings.values()) == sorted(report['bindings'].values())
from PIL import Image
from tinyassets import role_center_admission, role_decoder, role_owner_delete
from tinyassets import role_owner_tree_deletion as d218
from tinyassets.api.first_contact import ensure_founder_home
from tinyassets.auth.middleware import auth_middleware, identity_context, set_provider
from tinyassets.auth.provider import AuthProvider, Identity
from tinyassets.broker import owner_identities
from tinyassets.daemon_server import get_founder_home, grant_universe_access
from tinyassets.owner_launcher_client import OwnerLaunchRefused
out = io.BytesIO(); Image.new('RGB', (8, 8), 'blue').save(out, format='PNG'); PNG = out.getvalue()
USER
def create(owner, center):
    grant_universe_access(data, universe_id=center, actor_id=owner, permission='admin',
                          granted_by=owner)
    role_center_admission.admit_center(data, principal=owner, center=center)
def decode(owner, center):
    with user(owner):
        done = role_decoder.decode(PNG, 'image/png', data / center)
    return done.returncode == 0 and done.cell['caps'] == 'zero'
def refused(action):
    try: action()
    except (OwnerLaunchRefused, OSError, RuntimeError, PermissionError): return True
    return False
def signup(owner):
    with user(owner):
        return ensure_founder_home(data, owner)
def home(owner):
    return get_founder_home(data, owner)
def delete(owner, center):
    """D218 through the mapper: pass one, daemon pass, retire, finish."""
    with user(owner):
        return d218.delete_center(data, center, principal=owner)
def crash_inside(module, name):
    def die(*a, **k):
        os._exit(0)  # the container dies mid-step
    setattr(module, name, die)
def report_ok(**values):
    print(json.dumps(dict(boot, **values, ok=True)), flush=True)
try:
STEP
except BaseException:
    traceback.print_exc()
    print(json.dumps(dict(boot, ok=False)), flush=True)
    os._exit(1)
os._exit(0)
'''

USER = r'''
def user(owner):
    ident = Identity(owner, owner, capabilities=['read', 'write', 'costly', 'submit_request',
                                                 'list'])
    class Signed(AuthProvider):
        def resolve_token(self, token): return ident if token == 'ok' else None
        def is_auth_required(self): return False
        def resolve_always_writes(self): return True
        def register_client(self, metadata): return {'client_id': 't', **metadata}
        def create_authorization(self, *a, **k): return 'c'
        def exchange_code(self, *a, **k): return None
    set_provider(Signed()); auth_middleware('ok')
    return identity_context(ident)
'''

# D59-style, as root with writers stopped: each bound owner's real identity
# (zero groups, then zero capabilities after setresuid) against every other
# owner's paths. Bindings come from the boot's own evidence.
MATRIX = r'''
import json, os, sys
from pathlib import Path
bindings = json.loads(sys.argv[1])
trees = {}
def walk(path, machine):
    trees.setdefault(machine, []).append(str(path))
    if path.is_dir() and not path.is_symlink():
        fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_NOATIME)
        try: names = os.listdir(fd)
        finally: os.close(fd)
        for name in names: walk(path / name, machine)
for center, machine in bindings.items():
    walk(Path('/data') / center, machine)
totals = dict(bytes=0, mutations=0, attempts=0)
for machine in sorted(trees):
    foreign = [p for other, paths in trees.items() if other != machine for p in paths]
    reader, writer = os.pipe()
    pid = os.fork()
    if pid == 0:
        os.close(reader)
        os.setgroups([]); os.setresgid(machine, machine, machine)
        os.setresuid(machine, machine, machine)
        got = dict(bytes=0, mutations=0, attempts=0)
        def attempt(action):
            got['attempts'] += 1
            try: action()
            except OSError: return
            got['mutations'] += 1
        for path in foreign:
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError:
                pass
            else:
                try:
                    got['bytes'] += (len(os.listdir(fd)) if os.path.isdir(path)
                                     else len(os.read(fd, 1 << 20)))
                except OSError: pass
                os.close(fd)
            attempt(lambda: os.chmod(path, 0o777))
            attempt(lambda: os.chown(path, machine, machine))
            attempt(lambda: os.link(path, path + '.copy'))
            attempt(lambda: os.rename(path, path + '.moved'))
            attempt(lambda: os.close(os.open(path, os.O_WRONLY | os.O_NOFOLLOW)))
        os.write(writer, json.dumps(got).encode()); os._exit(0)
    os.close(writer)
    raw = b''
    while chunk := os.read(reader, 65536): raw += chunk
    os.close(reader); assert os.waitpid(pid, 0)[1] == 0
    for key, value in json.loads(raw).items(): totals[key] += value
print(json.dumps(dict(owners=len(trees), **totals)))
'''

# The startup probe's legacy volume, with the daemon's real founder_home schema
# (its fixture table has two columns; a real signup writes more). Run as 1001.
SCHEMA = r'''
import sqlite3, sys
sys.path.insert(0, '/app')
from tinyassets.daemon_server import claim_founder_home, initialize_author_server
with sqlite3.connect('/data/.tinyassets.db') as db:
    db.execute('DROP TABLE founder_home')
initialize_author_server('/data')
for owner in ('alice', 'bob'):
    assert claim_founder_home('/data', owner, owner) == owner
print('real schema')
'''

# The OLD production image's own code, as UID1001 with no capability, on the
# reversed volume: it reads and writes a runtime-admitted home, and signs up.
LEGACY_IMAGE = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, '/app')
os.environ['TINYASSETS_DATA_DIR'] = '/data'
from tinyassets.api.first_contact import ensure_founder_home
from tinyassets.auth.middleware import auth_middleware, identity_context, set_provider
from tinyassets.auth.provider import AuthProvider, Identity
from tinyassets.daemon_server import get_founder_home
USER
data = Path('/data')
assert os.getuid() == 1001
dave = get_founder_home(data, 'dave')
note = data / dave / 'old-image-note.md'
note.write_text('written by the old image'); assert note.read_text().startswith('written')
with user('erin'):
    erin = ensure_founder_home(data, 'erin')
assert erin and (data / erin).is_dir() and (data / erin).stat().st_uid == 1001
print(json.dumps(dict(legacy_image=True, runtime_home_writable=dave, signup=erin)))
'''

# Each boot: root pre-step (writers stopped), the refusal it expects (or None),
# and the daemon step asserting on the reconciled boot.
VOLUME_A = [
    ('forward-first', '', None, '''
    assert boot['principals'] == {'alice': 'alice', 'bob': 'bob'}, boot
    assert sorted(boot['admits']) == [['alice', 'alice'], ['bob', 'bob']]
    assert boot['generation'] == 2 and boot['missing'] == {}
    assert decode('alice', 'alice') and decode('bob', 'bob')
    dave = signup('dave'); assert dave and decode('dave', dave)
    create('alice', 'alice-second'); assert decode('alice', 'alice-second')
    receipt = delete('bob', 'bob')
    assert receipt['fence'] == 'released' and receipt['retired'] and not (data / 'bob').exists()
    report_ok(signup=dave, created='alice-second', deleted='bob', retired=receipt['retired'])
'''),
    ('signup-center-deletion', '', None, '''
    dave = home('dave')
    assert boot['principals'] == {'alice': 'alice', 'alice-second': 'alice', dave: 'dave'}, boot
    assert boot['generation'] == 5 and boot['missing'] == {} and not boot['admits']
    assert decode('alice', 'alice-second') and decode('dave', dave)
    assert refused(lambda: decode('bob', 'bob'))
    assert refused(lambda: create('bob', 'bob'))  # retired: never admitted again
    report_ok(crash='D218 between the daemon pass and its retire (alice-second)')
    crash_inside(role_owner_delete, 'retire'); delete('alice', 'alice-second')
'''),
    ('deletion-crash', '', None, '''
    assert 'alice-second' not in boot['principals'] and boot['missing'] == {}, boot
    assert not boot['alarms']  # mid-deletion (intent pending), not lost
    receipt = delete('alice', 'alice-second')  # tree-gone resume: retire, finish
    assert receipt['resumed'] and receipt['retired'] == 6, receipt
    assert d218.pending(data) == []
    report_ok(resumed=receipt, crash='between publish and the log append (alice-orphan)')
    crash_inside(owner_identities, 'center_admission'); create('alice', 'alice-orphan')
'''),
    ('orphan-adopted', '', None, '''
    assert boot['admits'] == [['alice', 'alice-orphan']], boot
    assert boot['principals']['alice-orphan'] == 'alice' and boot['generation'] == 7
    assert decode('alice', 'alice-orphan')
    report_ok(crash='before publish (dave-staged)')
    crash_inside(role_center_admission, '_renameat2'); create('dave', 'dave-staged')
'''),
    ('staging-swept', '', None, '''
    assert boot['swept'] > 0 and 'dave-staged' not in boot['principals'], boot
    assert not boot['admits'] and not boot['alarms']
    create('dave', 'dave-staged'); assert decode('dave', 'dave-staged')  # retried, same name
    report_ok(created='dave-staged')
'''),
    ('f1-b-missing', "shutil.rmtree(data / 'alice-orphan')", None, '''
    assert boot['missing'] == {'alice-orphan': 'alice'} and len(boot['alarms']) == 1, boot
    assert 'alice-orphan' not in boot['principals']
    assert decode('alice', 'alice') and decode('dave', 'dave-staged')  # everyone else
    assert refused(lambda: decode('alice', 'alice-orphan'))
    report_ok(f1='b')
'''),
    ('f1-b-rechecked', '', None, '''
    assert boot['missing'] == {'alice-orphan': 'alice'} and len(boot['alarms']) == 1, boot
    assert any(name.endswith('-missing-center-alice-orphan.md') for name in boot['concerns'])
    receipt = delete('alice', 'alice-orphan')  # D218 retires the missing center, no pass
    assert receipt['missing'] and receipt['fence'] == 'absent', receipt
    report_ok(retired_missing=receipt['retired'])
'''),
    ('missing-retired', '', None, '''
    assert boot['missing'] == {} and not boot['alarms'] and boot['generation'] == 9, boot
    assert decode('alice', 'alice') and decode('dave', home('dave'))
    report_ok(f1='b resolved by retire')
'''),
    ('unexplained-tree',
     "(data / 'stray').mkdir(); os.chown(data / 'stray', 1001, 300099); "
     "import sqlite3; db = sqlite3.connect(data / '.tinyassets.db'); "
     "db.execute(\"INSERT INTO universe_acl (universe_id, actor_id, permission, granted_at, "
     "granted_by) VALUES ('stray', 'dave', 'admin', 0, 'dave')\"); db.commit(); db.close()",
     'unexplained center tree: stray', None),
    ('healed',
     "(data / 'stray').rmdir(); import sqlite3; db = sqlite3.connect(data / '.tinyassets.db'); "
     "db.execute(\"DELETE FROM universe_acl WHERE universe_id='stray'\"); db.commit(); "
     "db.close()", None, '''
    assert boot['missing'] == {} and 'stray' not in boot['principals'], boot
    report_ok(healed=True)
'''),
    ('REVERSE', None, None, None),
    ('LEGACY', None, None, None),
    ('forward-after-legacy', '', None, '''
    erin = home('erin'); dave = home('dave')
    assert boot['admits'] == [['erin', erin]], boot  # seeded; dave's row already existed
    assert boot['principals'][erin] == 'erin' and boot['principals'][dave] == 'dave'
    assert boot['bindings'][erin] not in (boot['bindings'][dave], boot['bindings']['alice'])
    assert decode('erin', erin) and decode('dave', dave)
    assert (data / dave / 'old-image-note.md').stat().st_uid == boot['bindings'][dave]
    report_ok(forward_after_legacy=erin)
'''),
]

VOLUME_B = [
    ('forward-first', '', None, '''
    assert boot['generation'] == 2, boot
    for owner in ('alice', 'bob'):
        assert delete(owner, owner)['fence'] == 'released'
    report_ok(deleted=['alice', 'bob'])
'''),
    ('empty-volume', '', None, '''
    assert boot['principals'] == {} and boot['bindings'] == {} and boot['missing'] == {}, boot
    assert boot['generation'] == 4
    henry = signup('henry'); assert henry and decode('henry', henry)
    report_ok(signup=henry)
'''),
    ('signup-after-empty', '', None, '''
    henry = home('henry')
    assert boot['principals'] == {henry: 'henry'} and boot['generation'] == 5, boot
    assert decode('henry', henry)
    report_ok(bound=henry)
'''),
]


def docker(*args, **kwargs):
    return subprocess.run(['docker', *args], **kwargs)


def boot_program(pre, expect, step):
    code = BOOT.replace('PRE\n', (pre or 'pass') + '\n').replace('USER\n', USER)
    if expect is None:
        code = code.replace('    EXPECT_REFUSAL(str(exc))\n',
                            '    raise AssertionError("unexpected refusal")\n')
        body = step
    else:
        code = code.replace('    EXPECT_REFUSAL(str(exc))\n',
                            f'    assert {expect!r} in str(exc), str(exc)\n')
        body = '    raise AssertionError("startup should have refused")\n'
    return code.replace('STEP\n', body.strip('\n') + '\n')


def root_options(volume):
    options = ['--network', 'none', '--user', '0:0', '--cap-drop', 'ALL',
               '--mount', f'type=volume,src={volume},dst=/data']
    for cap in CAPS:
        options += ['--cap-add', cap]
    for option in SECURITY:
        options += ['--security-opt', option]
    return options


def run_boot(image, volume, name, pre, expect, step):
    command = ['run', '--rm', '-i', *root_options(volume), '-e', f'BOOT={name}',
               '-e', 'TINYASSETS_ROLE_SPLIT=1', '--entrypoint', '/opt/venv/bin/python',
               image, '-I', '-B', '-']
    result = docker(*command, input=boot_program(pre, expect, step), text=True,
                    capture_output=True, timeout=600)
    evidence = [line for line in result.stdout.splitlines() if line.startswith('{"boot"')]
    last = json.loads(evidence[-1]) if evidence else {}
    good = result.returncode == 0 and bool(evidence) and (
        expect is not None and 'refused' in last or expect is None and last.get('ok'))
    return good, last, result


def matrix(image, volume, bindings):
    result = docker('run', '--rm', '-i', *root_options(volume), '--entrypoint',
                    '/opt/venv/bin/python', image, '-I', '-B', '-', json.dumps(bindings),
                    input=MATRIX, text=True, capture_output=True, timeout=600)
    if result.returncode:
        raise RuntimeError(result.stderr[-3000:])
    return json.loads(result.stdout.strip().splitlines()[-1])


def reverse(image, volume):
    environment = [x for k, v in ENVIRONMENT.items() for x in ('-e', f'{k}={v}')]
    result = docker('run', '--rm', *root_options(volume), *environment,
                    '-e', 'TINYASSETS_ROLE_SPLIT=1', '-e', f'TINYASSETS_IMAGE={image}',
                    '--entrypoint', '/usr/local/libexec/ta-entry.sh', image,
                    '/opt/venv/bin/python', '-I', '-B', '/usr/local/libexec/ta-role-start.py',
                    'reverse', capture_output=True, text=True, timeout=600)
    good = result.returncode == 0 and 'reverse migration complete' in result.stdout
    return good, result


def legacy(old_image, volume):
    result = docker('run', '--rm', '-i', '--network', 'none', '--user', '1001:1001',
                    '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges=true',
                    '--mount', f'type=volume,src={volume},dst=/data', '--entrypoint',
                    '/opt/venv/bin/python', old_image, '-I', '-B', '-',
                    input=LEGACY_IMAGE.replace('USER\n', USER), text=True,
                    capture_output=True, timeout=300)
    return result.returncode == 0, result


def pin(image):
    return docker('image', 'inspect', image, '--format', '{{.Id}}', capture_output=True,
                  text=True, check=True).stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--old-image', required=True)
    args = parser.parse_args()
    image, old_image = pin(args.image), pin(args.old_image)
    print('production image:', image, 'old image:', old_image, flush=True)
    failures, largest = 0, 0
    for label, boots in (('A', VOLUME_A), ('B', VOLUME_B)):
        volume = f'ta-admission-startup-{label.lower()}-{uuid.uuid4().hex[:8]}'
        docker('volume', 'create', volume, check=True, capture_output=True)
        try:
            owner_caps = [x for c in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER')
                          for x in ('--cap-add', c)]
            prepared = docker('run', '--rm', '-i', '--network', 'none', '--user', '0:0',
                              '--cap-drop', 'ALL', *owner_caps, '--security-opt',
                              'no-new-privileges=true', '--mount',
                              f'type=volume,src={volume},dst=/data', '--entrypoint',
                              '/opt/venv/bin/python', image, '-I', '-B', '-', input=LEGACY,
                              text=True, capture_output=True, timeout=180)
            if prepared.returncode == 0:
                prepared = docker('run', '--rm', '-i', '--network', 'none', '--user',
                                  '1001:1001', '--cap-drop', 'ALL', '--security-opt',
                                  'no-new-privileges=true', '--mount',
                                  f'type=volume,src={volume},dst=/data', '--entrypoint',
                                  '/opt/venv/bin/python', image, '-I', '-B', '-', input=SCHEMA,
                                  text=True, capture_output=True, timeout=180)
            if prepared.returncode:
                raise RuntimeError(prepared.stderr[-3000:])
            for name, pre, expect, step in boots:
                if name == 'REVERSE':
                    good, result = reverse(image, volume)
                    evidence = 'real reverse mode exit 0' if good else ''
                elif name == 'LEGACY':
                    good, result = legacy(old_image, volume)
                    evidence = result.stdout.strip().splitlines()[-1] if good else ''
                else:
                    good, last, result = run_boot(image, volume, name, pre, expect, step)
                    evidence = json.dumps(last)
                    if good and expect is None:
                        foreign = matrix(image, volume, last['bindings'])
                        largest = max(largest, foreign['attempts'])
                        good = foreign['bytes'] == 0 and foreign['mutations'] == 0
                        evidence += f' foreign={json.dumps(foreign)}'
                print(f'[{label}] {name}: {"PASS" if good else "FAIL"} {evidence}', flush=True)
                if not good:
                    failures += 1
                    sys.stdout.write(result.stdout[-4000:] + result.stderr[-6000:])
                    break
        finally:
            docker('volume', 'rm', '-f', volume, capture_output=True)
    print(json.dumps(dict(restart_matrix='PASS' if not failures else 'FAIL',
                          real_startup_coordinator=True, zero_foreign_bytes=not failures,
                          largest_matrix_attempts=largest, startup_activated=False)),
          flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
