"""D88: shipped adapters complete turns inside each owner's provider-exec cell.

Production image, synthetic owners, offline HTTPS fixture (``--stream``). The
real Codex and Claude CLIs run through the real adapters and the K2-shaped
app-server launch, with a persistent workspace and session store, and an
in-cell scan shows no owner credential in any cell environment, command line
or file outside the sealed read-only snapshot. Startup stays OFF.
"""

#: Runs after role_provider_discovery_probe.SETUP inside the container.
SETUP = r'''
for owner,uid in identities.items():
    center=root/('decoder-'+owner)
    fixture=center/'.runtime/provider-launch-credentials/fixture'
    config=fixture/'config.toml'
    config.write_text('model_provider = "oracle"\n[model_providers.oracle]\nname = "oracle"\n'
        'base_url = "https://uid-stream.invalid"\nwire_api = "responses"\n'
        'requires_openai_auth = false\nsupports_websockets = false\n')
    os.chown(config,1001,uid); config.chmod(0o600)
    subprocess.run(['setfacl','-m',f'u:{uid}:r',str(config)],check=True)
    claude=center/'.runtime/provider-launch-credentials/claude'; claude.mkdir()
    os.chown(claude,1001,uid); claude.chmod(0o700)
    subprocess.run(['setfacl','-m',f'u:{uid}:rx',str(claude)],check=True)
    token=claude/'auth.json'; token.write_text('sk-ant-oat01-c1-'+owner+'-fixture')
    os.chown(token,1001,uid); token.chmod(0o600)
    subprocess.run(['setfacl','-m',f'u:{uid}:r',str(token)],check=True)
    # What the owner's tool-files cell creates (D83/D88): owner-owned, with the
    # daemon's inherited default ACL so it can see whether a session exists.
    workspace=center/'.provider-workspace'; workspace.mkdir()
    os.chown(workspace,uid,uid); workspace.chmod(0o770)
    subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',str(workspace)],check=True)
'''

