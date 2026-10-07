"""Full migration/reverse then the unchanged old image CMD on a disposable volume."""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid

from role_old_image_rollback_probe import pin

PREPARE = r'''
import json,os,runpy,sqlite3,sys
from pathlib import Path
sys.path.insert(0,'/app/scripts')
from role_image_oracle import child,snapshot
root=Path('/data')
os.chown(root,1001,1001); root.chmod(0o755)
(root/'.layout.lock').touch()
(root/'.layout.json').write_text(json.dumps({'layout':2,'state':'stable',
    'moves':{'consents_outside_command_centers':'done'}}))
(root/'platform-expected-instance.json').write_text(json.dumps({
    'schema':'platform_expected_instance','version':1,'expected_instance_id':'123456789'}))
(root/'release-state.json').write_text(json.dumps({'git_sha':'synthetic-rollback-probe'}))
for center in ('alice','bob'):
    tree=root/center; (tree/'work').mkdir(parents=True)
    (tree/'universe.json').write_text('{}')
    (tree/'work/retained').write_bytes(center.encode())
with sqlite3.connect(root/'.tinyassets.db') as db:
    db.execute('CREATE TABLE founder_home(founder_sub TEXT PRIMARY KEY, universe_id TEXT)')
    db.executemany('INSERT INTO founder_home VALUES (?,?)',[('alice','alice'),('bob','bob')])
with sqlite3.connect(root/'outbound.db') as db:
    db.execute('CREATE TABLE sentinel(value TEXT)')
    db.execute("INSERT INTO sentinel VALUES ('retained')")
for parent,dirs,files in os.walk(root):
    os.chown(parent,1001,1001)
    for name in files: os.chown(os.path.join(parent,name),1001,1001)
load=lambda name:runpy.run_path('/usr/local/libexec/ta-'+name+'.py')
launch=load('launch'); launch['verify_chain']()
owner=load('owner-migration')
helpers=dict(owner=owner,egress=load('egress-migration'),
    metadata=load('metadata-migration'),inventory=load('volume-inventory'),
    contract=load('admission-contract'),
    modes=runpy.run_path('/app/tinyassets/role_modes.py'),launch=launch)
migrate=load('volume-migration')['migrate']
original=snapshot(root)
before=snapshot(root); migrate(root,dry_run=True,**helpers); assert snapshot(root)==before
migrate(root,**helpers)
before=snapshot(root); migrate(root,**helpers); assert snapshot(root)==before
machine=(root/'alice/work').stat().st_uid
assert 300001 <= machine < 400000
assert (root/'bob/work').stat().st_uid != machine
def engine():
    private=root/'alice/work/private'; private.mkdir(mode=0o700)
    (private/'payload').write_bytes(b'engine-created'); (private/'payload').chmod(0o600)
child(machine,[],engine)
assert (root/'alice/work/private/payload').stat().st_uid==machine
before=snapshot(root); migrate(root,reverse=True,dry_run=True,**helpers)
assert snapshot(root)==before
migrate(root,reverse=True,**helpers)
assert (root/'alice/work/private/payload').stat().st_uid==1001
restored=snapshot(root)
for path,old in original.items():
    if path=='alice' or path=='bob' or path.startswith(('alice/','bob/')):
        new=restored[path]
        assert new[:2]==old[:2], (path,new,old)
        assert new[4]==old[4], (path,'content changed')
        # D211 retains live modes; only the protected center root is narrowed.
        expected_mode=old[2] if '/' in path else (old[2] & ~0o7777)|(old[2] & 0o2750)
        assert new[2]==expected_mode, (path,new,old)
before=snapshot(root); migrate(root,reverse=True,**helpers); assert snapshot(root)==before
assert json.loads((root/'.layout.json').read_text())['state']=='stable'
print(json.dumps(dict(full_coordinator=True,forward_reverse_dry_repeat=True,
    restrictive_owner_file=True,broker_allocated_identity=True)))
'''

