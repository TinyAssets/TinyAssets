"""Static workspace-git entries: pinned owner directories, no host paths or secrets.

Two cells live here, and neither can be reached with a path, an executable or a
credential:

``enter``/``inside``
    one local git command in one owner directory, with NO network at all.
``enter-remote``/``inside-remote``
    one workspace operation (checkout, push, a remote probe) against the
    owner's whole command center plus the center's checking egress socket. The
    credential stays in the broker: the daemon opened an ephemeral route and
    passed the URL rewrites as git options, so this cell holds a route id and
    nothing else.
"""
from __future__ import annotations

import json
import os
import runpy
import stat
import sys

#: Where the center and the egress relay appear inside the remote cell.
CENTER = '/center'
EGRESS_SOCKET = '/remote-egress.sock'
#: The loopback port the in-cell forwarder binds for git's http.proxy.
PROXY_PORT = 3128


def enter(uid, data_root):
    common = runpy.run_path('/usr/local/libexec/ta-decoder.py')
    common['identity'](uid)
    source = os.fstat(3)
    if not stat.S_ISDIR(source.st_mode) or (source.st_uid, source.st_gid) != (uid, uid):
        raise RuntimeError('git source is not owned by the admitted identity')
    host = common['namespaces']()
    filter_fd = runpy.run_path('/app/tinyassets/providers/jail_seccomp.py')['program_fd'](
        profile='cell-links')
    os.set_inheritable(filter_fd, True)
    os.set_inheritable(3, True)
    argv = ['/usr/bin/bwrap', '--die-with-parent', '--new-session', '--unshare-all',
            '--cap-drop', 'ALL', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'HOME', '/tmp', '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    for path in ('/usr', '/opt/venv', '/app', '/etc/ld.so.cache', '/bin', '/lib', '/lib64'):
        if os.path.exists(path):
            argv.extend(['--ro-bind', path, path])
    argv.extend(['--bind-fd', '3', '/workspace', '--proc', '/proc',
                 '--dev', '/dev', '--tmpfs', '/tmp', '--chdir', '/workspace',
                 '--seccomp', str(filter_fd), '--', '/opt/venv/bin/python', '-I', '-B',
                 '/usr/local/libexec/ta-git.py', 'inside', str(uid), data_root,
                 json.dumps(host), json.dumps([source.st_dev, source.st_ino])])
    os.execv(argv[0], argv)


def inside(uid, data_root, host, source):
    mounted = os.stat('/workspace', follow_symlinks=False)
    if [mounted.st_dev, mounted.st_ino] != source:
        raise RuntimeError('git mount does not match its pinned source')
    common = runpy.run_path('/usr/local/libexec/ta-decoder.py')
    proof = common['prove_cell'](host, data_root, uid, 'cell-links')
    proof['source'] = source
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (1024 ** 3, 1024 ** 3))
    resource.setrlimit(resource.RLIMIT_CPU, (60, 60))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    sys.path.insert(0, '/app')
    from tinyassets.workspace_git import run_git

    raw = sys.stdin.buffer.read(65537)
    if len(raw) > 65536:
        raise ValueError('git request exceeds its bound')
    request = json.loads(raw)
    if (not isinstance(request, dict) or set(request) != {'argv', 'options', 'timeout_s'}
            or not isinstance(request['argv'], list) or not request['argv']
            or not isinstance(request['options'], list)
            or any(not isinstance(value, str) or '\0' in value
                   for value in request['argv'] + request['options'])
            or type(request['timeout_s']) not in (int, float)
            or not 0 < request['timeout_s'] <= 60):
        raise ValueError('invalid git operation')
    if request['argv'][0].startswith('-'):
        raise ValueError('git operation must start with a subcommand')
    os.mkdir('/tmp/git-home', 0o700)
    # Repository configuration and hooks are untrusted code INSIDE this cell.
    # The protected command scope has exactly one trusted directory, no '*'.
    result = run_git(request['argv'], cwd='/workspace', home_dir='/tmp/git-home',
        path='/usr/bin:/bin', git_binary='/usr/bin/git', timeout_s=request['timeout_s'],
        options=(*request['options'], '-c', 'safe.directory=', '-c', 'safe.directory=/workspace'))
    payload = dict(returncode=result.returncode, stdout=result.stdout_tail,
                   stderr=result.stderr_scrubbed)
    sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n'
                            + json.dumps(payload).encode())
    return 0


