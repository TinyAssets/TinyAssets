"""Admission restart matrix (task 9): real broker IPC, real mapper, real restarts.

Each boot is a fresh production-image container on the same named volume, so
a restart is a real restart: new PID1, broker, mapper and bubblewrap cells.
Before the D70 bootstrap, as root with writers stopped, every boot runs the
DA7 contract (deploy/role_admission_contract.py) as the startup coordinator:
sweep staging, read the log through a retired broker child, reconcile against
the volume journal, adopt or seed, alarm, record the journal, then bootstrap
with the reconciled bindings and generation. U2's coordinator (volume, metadata
and owner phases, D218 intents) is not on this branch; the stand-in inventory
reads centers and their single admin grant, and pending deletions from a
daemon-written file. Synthetic state only; no network; startup stays OFF.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import uuid

PREAMBLE = r'''
import io, json, os, runpy, secrets, shutil, socket, sqlite3, stat, sys, tempfile, traceback
from pathlib import Path
sys.path.insert(0, '/app')
BOOT = os.environ['BOOT']
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
bounded = runpy.run_path('/usr/local/libexec/ta-owner-launch.py')
contract = runpy.run_path('/usr/local/libexec/ta-admission-contract.py')
launch['verify_chain']()
vol = Path('/vol'); data = vol / 'data'; state = vol / 'state'
PENDING = data / '.pending-deletions.json'  # stand-in for U2's .role-owner-delete
def directory(path, uid, gid, mode):
    # Exact fixture labels. The egress migration's _permissions never adds
    # setgid outside its named platform directories (D219), and this probe's
    # socket directory lives under a temporary run root.
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fchown(fd, uid, gid); os.setegid(gid)  # no FSETID: join the group (D17)
        try: os.fchmod(fd, mode)
        finally: os.setegid(0)
        info = os.fstat(fd)
        assert (info.st_uid, info.st_gid, info.st_mode & 0o7777) == (uid, gid, mode), path
    finally: os.close(fd)
if not data.exists():  # an empty forward volume; identity-map init is lane A
    data.mkdir(); data.chmod(0o755); os.chown(data, 1001, 1001)
    for name in ('.broker', '.broker/state', '.broker/.outbound-proxy'):
        path = data / name; path.mkdir(); directory(path, 1002, 1101, 0o2700)
    state.mkdir(mode=0o700)
    child = os.fork()
    if child == 0:
        launch['retire_child']('broker')
        from tinyassets.broker.owner_identities import OwnerIdentities
        OwnerIdentities(data / '.broker/state/owner-identities.db', initialize=True)
        os._exit(0)
    assert os.waitpid(child, 0)[1] == 0
def authority(sql, args):
    with sqlite3.connect(data / '.tinyassets.db') as db:
        db.execute(sql, args)
PRE
journal_path = state / 'volume.json'
journal = json.loads(journal_path.read_text()) if journal_path.exists() else None
root_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
swept = contract['clear_staging'](root_fd)
def inventory():
    found = {}
    db = (sqlite3.connect(f'file:{data}/.tinyassets.db?mode=ro', uri=True)
          if (data / '.tinyassets.db').exists() else None)
    try:
        for name in sorted(os.listdir(root_fd)):
            if name.startswith('.') or not stat.S_ISDIR(
                    os.stat(name, dir_fd=root_fd, follow_symlinks=False).st_mode):
                continue
            admins = db.execute("SELECT actor_id FROM universe_acl WHERE universe_id=? AND "
                                "permission='admin'", (name,)).fetchall() if db else []
            if not admins and not name.startswith('u-'):
                continue  # a platform directory, not a center
            if len(admins) != 1:
                raise contract['ContractRefused'](f'missing or ambiguous owner: {name}')
            found[name] = admins[0][0]
    finally:
        if db is not None: db.close()
    return found
discovered = inventory()
pending = set(json.loads(PENDING.read_text())) if PENDING.exists() else set()
forward = bool(journal) and journal['state'] == 'stable' and journal['direction'] == 'forward'
every = contract['broker_log'](data, launch, after=0)
rows = [r for r in every if r['generation'] > journal['generation']] if forward else every
logged = {r['center'] for r in every}
owners = contract['reservations'](data, launch, discovered.values())
def adoptable(center, principal):
    machine = owners.get(principal)
    if machine is None or center in logged:
        return False
    fd = os.open(center, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=root_fd)
    try: return contract['read_label'](fd) == contract['canonical_label'](machine)
    finally: os.close(fd)
try:
    plan = contract['reconcile'](journal=journal, discovered=discovered, rows=rows,
                                 pending=pending, adoptable=adoptable)
except contract['ContractRefused'] as exc:
    print(json.dumps(dict(boot=BOOT, refused=str(exc), mutated=False)), flush=True)
    EXPECT_REFUSAL(str(exc))
    os._exit(0)
appended = contract['broker_log'](data, launch, after=plan['generation'],
                                  append=plan['adopt'] + plan['seed'])
state_fd = os.open(state, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
concerns = contract['raise_alarms'](state_fd, plan['alarms'])
fields = contract['journal_fields'](plan, appended)
machines = {}
for r in contract['broker_log'](data, launch, after=0):
    if r['event'] == 'admit': machines[r['center']] = r['machine']
    else: machines.pop(r['center'], None)
bindings = {(p, c): machines[c] for c, p in fields['principals'].items()}
record = dict(direction='forward', state='stable', **fields)
fd = os.open('volume.json.tmp', os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600, dir_fd=state_fd)
with os.fdopen(fd, 'w') as handle:
    json.dump(record, handle); handle.flush(); os.fsync(handle.fileno())
os.replace('volume.json.tmp', 'volume.json', src_dir_fd=state_fd, dst_dir_fd=state_fd)
boot = dict(boot=BOOT, swept=swept, principals=fields['principals'], missing=fields['missing'],
            generation=fields['generation'], adopted=[list(a) for a in plan['adopt']],
            seeded=[list(s) for s in plan['seed']], concerns=concerns)
def foreign_bytes():
    """D59-style matrix as every other owner's real host identity, zero caps."""
    trees = {}
    for (_, center), machine in bindings.items():
        paths = []
        for top, dirs, names in os.walk(data / center):
            paths.append(top); paths.extend(os.path.join(top, n) for n in names)
        trees.setdefault(machine, []).extend(paths)
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
        os.close(reader); os.waitpid(pid, 0)
        for key, value in json.loads(raw).items(): totals[key] += value
    return totals
