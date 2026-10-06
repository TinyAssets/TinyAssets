"""Independent cell I/O, cancellation and reaping in the production image."""
from __future__ import annotations

import argparse
import subprocess

from role_service_bootstrap_probe import CONTAINER

PROBE = r'''
from tinyassets.broker.owner_identities import owner_identity
from tinyassets.owner_launcher_client import OwnerLaunchRefused
def start(owner):
    return client.start_cell(kind='image-decoder', principal=owner,
        command_center='decoder-'+owner, identity=owner_identity(root,principal=owner),
        extra={'mime':'image/png'})
alice=start('alice')  # Intentionally withhold EOF; this decoder cannot finish.
with client._lock:
    client._channel.sendall(b'{"op":"STOP"}')
    assert client._reply()=={'op':'REFUSED'}
try:
    client.start_cell(kind='image-decoder',principal='alice',command_center='decoder-bob',
        identity=owner_identity(root,principal='alice'),extra={'mime':'image/png'})
except OwnerLaunchRefused: pass
else: raise AssertionError('foreign center admitted through START')
with start('bob') as bob:
    bob.stream.settimeout(10)
    bob.stream.sendall(out.getvalue()); bob.stream.shutdown(socket.SHUT_WR)
    result=bytearray()
    while chunk:=bob.stream.recv(65536): result.extend(chunk)
    assert bob.wait(10)==0
    header,_,payload=bytes(result).partition(b'\n')
    proof=json.loads(header)['cell']
    assert proof['uid']==identities['bob']-300000 and proof['gid']==proof['uid']
    assert proof['fds']==[0,1,2] and proof['groups']==[] and not proof['nested_userns']
    _,_,png=payload.partition(b'\n')
    assert Image.open(io.BytesIO(png)).size==(8,8)
try:
    client.decode(out.getvalue(),'image/png',principal='alice',command_center='decoder-alice',
                  identity=owner_identity(root,principal='alice'))
except OwnerLaunchRefused: pass
else: raise AssertionError('blocking spawn suspended active cell supervision')
assert alice.cancel()==-9
alice.close()
with start('alice') as completed:
    completed.stream.sendall(out.getvalue()); completed.stream.shutdown(socket.SHUT_WR)
    while completed.stream.recv(65536): pass
    assert completed.wait(10)==0
    assert completed.wait()==0
# Fork descendants cannot retain either the data or lifetime capability.
cell=start('alice')
pid=os.fork()
if pid==0:
    assert cell.stream.fileno()==-1 and cell._status.fileno()==-1
    try: cell.cancel()
    except RuntimeError: os._exit(0)
    os._exit(1)
assert os.waitpid(pid,0)[1]==0
assert cell.cancel()==-9; cell.close()
# Losing the handle must revoke the cell without a privileged kill API.
orphan=start('bob'); orphan._status.close(); orphan._closed=True
orphan.stream.settimeout(5)
# Keep stdin open: normal decoder EOF cannot explain this cell's termination.
while orphan.stream.recv(65536): pass
orphan.stream.close()
import time
time.sleep(2)
assert client.decode(out.getvalue(),'image/png',principal='alice',command_center='decoder-alice',
    identity=owner_identity(root,principal='alice')).returncode==0  # no orphan jobs remain
with start('bob') as after:
    assert after.cancel()==-9
with start('alice') as timed:
    assert timed.wait(40)==-9  # fixed mapper deadline also applies without client cancellation
held=[start('alice') for _ in range(4)]
try:
    try: start('alice')
    except OwnerLaunchRefused: pass
    else: raise AssertionError('owner concurrency bound absent')
    with start('bob') as independent:
        assert independent.cancel()==-9
finally:
    for item in held: item.close()
print('D76 independent owner streams PASS; Bob completes while Alice blocks; '
      'cancel/reap -9; completed receipt repeat; fork handles closed; '
      'EOF revocation/reaped; fixed deadline; active STOP refused; foreign START refused; '
      'legacy blocking refusal; owner concurrency bound; zero foreign reads',flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    marker = "for path in (root/'.broker/outbound.db'"
    assert CONTAINER.count(marker) == 1
    script = ('DEATH=""\nFAIL=False\nGIT=False\nSNAPSHOTS=False\n'
              + CONTAINER.replace(marker, PROBE + '\n' + marker))
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print('production image:', digest, flush=True)
    return subprocess.run(command, input=script, text=True, timeout=180).returncode


if __name__ == '__main__':
    raise SystemExit(main())
