"""Legacy provider cell entry, confined before this module is imported."""
from __future__ import annotations

import json
import os
import re
import stat

from tinyassets.external_agents.launch_security import (
    AUTH_DIR,
    ENGINE_SOCKET,
    FIXED_ENV,
    MAX_ARGS,
    MAX_CONFIG_BYTES,
    MAX_ENV,
    MAX_EXEC_ARGS,
    SNAPSHOT,
    WORKSPACE,
    file_values,
    read_config,
    resolve_executable,
    safe_environment,
)
from tinyassets.external_agents.launch_security import (
    shipped_executable as shipped_executable,
)


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
    if any(data_root in item for item in (*argv, *env.values())):
        raise ValueError('provider config names a host data path')
    argv[0] = resolve_executable(argv[0])
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
