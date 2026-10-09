"""The filesystem side of the workspace sink: the real
:class:`~tinyassets.workspace_pool.PoolFilesystem`, and the no-follow directory
handles both sink paths hold while they touch a workspace.

It lives outside ``workspace_pool`` on purpose. That module computes paths and
never touches the filesystem, which its tests assert by reading its source; the
bytes move here, where the no-follow rules are one small reviewable place.

**Nothing here trusts a path twice.** On POSIX a path is resolved once, component
by component, from an already-open ancestor descriptor, and every later step goes
through descriptors: ``mkdir``/``rename``/``unlink``/``rmdir`` all take
``dir_fd``, and directories are listed through an open handle. A path-based call
after a path-based check is a TOCTOU by construction - the name can be re-pointed
in between - so there are none on that branch.

Windows has no ``O_NOFOLLOW`` and no ``dir_fd``, so it keeps the path-based
implementation behind a platform branch: deletion walks with ``scandir`` and
refuses to descend into a symlink OR a junction. That distinction matters -
``os.walk`` would descend into a junction, which any unprivileged user can
create inside a lease.

The descriptor helpers are POSIX only; on Windows they raise
``NotImplementedError`` rather than imitate a guarantee they cannot make.
"""

from __future__ import annotations

import errno
import math
import os
import stat
import time
from pathlib import Path
from typing import Callable

#: Present on POSIX only. The zero fallbacks are never reached: every function
#: that would use them refuses on a non-POSIX host first.
_O_DIRECTORY = getattr(os, "O_DIRECTORY", 0)
_O_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)
_O_NONBLOCK = getattr(os, "O_NONBLOCK", 0)

#: True when this host can do openat-style work at all.
_POSIX = os.name == "posix" and os.open in os.supports_dir_fd

_COPY_CHUNK = 1024 * 1024
_LEASE_DIR_MODE = 0o700
_COPY_DEST_MODE = 0o600
#: A lease directory's name must be unguessable: the parent is shared, and an
#: attacker who can predict the name can create it first. 16 hex chars is 64
#: bits, which is what ``secrets.token_hex(8)`` and up produce.
MIN_LEASE_NAME_CHARS = 16
_HEX = frozenset("0123456789abcdefABCDEF")
#: A workspace tree deeper than this is not a repository, and unbounded
#: recursion through descriptors is a stack overflow waiting for a fixture.
_MAX_TREE_DEPTH = 64


class UnsafePoolPath(OSError):
    """A path was refused before it was used: a link where a real directory or
    file was required, a traversal, a file larger than the caller's bound, or a
    directory that is not the one this call created.

    An ``OSError`` so that a caller which already treats filesystem failure as
    failure - the outbox processor marking a lease ``LOST`` - keeps working
    without a second except clause.
    """


def read_package_manifest(descriptor, *, owner_uid, max_bytes):
    """The one fixed daemon-custody leaf; no owner-reader identity override."""
    root = os.fstat(descriptor)
    if root.st_uid != owner_uid or root.st_mode & 0o022 or not stat.S_ISDIR(root.st_mode):
        raise UnsafePoolPath('package directory custody is invalid')
    fd = os.open('manifest.json', os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                 dir_fd=descriptor)
    try:
        info = os.fstat(fd)
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != owner_uid
                or info.st_mode & 0o022 or info.st_size > max_bytes):
            raise UnsafePoolPath('package manifest custody is invalid')
        data = bytearray()
        while chunk := os.read(fd, min(65536, max_bytes + 1 - len(data))):
            data.extend(chunk)
            if len(data) > max_bytes:
                raise UnsafePoolPath('package manifest exceeds its bound')
        return bytes(data)
    finally:
        os.close(fd)


