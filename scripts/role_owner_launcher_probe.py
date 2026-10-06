"""D68 actual decoder through the bounded owner launcher, production image.

Synthetic state only; no host mounts, network or startup activation.
"""
from __future__ import annotations

import argparse
import subprocess

CONTAINER = r'''
import array, io, json, os, runpy, socket, sys, tempfile, time, traceback
from pathlib import Path
sys.path.insert(0, '/app')
from PIL import Image
launch = runpy.run_path('/usr/local/libexec/ta-launch.py')
launch['verify_chain']()
bounded = runpy.run_path('/usr/local/libexec/ta-owner-launch.py')
root = Path(tempfile.mkdtemp(prefix='owner-launch-')); root.chmod(0o755)
private = root / '.broker'; private.mkdir(mode=0o700); os.chown(private,1002,1002)
reader, writer = socket.socketpair()
broker = os.fork()
if broker == 0:
    try:
        reader.close(); launch['retire_child']('broker')
        launch['close_descriptors']((writer.fileno(),))
        from tinyassets.broker.owner_identities import OwnerIdentities
        identities = OwnerIdentities(private/'owner-identities.db', initialize=True)
        result = {name: identities.resolve(name,allocate=True).uid for name in ('alice','bob')}
        writer.sendall(json.dumps(result).encode()); writer.close(); os._exit(0)
    except BaseException:
        traceback.print_exc(); os._exit(1)
writer.close(); identities = json.loads(reader.recv(4096)); reader.close()
assert os.waitpid(broker,0)[1] == 0
bindings = {}
for name, uid in identities.items():
    center = root / ('decoder-'+name); center.mkdir(); os.chown(center,uid,uid); center.chmod(0o700)
    (center/'workspace').mkdir()
    for filename in ('workspace/private','.credential-vault.json','owner.json'):
        target = center/filename; target.write_text(name+'-sentinel'); os.chown(target,uid,uid)
        target.chmod(0o600)
    bindings[(name,center.name)] = uid
parent, child = socket.socketpair(socket.AF_UNIX,socket.SOCK_SEQPACKET)
child.setsockopt(socket.SOL_SOCKET,socket.SO_PASSCRED,1)
ready_parent, ready_child = socket.socketpair()
daemon_pid = os.getpid(); daemon_pidfd = os.pidfd_open(daemon_pid)
mapper = os.fork()
if mapper == 0:
    try:
        parent.close(); ready_parent.close()
        launch['close_descriptors']((child.fileno(),ready_child.fileno(),daemon_pidfd))
        bounded['enter_namespace'](ready_child,launch)
        for switch in (os.setresuid,os.setresgid):
            for outside in (100000,200000,4294967294):
                try: switch(outside,outside,outside)
                except OSError: pass
                else: raise AssertionError('out-of-range identity admitted')
        server=bounded['OwnerLauncher'](child,daemon_pid,daemon_pidfd,bindings,root,launch)
        ready_child.sendall(b'S'); ready_child.close()
        baseline=len(os.listdir('/proc/self/fd'))
        while server.serve_one():
            assert len(os.listdir('/proc/self/fd'))==baseline, 'received descriptor leak'
            bounded['assert_mapper'](launch)
        print(json.dumps(dict(bounded_launcher=True,retained='SETUID/SETGID in owner userns',
                              bounding='zero',out_of_range_denied=True)),flush=True)
        os._exit(0)
    except BaseException:
        traceback.print_exc(); os._exit(1)
child.close(); ready_child.close(); os.close(daemon_pidfd)
bounded['install_maps'](mapper,ready_parent)
launch['retire_child']('daemon')
launch['close_descriptors']((parent.fileno(),ready_parent.fileno()))
assert ready_parent.recv(1)==b'S'; ready_parent.close()
parent.settimeout(40)
time.sleep(1.2)  # idle launcher stays available after its polling timeout
tcp=socket.socket(); tcp.bind(('127.0.0.1',39281)); tcp.listen(1)
abstract=socket.socket(socket.AF_UNIX,socket.SOCK_STREAM)
abstract.bind('\0ta-uid-cross-owner'); abstract.listen(1)
def request(document,fd=None):
    ancillary=[] if fd is None else [(socket.SOL_SOCKET,socket.SCM_RIGHTS,array.array('i',[fd]))]
    parent.sendmsg([json.dumps(document).encode()],ancillary)
def document(owner):
    return dict(op='SPAWN',kind='image-decoder',principal=owner,
                command_center='decoder-'+owner,mime='image/png')
if not CLIENT:
    # A descendant inherits the private endpoint, but never the daemon's PID.
    stranger=os.fork()
    if stranger==0:
        try:
            left,right=socket.socketpair()
            with left,right:
                parent.sendmsg([b''],[(socket.SOL_SOCKET,socket.SCM_RIGHTS,
                                      array.array('i',[right.fileno()]))])
                assert json.loads(parent.recv(4096))==dict(op='REFUSED')
            request(dict(op='STOP'))
            assert json.loads(parent.recv(4096))==dict(op='REFUSED')
            request(document('alice'))
            assert json.loads(parent.recv(4096))==dict(op='REFUSED')
            os._exit(0)
        except BaseException:
            traceback.print_exc(); os._exit(1)
    assert os.waitpid(stranger,0)[1]==0
    for bad in (dict(document('alice'),uid=300001),
                dict(document('alice'),command_center='decoder-bob'),
                dict(document('alice'),kind='ui-preview')):
        unused, partner = socket.socketpair()
        with unused, partner:
            request(bad,unused.fileno())
            assert json.loads(parent.recv(4096))==dict(op='REFUSED')
out=io.BytesIO(); Image.new('RGB',(8,8),'blue').save(out,format='PNG'); data=out.getvalue()
if CLIENT:
    from tinyassets.owner_launcher_client import OwnerLauncherClient, OwnerLaunchRefused
    from tinyassets.broker.owner_identities import OwnerIdentity
    client=OwnerLauncherClient(parent,mapper)
    descendant=os.fork()
    if descendant==0:
        try:
            assert parent.fileno()==-1
            try:
                client.decode(data,'image/png',principal='alice',command_center='decoder-alice',
                              identity=OwnerIdentity(300001,300001))
            except RuntimeError: pass
            else: raise AssertionError('fork child retained client authority')
            os._exit(0)
        except BaseException:
            traceback.print_exc(); os._exit(1)
    assert os.waitpid(descendant,0)[1]==0
    for center, expected in (('decoder-bob',300001),('decoder-alice',300002)):
        try:
            client.decode(data,'image/png',principal='alice',command_center=center,
                          identity=OwnerIdentity(expected,expected))
        except OwnerLaunchRefused: pass
        else: raise AssertionError('refused scope or mismatched identity was accepted')
    def decode_owner(owner):
        done=client.decode(data,'image/png',principal=owner,command_center='decoder-'+owner,
                           identity=OwnerIdentity(identities[owner],identities[owner]))
        assert done.returncode==0 and done.cell['uid']==identities[owner]-300000
        metadata,_,png=done.stdout.partition(b'\n')
        assert json.loads(metadata)['width']==8 and Image.open(io.BytesIO(png)).size==(8,8)
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(decode_owner,('alice','bob','alice','bob')))
    client.stop(); tcp.close(); abstract.close()
    assert os.waitpid(mapper,0)[1]==0
    print(json.dumps(dict(daemon_client=True,actual_png_owners=2,authenticated_replies=True,
        fork_channel_closed=True,serialized_concurrency=True,refusal_recovery=True,
        terminal_ack=True,startup_activated=False)),flush=True)
    raise SystemExit(0)
for owner in ('alice','bob'):
    input_end, output_end = socket.socketpair()
    with input_end, output_end:
        request(document(owner),output_end.fileno()); output_end.close()
        input_end.sendall(data); input_end.shutdown(socket.SHUT_WR)
        output=b''
        while chunk:=input_end.recv(65536): output+=chunk
    answer=json.loads(parent.recv(4096))
    expected=dict(op='SPAWN_DONE',returncode=0,uid=identities[owner],gid=identities[owner])
    assert answer==expected,answer
    header,_,payload=output.partition(b'\n'); proof=json.loads(header)['cell']
    assert proof['uid']==proof['gid']==identities[owner]-300000
    assert proof['fds']==[0,1,2] and proof['groups']==[] and proof['caps']=='zero'
    assert proof['nnp']==1 and proof['profile']=='cell-deny'
    assert all(proof['namespaces'][k]!=proof['host_namespaces'][k] for k in proof['namespaces'])
    # tool_images emits a JSON metadata line and the encoded PNG bytes.
    metadata,_,png=payload.partition(b'\n')
    assert json.loads(metadata)['width']==8
    assert Image.open(io.BytesIO(png)).size==(8,8)
    print(json.dumps(dict(engine_class='image-decoder',owner=owner,host_uid=identities[owner],
                         host_gid=identities[owner],cell=proof,actual_png=True)),flush=True)
# A real decoder waits on input; timeout must reap it with no retained CAP_KILL.
input_end, output_end=socket.socketpair()
with input_end, output_end:
    request(document('alice'),output_end.fileno()); output_end.close()
    answer=json.loads(parent.recv(4096))
    assert answer['op']=='SPAWN_DONE' and answer['returncode']==-9,answer
tcp.close(); abstract.close()
request(dict(op='STOP')); assert json.loads(parent.recv(4096))==dict(op='STOPPED')
parent.close()
assert os.waitpid(mapper,0)[1]==0
print(json.dumps(dict(actual_engine_classes=['image-decoder'],
    foreign_path_open_checks='decoder.decode: every denied path O_RDONLY and O_WRONLY',
    descendant_refused=True,request_identity_refused=True,foreign_scope_refused=True,
    timeout_reaped=True,descriptor_leaks=0,
    ui_preview_unadmitted=True,startup_activated=False)),flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--client', action='store_true')
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
    return subprocess.run(command, input='CLIENT='+repr(args.client)+'\n'+CONTAINER,
                          text=True, timeout=180).returncode


if __name__ == '__main__':
    raise SystemExit(main())
