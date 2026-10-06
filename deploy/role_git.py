"""Static workspace-git entry: pinned owner directory, no host paths or secrets."""
from __future__ import annotations

import json
import os
import runpy
import stat
import sys


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


if __name__ == '__main__':
    if len(sys.argv) == 4 and sys.argv[1] == 'enter':
        enter(int(sys.argv[2]), sys.argv[3])
    elif len(sys.argv) == 6 and sys.argv[1] == 'inside':
        raise SystemExit(inside(int(sys.argv[2]), sys.argv[3],
                               json.loads(sys.argv[4]), json.loads(sys.argv[5])))
    else:
        raise SystemExit('unsupported workspace-git bootstrap')