boot['foreign'] = foreign_bytes()
assert boot['foreign']['bytes'] == 0 and boot['foreign']['mutations'] == 0, boot['foreign']
run = Path(tempfile.mkdtemp(prefix='role-admission-', dir='/run')); run.chmod(0o755)
ipc = run / 'broker'; ipc.mkdir(); directory(ipc, 1002, 1101, 0o2750)
supervisor, client = bounded['bootstrap_services'](data, run, bindings, launch,
                                                   generation=fields['generation'])
os.environ['TINYASSETS_DATA_DIR'] = str(data)
os.environ['TINYASSETS_CREDENTIAL_BROKER'] = 'process'
assert os.getpid() == 1 and os.getuid() == 1001
assert all(int(launch['status']()[k], 16) == 0 for k in launch['CAP_FIELDS'])
from PIL import Image
from tinyassets import role_center_admission, role_decoder
from tinyassets import role_owner_delete as deletion
from tinyassets.api.first_contact import ensure_founder_home
from tinyassets.auth.middleware import auth_middleware, identity_context, set_provider
from tinyassets.auth.provider import AuthProvider, Identity
from tinyassets.broker import owner_identities
from tinyassets.daemon_server import get_founder_home, grant_universe_access
from tinyassets.owner_launcher_client import OwnerLaunchRefused
out = io.BytesIO(); Image.new('RGB', (8, 8), 'blue').save(out, format='PNG'); PNG = out.getvalue()
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
    except (OwnerLaunchRefused, OSError, RuntimeError): return True
    return False
def signup(owner):
    with user(owner):
        return ensure_founder_home(data, owner)
def home(owner):
    return get_founder_home(data, owner)
def delete(owner, center, *, finish=True):
    token = secrets.token_hex(16)
    with user(owner):
        os.rmdir(data / center / 'previews')  # U1 pass one predates U2's D218 cell change
        deletion.begin(data / center, token=token)
        shutil.rmtree(data / center)  # the daemon pass
        if finish:
            deletion.retire(data / center, token=token)
            deletion.finish(data / center, token=token)
def once(module, name, failure):
    real = getattr(module, name)
    def fail(*a, **k):
        setattr(module, name, real)
        raise failure
    setattr(module, name, fail)
def crash_inside(module, name):
    def die(*a, **k):
        os._exit(0)  # the container dies mid-admission
    setattr(module, name, die)
def report(**values):
    print(json.dumps(dict(boot, **values, ok=True)), flush=True)
try:
STEP
except BaseException:
    traceback.print_exc()
    print(json.dumps(dict(boot, ok=False)), flush=True)
    os._exit(1)
os._exit(0)
'''

# Each boot: root-side pre-step (writers stopped), the refusal it expects (if
# any), and the daemon step with its assertions on the reconciled boot.
VOLUME_A = [
    ('init', '', None, '''
    assert boot['principals'] == {} and boot['generation'] == 0
    for owner, center in (('alice', 'alice-home'), ('bob', 'bob-home'),
                          ('carol', 'carol-home')):
        create(owner, center); assert decode(owner, center)
    report(created=3)
