"""Fixed provider-discovery entry, imported only after owner confinement (D82).

The mapper already closed every mount descriptor (prove_cell). One bounded
config line arrives on the cell stream; afterwards the installed CLI inherits
that stream as raw stdin/stdout. Stdlib only: no application initialization.
"""
from __future__ import annotations

import json
import os
import re
import resource
import stat

MAX_CONFIG_BYTES = 64 * 1024
MAX_ARGS = 32
MAX_ENV = 128
INSTALL_TREES = ('/opt/codex-install/', '/opt/claude-code-install/')
SNAPSHOT = '/snapshot'
FIXED_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'USERPROFILE': '/tmp',
             'TMPDIR': '/tmp', 'TMP': '/tmp', 'TEMP': '/tmp', 'LANG': 'C.UTF-8'}


def safe_environment(env):
    """Only display settings and in-cell auth/scratch paths, never ambient tokens."""
    allowed = {}
    for key, value in env.items():
        if (type(key) is not str or type(value) is not str
                or key.startswith(('LD_', 'PYTHON', 'TINYASSETS_'))
                or key in {'NODE_OPTIONS', 'NODE_PATH', 'BASH_ENV', 'ENV'}):
            continue
        if key in {'TERM', 'NO_COLOR', 'LC_ALL'}:
            if len(value) <= 128 and re.fullmatch(r'[A-Za-z0-9_.@+-]*', value):
                allowed[key] = value
        elif (value == SNAPSHOT or value.startswith(SNAPSHOT + '/')
              or value == '/tmp' or value.startswith('/tmp/')):
            if not any(part in ('.', '..') for part in value.split('/')):
                allowed[key] = value
    return allowed


def read_config(fd=0):
    """Read exactly one line; never consume bytes that belong to the CLI."""
    line = bytearray()
    while True:
        byte = os.read(fd, 1)
        if not byte:
            raise ValueError('provider config ended early')
        if byte == b'\n':
            return bytes(line)
        line += byte
        if len(line) > MAX_CONFIG_BYTES:
            raise ValueError('provider config exceeds its bound')


def validate(raw, data_root):
    config = json.loads(raw)
    if type(config) is not dict or set(config) != {'argv', 'env'}:
        raise ValueError('invalid provider config')
    argv, env = config['argv'], config['env']
    if (type(argv) is not list or not 1 <= len(argv) <= MAX_ARGS
            or any(type(item) is not str or not 0 < len(item) <= 4096 or '\0' in item
                   for item in argv)
            or type(env) is not dict or len(env) > MAX_ENV
            or any(type(key) is not str or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', key)
                   or type(value) is not str or len(value) > 8192 or '\0' in value
                   for key, value in env.items())):
        raise ValueError('invalid provider config')
    # No host data path survives into the cell; the snapshot is pre-rewritten.
    if any(data_root in item for item in (*argv, *env.values())):
        raise ValueError('provider config names a host data path')
    executable = os.path.realpath(argv[0])
    if (not (any(executable.startswith(tree) for tree in INSTALL_TREES)
             or executable == '/usr/local/bin/codex')
            or not os.path.isfile(executable)):
        raise ValueError('provider executable is outside the shipped install trees')
    return argv, {**safe_environment(env), **FIXED_ENV}


def copy_snapshot(source, destination):
    """Copy sealed regular credential bytes into disposable owner-private state."""
    remaining = [128, 8 * 1024 * 1024]

    def copy(directory, target, depth):
        if depth > 8:
            raise ValueError('provider snapshot nesting exceeds its bound')
        os.mkdir(target, 0o700)
        with os.scandir(directory) as entries:
            for entry in entries:
                remaining[0] -= 1
                if remaining[0] < 0 or entry.is_symlink():
                    raise ValueError('provider snapshot is not a bounded regular tree')
                path = target + '/' + entry.name
                if entry.is_dir(follow_symlinks=False):
                    copy(entry.path, path, depth + 1)
                    continue
                descriptor = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                try:
                    info = os.fstat(descriptor)
                    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                            or info.st_size > remaining[1]):
                        raise ValueError('provider snapshot file is not admitted')
                    with open(path, 'xb') as output:
                        while chunk := os.read(descriptor, min(65536, remaining[1] + 1)):
                            remaining[1] -= len(chunk)
                            if remaining[1] < 0:
                                raise ValueError('provider snapshot bytes exceed their bound')
                            output.write(chunk)
                    os.chmod(path, 0o600)
                finally:
                    os.close(descriptor)
    copy(source, destination, 0)


def cell_main(data_root):
    # No RLIMIT_AS: Node/V8 reserves large virtual ranges. The mapper's fixed
    # 35s class deadline is the wall-clock bound.
    for limit, cap in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, 30),
                       (resource.RLIMIT_NOFILE, 256), (resource.RLIMIT_NPROC, 64),
                       (resource.RLIMIT_FSIZE, 64 * 1024 * 1024)):
        resource.setrlimit(limit, (cap, cap))
    argv, env = validate(read_config(), data_root)
    private = '/tmp/provider-auth'
    copy_snapshot(SNAPSHOT, private)
    def relocate(value):
        return private + value[len(SNAPSHOT):] if (
            value == SNAPSHOT or value.startswith(SNAPSHOT + '/')) else value
    argv = [relocate(value) for value in argv]
    env = {key: relocate(value) for key, value in env.items()}
    os.chdir(private)
    os.execve(argv[0], argv, env)
