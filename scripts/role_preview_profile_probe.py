"""Diagnostic: can the shipped preview render under D9's assigned profile?

Runs synthetic markup in an immutable production image with no host mounts or
network. This is NOT launcher/owner-cell acceptance and cannot enable startup.
The alternate profile is a diagnostic control, never a runtime policy change.
"""
from __future__ import annotations

import argparse
import json
import subprocess

CELL = r'''
import json, os, sys
for name in os.listdir('/proc/self/fd'):
    fd = int(name)
    if fd > 2:
        try:
            os.close(fd)
        except OSError as exc:
            if exc.errno != 9:
                raise
sys.path.insert(0, '/app')
from tinyassets.ui_preview import _child
spec = dict(base_path='/absent', owner_user_id='synthetic', universe_id='synthetic',
            ui_id='profile-probe', width=320, height=240, libraries=[], files=[],
            hashes={}, workflow_refs={}, agent_refs={},
            bundle=dict(markup='<h1>Owner preview</h1>', style='', script=''))
result = _child(spec)
png = result.pop('png_base64', '')
result['png_base64_bytes'] = len(png)
result['uid'] = os.getuid()
result['namespaces'] = {key: os.readlink('/proc/self/ns/' + key)
                        for key in ('mnt', 'pid', 'ipc', 'net')}
result['status'] = {key: value.strip() for key, value in
                   (line.split(':', 1) for line in open('/proc/self/status'))
                   if key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb', 'NoNewPrivs')}
print(json.dumps(result), flush=True)
'''

CONTAINER = r'''
import json, os, runpy, subprocess
launcher = runpy.run_path('/usr/local/libexec/ta-launch.py')
launcher['verify_chain']()
launcher['retire_migration_authority']()
launcher['_retire_identity'](1003, ())
host = {key: os.readlink('/proc/self/ns/' + key) for key in ('mnt', 'pid', 'ipc', 'net')}
factory = runpy.run_path('/app/tinyassets/providers/jail_seccomp.py')['program_fd']
records = []
for profile in ('cell-deny', 'cell-links', 'cell-nested'):
    fd = factory(profile=profile)
    argv = ['/usr/bin/bwrap', '--die-with-parent', '--new-session', '--unshare-all',
            '--cap-drop', 'ALL', '--clearenv']
    for name, value in dict(PATH='/opt/venv/bin:/usr/local/bin:/usr/bin:/bin',
                            HOME='/tmp', LANG='C.UTF-8', DEBUG='pw:browser',
                            PLAYWRIGHT_BROWSERS_PATH='/opt/ms-playwright').items():
        argv += ['--setenv', name, value]
    for path in ('/usr', '/opt', '/app', '/etc', '/bin', '/lib', '/lib64'):
        if os.path.exists(path):
            argv += ['--ro-bind', path, path]
    argv += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp', '--chdir', '/tmp',
             '--seccomp', str(fd), '--', '/opt/venv/bin/python', '-I', '-B', '-c', CELL]
    try:
        result = subprocess.run(argv, pass_fds=(fd,), capture_output=True, text=True, timeout=60)
    finally:
        os.close(fd)
    record = dict(profile=profile, returncode=result.returncode, host_namespaces=host,
                  stderr=result.stderr[-6000:])
    try:
        record['render'] = json.loads(result.stdout)
    except ValueError:
        record['stdout'] = result.stdout[-1000:]
    records.append(record)
    print(json.dumps(record), flush=True)
deny, links, nested = records
assert all(row['returncode'] == 0 for row in records), 'diagnostic bootstrap failed'
assert all(row['render'].get('unavailable') for row in (deny, links)), 'D9 conflict not reproduced'
assert all('No usable sandbox!' in row['stderr'] for row in (deny, links))
assert nested['render']['png_base64_bytes'] > 0, 'nested control did not render'
assert not nested['render'].get('delivery_error'), 'nested control did not deliver the UI'
assert not nested['render'].get('uncaught_errors'), 'nested control has UI errors'
for row in records:
    render = row['render']
    assert render['uid'] == 1003
    assert render['status']['NoNewPrivs'] == '1'
    assert all(int(value, 16) == 0 for key, value in render['status'].items()
               if key.startswith('Cap'))
    assert all(value != host[key] for key, value in render['namespaces'].items())
print('DIAGNOSTIC CONFIRMED: preview fails cell-deny/cell-links; cell-nested renders. '
      'No launcher acceptance, policy change, or startup activation.')
'''


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True, help='existing production Dockerfile image')
    args = parser.parse_args()
    digest = subprocess.run(
        ['docker', 'image', 'inspect', args.image, '--format', '{{.Id}}'],
        check=True, capture_output=True, text=True,
    ).stdout.strip()
    command = ['docker', 'run', '--rm', '-i', '--network', 'none', '--user', '0:0',
               '--cap-drop', 'ALL']
    for cap in ('CHOWN', 'DAC_OVERRIDE', 'FOWNER', 'SETUID', 'SETGID', 'SETPCAP', 'KILL'):
        command += ['--cap-add', cap]
    for option in ('no-new-privileges=true', 'seccomp=unconfined', 'apparmor=unconfined',
                   'systempaths=unconfined'):
        command += ['--security-opt', option]
    command += ['--entrypoint', '/opt/venv/bin/python', digest, '-I', '-B', '-']
    print(json.dumps({'image': digest, 'command': command}), flush=True)
    return subprocess.run(command, input='CELL = ' + repr(CELL) + '\n' + CONTAINER,
                          text=True, timeout=210).returncode


if __name__ == '__main__':
    raise SystemExit(main())