PROBE = r'''
import asyncio
SCAN = r"""
import json,os,subprocess,sys,time
needle=bytes.fromhex(sys.argv[1])[::-1]
names=[k for k in os.environ if k.endswith('_FILE_DESCRIPTOR')]
fds=[int(os.environ[k]) for k in names]
child=subprocess.Popen(sys.argv[2:],pass_fds=fds,stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,stderr=subprocess.PIPE)
for fd in fds: os.close(fd)
time.sleep(3)
def processes():
    hits=[]
    for pid in os.listdir('/proc'):
        if not pid.isdigit(): continue
        for part in ('environ','cmdline'):
            try: data=open('/proc/'+pid+'/'+part,'rb').read()
            except OSError: continue
            if needle in data: hits.append(pid+'/'+part)
    return hits
live=processes()
stdout,stderr=child.communicate(b'Reply with the fixture answer.',timeout=120)
files=[]
for top in ('/tmp','/workspace','/session'):
    for base,dirs,names_ in os.walk(top):
        for name in names_:
            path=os.path.join(base,name)
            try:
                if os.path.islink(path) or not os.path.isfile(path): continue
                if needle in open(path,'rb').read(): files.append(path)
            except OSError: pass
print(json.dumps(dict(returncode=child.returncode,live=live,after=processes(),files=files,
    fd_names=names,snapshot=needle in open('/snapshot/auth.json','rb').read(),
    answer=b'owner cell claude answer' in stdout,stderr=stderr[-2000:].decode('utf-8','replace'),
    stdout=stdout[-2000:].decode('utf-8','replace'))))
"""
MARK = r"""
import json,os,sys
os.makedirs('/workspace/notes',exist_ok=True)
with open('/workspace/notes/'+sys.argv[1],'w') as handle: handle.write(sys.argv[1])
seen=[]
for path in ('/workspace','/session'):
    try: seen.append(sorted(os.listdir(path)))
    except OSError as exc: seen.append(type(exc).__name__)
print(json.dumps(dict(cwd=os.getcwd(),seen=seen,uid=os.getuid())))
"""
from tinyassets import agent_sessions
from tinyassets.owner_launcher_client import OwnerLaunchRefused
from tinyassets.providers import claude_provider
from tinyassets.providers.base import ModelConfig, subprocess_env_for_provider
from tinyassets.providers.codex_provider import CodexProvider
from tinyassets.providers.owned_process import aspawn_owned, akill_owned_tree
from tinyassets.providers.provider_jail import provider_launch_scope, ProviderConfinementError
from tinyassets.role_provider_execution import CellView
PIPES=dict(stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
           stderr=asyncio.subprocess.PIPE)
APP=['/usr/local/bin/codex','app-server']
SETTINGS=json.dumps({'env':{'ANTHROPIC_BASE_URL':'https://uid-stream.invalid',
    'NODE_EXTRA_CA_CERTS':'/etc/ssl/certs/ca-certificates.crt',
    'CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC':'1','DISABLE_AUTOUPDATER':'1'}})
claude_provider._resolve_claude_cmd=lambda: (['/usr/local/bin/claude','--settings',SETTINGS],False)
async def rpc_turn(proc,thread_method,thread_params,text):
    ident=[0]; result={'messages':[]}
    async def send(message):
        proc.stdin.write(json.dumps(message).encode()+b'\n'); await proc.stdin.drain()
    async def call(method,params):
        ident[0]+=1; mine=ident[0]
        await send({'id':mine,'method':method,'params':params})
        while True:
            try: line=await proc.stdout.readline()
            except ConnectionResetError: line=b''
            if not line:
                err=await asyncio.wait_for(proc.stderr.read(65536),10)
                raise AssertionError(('app-server ended during',method,
                                      err[-3000:].decode('utf-8','replace')))
            message=json.loads(line)
            if message.get('id')==mine and 'method' not in message:
                assert 'error' not in message,(method,message)
                return message.get('result') or {}
            observe(message)
    def observe(message):
        if message.get('method')=='item/completed':
            item=message['params'].get('item') or {}
            if item.get('type')=='agentMessage': result['messages'].append(item.get('text'))
        if message.get('method')=='turn/completed':
            result['turn']=message['params']['turn']
    await call('initialize',{'clientInfo':{'name':'tinyassets','version':'1'}})
    await send({'method':'initialized','params':{}})
    started=await call(thread_method,thread_params)
    result['thread']=started['thread']['id']
    await call('turn/start',{'threadId':result['thread'],'input':[{'type':'text','text':text}]})
    async with asyncio.timeout(90):
        while 'turn' not in result:
            line=await proc.stdout.readline()
            assert line,'app-server ended before turn/completed'
            message=json.loads(line)
            if 'id' in message and 'method' in message:
                await send({'id':message['id'],'error':{'code':-32601,'message':'declined'}})
            observe(message)
    return result
async def adapters():
    assert os.environ.get('TA_ORACLE_HTTPS')=='1','run with --stream'
    for owner in identities:
        other='bob' if owner=='alice' else 'alice'
        center=root/('decoder-'+owner); foreign=root/('decoder-'+other)
        snapshot=center/'.runtime/provider-launch-credentials/fixture'
        claude_snapshot=center/'.runtime/provider-launch-credentials/claude'
        token=('sk-ant-oat01-c1-'+owner+'-fixture').encode()
        foreign_token=('sk-ant-oat01-c1-'+other+'-fixture').encode()
        outputs=[]
        with identity_context(Identity(owner,owner)):
            # 1. The real Codex adapter (workflow-node shape) completes a turn.
            codex=CodexProvider()
            codex.native_command_resolver=lambda: (['/usr/local/bin/codex'],False)
            from tinyassets.providers import codex_provider
            seen=[]
            async def recorded(cmd,**kwargs):
                seen.append((list(cmd),kwargs))
                return await aspawn_owned(cmd,**kwargs)
            codex_provider.aspawn_owned=recorded
            try:
                with provider_launch_scope(center,credential_dir=snapshot):
                    reply=await codex.complete('Return the fixture answer without tools.','',
                        ModelConfig(credential_snapshot_dir=snapshot,
                                    native_model_id='oracle-model',timeout=120),
                        universe_dir=center)
            except Exception:
                cmd,kwargs=seen[-1]
                kwargs.pop('limit',None)
                with provider_launch_scope(center,credential_dir=snapshot):
                    proc=await aspawn_owned(cmd,**kwargs)
                    try:
                        async with asyncio.timeout(45):
                            stdout,stderr=await proc.communicate(b'Return the answer.')
                    except TimeoutError:
                        stdout=b'(timeout)'
                        stderr=await asyncio.wait_for(proc.stderr.read(65536),5)
                print('DIAG',json.dumps(dict(cmd=cmd[1:],rc=proc.returncode,
                    out=stdout[-3000:].decode('utf-8','replace'),
                    err=stderr[-4000:].decode('utf-8','replace'))),flush=True)
                raise
            finally:
                codex_provider.aspawn_owned=aspawn_owned
            assert 'owner cell fixture answer' in reply.text,reply
            outputs.append(reply.text.encode())
            print(owner+': Codex adapter turn in cell PASS',flush=True)
            # 2. K2's served Claude flags (empty values) and the in-cell credential scan.
            env=subprocess_env_for_provider('claude-code',universe_dir=center,
                                            credential_snapshot_dir=claude_snapshot)
            assert env['CLAUDE_CODE_OAUTH_TOKEN']==token.decode()
            view=claude_provider._cell_view(env,str(center))
            assert view.secret_fds and view.persistent
            argv=['/usr/local/bin/python3.11','-I','-S','-c',SCAN,token[::-1].hex(),
                  '/usr/local/bin/claude','--settings',SETTINGS,'-p','--tools','',
                  '--setting-sources','','--permission-mode','default',
                  '--output-format','stream-json','--verbose']
            with provider_launch_scope(center,credential_dir=claude_snapshot):
                proc=await aspawn_owned(argv,env=env,cwd=center,cell_view=view,**PIPES)
                stdout,stderr=await proc.communicate()
            assert proc.returncode==0,(proc.returncode,stdout[-3000:],stderr[-3000:])
            scan=json.loads(stdout)
            assert scan['returncode']==0 and scan['answer'],scan
            assert scan['live']==[] and scan['after']==[] and scan['files']==[],scan
            assert scan['fd_names']==['CLAUDE_CODE_OAUTH_TOKEN_FILE_DESCRIPTOR'],scan
            assert scan['snapshot'] is True,scan  # only the sealed read-only mount holds it
            outputs.append(stdout+stderr)
            print(owner+': credential absent from every cell environ/cmdline and from '
                  '/tmp, /workspace and /session; present only in sealed /snapshot PASS',
                  flush=True)
            # 3. The real Claude adapter completes a turn; its raw token is piped.
            seen=[]
            async def recorded(cmd,**kwargs):
                seen.append((list(cmd),kwargs))
                return await aspawn_owned(cmd,**kwargs)
            claude_provider.aspawn_owned=recorded
            try:
                with provider_launch_scope(center,credential_dir=claude_snapshot):
                    reply=await claude_provider.ClaudeProvider().complete('Reply.','',
                        ModelConfig(credential_snapshot_dir=claude_snapshot,timeout=120,
                                    sandbox_workspace=True),universe_dir=center)
            except Exception:
                cmd,kwargs=seen[-1]
                kwargs.pop('limit',None)
                with provider_launch_scope(center,credential_dir=claude_snapshot):
                    proc=await aspawn_owned(cmd,**kwargs)
                    stdout,stderr=await proc.communicate(b'Reply.')
                print('DIAG',json.dumps(dict(cmd=cmd[3:],rc=proc.returncode,
                    out=stdout[-3000:].decode('utf-8','replace'),
                    err=stderr[-3000:].decode('utf-8','replace'))),flush=True)
                raise
            finally:
                claude_provider.aspawn_owned=aspawn_owned
            assert 'owner cell claude answer' in reply.text,reply
            outputs.append(reply.text.encode())
            print(owner+': Claude adapter turn in cell PASS',flush=True)
            # 4. K2-shaped app-server: session persists across two cells.
            app_view=CellView(persistent=True,home='CODEX_HOME')
            app_env={'CODEX_HOME':'/codex-home','HOME':'/tmp'}
            store=agent_sessions.native_store(center,'codex')
            assert store==center/'.provider-workspace/sessions'
            with provider_launch_scope(center,credential_dir=snapshot):
                proc=await aspawn_owned(APP,env=app_env,universe_view=object(),
                    cell_view=app_view,limit=1024*1024,**PIPES)
                try:
                    first=await rpc_turn(proc,'thread/start',{'model':'oracle-model',
                        'cwd':'/workspace','ephemeral':False,'approvalPolicy':'never',
                        'sandbox':'danger-full-access'},'First fixture turn.')
                    # The daemon only checks existence, never follows a link.
                    for _ in range(150):
                        if agent_sessions.native_file_exists(store,first['thread']+'.jsonl'):
                            break
                        await asyncio.sleep(0.1)
                finally:
                    await akill_owned_tree(proc); await proc.wait()
            assert first['turn']['status']=='completed',first
            assert 'owner cell fixture answer' in first['messages'],first
            thread=first['thread']
            assert agent_sessions.native_file_exists(store,thread+'.jsonl'),thread
            with provider_launch_scope(center,credential_dir=snapshot):
                proc=await aspawn_owned(APP,env=app_env,universe_view=object(),
                    cell_view=app_view,limit=1024*1024,**PIPES)
                try:
                    second=await rpc_turn(proc,'thread/resume',{'threadId':thread},
                                          'Second fixture turn.')
                    await asyncio.sleep(1)
                finally:
                    await akill_owned_tree(proc); await proc.wait()
            assert second['thread']==thread and second['turn']['status']=='completed',second
            rollout=[path for path in store.rglob('*'+thread+'.jsonl')]
            assert len(rollout)==1 and rollout[0].stat().st_uid==identities[owner],rollout
            text=rollout[0].read_bytes()
            assert b'First fixture turn.' in text and b'Second fixture turn.' in text
            assert token not in text and b'"OPENAI' not in text
            outputs.append(text)
            print(owner+': app-server thread/start then thread/resume across two cells; '
                  'rollout persisted in the owner store PASS',flush=True)
            # 5. Persistent workspace keeps bytes; scratch does not.
            for persistent in (True,False):
                with provider_launch_scope(center,credential_dir=snapshot):
                    proc=await aspawn_owned(['/usr/local/bin/python3.11','-I','-S','-c',MARK,
                        owner+'-'+str(persistent)],env={},cell_view=CellView(
                        persistent=persistent),**PIPES)
                    stdout,stderr=await proc.communicate()
                assert proc.returncode==0,(stdout,stderr)
                report=json.loads(stdout)
                assert report['cwd']=='/workspace' and report['uid'] not in (0,1001),report
                kept=center/'.provider-workspace/work/notes'/(owner+'-'+str(persistent))
                assert kept.exists()==persistent,(persistent,report)
                outputs.append(stdout+stderr)
            assert kept.parent.stat().st_uid==identities[owner]
            print(owner+': persistent /workspace kept, scratch discarded PASS',flush=True)
            # 6. Cross-owner: no foreign center, workspace or session is admitted.
            with provider_launch_scope(foreign,credential_dir=foreign/
                                       '.runtime/provider-launch-credentials/fixture'):
                try:
                    await aspawn_owned(APP,env=app_env,universe_view=object(),
                                       cell_view=app_view,**PIPES)
                except (PermissionError,ProviderConfinementError): pass
                else: raise AssertionError('foreign provider workspace admitted')
            with provider_launch_scope(center,credential_dir=snapshot):
                try:
                    await aspawn_owned(APP,env=app_env,cwd=foreign,cell_view=app_view,**PIPES)
                except ProviderConfinementError: pass
                else: raise AssertionError('foreign cwd admitted')
            from tinyassets import role_decoder
            from tinyassets.broker.owner_identities import owner_identity
            from tinyassets.workspace_fs import open_dir_nofollow
            identity=owner_identity(root,principal=owner)
            for chosen in (foreign/'.provider-workspace',foreign/'.provider-workspace/sessions',
                           center/'.runtime'):
                snapshot_fd=open_dir_nofollow(snapshot)
                try: chosen_fd=open_dir_nofollow(chosen)
                except OSError:
                    os.close(snapshot_fd); continue
                try:
                    role_decoder._bounded_client.start_cell(kind='provider-exec',
                        principal=owner,command_center=center.name,identity=identity,
                        extra={'egress':False,'workspace':True},directory_fd=snapshot_fd,
                        workspace_fd=chosen_fd).close()
                except OwnerLaunchRefused: pass
                else: raise AssertionError(('mapper admitted a foreign workspace',chosen))
                finally:
                    os.close(snapshot_fd); os.close(chosen_fd)
            print(owner+': foreign center, cwd and forged workspace descriptors refused PASS',
                  flush=True)
        joined=b''.join(outputs)
        assert foreign_token not in joined and (other+'-only').encode() not in joined
        assert other.encode()+b'-True' not in joined
        print(owner+': ZERO FOREIGN_BYTES',flush=True)
    print('D88 provider adapters through provider-exec PASS; startup OFF; ZERO FOREIGN_BYTES',
          flush=True)
asyncio.run(adapters())
'''


def packed(body):
    """Compressed inline body: the docker command line stays within Windows' limit."""
    import base64
    import zlib

    data = base64.b64encode(zlib.compress(body.encode(), 9)).decode()
    return ("exec(__import__('zlib').decompress(__import__('base64').b64decode("
            f"'{data}')).decode())\n")


if __name__ == '__main__':
    import role_service_bootstrap_probe as probe
    from role_provider_discovery_probe import SETUP as DISCOVERY_SETUP

    setup = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert probe.CONTAINER.count(setup) == probe.CONTAINER.count(marker) == 1
    probe.CONTAINER = probe.CONTAINER.replace(
        setup, packed(DISCOVERY_SETUP + SETUP) + setup).replace(marker, packed(PROBE))
    raise SystemExit(probe.main())