def verify_package_tree(descriptor, files, *, owner_uid, max_bytes):
    """Verify a daemon-sealed package through pinned no-follow descriptors.

    Every inode must be owned by the declared custody UID, unwritable by group
    or others, and regular files must have one link. No unmanifested entry can
    supply code to an interpreter's import or native-loader search.
    """
    import hashlib

    remaining = max_bytes
    found = set()

    def walk(parent, prefix='', depth=0):
        nonlocal remaining
        info = os.fstat(parent)
        if (depth > 32 or info.st_uid != owner_uid or info.st_mode & 0o022
                or not stat.S_ISDIR(info.st_mode)):
            raise UnsafePoolPath('package directory custody is invalid')
        with os.scandir(parent) as entries:
            for entry in entries:
                name = prefix + entry.name
                fd = os.open(entry.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                             dir_fd=parent)
                try:
                    info = os.fstat(fd)
                    if info.st_uid != owner_uid or info.st_mode & 0o022:
                        raise UnsafePoolPath('package inode custody is invalid')
                    if stat.S_ISDIR(info.st_mode):
                        if not any(path.startswith(name + '/') for path in files):
                            raise UnsafePoolPath('unmanifested package directory')
                        walk(fd, name + '/', depth + 1)
                    else:
                        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                                or (name != 'manifest.json' and name not in files)):
                            raise UnsafePoolPath('unmanifested or aliased package file')
                        if name == 'manifest.json':
                            continue
                        found.add(name)
                        digest = hashlib.sha256()
                        while data := os.read(fd, min(65536, max(1, remaining + 1))):
                            remaining -= len(data)
                            if remaining < 0:
                                raise UnsafePoolPath('package content exceeds its bound')
                            digest.update(data)
                        if digest.hexdigest() != files[name]:
                            raise UnsafePoolPath('package content revision mismatch')
                finally:
                    os.close(fd)

    walk(descriptor)
    if found != set(files):
        raise UnsafePoolPath('package content is incomplete')


def _is_link(path: str) -> bool:
    """A symlink or a Windows junction: something whose target is elsewhere."""
    if os.path.islink(path):
        return True
    isjunction = getattr(os.path, "isjunction", None)
    if isjunction is None:
        return False
    try:
        return bool(isjunction(path))
    except OSError:
        return False


def _unlink_link(path: str) -> None:
    """Remove the link itself. Windows needs ``rmdir`` for a directory link."""
    try:
        os.unlink(path)
    except OSError:
        os.rmdir(path)


#: Windows deletes lose to a process that is still exiting: 32 is a sharing
#: violation, 5 is access denied (the read-only bit, or a handle), and 145 is
#: "directory not empty", which here means a child reappeared under us while a
#: git child was closing. None of these is a permanent state, and the lease
#: going LOST for one keeps its bytes charged forever. POSIX has no equivalent:
#: an open file unlinks fine there, which is why this is the Windows branch
#: only.
WINDOWS_TRANSIENT_WINERRORS = frozenset({5, 32, 145})
#: How long a wipe keeps trying, and how often. Three seconds is longer than a
#: git child takes to exit and far shorter than the sweep's own patience.
WINDOWS_RETRY_TOTAL_S = 3.0
WINDOWS_RETRY_STEP_S = 0.05


def _is_transient_windows_error(exc: OSError) -> bool:
    return getattr(exc, "winerror", None) in WINDOWS_TRANSIENT_WINERRORS


def _retry_transient_windows(
    action: Callable[[], None],
    path: str,
    *,
    total_s: float,
    step_s: float,
    sleep: Callable[[float], None],
    monotonic: Callable[[], float] = time.monotonic,
) -> None:
    """Run ``action`` until it stops failing for a reason that passes.

    The read-only bit is re-cleared before every retry, not once: a git child
    that is still writing can set it again between attempts.
    """
    deadline = monotonic() + max(0.0, total_s)
    while True:
        try:
            action()
            return
        except FileNotFoundError:
            return
        except OSError as exc:
            if not _is_transient_windows_error(exc) or monotonic() >= deadline:
                raise
        try:
            os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
        except OSError:
            pass
        sleep(step_s)


def _unlink_windows(path: str) -> None:
    """Unlink, clearing the read-only attribute first when that is the refusal.

    Git marks everything under ``.git/objects`` read-only, and on Windows -
    unlike POSIX, where the containing directory's permission is what counts -
    unlinking a read-only file raises PermissionError. Every wipe of a real
    checkout therefore failed and the lease went LOST with its bytes still
    charged (found by the end-to-end chain test, 2026-08-30). The chmod is
    scoped to the retry: a failure for any other reason still propagates.
    """
    try:
        os.unlink(path)
        return
    except PermissionError:
        pass
    os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    os.unlink(path)


# --------------------------------------------------------------------------
# no-follow directory handles (POSIX)
# --------------------------------------------------------------------------


def _require_posix(what: str) -> None:
    """Refuse loudly rather than pretend. A Windows fallback here would be a
    path-based imitation of a descriptor-based guarantee, which is the bug this
    module exists to prevent."""
    if not _POSIX:
        raise NotImplementedError(
            f"{what} needs POSIX openat semantics (O_NOFOLLOW + dir_fd); "
            f"this host is {os.name!r}. The workspace sink runs on Linux; "
            "on Windows there is no safe equivalent, so there is no fallback."
        )


