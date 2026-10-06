"""Actual broker IPC and application decoder under D60's staged PID1 bootstrap.

Only synthetic container state; production CMD and data are never used.
"""
from __future__ import annotations

import argparse
import subprocess
import time
import uuid

CONTAINER = r'''
import gc, io, json, os, runpy, socket, subprocess, sys, tempfile
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
snapshot_children=[]
for owner,uid in identities.items():
    center=root/('decoder-'+owner); center.mkdir()
    os.chown(center,uid,uid); center.chmod(0o700)
    for name in ('owner.json','.credential-vault.json'):
        path=center/name; path.write_text(owner+'-sentinel')
        os.chown(path,uid,uid); path.chmod(0o600)
    bindings[(owner,center.name)]=uid
    if GIT:
        work=center/'workspace'; work.mkdir(); os.chown(work,uid,uid); work.chmod(0o700)
        own=work/'own.txt'; own.write_text(owner+'-own-bytes'); os.chown(own,uid,uid)
        own.chmod(0o600)
        os.symlink('own.txt',work/'own-link')
        subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',
                        str(center),str(work)],check=True)
        subprocess.run(['setfacl','-m','u:1001:r',str(own)],check=True)
if SNAPSHOTS:
    proof_dir=Path(tempfile.mkdtemp(prefix='snapshot-proof-')); proof_dir.chmod(0o777)
    for owner,uid in identities.items():
        center=root/('snapshot-'+owner); center.mkdir()
        os.chown(center,1001,uid); center.chmod(0o711)
        bindings[(owner,center.name)]=uid
        runtime=center/'.runtime'; runtime.mkdir()
        os.chown(runtime,1001,1100); runtime.chmod(0o2750)
        foreign_uid=300002 if owner=='alice' else 300001
        subprocess.run(['setfacl','-m',f'u:{foreign_uid}:rx,d:u:{foreign_uid}:rx',
                        str(runtime)],check=True)
        pid=os.fork()
        if pid==0:
            try:
                launch['_retire_identity'](uid,())
                launch['close_descriptors']()
                import fcntl, time
                manifest=proof_dir/'manifest.json'
                deadline=time.monotonic()+60
                while not manifest.exists():
                    assert time.monotonic()<deadline,'snapshot publication timed out'
                    time.sleep(0.05)
                paths=json.loads(manifest.read_text())
                own=Path(paths[owner]); foreign=Path(paths['bob' if owner=='alice' else 'alice'])
                expected=json.dumps({'token':owner+'-fixture'}).encode()
                assert (own/'auth.json').read_bytes()==expected
                with (own/'.lock').open('rb') as handle:
                    fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
                for path in (own/'auth.json',own/'config.toml',own/'.lock',own/'new-file'):
                    try: descriptor=os.open(path,os.O_WRONLY|os.O_CREAT,0o600)
                    except PermissionError: pass
                    else:
                        os.close(descriptor); raise AssertionError('engine wrote sealed snapshot')
                for path in (foreign/'auth.json',root/('snapshot-'+owner)/'.credential-vault.json'):
                    try: path.read_bytes()
                    except PermissionError: pass
                    else: raise AssertionError('engine read foreign/protected credentials')
                try: list(own.parent.iterdir())
                except PermissionError: pass
                else: raise AssertionError('engine listed sibling snapshots')
                version=subprocess.run(['/usr/local/bin/codex','--version'],cwd=own,
                    env={'PATH':'/usr/local/bin:/usr/bin:/bin','HOME':'/tmp','CODEX_HOME':str(own)},
                    capture_output=True,timeout=15)
                assert version.returncode==0,version.stderr.decode()
                (proof_dir/owner).write_text('PASS')
                (proof_dir/owner).chmod(0o644)
                os._exit(0)
            except BaseException:
                import traceback
                traceback.print_exc(); os._exit(1)
        snapshot_children.append(pid)
    for name,uid,groups in (('legacy',1003,(1100,)),('broker-reader',1002,(1102,))):
        pid=os.fork()
        if pid==0:
            try:
                launch['_retire_identity'](uid,groups); launch['close_descriptors']()
                import time
                deadline=time.monotonic()+60
                while not (proof_dir/'manifest.json').exists():
                    assert time.monotonic()<deadline
                    time.sleep(0.05)
                paths=json.loads((proof_dir/'manifest.json').read_text())
                for path in paths.values():
                    try: (Path(path)/'auth.json').read_bytes()
                    except PermissionError: pass
                    else: raise AssertionError('legacy/broker role read sealed owner credentials')
                (proof_dir/name).write_text('PASS'); (proof_dir/name).chmod(0o644)
                os._exit(0)
            except BaseException:
                import traceback
                traceback.print_exc(); os._exit(1)
        snapshot_children.append(pid)
if GIT:
    foreign=root/'decoder-bob/workspace/foreign.txt'; foreign.write_text('bob-foreign-sentinel')
    os.chown(foreign,300002,300002); foreign.chmod(0o600)
    subprocess.run(['setfacl','-m','u:1001:r',str(foreign)],check=True)
    os.link(foreign,root/'decoder-alice/workspace/foreign-hardlink')
    os.symlink(str(foreign),
               root/'decoder-alice/workspace/foreign-symlink')
    os.mkfifo(root/'decoder-alice/workspace/preplanted-fifo')
    os.chown(root/'decoder-alice/workspace/preplanted-fifo',300001,300001)
    outside=root/'not-admitted'; outside.mkdir(); os.chown(outside,300001,300001)
    outside.chmod(0o700)
    subprocess.run(['setfacl','-m','u:1001:rwx',str(outside)],check=True)
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
supervisor,client=bounded['bootstrap_services'](root,run,bindings,launch,generation=0)
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
if SNAPSHOTS:
    import base64, sqlite3
    from tinyassets import credential_vault as vault
    from tinyassets.storage import db_path
    paths={}; snapshots=[]
    for owner in identities:
        center=root/('snapshot-'+owner)
        material=json.dumps({'token':owner+'-fixture'}).encode()
        vault.write_credential_vault(center,[{'credential_type':'llm_subscription',
            'service':'codex','auth_json_b64':base64.b64encode(material).decode()}],
            owner_user_id=owner,universe_id=center.name)
        with sqlite3.connect(db_path(root)) as db:
            db.execute('BEGIN IMMEDIATE')
            custody=vault.adopt_llm_subscription_custody(db,universe_dir=center,
                owner_user_id=owner,universe_id=center.name,service='codex')
        made=vault.snapshot_llm_subscription_credential(universe_dir=center,custody=custody)
        vault._prepare_snapshot_root(center)  # repeat cannot restore shared access
        paths[owner]=str(made.directory); snapshots.append(made)
    # Refuse another principal before any snapshot is created.
    import dataclasses
    wrong=dataclasses.replace(custody,owner_user_id='alice')
    try: vault.snapshot_llm_subscription_credential(universe_dir=center,custody=wrong)
    except PermissionError: pass
    else: raise AssertionError('foreign snapshot custody admitted')
    (proof_dir/'manifest.tmp').write_text(json.dumps(paths))
    (proof_dir/'manifest.tmp').chmod(0o644)
    (proof_dir/'manifest.tmp').replace(proof_dir/'manifest.json')
    for pid in snapshot_children:
        assert os.waitpid(pid,0)[1]==0,'dedicated snapshot permissions failed'
    assert all((proof_dir/owner).read_text()=='PASS'
               for owner in (*identities,'legacy','broker-reader'))
    for made in snapshots:
        vault.cleanup_llm_credential_snapshot(made)
        assert not made.directory.exists()
    print('snapshot prerequisite: Alice/Bob dedicated UID/GID read own sealed bytes; '
          'CLI lock/version works; foreign/vault/write/list/shared-group/broker denied; '
          'inherited foreign ACL removed; daemon cleanup works; '
          'not provider-class launcher acceptance',flush=True)
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
if GIT:
    import array
    from tinyassets.workspace_git import run_git
    git_home=Path(tempfile.mkdtemp(prefix='git-empty-home-'))
    # Bypass client validation deliberately: the real mapper must reject the
    # wrong owner's inode AND same-owner directory outside the admitted center.
    for wrong in (root/'decoder-bob/workspace',root/'not-admitted'):
        fd=os.open(wrong,os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW)
        try:
            left,right=socket.socketpair()
            with left,right:
                client._channel.sendmsg([json.dumps(dict(op='SPAWN',kind='workspace-git',
                    principal='alice',command_center='decoder-alice')).encode()],[(
                    socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[right.fileno(),fd]))])
                assert client._reply()==dict(op='REFUSED')
        finally: os.close(fd)
    for owner in identities:
        work=root/('decoder-'+owner)/'workspace'
        with identity_context(Identity(owner,owner)):
            def git(*argv):
                return run_git(argv,cwd=work,home_dir=git_home,path='/usr/bin:/bin',
                    options=('-c','user.name=Oracle','-c','user.email=oracle@example.invalid'),
                    timeout_s=10)
            for argv in (('init',),('add','own.txt','own-link'),('commit','-m','owner commit'),
                         ('status','--porcelain'),('show','HEAD:own.txt')):
                result=git(*argv)
                assert result.returncode==0,(argv,result)
                assert 'bob-own-bytes' not in result.stdout_tail if owner=='alice' else True
            (work/'own-link').unlink()
            assert git('checkout','--','own-link').returncode==0
            assert (work/'own-link').is_symlink()
            if owner=='alice':
                for path in ('foreign-hardlink','foreign-symlink'):
                    result=git('hash-object',path)
                    assert result.returncode!=0 and 'bob-foreign-sentinel' not in result.stdout_tail
                from tinyassets.owner_launcher_client import OwnerLaunchRefused
                try:
                    run_git(['stall'],cwd=work,home_dir=git_home,path='/usr/bin:/bin',
                            options=('-c','alias.stall=!sleep 30'),timeout_s=0.1)
                except OwnerLaunchRefused: pass
                else: raise AssertionError('git timeout did not refuse')
                assert git('status','--porcelain').returncode==0
            from tinyassets.universe_files import read_universe_file
            assert (owner+'-own-bytes').encode() in read_universe_file(
                root/('decoder-'+owner),'workspace/own.txt')
    for alias in ('foreign-hardlink','foreign-symlink'):
        try:
            result=read_universe_file(root/'decoder-alice','workspace/'+alias)
        except (OSError,ValueError): pass
        else: raise AssertionError('daemon reader admitted a git foreign alias')
    from tinyassets import git_bridge
    assert not git_bridge.is_enabled(root.parent)
    for owner in identities:
        work=root/('decoder-'+owner)/'workspace'
        with identity_context(Identity(owner,owner)):
            assert git('config','user.name','Oracle').returncode==0
            assert git('config','user.email','oracle@example.invalid').returncode==0
            assert git_bridge.is_enabled(work)
            changed=run_git(['bridge-write'],cwd=work,home_dir=git_home,path='/usr/bin:/bin',
                options=('-c',f'alias.bridge-write=!printf {owner}-bridge-change > own.txt'),
                timeout_s=10)
            assert changed.returncode==0,changed
            assert git_bridge.has_uncommitted_changes(work/'own.txt',repo_path=work)
            assert git_bridge.stage(work/'own.txt',repo_path=work)
            assert git_bridge.unstage([work/'own.txt'],repo_path=work)
            receipt=git_bridge.commit('bridge owner commit',
                                     author='Oracle <oracle@example.invalid>',
                                     paths=[work/'own.txt'],repo_path=work)
            assert receipt.ok and len(receipt.sha)==40,receipt
            assert not git_bridge.has_uncommitted_changes(work/'own.txt',repo_path=work)
            foreign_owner='bob' if owner=='alice' else 'alice'
            # A cached successful probe must not admit the other owner's tree.
            assert not git_bridge.is_enabled(root/('decoder-'+foreign_owner)/'workspace')
            if owner=='alice':
                for alias in ('foreign-hardlink','foreign-symlink'):
                    value=git_bridge._run(['git','hash-object','--',str(work/alias)],
                        capture_output=True,text=True,check=False,timeout=10,cwd=str(work))
                    assert value.returncode!=0 and 'bob-foreign-sentinel' not in value.stdout
    with identity_context(Identity('alice','alice')):
        refused=git_bridge._run(['git','hash-object','preplanted-fifo'],
            capture_output=True,text=True,check=False,timeout=0.1,
            cwd=str(root/'decoder-alice/workspace'))
        assert refused.returncode==126 and not refused.stdout
        assert git_bridge.is_enabled(root/'decoder-alice/workspace')
    print('git-bridge: actual local detect/diff/stage/unstage/commit for Alice/Bob; '
          'cross-owner cache reuse and foreign aliases denied',flush=True)
    print('workspace-git: actual run_git init/add/commit/show/checkout for Alice/Bob; '
          'foreign aliases denied; cell-links and descriptor checks passed',flush=True)
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
    parser.add_argument('--git', action='store_true')
    parser.add_argument('--snapshots', action='store_true')
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
    script = (f'DEATH={death!r}\nFAIL={args.bootstrap_failure!r}\nGIT={args.git!r}\n'
              f'SNAPSHOTS={args.snapshots!r}\n' + CONTAINER)
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
