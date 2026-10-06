"""Runtime command-center admission (owner-dynamic-admission DA3/DA4).

Only while the bounded mapper client is installed. A new center root gets the
canonical migrated label ``1001:<machine>``, mode 0750, the canonical access
ACL and no default ACL, without any process gaining a capability: a fixed
``center-root`` owner cell makes a setgid directory ``g`` inside daemon-private
staging S, the daemon creates the root inside it (inheriting the owner GID and
the ACL), clears the default ACL and S_ISGID, and renames it into place.
"""
from __future__ import annotations

import ctypes
import errno
import json
import os
import re
import secrets
import stat
import struct

STAGING = '.role-admission'
ACCESS, DEFAULT = 'system.posix_acl_access', 'system.posix_acl_default'
_UNDEFINED = 0xFFFFFFFF
_RENAME_NOREPLACE = 1


class AdmissionRefused(RuntimeError):
    """A label, row or binding differs; nothing is guessed or published."""


def _acl(owner, named, *, mask, group=0, other=0):
    # Linux POSIX ACL xattr version 2, in the kernel's canonical entry order;
    # byte-identical to deploy/role_owner_migration._acl for these shapes.
    entries = [(1, owner, _UNDEFINED)]
    entries += [(2, rights, uid) for uid, rights in sorted(named.items())]
    entries += [(4, group, _UNDEFINED), (16, mask, _UNDEFINED), (32, other, _UNDEFINED)]
    return struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in entries)


def canonical_root_acl(machine: int) -> bytes:
    """``user::rwx user:<machine>:r-x user:1002:--x group::--- mask::r-x other::---``."""
    return _acl(7, {machine: 5, 1002: 1}, mask=5)


def canonical_label(machine: int) -> tuple:
    return (1001, machine, 0o750, canonical_root_acl(machine), None)


def _xattr(fd, name):
    try:
        return os.getxattr(fd, name)
    except OSError as exc:
        if exc.errno != errno.ENODATA:
            raise
        return None


def read_label(fd) -> tuple:
    """The full ``_permissions`` tuple: uid, gid, mode, access ACL, default ACL."""
    info = os.fstat(fd)
    return (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode),
            _xattr(fd, ACCESS), _xattr(fd, DEFAULT))


def _open_dir(name, dir_fd):
    return os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                   dir_fd=dir_fd)


def _renameat2(src_dir, src, dst_dir, dst):
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(src_dir, src.encode(), dst_dir, dst.encode(), _RENAME_NOREPLACE):
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code), dst)


def _staging(root_fd):
    """``<data>/.role-admission``: 1001:1001 0711, no ACL; created on first use.

    Search-only for others: bubblewrap resolves the cell's staging descriptor
    by path, so the owner must traverse here. Nobody else can list it, and each
    S is a 128-bit name whose ACL admits only the daemon and that one owner.
    """
    try:
        os.mkdir(STAGING, 0o711, dir_fd=root_fd)
    except FileExistsError:
        pass
    fd = _open_dir(STAGING, root_fd)
    if stat.S_IMODE(os.fstat(fd).st_mode) != 0o711:
        os.fchmod(fd, 0o711)  # mkdir applied the daemon umask
    if read_label(fd) != (1001, 1001, 0o711, None, None):
        os.close(fd)
        raise AdmissionRefused('admission staging is not daemon-private')
    return fd


def _remove_tree(parent, name):
    """Remove only this admission's own staging; never follows a link."""
    try:
        fd = _open_dir(name, parent)
    except FileNotFoundError:
        return
    try:
        for entry in os.listdir(fd):
            info = os.stat(entry, dir_fd=fd, follow_symlinks=False)
            if not stat.S_ISDIR(info.st_mode):
                os.unlink(entry, dir_fd=fd)
                continue
            try:
                os.rmdir(entry, dir_fd=fd)  # an empty owner directory needs no read
            except OSError as exc:
                if exc.errno != errno.ENOTEMPTY:
                    raise
                _remove_tree(fd, entry)
    finally:
        os.close(fd)
    os.rmdir(name, dir_fd=parent)