def _require_component(name: str) -> str:
    """One path component, never a traversal and never a separator."""
    if not isinstance(name, str) or not name:
        raise UnsafePoolPath(f"path component must be a non-empty string, got {name!r}")
    if name in (".", ".."):
        raise UnsafePoolPath(f"path component {name!r} is a traversal")
    if "/" in name or "\\" in name or "\0" in name:
        raise UnsafePoolPath(f"path component {name!r} contains a separator")
    return name


def _split_relpath(relpath: str | Path) -> list[str]:
    """Split a relative path into safe components, refusing anything that could
    leave the directory the descriptor names."""
    raw = str(relpath)
    if not raw:
        raise UnsafePoolPath("relpath is empty")
    if raw.startswith("/") or raw.startswith("\\") or os.path.isabs(raw):
        raise UnsafePoolPath(f"relpath {raw!r} is absolute; it must be relative to the handle")
    parts = [part for part in raw.replace("\\", "/").split("/")]
    if any(part == "" for part in parts):
        raise UnsafePoolPath(f"relpath {raw!r} has an empty component")
    return [_require_component(part) for part in parts]


def _open_child_dir(parent_fd: int, name: str) -> int:
    """``openat`` one directory component, refusing a symlink at that step."""
    try:
        return os.open(name, os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=parent_fd)
    except OSError as exc:
        if exc.errno in (errno.ELOOP, errno.ENOTDIR):
            raise UnsafePoolPath(
                f"{name!r} is a link or not a directory; a workspace path is "
                "resolved without following links"
            ) from exc
        raise


def _open_leaf(parent_fd: int, name: str) -> int:
    """``openat`` the final component for reading, never following a link."""
    try:
        return os.open(
            name, os.O_RDONLY | _O_NOFOLLOW | _O_NONBLOCK, dir_fd=parent_fd
        )
    except OSError as exc:
        if exc.errno == errno.ELOOP:
            raise UnsafePoolPath(
                f"{name!r} is a symlink; a workspace file is read through the "
                "descriptor of the file itself"
            ) from exc
        raise


def open_dir_nofollow(path: str | Path) -> int:
    """Open ``path`` as a directory with no component allowed to be a symlink.

    Resolution walks from the root, opening each component with ``dir_fd`` and
    ``O_NOFOLLOW``, never a single ``os.open`` on the whole path: one ``os.open``
    would only refuse a symlink at the LAST component, and a swapped parent is
    exactly the attack. The caller owns the returned descriptor and closes it.
    """
    _require_posix("open_dir_nofollow")
    raw = str(path)
    if not os.path.isabs(raw):
        raise UnsafePoolPath(f"{raw!r} must be absolute: resolution starts at the root")
    parts = [part for part in raw.replace("\\", "/").split("/") if part]
    current = os.open("/", os.O_RDONLY | _O_DIRECTORY)
    try:
        for part in parts:
            _require_component(part)
            child = _open_child_dir(current, part)
            os.close(current)
            current = child
    except BaseException:
        os.close(current)
        raise
    return current


def open_subdir_nofollow(parent_fd: int, name: str) -> int:
    """Open ONE existing child directory through a handle the caller holds.

    The public name for what the sink actually needs: descend exactly one
    level, from a descriptor, without following a link. Not
    :func:`open_dir_nofollow`, which starts at the root and walks a whole
    absolute path - a caller that already holds the parent open would be
    re-resolving every component above it, which is the window the handle
    exists to close.

    Refuses a name that is not a single safe component, a link or a
    non-directory at that step (``UnsafePoolPath``), and anything at all
    off-POSIX. The descriptor is the caller's to close.
    """
    _require_posix("open_subdir_nofollow")
    _require_component(name)
    fd = _open_child_dir(parent_fd, name)
    try:
        # O_DIRECTORY already refuses a non-directory on Linux; the fstat is
        # what makes that a promise of THIS function rather than of the flag.
        if not stat.S_ISDIR(os.fstat(fd).st_mode):
            raise UnsafePoolPath(f"{name!r} is not a directory")
    except BaseException:
        os.close(fd)
        raise
    return fd


