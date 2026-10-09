"""Runtime command-center admission (owner-dynamic-admission DA3/DA4).

Every center is created here, through the bounded mapper client. A new center root gets the
canonical migrated label ``1001:<machine>``, mode 0750, the canonical access
ACL and no default ACL, without any process gaining a capability: a fixed
``center-root`` owner cell makes a setgid directory ``g`` inside daemon-private
staging S, plus the owner-owned seed entries inside ``g``; the daemon creates
the root inside ``g`` (inheriting the owner GID), sets its canonical ACL, clears
the default ACL and S_ISGID, renames the seeds into it and renames it into place.

The root grants the owner r-x only, so the seeds are the entries an owner cell
may write (``role_tools.maintain``, the preview writer). Each seed inherits S's
default ACL, which is the migration's default for owner content: the daemon's
named rwx, so it can rename the seed and reach what is later written inside.
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
DAEMON_UID = 1001

#: Owner-owned 0770 directories in every center root: the tool workspace, the
#: preview output and ``universe_tools.AGENT_HARNESS_DIRS``. The center-root
#: cell (``deploy/role_decoder.SEED_ENTRIES``) makes exactly these.
SEED_ENTRIES = ('.agent-workspace', 'previews', 'skills', 'prompts', 'extensions',
                'workflows', 'bin', 'notes', 'wiki')


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


def seed_default_acl() -> bytes:
    """``user::rwx user:1001:rwx group::--- mask::rwx other::---``: the migration's
    default ACL for owner content (``deploy/role_migrate._acl(7, {1001: 7})``)."""
    return _acl(7, {DAEMON_UID: 7}, mask=7)


def seed_label(machine: int) -> tuple:
    """A seed as the cell leaves it: inherited from S's default ACL, mode 0770."""
    return (machine, machine, 0o770, seed_default_acl(), seed_default_acl())


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
        if cell.wait(5) != 0 or made != {'g': [inner, inner, 0o2777], 'seeds': {
                name: [inner, inner, 0o770] for name in SEED_ENTRIES}}:
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
            os.setxattr(s_fd, DEFAULT, seed_default_acl())
            if (_xattr(s_fd, DEFAULT) != seed_default_acl()
                    or os.fstat(s_fd).st_mode & 0o007):
                raise AdmissionRefused('staging ACL readback differs')
            _handoff(client, s_fd, principal=principal, center=center, identity=identity)
            if os.listdir(s_fd) != ['g']:
                raise AdmissionRefused('center-root staging holds foreign entries')
            g_fd = _open_dir('g', s_fd)
            try:
                info = os.fstat(g_fd)
                if ((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) != (
                        machine, machine, 0o2777)
                        or sorted(os.listdir(g_fd)) != sorted(SEED_ENTRIES)):
                    raise AdmissionRefused('setgid hand-off directory has the wrong shape')
                for name in SEED_ENTRIES:
                    seed = _open_dir(name, g_fd)
                    try:
                        if read_label(seed) != seed_label(machine) or os.listdir(seed):
                            raise AdmissionRefused(f'seed {name!r} has the wrong shape')
                    finally:
                        os.close(seed)
                os.mkdir('root', 0o750, dir_fd=g_fd)
                new = _open_dir('root', g_fd)
                try:
                    # The daemon owns the new root, so it writes the canonical
                    # ACL itself instead of inheriting the seeds' default.
                    os.setxattr(new, ACCESS, canonical_root_acl(machine))
                    if _xattr(new, DEFAULT) is not None:
                        os.removexattr(new, DEFAULT)
                    # Requested mode has no S_ISGID: the inherited bit clears and
                    # the kernel keeps the named entries, mask r-x (D17).
                    os.fchmod(new, 0o750)
                    if read_label(new) != canonical_label(machine):
                        raise AdmissionRefused('center root label differs from canonical')
                    key = os.fstat(new)
                    for name in SEED_ENTRIES:
                        _renameat2(g_fd, name, new, name)
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
    _seed_entries(root_fd, center, published, machine)
    os.fsync(root_fd)
    return published


def _seed_entries(root_fd, center, key, machine):
    """Every seed is in the published root and owned by its owner.

    The seeds move in before the root is published, so a published root
    without one was never made here: refuse rather than guess.
    """
    fd = _open_dir(center, root_fd)
    try:
        info = os.fstat(fd)
        if (info.st_dev, info.st_ino) != key:
            raise AdmissionRefused('published root changed')
        for name in SEED_ENTRIES:
            try:
                seed = _open_dir(name, fd)
            except FileNotFoundError:
                raise AdmissionRefused(f'published root lacks seed {name!r}') from None
            try:
                found = os.fstat(seed)
                if (found.st_uid, found.st_gid) != (machine, machine):
                    raise AdmissionRefused(f'seed {name!r} is not owned by its owner')
                os.fsync(seed)
            finally:
                os.close(seed)
        os.fsync(fd)
    finally:
        os.close(fd)


def bounded_client():
    """The installed D69/D70 client; admission refuses loudly without it."""
    from tinyassets import role_decoder

    client = role_decoder._bounded_client
    if client is None:
        raise AdmissionRefused('runtime admission requires the bounded mapper client')
    return client


def ensure_center_dir(udir) -> None:
    """A write site's guard: only admit_center creates a center root (DA4)."""
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
    identity = owner_identity(data_root, principal=principal, allocate=True)
    root_fd = os.open(data_root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        key = published_root(root_fd, center, identity.gid)
        if key is None:
            key = label_root(root_fd, client=client, principal=principal, center=center,
                             identity=identity)
        else:
            _seed_entries(root_fd, center, key, identity.gid)
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
