"""Switched role-split startup on the production image, synthetic volume only.

OFF: the switch absent, the split entry refuses (78) and the volume is
untouched. ON: the compose.role-split.yml options start PID1 through
ta-entry.sh, migrate forward, bootstrap broker and mapper, retire PID1 to the
capability-free daemon and serve; the switched healthcheck passes. Then the
reverse mode exits before any service, and the image's default (OFF)
ENTRYPOINT and CMD boot on the reversed volume and pass ``ta-op pulse``.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import time
import uuid

from role_old_image_rollback_probe import pin
from role_volume_rollback_probe import PREPARE

# The rollback fixture, cut before its migration: a legacy single-UID volume.
LEGACY = PREPARE[:PREPARE.index("load=lambda name:")] + "print('legacy volume ready')\n"

STATE = r'''
import json,os
from pathlib import Path
root=Path('/data')
def ids(path):
    info=path.lstat(); return [info.st_uid,info.st_gid]
layout=json.loads((root/'.layout.json').read_text())
print(json.dumps(dict(roles=layout.get('roles',{}).get('owners'),
    alice=ids(root/'alice/work'),bob=ids(root/'bob/work'),
    retained=(root/'bob/work/retained').read_bytes().decode())))
'''

PROCESSES = r'''
import json,os
from pathlib import Path
def status(pid):
    return dict(l.split(':',1) for l in Path(f'/proc/{pid}/status').read_text().splitlines())
one=status(1)
roles={}
for name in os.listdir('/proc'):
    if name.isdigit():
        try: roles.setdefault(status(name)['Uid'].split()[0],0)
        except (FileNotFoundError,ProcessLookupError): continue
print(json.dumps(dict(pid1_uid=one['Uid'].split(),pid1_gid=one['Gid'].split(),
    pid1_groups=one['Groups'].split(),pid1_nnp=one['NoNewPrivs'].strip(),
    pid1_caps={k:one[k].strip() for k in ('CapInh','CapPrm','CapEff','CapBnd','CapAmb')},
    uids=sorted(roles))))
'''

CAPS = ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'KILL', 'SETGID', 'SETUID', 'SETPCAP')
ENVIRONMENT = {
    'HOME': '/app', 'TINYASSETS_REPO_ROOT': '/data/community-pool',
    'TINYASSETS_CLOUD_DAEMON_SUBSCRIPTION_ONLY': '1', 'TINYASSETS_GOAL_POOL': 'off',
    'TINYASSETS_MCP_CANARY_URL': 'http://127.0.0.1:8001/mcp',
    'TINYASSETS_ONBOARDING_APP': '1', 'TINYASSETS_ALLOW_CLAUDE_SERVING': '1',
    # Synthetic dev operator: storage/boot evidence, not production auth.
    'UNIVERSE_SERVER_AUTH': 'false', 'UNIVERSE_SERVER_DEV_USER': 'u2-synthetic-operator',
    'TINYASSETS_DATA_DIR': '/data',
    'TINYASSETS_WIKI_CANARY_TOKEN': 'synthetic-u2-startup-only',
}


def docker(*args, **kwargs):
    kwargs.setdefault('check', True)
    return subprocess.run(['docker', *args], **kwargs)


def inspect(command, image, volume):
    result = docker('run', '--rm', '-i', '--network', 'none', '--user', '0:0',
                    '--cap-drop', 'ALL', '--cap-add', 'DAC_OVERRIDE', '--security-opt',
                    'no-new-privileges=true', '--mount', f'type=volume,src={volume},dst=/data',
                    '--entrypoint', '/opt/venv/bin/python', image, '-I', '-B', '-',
                    input=command, text=True, capture_output=True, timeout=120)
    return json.loads(result.stdout.strip().splitlines()[-1])


def ready(name, label):
    deadline = time.monotonic() + 180
    while True:
        probe = docker('exec', '--user', '1001:1001', name, '/opt/venv/bin/python', '-c',
                       'import socket; socket.create_connection(("127.0.0.1",8001),2).close()',
                       check=False, capture_output=True)
        if probe.returncode == 0:
            return
        running = docker('inspect', name, '--format', '{{.State.Running}}',
                         capture_output=True, text=True).stdout.strip()
        if running != 'true' or time.monotonic() >= deadline:
            docker('logs', '--tail', '120', name, check=False)
            raise RuntimeError(f'{label} did not become ready')
        time.sleep(1)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    args = parser.parse_args()
    image = pin(args.image)
    name = 'ta-u2-startup-' + uuid.uuid4().hex
    docker('volume', 'create', name, capture_output=True)
    network = False
    try:
        base = ['--cap-drop', 'ALL', '--mount', f'type=volume,src={name},dst=/data']
        for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                       'systempaths=unconfined'):
            base += ['--security-opt', option]
        for key, value in {**ENVIRONMENT, 'TINYASSETS_IMAGE': image}.items():
            base += ['-e', f'{key}={value}']
        split = [*base, '--user', '0:0', '--entrypoint', '/usr/local/libexec/ta-entry.sh']
        for cap in CAPS:
            split += ['--cap-add', cap]
        start = ['/opt/venv/bin/python', '-I', '-B', '/usr/local/libexec/ta-role-start.py']

        docker('run', '--rm', '-i', '--network', 'none', '--user', '0:0', '--cap-drop', 'ALL',
               *[x for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER') for x in ('--cap-add', cap)],
               '--security-opt', 'no-new-privileges=true',
               '--mount', f'type=volume,src={name},dst=/data', '--entrypoint',
               '/opt/venv/bin/python', image, '-I', '-B', '-', input=LEGACY, text=True,
               timeout=120)
        legacy = inspect(STATE, image, name)
        assert legacy['roles'] is None and legacy['alice'] == [1001, 1001], legacy

        # OFF: the split entry with every option but the switch refuses untouched.
        for mode in ('start', 'reverse', 'health'):
            off = docker('run', '--rm', '--network', 'none', *split, image, *start, mode,
                         check=False, capture_output=True, text=True, timeout=120)
            assert off.returncode == 78 and 'is OFF' in off.stderr, (mode, off)
        assert inspect(STATE, image, name) == legacy

        docker('network', 'create', '--internal', '--subnet', '169.254.0.0/16', name,
               capture_output=True)
        network = True
        server = ('from http.server import BaseHTTPRequestHandler,HTTPServer\n'
                  'class Handler(BaseHTTPRequestHandler):\n'
                  ' def do_GET(self):\n'
                  '  self.send_response(200); self.end_headers(); self.wfile.write(b"123456789")\n'
                  'HTTPServer(("0.0.0.0",80),Handler).serve_forever()')
        docker('run', '-d', '--name', name + '-metadata', '--network', name, '--ip',
               '169.254.169.254', '--user', '1001:1001', '--cap-drop', 'ALL', '--security-opt',
               'no-new-privileges=true', '--entrypoint', '/opt/venv/bin/python', image, '-c',
               server, capture_output=True)

        # ON: exactly the overlay's user, capabilities, entrypoint, command, switch.
        docker('run', '-d', '--name', name, '--network', name, *split,
               '-e', 'TINYASSETS_ROLE_SPLIT=1', image, *start, 'start', capture_output=True)
        ready(name, 'switched role startup')
        processes = json.loads(docker('exec', '--user', '1001:1001', name, '/opt/venv/bin/python',
                                      '-I', '-c', PROCESSES, capture_output=True,
                                      text=True).stdout)
        assert processes['pid1_uid'] == ['1001'] * 4, processes
        assert processes['pid1_gid'] == ['1001'] * 4, processes
        assert processes['pid1_groups'] == ['1100', '1101', '1102'], processes
        assert set(processes['pid1_caps'].values()) == {'0000000000000000'}, processes
        assert processes['pid1_nnp'] == '1' and '1002' in processes['uids'], processes
        assert '0' not in processes['uids'], processes  # no host-root process survives
        health = docker('exec', name, *start, 'health', check=False)
        if health.returncode:
            docker('logs', '--tail', '120', name, check=False)
            raise RuntimeError('switched healthcheck failed')
        docker('stop', '-t', '20', name, capture_output=True)
        docker('rm', '-f', name, capture_output=True)
        forward = inspect(STATE, image, name)
        assert forward['roles'] == {'state': 'stable', 'direction': 'forward'}, forward
        assert 300001 <= forward['alice'][0] < 400000 and forward['alice'] != forward['bob']
        assert forward['retained'] == 'bob'

        reverse = docker('run', '--rm', '--network', 'none', *split,
                         '-e', 'TINYASSETS_ROLE_SPLIT=1', image, *start, 'reverse',
                         capture_output=True, text=True, timeout=180)
        assert reverse.returncode == 0, reverse.stderr[-2000:]
        assert 'reverse migration complete' in reverse.stdout, reverse.stdout
        reversed_state = inspect(STATE, image, name)
        assert reversed_state['roles'] == {'state': 'stable', 'direction': 'reverse'}
        assert reversed_state['alice'] == reversed_state['bob'] == [1001, 1001]

        # Default OFF image startup, unchanged ENTRYPOINT and CMD, on the reversed volume.
        docker('run', '-d', '--name', name, '--network', name, *base, '--user', '1001:1001',
               image, capture_output=True)
        ready(name, 'default OFF startup')
        pulse = docker('exec', name, '/usr/local/libexec/ta-op', 'pulse', check=False,
                       timeout=60)
        if pulse.returncode:
            docker('logs', '--tail', '120', name, check=False)
            raise RuntimeError('default healthcheck failed after reverse')
        print(json.dumps(dict(image=image, off_refused=True, on_boot=True, switched_health=True,
                              pid1_uid=1001, pid1_caps='zero', broker_uid=1002,
                              forward_migrated=True, reverse_mode_exit=0,
                              default_off_boot_after_reverse=True, synthetic_volume=True,
                              auth_fixture='dev-operator')))
    finally:
        docker('rm', '-f', name, check=False, capture_output=True)
        docker('rm', '-f', name + '-metadata', check=False, capture_output=True)
        if network:
            docker('network', 'rm', name, capture_output=True)
        docker('volume', 'rm', name, capture_output=True)


if __name__ == '__main__':
    main()
