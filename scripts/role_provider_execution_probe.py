"""Installed native execution, separate stderr and checked HTTPS in owner cells."""
import role_service_bootstrap_probe as probe
from role_provider_discovery_probe import SETUP

PROBE = r'''
import asyncio
from tinyassets.providers.owned_process import aspawn_owned, akill_owned_tree
from tinyassets.providers.provider_jail import provider_launch_scope, ProviderConfinementError
async def execution():
    assert os.environ.get('TA_ORACLE_HTTPS')=='1','run with --stream fixture'
    for owner in identities:
        center=root/('decoder-'+owner)
        snapshot=center/'.runtime/provider-launch-credentials/fixture'
        options=dict(env={'CODEX_HOME':str(snapshot),'HOST_SECRET':'must-not-enter'},
            stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE)
        with identity_context(Identity(owner,owner)):
            with provider_launch_scope(center,credential_dir=snapshot):
                proc=await aspawn_owned(['/usr/local/bin/codex','--version'],**options)
                stdout,stderr=await proc.communicate()
                assert proc.returncode==0 and b'codex-cli' in stdout,(proc.returncode,stdout,stderr)
                proc=await aspawn_owned(['/usr/local/bin/codex','--not-a-real-flag'],**options)
                stdout,stderr=await proc.communicate()
                assert proc.returncode!=0 and b'error' in stderr.lower() and not stdout
                command=['/usr/local/bin/codex','exec','--json','--skip-git-repo-check',
                    '--dangerously-bypass-approvals-and-sandbox','-C','/tmp/workspace',
                    '--model','oracle-model',
                    '-c','model_provider="oracle"',
                    '-c','model_providers.oracle.name="oracle"',
                    '-c','model_providers.oracle.base_url="https://uid-stream.invalid"',
                    '-c','model_providers.oracle.wire_api="responses"',
                    '-c','model_providers.oracle.requires_openai_auth=false',
                    '-c','model_providers.oracle.supports_websockets=false',
                    'Return the fixture answer without tools.']
                async with asyncio.timeout(90):
                    proc=await aspawn_owned(command,**options)
                    stdout,stderr=await proc.communicate()
                assert proc.returncode==0,(proc.returncode,stdout[-4000:],stderr[-4000:])
                assert b'owner cell fixture answer' in stdout,stdout[-4000:]
                assert b'must-not-enter' not in stdout+stderr
                assert (snapshot/'auth.json').read_text()=='{}'
                proc=await aspawn_owned(['/usr/local/bin/codex','app-server'],**options)
                await akill_owned_tree(proc)
                assert await proc.wait() is not None
            other='bob' if owner=='alice' else 'alice'
            with provider_launch_scope(root/('decoder-'+other),credential_dir=snapshot):
                try: await aspawn_owned(['/usr/local/bin/codex','--version'],**options)
                except (PermissionError,ProviderConfinementError): pass
                else: raise AssertionError('foreign provider owner admitted')
            with provider_launch_scope(center,credential_dir=snapshot,
                                       engine_route=(owner,center.name)):
                try: await aspawn_owned(['/usr/local/bin/codex','--version'],**options)
                except ProviderConfinementError: pass
                else: raise AssertionError('unsupported engine route fell back to daemon')
            with provider_launch_scope(center,credential_dir=snapshot):
                try:
                    await aspawn_owned(['/usr/local/bin/codex','--version'],cwd=center,**options)
                except ProviderConfinementError: pass
                else: raise AssertionError('persistent cwd was replaced with disposable state')
            print(owner+': native version, distinct stderr, actual HTTPS Responses completion, '
                  'cancellation and foreign/unsupported refusal PASS; ZERO FOREIGN_BYTES',
                  flush=True)
    print('provider-exec text-only acceptance PASS; startup OFF; ZERO FOREIGN_BYTES',flush=True)
asyncio.run(execution())
'''


if __name__ == '__main__':
    setup = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    assert probe.CONTAINER.count(setup) == probe.CONTAINER.count(marker) == 1
    probe.CONTAINER = probe.CONTAINER.replace(setup, SETUP + '\n' + setup).replace(marker, PROBE)
    raise SystemExit(probe.main())
