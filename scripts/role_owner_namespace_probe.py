"""D60 production-image feasibility: kernel-bounded owner identity mapping.

Synthetic data, no mounts or network. This does not admit an engine class or
replace the reader matrix. The privileged parent only installs startup maps;
the serving candidate retains SETUID/SETGID inside that bounded user namespace.
"""
from __future__ import annotations

import argparse
import json
import subprocess

CONTAINER = r'''
import ctypes, json, os, runpy, socket, subprocess, sys, tempfile
from pathlib import Path
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
launch['verify_chain']()
launch['_assert_caps'](launch['ENTRY_CAPS'])
root = Path(tempfile.mkdtemp(prefix='owner-map-')); root.chmod(0o755)
for uid, name in ((300001,'alice'), (300002,'bob'), (1002,'broker')):
    directory = root / name; directory.mkdir(); os.chown(directory,uid,uid)
    directory.chmod(0o700)
    target = directory / 'private'; target.write_text(name.upper() + '-PRIVATE')
    os.chown(target,uid,uid); target.chmod(0o600)
alias = root / 'alice' / 'foreign'
os.link(root / 'bob' / 'private',alias)
(root / 'bob' / 'private').unlink()
os.setgroups([])
parent, child = socket.socketpair()
pid = os.fork()
if pid == 0:
    parent.close()
    try:
        libc = ctypes.CDLL(None,use_errno=True)
        launch['_checked'](libc.unshare(0x10000000),'owner user namespace')
        child.sendall(b'R'); assert child.recv(1) == b'M'; child.close()
        os.setresgid(0,0,0); os.setresuid(0,0,0)
        # Retire every bounding capability while SETPCAP is still effective.
        # Only mapping capabilities remain permitted/effective, in this userns.
        for cap in range(int(Path('/proc/sys/kernel/cap_last_cap').read_text()) + 1):
            launch['_checked'](libc.prctl(24,cap,0,0,0),'retire bounding')
        launch['_capset']((1<<6)|(1<<7))
        fields = launch['status']()
        assert all(int(fields[k],16)==0 for k in ('CapBnd','CapAmb','CapInh'))
        assert int(fields['CapEff'],16)==int(fields['CapPrm'],16)==192
        assert int(fields['NoNewPrivs'])==1
        assert Path('/proc/self/uid_map').read_text().split()==['0','300000','100000']
        assert Path('/proc/self/gid_map').read_text().split()==['0','300000','100000']
        for switch in (os.setresuid,os.setresgid):
            for outside in (100000,200000,4294967294):
                try: switch(outside,outside,outside)
                except OSError: pass
                else: raise AssertionError('out-of-range mapping succeeded')
        pids=[]
        for inner,name in ((1,'alice'),(2,'bob')):
            worker=os.fork()
            if worker==0:
                try:
                    os.setgroups([]); os.setresgid(inner,inner,inner)
                    os.setresuid(inner,inner,inner); launch['_capset'](0)
                    fields=launch['status']()
                    assert all(int(fields[k],16)==0 for k in launch['CAP_FIELDS'])
                    own=root/name; (own/'positive').write_text(name)
                    assert (own/'positive').read_text()==name
                    for foreign in (root/('bob' if name=='alice' else 'alice'),root/'broker'):
                        try: list(foreign.iterdir())
                        except PermissionError: pass
                        else: raise AssertionError('foreign directory readable')
                    if name=='alice':
                        assert (own/'foreign').stat().st_uid==2
                        for attack in ('read','relabel','copy'):
                            try:
                                if attack=='relabel': os.chown(own/'foreign',-1,inner)
                                else:
                                    data=(own/'foreign').read_bytes()
                                    if attack=='copy': (own/'copy').write_bytes(data)
                            except PermissionError: pass
                            else: raise AssertionError('FOREIGN_BYTES via '+attack)
                        factory=runpy.run_path('/app/tinyassets/providers/jail_seccomp.py')['program_fd']
                        for profile in ('cell-deny','cell-links','cell-nested'):
                            fd=factory(profile=profile)
                            argv=['/usr/bin/bwrap','--die-with-parent','--new-session','--unshare-all',
                                  '--cap-drop','ALL','--clearenv']
                            for path in ('/usr','/opt','/app','/etc','/bin','/lib','/lib64'):
                                if os.path.exists(path): argv+=['--ro-bind',path,path]
                            argv+=['--proc','/proc','--dev','/dev','--tmpfs','/tmp',
                                   '--bind',str(own),'/owner','--chdir','/owner','--seccomp',str(fd),
                                   '--','/opt/venv/bin/python','-I','-B','-c',CELL]
                            result=subprocess.run(argv,pass_fds=(fd,),capture_output=True,text=True)
                            os.close(fd)
                            assert result.returncode==0,(profile,result.stdout,result.stderr)
                            print(json.dumps(dict(profile=profile,cell=json.loads(result.stdout))),flush=True)
                    print(json.dumps(dict(owner=name,inner_uid=os.getuid(),
                        host_uid=300000+os.getuid(),capabilities='zero',
                        foreign_denied=True,positive_control=True)),flush=True)
                    os._exit(0)
                except BaseException:
                    import traceback; traceback.print_exc(); os._exit(1)
            pids.append(worker)
        for worker in pids:
            assert os.waitpid(worker,0)[1]==0
        print(json.dumps(dict(mapping='0 300000 100000',out_of_range_denied=True,
              retained='SETUID/SETGID in owner userns only',bounding='zero',
              foreign_reads=0,actual_engine_classes=False)),flush=True)
        os._exit(0)
    except BaseException:
        import traceback; traceback.print_exc(); os._exit(1)
child.close()
assert parent.recv(1)==b'R'
Path('/proc/'+str(pid)+'/uid_map').write_text('0 300000 100000\n')
Path('/proc/'+str(pid)+'/gid_map').write_text('0 300000 100000\n')
parent.sendall(b'M'); parent.close()
# The startup mapper retains no privilege while waiting. No runtime helper.
launch['retire_child']('daemon')
raise SystemExit(os.waitstatus_to_exitcode(os.waitpid(pid,0)[1]))
'''

CELL = r'''
import json, os
from pathlib import Path
for name in os.listdir('/proc/self/fd'):
    fd=int(name)
    if fd>2:
        try: os.close(fd)
        except OSError as exc:
            if exc.errno!=9: raise
fields=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
assert all(int(fields[k],16)==0 for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb'))
assert int(fields['NoNewPrivs'])==1 and int(fields['Seccomp'])==2
assert os.getuid()==os.getgid()==1 and os.getgroups()==[]
assert Path('/owner/positive').read_text()=='alice'
target=Path('/owner/foreign')
assert target.stat().st_uid==65534 and target.stat().st_gid==65534
assert target.stat().st_nlink==1
for attack in ('read','relabel','copy'):
    try:
        if attack=='relabel': os.chown(target,-1,os.getgid())
        else:
            data=target.read_bytes()
            if attack=='copy': Path('/owner/copied').write_bytes(data)
    except PermissionError: pass
    else: raise AssertionError('FOREIGN_BYTES via '+attack)
print(json.dumps(dict(uid=os.getuid(),gid=os.getgid(),foreign_reads=0,
                     attacks_denied=['read','relabel','copy'],capabilities='zero',
                     positive_control=True,nnp=1,seccomp=2)))
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
    return subprocess.run(command, input='CELL='+repr(CELL)+'\n'+CONTAINER,
                          text=True, timeout=180).returncode


if __name__ == '__main__':
    raise SystemExit(main())
