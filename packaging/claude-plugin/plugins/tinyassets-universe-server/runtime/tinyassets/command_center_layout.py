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

USER = "user"
PLATFORM = "platform"

#: Agent- or person-written content (exact entry names in a home).
USER_NAMES = frozenset({
    # brain and harness (AGENT_BRAIN_FILES / AGENT_HARNESS_DIRS)
    "AGENTS.md", "soul.md", "soul_versions", "voice.md", "identity.md", "founder.md",
    "MEMORY.md", "settings.yaml",
    "origin.md", "body.md", "orgchart.md", "projects.md", "goals.md", "index.md",
    "log.md", "skills", "prompts", "extensions", "workflows", "bin", "notes",
    "notes.json", "wiki",
    # agent-editable settings; its authority fields move out (design E6)
    "config.yaml",
    # content the agent or a run wrote, uploads, permanent workspaces:
    # `workspace` is where fantasy_daemon/api.py writes uploaded files, and
    # `.agent-workspace` is the agent's own (provider_jail.py AGENT_WORKSPACE_DIR;
    # storage_accounting calls it user bytes, harness W2)
    "workspace", "workspaces", ".agent-workspace",
    # the owner's provider-exec workspace and native sessions (D88)
    ".provider-workspace",
    "canon", "output", "artifacts", "PROGRAM.md", "progress.md",
    "design-proposals", "feature-requests", "patch-requests",
    # fiction-domain brain data the agent maintains
    "timeline.json", "promises.json", "facts.json", "characters.json",
})

#: State the daemon trusts (exact entry names in a home).
PLATFORM_NAMES = frozenset({
    # trusted policies
    "soul.edit.md", "dispatcher_config.yaml",
    # records, ledgers, status, assignment and routing
    "activity.log", "status.json", "universe.json", "work_targets.json",
    "ledger.json", "provider_definitions.json", "hard_priorities.json",
    "requests.json", "subscriptions.json", "branch_tasks.json",
    "branch_tasks_archive.json", "auto_ship_attempts.jsonl", "bid_ledger.json",
    "bid_execution_log.json", "enrichment_signals.json", "worldbuild_signals.json",
    "platform-expected-instance.json", "uptime-probe", "executions", "runs",
    "reviews", "settlements", "discarded_targets", "archived",
    "app_refresh_sessions", "import", "import-verify", "verify",
    # stores
    "lancedb",
    # the provider authority record the tool jail never maps
    # (provider_authority.py _DIR / write_record)
    ".provider-authority",
    # credentials and runtime
    ".credentials", ".credentials.json", ".credential-vault.json", ".oauth-refresh", ".runtime",
    ".runtime_status.json", ".engine_mcp_config.json", ".engine_mcp_http_routes.json",
    ".pause", ".agent-sessions", ".consumer_liveness", ".quarantine",
    ".workspace-staging", ".authoring_blobs", ".tinyassets_auth_probe.json",
    ".idle_cycle_stamp.json",
    # the queue consumer's heartbeat (api/universe.py _WORKER_SUPERVISOR_FILENAME,
    # runtime/assigned_queue_consumer.py SUPERVISOR_HEARTBEAT_FILENAME)
    ".worker_supervisor.json",
})

#: storage_accounting.UNIVERSE_ENTRIES; rules/steering in ROOT_ENTRIES.
PLATFORM_DB_NAMES: frozenset[str] = frozenset({
    ".conversation_memory.db", ".conversation_attention.db", ".subscription_state.db",
    ".pending_requests.db", ".usage_ledger.db", ".wiki_write_back_destination_markers.db",
    ".authoring.db", ".effector_consents.db", ".external_write_receipts.db",
    ".idempotency.db", "rules.db", "steering.db",
})

#: Locks a creator writes as an entry OF a home: the admission lock
#: provider_assignment.py joins onto the universe directory, and
#: SOUL_LOCK_FILENAME (soul_edit.py). Bare lock is NOT here: its creator puts it
#: inside a credential snapshot directory beside auth.json (credential_vault.py),
#: so a home holding one at the top level is something nobody writes, and stays
#: unknown. (Spelled without a path join on purpose -- test_storage_registry
#: _complete scrapes this directory's source for `dir / "<dotted name>"` and
#: would read the example as a real on-disk name nobody accounts for.)
PLATFORM_LOCK_NAMES: frozenset[str] = frozenset({
    ".provider-assignment-admission.lock", ".soul.lock",
})

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

#: The ONE name family with a creator that mints the middle segment per worker:
#: ``_WORKER_SUPERVISOR_PREFIX`` + an assignment key + ``_WORKER_SUPERVISOR_SUFFIX``
#: (api/universe.py). Production holds hundreds of these; an exact-name table
#: cannot enumerate a minted key, so the pattern is the provenance.
_MINTED_PATTERNS = (re.compile(r"\.worker_supervisor\.[^.][^/\\]*\.json"),)

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
    if name in PLATFORM_NAMES or name in PLATFORM_LOCK_NAMES:
        return PLATFORM
    if name in USER_NAMES:
        return USER
    if any(pattern.fullmatch(name) for pattern in _DB_PATTERNS + _MINTED_PATTERNS):
        return PLATFORM
    return None
