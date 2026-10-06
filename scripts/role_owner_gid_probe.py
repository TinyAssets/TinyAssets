"""D58 diagnostic: shared-UID cells can relabel/copy a preplanted foreign inode.

Synthetic retired-original aliases only; no live data, host mounts or network.
Runs every existing D9 profile, not actual engine-class launcher acceptance.
Exit 3 is a completed isolation failure; setup failures raise separately.
"""
from __future__ import annotations

import argparse
import json
import subprocess

CELL = r'''
import json, os, stat, sys
from pathlib import Path
for name in os.listdir('/proc/self/fd'):
    fd = int(name)
    if fd > 2:
        try:
            os.close(fd)
        except OSError as exc:
            if exc.errno != 9:
                raise
status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines())
assert os.getuid() == 1003 and os.getgid() == 200000 and os.getgroups() == []
assert int(status['NoNewPrivs']) == 1 and int(status['Seccomp']) == 2
assert all(int(status[k], 16) == 0 for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
namespaces = {k: os.readlink('/proc/self/ns/' + k) for k in ('mnt','pid','ipc','net')}
assert all(value != HOST[key] for key, value in namespaces.items())
positive = Path('/owner/positive.txt')
positive.write_text('ALICE-POSITIVE-CONTROL')
positive.chmod(0o660)
assert positive.read_text() == 'ALICE-POSITIVE-CONTROL'
try:
    Path(FOREIGN).read_bytes()
except (FileNotFoundError, PermissionError):
    pass
else:
    raise AssertionError('Bob host path is visible')
rows = []
for relpath in PATHS:
    target = Path('/owner') / relpath
    before = target.stat()
    assert before.st_uid == 1003 and before.st_gid == 65534 and before.st_nlink == 1
    # UID ownership alone authorizes this read despite the unmapped foreign GID.
    data = target.read_bytes()
    assert data == SENTINEL
    if ATTACK == 'relabel':
        os.chown(target, -1, os.getgid())
    else:
        target.unlink()
        target.write_bytes(data)
    target.chmod(0o660)  # restore the inherited daemon ACL's effective mask
    after = target.stat()
    assert (after.st_uid, after.st_gid, after.st_nlink) == (1003, 200000, 1)
    rows.append(dict(path=relpath, uid=after.st_uid, gid=after.st_gid,
                     nlink=after.st_nlink, outcome='FOREIGN_BYTES'))
print(json.dumps(dict(uid=os.getuid(), gid=os.getgid(), groups=os.getgroups(),
                     capabilities='zero', nnp=1, seccomp=2, namespaces=namespaces,
                     host_path_denied=True, rows=rows)), flush=True)
'''

