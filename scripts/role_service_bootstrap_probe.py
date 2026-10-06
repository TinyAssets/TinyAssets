"""Actual broker IPC and application decoder under D60's staged PID1 bootstrap.

Only synthetic container state; production CMD and data are never used.
"""
from __future__ import annotations

import argparse
import subprocess
import time
import uuid

CONTAINER = r'''
import gc, io, json, os, runpy, socket, sys, tempfile
from pathlib import Path
sys.path.insert(0, '/app')
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
bounded = runpy.run_path('/usr/local/libexec/ta-owner-launch.py')
launch['verify_chain']()
permissions=runpy.run_path('/usr/local/libexec/ta-egress-migration.py')['_permissions']
def directory(path,uid,gid,mode):
    fd=os.open(path,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
    try: permissions(fd,uid,gid,mode)
    finally: os.close(fd)
root = Path(tempfile.mkdtemp(prefix='role-services-'))
root.chmod(0o755); os.chown(root,1001,1001)
for name in ('.broker','.broker/state','.broker/.outbound-proxy'):
    path=root/name; path.mkdir(); directory(path,1002,1101,0o2700)
reader,writer=socket.socketpair()
seed=os.fork()
if seed==0:
    reader.close(); launch['retire_child']('broker')
    launch['close_descriptors']((writer.fileno(),))
    from tinyassets.broker.owner_identities import OwnerIdentities
    store=OwnerIdentities(root/'.broker/state/owner-identities.db',initialize=True)
    identities={owner:store.resolve(owner,allocate=True).uid for owner in ('alice','bob')}
    writer.sendall(json.dumps(identities).encode()); os._exit(0)
writer.close(); identities=json.loads(reader.recv(4096)); reader.close()
assert os.waitpid(seed,0)[1]==0
bindings={}
for owner,uid in identities.items():
    center=root/('decoder-'+owner); center.mkdir()
    os.chown(center,uid,uid); center.chmod(0o700)
    for name in ('owner.json','.credential-vault.json'):
        path=center/name; path.write_text(owner+'-sentinel')
        os.chown(path,uid,uid); path.chmod(0o600)
    bindings[(owner,center.name)]=uid
run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run')); run.chmod(0o755)
ipc=run/'broker'; ipc.mkdir(); directory(ipc,1002,1101,0o2750)
if os.environ.get('TA_ORACLE_HTTPS')=='1':
    stream=runpy.run_path('/app/scripts/role_stream_oracle.py')
    stream['install_fixture_trust'](); stream['seed'](root)
gc.collect()
assert not (root/'.broker/owner.json').exists()
if FAIL:
    retire=launch['retire_child']
    def fail_retirement(role):
        if role=='daemon': raise RuntimeError('injected pre-retirement failure')
        return retire(role)
    launch['retire_child']=fail_retirement
supervisor,client=bounded['bootstrap_services'](root,run,bindings,launch)
os.environ['TINYASSETS_DATA_DIR']=str(root)
os.environ['TINYASSETS_CREDENTIAL_BROKER']='process'
assert os.getpid()==1 and os.getuid()==1001 and os.getgroups()==[1100,1101,1102]
assert all(int(launch['status']()[k],16)==0 for k in launch['CAP_FIELDS'])
assert not (run/'launcher.sock').exists()  # no host-root serving launcher
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.owner_identities import owner_identity
from tinyassets.daemon_server import grant_universe_access
from tinyassets import role_decoder
from PIL import Image
for owner in identities:
    identity=owner_identity(root,principal=owner)
    assert identity.uid==identity.gid==identities[owner]
    grant_universe_access(root,universe_id='decoder-'+owner,actor_id=owner,
                          permission='admin',granted_by=owner)
try: owner_identity(root,principal='not-allocated')
except RuntimeError: pass
else: raise AssertionError('lookup allocated an identity')
allocated=owner_identity(root,principal='dynamic-owner',allocate=True)
assert allocated.uid==allocated.gid==300003
assert owner_identity(root,principal='dynamic-owner')==allocated
out=io.BytesIO(); Image.new('RGB',(8,8),'blue').save(out,format='PNG')
for owner in identities:
    with identity_context(Identity(owner,owner)):
        done=role_decoder.decode(out.getvalue(),'image/png',root/('decoder-'+owner))
        assert done.returncode==0 and done.cell['uid']==identities[owner]-300000
        meta,_,png=done.stdout.partition(b'\n')
        assert json.loads(meta)['width']==8 and Image.open(io.BytesIO(png)).size==(8,8)
        foreign='bob' if owner=='alice' else 'alice'
        try: role_decoder.decode(out.getvalue(),'image/png',root/('decoder-'+foreign))
        except PermissionError: pass
        else: raise AssertionError('application admitted another owner')
for path in (root/'.broker/outbound.db',root/'.broker/state/owner-identities.db',
             root/'.broker/owner.json'):
    try: path.read_bytes()
    except PermissionError: pass
    else: raise AssertionError('daemon opened broker state')
if os.environ.get('TA_ORACLE_HTTPS')=='1':
    stream['probe'](root)
print(json.dumps(dict(bootstrap=True,daemon_pid=1,daemon_caps='zero',
    live_identity_ipc=True,allocation_persisted=True,application_png_owners=2,
    foreign_application_refused=True,legacy_launcher_absent=True,
    startup_activated=False)),flush=True)
if DEATH=='broker':
    Path('/tmp/role-service-ready').write_text(str(supervisor._bootstrap_pid))
    import time
    time.sleep(30)
    raise AssertionError('broker death did not terminate PID1')
if DEATH=='mapper':
    # STOP is authenticated, mapper exits, PID1 must terminate the container.
    client.stop()
    import time
    time.sleep(5)
    raise AssertionError('service death left PID1 serving')
os._exit(0)  # Docker tears down remaining roles, no cross-UID kill authority.
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--service-death', action='store_true')
    parser.add_argument('--broker-death', action='store_true')
    parser.add_argument('--bootstrap-failure', action='store_true')
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    if sum((args.stream, args.service_death, args.broker_death, args.bootstrap_failure)) > 1:
        parser.error('run stream and failure modes independently')
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
    death = 'broker' if args.broker_death else 'mapper' if args.service_death else ''
    script = f'DEATH={death!r}\nFAIL={args.bootstrap_failure!r}\n' + CONTAINER
    if args.stream:
        from linux_oracle import production_stream_oracle

        command.remove('-i')
        command[-1:] = ['-c', script]
        return production_stream_oracle(command, digest)
    if args.broker_death:
        name = 'ta-role-death-' + uuid.uuid4().hex[:12]
        command[2:2] = ['--name', name]
        process = subprocess.Popen(command, stdin=subprocess.PIPE, text=True)
        try:
            process.stdin.write(script)
            process.stdin.close()
            deadline = time.monotonic() + 60
            while time.monotonic() < deadline and process.poll() is None:
                ready = subprocess.run(['docker', 'exec', name, 'test', '-f',
                                        '/tmp/role-service-ready'], capture_output=True)
                if ready.returncode == 0:
                    break
                time.sleep(0.2)
            else:
                raise RuntimeError('broker death fixture did not become ready')
            subprocess.run(['docker', 'exec', '--user', '0:0', name,
                '/opt/venv/bin/python', '-I', '-c',
                "import os,signal; from pathlib import Path; "
                "os.kill(int(Path('/tmp/role-service-ready').read_text()),signal.SIGKILL)"],
                check=True)
            if process.wait(timeout=10) != 78:
                raise RuntimeError('broker death did not terminate PID1 with exit 78')
            print('broker death: container exited 78 without privileged restart')
            return 0
        finally:
            if process.poll() is None:
                subprocess.run(['docker', 'rm', '-f', name], check=True, capture_output=True)
                process.wait(timeout=10)
    result = subprocess.run(command, input=script,
                            text=True, timeout=180)
    if args.service_death or args.bootstrap_failure:
        if result.returncode != 78:
            raise RuntimeError(f'service death did not terminate PID1: {result.returncode}')
        print('service death: container exited 78 without privileged restart')
        return 0
    return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
