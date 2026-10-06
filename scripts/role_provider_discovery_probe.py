"""Actual installed CLI model discovery, two sealed owner snapshots, no network/inference."""
from __future__ import annotations

import argparse
import subprocess

from role_service_bootstrap_probe import CONTAINER

SETUP = r'''
for owner,uid in identities.items():
    center=root/('decoder-'+owner)
    os.chown(center,1001,uid); center.chmod(0o710)
    subprocess.run(['setfacl','-m',f'u:{uid}:x',str(center)],check=True)
    parent=center
    for name in ('.runtime','provider-launch-credentials','fixture'):
        parent=parent/name; parent.mkdir()
        os.chown(parent,1001,uid); parent.chmod(0o700)
        subprocess.run(['setfacl','-m',f'u:{uid}:rx',str(parent)],check=True)
    for name,value in (('auth.json','{}'),('.lock',''),('own-sentinel',owner+'-only')):
        path=parent/name; path.write_text(value); os.chown(path,1001,uid); path.chmod(0o600)
        subprocess.run(['setfacl','-m',f'u:{uid}:r',str(path)],check=True)
'''

PROBE = r'''
import asyncio
from tinyassets.providers.codex_provider import CodexProvider
from tinyassets.providers.native_jsonrpc_discovery import read_native_catalogue
from tinyassets.role_provider_discovery import aspawn_cell
from tinyassets.providers.provider_jail import metadata_view
from tinyassets.exceptions import ProviderError
async def discovery():
    for owner in identities:
        center=root/('decoder-'+owner)
        snapshot=center/'.runtime/provider-launch-credentials/fixture'
        env={'CODEX_HOME':str(snapshot),'DAEMON_TOKEN':'foreign-token-do-not-forward'}
        with identity_context(Identity(owner,owner)):
            result=await read_native_catalogue(['/usr/local/bin/codex','app-server'],
                protocol=CodexProvider.native_discovery_protocol,env=env,cwd=str(snapshot),
                universe_dir=center,auth_env_names=('CODEX_HOME',),timeout=30)
            assert result.models and result.default_model_id,result
            print(owner+': installed Codex model/list via actual metadata API: '
                  +str(len(result.models))+' models',flush=True)
            other='bob' if owner=='alice' else 'alice'
            foreign=root/('decoder-'+other)/'.runtime/provider-launch-credentials/fixture'
            for chosen_center,chosen_snapshot in ((root/('decoder-'+other),foreign),
                                                   (center,foreign)):
                try:
                    await aspawn_cell(['/usr/local/bin/codex','app-server'],env=env,
                        view=metadata_view(center,snapshot,env,('CODEX_HOME',)),
                        universe_dir=chosen_center,snapshot_dir=chosen_snapshot,limit=65536)
                except PermissionError: pass
                else: raise AssertionError('foreign discovery scope or snapshot admitted')
            # Cancellation of a real initialized CLI must reap through the mapper,
            # never use a daemon PID signal, and preserve later admission.
            proc=await aspawn_cell(['/usr/local/bin/codex','app-server'],env=env,
                view=metadata_view(center,snapshot,env,('CODEX_HOME',)),
                universe_dir=center,snapshot_dir=snapshot,limit=65536)
            from tinyassets.providers.native_jsonrpc_discovery import _close_metadata_process
            await _close_metadata_process(proc)
            assert proc.returncode is not None
            again=await read_native_catalogue(['/usr/local/bin/codex','app-server'],
                protocol=CodexProvider.native_discovery_protocol,env=env,cwd=str(snapshot),
                universe_dir=center,auth_env_names=('CODEX_HOME',),timeout=30)
            assert again.models
    print('provider-discovery: ZERO FOREIGN_BYTES; actual cancellation/reuse PASS',flush=True)
asyncio.run(discovery())
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
    setup_marker = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
    assert CONTAINER.count(marker) == CONTAINER.count(setup_marker) == 1
    script = ('DEATH=""\nFAIL=False\nGIT=False\nSNAPSHOTS=False\n'
              + CONTAINER.replace(marker, PROBE + '\n' + marker)
                         .replace(setup_marker, SETUP + '\n' + setup_marker))
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
