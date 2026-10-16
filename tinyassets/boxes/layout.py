"""The owner-box data layout: platform identities, modes and per-center dirs.

Every box service admits the committed layout for its lifetime (:func:`hold`).
The numeric identities and file modes here are the platform's, shared by the
daemon, the broker and the one-shot layout migration; no owner process runs
under any of them.
"""

import json
import os
from contextlib import contextmanager
from fnmatch import fnmatchcase
from pathlib import Path

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
# Per-center state still addressed through <data>/<center> by the daemon:
# subscription_state/account_type_of and Stripe; agent_sessions and provider
# launches; the per-universe platform stores and status readers. Owner content
# is in the box. SQLite journal companions follow their database on cutover.
HOST_CENTER_ENTRIES = frozenset({
    PLATFORM_RUNTIME_DIR,
    ".subscription_state.db", ".usage_ledger.db",
    ".external_write_receipts.db", ".pending_requests.db",
    ".wiki_write_back_destination_markers.db", ".authoring.db",
    ".idempotency.db", ".conversation_memory.db",
    ".conversation_attention.db", ".effector_consents.db",
    # api/storage_observations, automation_context, conversation_custody
    ".runs.db", ".automations.db", ".tinyassets.db",
    "rules.db", "steering.db",
    # api/universe status and liveness readers
    "activity.log", "status.json", ".runtime_status.json", ".engine_mcp_config.json",
    ".engine_mcp_http_routes.json", ".pause",
    ".provider-authority", ".provider-assignment-admission.lock",
    ".worker_supervisor*.json", "auto_ship_attempts.jsonl",
    ".auto_ship_attempts.jsonl",
    ".auto_ship_attempts.jsonl.lock",
})
_SQLITE_COMPANIONS = ("-wal", "-shm", "-journal")
HOST_CENTER_COPY_EXCLUDES = HOST_CENTER_ENTRIES | frozenset(
    database + suffix
    for database in HOST_CENTER_ENTRIES if database.endswith(".db")
    for suffix in _SQLITE_COMPANIONS
)


def host_center_entry(name: str) -> bool:
    """Whether a top-level entry stays in the host command center."""
    return name in HOST_CENTER_ENTRIES or (
        "/" not in name and "\\" not in name
        and fnmatchcase(name, ".worker_supervisor*.json")
    )

# Only these copied owner entries are retired from the host at cutover. Other
# entries may still be read or written by platform code through <data>/<center>.
# In particular, the four split host/box files are intentionally absent here.
HOST_CENTER_MOVED = frozenset({
    # C12 knowledge sources and their original vector stores.
    "daemon_brain.db", ".project_memory.db", "episodic.db", "knowledge.db",
    "promises.db", "versions.db", "temporal.db", "lancedb", ".lance", "lance",
    # Agent definitions, brain and owner-editable harness/content.
    ".agent-workspace", "agents", "brain", "AGENTS.md", "MEMORY.md",
    "Activities.md", "Activities.tools.json", "Activities.error.txt",
    "voice.md", "identity.md", "founder.md", "settings.yaml", "origin.md",
    "body.md", "orgchart.md", "projects.md", "goals.md", "index.md",
    "log.md", "skills", "prompts", "extensions", "workflows", "bin",
    "notes", "notes.json", "PROGRAM.md", "progress.md", "timeline.json",
    "promises.json",
    "facts.json", "characters.json",
})


def host_center_moved(name: str) -> bool:
    """Whether a copied top-level owner entry is retired from the host."""
    return name in HOST_CENTER_MOVED or any(
        name == database + suffix
        for database in HOST_CENTER_MOVED if database.endswith(".db")
        for suffix in _SQLITE_COMPANIONS
    )


def host_center_stub(data_root: str | Path, center: str) -> Path:
    """The host-side platform directory for one admitted center."""
    if (not center or center in (".", "..") or center.startswith(".")
            or Path(center).name != center or "/" in center or "\\" in center):
        raise ValueError("invalid host center name")
    return Path(data_root) / center
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
