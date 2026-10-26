"""A seal key belongs to the owner volume, never to the shared host."""

from __future__ import annotations

import os
import secrets
import stat
from pathlib import Path


def load(root: Path) -> bytes:
    """Persist before use; serialize first starts and refuse damaged state.

    The volume is the isolation boundary. Keeping the key with that volume also
    keeps sealed owner data usable after snapshot restore or host relocation.
    No shared authentication credential is stored here.
    """
    import fcntl

    directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        fcntl.flock(directory, fcntl.LOCK_EX)
        name = ".session-seal.key"
        try:
            os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            # Write aside, then rename: an interrupted first start leaves at
            # most a stale temporary file, never an empty key. The lock above
            # makes this the only writer, so the fixed temporary name is safe.
            partial = name + ".partial"
            try:
                os.unlink(partial, dir_fd=directory)
            except FileNotFoundError:
                pass
            fd = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=directory)
            with os.fdopen(fd, "wb") as stream:
                stream.write(secrets.token_bytes(32))
                stream.flush()
                os.fsync(stream.fileno())
            os.rename(partial, name, src_dir_fd=directory, dst_dir_fd=directory)
            os.fsync(directory)
        fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or stat.S_IMODE(info.st_mode) != 0o600 or info.st_uid != os.geteuid()):
                raise RuntimeError("owner session seal key must be a private regular file")
            key = stream.read(33)
        if len(key) != 32:
            raise RuntimeError("owner session seal key must contain exactly 32 bytes")
        return key
    finally:
        os.close(directory)
