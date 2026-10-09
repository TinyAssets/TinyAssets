"""Owner-created content, published by the daemon without changing root authority."""
from __future__ import annotations

import hashlib
import json
import os
import secrets
import stat
from contextlib import contextmanager
from pathlib import Path

from tinyassets import role_center_admission as admission
from tinyassets.workspace_fs import open_dir_nofollow

STAGING = admission.STAGING
CHUNK_BYTES = 1024 * 1024
MAX_PARENTS = 64
FRAME_BOUND = 4096
PLATFORM_VISIBLE = frozenset({'owner.json', 'provider_definitions.json'})


def _owner_entry(name):
    # Mirrors migration's OWNER_HIDDEN and visible vault-metadata exception.
    return name == '.agent-workspace' or (not name.startswith('.')
                                         and name not in PLATFORM_VISIBLE)


def _frame(value):
    raw = json.dumps(value, separators=(',', ':')).encode() + b'\n'
    if len(raw) > FRAME_BOUND:
        raise ValueError('owner content exceeds frame bound')
    return raw


def _read(stream, bound):
    raw = stream.readline(bound + 1)
    if len(raw) > bound or not raw.endswith(b'\n'):
        raise RuntimeError('invalid owner content frame')
    return json.loads(raw)


def _directory(parent, name):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=parent)


def locate(root: Path, parts: list[str]):
    """Find a structurally admitted center, never infer it from caller identity."""
    from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST
    from tinyassets.storage import data_dir

    configured = Path(data_dir()).absolute()
    target = root.absolute().joinpath(*parts)
    for base in (configured, configured.resolve()):
        try:
            relative = target.relative_to(base).parts
        except ValueError:
            continue
        break
    else:
        return None
    if len(relative) < 2 or relative[0].startswith('.'):
        return None
    center = base / relative[0]
    try:
        fd = open_dir_nofollow(center)
    except FileNotFoundError:
        return None  # the generic writer separately refuses unadmitted roots
    try:
        info = os.fstat(fd)
        if not OWNER_ID_FIRST <= info.st_gid <= OWNER_ID_LAST:
            return None  # not an owner-labelled center (global data/test fixture)
        if admission.read_label(fd) != admission.canonical_label(info.st_gid):
            raise PermissionError('owner content root has a noncanonical label')
    finally:
        os.close(fd)
    if not _owner_entry(relative[1]):
        return None
    return center, list(relative[1:])


@contextmanager
def _staging(base, machine):
    """A private daemon-owned name, writable only by its selected owner cell."""
    root = open_dir_nofollow(base)
    stage = private = None
    token = secrets.token_hex(16)
    try:
        try:
            os.mkdir(STAGING, 0o711, dir_fd=root)
        except FileExistsError:
            pass
        stage = _directory(root, STAGING)
        # This root is daemon-owned, never an owner-writable parent.
        info = os.fstat(stage)
        if (info.st_uid, info.st_gid) != (1001, 1001):
            raise PermissionError('owner content staging has a foreign owner')
        os.fchmod(stage, 0o711)
        if admission.read_label(stage) != (1001, 1001, 0o711, None, None):
            raise PermissionError('owner content staging is not private')
        os.mkdir(token, 0o700, dir_fd=stage)
        private = _directory(stage, token)
        os.setxattr(private, admission.ACCESS, admission._acl(7, {machine: 7}, mask=7))
        os.setxattr(private, admission.DEFAULT, admission.seed_default_acl())
        yield private
    finally:
        if private is not None:
            os.close(private)
            admission._remove_tree(stage, token)
        if stage is not None:
            os.close(stage)
        os.close(root)


