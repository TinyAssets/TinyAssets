"""Installed-image package stdio, immutable revisions, limits and owner denials."""
from __future__ import annotations

import argparse
import subprocess

from role_service_bootstrap_probe import CONTAINER

PYTHON = r'''
import ctypes,json,os,resource,socket,sys
import helper
from pathlib import Path
assert helper.VALUE==17
assert os.getuid()==os.getgid()==EXPECTED
assert os.statvfs('/package').f_flag & os.ST_RDONLY
assert os.statvfs('/tmp').f_blocks*os.statvfs('/tmp').f_frsize<=256*1024*1024
assert 'SYNTHETIC_PARENT_SECRET' not in os.environ
assert resource.getrlimit(resource.RLIMIT_NOFILE)==(128,128)
assert resource.getrlimit(resource.RLIMIT_NPROC)==(64,64)
assert resource.getrlimit(resource.RLIMIT_CORE)==(0,0)
assert ctypes.CDLL(None,use_errno=True).unshare(0x10000000)==-1
fds=[]
for name in os.listdir('/proc/self/fd'):
    try: os.fstat(int(name))
    except OSError: continue
    fds.append(int(name))
assert sorted(fds)==[0,1,2],fds
for path in ('/data','/run/tinyassets','/package/../data',
             '/proc/1/root/data/.broker/outbound.db','/package-broker.sock'):
    try: Path(path).read_bytes()
    except OSError: pass
    else: raise AssertionError('foreign read '+path)
try: Path('/package/main.py').write_text('replacement')
except OSError: pass
else: raise AssertionError('mutable package')
with socket.socket() as connection:
    connection.settimeout(.1)
    assert connection.connect_ex(('93.184.216.2',443))!=0
print(json.dumps(dict(uid=os.getuid(),foreign_bytes=0,immutable=True)),flush=True)
for line in sys.stdin:
    request=json.loads(line)
    print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':{
        'protocolVersion':'2024-11-05','capabilities':{},
        'serverInfo':{'name':'package-fixture','version':'1'}}}),flush=True)
'''

NODE = r'''
const fs=require('fs'), rl=require('readline');
if(process.getuid()!==EXPECTED) throw new Error('wrong identity');
try {fs.writeFileSync('/package/main.js','replacement'); throw new Error('mutable');}
catch(e) {if(e.message==='mutable') throw e;}
process.stdout.write(JSON.stringify({uid:process.getuid(),foreign_bytes:0})+'\n');
rl.createInterface({input:process.stdin}).on('line',line=>{
 const request=JSON.parse(line);
 process.stdout.write(JSON.stringify({jsonrpc:'2.0',id:request.id,result:{ok:true}})+'\n');
});
'''

BROKER = r'''
import json,os,socket
def request(value):
 with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as channel:
  channel.connect(os.environ['TINYASSETS_PACKAGE_BROKER'])
  channel.sendall(json.dumps(value).encode()+b'\n')
  with channel.makefile('rb') as reader: return json.loads(reader.readline())
assert 'error' in request({'slot':'foreign','request':{}})
assert 'error' in request({'slot':'read','principal':'foreign','request':{}})
answer=request({'slot':'read','request':{'url':'https://uid-stream.invalid/catalogue'}})
assert answer.get('result',{}).get('delivered') is True,answer
assert answer['result']['response']['status']==200,answer
assert 'launcher-broker-stream' in answer['result']['response']['body']
assert 'synthetic-uid-oracle-token' not in json.dumps(answer)
assert 'headers' not in answer['result']['response']
refused=request({'slot':'read','request':{'url':'https://169.254.169.254/latest'}})
assert refused.get('result',{}).get('delivered') is not True,refused
print(json.dumps(dict(broker_only=True,pinned_egress=True,foreign_bytes=0)),flush=True)
'''