'''),
    ('grow', '', None, '''
    assert boot['principals'] == {'alice-home': 'alice', 'bob-home': 'bob',
                                  'carol-home': 'carol'}, boot
    assert boot['generation'] == 3 and boot['missing'] == {} and not boot['adopted']
    assert signup('dave'); create('alice', 'alice-second'); delete('carol', 'carol-home')
    report(signup=home('dave'))
'''),
    ('signup-center-deletion-restart', '', None, '''
    assert boot['principals'] == {'alice-home': 'alice', 'bob-home': 'bob',
                                  'alice-second': 'alice', home('dave'): 'dave'}, boot
    assert boot['generation'] == 6 and boot['missing'] == {} and not boot['concerns']
    assert decode('alice', 'alice-second') and decode('dave', home('dave'))
    assert refused(lambda: decode('carol', 'carol-home'))
    report(crash='before publish (bob-second)')
    crash_inside(role_center_admission, '_renameat2'); create('bob', 'bob-second')
'''),
    ('crash-before-publish-restart', '', None, '''
    assert boot['swept'] > 0 and 'bob-second' not in boot['principals'], boot
    assert not boot['adopted'] and not boot['concerns']
    create('bob', 'bob-second'); assert decode('bob', 'bob-second')
    report(crash='between publish and log append (bob-third)')
    crash_inside(owner_identities, 'center_admission'); create('bob', 'bob-third')
'''),
    ('crash-before-append-restart', '', None, '''
    assert boot['adopted'] == [['bob', 'bob-third']], boot
    assert boot['principals']['bob-third'] == 'bob' and not boot['concerns']
    assert decode('bob', 'bob-third')
    report(crash='between log append and bind (alice-third)')
    client.admit = lambda **_: os._exit(0); create('alice', 'alice-third')
'''),
    ('crash-before-bind-restart', '', None, '''
    assert boot['principals']['alice-third'] == 'alice' and not boot['adopted'], boot
    assert decode('alice', 'alice-third')
    once(owner_identities, 'center_admission', RuntimeError('log append failed'))
    assert signup('erin') == ''
    erin = home('erin'); assert (data / erin).is_dir()  # root and grant kept
    assert signup('erin') == erin and decode('erin', erin)  # retried, no restart
    once(client, 'admit', RuntimeError('bind failed'))
    assert signup('frank') == '' and (data / home('frank')).is_dir()
    report(first_contact='erin retried in place; frank failed at bind, restarting')
'''),
    ('first-contact-bind-failure-restart', '', None, '''
    frank = home('frank')
    assert boot['principals'][frank] == 'frank' and not boot['adopted'], boot
    assert not boot['concerns'] and boot['missing'] == {}
    assert signup('frank') == frank and decode('frank', frank)
    from tinyassets.api import universe as universe_api
    once(universe_api, 'seed_okf_bundle', OSError('seeding failed'))
    assert signup('gina') == ''
    report(first_contact='frank resumed; gina failed at seeding, restarting')
'''),
    ('first-contact-seeding-failure-restart', '', None, '''
    gina = home('gina')
    assert boot['principals'][gina] == 'gina' and not boot['concerns'], boot
    assert signup('gina') == gina and decode('gina', gina)
    once(owner_identities, 'center_admission', RuntimeError('log append failed'))
    assert signup('hank') == ''
    report(first_contact='gina resumed; hank failed at the log append, restarting')
'''),
    ('first-contact-append-failure-restart', '', None, '''
    hank = home('hank')
    assert boot['adopted'] == [['hank', hank]] and not boot['concerns'], boot
    assert signup('hank') == hank and decode('hank', hank)
    delete('bob', 'bob-home', finish=False)
    PENDING.write_text(json.dumps(['bob-home']))
    report(crash='between the deletion daemon pass and its retire (bob-home)')
    os._exit(0)
'''),
    ('deletion-crash-restart', '', None, '''
    assert 'bob-home' not in boot['principals'] and boot['missing'] == {}, boot
    assert not boot['concerns']  # mid-deletion, not lost
    with user('bob'):
        generation = deletion.retire(data / 'bob-home', token='0' * 32)  # unbound: no-op
    PENDING.unlink()
    assert refused(lambda: create('bob', 'bob-home'))  # retired: never admitted again
    report(retired=generation)
