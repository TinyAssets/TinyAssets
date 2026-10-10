"""Real Claude and Codex CLIs in real owner cells, in the cutover image.

Synthetic data only, ``--network none``, no credential. Inside the image the
script becomes PID1, runs the production bootstrap (broker, bounded mapper,
daemon retirement), then, as the retired daemon and for two owners:

* launches the shipped Claude and Codex CLIs through the one provider spawn
  point (``owned_process.aspawn_owned``) into ``provider-exec`` cells;
* drives a real ``codex app-server`` JSON-RPC handshake in a cell;
* reads Codex's model list through ``provider-discovery``;
* runs an in-cell probe that must find: the owner's uid, zero capabilities,
  only stdio descriptors, no foreign center, no broker state, no daemon
  environ, no direct network, and its own engine route through the relay.

    python scripts/role_provider_cell_probe.py --image <cutover image>
"""
from __future__ import annotations

import argparse
import subprocess

CONTAINER = r'''
import asyncio, json, os, runpy, socket, subprocess, sys, tempfile, threading
from pathlib import Path
sys.path.insert(0, '/app')
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
bounded = runpy.run_path('/usr/local/libexec/ta-owner-launch.py')
launch['verify_chain']()

def own(path, uid, gid, mode):
    # Setup holds CAP_FSETID, so the setgid bit survives a group root is not in.
    os.chown(path, uid, gid); os.chmod(path, mode)
    assert (os.stat(path).st_mode & 0o7777, os.stat(path).st_gid) == (mode, gid), path

root = Path(tempfile.mkdtemp(prefix='provider-cells-'))
own(root, 1001, 1001, 0o755)
for name in ('.broker', '.broker/state', '.broker/.outbound-proxy'):
    path = root / name; path.mkdir(); own(path, 1002, 1101, 0o2700)
reader, writer = socket.socketpair()
seed = os.fork()
if seed == 0:
    reader.close(); launch['retire_child']('broker')
    launch['close_descriptors']((writer.fileno(),))
    from tinyassets.broker.owner_identities import OwnerIdentities
    store = OwnerIdentities(root / '.broker/state/owner-identities.db', initialize=True)
    writer.sendall(json.dumps({o: store.resolve(o, allocate=True).uid
                               for o in ('alice', 'bob')}).encode())
    os._exit(0)
writer.close(); identities = json.loads(reader.recv(4096)); reader.close()
assert os.waitpid(seed, 0)[1] == 0
bindings = {}
for owner, uid in identities.items():
    center = root / ('cell-' + owner); center.mkdir(); own(center, 1001, uid, 0o710)
    subprocess.run(['setfacl', '-m', f'u:{uid}:x', str(center)], check=True)
    parent = center
    for name in ('.runtime', 'provider-launch-credentials', 'fixture'):
        parent = parent / name; parent.mkdir(); own(parent, 1001, uid, 0o700)
        subprocess.run(['setfacl', '-m', f'u:{uid}:rx', str(parent)], check=True)
    for name, value in (('auth.json', '{}'), ('.lock', ''), ('own-sentinel', owner + '-only')):
        path = parent / name; path.write_text(value); own(path, 1001, uid, 0o600)
        subprocess.run(['setfacl', '-m', f'u:{uid}:r', str(path)], check=True)
    sibling = parent.parent / 'other-launch'; sibling.mkdir(); own(sibling, 1001, uid, 0o700)
    (sibling / 'auth.json').write_text(owner + '-sibling-credential')
    own(sibling / 'auth.json', 1001, uid, 0o600)
    (center / 'notes.txt').write_text(owner + '-content-not-needed-by-provider')
    own(center / 'notes.txt', 1001, uid, 0o600)
    (center / '.credential-vault.json').write_text(owner + '-vault')
    own(center / '.credential-vault.json', 1001, uid, 0o600)
    bindings[(owner, center.name)] = uid
run = Path(tempfile.mkdtemp(prefix='provider-cells-', dir='/run')); run.chmod(0o755)
ipc = run / 'broker'; ipc.mkdir(); own(ipc, 1002, 1101, 0o2750)
os.environ['DAEMON_ONLY_SECRET'] = 'daemon-secret-sentinel'
# Setup needed CHOWN/DAC_OVERRIDE/FOWNER/FSETID; the service bootstrap must start with
# exactly its own set (KILL, SETGID, SETUID, SETPCAP), so drop the rest first.
libc = launch['_libc']()
for cap in (0, 1, 3, 4):
    launch['_checked'](libc.prctl(24, cap, 0, 0, 0), 'drop setup capability')
launch['_capset'](launch['ENTRY_CAPS'])
launch['_assert_caps'](launch['ENTRY_CAPS'])
supervisor, client = bounded['bootstrap_services'](root, run, bindings, launch, generation=0)
os.environ['TINYASSETS_DATA_DIR'] = str(root)
assert os.getpid() == 1 and os.getuid() == 1001
assert all(int(launch['status']()[k], 16) == 0 for k in launch['CAP_FIELDS'])

from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import grant_universe_access
from tinyassets import universe_egress
from tinyassets.providers.owned_process import aspawn_owned, kill_owned_tree
from tinyassets.providers.provider_jail import provider_launch_scope
from tinyassets.providers.native_jsonrpc_discovery import read_native_catalogue
from tinyassets.providers.codex_provider import CodexProvider
for owner in identities:
    grant_universe_access(root, universe_id='cell-' + owner, actor_id=owner,
                          permission='admin', granted_by=owner)

# A stand-in engine route on the daemon's loopback. The relay re-reads the
# owner-checked route per connection; here that read answers this port.
engine = socket.socket(); engine.bind(('127.0.0.1', 0)); engine.listen(8)
ENGINE_PORT = engine.getsockname()[1]
def serve_engine():
    while True:
        conn, _ = engine.accept()
        with conn:
            conn.sendall(b'ENGINE-ROUTE-OK\n')
threading.Thread(target=serve_engine, daemon=True).start()
universe_egress._route_port = lambda actor_id, graph_id: ENGINE_PORT

CLAUDE = '/opt/claude-code-install/node_modules/.bin/claude'
CODEX = '/opt/codex-install/node_modules/.bin/codex'
PYTHON = '/usr/local/bin/python3.11'
PROBE = r"""
import json, os, socket, sys
targets, port = json.loads(bytes.fromhex(sys.argv[1]))
fields = dict(l.split(':', 1) for l in open('/proc/self/status').read().splitlines())
with open(os.path.join(os.environ['CODEX_HOME'], 'own-sentinel')) as f:
    own_snapshot = f.read()
assert os.environ['HOME'] == '/tmp'
with open('/tmp/metadata-positive-control', 'w') as f:
    f.write('private scratch works')
opened = []
for path in targets:
    try:
        (os.listdir(path) if os.path.isdir(path) else open(path, 'rb').read())
        opened.append(path)
    except OSError:
        pass
try:
    socket.create_connection(('93.184.216.2', 443), timeout=3).close(); direct = 'connected'
except OSError:
    direct = 'refused'
try:
    with socket.create_connection(('127.0.0.1', port), timeout=5) as c:
        engine = c.recv(64).decode().strip()
except OSError as exc:
    engine = 'unreachable: ' + type(exc).__name__
# /proc is the cell's own PID namespace: its PID1 is the cell's init, and no
# daemon (or any host) process, environment or root is visible from here.
pids = [n for n in os.listdir('/proc') if n.isdigit()]
seen_secret = False
for pid in pids:
    try:
        seen_secret |= b'daemon-secret-sentinel' in open(f'/proc/{pid}/environ', 'rb').read()
    except OSError:
        pass
fds = sorted(int(n) for n in os.listdir('/proc/self/fd') if n.isdigit()
             and os.path.exists('/proc/self/fd/' + n))
print(json.dumps(dict(uid=os.getuid(), gid=os.getgid(), caps=int(fields['CapEff'], 16),
    nnp=int(fields['NoNewPrivs']), fds=fds, opened=opened, direct=direct, engine=engine,
    env_secret='DAEMON_ONLY_SECRET' in os.environ, pids=len(pids), proc_secret=seen_secret,
    own_snapshot=own_snapshot)))
"""
PIPES = dict(stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
             stderr=asyncio.subprocess.PIPE)
report = {}

async def in_cell(owner, argv, *, engine_route=True, stdin=b''):
    center = root / ('cell-' + owner)
    snapshot = center / '.runtime/provider-launch-credentials/fixture'
    with provider_launch_scope(center, credential_dir=snapshot,
                               engine_route=(owner, center.name) if engine_route else None):
        proc = await aspawn_owned(argv, env={'CODEX_HOME': str(snapshot), 'TERM': 'dumb',
                                             'DAEMON_ONLY_SECRET': 'daemon-secret-sentinel'},
                                  **PIPES)
    async with asyncio.timeout(120):
        out, err = await proc.communicate(stdin)
    return proc.returncode, out, err

async def in_discovery_cell(owner, argv):
    from tinyassets.role_provider_discovery import aspawn_cell
    from tinyassets.providers.provider_jail import metadata_view

    center = root / ('cell-' + owner)
    snapshot = center / '.runtime/provider-launch-credentials/fixture'
    env = {'CODEX_HOME': str(snapshot), 'DAEMON_ONLY_SECRET': 'daemon-secret-sentinel'}
    view = metadata_view(center, snapshot, env, ('CODEX_HOME',))
    proc = await aspawn_cell(argv, env=env, view=view, universe_dir=center,
                            snapshot_dir=snapshot, limit=4 * 1024 * 1024)
    try:
        # Half-close input: close() would discard the same socket's output.
        proc.stdin.write_eof()
        async with asyncio.timeout(120):
            out = await proc.stdout.read()
            code = await proc.wait()
        return code, out, b''
    except BaseException:
        kill_owned_tree(proc)
        await proc.wait()
        raise
    finally:
        proc._transport.close()

async def app_server_handshake(owner):
    center = root / ('cell-' + owner)
    snapshot = center / '.runtime/provider-launch-credentials/fixture'
    with provider_launch_scope(center, credential_dir=snapshot):
        proc = await aspawn_owned([CODEX, 'app-server'], env={'CODEX_HOME': str(snapshot)},
                                  **PIPES)
    try:
        proc.stdin.write(json.dumps({'id': 1, 'method': 'initialize', 'params': {
            'clientInfo': {'name': 'cell_probe', 'version': '1'}}}).encode() + b'\n')
        await proc.stdin.drain()
        async with asyncio.timeout(60):
            while True:
                line = json.loads(await proc.stdout.readline())
                if line.get('id') == 1:
                    return line
    finally:
        kill_owned_tree(proc)
        await proc.wait()

async def main():
    for owner in identities:
        other = 'bob' if owner == 'alice' else 'alice'
        with identity_context(Identity(owner, owner)):
            claude = await in_cell(owner, [CLAUDE, '--version'])
            assert claude[0] == 0 and claude[1].strip(), claude
            codex = await in_cell(owner, [CODEX, '--version'])
            assert codex[0] == 0 and b'codex-cli' in codex[1], codex
            # What the adapters resolve on PATH: the codex flock wrapper (it
            # locks the cell's private CODEX_HOME copy) and the claude link.
            for wrapper, marker in (('/usr/local/bin/codex', b'codex-cli'),
                                    ('/usr/local/bin/claude', claude[1].split()[0])):
                done = await in_cell(owner, [wrapper, '--version'])
                assert done[0] == 0 and marker in done[1], (wrapper, done)
            hello = await app_server_handshake(owner)
            assert 'result' in hello, hello
            catalogue = await read_native_catalogue(
                [CODEX, 'app-server'], protocol=CodexProvider.native_discovery_protocol,
                env={'CODEX_HOME': str(root / ('cell-' + owner)
                                       / '.runtime/provider-launch-credentials/fixture')},
                cwd=str(root / ('cell-' + owner) / '.runtime/provider-launch-credentials/fixture'),
                universe_dir=root / ('cell-' + owner), auth_env_names=('CODEX_HOME',),
                timeout=60)
            foreign = root / ('cell-' + other)
            targets = [str(foreign), str(foreign / '.runtime/provider-launch-credentials'
                                         / 'fixture/own-sentinel'),
                       str(root / ('cell-' + owner) / '.credential-vault.json'),
                       str(root / ('cell-' + owner) / 'notes.txt'),
                       str(root / ('cell-' + owner) / '.runtime/provider-launch-credentials'
                           / 'other-launch/auth.json'),
                       str(root / '.broker/state/owner-identities.db'), str(root)]
            code, out, err = await in_cell(
                owner, [PYTHON, '-I', '-S', '-c', PROBE,
                        json.dumps([targets, ENGINE_PORT]).encode().hex()])
            assert code == 0, (code, out, err)
            seen = json.loads(out)
            inner = identities[owner] - 300000
            assert seen['uid'] == seen['gid'] == inner, seen
            assert seen['caps'] == 0 and seen['nnp'] == 1 and seen['fds'] == [0, 1, 2], seen
            assert seen['opened'] == [] and seen['direct'] == 'refused', seen
            assert seen['engine'] == 'ENGINE-ROUTE-OK' and not seen['env_secret'], seen
            assert not seen['proc_secret'] and seen['pids'] < 16, seen
            assert seen['own_snapshot'] == owner + '-only', seen
            # The discovery class gets no engine relay and sees no more owner
            # or daemon data than provider-exec, despite an outer launch scope.
            with provider_launch_scope(root / ('cell-' + other),
                                       engine_route=(other, 'cell-' + other)):
                code, out, err = await in_discovery_cell(
                    owner, [PYTHON, '-I', '-S', '-c', PROBE,
                            json.dumps([targets, ENGINE_PORT]).encode().hex()])
            assert code == 0, (code, out, err)
            metadata = json.loads(out)
            assert metadata['uid'] == metadata['gid'] == inner, metadata
            assert metadata['caps'] == 0 and metadata['nnp'] == 1, metadata
            assert metadata['fds'] == [0, 1, 2] and not metadata['opened'], metadata
            assert metadata['direct'] == 'refused', metadata
            assert metadata['engine'].startswith('unreachable:'), metadata
            assert not metadata['env_secret'] and not metadata['proc_secret'], metadata
            assert metadata['pids'] < 16, metadata
            assert metadata['own_snapshot'] == owner + '-only', metadata
            snapshot = root / ('cell-' + owner) / '.runtime/provider-launch-credentials/fixture'
            assert (snapshot / 'auth.json').read_text() == '{}'
            assert (snapshot / 'own-sentinel').read_text() == owner + '-only'
            assert not (root / 'metadata-positive-control').exists()
            report[owner] = dict(claude=claude[1].decode().strip(),
                                 codex=codex[1].decode().strip(),
                                 app_server=sorted(hello['result'])[:4],
                                 discovered_models=len(catalogue.models), cell=seen,
                                 metadata_cell=metadata)
    print(json.dumps(report, indent=1), flush=True)

asyncio.run(main())
print('PROVIDER CELL PROBE PASS', flush=True)
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
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'FSETID', 'SETUID', 'SETGID', 'SETPCAP',
                'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print('cutover image:', digest, flush=True)
    return subprocess.run(command, input=CONTAINER, text=True, timeout=600).returncode


if __name__ == '__main__':
    raise SystemExit(main())
