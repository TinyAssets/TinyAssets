"""Fixed read-only storage observation under the admitted owner identity."""
from __future__ import annotations

import os
import stat
import time
from pathlib import Path

from tinyassets.role_content import _frame, _owner_entry, _read

SCOPES = frozenset({'jail', 'universe', 'workspaces'})


def scan(root, scope, *, uid, timeout=20, max_entries=1000000):
    """Logical regular bytes; no follows, no file reads, bounded complete walk."""
    if scope not in SCOPES:
        raise ValueError('invalid storage scope')
    from tinyassets.jail_disk import _NOT_JAIL_WRITABLE
    from tinyassets.storage_accounting import _NOT_USER_BYTES
    from tinyassets.workspace_owner_pool import SCRATCH_DIR

    excluded = _NOT_USER_BYTES if scope == 'universe' else _NOT_JAIL_WRITABLE
    deadline = time.monotonic() + timeout
    device = os.fstat(root).st_dev
    seen = set()
    visited = total = 0

    def walk(parent, depth):
        nonlocal visited, total
        if depth > 64:
            raise OSError('storage traversal depth exceeded')
        with os.scandir(parent) as entries:
            for entry in entries:
                visited += 1
                if visited > max_entries or time.monotonic() >= deadline:
                    raise OSError('storage traversal bound exceeded')
                if depth == 0 and (not _owner_entry(entry.name)
                        or (scope != 'workspaces' and entry.name in excluded)
                        or (scope == 'workspaces' and entry.name != 'workspaces')):
                    continue
                if scope == 'workspaces' and depth == 1 and entry.name == SCRATCH_DIR:
                    continue
                try:
                    info = os.stat(entry.name, dir_fd=parent, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if stat.S_ISLNK(info.st_mode):
                    continue
                if info.st_dev != device or (info.st_uid, info.st_gid) != (uid, uid):
                    raise PermissionError('storage entry is not admitted owner content')
                if stat.S_ISDIR(info.st_mode):
                    try:
                        child = os.open(entry.name, os.O_RDONLY | os.O_DIRECTORY
                                        | os.O_NOFOLLOW, dir_fd=parent)
                    except FileNotFoundError:
                        continue
                    try:
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise OSError('storage directory changed')
                        walk(child, depth + 1)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    key = (info.st_dev, info.st_ino)
                    if key not in seen:
                        seen.add(key)
                        total += info.st_size
    walk(root, 0)
    return total


def measure(center, scope):
    from tinyassets import role_center_admission as admission
    from tinyassets import role_decoder
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.storage import data_dir
    from tinyassets.universe_owner import owner_of
    from tinyassets.workspace_fs import open_dir_nofollow

    center = Path(center).absolute()
    if scope not in SCOPES or center.parent != Path(data_dir()).resolve():
        raise PermissionError('storage center is outside the admitted data root')
    supervisor._protect_daemon()
    client = role_decoder._bounded_client
    if client is None:
        raise PermissionError('storage requires its bounded owner launcher')
    principal = owner_of(center.parent, center.name)
    if principal is None:
        raise PermissionError('storage center has no recorded owner')
    identity = owner_identity(center.parent, principal=principal)
    fd = open_dir_nofollow(center)
    try:
        if admission.read_label(fd) != admission.canonical_label(identity.uid):
            raise PermissionError('storage root differs from its recorded owner')
        info = os.fstat(fd)
        with client.start_cell(kind='owner-measure', principal=principal,
                command_center=center.name, identity=identity, directory_fd=fd) as cell:
            cell.stream.settimeout(30)
            with cell.stream.makefile('rb') as reader:
                proof = _read(reader, 16384)['cell']
                inner = identity.uid - 300000
                if (proof.get('uid') != inner or proof.get('gid') != inner
                        or proof.get('source') != [info.st_dev, info.st_ino]
                        or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                        or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                        or proof.get('profile') != 'cell-deny'):
                    raise OSError('storage cell proof is absent')
                cell.stream.sendall(_frame({'scope': scope}))
                result = _read(reader, 1024)
                if (set(result) != {'bytes'} or type(result['bytes']) is not int
                        or not 0 <= result['bytes'] <= 2**63 - 1 or cell.wait(5) != 0):
                    raise OSError('storage cell observation is incomplete')
                return result['bytes']
    finally:
        os.close(fd)


def cell_main():
    import sys

    request = _read(sys.stdin.buffer, 1024)
    if set(request) != {'scope'} or request['scope'] not in SCOPES:
        raise ValueError('invalid storage observation request')
    fd = os.open('/workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        total = scan(fd, request['scope'], uid=os.getuid())
    finally:
        os.close(fd)
    sys.stdout.buffer.write(_frame({'bytes': total}))
    sys.stdout.buffer.flush()
