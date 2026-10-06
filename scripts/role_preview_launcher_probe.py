"""Actual Alice/Bob UI renders through the dedicated-owner launcher, D73.

Synthetic state only, using the existing staged service fixture. No activation.
"""
from __future__ import annotations

import argparse
import subprocess

from role_service_bootstrap_probe import CONTAINER

SETUP = r'''
previews=root/'decoder-alice/previews'; previews.mkdir()
os.chown(previews,300001,300001); previews.chmod(0o700)
subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',str(previews)],check=True)
foreign=root/'decoder-bob/workspace/foreign.txt'
os.link(foreign,previews/'hardlink.png')
os.symlink(str(foreign),previews/'symlink.png')
os.mkfifo(previews/'fifo.png'); os.chown(previews/'fifo.png',300001,300001)
# D65 protected canonical root: owner cannot replace root metadata. Migration
# supplies the dedicated writable preview subtree; the writer needs no chown.
bob_center=root/'decoder-bob'
directory(bob_center,1001,300002,0o2750)
subprocess.run(['setfacl','-m','g::rx',str(bob_center)],check=True)
bob_previews=bob_center/'previews'; bob_previews.mkdir()
directory(bob_previews,300002,300002,0o2700)
subprocess.run(['setfacl','-m','u:1001:rwx,d:u:1001:rwx',str(bob_previews)],check=True)
'''

PREVIEW = r'''
from tinyassets import ui_preview, role_preview, custom_agents as ca
import array, base64
# A caller cannot select a seccomp profile for any class. Unintegrated kinds
# refuse outright; this is negative admission evidence, not actual-class proof.
for kind in ('image-decoder','workspace-git','preview-write','provider-cli',
             'provider-discovery','provider-auth','engine-mcp','node-sandbox',
             'tool-jail','workspace-provision','workspace-registry','workspace-worker',
             'git-bridge','local-box','ingestion-video'):
    unused,partner=socket.socketpair()
    with unused,partner,client._lock:
        doc=dict(op='SPAWN',kind=kind,principal='alice',command_center='decoder-alice',
                 profile='cell-nested')
        client._channel.sendmsg([json.dumps(doc).encode()],[(socket.SOL_SOCKET,
            socket.SCM_RIGHTS,array.array('i',[partner.fileno()]))])
        assert client._reply()==dict(op='REFUSED'),kind
print('profile override refused for every other D9 class',flush=True)
for owner in identities:
    center='decoder-'+owner
    with identity_context(Identity(owner,owner)):
        asset=ca.store_app_ui_asset(root,owner_user_id=owner,
            data=out.getvalue(),media_type='image/png')
        component=dict(kind='tinyassets.app-ui.v1',version=1,ui_id='owner-preview',
            name='Owner preview',markup='<h1>'+owner+'</h1><img src="ta-asset:own.png">',
            style='body{background:#123456}',script='',assets={'own.png':asset})
        ca.change_app_ui_entry(root,owner_user_id=owner,universe_id=center,
            operation='add_ui',payload={'component':component})
        spec=ui_preview._spec_for(root,owner,center,'owner-preview',320,240)
        done=role_preview.render(spec,60)
        assert done.returncode==0 and done.cell['uid']==identities[owner]-300000
        assert done.cell['profile']=='cell-nested' and done.cell['nested_userns']
        report=json.loads(done.stdout)
        assert report['chromium_sandbox'] is True,report
        assert not report['missing_assets'] and not report['delivery_error'],report
        assert not report['uncaught_errors'],report
        assert Image.open(io.BytesIO(base64.b64decode(report['png_base64']))).size==(320,240)
        png=base64.b64decode(report['png_base64'])
        from tinyassets.universe_files import read_universe_file
        directory=root/center
        for name in ('owner-preview','symlink','hardlink','fifo'):
            assert ui_preview.write_preview(directory,name,png)==f'/u/previews/{name}.png'
            assert read_universe_file(directory,f'previews/{name}.png')==png
            info=(directory/'previews'/f'{name}.png').stat()
            assert (info.st_uid,info.st_gid,info.st_nlink)==(identities[owner],identities[owner],1)
        assert (root/'decoder-bob/workspace/foreign.txt').read_text()=='bob-foreign-sentinel'
        foreign='bob' if owner=='alice' else 'alice'
        for changed in (dict(spec,owner_user_id=foreign),
                        dict(spec,universe_id='decoder-'+foreign),
                        dict(spec,base_path='/tmp')):
            try: role_preview.render(changed,60)
            except PermissionError: pass
            else: raise AssertionError('foreign preview scope admitted')
        print(json.dumps(dict(owner=owner,preview='PASS',cell=done.cell,
            chromium_sandbox=True,assets=True,foreign_scope_denied=True)),flush=True)
with identity_context(Identity('alice','alice')):
    report=ui_preview.preview_app_ui(root,owner_user_id='alice',
        universe_id='decoder-alice',ui_id='owner-preview',width=320,height=240)
    assert report['png'].startswith(b'\x89PNG') and report['chromium_sandbox']
    center=root/'decoder-alice'
    screenshot=ui_preview.write_preview(center,'owner-preview',report['png'])
    assert screenshot=='/u/previews/owner-preview.png'
    from tinyassets.universe_files import read_universe_file
    assert read_universe_file(center,'previews/owner-preview.png')==report['png']
    info=(center/'previews/owner-preview.png').stat()
    assert (info.st_uid,info.st_gid)==(identities['alice'],identities['alice'])
print('D73 actual preview entry: Alice/Bob sandboxed Chromium; zero foreign reads; '
      'data-only cell; application entry PASS',flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    marker = "for path in (root/'.broker/outbound.db'"
    assert CONTAINER.count(marker) == 1
    script = ('DEATH=""\nFAIL=False\nGIT=True\nSNAPSHOTS=False\n'
              + CONTAINER.replace(marker, PREVIEW + '\n' + marker))
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
