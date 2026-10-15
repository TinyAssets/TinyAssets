"""Pure launch validation shared by the old cell and in-box CLI worker."""
from __future__ import annotations

import os
import re
import stat

MAX_CONFIG_BYTES = 64 * 1024
MAX_ARGS = 32
MAX_EXEC_ARGS = 128
MAX_ENV = 128
INSTALL_TREE = re.compile(r'/opt/[a-z0-9][a-z0-9.-]*-install/.+')
WRAPPER_DIR = '/usr/local/bin/'
SNAPSHOT = '/snapshot'
AUTH_DIR = '/tmp/provider-auth'
WORKSPACE = '/tmp/workspace'
ENGINE_SOCKET = '/provider-engine.sock'
SWITCH_VALUES = frozenset({'0', '1', 'true', 'false', 'yes', 'no', 'on', 'off'})
MAX_VALUE_FILE_BYTES = 8192
FIXED_ENV = {'PATH': '/usr/bin:/bin', 'HOME': '/tmp', 'USERPROFILE': '/tmp',
             'TMPDIR': '/tmp', 'TMP': '/tmp', 'TEMP': '/tmp', 'LANG': 'C.UTF-8'}


def file_values(directory):
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
    """Permit display settings, safe paths and the current sealed owner values."""
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
              or value == '/agent-home' or value.startswith('/agent-home/')
              or value == '/tmp' or value.startswith('/tmp/')):
            if not any(part in ('.', '..') for part in value.split('/')):
                allowed[key] = value
    return allowed


def read_config(fd=0):
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
    """Require a shipped, non-writable executable outside the payload UID."""
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


def resolve_executable(command):
    if '/' not in command and re.fullmatch(r'[A-Za-z0-9_.-]+', command):
        command = WRAPPER_DIR + command
    if not command or not shipped_executable(command):
        raise ValueError('provider executable is outside the shipped install trees')
    return command