def enter_remote(uid, data_root, mode):
    """The workspace-remote cell: the owner's center, and its egress when routed.

    ``mode`` is ``'e'`` for an operation that reaches a remote and ``'-'`` for
    one that only makes an owner directory. The routeless shape has no socket
    bound at all, so making an empty workspace cannot reach the network.
    """
    common = runpy.run_path('/usr/local/libexec/ta-decoder.py')
    common['identity'](uid)
    center = os.fstat(3)
    # The center root is daemon-owned with this owner's group; inside the
    # mapper's namespace the daemon's uid can only read back as overflow.
    if not stat.S_ISDIR(center.st_mode) or center.st_gid != uid:
        raise RuntimeError('workspace center is not labelled for the admitted identity')
    host = common['namespaces']()
    host['source'] = [center.st_dev, center.st_ino]
    if mode == 'e':
        relay = os.fstat(4)
        if not stat.S_ISSOCK(relay.st_mode) or relay.st_nlink != 1:
            raise RuntimeError('workspace egress relay is not a socket')
        host['relay'] = [relay.st_dev, relay.st_ino]
        os.set_inheritable(4, True)
    filter_fd = runpy.run_path('/app/tinyassets/providers/jail_seccomp.py')['program_fd'](
        profile='cell-links')
    os.set_inheritable(filter_fd, True)
    os.set_inheritable(3, True)
    argv = ['/usr/bin/bwrap', '--die-with-parent', '--new-session', '--unshare-all',
            '--cap-drop', 'ALL', '--clearenv', '--setenv', 'PATH', '/usr/bin:/bin',
            '--setenv', 'HOME', '/tmp', '--setenv', 'LANG', 'C.UTF-8',
            '--setenv', 'PYTHONDONTWRITEBYTECODE', '1']
    for path in ('/usr', '/opt/venv', '/app', '/etc/ld.so.cache', '/bin', '/lib', '/lib64'):
        if os.path.exists(path):
            argv.extend(['--ro-bind', path, path])
    # No /etc/ssl/certs: the broker owns the TLS session. This cell speaks
    # plain HTTP to an invalid host name that only its route resolves.
    argv.extend(['--bind-fd', '3', CENTER])
    if mode == 'e':
        argv.extend(['--bind-fd', '4', EGRESS_SOCKET])
    argv.extend(['--proc', '/proc', '--dev', '/dev',
                 '--size', str(64 * 1024 * 1024), '--tmpfs', '/tmp',
                 '--chdir', '/tmp', '--seccomp', str(filter_fd), '--',
                 '/opt/venv/bin/python', '-I', '-B', '/usr/local/libexec/ta-git.py',
                 'inside-remote', str(uid), data_root, mode,
                 json.dumps(host, sort_keys=True)])
    os.execv(argv[0], argv)


def _forward(port, path):
    """Serve ``127.0.0.1:port`` into the bound unix socket, in this process.

    git speaks to a proxy over TCP and the cell has no network interface, so
    one loopback listener inside its own empty network namespace is the whole
    route. Nothing is accepted from anywhere else: the namespace has no other
    address, and the daemon's proxy is what decides every destination.
    """
    import socket
    import threading

    def pump(source, target):
        try:
            while True:
                data = source.recv(65536)
                if not data:
                    break
                target.sendall(data)
        except OSError:
            pass
        for handle in (source, target):
            try:
                handle.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def serve(client):
        upstream = socket.socket(socket.AF_UNIX)
        try:
            upstream.connect(path)
        except OSError:
            client.close()
            upstream.close()
            return
        back = threading.Thread(target=pump, args=(upstream, client), daemon=True)
        back.start()
        pump(client, upstream)
        back.join()
        client.close()
        upstream.close()

    server = socket.socket()
    server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    server.bind(('127.0.0.1', port))
    server.listen(16)

    def accept():
        while True:
            try:
                client, _ = server.accept()
            except OSError:
                return
            threading.Thread(target=serve, args=(client,), daemon=True).start()

    threading.Thread(target=accept, name='remote-egress', daemon=True).start()


