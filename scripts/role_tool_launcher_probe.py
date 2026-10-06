"""Actual offline tool operations inside dedicated Alice/Bob owner cells."""
from __future__ import annotations

import argparse
import subprocess

from role_service_bootstrap_probe import CONTAINER

SETUP = r'''
for owner,uid in identities.items():
    center=root/('decoder-'+owner)
    for name in ('.agent-workspace','skills','prompts','extensions',
                 'workflows','bin','notes','wiki'):
        path=center/name; path.mkdir(); os.chown(path,uid,uid); path.chmod(0o700)
        subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',str(path)],check=True)
    own=center/'.agent-workspace/own.txt'; own.write_text(owner+'-own-bytes')
    os.chown(own,uid,uid); own.chmod(0o660)
    subprocess.run(['setfacl','-m','u:1001:rw',str(own)],check=True)
    brain=center/'MEMORY.md'; brain.write_text(owner+'-memory')
    os.chown(brain,uid,uid); brain.chmod(0o660)
    subprocess.run(['setfacl','-m','u:1001:rw',str(brain)],check=True)
    from PIL import Image
    picture=center/'.agent-workspace/own.png'
    Image.new('RGB',(7,5),'blue').save(picture)
    os.chown(picture,uid,uid); picture.chmod(0o600)
alice=root/'decoder-alice/.agent-workspace'
bob=root/'decoder-bob/.agent-workspace'
os.link(bob/'own.txt',alice/'foreign-hardlink')
os.symlink(str(bob/'own.txt'),alice/'foreign-symlink')
os.mkfifo(alice/'preplanted-fifo',0o600)
os.chown(alice/'preplanted-fifo',identities['alice'],identities['alice'])
'''

TOOL = r'''
from tinyassets import universe_tools as tools
for owner in identities:
    center=root/('decoder-'+owner)
    with identity_context(Identity(owner,owner)):
        assert owner+'-own-bytes' in tools.read_file(center,'own.txt',agent_id='main')
        assert 'wrote' in tools.write_file(center,'notes/proof.txt',owner+'-write',agent_id='main')
        assert owner+'-write' in tools.read_file(center,'notes/proof.txt',agent_id='main')
        assert 'edited' in tools.edit_file(center,'notes/proof.txt',owner+'-write',
                                          owner+'-edit',agent_id='main')
        assert owner+'-edit' in tools.read_file(center,'notes/proof.txt',agent_id='main')
        assert owner+'-memory' in tools.read_file(center,'MEMORY.md',agent_id='main')
        picture=tools.read_file(center,'own.png',agent_id='main')
        assert isinstance(picture,tools.ToolImage) and (picture.width,picture.height)==(7,5)
        script = """
import ctypes,json,os,socket
from pathlib import Path
assert os.getuid()==os.getgid()==EXPECTED
fds=[]
for name in os.listdir('/proc/self/fd'):
    fd=int(name)
    try: os.fstat(fd)
    except OSError: continue
    fds.append(fd)
    assert fd<=2
    try: escaped=os.open('..',os.O_RDONLY|os.O_DIRECTORY,dir_fd=fd)
    except OSError: pass
    else: os.close(escaped); raise AssertionError('host parent FD')
assert ctypes.CDLL(None,use_errno=True).unshare(0x10000000)==-1
for path in ('/data','/center','/app','/u/owner.json','/u/.credential-vault.json',
             '/u/.runtime','/proc/1/root/data/.broker/outbound.db','foreign-hardlink',
             'foreign-symlink'):
    try: Path(path).read_bytes()
    except OSError: pass
    else: raise AssertionError('unexpected read '+path)
for path in ('foreign-hardlink','foreign-symlink'):
    try: os.chown(path,os.getuid(),os.getgid())
    except OSError: pass
    else: raise AssertionError('foreign relabel')
with socket.socket() as network:
    network.settimeout(.2)
    assert network.connect_ex(('127.0.0.1',39281))!=0
print(json.dumps(dict(uid=os.getuid(),fds=sorted(fds),foreign_bytes=0)))
""".replace('EXPECTED',str(identities[owner]-300000))
        result=tools.run_jailed(center,['/usr/local/bin/python','-I','-c',script],agent_id='main')
        assert result.exit_code==0,(owner,result)
        assert json.loads(result.output)['foreign_bytes']==0
        print(owner+' tool native matrix '+result.output.decode().strip(),flush=True)
        result=tools.run_jailed(center,['/bin/sh','-c','sleep 10'],agent_id='main',wall_seconds=.2)
        assert result.killed=='timeout',result
        result=tools.run_jailed(center,['/bin/sh','-c','yes output'],
                                agent_id='main',output_bytes=1024)
        assert result.killed=='output_limit' and len(result.output)<=1024,result
        result=tools.run_jailed(center,['/bin/cat'],agent_id='main',stdin=b'input-roundtrip')
        assert result.output==b'input-roundtrip',result
        floor=tools.jail_disk.floor_breach
        try:
            tools.jail_disk.floor_breach=lambda *args,**kwargs: 'synthetic full disk'
            try: tools.run_jailed(center,['/bin/true'],agent_id='main')
            except tools.UniverseToolError as exc: assert 'nothing ran' in str(exc)
            else: raise AssertionError('disk floor started a tool')
        finally:
            tools.jail_disk.floor_breach=floor
        if owner=='alice':
            from dataclasses import replace
            maximum=tools.MAX_IMAGE_SOURCE_BYTES
            result=tools.run_jailed(center,['/usr/bin/head','-c',str(maximum),'/dev/zero'],
                agent_id='main',limits=replace(tools.DEFAULT_LIMITS,output_bytes=maximum))
            assert result.exit_code==0 and len(result.output)==maximum,result.exit_code
            assert result.output==bytes(maximum)
        if owner=='alice':
            result=tools.run_jailed(center,['/bin/cat','preplanted-fifo'],agent_id='main',wall_seconds=.2)
            assert result.killed=='timeout',result
        other=root/('decoder-'+('bob' if owner=='alice' else 'alice'))
        try: tools.run_jailed(other,['/bin/true'],agent_id='main')
        except PermissionError: pass
        else: raise AssertionError('foreign center accepted')
        print(owner+': real tool read/write/edit/stdin, limits and foreign scope PASS',flush=True)
print('offline tool-jail staged acceptance PASS; ZERO FOREIGN_BYTES; '
      'socket forwarding not exercised here',flush=True)
'''


def main(*, timeout=300):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert CONTAINER.count(marker) == 1
    script = ('DEATH=""\nFAIL=False\nGIT=True\nSNAPSHOTS=False\n'
              + CONTAINER.replace(marker, TOOL + '\n' + marker))
    setup_marker = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    assert script.count(setup_marker) == 1
    script = script.replace(setup_marker, SETUP + '\n' + setup_marker)
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print('production image:', digest, flush=True)
    return subprocess.run(command, input=script, text=True, timeout=timeout).returncode


if __name__ == '__main__':
    raise SystemExit(main())
