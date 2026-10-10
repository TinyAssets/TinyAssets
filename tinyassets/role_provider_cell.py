"""Fixed provider-discovery entry, imported only after owner confinement (D82).

The mapper already closed every mount descriptor (prove_cell). One bounded
config line arrives on the cell stream; afterwards the installed CLI inherits
that stream as raw stdin/stdout. Stdlib only: no application initialization.
"""
from __future__ import annotations

import json
import os
import re
import stat

MAX_CONFIG_BYTES = 64 * 1024
MAX_ARGS = 32
# Served tool policies have more arguments than discovery commands. The whole
# launch still fits MAX_CONFIG_BYTES; this is not a process/resource limit.
MAX_EXEC_ARGS = 128
MAX_ENV = 128
# Shipped CLI install trees and their root-owned image wrappers; no provider
# is named here, the image layout alone decides what a cell may exec.
INSTALL_TREE = re.compile(r'/opt/[a-z0-9][a-z0-9.-]*-install/.+')
WRAPPER_DIR = '/usr/local/bin/'
SNAPSHOT = '/snapshot'
#: The cell's private, writable copy of the launch snapshot.
AUTH_DIR = '/tmp/provider-auth'
#: The provider's working directory inside an execution cell: empty, owner-private.
WORKSPACE = '/tmp/workspace'
#: Where the cell reaches its owner's engine MCP route (served turns only).
ENGINE_SOCKET = '/provider-engine.sock'
#: Settings values that cannot carry a secret.
SWITCH_VALUES = frozenset({'0', '1', 'true', 'false', 'yes', 'no', 'on', 'off'})
MAX_VALUE_FILE_BYTES = 8192
FIXED_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'USERPROFILE': '/tmp',
             'TMPDIR': '/tmp', 'TMP': '/tmp', 'TEMP': '/tmp', 'LANG': 'C.UTF-8'}


def file_values(directory):
    """Stripped text of each small top-level regular file in ``directory``."""
    values = set()
    if not os.path.isdir(directory):
        return frozenset()
    with os.scandir(directory) as entries:
        for entry in entries:
            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                continue
            try:
                descriptor = os.open(entry.path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except OSError:
                continue
            try:
                info = os.fstat(descriptor)
                if stat.S_ISREG(info.st_mode) and info.st_size <= MAX_VALUE_FILE_BYTES:
                    text = os.read(descriptor, MAX_VALUE_FILE_BYTES).decode('utf-8', 'replace')
                    if text.strip():
                        values.add(text.strip())
            finally:
                os.close(descriptor)
    return frozenset(values)


def safe_environment(env, owner_values=frozenset()):
    """Display settings, switches, in-cell auth/scratch paths and the owner's own
    sealed credential values; never an ambient daemon token.

    ``owner_values`` are the stripped contents of the launch snapshot's files:
    a value equal to one of them is already readable inside this cell.
    """
    allowed = {}
    for key, value in env.items():
        if (type(key) is not str or type(value) is not str
                or key.startswith(('LD_', 'PYTHON', 'TINYASSETS_'))
                or key in {'NODE_OPTIONS', 'NODE_PATH', 'BASH_ENV', 'ENV'}):
            continue
        if value and value in owner_values:
            allowed[key] = value
        elif value.lower() in SWITCH_VALUES:
            allowed[key] = value
        elif key in {'TERM', 'NO_COLOR', 'LC_ALL'}:
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


def validate(raw, data_root, *, execution=False):
    config = json.loads(raw)
    if (type(config) is not dict or not {'argv', 'env'} <= set(config)
            or set(config) - {'argv', 'env', 'engine_port'}):
        raise ValueError('invalid provider config')
    argv, env = config['argv'], config['env']
    engine_port = config.get('engine_port')
    if engine_port is not None and (not execution or type(engine_port) is not int
                                    or not 1024 <= engine_port <= 65535 or engine_port == 3128):
        raise ValueError('invalid provider engine port')
    if (type(argv) is not list or not 1 <= len(argv) <= (MAX_EXEC_ARGS if execution else MAX_ARGS)
            or any(type(item) is not str or not (0 if execution else 1) <= len(item) <= (
                MAX_CONFIG_BYTES if execution else 4096) or '\0' in item
                   for item in argv)
            or type(env) is not dict or len(env) > MAX_ENV
            or any(type(key) is not str or not re.fullmatch(r'[A-Z_][A-Z0-9_]{0,127}', key)
                   or type(value) is not str or len(value) > 8192 or '\0' in value
                   for key, value in env.items())):
        raise ValueError('invalid provider config')
    # No host data path survives into the cell; the snapshot is pre-rewritten.
    if any(data_root in item for item in (*argv, *env.values())):
        raise ValueError('provider config names a host data path')
    if not argv[0] or not shipped_executable(argv[0]):
        raise ValueError('provider executable is outside the shipped install trees')
    return argv, {**safe_environment(env, file_values(SNAPSHOT)), **FIXED_ENV}, engine_port


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


def cell_main(data_root, *, execution=False, egress=False, engine=False):
    import resource

    # No RLIMIT_AS: Node/V8 reserves large virtual ranges. Discovery has the
    # mapper's fixed 35s deadline; execution has no wall clock, only CPU time
    # plus the mapper's external RSS/process-count guard.
    for limit, cap in ((resource.RLIMIT_CORE, 0), (resource.RLIMIT_CPU, 600 if execution else 30),
                       (resource.RLIMIT_NOFILE, 256), (resource.RLIMIT_NPROC, 64),
                       (resource.RLIMIT_FSIZE, 64 * 1024 * 1024)):
        resource.setrlimit(limit, (cap, cap))
    argv, env, engine_port = validate(read_config(), data_root, execution=execution)
    if (engine_port is not None) != bool(engine) or (engine and not egress):
        raise ValueError('provider engine route differs from its admitted socket')
    private = AUTH_DIR
    copy_snapshot(SNAPSHOT, private)
    def relocate(value):
        return private + value[len(SNAPSHOT):] if (
            value == SNAPSHOT or value.startswith(SNAPSHOT + '/')) else value
    argv = [relocate(value) for value in argv]
    env = {key: relocate(value) for key, value in env.items()}
    if execution:
        os.mkdir(WORKSPACE, 0o700)
    os.chdir(WORKSPACE if execution else private)
    if egress:
        from tinyassets.universe_egress import FORWARDER, PROXY_ENV

        env.update(PROXY_ENV)
        specs = ['3128=/provider-egress.sock']
        if engine:
            # The engine route's URL names 127.0.0.1:<port>; inside the cell
            # that port is this one pinned relay and nothing else.
            specs.append(f'{engine_port}={ENGINE_SOCKET}')
        argv = ['/opt/venv/bin/python', '-I', '-S', '-c', FORWARDER, *specs, '--', *argv]
    os.execve(argv[0], argv, env)
