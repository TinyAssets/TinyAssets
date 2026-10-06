"""Fixed capability-free owner pass; imported only after cell retirement."""
import os
import stat
import time
from pathlib import Path


def remove_owned(root, *, classify_daemon, overflow_uid=None):
    """Delete owner entries only; leave daemon entries for its verified pass.

    No regular file is opened for reading. This is intentionally non-atomic:
    errors name the relative entry and leave the mapper fence intact for retry.
    """
    uid, gid = os.getuid(), os.getgid()
    if overflow_uid is None:
        overflow_uid = int(Path('/proc/sys/kernel/overflowuid').read_text())
    deadline = time.monotonic() + 20
    visited = removed = retained = 0

    def detach(parent, name, directory):
        nonlocal removed, retained
        try:
            (os.rmdir if directory else os.unlink)(name, dir_fd=parent)
            removed += 1
        except PermissionError:
            # A daemon-owned parent the owner cannot write (the migrated
            # center root is 1001:<owner> 2750): the daemon pass unlinks this
            # exact owner name. An owner-owned parent refusing is a failure.
            if os.fstat(parent).st_uid == uid:
                raise
            retained += 1

    def walk(parent, name, relative, depth):
        nonlocal visited, removed, retained
        visited += 1
        if visited > 100000 or depth > 64 or time.monotonic() >= deadline:
            raise RuntimeError('deletion traversal bound: ' + relative[:1024])
        fd = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            owned = (info.st_uid, info.st_gid) == (uid, gid)
            # Nested userns collapses ALL other identities to overflow. The
            # daemon classifies this exact O_PATH inode using the host uid;
            # a name, caller uid or in-cell overflow value is not evidence.
            if not owned and (info.st_uid != overflow_uid or not classify_daemon(fd)):
                raise PermissionError('foreign deletion entry: ' + relative[:1024])
            if stat.S_ISDIR(info.st_mode):
                if owned:
                    os.chmod(f'/proc/self/fd/{fd}', stat.S_IMODE(info.st_mode) | 0o770)
                try:
                    child = os.open(f'/proc/self/fd/{fd}', os.O_RDONLY | os.O_DIRECTORY)
                except PermissionError:
                    if owned:
                        raise
                    # A daemon directory the owner may only search (.runtime)
                    # holds nothing it wrote; the daemon pass verifies it all.
                    retained += 1
                    return
                try:
                    with os.scandir(child) as entries:
                        for entry in entries:
                            walk(child, entry.name, relative + '/' + entry.name, depth + 1)
                    empty = not os.listdir(child)
                finally:
                    os.close(child)
                if owned and empty:
                    detach(parent, name, True)
                else:
                    retained += 1  # daemon pass removes its entries/empty structure
            elif owned:
                # Unlinking a hardlink or symlink never chmods or reads its target.
                detach(parent, name, False)
            else:
                retained += 1
        except OSError as exc:
            raise RuntimeError('owner deletion failed: ' + relative[:1024]) from exc
        finally:
            os.close(fd)

    with os.scandir(root) as entries:
        for entry in entries:
            walk(root, entry.name, entry.name, 1)
    os.fsync(root)
    return dict(visited=visited, removed=removed, retained=retained)


