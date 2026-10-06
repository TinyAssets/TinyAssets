"""Actual node executor, nested workspace jail and RPC under Alice/Bob identities."""
from __future__ import annotations

import argparse
import subprocess

from role_service_bootstrap_probe import CONTAINER

SETUP = r'''
native = """
import json, os, socket
from pathlib import Path
expected=int(os.environ['EXPECTED_UID'])
assert os.getuid()==os.getgid()==expected and not os.getgroups()
status=dict(line.split(':',1) for line in Path('/proc/self/status').read_text().splitlines())
assert all(int(status[k],16)==0 for k in ('CapEff','CapPrm','CapInh','CapAmb','CapBnd'))
assert int(status['NoNewPrivs'])==1
fds=[]
for value in os.listdir('/proc/self/fd'):
    fd=int(value)
    try: os.fstat(fd)
    except OSError: continue
    fds.append(fd)
    assert fd<=2
    try: escaped=os.open('..',os.O_RDONLY|os.O_DIRECTORY,dir_fd=fd)
    except OSError: pass
    else: os.close(escaped); raise AssertionError('host parent descriptor')
denied=[]
for name in ('foreign-hardlink','foreign-symlink','/data/bob/.credential-vault.json',
             '/data/.broker/owner.json','/data/.broker/outbound.db','/run/tinyassets',
             '/proc/1/root/data/bob/private'):
    try: content=Path(name).read_bytes()
    except (PermissionError,FileNotFoundError,NotADirectoryError): denied.append(name)
    else: raise AssertionError('unexpected read '+name)
for name in ('foreign-hardlink','foreign-symlink'):
    try: os.chown(name,os.getuid(),os.getgid())
    except OSError: pass
    else: raise AssertionError('foreign relabel')
with socket.socket() as network:
    network.settimeout(.2)
    assert network.connect_ex(('127.0.0.1',39281))!=0
assert 'own' in Path('own.txt').read_text() or 'bridge-change' in Path('own.txt').read_text()
print(json.dumps(dict(uid=expected,fds=sorted(fds),denied=denied,foreign_bytes=0)))
"""
for owner,uid in identities.items():
    work=root/('decoder-'+owner)/'workspace'
    script=work/'native_probe.py'; script.write_text(native)
    os.chown(script,uid,uid); script.chmod(0o600)
    subprocess.run(['/opt/venv/bin/python','-m','venv','--without-pip',str(work/'.venv')],
                   check=True)
    for directory_path,dirs,files in os.walk(work/'.venv'):
        os.chown(directory_path,uid,uid)
        for name in dirs+files:
            os.chown(Path(directory_path)/name,uid,uid,follow_symlinks=False)
'''

