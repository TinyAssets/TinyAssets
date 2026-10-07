"""Count <defunct> processes while PID1 is the daemon (overlay topology, D60).

Real broker and mapper, PID1 retired to the capability-free daemon with its
orphan reaper. Three engine turns run in provider-exec cells, each forking
children every second (waited, double-fork orphan, setsid orphan). A
daemon-direct leg runs ``sh -c 'sleep &'``, which orphans a grandchild straight
to PID1. A PID1 sampler counts zombies by parent and name, then requires zero
after the reaper's grace. Synthetic container state only; about six minutes.
"""
from __future__ import annotations

import argparse
import sys

import role_provider_discovery_probe
import role_service_bootstrap_probe as probe

SECONDS, TURNS, DIRECT = 60, 3, 60

TURN = f'''
import os,time
print('ready',flush=True)
end=time.monotonic()+{SECONDS}
forks=0
while time.monotonic()<end:
    pid=os.fork()
    if pid==0: os._exit(0)
    os.waitpid(pid,0)
    for detach in (False,True):
        pid=os.fork()
        if pid==0:
            if detach: os.setsid()
            if os.fork()==0:
                time.sleep(0.5); os._exit(0)
            os._exit(0)
        os.waitpid(pid,0)
    forks+=5
    print('tick',flush=True)  # turns stream output; a silent 150s cell was SIGKILLed
    time.sleep(1)
print('alive',forks,flush=True)
'''

BODY = r'''
import asyncio,threading,time
from tinyassets.providers.owned_process import aspawn_owned
from tinyassets.providers.provider_jail import provider_launch_scope
def zombies():
    found=[]
    for name in os.listdir('/proc'):
        if not name.isdigit(): continue
        try: raw=Path('/proc',name,'stat').read_text()
        except OSError: continue
        fields=raw[raw.rfind(')')+2:].split()
        if fields[0]=='Z': found.append((int(fields[1]),raw[raw.find('(')+1:raw.rfind(')')]))
    ppids=[p for p,_ in found]
    return dict(total=len(found),pid1=ppids.count(1),other=len(found)-ppids.count(1),
                names=sorted(n for _,n in found))
samples=[]
started=time.monotonic()
done=threading.Event()
def sample():
    while not done.wait(10):
        samples.append((round(time.monotonic()-started),'turn',zombies()))
def direct():
    # Daemon-direct helper whose child backgrounds a grandchild and exits.
    for _ in range(DIRECT):
        subprocess.run(['/bin/sh','-c','/bin/sleep 0.2 & exit 0'],check=True)
        time.sleep(1)
samples.append((0,'before',zombies()))
threading.Thread(target=sample,daemon=True).start()
async def long_turn():
    owner='alice'
    center=root/('decoder-'+owner)
    snapshot=center/'.runtime/provider-launch-credentials/fixture'
    with identity_context(Identity(owner,owner)):
        with provider_launch_scope(center,credential_dir=snapshot):
            proc=await aspawn_owned(['/usr/local/bin/python3.11','-I','-S','-c',TURN],env={},
                stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE)
            stdout,stderr=await proc.communicate()
    assert proc.returncode==0 and b'\nalive ' in stdout,(proc.returncode,stdout[-200:],stderr)
    return int(stdout.split()[-1])
forks=0
for turn in range(TURNS):
    forks+=asyncio.run(long_turn())
    samples.append((round(time.monotonic()-started),f'after-turn-{turn}',zombies()))
leg=threading.Thread(target=direct)
leg.start(); leg.join()
samples.append((round(time.monotonic()-started),'after-direct',zombies()))
time.sleep(80)  # reaper grace 60s + interval 10s, with margin
done.set()
samples.append((round(time.monotonic()-started),'settled',zombies()))
for at,phase,counts in samples:
    print(f'zombies t={at}s {phase}: {counts}',flush=True)
peak=max(c['total'] for _,_,c in samples)
print(json.dumps(dict(turns=TURNS,turn_forks=forks,direct_orphans=DIRECT,peak=peak,
    settled=samples[-1][2]['total'])),flush=True)
assert samples[-1][2]['total']==0,samples[-1]
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    setup = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert probe.CONTAINER.count(setup) == probe.CONTAINER.count(marker) == 1
    probe.CONTAINER = (f'TURN={TURN!r}\nTURNS={TURNS}\nDIRECT={DIRECT}\n' + probe.CONTAINER
                       .replace(setup, role_provider_discovery_probe.SETUP + '\n' + setup)
                       .replace(marker, BODY))
    # Stream mode supplies the egress relay provider-exec pins and no 180s cap.
    sys.argv = [sys.argv[0], '--image', args.image, '--stream']
    return probe.main()


if __name__ == '__main__':
    raise SystemExit(main())
