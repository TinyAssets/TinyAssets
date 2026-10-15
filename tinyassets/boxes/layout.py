"""Lifetime shared layout admission for each independently started box service."""

import fcntl
import json
import os
from contextlib import contextmanager


@contextmanager
def hold(data):
    # Never create or migrate data at startup. Keep the same inode locked for
    # the entire service lifetime, including broker policy initialization.
    fd = os.open(data / ".layout.lock", os.O_RDONLY | os.O_NOFOLLOW)
    try:
        fcntl.flock(fd, fcntl.LOCK_SH)
        marker = os.open(data / ".layout.json", os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(marker) as stream:
            document = json.load(stream)
        if (not isinstance(document, dict) or document.get("layout") not in (1, 2)
                or document.get("state") != "stable"
                or document.get("box_layout") != "owner-box"):
            raise RuntimeError("box service requires a committed owner-box layout")
        yield
    finally:
        os.close(fd)