def _read_line(stream, bound=65536):
    raw = bytearray()
    while len(raw) <= bound:
        byte = stream.recv(1)
        if byte == b'\n':
            return json.loads(raw)
        if not byte:
            raise AdmissionRefused('center-root cell ended early')
        raw.extend(byte)
    raise AdmissionRefused('oversized center-root cell output')


def _handoff(client, staging_fd, *, principal, center, identity):
    """DA3 step 2: the fixed capability-free owner cell creates ``g``."""
    info = os.fstat(staging_fd)
    with client.start_cell(kind='center-root', principal=principal, command_center=center,
                           identity=identity, directory_fd=staging_fd) as cell:
        cell.stream.settimeout(40)
        proof = _read_line(cell.stream)['cell']
        inner = identity.uid - 300000
        if (proof.get('uid') != inner or proof.get('gid') != inner
                or proof.get('source') != [info.st_dev, info.st_ino]
                or proof.get('fds') != [0, 1, 2] or proof.get('groups') != []
                or proof.get('caps') != 'zero' or proof.get('nnp') != 1
                or proof.get('profile') != 'cell-deny'):
            raise AdmissionRefused('center-root cell proof is absent')
        made = _read_line(cell.stream)  # the cell's own namespace view of g
        if cell.wait(5) != 0 or made != {'g': [inner, inner, 0o2777]}:
            raise AdmissionRefused('center-root cell did not create its directory')


def label_root(root_fd, *, client, principal, center, identity):
    """DA3: label and publish ``<data>/<center>``; returns its (dev, ino).

    Refuses (and publishes nothing) on any shape or ACL difference, including
    a kernel that loses S_ISGID before the root is created.
    """
    from tinyassets.broker.owner_identities import validate_center, validate_principal

    validate_principal(principal)
    validate_center(center)
    machine = identity.gid
    if os.geteuid() != 1001 or identity.uid != machine:
        raise AdmissionRefused('center-root labelling runs only as the daemon')
    staging = _staging(root_fd)
    token = secrets.token_hex(16)
    published = None
    try:
        os.mkdir(token, 0o700, dir_fd=staging)
        s_fd = _open_dir(token, staging)
        try:
            # The daemon owns S, so neither write needs a capability. machine
            # only shapes S; the mapper resolves the owner itself (DA2).
            os.setxattr(s_fd, ACCESS, _acl(7, {machine: 7}, mask=7))
            os.setxattr(s_fd, DEFAULT, canonical_root_acl(machine))
            if (_xattr(s_fd, DEFAULT) != canonical_root_acl(machine)
                    or os.fstat(s_fd).st_mode & 0o007):
                raise AdmissionRefused('staging ACL readback differs')
            _handoff(client, s_fd, principal=principal, center=center, identity=identity)
            if os.listdir(s_fd) != ['g']:
                raise AdmissionRefused('center-root staging holds foreign entries')
            g_fd = _open_dir('g', s_fd)
            try:
                info = os.fstat(g_fd)
                if ((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                        machine, machine, 0o2777) or os.listdir(g_fd)):
                    raise AdmissionRefused('setgid hand-off directory has the wrong shape')
                os.mkdir('root', 0o750, dir_fd=g_fd)
                new = _open_dir('root', g_fd)
                try:
                    if _xattr(new, DEFAULT) is not None:
                        os.removexattr(new, DEFAULT)
                    # Requested mode has no S_ISGID: the inherited bit clears and
                    # the kernel keeps the named entries, mask r-x (D17).
                    os.fchmod(new, 0o750)
                    if read_label(new) != canonical_label(machine):
                        raise AdmissionRefused('center root label differs from canonical')
                    key = os.fstat(new)
                finally:
                    os.close(new)
                _renameat2(g_fd, 'root', root_fd, center)
                published = (key.st_dev, key.st_ino)
            finally:
                os.close(g_fd)
            os.rmdir('g', dir_fd=s_fd)
        finally:
            os.close(s_fd)
        os.rmdir(token, dir_fd=staging)
        os.fsync(staging)
    except BaseException:
        try:
            _remove_tree(staging, token)
        except OSError:
            pass  # startup removes all staging with writers stopped (DA4)
        raise
    finally:
        os.close(staging)
    _seed_entries(root_fd, center, published)
    os.fsync(root_fd)
    return published