CONTAINER = r'''
import json, os, runpy, subprocess, sys, tempfile
from pathlib import Path
launcher = runpy.run_path('/usr/local/libexec/ta-launch.py')
launcher['verify_chain']()
factory = runpy.run_path('/app/tinyassets/providers/jail_seccomp.py')['program_fd']
root = Path(tempfile.mkdtemp(prefix='role-owner-gid-'))
os.chown(root, 1001, 1001); root.chmod(0o700)
bob = root / 'bob'; bob.mkdir(); os.chown(bob,1001,200001); bob.chmod(0o2770)
outside = bob / 'untouched'; outside.write_bytes(b'OUTSIDE-UNCHANGED')
original = outside.stat()
paths = ('activity.log', 'workspace/record.txt', 'wiki/page.md',
         'canon/record.md', 'output/record.md', 'logs/run.log')
sentinel = b'BOB-PRIVATE-SYNTHETIC-SENTINEL'
host = {k: os.readlink('/proc/self/ns/' + k) for k in ('mnt','pid','ipc','net')}
cases = []
for profile in ('cell-deny', 'cell-links', 'cell-nested'):
    for attack in ('relabel', 'copy'):
        center = profile + '-' + attack
        owner = root / center
        owner.mkdir(); os.chown(owner,1001,200000); owner.chmod(0o2770)
        for relpath in paths:
            target = owner / relpath
            target.parent.mkdir(parents=True, exist_ok=True)
            os.chown(target.parent,1001,200000); target.parent.chmod(0o2770)
            subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',str(target.parent)],check=True)
            foreign = bob / 'original'
            foreign.write_bytes(sentinel); os.chown(foreign,1003,200001); foreign.chmod(0o600)
            subprocess.run(['setfacl','-m','u:1001:rw',str(foreign)],check=True)
            os.link(foreign,target); foreign.unlink()
            assert target.stat().st_gid != 200000, 'unmodified alias must fail the D58 predicate'
        fd = factory(profile=profile)
        argv = ['setpriv','--reuid=1003','--regid=200000','--clear-groups',
                '--bounding-set=-all','--no-new-privs',
                '/usr/bin/bwrap','--die-with-parent','--new-session','--unshare-all',
                '--cap-drop','ALL','--clearenv']
        for path in ('/usr','/opt','/app','/etc','/bin','/lib','/lib64'):
            if os.path.exists(path):
                argv += ['--ro-bind',path,path]
        argv += ['--proc','/proc','--dev','/dev','--tmpfs','/tmp',
                 '--bind',str(owner),'/owner','--chdir','/owner','--seccomp',str(fd),
                 '--','/opt/venv/bin/python','-I','-B','-c',
                 'HOST='+repr(host)+'; FOREIGN='+repr(str(outside))+'; PATHS='+repr(paths)+
                 '; SENTINEL='+repr(sentinel)+'; ATTACK='+repr(attack)+'\n'+CELL]
        # The data-root ancestor must be traversable by the retired launch identity;
        # Bob itself stays outside the mounted owner view.
        root.chmod(0o711)
        try:
            result = subprocess.run(argv,pass_fds=(fd,),capture_output=True,text=True,timeout=20)
        finally:
            os.close(fd)
        assert result.returncode == 0, (result.stdout,result.stderr)
        print(json.dumps(dict(profile=profile,attack=attack,cell=json.loads(result.stdout))),flush=True)
        cases.append((center,profile,attack))
assert outside.read_bytes() == b'OUTSIDE-UNCHANGED'
after = outside.stat()
assert (after.st_uid,after.st_gid,after.st_mode,after.st_mtime_ns) == (
        original.st_uid,original.st_gid,original.st_mode,original.st_mtime_ns)
launcher['retire_migration_authority']()
launcher['retire_child']('daemon')
sys.path.insert(0,'/app')
os.environ['TINYASSETS_DATA_DIR'] = str(root)
from tinyassets.api.helpers import _read_platform_text
from tinyassets.api.universe import _action_inspect_universe
from tinyassets.api.universe_file_reads import _read as api_file_read
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.daemon_server import ensure_universe_registered, grant_universe_access
from tinyassets.universe_files import read_universe_file
from tinyassets.workspace_fs import open_dir_nofollow, _open_regular_beneath
failures=[]
ensure_universe_registered(root,universe_id='bob',universe_path=bob)
grant_universe_access(root,universe_id='bob',actor_id='bob',permission='admin',granted_by='bob')
with identity_context(Identity('alice','alice')):
    assert 'error' in json.loads(_action_inspect_universe(universe_id='bob'))
    for center,profile,attack in cases:
        owner = root / center
        ensure_universe_registered(root,universe_id=center,universe_path=owner)
        grant_universe_access(root,universe_id=center,actor_id='alice',permission='admin',granted_by='alice')
        assert read_universe_file(owner,'positive.txt') == b'ALICE-POSITIVE-CONTROL'
        for relpath in paths:
            parent = open_dir_nofollow(owner)
            try:
                fd,_ = _open_regular_beneath(parent,relpath,max_bytes=1024)
                try:
                    info = os.fstat(fd)
                    # Proposed D58 predicate on the actual no-follow OPEN descriptor.
                    assert (info.st_uid in (1001,1003) and info.st_gid == 200000
                            and info.st_nlink == 1)
                finally:
                    os.close(fd)
            finally:
                os.close(parent)
            readers = {
                'universe-file': lambda: read_universe_file(owner,relpath),
                'platform-text': lambda: _read_platform_text(owner/relpath,'','strict').encode(),
                'api-file-read': lambda: api_file_read(root,center,relpath),
            }
            if relpath == 'activity.log':
                readers['inspect-universe'] = lambda: _action_inspect_universe(
                    universe_id=center).encode()
            for name,read in readers.items():
                answer=read()
                outcome='FOREIGN_BYTES' if sentinel in answer else 'NO_FOREIGN_BYTES'
                print(json.dumps(dict(profile=profile,attack=attack,path=relpath,reader=name,
                                      descriptor_gid_check=True,outcome=outcome)),flush=True)
                if outcome == 'FOREIGN_BYTES':
                    failures.append(':'.join((profile,attack,relpath,name)))
print(json.dumps(dict(failures=failures,foreign_reads=len(failures),
                     unchanged_outside=True,positive_control=True,
                     foreign_metadata_denied=True,profiles=3,attacks=2)),flush=True)
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
    return subprocess.run(command, input='CELL = ' + repr(CELL) + '\n' + CONTAINER,
                          text=True, timeout=180).returncode


if __name__ == '__main__':
    raise SystemExit(main())
