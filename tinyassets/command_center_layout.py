"""Where every entry of a command center's home belongs after the cutover.

`openspec/changes/command-center-cutover` design E6, agreed with
`target-architecture` D8a (#4263). One rule decides: anything the daemon TRUSTS
(authority, identity, owner settings, records) is ``platform`` state and moves
to ``.platform/cc-<ulid>/``; anything the person or their agent may write is
``user`` content and stays in ``cc-<ulid>/`` (the future box volume), read by the
daemon as untrusted.

`classify` is the one table both the read-only inventory and the migration use.
An entry it does not know returns ``None``: the inventory reports it, and the
migration refuses to run until it is classified here, so nothing is guessed into
the box. Classification uses exact names and creator-backed DB families only.
Extend the table; never add a default.
"""

from __future__ import annotations

import re

from tinyassets.boxes.layout import (
    HOST_CENTER_ENTRIES,
    HOST_CENTER_MOVED,
    host_center_entry,
)
from tinyassets.retired_home_entries import (
    PLATFORM_LOCK_NAMES as RETIRED_PLATFORM_LOCK_NAMES,
)
from tinyassets.retired_home_entries import (
    PLATFORM_NAMES as RETIRED_PLATFORM_NAMES,
)

USER = "user"
PLATFORM = "platform"

#: Agent- or person-written content (exact entry names in a home).
USER_NAMES = HOST_CENTER_MOVED | frozenset({
    "voice.md", "projects.md", "goals.md", "index.md", "log.md", "bin",
    "workspace", "workspaces", "artifacts", "PROGRAM.md", "progress.md",
    "design-proposals", "feature-requests", "patch-requests",
    "timeline.json", "promises.json", "facts.json", "characters.json",
})

#: Additional historical entries used by the inventory/cutover classifier.
#: Current host and moved entries are defined only by boxes.layout.
PLATFORM_NAMES = frozenset(name for name in HOST_CENTER_ENTRIES
                           if not name.endswith((".db", ".lock"))) | frozenset({
    "soul.edit.md", "universe.json", "work_targets.json", "ledger.json",
    "hard_priorities.json", "requests.json", "branch_tasks.json",
    "branch_tasks_archive.json", "bid_execution_log.json", "enrichment_signals.json",
    "worldbuild_signals.json", "platform-expected-instance.json", "uptime-probe",
    "executions", "runs", "reviews", "discarded_targets", "archived",
    "app_refresh_sessions", "import", "import-verify", "verify", "lancedb",
    ".credentials", ".credentials.json", ".credential-vault.json",
    ".agent-sessions", ".consumer_liveness", ".quarantine", ".authoring_blobs",
}) | RETIRED_PLATFORM_NAMES

PLATFORM_DB_NAMES = frozenset(name for name in HOST_CENTER_ENTRIES if name.endswith(".db"))
PLATFORM_LOCK_NAMES = (frozenset(name for name in HOST_CENTER_ENTRIES if name.endswith(".lock"))
                       | RETIRED_PLATFORM_LOCK_NAMES)

VERBATIM_EXEMPT_NAMES: frozenset[str] = frozenset({
    "workspace", "workspaces", ".agent-workspace", "output", "artifacts", "canon",
    "notes", "wiki", "soul_versions",
})
PRUNE_DIR_NAMES: frozenset[str] = frozenset({
    ".git", "node_modules", "__pycache__", ".venv", "venv", ".mypy_cache", ".pytest_cache",
})


def sqlite_family(db_name: str) -> tuple[str, ...]:
    """Main file and SQLite's three possible sidecars, including for a backup."""
    return (db_name, *(db_name + suffix for suffix in ("-wal", "-shm", "-journal")))


# Backup family observed in production 2026-10-02: command-center-cutover/tasks.md,
# task 1, the three .conversation_memory.db.bak-premigrate-* unclassified entries.
_DB_PATTERNS = tuple(
    re.compile(re.escape(name) + r"(?:\.bak-[A-Za-z0-9][A-Za-z0-9._-]*)?(?:-wal|-shm|-journal)?")
    for name in PLATFORM_DB_NAMES
)

#: Authority fields that must leave the agent-editable config.yaml (#4263 D8a):
#: config.py:60-66 and the routing ceiling the router enforces.
CONFIG_AUTHORITY_FIELDS = (
    "engine_assignment_state",
    "engine_assignment_generation",
    "provider_authority_bindings",
    "allowed_providers",
)


def classify(name: str) -> str | None:
    """``user``, ``platform``, or ``None`` when this table does not know it."""
    if host_center_entry(name) or name in PLATFORM_NAMES or name in PLATFORM_LOCK_NAMES:
        return PLATFORM
    if name in USER_NAMES:
        return USER
    if any(pattern.fullmatch(name) for pattern in _DB_PATTERNS):
        return PLATFORM
    return None
