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

PAUSE = ".pause"
WORKER_HEARTBEAT = ".worker_supervisor.json"
WORKER_HEARTBEAT_PATTERN = ".worker_supervisor.<id>.json"
LEDGER = "auto_ship_attempts.jsonl"
LEDGER_LOCK = ".auto_ship_attempts.jsonl.lock"

# A center's host control state and its owner box content have one home each.
HOST_CENTER_ENTRIES = frozenset({
    PLATFORM_RUNTIME_DIR, PAUSE, WORKER_HEARTBEAT, LEDGER, LEDGER_LOCK,
    ".subscription_state.db", ".usage_ledger.db", ".external_write_receipts.db",
    ".pending_requests.db", ".wiki_write_back_destination_markers.db",
    ".authoring.db", ".idempotency.db", ".conversation_memory.db",
    ".conversation_attention.db", ".effector_consents.db",
    ".effector_consents.db.premigration", ".runs.db", ".automations.db",
    ".tinyassets.db", "rules.db", "steering.db",
    ".runtime_status.json", ".engine_mcp_config.json",
    ".engine_mcp_http_routes.json", ".provider-authority",
    ".provider-assignment-admission.lock", "provider_definitions.json",
    ".oauth-refresh", ".soul.lock",
    ".workspace-staging", "checkpoints.db",
})
HOST_CENTER_MOVED = frozenset({
    "soul.md", "soul_versions", "config.yaml", "wiki", "canon", "output",
    "agents", "brain", "identity.md", "founder.md", "origin.md", "body.md",
    "orgchart.md", "AGENTS.md", "MEMORY.md", "settings.yaml",
    AGENT_WORKSPACE_DIR, "skills", "prompts", "extensions", "workflows",
    "notes", "notes.json", "dispatcher_config.yaml", "activity.log", "status.json",
})
assert HOST_CENTER_ENTRIES.isdisjoint(HOST_CENTER_MOVED)


def host_center_entry(name: str) -> bool:
    if name in HOST_CENTER_ENTRIES:
        return True
    if (name.startswith(".worker_supervisor.") and name.endswith(".json")
            and len(name) > len(".worker_supervisor..json")):
        return True
    return any(name == database + suffix for database in HOST_CENTER_ENTRIES
               if database.endswith((".db", ".db.premigration"))
               for suffix in ("-wal", "-shm", "-journal"))


def host_center_moved(name: str) -> bool:
    return name in HOST_CENTER_MOVED


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