SETUP = r'''
import hashlib
packages={}
for owner,uid in identities.items():
    center=root/('decoder-'+owner)
    subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',str(center)],check=True)
    for part in (center/'.runtime',center/'.runtime/package-cells'):
        part.mkdir(); os.chown(part,1001,1001); part.chmod(0o700)
        subprocess.run(['setfacl','-m',f'u:{uid}:x',str(part)],check=True)
    definitions={
        'python':('python','main.py',PYTHON.replace('EXPECTED',str(uid-300000))),
        'node':('node','main.js',NODE.replace('EXPECTED',str(uid-300000))),
        'memory':('python','main.py',"import time\nprint('ready',flush=True)\na=[]\n"
                  "while True:\n a.append(bytearray(32*1024*1024)); time.sleep(.03)\n"),
        'cancel':('python','main.py',"import os,time\n"
                  "if os.fork()==0:\n os.setsid(); time.sleep(600)\n"
                  "else:\n print('ready',flush=True); time.sleep(600)\n"),
        'stop-supervisor':('node','main.js',"process.kill(process.ppid,'SIGSTOP');\n"
            "console.log('ready');const a=[];"
            "setInterval(()=>a.push(Buffer.alloc(32*1024*1024,1)),30);"),
        'shell':('shell','main.sh',
            "printf '{\"uid\":%s,\"foreign_bytes\":0}\\n' \"$(id -u)\"\n"
            "while IFS= read -r line; do printf '%s\\n' \"$line\"; done\n"),
    }
    if os.environ.get('TA_ORACLE_HTTPS')=='1':
        definitions['broker']=('python','main.py',BROKER)
    packages[owner]={}
    for name,(runtime,entry,source) in definitions.items():
        data=source.encode()
        contents={entry:data}
        if name=='python': contents['helper.py']=b'VALUE=17\n'
        raw=json.dumps(dict(runtime=runtime,entry=entry,args=[],
                            slots=['read'] if name=='broker' else [],
                            files={path:hashlib.sha256(value).hexdigest()
                                   for path,value in contents.items()})).encode()
        revision=hashlib.sha256(raw).hexdigest()
        package=center/'.runtime/package-cells'/revision
        package.mkdir(); os.chown(package,1001,1001); package.chmod(0o700)
        for filename,value in {'manifest.json':raw,**contents}.items():
            path=package/filename; path.write_bytes(value); os.chown(path,1001,1001)
            path.chmod(0o400)
            subprocess.run(['setfacl','--set',f'u::r,u:{uid}:r,g::-,m::r,o::-',
                            str(path)],check=True)
        package.chmod(0o500)
        subprocess.run(['setfacl','-k',str(package)],check=True)
        subprocess.run(['setfacl','--set',f'u::rx,u:{uid}:rx,g::-,m::rx,o::-',
                        str(package)],check=True)
        packages[owner][name]=revision
if os.environ.get('TA_ORACLE_HTTPS')=='1':
    from tinyassets.credential_vault import http_credential_record
    from tinyassets.storage.outbound_connections import ConnectionLedger
    fixture=runpy.run_path('/app/scripts/role_stream_oracle.py')
    ledger=ConnectionLedger(root/'.broker/outbound.db',data_root=root)
    for owner,uid in identities.items():
        center=root/('decoder-'+owner)
        subprocess.run(['setfacl','-m','u:1002:x',str(center)],check=True)
        vault=center/'.credential-vault.json'
        vault.write_text(json.dumps([http_credential_record(
            destination='package-fixture',token=fixture['TOKEN'])]))
        os.chown(vault,1001,1102); vault.chmod(0o640)
        ledger.create_connection(connection_id='package-'+owner,owner_user_id=owner,
            connection_class='http',connection_type='http',auth_scheme='bearer',
            scopes=('GET',),provider='http',destination='package-fixture',
            credential_ref='vault://http/package-fixture',allowed_endpoints=[
                dict(host=fixture['HOST'],path_template='/catalogue',methods=['GET'])])
        ledger.grant_connection(grant_id='package-grant-'+owner,connection_id='package-'+owner,
                                owner_user_id=owner,universe_id=center.name)
    for path in (root/'.broker').glob('outbound.db*'):
        os.chown(path,1002,1101); path.chmod(0o600)
'''