def measure_tree_beneath(
    root_fd: int, *, max_bytes: int, timeout_s: float = 1.0,
) -> int:
    """Bounded-work, no-follow storage sample through an already held root.

    Return measured bytes, or max_bytes + 1 as soon as the bound is exceeded.
    Count the greater of apparent and allocated size for each entry (hard links
    may be conservatively counted twice); count symlinks themselves, NEVER their
    targets. Directory handles and scan iterators are bounded by the existing
    tree-depth limit and closed on every failure. No file contents are opened.

    This is a best-effort live sample, not a kernel quota or an atomic snapshot.
    Deleted entries can disappear during a scan; replaced directories refuse.
    Iteration has a monotonic deadline, not a promise to interrupt a blocked
    filesystem syscall. Call again after all writers have exited before accepting
    installation. Unknown/incomplete samples raise a fixed error, never zero.
    """
    _require_posix("measure_tree_beneath")
    if type(root_fd) is not int or root_fd < 0:
        raise ValueError("storage measurement requires a held directory descriptor")
    if type(max_bytes) is not int or max_bytes < 0:
        raise ValueError("storage measurement bound must be a nonnegative integer")
    if (isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float))
            or not math.isfinite(timeout_s) or timeout_s <= 0):
        raise ValueError("storage measurement timeout must be positive and finite")
    deadline = time.monotonic() + timeout_s
    total = 0
    device = None

    def check_time():
        if time.monotonic() >= deadline:
            raise UnsafePoolPath("storage measurement deadline exceeded")

    def add(info):
        nonlocal total
        size = max(info.st_size, getattr(info, "st_blocks", 0) * 512)
        if size < 0:
            raise UnsafePoolPath("storage measurement returned invalid size")
        total += size

    def scan(fd, depth):
        check_time()
        if depth > _MAX_TREE_DEPTH:
            raise UnsafePoolPath("storage measurement depth exceeded")
        with os.scandir(fd) as entries:
            for entry in entries:
                check_time()
                try:
                    info = os.stat(entry.name, dir_fd=fd, follow_symlinks=False)
                except FileNotFoundError:
                    continue
                if info.st_dev != device:
                    raise UnsafePoolPath("storage measurement crossed a filesystem")
                add(info)
                if total > max_bytes:
                    return
                if stat.S_ISDIR(info.st_mode):
                    child = None
                    try:
                        check_time()
                        try:
                            child = _open_child_dir(fd, entry.name)
                        except FileNotFoundError:
                            continue
                        except OSError:
                            raise UnsafePoolPath(
                                "storage measurement directory unavailable"
                            ) from None
                        opened = os.fstat(child)
                        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
                            raise UnsafePoolPath("storage measurement directory changed")
                        scan(child, depth + 1)
                    except FileNotFoundError:
                        continue  # A vanished subtree will be covered by the final stable scan.
                    finally:
                        if child is not None:
                            os.close(child)
                    if total > max_bytes:
                        return

    owned = None
    try:
        check_time()
        original = os.fstat(root_fd)
        if not stat.S_ISDIR(original.st_mode):
            raise UnsafePoolPath("storage measurement root is not a directory")
        # A new open-file description, not dup(): scandir advances directory
        # offsets, and sharing one would make the next sample miss everything.
        owned = os.open(".", os.O_RDONLY | _O_DIRECTORY | _O_NOFOLLOW, dir_fd=root_fd)
        info = os.fstat(owned)
        if (info.st_dev, info.st_ino) != (original.st_dev, original.st_ino):
            raise UnsafePoolPath("storage measurement root changed")
        device = info.st_dev
        add(info)
        if total <= max_bytes:
            scan(owned, 0)
        check_time()
        return min(total, max_bytes + 1)
    except UnsafePoolPath:
        raise
    except OSError:
        # Paths may contain private names; no raw filesystem exception escapes.
        raise UnsafePoolPath("storage measurement unavailable") from None
    finally:
        if owned is not None:
            os.close(owned)


def _open_dir_making_one_level(path: Path) -> int:
    """Open ``path``, creating that ONE last component if it is missing.

    The quarantine directory is the only thing the processor may have to create,
    and it creates it through its parent's descriptor - ``mkdir(name,
    dir_fd=...)``, never ``makedirs`` on a string. A missing grandparent is an
    error, not something to conjure: the pool root is the caller's to make.
    """
    parent_fd = open_dir_nofollow(path.parent)
    try:
        name = _require_component(path.name)
        try:
            os.mkdir(name, _LEASE_DIR_MODE, dir_fd=parent_fd)
        except FileExistsError:
            pass
        return _open_child_dir(parent_fd, name)
    finally:
        os.close(parent_fd)


