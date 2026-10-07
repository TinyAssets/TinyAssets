"""Runtime center admission through the real D70 bootstrap, production image.

Real broker IPC (OWNER channel and DA2's mapper pair), the real bounded mapper,
the real bubblewrap center-root cell, then a real decoder cell for each
runtime-admitted center. Synthetic container state only; no host mounts,
network or startup activation.
"""
from __future__ import annotations

import argparse
import subprocess

CONTAINER = r'''
import io, json, os, runpy, socket, sys, tempfile
from pathlib import Path
sys.path.insert(0, '/app')
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
bounded = runpy.run_path('/usr/local/libexec/ta-owner-launch.py')
launch['verify_chain']()
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
root = Path(tempfile.mkdtemp(prefix='role-admission-'))
root.chmod(0o755); os.chown(root, 1001, 1001)
for name in ('.broker', '.broker/state', '.broker/.outbound-proxy'):
    path = root / name; path.mkdir(); directory(path, 1002, 1101, 0o2700)
reader, writer = socket.socketpair()
seed = os.fork()
if seed == 0:
    # Startup seeding through a retired broker child (DA4/DA7), never root.
    reader.close(); launch['retire_child']('broker')
    launch['close_descriptors']((writer.fileno(),))
    from tinyassets.broker.owner_identities import OwnerIdentities
    store = OwnerIdentities(root / '.broker/state/owner-identities.db', initialize=True)
    uids = {owner: store.resolve(owner, allocate=True).uid for owner in ('alice', 'bob')}
    writer.sendall(json.dumps(uids).encode()); os._exit(0)
writer.close(); identities = json.loads(reader.recv(4096)); reader.close()
assert os.waitpid(seed, 0)[1] == 0
# DA7 startup: sweep admission staging, then seed rows through a retired
# broker child (root never opens the broker database). bob-old is a logged row
# at the bootstrap generation, so it is never bound at runtime.
contract = runpy.run_path('/usr/local/libexec/ta-admission-contract.py')
stale = root / '.role-admission' / ('f' * 32) / 'g'; stale.mkdir(parents=True)
root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
assert contract['clear_staging'](root_fd) == 3 and not (root / '.role-admission').exists()
os.close(root_fd)
seeded = contract['broker_log'](root, launch, append=[('alice', 'decoder-alice'),
                                                      ('bob', 'bob-old')])
assert [(r['generation'], r['event'], r['center']) for r in seeded] == [
    (1, 'admit', 'decoder-alice'), (2, 'admit', 'bob-old')], seeded
assert contract['broker_log'](root, launch, append=[('alice', 'decoder-alice')]) == seeded
try: contract['broker_log'](root, launch, append=[('bob', 'decoder-alice')])
except contract['ContractRefused']: pass
else: raise AssertionError('startup seeded a conflicting admission')
assert contract['broker_log'](root, launch, after=1) == seeded[1:]
generation = seeded[-1]['generation']
center = root / 'decoder-alice'; center.mkdir()
os.chown(center, identities['alice'], identities['alice']); center.chmod(0o700)
bindings = {('alice', 'decoder-alice'): identities['alice']}
run = Path(tempfile.mkdtemp(prefix='role-admission-', dir='/run')); run.chmod(0o755)
ipc = run / 'broker'; ipc.mkdir(); directory(ipc, 1002, 1101, 0o2750)
supervisor, client = bounded['bootstrap_services'](root, run, bindings, launch,
                                                   generation=generation)
os.environ['TINYASSETS_DATA_DIR'] = str(root)
os.environ['TINYASSETS_CREDENTIAL_BROKER'] = 'process'
assert os.getpid() == 1 and os.getuid() == 1001
def zero_caps():
    return all(int(launch['status']()[k], 16) == 0 for k in launch['CAP_FIELDS'])
assert zero_caps()
from tinyassets import role_decoder
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.owner_identities import center_admission, owner_identity
from tinyassets.daemon_server import grant_universe_access
from tinyassets.owner_launcher_client import OwnerLaunchRefused
from tinyassets.role_center_admission import (AdmissionRefused, admit_center, canonical_label,
                                              read_label)
from PIL import Image
evidence = {}

def refused(action):
    try: action()
    except (OwnerLaunchRefused, AdmissionRefused, RuntimeError, PermissionError): return True
    return False

# Before admission, a cell for the new center refuses with no legacy fallback.
out = io.BytesIO(); Image.new('RGB', (8, 8), 'blue').save(out, format='PNG')
png = out.getvalue()
assert refused(lambda: client.start_cell(kind='ui-preview', principal='alice',
    command_center='alice-second', identity=owner_identity(root, principal='alice')))
# A bound center and an unreserved principal never get a center-root cell.
root_fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
from tinyassets.role_center_admission import label_root
from tinyassets.broker.owner_identities import OwnerIdentity
evidence['bound_center_root_refused'] = refused(lambda: label_root(root_fd, client=client,
    principal='alice', center='decoder-alice', identity=owner_identity(root, principal='alice')))
evidence['unreserved_center_root_refused'] = refused(lambda: label_root(root_fd, client=client,
    principal='carol', center='carol-home', identity=OwnerIdentity(300003, 300003)))
assert not (root / 'carol-home').exists() and os.listdir(root / '.role-admission') == []
# DA4 end to end: Alice adds a second center; Carol signs up (new identity).
results = {}
for owner, name in (('alice', 'alice-second'), ('carol', 'carol-home')):
    generation_of = admit_center(root, principal=owner, center=name)
    assert admit_center(root, principal=owner, center=name) == generation_of  # idempotent
    machine = owner_identity(root, principal=owner).uid
    fd = os.open(root / name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: label = read_label(fd)
    finally: os.close(fd)
    assert label == canonical_label(machine), label
    assert sorted(os.listdir(root / name)) == ['previews']
    grant_universe_access(root, universe_id=name, actor_id=owner, permission='admin',
                          granted_by=owner)
    with identity_context(Identity(owner, owner)):
        done = role_decoder.decode(png, 'image/png', root / name)
    assert done.returncode == 0 and done.cell['uid'] == done.cell['gid'] == machine - 300000
    results[name] = dict(machine=machine, generation=generation_of, label='canonical',
                         decoder_cell_uid=done.cell['uid'], caps=done.cell['caps'])
assert results['carol-home']['machine'] == 300003
assert os.listdir(root / '.role-admission') == []
evidence['admitted'] = results
# Forged binds through the real mapper: absent row, another principal, a
# stale (pre-bootstrap) row, a retire row, another center's root inode.
def bind(principal, name, number, path):
    fd = os.open(path, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: client.admit(principal=principal, command_center=name, generation=number, root_fd=fd)
    finally: os.close(fd)
alice_gen = results['alice-second']['generation']
forged = {
    'absent_row': lambda: bind('alice', 'alice-second', 99, root / 'alice-second'),
    'other_principal': lambda: bind('bob', 'alice-second', alice_gen, root / 'alice-second'),
    'stale_generation': lambda: bind('bob', 'bob-old', 2, root / 'decoder-alice'),
    'other_root_inode': lambda: bind('alice', 'alice-second', alice_gen, root / 'carol-home'),
    'cell_for_unbound_logged_center': lambda: client.start_cell(
        kind='ui-preview', principal='bob', command_center='bob-old',
        identity=owner_identity(root, principal='bob')),
}
evidence['forged_refused'] = {name: refused(action) for name, action in forged.items()}
assert all(evidence['forged_refused'].values()), evidence['forged_refused']
bind('alice', 'alice-second', alice_gen, root / 'alice-second')  # channel still serves
# The broker never admits a retired name or another principal's center.
evidence['broker_conflict_refused'] = refused(lambda: center_admission(
    root, event='admit', principal='bob', center='alice-second'))
# Foreign owner: the application refuses Bob's scope into Alice's new center.
with identity_context(Identity('carol', 'carol')):
    evidence['foreign_application_refused'] = refused(
        lambda: role_decoder.decode(png, 'image/png', root / 'alice-second'))
for path in (root / '.broker/state/owner-identities.db',):
    evidence['daemon_cannot_open_broker_log'] = refused(path.read_bytes)
# Application path (task 6): a new user's first home through first contact,
# _universe_impl and admit_center, then a real decoder cell in it.
from tinyassets.api.first_contact import ensure_founder_home
from tinyassets.auth.middleware import auth_middleware, set_provider
from tinyassets.auth.provider import AuthProvider
dave = Identity('dave', 'dave', capabilities=['read', 'write', 'costly', 'submit_request',
                                              'list'])
class Signed(AuthProvider):  # the authenticated request a real connector carries
    def resolve_token(self, token): return dave if token == 'ok' else None
    def is_auth_required(self): return False
    def resolve_always_writes(self): return True
    def register_client(self, metadata): return {'client_id': 't', **metadata}
    def create_authorization(self, *a, **k): return 'c'
    def exchange_code(self, *a, **k): return None
set_provider(Signed()); auth_middleware('ok')
with identity_context(dave):
    home = ensure_founder_home(root, 'dave')
    assert home and (root / home / 'soul.md').is_file(), home
    fd = os.open(root / home, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: assert read_label(fd) == canonical_label(owner_identity(root, principal='dave').uid)
    finally: os.close(fd)
    done = role_decoder.decode(png, 'image/png', root / home)
    assert done.returncode == 0 and done.cell['caps'] == 'zero'
evidence['first_contact_home_admitted'] = dict(center=home, decoder_cell_uid=done.cell['uid'])
# DA6 through real IPC: pass one, the daemon pass, retire (twice: resume), finish.
from tinyassets import role_owner_delete as deletion
token = '0123456789abcdef' * 2
with identity_context(Identity('carol', 'carol')):
    # U1's pass-one cell predates U2's D218 change that retains daemon
    # directories the owner may only search; the daemon's own empty previews
    # goes first here. D218's daemon pass and its intent store live on U2.
    os.rmdir(root / 'carol-home' / 'previews')
    deletion.begin(root / 'carol-home', token=token)
    os.rmdir(root / 'carol-home')  # the daemon pass: the tree is gone
    retired = deletion.retire(root / 'carol-home', token=token)
    assert deletion.retire(root / 'carol-home', token=token) == retired
    deletion.finish(root / 'carol-home', token=token)
evidence['retired_cell_refused'] = refused(lambda: client.start_cell(
    kind='ui-preview', principal='carol', command_center='carol-home',
    identity=owner_identity(root, principal='carol')))
evidence['retired_name_never_readmitted'] = refused(
    lambda: admit_center(root, principal='carol', center='carol-home'))
assert not (root / 'carol-home').exists()
evidence['daemon_caps_zero'] = zero_caps()
evidence['startup_log_via_retired_broker'] = True
print(json.dumps(dict(evidence, startup_activated=False)), flush=True)
assert all(value for key, value in evidence.items() if isinstance(value, bool)), evidence
os._exit(0)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print('production image:', digest, flush=True)
    return subprocess.run(command, input=CONTAINER, text=True, timeout=300).returncode


if __name__ == '__main__':
    raise SystemExit(main())
