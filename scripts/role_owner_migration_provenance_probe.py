"""D60/D61 legacy provenance diagnostic; NOT a migration implementation.

Run the unchanged D59 reader/profile matrix with dedicated engine identities,
but start the preplanted inode in the legacy shared-identity layout. Model the
proposed pathname-based chown after its original foreign name has disappeared.
Every final descriptor has Alice's correct dedicated UID/GID and nlink=1.
The default applies founder D61: sole reachability establishes legacy ownership.
--historical reproduces the prior D63 classification without changing its cases.
Only disposable synthetic files are changed; no host mounts or live data.
"""
from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

from role_owner_gid_probe import CELL, CONTAINER


def substitute(source, old, new):
    if old not in source:
        raise RuntimeError("D59 diagnostic changed; reconcile the provenance probe")
    return source.replace(old, new)


def program(*, historical=False):
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
    if historical:
        return 'CELL = ' + repr(cell) + '\n' + setup
    setup = substitute(setup, 'original = outside.stat()',
                        'os.chown(outside,1001,1001)\noriginal = outside.stat()')
    # Same 114 reads, same sentinel, same profiles: disclose the changed legacy
    # provenance rule rather than pretending these bytes were denied.
    scanner = Path(__file__).with_name('role_legacy_alias_scan.py').read_text()
    prelude = "SCAN_NS={'__name__':'legacy_scan'}\nexec(" + repr(scanner) + ",SCAN_NS)\n"
    setup = substitute(setup,
        '            # UNSAFE MODEL, not shipped code: infer identity from its surviving path.\n'
        '            os.chown(target,300001,300001)',
        "            report=SCAN_NS['scan'](root,[center,'bob'],"
        "frozenset({(1001,1001),(1001,300001),(1001,300002),(300001,300001)}))\n"
        "            assert not report['findings'], report\n"
        "            # Founder D61: all names of this legacy inode are in this tree.\n"
        "            os.chown(target,300001,300001)")
    cell = cell.replace("outcome='FOREIGN_BYTES'", "outcome='ASSIGNED_LEGACY_BYTES'")
    setup = substitute(setup,
        "outcome='FOREIGN_BYTES' if sentinel in answer else 'NO_FOREIGN_BYTES'",
        "assert sentinel in answer, 'sole-reachability legacy data was lost'\n"
        "                outcome='ASSIGNED_LEGACY_BYTES'")
    setup = substitute(setup, "failures=[]", "failures=[]\nassigned_legacy_reads=0")
    setup = substitute(setup, "                answer=read()",
                        "                answer=read()\n                assigned_legacy_reads+=1")
    setup = substitute(setup, "foreign_reads=len(failures),",
                        "foreign_reads=len(failures),assigned_legacy_reads=assigned_legacy_reads,")
    # Cross-tree live names are a separate model. Preserve all names and bytes
    # behind a platform-only directory; never assign either owner to the inode.
    quarantine = r'''
qroot=Path(tempfile.mkdtemp(prefix='legacy-quarantine-')); qroot.chmod(0o755)
for name in ('alice','bob'):
    (qroot/name).mkdir(); os.chown(qroot/name,1001,1001)
source=qroot/'bob'/'record'; source.write_bytes(b'SHARED-LEGACY')
os.chown(source,1001,1001); os.link(source,qroot/'alice'/'alias')
report=SCAN_NS['scan'](qroot,['alice','bob'])
assert report['finding_counts']=={'cross_owner_inode':1}, report
row=report['findings'][0]
quarantine=qroot/'.quarantine'; quarantine.mkdir(mode=0o700)
for index,path in enumerate(row['paths']):
    os.rename(qroot/path,quarantine/str(index))
for name in ('alice','bob'):
    assert list((qroot/name).iterdir())==[]
assert (quarantine/'0').stat().st_ino==(quarantine/'1').stat().st_ino
assert (quarantine/'0').read_bytes()==b'SHARED-LEGACY'
assert (quarantine/'0').stat().st_uid==1001
pid=os.fork()
if pid==0:
    try:
        launcher['retire_child']('daemon')
        try: (quarantine/'0').read_bytes()
        except PermissionError: os._exit(0)
        os._exit(1)
    except BaseException: os._exit(2)
assert os.waitpid(pid,0)[1]==0
print(json.dumps(dict(quarantined_inodes=1,quarantined_names=2,
    owner_assigned=False,daemon_denied=True,alarm='cross_owner_inode',
    model_only=True)),flush=True)
'''
    setup = substitute(setup, "launcher['retire_migration_authority']()",
                        quarantine + "\nlauncher['retire_migration_authority']()")
    return prelude + 'CELL = ' + repr(cell) + '\n' + setup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--image', required=True)
    parser.add_argument('--historical', action='store_true')
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
    return subprocess.run(command, input=program(historical=args.historical),
                          text=True, timeout=180).returncode


if __name__ == '__main__':
    raise SystemExit(main())