def _create_dir_beneath(parent_fd: int, name: str, *, mode: int) -> int:
    """``mkdirat`` then ``openat``, and verify the handle IS what we just made.

    Fresh, empty, owned by this uid, exactly the mode we asked for. The inode
    compare closes the window after the first stat; the rest closes the one
    before it, for a directory that was swapped rather than linked.
    """
    os.mkdir(name, mode, dir_fd=parent_fd)
    created = os.stat(name, dir_fd=parent_fd, follow_symlinks=False)
    fd = _open_child_dir(parent_fd, name)
    try:
        opened = os.fstat(fd)
        if (opened.st_dev, opened.st_ino) != (created.st_dev, created.st_ino):
            raise UnsafePoolPath(
                f"{name!r} was replaced between its creation and its open "
                f"(inode {created.st_ino} became {opened.st_ino})"
            )
        if opened.st_uid != os.getuid():
            raise UnsafePoolPath(
                f"{name!r} is owned by uid {opened.st_uid}, not this process"
            )
        if stat.S_IMODE(opened.st_mode) != mode:
            raise UnsafePoolPath(
                f"{name!r} has mode {stat.S_IMODE(opened.st_mode):#o}, not the "
                f"{mode:#o} this call created"
            )
        if os.listdir(fd):
            raise UnsafePoolPath(
                f"{name!r} is not the empty directory this call created; "
                "something was renamed over it"
            )
        try:
            os.lseek(fd, 0, os.SEEK_SET)  # rewind: the caller gets a fresh handle
        except OSError:
            pass
    except BaseException:
        os.close(fd)
        raise
    return fd


def create_workspace_subdir(
    parent_fd: int, name: str, *, mode: int = _LEASE_DIR_MODE
) -> int:
    """Create a FIXED-name directory inside a lease this process already owns.

    ``<lease>/repo`` is not a lease: its parent is the private, unguessable
    directory ``create_lease_dir`` just made, so the entropy rule that protects
    a name in the SHARED pool root would be cargo. What still applies is
    everything else - the mkdirat, the openat, and the verification that the
    handle is the fresh empty directory this call created.

    It exists because the adapter was calling ``create_lease_dir`` for this and
    the hardened name rule refused ``'repo'``, so every checkout failed on
    POSIX (found by the end-to-end chain test, 2026-08-30). A flag on
    ``create_lease_dir`` would have been worse: a security rule you can switch
    off from the call site is configuration, not a rule.
    """
    _require_posix("create_workspace_subdir")
    _require_component(name)
    return _create_dir_beneath(parent_fd, name, mode=mode)


def create_lease_dir(parent_fd: int, name: str, *, mode: int = _LEASE_DIR_MODE) -> int:
    """Create one directory under ``parent_fd`` and return a handle to IT.

    What the inode compare actually proves is narrow, and it is worth saying
    plainly: it closes the window between the ``stat`` and the ``open`` only. A
    rename that lands BEFORE the first stat is invisible to it - both calls would
    then see the intruder.

    The guarantee therefore rests on three things, all enforced here:

    * the parent must be **owned by this uid and not group- or world-writable**,
      so no other principal can rename anything into it;
    * the name must be **at least 16 random hex characters**, so it cannot be
      created or targeted before we get there; and
    * the opened handle must be a **fresh, empty directory** owned by this uid
      with exactly the mode we asked for - a swapped-in directory with any
      content, any other owner, or a looser mode is refused.

    Together those close the pre-stat window too, and none of them depends on
    winning a race.
    """
    _require_posix("create_lease_dir")
    _require_component(name)
    if len(name) < MIN_LEASE_NAME_CHARS or any(char not in _HEX for char in name):
        raise UnsafePoolPath(
            f"a lease directory name must be at least {MIN_LEASE_NAME_CHARS} "
            f"random hex characters (secrets.token_hex(8) or wider), got {name!r}"
        )
    parent = os.fstat(parent_fd)
    if parent.st_uid != os.getuid():
        raise UnsafePoolPath(
            f"the pool parent is owned by uid {parent.st_uid}, not this process "
            f"({os.getuid()}): another user could rename over the name we create"
        )
    if parent.st_mode & (stat.S_IWGRP | stat.S_IWOTH):
        raise UnsafePoolPath(
            f"the pool parent is group- or world-writable (mode "
            f"{stat.S_IMODE(parent.st_mode):#o}): anyone could create or replace "
            "the lease directory"
        )

    return _create_dir_beneath(parent_fd, name, mode=mode)


