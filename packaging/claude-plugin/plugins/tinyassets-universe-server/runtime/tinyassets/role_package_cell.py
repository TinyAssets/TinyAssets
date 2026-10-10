"""Trusted package supervisor, entered only after owner-cell confinement."""
from __future__ import annotations

import ctypes
import os
import resource
import signal
import subprocess
import time

from tinyassets.role_package_manifest import MANIFEST_BYTES, PACKAGE_BYTES, RUNTIMES, parse
from tinyassets.workspace_fs import read_package_manifest, verify_package_tree


def manifest(revision):
    fd = os.open('/package', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        raw = read_package_manifest(fd, owner_uid=os.fstat(fd).st_uid, max_bytes=MANIFEST_BYTES)
        doc = parse(raw, revision)
        verify_package_tree(fd, doc['files'], owner_uid=os.fstat(fd).st_uid,
                            max_bytes=PACKAGE_BYTES)
        return doc
    finally:
        os.close(fd)


def run(doc, *, broker, egress=False):
    from tinyassets.node_sandbox import read_process_tree

    if bool(doc['slots']) != broker:
        raise ValueError('package broker slots do not match admission')
    if doc['egress'] is not egress:
        raise ValueError('package egress does not match admission')
    if ctypes.CDLL(None, use_errno=True).prctl(4, 0, 0, 0, 0) != 0:
        raise RuntimeError('package supervisor dumpability could not be retired')
    for kind, value in ((resource.RLIMIT_CPU, 300), (resource.RLIMIT_NPROC, 64),
                        (resource.RLIMIT_NOFILE, 128),
                        (resource.RLIMIT_FSIZE, 64 * 1024 * 1024), (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (value, value))
    runtime = doc['runtime']
    if runtime in ('python', 'shell'):
        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024, 512 * 1024 * 1024))
    prefix = ['-I', '-B', '-c',
        'import os,runpy,sys;sys.path[:0]=[os.path.dirname(sys.argv[1]),"/package"];'
        'sys.argv=sys.argv[1:];runpy.run_path(sys.argv[0],run_name="__main__")'
    ] if runtime == 'python' else (
        ['--max-old-space-size=256'] if runtime == 'node' else [])
    argv = [RUNTIMES[runtime], *prefix, '/package/' + doc['entry'], *doc['args']]
    env = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'TMPDIR': '/tmp', 'LANG': 'C.UTF-8'}
    if broker:
        env['TINYASSETS_PACKAGE_BROKER'] = '/package-broker.sock'
    if egress:
        from tinyassets.universe_egress import FORWARDER, PROXY_ENV

        # The checked relay is the only route out; no network interface exists.
        env.update(PROXY_ENV)
        argv = ['/opt/venv/bin/python', '-I', '-S', '-c', FORWARDER,
                '3128=/package-egress.sock', '--', *argv]
    child = subprocess.Popen(argv, stdin=0, stdout=1, stderr=2, cwd='/package',
                             env=env, close_fds=True, start_new_session=True)
    # Only the payload now owns stdin/stdout. Its consumer sees EOF promptly;
    # the independent mapper channel carries authenticated completion/reaping.
    os.close(0)
    os.close(1)
    # No wall clock: a stdio server lives until it exits, the consumer revokes
    # the cell or a resource guard below ends it.
    try:
        while child.poll() is None:
            # The whole private PID namespace includes detached/orphaned
            # descendants; tracking just the original leader would miss them.
            count, rss = read_process_tree(1)
            if count < 0 or rss < 0 or count > 64 or rss > 512 * 1024 * 1024:
                os.killpg(child.pid, signal.SIGKILL)
                child.wait(timeout=5)
                return 137
            time.sleep(0.05)
        return child.returncode
    finally:
        # Descendants that outlived their leader are never a successful daemon.
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        for name in os.listdir('/proc'):
            if name.isdigit() and int(name) not in (1, os.getpid()):
                try:
                    os.kill(int(name), signal.SIGKILL)
                except ProcessLookupError:
                    pass
        child.wait(timeout=5)