PROBE = r'''
from tinyassets import role_packages
os.environ['SYNTHETIC_PARENT_SECRET']='must-not-cross'
for owner in identities:
    center=root/('decoder-'+owner)
    with identity_context(Identity(owner,owner)):
        for runtime in ('python','node','shell'):
            with role_packages.start(center,packages[owner][runtime]) as cell:
                with cell.stream.makefile('rb') as reader:
                    proof=json.loads(reader.readline())
                    assert proof['uid']==identities[owner]-300000 and proof['foreign_bytes']==0
                    for number in (1,2):
                        request=dict(jsonrpc='2.0',id=number,method='initialize',params={})
                        cell.stream.sendall(json.dumps(request).encode()+b'\n')
                        assert json.loads(reader.readline())['id']==number
                    cell.stream.shutdown(socket.SHUT_WR)
                    assert reader.read()==b''
                assert cell.wait(10)==0
            print(owner+' '+runtime+': actual stdio/immutable package PASS; ZERO FOREIGN_BYTES',
                  flush=True)
        with role_packages.start(center,packages[owner]['memory']) as cell:
            assert cell.stream.recv(6)==b'ready\n'
            assert cell.wait(30) in (1,137,-9)
        with role_packages.start(center,packages[owner]['stop-supervisor']) as cell:
            assert cell.stream.recv(6)==b'ready\n'
            assert cell.wait(30)==-9
        print(owner+': stopped-supervisor Node Buffer attack killed by mapper PASS',flush=True)
        with role_packages.start(center,packages[owner]['cancel']) as cell:
            assert cell.stream.recv(6)==b'ready\n'
            assert cell.cancel()!=0
        other='bob' if owner=='alice' else 'alice'
        try:
            with role_packages.start(root/('decoder-'+other),packages[other]['python']):
                raise AssertionError('foreign package admitted')
        except PermissionError: pass
        try:
            with role_packages.start(center,'0'*64):
                raise AssertionError('uninstalled revision admitted')
        except FileNotFoundError: pass
        print(owner+': memory cap, descendant cancellation, foreign refusal and reuse PASS',
              flush=True)
        if os.environ.get('TA_ORACLE_HTTPS')=='1':
            from tinyassets import agent_review,agent_rules
            from tinyassets.storage.effector_consents import grant_consent
            from tinyassets.ta_capabilities import Capabilities,ExecutionContext
            agent_rules.set_rule(center,'app.write',agent_rules.DO,agent='main')
            agent_review.set_review(center,'app.write',False,confirm=True,agent='main')
            grant_consent(center,sink='authenticated_external_call',destination='package-fixture',
                          granted_by=owner)
            caps=Capabilities(center,ExecutionContext(center.name,owner,'main'),[],None,lambda:None)
            with role_packages.start(center,packages[owner]['broker'],capabilities=caps,
                    slots={'read':'connection:package-'+owner+':GET'}) as cell:
                with cell.stream.makefile('rb') as reader:
                    answer=json.loads(reader.readline())
                    assert answer==dict(broker_only=True,pinned_egress=True,foreign_bytes=0)
                assert cell.wait(10)==0
            print(owner+': real broker TLS/bearer through declared slot PASS; ZERO FOREIGN_BYTES',
                  flush=True)
print('package cell acceptance PASS; ZERO FOREIGN_BYTES; startup OFF',flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--stream', action='store_true')
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    setup_marker = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    probe_marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert CONTAINER.count(setup_marker) == CONTAINER.count(probe_marker) == 1
    script = ("DEATH=''\nFAIL=False\nGIT=False\nSNAPSHOTS=False\n"
              + f'PYTHON={PYTHON!r}\nNODE={NODE!r}\nBROKER={BROKER!r}\n'
              + CONTAINER.replace(setup_marker, SETUP + '\n' + setup_marker)
                         .replace(probe_marker, PROBE))
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print('production image:', digest, flush=True)
    if args.stream:
        from linux_oracle import production_stream_oracle

        command.remove('-i')
        command[-1:] = ['-c', script]
        return production_stream_oracle(command, digest)
    return subprocess.run(command, input=script, text=True, timeout=300).returncode


if __name__ == '__main__':
    raise SystemExit(main())