def _directory_owner_identity(fd: int) -> tuple[int, int] | None:
    """D60 label on a pinned owner root, assigned in privileged migration.

    A daemon-owned root may carry the reserved owner group so the owner cannot
    replace protected root entries. Dedicated work directories carry both IDs.
    Legacy/shared roots confer no dedicated identity. Admission must still
    bind the requested root to the owner; these labels do not grant API access.
    """
    info = os.fstat(fd)
    uid, gid = info.st_uid, info.st_gid
    if 300001 <= gid <= 399999 and uid in (1001, gid):
        return gid, gid
    if 300000 <= uid <= 399999 or 300000 <= gid <= 399999:
        raise UnsafePoolPath("invalid dedicated owner directory identity")
    return None


def _read_owner_identity(fd: int) -> tuple[int, int] | None:
    """Keep owner provenance when the caller pins a nested daemon-owned dir.

    Traverse only directory descriptors, never /proc pathname spellings. A
    foreign labelled subtree cannot replace the enclosing owner's identity.
    """
    current = os.dup(fd)
    identity = None
    try:
        for _ in range(256):
            found = _directory_owner_identity(current)
            if found is not None:
                if identity is not None and identity != found:
                    raise UnsafePoolPath("read crosses dedicated owner identities")
                identity = found
            parent = _open_child_dir(current, "..")
            same = os.path.samestat(os.fstat(current), os.fstat(parent))
            os.close(current)
            current = parent
            if same:
                return identity
        raise UnsafePoolPath("read directory ancestry exceeds the identity bound")
    finally:
        os.close(current)


def _open_regular_beneath(
    dir_fd: int, relpath: str | Path, *, max_bytes: int,
    expected_identity: tuple[int, int] | None = None,
) -> tuple[int, int]:
    """Open a REGULAR file beneath ``dir_fd``; return ``(fd, size)``.

    Every directory component is opened with ``O_NOFOLLOW``, the leaf too, and
    the file type is checked with ``fstat`` on the OPEN descriptor - never a
    stat of the path, which describes whatever the name pointed at a moment ago.
    """
    _require_posix("read_regular_file_beneath")
    if int(max_bytes) < 0:
        raise ValueError(f"max_bytes must be >= 0, got {max_bytes}")
    parts = _split_relpath(relpath)
    current = dir_fd
    identity = _read_owner_identity(dir_fd)
    if expected_identity is not None:
        if identity is not None and identity != expected_identity:
            raise UnsafePoolPath("read root does not match the admitted owner identity")
        identity = expected_identity
    opened: list[int] = []
    try:
        for part in parts[:-1]:
            current = _open_child_dir(current, part)
            opened.append(current)
            child_identity = _directory_owner_identity(current)
            if child_identity is not None:
                if identity is not None and child_identity != identity:
                    raise UnsafePoolPath("read crosses dedicated owner identities")
                identity = child_identity
        fd = _open_leaf(current, parts[-1])
    finally:
        for handle in opened:
            os.close(handle)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode):
            raise UnsafePoolPath(
                f"{str(relpath)!r} is not a regular file (mode {info.st_mode:#o}); "
                "a workspace read never opens a device, a FIFO or a directory"
            )
        if identity is not None and (info.st_uid, info.st_gid) != identity:
            raise UnsafePoolPath(
                f"{str(relpath)!r} does not belong to the admitted owner identity"
            )
        if info.st_size > int(max_bytes):
            raise UnsafePoolPath(
                f"{str(relpath)!r} is {info.st_size} bytes, over the {max_bytes} bound"
            )
    except BaseException:
        os.close(fd)
        raise
    return fd, int(info.st_size)


