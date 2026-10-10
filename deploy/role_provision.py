"""Static owner provisioning cell: one pinned lease and one registry relay."""
from __future__ import annotations

import json
import os
import runpy
import stat
import sys


def enter(uid, data_root):
    common = runpy.run_path('/usr/local/libexec/ta-decoder.py')
    common['identity'](uid)
    lease, relay = os.fstat(3), os.fstat(4)
    if (not stat.S_ISDIR(lease.st_mode) or (lease.st_uid, lease.st_gid) != (uid, uid)
            or not stat.S_ISSOCK(relay.st_mode) or relay.st_nlink != 1):
        raise RuntimeError('provisioning sources are not admitted')
    host = common['namespaces']()
    host.update(source=[lease.st_dev, lease.st_ino], relay=[relay.st_dev, relay.st_ino])
    filter_fd = runpy.run_path('/app/tinyassets/providers/jail_seccomp.py')['program_fd'](
        profile='cell-nested')
    for fd in (3, 4, filter_fd):
        os.set_inheritable(fd, True)
    argv = ['/usr/bin/bwrap', '--die-with-parent', '--new-session', '--unshare-all',
            '--cap-drop', 'ALL', '--clearenv', '--setenv', 'PATH', '/usr/local/bin:/usr/bin:/bin',
            '--setenv', 'HOME', '/tmp', '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    for path in ('/usr', '/opt/venv', '/app', '/etc/ld.so.cache', '/bin', '/lib', '/lib64',
                 '/etc/ssl/certs/ca-certificates.crt'):
        if os.path.exists(path):
            argv.extend(['--ro-bind', path, path])
    argv.extend(['--bind-fd', '3', '/lease', '--bind-fd', '4', '/registry.sock',
                 '--proc', '/proc', '--dev', '/dev', '--size', str(64 * 1024 * 1024),
                 '--tmpfs', '/tmp', '--chdir', '/tmp', '--seccomp', str(filter_fd), '--',
                 '/opt/venv/bin/python', '-I', '-B', '/usr/local/libexec/ta-provision.py',
                 'inside', str(uid), data_root, json.dumps(host)])
    os.execv(argv[0], argv)


def inside(uid, data_root, host):
    source, relay = host.pop('source'), host.pop('relay')
    for path, expected in (('/lease', source), ('/registry.sock', relay)):
        info = os.stat(path, follow_symlinks=False)
        if [info.st_dev, info.st_ino] != expected:
            raise RuntimeError('provisioning mount differs from pinned source')
    proof = runpy.run_path('/usr/local/libexec/ta-decoder.py')['prove_cell'](
        host, data_root, uid, 'cell-nested')
    proof.update(source=source, relay=relay)
    import resource

    for kind, bound in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_NOFILE, 256),
                        (resource.RLIMIT_AS, 2 * 1024 ** 3), (resource.RLIMIT_CPU, 1800)):
        resource.setrlimit(kind, (bound, bound))
    sys.path.insert(0, '/app')
    from tinyassets.workspace_provision_cell import cell_main

    return cell_main(proof)


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'enter' and 0 < int(sys.argv[2]) < 100000:
        enter(int(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 5 and sys.argv[1] == 'inside' and 0 < int(sys.argv[2]) < 100000:
        raise SystemExit(inside(int(sys.argv[2]), sys.argv[3], json.loads(sys.argv[4])))
    else:
        raise SystemExit('unsupported provisioning bootstrap')
