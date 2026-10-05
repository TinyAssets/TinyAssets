"""D60 legacy provenance diagnostic; this is NOT a migration implementation.

Run the unchanged D59 reader/profile matrix with dedicated engine identities,
but start the preplanted inode in the legacy shared-identity layout. Model the
proposed pathname-based chown after its original foreign name has disappeared.
Every final descriptor has Alice's correct dedicated UID/GID and nlink=1.
Exit 3 demonstrates why migration cannot infer ownership from the last name.
Only disposable synthetic files are changed; no host mounts or live data.
"""
from __future__ import annotations

import argparse
import json
import subprocess

from role_owner_gid_probe import CELL, CONTAINER


def substitute(source, old, new):
    if old not in source:
        raise RuntimeError("D59 diagnostic changed; reconcile the provenance probe")
    return source.replace(old, new)


def program():
    # Reuse every positive control and actual daemon reader from D59. The
    # deliberate insecure operation is visible here, not hidden in a fixture.
    cell = CELL.replace('1003', '300001').replace('200000', '300001')
    cell = substitute(cell, 'before.st_gid == 65534', 'before.st_gid == 300001')
    cell = substitute(cell, 'and before.st_nlink == 1',
                      'and before.st_nlink == 1, (before.st_uid,before.st_gid,before.st_nlink)')
    setup = CONTAINER.replace('200000', '300001').replace('200001', '300002')
    setup = substitute(setup, 'os.chown(foreign,1003,300002)', 'os.chown(foreign,1001,1001)')
    setup = substitute(setup, "'--reuid=1003'", "'--reuid=300001'")
    setup = substitute(setup,
        "assert target.stat().st_gid != 300001, 'unmodified alias must fail the D58 predicate'",
        "assert (target.stat().st_uid,target.stat().st_gid,target.stat().st_nlink) "
        "== (1001,1001,1)\n"
        "            # UNSAFE MODEL, not shipped code: infer identity from its surviving path.\n"
        "            os.chown(target,300001,300001)")
    setup = substitute(setup, 'info.st_uid in (1001,1003)', 'info.st_uid == 300001')
    return 'CELL = ' + repr(cell) + '\n' + setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
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
    print(json.dumps({'image': digest, 'command': command,
                      'modeled_migration_only': True}), flush=True)
    return subprocess.run(command, input=program(), text=True, timeout=180).returncode


if __name__ == '__main__':
    raise SystemExit(main())