def inside_remote(uid, data_root, mode, host):
    source = host.pop('source')
    relay = host.pop('relay', None)
    mounted = os.stat(CENTER, follow_symlinks=False)
    if [mounted.st_dev, mounted.st_ino] != source:
        raise RuntimeError('workspace center mount does not match its pinned source')
    if (relay is not None) != (mode == 'e'):
        raise RuntimeError('workspace relay differs from its admitted flag')
    if relay is not None:
        bound = os.stat(EGRESS_SOCKET, follow_symlinks=False)
        if [bound.st_dev, bound.st_ino] != relay or not stat.S_ISSOCK(bound.st_mode):
            raise RuntimeError('workspace egress mount does not match its pinned source')
    common = runpy.run_path('/usr/local/libexec/ta-decoder.py')
    proof = common['prove_cell'](host, data_root, uid, 'cell-links')
    proof['source'] = source
    proof['relay'] = relay
    import resource

    for kind, bound_value in ((resource.RLIMIT_AS, 2 * 1024 ** 3),
                              (resource.RLIMIT_CPU, 1200),
                              (resource.RLIMIT_NOFILE, 256),
                              (resource.RLIMIT_CORE, 0)):
        resource.setrlimit(kind, (bound_value, bound_value))
    sys.path.insert(0, '/app')
    from tinyassets.workspace_fs import open_dir_nofollow
    from tinyassets.workspace_remote_cell import MAX_REQUEST_BYTES, perform

    # stdin and stdout are the SAME socket, so the exchange is line framed:
    # the proof, then the request, then the answer. A read to EOF would wait
    # for a daemon that is itself waiting for this cell's proof.
    sys.stdout.buffer.write(json.dumps({'cell': proof}).encode() + b'\n')
    sys.stdout.buffer.flush()
    raw = sys.stdin.buffer.readline(MAX_REQUEST_BYTES + 1)
    if len(raw) > MAX_REQUEST_BYTES or not raw.endswith(b'\n'):
        raise ValueError('workspace request exceeds its bound or is unframed')
    if relay is not None:
        _forward(PROXY_PORT, EGRESS_SOCKET)
    os.mkdir('/tmp/cell', 0o700)
    fd = open_dir_nofollow(CENTER)
    try:
        answer = perform(json.loads(raw), root_fd=fd, root_path=CENTER,
                         scratch='/tmp/cell', git_binary='/usr/bin/git',
                         path='/usr/bin:/bin')
    finally:
        os.close(fd)
    sys.stdout.buffer.write(json.dumps(answer).encode() + b'\n')
    sys.stdout.buffer.flush()
    return 0


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'enter':
        enter(int(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 6 and sys.argv[1] == 'inside':
        raise SystemExit(inside(int(sys.argv[2]), sys.argv[3],
                               json.loads(sys.argv[4]), json.loads(sys.argv[5])))
    elif (len(sys.argv) == 5 and sys.argv[1] == 'enter-remote'
            and 0 < int(sys.argv[2]) < 100000 and sys.argv[4] in ('e', '-')):
        enter_remote(int(sys.argv[2]), sys.argv[3], sys.argv[4])
    elif (len(sys.argv) == 6 and sys.argv[1] == 'inside-remote'
            and 0 < int(sys.argv[2]) < 100000 and sys.argv[4] in ('e', '-')):
        raise SystemExit(inside_remote(int(sys.argv[2]), sys.argv[3], sys.argv[4],
                                       json.loads(sys.argv[5])))
    else:
        raise SystemExit('unsupported workspace-git bootstrap')