def _materialize(client, staging, *, principal, center, identity, data, directories):
    info = os.fstat(staging)
    with client.start_cell(kind='owner-content', principal=principal,
            command_center=center.name, identity=identity, directory_fd=staging) as cell:
        cell.stream.settimeout(60)
        with cell.stream.makefile('rb') as reader:
            proof = _read(reader, 16384)['cell']
            inner = identity.uid - 300000
            if (proof.get('uid') != inner or proof.get('gid') != inner
                    or proof.get('source') != [info.st_dev, info.st_ino]
                    or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                    or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                    or proof.get('profile') != 'cell-deny'):
                raise RuntimeError('owner content proof is absent')
            size = 0 if data is None else len(data)
            cell.stream.sendall(_frame({'size': size, 'directories': directories,
                                       'file': data is not None}))
            if data is not None:
                payload = memoryview(data)
                for offset in range(0, size, CHUNK_BYTES):
                    cell.stream.sendall(payload[offset:offset + CHUNK_BYTES])
            if _read(reader, 1024) != {'written': size} or cell.wait(5) != 0:
                raise RuntimeError('owner content cell did not complete')
    # Revoke owner traversal before inspecting or publishing outputs. No owner
    # cell keeps descriptors alive after the launcher has reaped this job.
    os.setxattr(staging, admission.ACCESS, admission._acl(7, {}, mask=0))
    names = ([] if data is None else ['file']) + [f'dir-{i}' for i in range(directories)]
    if sorted(os.listdir(staging)) != sorted(names):
        raise PermissionError('owner content output inventory differs')
    for name in names:
        directory = name != 'file'
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
                     | (os.O_DIRECTORY if directory else 0), dir_fd=staging)
        try:
            info = os.fstat(fd)
            label = admission.read_label(fd)
            expected = (admission.seed_label(identity.uid) if directory else
                        (identity.uid, identity.gid, 0o660,
                         admission._acl(6, {1001: 7}, mask=6), None))
            if (label != expected or (directory and os.listdir(fd))
                    or (not directory and (not stat.S_ISREG(info.st_mode)
                        or info.st_nlink != 1 or info.st_size != len(data)))):
                raise PermissionError('owner content output custody differs')
            if not directory:
                digest = hashlib.sha256()
                while chunk := os.read(fd, CHUNK_BYTES):
                    digest.update(chunk)
                if digest.digest() != hashlib.sha256(data).digest():
                    raise PermissionError('owner content bytes differ from publication request')
        finally:
            os.close(fd)


def _check_owner_directory(fd, machine):
    info = os.fstat(fd)
    if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (machine, machine):
        raise PermissionError('content parent is not an owner directory')


def _append(parent, name, data, machine):
    """Append to one pinned existing owner inode; never follow or create."""
    fd = os.open(name, os.O_WRONLY | os.O_APPEND | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=parent)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or (info.st_uid, info.st_gid) != (machine, machine)):
            raise PermissionError('append target is not exclusive owner content')
        from tinyassets.universe_files import _write_all
        _write_all(fd, data)
        os.fsync(fd)
    finally:
        os.close(fd)


def write(center, parts, data, *, make_parents=True, mode='replace'):
    """The sole daemon file publication operation for admitted visible content."""
    if mode not in ('replace', 'exclusive', 'append') or not isinstance(data, bytes):
        raise ValueError('invalid owner content write')
    _publish(center, parts, data, make_parents=make_parents, mode=mode)


def ensure_directories(center, parts, *, machine):
    """Publish only missing owner directories; never replace existing trees."""
    if type(machine) is not int:
        raise ValueError('owner directory identity must be an integer')
    _publish(center, parts, None, make_parents=True, mode='directories',
             expected_machine=machine)


def _publish(center, parts, data, *, make_parents, mode, expected_machine=None):
    """Create owner inodes in one cell and publish names from pinned parents."""
    from tinyassets import role_decoder
    from tinyassets.broker import supervisor
    from tinyassets.broker.owner_identities import owner_identity
    from tinyassets.universe_owner import owner_of

    if (not 1 <= len(parts) <= MAX_PARENTS + (mode != 'directories')
            or any(not part or part in ('.', '..') or '/' in part or '\\' in part
                   or '\0' in part for part in parts)
            or not _owner_entry(parts[0])):
        raise ValueError('invalid owner content write')
    supervisor._protect_daemon()
    client = role_decoder._bounded_client
    if client is None:
        raise RuntimeError('owner content requires its bounded owner launcher')
    principal = owner_of(center.parent, center.name)
    if principal is None:
        raise PermissionError('owner content center has no recorded owner')
    identity = owner_identity(center.parent, principal=principal)
    if expected_machine is not None and identity.uid != expected_machine:
        raise PermissionError('owner directory identity differs from its recorded owner')
    parent = open_dir_nofollow(center)
    try:
        if admission.read_label(parent) != admission.canonical_label(identity.uid):
            raise PermissionError('owner content root differs from its recorded owner')
        # Prepare all potentially missing parents once. Publish each empty
        # directory exclusively; a concurrent creator wins without losing data.
        directories = parts if mode == 'directories' else parts[:-1]
        with _staging(center.parent, identity.uid) as staging:
            _materialize(client, staging, principal=principal, center=center,
                         identity=identity, data=data, directories=len(directories))
            for index, name in enumerate(directories):
                try:
                    child = _directory(parent, name)
                except FileNotFoundError:
                    if not make_parents or (index == 0 and name == '.agent-workspace'):
                        raise
                    try:
                        admission._renameat2(staging, f'dir-{index}', parent, name)
                        os.fsync(parent)
                    except FileExistsError:
                        pass
                    child = _directory(parent, name)
                try:
                    _check_owner_directory(child, identity.uid)
                except BaseException:
                    os.close(child)
                    raise
                os.close(parent)
                parent = child
            if mode == 'directories':
                return
            name = parts[-1]
            if mode == 'replace':
                os.replace('file', name, src_dir_fd=staging, dst_dir_fd=parent)
            elif mode == 'exclusive':
                admission._renameat2(staging, 'file', parent, name)
            else:
                try:
                    admission._renameat2(staging, 'file', parent, name)
                except FileExistsError:
                    _append(parent, name, data, identity.uid)
            os.fsync(parent)
    finally:
        os.close(parent)


