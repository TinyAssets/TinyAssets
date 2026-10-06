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
# Shipped CLI install trees and their root-owned image wrappers; no provider
# is named here, the image layout alone decides what a cell may exec.
INSTALL_TREE = re.compile(r'/opt/[a-z0-9][a-z0-9.-]*-install/.+')
WRAPPER_DIR = '/usr/local/bin/'
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


def shipped_executable(path):
    """An image file in a shipped install location the payload cannot have written.

    Inside the cell's user namespace image root appears as the overflow UID, so
    ownership is checked as "not the payload identity", never "is UID 0".
    """
    executable = os.path.realpath(path)
    if not (INSTALL_TREE.fullmatch(executable)
            or os.path.dirname(executable) + '/' == WRAPPER_DIR):
        return False
    try:
        info = os.stat(executable)
    except OSError:
        return False
    return (stat.S_ISREG(info.st_mode) and info.st_uid != os.getuid()
            and not info.st_mode & 0o022)


NAME = re.compile(r'[A-Z_][A-Z0-9_]{0,127}')
RELATIVE = re.compile(r'[A-Za-z0-9._-]{1,128}(/[A-Za-z0-9._-]{1,128}){0,3}')


def _relative(value):
    return (type(value) is str and RELATIVE.fullmatch(value) is not None
            and not any(part in ('.', '..') for part in value.split('/')))


def validate_view(view):
    """The D88 execution view, re-checked inside the cell; never trusted as sent."""
    if (type(view) is not dict or set(view) != {'persistent', 'home', 'secret_fds'}
            or type(view['persistent']) is not bool
            or (view['home'] is not None and (
                type(view['home']) is not str or not NAME.fullmatch(view['home'])))
            or type(view['secret_fds']) is not list or len(view['secret_fds']) > 4
            or any(type(item) is not list or len(item) != 2 or type(item[0]) is not str
                   or not NAME.fullmatch(item[0]) or item[0] == view['home']
                   or not _relative(item[1]) for item in view['secret_fds'])
            or len({item[0] for item in view['secret_fds']}) != len(view['secret_fds'])):
        raise ValueError('invalid provider execution view')
    return view


def validate(raw, data_root, *, execution=False):
    config = json.loads(raw)
    if type(config) is not dict or not (set(config) == {'argv', 'env'} or (
            execution and set(config) == {'argv', 'env', 'view'})):
        raise ValueError('invalid provider config')
    argv, env = config['argv'], config['env']
    # An empty argument is a real CLI value (`--tools ""`); never the executable.
    if (type(argv) is not list or not 1 <= len(argv) <= MAX_ARGS
            or any(type(item) is not str or not (index or item) or len(item) > (
                MAX_CONFIG_BYTES if execution else 4096) or '\0' in item
                   for index, item in enumerate(argv))
            or type(env) is not dict or len(env) > MAX_ENV
            or any(type(key) is not str or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', key)
                   or type(value) is not str or len(value) > 8192 or '\0' in value
                   for key, value in env.items())):
        raise ValueError('invalid provider config')
    # No host data path survives into the cell; the snapshot is pre-rewritten.
    if any(data_root in item for item in (*argv, *env.values())):
        raise ValueError('provider config names a host data path')
    if not shipped_executable(argv[0]):
        raise ValueError('provider executable is outside the shipped install trees')
    if not execution:
        return argv, {**safe_environment(env), **FIXED_ENV}
    view = validate_view(config.get('view', {'persistent': False, 'home': None,
                                            'secret_fds': []}))
    return argv, {**safe_environment(env), **FIXED_ENV}, view


def copy_snapshot(source, destination, *, exclude=()):
    """Copy sealed regular credential bytes into disposable owner-private state.

    ``exclude`` names snapshot-relative files that travel on a pipe instead.
    """
    remaining = [128, 8 * 1024 * 1024]

    def copy(directory, target, depth):
        if depth > 8:
            raise ValueError('provider snapshot nesting exceeds its bound')
        try:
            os.mkdir(target, 0o700)
        except FileExistsError:
            # Only the private home itself pre-exists: the parent of a persistent
            # view's sessions mount. A snapshot entry there refuses (open 'xb').
            if depth or not stat.S_ISDIR(os.stat(target, follow_symlinks=False).st_mode):
                raise
        with os.scandir(directory) as entries:
            for entry in entries:
                remaining[0] -= 1
                if remaining[0] < 0 or entry.is_symlink():
                    raise ValueError('provider snapshot is not a bounded regular tree')
                path = target + '/' + entry.name
                if entry.path[len(source) + 1:] in exclude:
                    continue
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


def prepare_view(view, private, env, *, snapshot=SNAPSHOT):
    """Apply a validated D88 view: the home variable and pipe-carried secrets."""
    if view['home'] is not None:
        env[view['home']] = private
    for name, relative in view['secret_fds']:
        descriptor = os.open(snapshot + '/' + relative, os.O_RDONLY | os.O_NOFOLLOW)
        try:
            info = os.fstat(descriptor)
            if not stat.S_ISREG(info.st_mode) or info.st_size > 60 * 1024:
                raise ValueError('provider secret file is not admitted')
            data = os.read(descriptor, 60 * 1024 + 1)
        finally:
            os.close(descriptor)
        if len(data) > 60 * 1024:
            raise ValueError('provider secret file is not admitted')
        reader, writer = os.pipe()
        try:
            # Below the pipe capacity, so this never waits for the reader.
            os.write(writer, data)
        finally:
            os.close(writer)
        os.set_inheritable(reader, True)
        env[name] = str(reader)


def cell_main(data_root, *, execution=False, egress=False):
    # No RLIMIT_AS: Node/V8 reserves large virtual ranges. Discovery has the
    # mapper's fixed 35s deadline; execution has no wall clock, only CPU time
    # plus the mapper's external RSS/process-count guard. RLIMIT_NPROC counts
    # THREADS of the owner's UID across all its cells (D88): a shipped Codex
    # runs ~2 per CPU (40 at 20 CPUs), so execution's bound is per owner, not 64.
    for limit, cap in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, 600 if execution else 30),
                       (resource.RLIMIT_NOFILE, 256),
                       (resource.RLIMIT_NPROC, 512 if execution else 64),
                       (resource.RLIMIT_FSIZE, 64 * 1024 * 1024)):
        resource.setrlimit(limit, (cap, cap))
    validated = validate(read_config(), data_root, execution=execution)
    argv, env = validated[:2]
    view = validated[2] if execution else None
    private = '/tmp/provider-auth'
    secrets = [] if view is None else view['secret_fds']
    copy_snapshot(SNAPSHOT, private, exclude={item[1] for item in secrets})
    def relocate(value):
        return private + value[len(SNAPSHOT):] if (
            value == SNAPSHOT or value.startswith(SNAPSHOT + '/')) else value
    argv = [relocate(value) for value in argv]
    env = {key: relocate(value) for key, value in env.items()}
    if execution:
        prepare_view(view, private, env)
    # Execution's /workspace is mounted by the entry: private tmpfs scratch or
    # the owner's pinned persistent workspace (D88).
    os.chdir('/workspace' if execution else private)
    if egress:
        from tinyassets.universe_egress import FORWARDER, PROXY_ENV

        env.update(PROXY_ENV)
        argv = ['/opt/venv/bin/python', '-I', '-S', '-c', FORWARDER,
                '3128=/provider-egress.sock', '--', *argv]
    os.execve(argv[0], argv, env)
