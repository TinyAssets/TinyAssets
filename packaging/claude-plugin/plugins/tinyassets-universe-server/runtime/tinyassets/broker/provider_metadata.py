"""Read the daemon's provider metadata with traversal-only directory authority."""
from __future__ import annotations

import json
import os
import stat
from pathlib import Path

from tinyassets.broker.owner_identities import validate_center


def read_definitions(base: Path, center: str):
    """Pin every component; never list a center or read an owner-content inode.

    The center and metadata publisher are the daemon. The broker's existing
    group grants traversal on the center and read access on this fixed leaf.
    Owner content has a different UID and cannot stand in for metadata.
    """
    from tinyassets.universe_files import MAX_UNIVERSE_FILE_BYTES

    validate_center(center)
    root = Path(base).absolute()
    flags = os.O_PATH | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC
    directory = os.open(root.anchor, flags)
    try:
        for component in (*root.parts[1:], center):
            child = os.open(component, flags, dir_fd=directory)
            os.close(directory)
            directory = child
        publisher = os.fstat(directory).st_uid
        fd = os.open('provider_definitions.json',
                     os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC,
                     dir_fd=directory)
        with os.fdopen(fd, 'rb') as handle:
            info = os.fstat(handle.fileno())
            if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                    or info.st_uid != publisher or info.st_mode & 0o022):
                raise PermissionError('provider metadata is not exclusively publisher-owned')
            raw = handle.read(MAX_UNIVERSE_FILE_BYTES + 1)
            if len(raw) > MAX_UNIVERSE_FILE_BYTES:
                raise ValueError('provider metadata exceeds its read bound')
            rows = json.loads(raw)
            if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
                raise ValueError('provider metadata must be a list of definitions')
            return rows
    finally:
        os.close(directory)