def _create_outputs(root, stream, *, size, directories, file):
    """Write only numbered directories and the explicitly requested file."""
    for index in range(directories):
        os.mkdir(f'dir-{index}', 0o770, dir_fd=root)
    if file:
        fd = os.open('file', os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                     0o660, dir_fd=root)
        try:
            from tinyassets.universe_files import _write_all
            remaining = size
            while remaining:
                chunk = stream.read(min(CHUNK_BYTES, remaining))
                if not chunk:
                    raise RuntimeError('owner content ended before its declared size')
                _write_all(fd, chunk)
                remaining -= len(chunk)
            os.fsync(fd)
        finally:
            os.close(fd)
    os.fsync(root)


def cell_main():
    """Fixed numbered outputs only; no caller-selected paths or executables."""
    import resource
    import sys

    request = _read(sys.stdin.buffer, FRAME_BOUND)
    if (set(request) != {'size', 'directories', 'file'}
            or type(request['file']) is not bool or type(request['size']) is not int
            or not 0 <= request['size'] <= (1 << 63) - 1
            or type(request['directories']) is not int
            or not 0 <= request['directories'] <= MAX_PARENTS
            or (not request['file'] and (request['size'] != 0 or not request['directories']))):
        raise ValueError('invalid owner content request')
    # Exact declared bytes bound disk writes; memory stays at one chunk. The
    # generic writer gains no new file-size ceiling or truncation behavior.
    size = request['size']
    resource.setrlimit(resource.RLIMIT_FSIZE, (size, size))
    root = open_dir_nofollow('/workspace')
    try:
        if os.listdir(root):
            raise PermissionError('owner content staging is not empty')
        _create_outputs(root, sys.stdin.buffer, size=size,
                        directories=request['directories'], file=request['file'])
    finally:
        os.close(root)
    sys.stdout.buffer.write(_frame({'written': size}))
    sys.stdout.buffer.flush()


def promote_brains(center, root_fd, identity, result, *, agent_id):
    """Publish bounded, pinned owner bytes; keep workspace originals on failure."""
    from tinyassets.universe_tools import AGENT_BRAIN_FILES

    pending = result.pop('pending')
    allowed = set(AGENT_BRAIN_FILES) - ({'identity.md'} if agent_id != 'main' else set())
    if (type(pending) is not list or len(pending) > len(allowed)
            or any(type(name) is not str or name not in allowed for name in pending)
            or len(set(pending)) != len(pending)):
        raise RuntimeError('invalid brain publication candidates')
    workspace = _directory(root_fd, '.agent-workspace')
    try:
        _check_owner_directory(workspace, identity.uid)
        for name in pending:
            try:
                source = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                 dir_fd=workspace)
            except OSError:
                result['skipped'].append(name)
                continue
            try:
                info = os.fstat(source)
                if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                        or (info.st_uid, info.st_gid) != (identity.uid, identity.gid)
                        or info.st_size > 1024 * 1024):
                    result['skipped'].append(name)
                    continue
                chunks = []
                size = 0
                while chunk := os.read(source, min(65536, 1024 * 1024 + 1 - size)):
                    chunks.append(chunk)
                    size += len(chunk)
                    if size > 1024 * 1024:
                        break
                after = os.fstat(source)
                if (size > 1024 * 1024 or after.st_nlink != 1
                        or (after.st_size, after.st_mtime_ns, after.st_ctime_ns)
                        != (info.st_size, info.st_mtime_ns, info.st_ctime_ns)):
                    result['skipped'].append(name)
                    continue
                data = b''.join(chunks)
            finally:
                os.close(source)
            try:
                write(center, [name], data, mode='exclusive')
            except FileExistsError:
                continue
            result['promoted'].append(name)
    finally:
        os.close(workspace)