NODE = r'''
from tinyassets.node_sandbox import NodeSandbox, WorkspaceMount
from tinyassets import workspace_fs
import threading, time
for owner in identities:
    center=root/('decoder-'+owner)
    with identity_context(Identity(owner,owner)):
        runner=NodeSandbox(universe_dir=center)
        result=runner.run_sync('arithmetic','def run(s): return {"answer": s["x"]+1}',
                              {'x':41,'foreign':'not-declared'},['x'],['answer'])
        assert result.success and result.output_state=={'answer':42},result
        calls=[]
        def invoke(action,kwargs):
            calls.append((action,kwargs))
            return {'owner':owner,'value':kwargs['value']}
        result=runner.run_sync('rpc',
            'def run(s): return {"answer": invoke_mcp_action("echo", value=7)}',
            {},[],['answer'],invoke=invoke)
        assert result.success and result.output_state=={'answer':{'owner':owner,'value':7}},result
        assert calls==[('echo',{'value':7})]
        work=center/'workspace'
        fd=workspace_fs.open_dir_nofollow(work)
        try:
            mount=WorkspaceMount(f'/proc/self/fd/{fd}',pass_fds=(fd,))
            source=('def run(s):\n    r=ws.run(["/usr/bin/git","status","--porcelain"])'
                    '\n    return {"r":r,"own":ws.read("own.txt")}')
            result=runner.run_sync('workspace',source,{},[],['r'],workspace=mount)
            assert result.success and result.output_state['r']['returncode']==0,result
            assert result.output_state['own']==owner+'-bridge-change',result
            source=('def run(s):\n    return {"r":ws.run([".venv/bin/python",'
                    '"native_probe.py"], env={"EXPECTED_UID":'+repr(str(identities[owner]-300000))
                    +'})}')
            result=runner.run_sync('native-matrix',source,{},[],['r'],workspace=mount)
            assert result.success and result.output_state['r']['returncode']==0,result
            print(owner+' native descendant matrix: '+str(result.output_state),flush=True)
            aliases=('foreign-hardlink','foreign-symlink','preplanted-fifo')
            for alias in aliases if owner=='alice' else ():
                source='def run(s):\n    return {"r":ws.read('+repr(alias)+')}'
                result=runner.run_sync('alias',source,{},[],['r'],timeout=3,workspace=mount)
                assert 'bob-foreign-sentinel' not in str(result),result
                assert not result.success,result
            other='bob' if owner=='alice' else 'alice'
            other_fd=workspace_fs.open_dir_nofollow(root/('decoder-'+other)/'workspace')
            try:
                try:
                    runner.run_sync('foreign','def run(s): return s',{},[],[],workspace=
                        WorkspaceMount(f'/proc/self/fd/{other_fd}',pass_fds=(other_fd,)))
                except (PermissionError,ValueError): pass
                else: raise AssertionError('foreign workspace accepted')
            finally: os.close(other_fd)
        finally: os.close(fd)
        from tinyassets import graph_compiler
        from tinyassets.branches import NodeDefinition
        from tinyassets.effectors import EffectChain, WorkspaceMount as ChainMount
        chain=EffectChain()
        fd=workspace_fs.open_dir_nofollow(work)
        chain.register_workspace('checkout',ChainMount('checkout',f'/proc/self/fd/{fd}',
            repo_fd=fd,lease_fd=os.dup(fd),pass_fds=(fd,)))
        from unittest.mock import patch
        try:
            node=NodeDefinition(node_id='build',display_name='Build',phase='draft',
                source_code='def run(s): return {"own":ws.read("own.txt")}',
                output_keys=['own'],workspace='checkout')
            fn=graph_compiler._build_source_code_node(node,event_sink=None,
                base_path=center,effect_chain=chain,ancestors={'checkout'})
            with patch('tinyassets.node_sandbox.WORKSPACE_LAUNCHER_FACTORY',
                       side_effect=AssertionError('daemon jail factory called')):
                assert fn({})['own']==owner+'-bridge-change'
        finally: chain.close_workspaces()
        from tinyassets.authoring.models import AuthoringSession
        from tinyassets.authoring.sandbox import BudgetLedger, DEFAULT_POLICY
        from tinyassets.authoring.service import _execute_draft_nodes
        from tinyassets.daemon_server import set_founder_home
        set_founder_home(root,founder_sub=owner,universe_id=center.name)
        draft=AuthoringSession(session_id='probe',owner_id=owner,artifact_id='probe',
            artifact_kind='node',seed_mode='sketch',seed_ref='',status='active',
            draft_version=1,created_at='',updated_at='',retention_until='',
            definition={'node_defs':[{'node_id':'draft','source_code':
                'def run(s): return {"ok":True}','output_keys':['ok']}]})
        records,budget_error=_execute_draft_nodes(draft,ledger=BudgetLedger(DEFAULT_POLICY),
                                                policy=DEFAULT_POLICY,bound=None)
        assert budget_error is None and records[0]['status']=='passed',records
        cancel=threading.Event()
        waiting=threading.Event()
        release=threading.Event()
        def blocked(action,kwargs):
            waiting.set()
            release.wait(10)
            return None
        def trigger():
            assert waiting.wait(10)
            cancel.set()
        thread=threading.Thread(target=trigger); thread.start()
        try:
            result=NodeSandbox(universe_dir=center,should_cancel=cancel.is_set).run_sync(
                'cancel','def run(s): return {"r":invoke_mcp_action("wait")}',
                {},[],['r'],invoke=blocked)
            assert result.cancelled,result
        finally:
            release.set(); thread.join(5)
        result=runner.run_sync('after-cancel','def run(s): return {"ok":True}',{},[],['ok'])
        assert result.success,result
        print(owner+': actual node compiler/data/workspace/RPC/cancel; '
              'foreign mounts and aliases DENIED',flush=True)
print('node-sandbox actual application entry PASS; ZERO FOREIGN_BYTES',flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert CONTAINER.count(marker) == 1
    script = ('DEATH=""\nFAIL=False\nGIT=True\nSNAPSHOTS=False\n'
              + CONTAINER.replace(marker, NODE + '\n' + marker))
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
    return subprocess.run(command, input=script, text=True, timeout=300).returncode


if __name__ == '__main__':
    raise SystemExit(main())