def read_regular_file_beneath(
    dir_fd: int, relpath: str | Path, *, max_bytes: int,
    expected_identity: tuple[int, int] | None = None,
) -> bytes:
    """Read a regular file beneath a held directory handle, bounded.

    The bound is enforced twice: the size reported by ``fstat`` on the open
    descriptor, and the read itself, which stops at ``max_bytes + 1`` and
    refuses. A file that GROWS between the stat and the read must not slip
    through on the strength of its earlier size.
    """
    fd, _size = _open_regular_beneath(
        dir_fd, relpath, max_bytes=max_bytes, expected_identity=expected_identity,
    )
    try:
        chunks: list[bytes] = []
        remaining = int(max_bytes) + 1
        while remaining > 0:
            chunk = os.read(fd, min(_COPY_CHUNK, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        data = b"".join(chunks)
    finally:
        os.close(fd)
    if len(data) > int(max_bytes):
        raise UnsafePoolPath(
            f"{str(relpath)!r} grew past the {max_bytes} bound while it was being read"
        )
    return data


def _unlink_if_same_inode(dest: str, created: os.stat_result) -> None:
    """Remove ``dest`` only while it is still the file this call created.

    Cleanup that unlinks by NAME would delete whatever now answers to it. If the
    destination was replaced after we created it, deleting it is destroying
    somebody else's file to tidy up after ourselves.
    """
    try:
        current = os.lstat(dest)
    except OSError:
        return
    if (current.st_dev, current.st_ino) != (created.st_dev, created.st_ino):
        return
    try:
        os.unlink(dest)
    except OSError:
        pass


def copy_regular_file_beneath(
    dir_fd: int, relpath: str | Path, dest_path: str | Path, *, max_bytes: int,
    expected_identity: tuple[int, int] | None = None,
) -> int:
    """Stream a regular file from beneath a held handle to ``dest_path``.

    The destination is created ``O_CREAT | O_EXCL | O_WRONLY | O_NOFOLLOW`` at
    mode 0o600, so an existing file or a symlink planted at the destination is a
    refusal rather than an overwrite of whatever it points at. A partial
    destination is removed on ANY failure, the bound included - half a manifest
    left behind would be read later as a whole one - but only after an inode
    compare proves it is still the file this call created.
    """
    fd, _size = _open_regular_beneath(
        dir_fd, relpath, max_bytes=max_bytes, expected_identity=expected_identity,
    )
    dest = str(dest_path)
    copied = 0
    try:
        # Outside the cleanup below on purpose: a destination this call did not
        # create is not this call's to delete. A symlink already sitting there
        # is refused by O_EXCL and left exactly as it was found.
        dest_fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY | _O_NOFOLLOW, _COPY_DEST_MODE)
    except BaseException:
        os.close(fd)
        raise
    created = os.fstat(dest_fd)
    try:
        try:
            while True:
                chunk = os.read(fd, _COPY_CHUNK)
                if not chunk:
                    break
                copied += len(chunk)
                if copied > int(max_bytes):
                    raise UnsafePoolPath(
                        f"{str(relpath)!r} grew past the {max_bytes} bound while it "
                        "was being copied"
                    )
                offset = 0
                while offset < len(chunk):
                    offset += os.write(dest_fd, chunk[offset:])
        finally:
            os.close(dest_fd)
    except BaseException:
        _unlink_if_same_inode(dest, created)
        raise
    finally:
        os.close(fd)
    return copied


def bind_target_for(dir_fd: int) -> str:
    """The ``/proc/self/fd/<n>`` name of a held directory handle.

    A sandbox is given THIS as its ``--bind`` source, not the path the handle
    was opened from. The path is a name that can be re-pointed: between the
    check and the bind, a rename can put a different directory - or a symlink to
    one - under the same name, and the sandbox would mount that instead. The
    descriptor cannot be re-pointed; it names the inode that was verified.
    """
    _require_posix("bind_target_for")
    if not isinstance(dir_fd, int) or isinstance(dir_fd, bool) or dir_fd < 0:
        raise ValueError(f"dir_fd must be a non-negative descriptor, got {dir_fd!r}")
    return f"/proc/self/fd/{dir_fd}"


# --------------------------------------------------------------------------
# the PoolFilesystem the outbox processor drives
# --------------------------------------------------------------------------


def _remove_beneath(parent_fd: int, name: str, depth: int = 0) -> None:
    """Delete ``name`` under ``parent_fd``, never following a link.

    Every step is an ``*at`` call through a descriptor we opened with
    ``O_NOFOLLOW``, so there is no window in which the name could be re-pointed
    between a check and the act. A symlink is unlinked; only a real directory is
    descended into, through its own handle.
    """
    if depth > _MAX_TREE_DEPTH:
        raise UnsafePoolPath(
            f"workspace tree deeper than {_MAX_TREE_DEPTH} at {name!r}: refusing "
            "to recurse further"
        )
    try:
        info = os.lstat(name, dir_fd=parent_fd)
    except FileNotFoundError:
        return
    if not stat.S_ISDIR(info.st_mode):
        # Regular files, devices, FIFOs and SYMLINKS (lstat, so a link to a
        # directory lands here) are unlinked, not walked.
        try:
            os.unlink(name, dir_fd=parent_fd)
        except FileNotFoundError:
            pass
        return
    child_fd = _open_child_dir(parent_fd, name)
    try:
        for entry in os.listdir(child_fd):
            _remove_beneath(child_fd, entry, depth + 1)
    finally:
        os.close(child_fd)
    try:
        os.rmdir(name, dir_fd=parent_fd)
    except FileNotFoundError:
        pass


def _remove_tree_windows(
    target: str,
    *,
    total_s: float = WINDOWS_RETRY_TOTAL_S,
    step_s: float = WINDOWS_RETRY_STEP_S,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    """The Windows branch: no ``dir_fd``, so this walks paths - but it still
    refuses to descend into a symlink or a JUNCTION.

    ``os.walk`` decides what to descend into from ``is_dir(follow_symlinks=
    False)``, which is TRUE for a junction, and a junction needs no privilege to
    create. Hence the explicit ``scandir`` stack.
    """
    if not os.path.lexists(target):
        return
    if _is_link(target):
        _unlink_link(target)
        return
    def _attempt(action: Callable[[], None], path: str) -> None:
        _retry_transient_windows(
            action, path, total_s=total_s, step_s=step_s, sleep=sleep
        )

    if not os.path.isdir(target):
        _attempt(lambda: _unlink_windows(target), target)
        return
    pending: list[str] = [target]
    emptied: list[str] = []
    while pending:
        current = pending.pop()
        emptied.append(current)
        try:
            with os.scandir(current) as entries:
                children = list(entries)
        except FileNotFoundError:
            continue
        for entry in children:
            child = entry.path
            try:
                if entry.is_symlink() or _is_link(child):
                    _unlink_link(child)
                elif entry.is_dir(follow_symlinks=False):
                    pending.append(child)
                else:
                    _attempt(lambda c=child: _unlink_windows(c), child)
            except FileNotFoundError:
                continue
    for directory in reversed(emptied):
        try:
            _attempt(lambda d=directory: os.rmdir(d), directory)
        except FileNotFoundError:
            continue


class RealPoolFilesystem:
    """``exists``/``rename``/``remove_tree_no_follow`` against the real disk.

    On POSIX every step goes through a descriptor opened without following
    links; on Windows, where there is no ``dir_fd``, the path-based
    implementation is used behind an explicit platform branch. ``posix`` is
    injectable so a test can drive the Windows branch on Linux and vice versa.
    """

    def __init__(
        self,
        *,
        posix: bool | None = None,
        retry_total_s: float = WINDOWS_RETRY_TOTAL_S,
        retry_step_s: float = WINDOWS_RETRY_STEP_S,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._posix = _POSIX if posix is None else bool(posix)
        # Windows-branch knobs: a test drives a transient failure without
        # waiting three real seconds for the deadline.
        self._retry_total_s = retry_total_s
        self._retry_step_s = retry_step_s
        self._sleep = sleep

    def exists(self, path: Path) -> bool:
        """Presence WITHOUT following links: a dangling symlink is present, and
        the processor still owes its removal."""
        target = Path(path)
        if not self._posix:
            return os.path.lexists(str(target))
        try:
            parent_fd = open_dir_nofollow(target.parent)
        except (OSError, UnsafePoolPath):
            # No parent, or a parent that is a link: nothing of ours is there.
            return False
        try:
            os.lstat(target.name, dir_fd=parent_fd)
            return True
        except OSError:
            return False
        finally:
            os.close(parent_fd)

    def rename(self, src: Path, dst: Path) -> None:
        """Move a workspace to its deterministic quarantine name.

        The quarantine parent is created here when missing - the pool module
        creates no directories, and a rename into a missing parent would leave
        the bytes in place with the entry marked done.
        """
        source = Path(src)
        target = Path(dst)
        if not self._posix:
            parent = os.path.dirname(str(target))
            if parent:
                os.makedirs(parent, exist_ok=True)
            os.replace(str(source), str(target))
            return
        src_fd = open_dir_nofollow(source.parent)
        try:
            dst_fd = _open_dir_making_one_level(target.parent)
            try:
                os.rename(
                    _require_component(source.name),
                    _require_component(target.name),
                    src_dir_fd=src_fd,
                    dst_dir_fd=dst_fd,
                )
            finally:
                os.close(dst_fd)
        finally:
            os.close(src_fd)

    def remove_tree_no_follow(self, path: Path) -> None:
        """Delete a tree bottom-up, never descending into a link or junction.

        A path that is already gone is not an error: the processor is
        at-least-once, so a repeat has to be a no-op. Anything else propagates as
        ``OSError`` and the lease becomes ``LOST`` with its bytes still charged.
        """
        target = Path(path)
        if not self._posix:
            _remove_tree_windows(
                str(target),
                total_s=self._retry_total_s,
                step_s=self._retry_step_s,
                sleep=self._sleep,
            )
            return
        try:
            parent_fd = open_dir_nofollow(target.parent)
        except FileNotFoundError:
            return
        try:
            _remove_beneath(parent_fd, _require_component(target.name))
        finally:
            os.close(parent_fd)