def _seed_entries(root_fd, center, key):
    """Every entry forward migration creates in a center, explicit owner and mode."""
    fd = _open_dir(center, root_fd)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != key:
            raise AdmissionRefused('published root changed')
        try:
            os.mkdir('previews', 0o700, dir_fd=fd)
        except FileExistsError:
            pass
        previews = _open_dir('previews', fd)
        try:
            if read_label(previews) != (1001, 1001, 0o700, None, None):
                raise AdmissionRefused('previews readback differs')
            os.fsync(previews)
        finally:
            os.close(previews)
        os.fsync(fd)
    finally:
        os.close(fd)


def bounded_client():
    """The installed D69/D70 client, or None: every rule here is inert without it."""
    from tinyassets import role_decoder

    return role_decoder._bounded_client


def ensure_center_dir(udir) -> None:
    """A write site's ``udir.mkdir(parents=True, exist_ok=True)``.

    Legacy (no client): exactly that mkdir. With the bounded client installed a
    missing root refuses loudly: only admit_center creates one (DA4).
    """
    if bounded_client() is None:
        udir.mkdir(parents=True, exist_ok=True)
        return
    if not udir.is_dir():
        raise AdmissionRefused(f'command center {udir.name!r} has no admitted root')


def admit_center(data_root, *, principal, center):
    """DA4: reserve, label, publish, append ``admit``, bind; returns the generation.

    Idempotent for the same principal and center: a repeat resumes at the first
    unfinished step, and an already-bound center is a success. Nothing here
    removes a published root, and there is no legacy mkdir fallback.
    """
    from tinyassets.broker.owner_identities import center_admission, owner_identity

    client = bounded_client()
    if client is None:
        raise AdmissionRefused('runtime admission requires the bounded mapper client')
    identity = owner_identity(data_root, principal=principal, allocate=True)
    root_fd = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        key = published_root(root_fd, center, identity.gid)
        if key is None:
            key = label_root(root_fd, client=client, principal=principal, center=center,
                             identity=identity)
        else:
            _seed_entries(root_fd, center, key)
        generation, machine = center_admission(data_root, event='admit',
                                               principal=principal, center=center)
        if machine != identity.uid:
            raise AdmissionRefused('admission row names another machine')
        fd = os.open(center, os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC,
                     dir_fd=root_fd)
        try:
            # The mapper cannot see host uid 1001; this descriptor-bound check
            # is the daemon's assertion (D85), so it is made here, now.
            info = os.fstat(fd)
            if (info.st_dev, info.st_ino) != key or info.st_uid != 1001:
                raise AdmissionRefused('published root changed before the bind')
            client.admit(principal=principal, command_center=center,
                         generation=generation, root_fd=fd)
        finally:
            os.close(fd)
    finally:
        os.close(root_fd)
    return generation


def published_root(root_fd, center, machine):
    """An existing root with exactly the canonical label, else None if absent."""
    if not re.fullmatch('[A-Za-z0-9_-]{1,128}', center):
        raise AdmissionRefused('invalid command center name')
    try:
        fd = _open_dir(center, root_fd)
    except FileNotFoundError:
        return None
    try:
        if read_label(fd) != canonical_label(machine):
            raise AdmissionRefused('existing center root is not canonically labelled')
        info = os.fstat(fd)
        return info.st_dev, info.st_ino
    finally:
        os.close(fd)
