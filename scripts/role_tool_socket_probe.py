"""Real public bash/ta and proxy traffic over exact owner-cell socket mounts."""
from __future__ import annotations

import argparse
import subprocess
import time
import uuid

from role_service_bootstrap_probe import CONTAINER
from role_tool_launcher_probe import SETUP, TOOL

SERVER = r'''
from http.server import BaseHTTPRequestHandler, HTTPServer
class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        body=b'tool-egress-positive'
        self.send_response(200)
        self.send_header('Content-Length',str(len(body)))
        self.end_headers()
        self.wfile.write(body)
server=HTTPServer(('0.0.0.0',8080),Handler)
print('ready',flush=True)
server.serve_forever()
'''

SOCKETS = r'''
from tinyassets import role_relays, universe_egress, workspace_fs
from tinyassets.auth.middleware import current_identity
from tinyassets.owner_launcher_client import OwnerLaunchRefused
from tinyassets.ta_capabilities import JailBridge
import shlex
for owner in identities:
    center=root/('decoder-'+owner)
    with identity_context(Identity(owner,owner)):
        calls=[]
        def dispatch(message):
            assert current_identity().user_id==owner
            calls.append(message)
            if message=={'op':'catalog'}:
                return {'extension_roots':{},'capabilities':[
                    {'name':'owner','description':'synthetic owner control','arguments':{}}]}
            assert message=={'op':'call','name':'owner','arguments':{}}
            return {'result':{'owner':owner}}
        answer=tools.bash(center,"ta owner --json '{}'",agent_id='main',ta_dispatch=dispatch)
        assert '"owner": "'+owner+'"' in answer and '[exit code 0]' in answer,answer
        assert len(calls)==2,calls
        assert not list((root/'.universe-sidecars'/center.name).glob('ta-*.sock'))
        with JailBridge(dispatch,universe_dir=center) as only_ta:
            result=tools.run_jailed(center,
                ['/usr/local/bin/python','/ta/bin/ta','owner','--json','{}'],
                agent_id='main',ta_socket=only_ta.path)
            assert result.exit_code==0 and json.loads(result.output)=={'owner':owner},result
        script = """
import socket,urllib.request,urllib.error
assert urllib.request.urlopen('http://93.184.217.2:8080/',timeout=10).read()==b'tool-egress-positive'
try: urllib.request.urlopen('http://169.254.169.254:8080/',timeout=3)
except urllib.error.HTTPError as error: assert error.code==403
else: raise AssertionError('proxy reached metadata address')
with socket.socket() as raw:
    raw.settimeout(.2)
    assert raw.connect_ex(('93.184.217.2',8080))!=0
print('EGRESS_POSITIVE private-address=DENIED direct-network=DENIED')
"""
        answer=tools.bash(center,'/usr/local/bin/python -I -c '+shlex.quote(script),agent_id='main')
        assert 'EGRESS_POSITIVE' in answer and '[exit code 0]' in answer,answer
        other='bob' if owner=='alice' else 'alice'
        other_center=root/('decoder-'+other)
        other_socket=universe_egress.ensure_proxy(other_center)
        try:
            tools.run_jailed(center,['/bin/true'],agent_id='main',egress_socket=other_socket)
        except tools.UniverseToolError: pass
        else: raise AssertionError('foreign socket accepted by application')
        foreign=role_relays.pin_for_owner(other_socket,other_center,identities[other],kind='egress')
        own_fd=workspace_fs.open_dir_nofollow(center)
        own_identity=owner_identity(root,principal=owner)
        try:
            try:
                with client.start_cell(kind='tool-jail',principal=owner,
                    command_center=center.name,identity=own_identity,directory_fd=own_fd,
                    extra={'egress':True,'ta':False},socket_fds=(foreign,)):
                    raise AssertionError('mapper admitted foreign socket')
            except OwnerLaunchRefused: pass
            bridge=JailBridge(dispatch,universe_dir=center).__enter__()
            stale=role_relays.pin_for_owner(bridge.path,center,identities[owner],kind='ta')
            bridge.__exit__()
            try:
                try:
                    with client.start_cell(kind='tool-jail',principal=owner,
                        command_center=center.name,identity=own_identity,directory_fd=own_fd,
                        extra={'egress':False,'ta':True},socket_fds=(stale,)):
                        raise AssertionError('mapper admitted revoked socket')
                except OwnerLaunchRefused: pass
            finally: os.close(stale)
        finally:
            os.close(foreign); os.close(own_fd)
        assert owner+'-own-bytes' in tools.read_file(center,'own.txt',agent_id='main')
        print(owner+': actual bash/ta callback and HTTP egress PASS; foreign/revoked sockets '
              'DENIED at application and mapper; ZERO FOREIGN_BYTES',flush=True)
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    digest = subprocess.run(['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
                            capture_output=True, text=True, check=True).stdout.strip()
    token = uuid.uuid4().hex[:12]
    network, fixture = 'uid-tools-' + token, 'uid-tools-http-' + token
    created = False
    subprocess.run(['docker', 'network', 'create', '--internal', '--subnet',
                    '93.184.217.0/24', network], check=True, capture_output=True)
    try:
        subprocess.run(['docker', 'run', '-d', '--name', fixture, '--network', network,
            '--ip', '93.184.217.2', '--cap-drop', 'ALL', '--security-opt',
            'no-new-privileges=true', '--entrypoint', '/opt/venv/bin/python', digest,
            '-I', '-B', '-c', SERVER], check=True, capture_output=True)
        created = True
        deadline = time.monotonic() + 20
        while 'ready' not in subprocess.run(['docker', 'logs', fixture], capture_output=True,
                                           text=True, check=True).stdout:
            if time.monotonic() > deadline:
                raise RuntimeError('synthetic HTTP fixture did not start')
            time.sleep(.2)
        marker = "if os.environ.get('TA_ORACLE_HTTPS')=='1':\n    stream['probe'](root)"
        script = ('DEATH=""\nFAIL=False\nGIT=True\nSNAPSHOTS=False\n'
                  + CONTAINER.replace(marker, TOOL + '\n' + SOCKETS + '\n' + marker))
        setup = "run=Path(tempfile.mkdtemp(prefix='role-services-',dir='/run'))"
        assert script.count(setup) == 1
        script = script.replace(setup, SETUP + '\n' + setup)
        command = ['docker', 'run', '--rm', '-i', '--network', network,
                   '--ip', '93.184.217.3', '--user', '0:0', '--cap-drop', 'ALL']
        for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
            command += ['--cap-add', cap]
        for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                       'systempaths=unconfined'):
            command += ['--security-opt', option]
        command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
        print('production image:', digest, flush=True)
        return subprocess.run(command, input=script, text=True, timeout=360).returncode
    finally:
        if created:
            subprocess.run(['docker', 'rm', '-f', fixture], check=True, capture_output=True)
        subprocess.run(['docker', 'network', 'rm', network], check=True, capture_output=True)


if __name__ == '__main__':
    raise SystemExit(main())
