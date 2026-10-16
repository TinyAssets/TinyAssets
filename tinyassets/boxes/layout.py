"""The owner-box data layout: platform identities, modes and per-center dirs.

Every box service admits the committed layout for its lifetime (:func:`hold`).
The numeric identities and file modes here are the platform's, shared by the
daemon, the broker and the one-shot layout migration; no owner process runs
under any of them.
"""

import json
import os
from contextlib import contextmanager

#: The retired, capability-free daemon.
DAEMON_UID = 1001
#: The credential broker's own identity.
BROKER_UID = 1002
#: Daemon-written files the broker may read (vault copies, definitions).
BROKER_READ_GID = 1102
BROKER_METADATA_FILE_MODE = 0o640
VAULT_FILE_MODE = 0o640
LIVENESS_DIRECTORY_MODE = 0o2750
LIVENESS_FILE_MODE = 0o640

#: Daemon-owned files that belong to one command center but must not live
#: inside it (consent databases, seed locks): ``<data>/.universe-sidecars/<center>``.
UNIVERSE_SIDECARS_DIR = ".universe-sidecars"
#: The platform's per-center runtime directory (session records); never in the box.
PLATFORM_RUNTIME_DIR = ".runtime"
#: The agent's own workspace inside its command center: the tools' ``/u``.
AGENT_WORKSPACE_DIR = ".agent-workspace"


@contextmanager
def hold(data):
    import fcntl

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
