"""Fixed owner-file maintenance, called only inside the trusted maintenance cell.

No caller-selected path or executable is accepted. chmod restores inherited
daemon ACL masks; it cannot invent access after an ACL has been removed.
"""
from __future__ import annotations

import ctypes
import errno
import os
import stat
import time
import uuid

from tinyassets.universe_tools import AGENT_BRAIN_FILES, AGENT_HARNESS_DIRS


def maintain(root, *, agent_id):
    """Prepare fixed directories, restore owner modes, promote absent brains."""
    uid = os.getuid()
    visited = 0
    truncated = False
    skipped = []
    deadline = time.monotonic() + 20

    def repair(parent, name, depth=0):
        nonlocal visited, truncated
        visited += 1
        if depth and (visited > 100000 or depth > 64 or time.monotonic() >= deadline):
            truncated = True
            return
        fd = os.open(name, os.O_PATH | os.O_NOFOLLOW, dir_fd=parent)
        try:
            info = os.fstat(fd)
            directory = stat.S_ISDIR(info.st_mode)
            if ((info.st_uid, info.st_gid) != (uid, uid)
                    or not (directory or stat.S_ISREG(info.st_mode))
                    or (not directory and info.st_nlink != 1)):
                return  # Never relabel, follow, or read foreign/alias/special entries.
            # O_PATH pins even mode-000 objects. /proc/self/fd names that exact
            # inode; following this kernel link cannot select a different file.
            mode = stat.S_IMODE(info.st_mode) | (0o770 if directory else 0o660)
            if mode != stat.S_IMODE(info.st_mode):
                os.chmod(f'/proc/self/fd/{fd}', mode)
            if directory:
                child = os.open(f'/proc/self/fd/{fd}', os.O_RDONLY | os.O_DIRECTORY)
                try:
                    with os.scandir(child) as entries:
                        for entry in entries:
                            repair(child, entry.name, depth + 1)
                            if visited >= 100000 or time.monotonic() >= deadline:
                                truncated = True
                                break
                finally:
                    os.close(child)
        finally:
            os.close(fd)

    required = ('.agent-workspace', *AGENT_HARNESS_DIRS)
    for name in required:
        try:
            os.mkdir(name, 0o770, dir_fd=root)
        except FileExistsError:
            pass
        info = os.stat(name, dir_fd=root, follow_symlinks=False)
        if not stat.S_ISDIR(info.st_mode) or (info.st_uid, info.st_gid) != (uid, uid):
            raise PermissionError('tool directory is not owned by the admitted owner')
        repair(root, name)
    for name in AGENT_BRAIN_FILES:
        try:
            repair(root, name)
        except FileNotFoundError:
            pass
    workspace = os.open('.agent-workspace', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW,
                        dir_fd=root)
    try:
        promoted = []
        libc = ctypes.CDLL(None, use_errno=True)
        for name in AGENT_BRAIN_FILES:
            if name == 'identity.md' and agent_id != 'main':
                continue
            try:
                os.stat(name, dir_fd=root, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                continue
            try:
                source_fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                                    dir_fd=workspace)
            except FileNotFoundError:
                continue
            except OSError:
                skipped.append(name)
                continue
            try:
                source = os.fstat(source_fd)
                if (not stat.S_ISREG(source.st_mode) or source.st_nlink != 1
                        or (source.st_uid, source.st_gid) != (uid, uid)
                        or source.st_size > 1024 * 1024):
                    skipped.append(name)
                    continue
                data = bytearray()
                while chunk := os.read(source_fd, min(65536, 1024 * 1024 + 1 - len(data))):
                    data.extend(chunk)
                    if len(data) > 1024 * 1024:
                        break
            finally:
                os.close(source_fd)
            if len(data) > 1024 * 1024:
                skipped.append(name)
                continue
            # Copy from the pinned file; never rename an untrusted source name
            # after checking it. Preserve workspace bytes for crash recovery.
            temporary = '.tool-brain-' + uuid.uuid4().hex
            out = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                          0o660, dir_fd=root)
            try:
                with os.fdopen(out, 'wb') as stream:
                    stream.write(data)
                    stream.flush()
                    os.fsync(stream.fileno())
                if libc.renameat2(root, os.fsencode(temporary), root, os.fsencode(name), 1) != 0:
                    code = ctypes.get_errno()
                    if code != errno.EEXIST:
                        raise OSError(code, os.strerror(code), name)
                else:
                    promoted.append(name)
            finally:
                try:
                    os.unlink(temporary, dir_fd=root)
                except FileNotFoundError:
                    pass
        os.fsync(workspace)
        os.fsync(root)
        return {'visited': visited, 'promoted': promoted, 'skipped': skipped,
                'truncated': truncated}
    finally:
        os.close(workspace)