VERIFY = r'''
import os,sqlite3
from pathlib import Path
assert os.getuid()==1001 and os.getgid()==1001
assert not set(os.getgroups()) & {1100,1101,1102}
s=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
assert all(int(s[k],16)==0 for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
assert int(s['NoNewPrivs'])==1
p=Path('/data/alice/work/private/payload')
assert p.read_bytes()==b'engine-created'
p.write_bytes(b'old-image-write'); p.unlink(); p.parent.rmdir()
assert Path('/data/bob/work/retained').read_bytes()==b'bob'
with sqlite3.connect('/data/outbound.db') as db:
    assert db.execute('SELECT value FROM sentinel').fetchone()==('retained',)
    db.execute("INSERT INTO sentinel VALUES ('old-image-write')")
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--old-image', required=True)
    args = parser.parse_args()
    candidate, old = pin(args.image), pin(args.old_image)
    name = 'ta-u2-rollback-' + uuid.uuid4().hex
    subprocess.run(['docker', 'volume', 'create', name], check=True, capture_output=True)
    common = ['--network', 'none', '--cap-drop', 'ALL', '--security-opt',
              'no-new-privileges=true', '--mount', f'type=volume,src={name},dst=/data']
    network = False
    try:
        command = ['docker', 'run', '--rm', '-i', *common, '--user', '0:0',
                   '--security-opt', 'seccomp=unconfined']
        for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
            command += ['--cap-add', cap]
        subprocess.run([*command, '--entrypoint', '/opt/venv/bin/python', candidate,
                        '-I', '-B', '-'], input=PREPARE, text=True, check=True, timeout=180)
        # Isolated test metadata, never host routing or a production credential.
        subprocess.run(['docker', 'network', 'create', '--internal', '--subnet',
                        '169.254.0.0/16', name], check=True, capture_output=True)
        network = True
        server = ('from http.server import BaseHTTPRequestHandler,HTTPServer\n'
                  'class Handler(BaseHTTPRequestHandler):\n'
                  ' def do_GET(self):\n'
                  '  self.send_response(200); self.end_headers(); self.wfile.write(b"123456789")\n'
                  'HTTPServer(("0.0.0.0",80),Handler).serve_forever()')
        subprocess.run(['docker', 'run', '-d', '--name', name+'-metadata', '--network', name,
                        '--ip', '169.254.169.254', '--user', '1001:1001', '--cap-drop', 'ALL',
                        '--security-opt', 'no-new-privileges=true', '--entrypoint',
                        '/opt/venv/bin/python', old, '-c', server], check=True, capture_output=True)
        # No entrypoint/CMD override: exercise the actual previous production startup.
        boot_options = common.copy()
        boot_options[1] = name
        boot_options += ['--security-opt', 'seccomp=unconfined', '--security-opt',
                         'apparmor=unconfined', '--security-opt', 'systempaths=unconfined']
        environment = {
            'HOME': '/app', 'TINYASSETS_REPO_ROOT': '/data/community-pool',
            'TINYASSETS_CLOUD_DAEMON_SUBSCRIPTION_ONLY': '1', 'TINYASSETS_GOAL_POOL': 'off',
            'TINYASSETS_AUTO_SHIP_RUBRIC_MODE': 'enforce',
            'TINYASSETS_AUTO_SHIP_TRAJECTORY_MODE': 'enforce',
            'TINYASSETS_MCP_CANARY_URL': 'http://127.0.0.1:8001/mcp',
            'TINYASSETS_ONBOARDING_APP': '1', 'TINYASSETS_ALLOW_CLAUDE_SERVING': '1',
            # The old WorkOS path rejects the synthetic canary as a non-JWT.
            # This fixture proves storage/boot compatibility, not production auth.
            'UNIVERSE_SERVER_AUTH': 'false',
            'UNIVERSE_SERVER_DEV_USER': 'u2-synthetic-operator',
        }
        for key, value in environment.items():
            boot_options += ['-e', key+'='+value]
        subprocess.run(['docker', 'run', '-d', '--name', name, *boot_options, '--user', '1001:1001',
                        '-e', 'TINYASSETS_DATA_DIR=/data', '-e', 'TINYASSETS_IMAGE='+old,
                        '-e', 'TINYASSETS_WIKI_CANARY_TOKEN=synthetic-u2-rollback-only',
                        old], check=True, capture_output=True)
        deadline = time.monotonic() + 180
        while True:
            result = subprocess.run(['docker', 'exec', name, '/opt/venv/bin/python', '-c',
                'import socket; socket.create_connection(("127.0.0.1",8001),2).close()'],
                capture_output=True)
            if result.returncode == 0:
                break
            running = subprocess.run(['docker', 'inspect', name, '--format', '{{.State.Running}}'],
                                     capture_output=True, text=True, check=True).stdout.strip()
            if running != 'true' or time.monotonic() >= deadline:
                subprocess.run(['docker', 'logs', '--tail', '80', name], check=False)
                raise RuntimeError('old production CMD did not become ready')
            time.sleep(1)
        subprocess.run(['docker', 'exec', '-i', name, '/opt/venv/bin/python', '-I', '-B', '-'],
                       input=VERIFY, text=True, check=True, timeout=30)
        pulse = subprocess.run(['docker', 'exec', name, '/usr/local/libexec/ta-op', 'pulse'],
                               timeout=60)
        if pulse.returncode:
            subprocess.run(['docker', 'logs', '--tail', '100', name], check=False)
            raise RuntimeError('old production healthcheck failed')
        print(json.dumps(dict(candidate=candidate, old_image=old, old_cmd_boot=True,
                              old_healthcheck=True, owner_tree_rollback=True,
                              old_uid=1001, old_capabilities='zero', synthetic_volume=True,
                              synthetic_metadata=True, auth_fixture='dev-operator')))
    finally:
        # A failed start can still create a container; cleanup must not mask it.
        subprocess.run(['docker', 'rm', '-f', name], check=False, capture_output=True)
        subprocess.run(['docker', 'rm', '-f', name+'-metadata'],
                       check=False, capture_output=True)
        if network:
            subprocess.run(['docker', 'network', 'rm', name], check=True, capture_output=True)
        subprocess.run(['docker', 'volume', 'rm', name], check=True, capture_output=True)


if __name__ == '__main__':
    main()