'''),
    ('f1-b-missing', "shutil.rmtree(data / 'alice-third')", None, '''
    assert boot['missing'] == {'alice-third': 'alice'}, boot
    assert len(boot['concerns']) == 1 and 'alice-third' not in boot['principals']
    assert decode('alice', 'alice-home') and decode('bob', 'bob-second')  # everyone else
    assert refused(lambda: decode('alice', 'alice-third'))
    report(f1='b')
'''),
    ('f1-b-rechecked', '', None, '''
    assert boot['missing'] == {'alice-third': 'alice'} and len(boot['concerns']) == 1, boot
    assert decode('dave', home('dave'))
    report(f1='b, re-checked')
'''),
    ('unexplained-tree',
     "(data / 'stray').mkdir(); os.chown(data / 'stray', 1001, 1001); "
     "authority(\"INSERT INTO universe_acl (universe_id, actor_id, permission, granted_at, "
     "granted_by) VALUES ('stray', 'mallory', 'admin', 0, 'mallory')\", ())",
     'unexplained center tree: stray', None),
    ('changed-owner',
     "(data / 'stray').rmdir(); "
     "authority(\"DELETE FROM universe_acl WHERE universe_id='stray'\", ()); "
     "authority(\"UPDATE universe_acl SET actor_id='mallory' WHERE universe_id='bob-second' "
     "AND permission='admin'\", ())",
     'center owner changed: bob-second', None),
    ('healed',
     "authority(\"UPDATE universe_acl SET actor_id='bob' WHERE universe_id='bob-second' "
     "AND permission='admin'\", ())", None, '''
    assert boot['missing'] == {'alice-third': 'alice'} and decode('bob', 'bob-second'), boot
    report(healed=True)
'''),
]

VOLUME_B = [
    ('only-center', '', None, '''
    create('alice', 'solo'); assert decode('alice', 'solo')
    report(created='solo')
'''),
    ('delete-only-center', '', None, '''
    assert boot['principals'] == {'solo': 'alice'}, boot
    delete('alice', 'solo')
    report(deleted='solo')
'''),
    ('empty-volume-restart', '', None, '''
    assert boot['principals'] == {} and boot['missing'] == {} and boot['generation'] == 2, boot
    assert signup('henry')
    report(signup=home('henry'))
'''),
    ('signup-after-empty-restart', '', None, '''
    henry = home('henry')
    assert boot['principals'] == {henry: 'henry'} and boot['generation'] == 3, boot
    assert decode('henry', henry)
    report(bound=henry)
'''),
]


def container(pre, expect, step):
    code = PREAMBLE.replace('PRE\n', (pre or 'pass') + '\n')
    if expect is None:
        code = code.replace('    EXPECT_REFUSAL(str(exc))\n',
                            '    raise AssertionError("unexpected refusal")\n')
    else:
        code = code.replace('    EXPECT_REFUSAL(str(exc))\n',
                            f'    assert {expect!r} in str(exc), str(exc)\n')
        step = '    raise AssertionError("startup should have refused")\n'
    return code.replace('STEP\n', step.strip('\n') + '\n')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    print('production image:', digest, flush=True)
    failures = 0
    for label, boots in (('A', VOLUME_A), ('B', VOLUME_B)):
        volume = f'ta-admission-restart-{label.lower()}-{uuid.uuid4().hex[:8]}'
        subprocess.run(['docker', 'volume', 'create', volume], check=True, capture_output=True)
        try:
            for name, pre, expect, step in boots:
                command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
                           '--cap-drop', 'ALL', '-v', f'{volume}:/vol', '-e', f'BOOT={name}']
                for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID',
                            'SETPCAP', 'KILL'):
                    command += ['--cap-add', cap]
                for option in ('no-new-privileges=true', 'seccomp=unconfined',
                               'apparmor=unconfined', 'systempaths=unconfined'):
                    command += ['--security-opt', option]
                command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
                result = subprocess.run(command, input=container(pre, expect, step), text=True,
                                        capture_output=True, timeout=300)
                evidence = [line for line in result.stdout.splitlines()
                            if line.startswith('{"boot"')]
                good = result.returncode == 0 and evidence and (
                    expect is not None or json.loads(evidence[-1]).get('ok'))
                print(f'[{label}] {name}: {"PASS" if good else "FAIL"} '
                      f'{evidence[-1] if evidence else ""}', flush=True)
                if not good:
                    failures += 1
                    sys.stdout.write(result.stdout[-4000:] + result.stderr[-6000:])
                    break
        finally:
            subprocess.run(['docker', 'volume', 'rm', '-f', volume], capture_output=True)
    print(json.dumps(dict(restart_matrix='PASS' if not failures else 'FAIL',
                          zero_foreign_bytes=not failures, startup_activated=False)),
          flush=True)
    return 1 if failures else 0


if __name__ == '__main__':
    raise SystemExit(main())
