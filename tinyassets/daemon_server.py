"""SQLite-backed multiplayer daemon-server substrate.

R7 split in progress. Shared helpers live in ``tinyassets/storage/``
per the Module Layout commitment (PLAN.md §Module Layout). This
module still hosts the bounded-context functions + schema migration
entry point; those move to ``tinyassets/storage/{accounts, daemons,
universes_branches, requests_votes, notes_work_targets, goals_gates}.py``
in follow-up commits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import sqlite3
import threading
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from tinyassets.principals import named_principal
from tinyassets.storage import (  # noqa: F401  (re-exports for in-flight R7 split; unused-in-this-module symbols are still imported by external callers of `tinyassets.daemon_server`)
    ALL_CAPABILITIES,
    CAP_ASSIGN_RUNTIME_PROVIDER,
    CAP_DEFINE_GATE_LADDER,
    CAP_EDIT_UNIVERSE_RULES,
    CAP_FORK_BRANCH,
    CAP_GRANT_CAPABILITIES,
    CAP_PAUSE_RESUME_SERVER,
    CAP_PROMOTE_BRANCH,
    CAP_PROPOSE_AUTHOR_FORK,
    CAP_READ_PUBLIC_UNIVERSE,
    CAP_RESOLVE_VOTE,
    CAP_RETRACT_GATE_CLAIM,
    CAP_ROLLBACK_BRANCH,
    CAP_SET_CANONICAL_BRANCH,
    CAP_SET_GOAL_SELECTOR,
    CAP_SPAWN_RUNTIME_CAPACITY,
    CAP_SUBMIT_REQUEST,
    CAP_SUPERSEDE_BRANCH,
    DB_FILENAME,
    DEFAULT_BRANCH_MODE,
    DEFAULT_QUICK_VOTE_SECONDS,
    DEFAULT_USER_CAPABILITIES,
    SESSION_PREFIX,
    _account_id_for_username,
    _connect,
    _json_dumps,
    _json_loads,
    _now,
    _slugify,
    actor_has_capability,
    base_path_from_universe,
    create_or_update_account,
    create_session,
    db_path,
    ensure_host_account,
    get_account,
    grant_capabilities,
    list_accounts,
    list_capabilities,
    resolve_bearer_token,
    universe_id_from_path,
)
from tinyassets.universe_files import (
    MAX_CONFIG_BYTES,
    MAX_PLATFORM_FILE_BYTES,
    read_data_path,
    write_data_path,
)

# The symbols above are the shared-helpers surface. Re-exported here
# so existing `tinyassets.daemon_server` callers keep working without
# changing imports during the in-flight R7 split. These re-exports
# delete in the final R7 commit when callers migrate to
# `tinyassets.storage.<context>`.


_AUTHOR_SERVER_INIT_LOCK = threading.Lock()
_AUTHOR_SERVER_INITIALIZED: set[str] = set()


def initialize_author_server(base_path: str | Path) -> Path:
    """Ensure the host-level daemon-server database exists and is migrated.

    Idempotent AND concurrency-safe. The schema creation + the check-then-ALTER
    migrations below are not atomic across threads: two concurrent first-contact
    ``get_status`` calls on a fresh DB otherwise race into ``duplicate column
    name`` (both pass the ``PRAGMA table_info`` check, then both ``ALTER``) or
    ``database is locked``. A process-level lock + a per-base "already
    initialized" guard runs the migration exactly once per base path, so callers
    on the ASGI worker threads serialize through it before touching any table.
    (Auto-birth on first connect made this reachable — get_status now hits the DB
    concurrently on a brand-new install.)
    """
    key = str(db_path(base_path))
    if key in _AUTHOR_SERVER_INITIALIZED:
        return db_path(base_path)
    with _AUTHOR_SERVER_INIT_LOCK:
        if key not in _AUTHOR_SERVER_INITIALIZED:
            _initialize_author_server_locked(base_path)
            _AUTHOR_SERVER_INITIALIZED.add(key)
    return db_path(base_path)


def _initialize_author_server_locked(base_path: str | Path) -> Path:
    """Create the schema + run migrations once. Callers hold the init lock."""
    schema = """
    CREATE TABLE IF NOT EXISTS universes (
        universe_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL,
        host_path TEXT NOT NULL,
        created_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS universe_rules (
        universe_id TEXT PRIMARY KEY,
        public_read INTEGER NOT NULL DEFAULT 1,
        public_fork INTEGER NOT NULL DEFAULT 1,
        branch_mode TEXT NOT NULL DEFAULT 'no_fixed_mainline',
        quick_vote_seconds INTEGER NOT NULL DEFAULT 300,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(universe_id) REFERENCES universes(universe_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS user_accounts (
        user_id TEXT PRIMARY KEY,
        username TEXT NOT NULL UNIQUE COLLATE NOCASE,
        display_name TEXT NOT NULL,
        is_active INTEGER NOT NULL DEFAULT 1,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS user_sessions (
        session_token TEXT PRIMARY KEY,
        user_id TEXT NOT NULL,
        created_at REAL NOT NULL,
        last_seen REAL NOT NULL,
        expires_at REAL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        FOREIGN KEY(user_id) REFERENCES user_accounts(user_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS capability_grants (
        user_id TEXT NOT NULL,
        capability TEXT NOT NULL,
        scope TEXT NOT NULL DEFAULT '*',
        granted_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        expires_at REAL,
        revoked_at REAL,
        generation INTEGER NOT NULL DEFAULT 1 CHECK (generation >= 1),
        PRIMARY KEY(user_id, capability, scope, generation),
        CHECK (expires_at IS NULL OR expires_at > created_at),
        CHECK (revoked_at IS NULL OR revoked_at >= created_at),
        FOREIGN KEY(user_id) REFERENCES user_accounts(user_id) ON DELETE CASCADE
    );

    CREATE TABLE IF NOT EXISTS author_definitions (
        author_id TEXT PRIMARY KEY,
        display_name TEXT NOT NULL UNIQUE COLLATE NOCASE,
        soul_text TEXT NOT NULL,
        soul_hash TEXT NOT NULL,
        lineage_parent_id TEXT,
        reputation_score REAL NOT NULL DEFAULT 0.0,
        created_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS author_forks (
        fork_id TEXT PRIMARY KEY,
        parent_author_id TEXT NOT NULL,
        child_author_id TEXT NOT NULL,
        proposed_by TEXT NOT NULL,
        vote_id TEXT,
        reason TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS author_runtime_instances (
        instance_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        author_id TEXT NOT NULL,
        provider_name TEXT NOT NULL,
        model_name TEXT NOT NULL,
        branch_id TEXT,
        status TEXT NOT NULL,
        created_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS branches (
        branch_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        name TEXT NOT NULL,
        parent_branch_id TEXT,
        is_public INTEGER NOT NULL DEFAULT 1,
        status TEXT NOT NULL DEFAULT 'active',
        created_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}',
        UNIQUE(universe_id, name)
    );

    CREATE TABLE IF NOT EXISTS branch_heads (
        branch_id TEXT PRIMARY KEY,
        snapshot_id TEXT,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS universe_snapshots (
        snapshot_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        branch_id TEXT NOT NULL,
        label TEXT NOT NULL,
        artifact_ref TEXT NOT NULL DEFAULT '',
        created_by TEXT NOT NULL,
        created_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS user_requests (
        request_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        branch_id TEXT,
        user_id TEXT NOT NULL,
        request_type TEXT NOT NULL,
        text TEXT NOT NULL,
        preferred_author_id TEXT,
        status TEXT NOT NULL DEFAULT 'open',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL,
        metadata_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS vote_windows (
        vote_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        vote_type TEXT NOT NULL,
        subject_type TEXT NOT NULL,
        subject_id TEXT NOT NULL,
        created_by TEXT NOT NULL,
        opens_at REAL NOT NULL,
        closes_at REAL NOT NULL,
        status TEXT NOT NULL DEFAULT 'open',
        payload_json TEXT NOT NULL DEFAULT '{}',
        result_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS vote_ballots (
        vote_id TEXT NOT NULL,
        user_id TEXT NOT NULL,
        choice TEXT NOT NULL,
        comment TEXT NOT NULL DEFAULT '',
        created_at REAL NOT NULL,
        PRIMARY KEY(vote_id, user_id)
    );

    CREATE TABLE IF NOT EXISTS action_records (
        action_id TEXT PRIMARY KEY,
        universe_id TEXT,
        visibility TEXT NOT NULL,
        actor_type TEXT NOT NULL,
        actor_id TEXT NOT NULL,
        action_type TEXT NOT NULL,
        target_type TEXT NOT NULL,
        target_id TEXT NOT NULL,
        summary TEXT NOT NULL,
        created_at REAL NOT NULL,
        payload_json TEXT NOT NULL DEFAULT '{}'
    );

    CREATE TABLE IF NOT EXISTS universe_notes (
        note_id TEXT PRIMARY KEY,
        universe_id TEXT NOT NULL,
        source TEXT NOT NULL,
        text TEXT NOT NULL,
        category TEXT NOT NULL,
        status TEXT NOT NULL,
        target TEXT,
        clearly_wrong INTEGER NOT NULL DEFAULT 0,
        quoted_passage TEXT NOT NULL DEFAULT '',
        tags_json TEXT NOT NULL DEFAULT '[]',
        metadata_json TEXT NOT NULL DEFAULT '{}',
        timestamp REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE TABLE IF NOT EXISTS universe_work_targets (
        universe_id TEXT NOT NULL,
        target_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(universe_id, target_id)
    );

    CREATE TABLE IF NOT EXISTS universe_hard_priorities (
        universe_id TEXT NOT NULL,
        priority_id TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        updated_at REAL NOT NULL,
        PRIMARY KEY(universe_id, priority_id)
    );

    CREATE TABLE IF NOT EXISTS branch_definitions (
        branch_def_id TEXT PRIMARY KEY,
        name TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        author TEXT NOT NULL DEFAULT '',
        domain_id TEXT NOT NULL DEFAULT 'workflow',
        tags_json TEXT NOT NULL DEFAULT '[]',
        version INTEGER NOT NULL DEFAULT 1,
        skills_json TEXT NOT NULL DEFAULT '[]',
        parent_def_id TEXT,
        entry_point TEXT NOT NULL DEFAULT '',  -- also in graph_json for export/fork
        graph_json TEXT NOT NULL DEFAULT '{}',
        node_defs_json TEXT NOT NULL DEFAULT '[]',
        state_schema_json TEXT NOT NULL DEFAULT '[]',
        published INTEGER NOT NULL DEFAULT 0,
        stats_json TEXT NOT NULL DEFAULT '{}',
        created_at REAL NOT NULL,
        updated_at REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_branch_defs_author
        ON branch_definitions(author);
    CREATE INDEX IF NOT EXISTS idx_branch_defs_published
        ON branch_definitions(published);
    CREATE INDEX IF NOT EXISTS idx_branch_defs_parent
        ON branch_definitions(parent_def_id);
    CREATE INDEX IF NOT EXISTS idx_branch_defs_domain
        ON branch_definitions(domain_id);

    -- Phase 5: Goal as first-class shared primitive.
    -- Flat namespace; users propose Goals freely. Soft-delete via
    -- visibility='deleted'. See docs/specs/community_branches_phase5.md.
    CREATE TABLE IF NOT EXISTS goals (
        goal_id     TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        author      TEXT NOT NULL DEFAULT '',
        tags_json   TEXT NOT NULL DEFAULT '[]',
        visibility  TEXT NOT NULL DEFAULT 'public',
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL
    );

    CREATE INDEX IF NOT EXISTS idx_goals_author ON goals(author);
    CREATE INDEX IF NOT EXISTS idx_goals_visibility ON goals(visibility);

    CREATE TABLE IF NOT EXISTS gate_claims (
        claim_id          TEXT PRIMARY KEY,
        branch_def_id     TEXT NOT NULL,
        goal_id           TEXT NOT NULL,
        rung_key          TEXT NOT NULL,
        evidence_url      TEXT NOT NULL,
        evidence_note     TEXT NOT NULL DEFAULT '',
        conformance_pack_id TEXT NOT NULL DEFAULT '',
        claimed_by        TEXT NOT NULL,
        claimed_at        TEXT NOT NULL,
        retracted_at      TEXT,
        retracted_reason  TEXT NOT NULL DEFAULT '',
        UNIQUE (branch_def_id, rung_key)
    );

    CREATE INDEX IF NOT EXISTS idx_gate_claims_goal
        ON gate_claims(goal_id);
    CREATE INDEX IF NOT EXISTS idx_gate_claims_branch
        ON gate_claims(branch_def_id);

    CREATE TABLE IF NOT EXISTS unreconciled_writes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        recorded_at  TEXT NOT NULL,
        helper_name  TEXT NOT NULL,
        paths_json   TEXT NOT NULL,
        row_ref      TEXT NOT NULL DEFAULT '',
        git_error    TEXT NOT NULL DEFAULT ''
    );
    CREATE INDEX IF NOT EXISTS idx_unreconciled_writes_at
        ON unreconciled_writes(recorded_at);

    -- Variant canonicals (Task #61 Step 0). See
    -- docs/design-notes/2026-04-25-variant-canonicals-proposal.md.
    -- Composite PK enforces "one canonical per (goal, scope)". scope_token
    -- is opaque: '' = default/unscoped, 'user:<actor_id>' = personal,
    -- 'tier:<tier>' / 'team:<team>' = future. The legacy
    -- goals.canonical_branch_version_id column stays as-is; this table is
    -- additive-only until Step 2 (dual-write) lands.
    -- Note: branch_version_id is NOT a FK because branch_versions lives in
    -- the runs database, not this one. Validation of branch_version_id
    -- existence happens at write time via tinyassets.branch_versions API
    -- (matches how goals.canonical_branch_version_id behaves today).
    CREATE TABLE IF NOT EXISTS canonical_bindings (
        goal_id            TEXT NOT NULL,
        scope_token        TEXT NOT NULL DEFAULT '',
        branch_version_id  TEXT NOT NULL,
        bound_by_actor_id  TEXT NOT NULL,
        bound_at           REAL NOT NULL,
        visibility         TEXT NOT NULL DEFAULT 'public',
        PRIMARY KEY (goal_id, scope_token),
        FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
    );
    CREATE INDEX IF NOT EXISTS idx_canonical_bindings_goal
        ON canonical_bindings(goal_id);
    CREATE INDEX IF NOT EXISTS idx_canonical_bindings_actor
        ON canonical_bindings(bound_by_actor_id);
    CREATE INDEX IF NOT EXISTS idx_canonical_bindings_branch_ver
        ON canonical_bindings(branch_version_id);
    CREATE INDEX IF NOT EXISTS idx_canonical_bindings_scope_goal
        ON canonical_bindings(scope_token, goal_id);

    -- Actor-scoped Goal canonicals. Empty scope_actor is the Goal default;
    -- non-empty values are exact authenticated actor IDs. The legacy
    -- goals.canonical_branch_version_id column remains during transition.
    CREATE TABLE IF NOT EXISTS goal_canonicals (
        goal_id            TEXT NOT NULL,
        scope_actor        TEXT NOT NULL DEFAULT '',
        branch_version_id  TEXT NOT NULL,
        set_at             REAL NOT NULL,
        set_by             TEXT NOT NULL,
        PRIMARY KEY (goal_id, scope_actor),
        FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
    );
    CREATE INDEX IF NOT EXISTS idx_goal_canonicals_branch_version
        ON goal_canonicals(branch_version_id);

    -- Memory-scope Stage 2a: per-universe access control list.
    -- A universe with zero rows is public; a universe with at least
    -- one row is private and only listed actors can access it.
    -- ``permission`` is one of {'read', 'write', 'admin'}.
    -- ``actor_id`` = user login (host Q1 answer 2026-04-15).
    -- Enforcement lands in Stage 2b; this table is infrastructure
    -- only for the 2a landing.
    CREATE TABLE IF NOT EXISTS universe_acl (
        universe_id  TEXT NOT NULL,
        actor_id     TEXT NOT NULL,
        permission   TEXT NOT NULL,
        granted_at   REAL NOT NULL,
        granted_by   TEXT NOT NULL DEFAULT '',
        PRIMARY KEY (universe_id, actor_id)
    );
    CREATE INDEX IF NOT EXISTS idx_universe_acl_actor
        ON universe_acl(actor_id);

    -- First-contact home binding (D10): the one universe an authenticated
    -- founder (WorkOS `sub`) is bound to as their home. A founder may hold
    -- admin ACL on many universes, so "home" is an explicit binding, not the
    -- ACL. Resolves default MCP entry per-founder instead of the host-global
    -- `.active_universe` marker.
    CREATE TABLE IF NOT EXISTS founder_home (
        founder_sub        TEXT PRIMARY KEY,
        universe_id        TEXT NOT NULL,
        created_at         REAL NOT NULL,
        -- universe-creation 5.2 provenance marker: 1 iff this universe_id was
        -- generated by the platform (via new_universe_id() inside
        -- claim_founder_home, or an id _action_create_universe generated
        -- itself), 0 iff it is a caller-influenced value. First-contact may
        -- only materialize a value proven platform-generated; format shape
        -- alone (is_universe_serial) does NOT prove generation provenance.
        platform_generated INTEGER NOT NULL DEFAULT 0
    );

    -- Account-deletion tombstone. A deleted principal's own JWT stays valid at
    -- this daemon until it expires and until the identity provider removes the
    -- user, so without this row a second device could walk straight back into
    -- first-contact and be handed a brand-new universe seconds after the person
    -- deleted their account. `ensure_founder_home` refuses a tombstoned
    -- principal. Cleared by the operator's scoped identity reset, which
    -- deliberately keeps the login.
    -- `founder_sub` here holds a ONE-WAY DIGEST of the principal, never the
    -- principal: this row outlives the account, and keeping the WorkOS user id
    -- of someone who asked to be deleted would contradict what /account says.
    CREATE TABLE IF NOT EXISTS deleted_principals (
        founder_sub TEXT PRIMARY KEY,
        deleted_at  REAL NOT NULL
    );
    """
    with _connect(base_path) as conn:
        conn.executescript(schema)
        # NOT in the schema script: `executescript` autocommits, so the table
        # would exist before its backfill committed, and a crash between the two
        # would skip the backfill forever (gpt-6-astra, PR #4139). The table and
        # its backfill are born in ONE transaction.
        from tinyassets.universe_owner import migrate_universe_owner

        migrate_universe_owner(conn)
        from tinyassets.storage.accounts import (
            migrate_capability_grants_schema,
        )
        from tinyassets.storage.request_admissions import (
            migrate_request_admission_schema,
        )

        migrate_capability_grants_schema(conn)
        migrate_request_admission_schema(conn)
        # Phase 5 migration: branch_definitions.goal_id column. Older
        # installs predate Phase 5. SQLite lacks ADD COLUMN IF NOT EXISTS,
        # so probe table_info first. Nullable so existing rows stay
        # valid; index on goal_id for leaderboard/list filters.
        existing_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(branch_definitions)")
        }
        if "goal_id" not in existing_cols:
            conn.execute(
                "ALTER TABLE branch_definitions ADD COLUMN goal_id TEXT"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_branch_defs_goal "
            "ON branch_definitions(goal_id)"
        )
        # Phase 6.2.2 migration: branch_definitions.visibility column.
        # Mirrors the Goals visibility pattern at :347. Default 'public'
        # so existing rows stay behaviorally unchanged; users opt into
        # private explicitly. Index for the filter helpers.
        if "visibility" not in existing_cols:
            conn.execute(
                "ALTER TABLE branch_definitions ADD COLUMN visibility "
                "TEXT NOT NULL DEFAULT 'public'"
            )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_branch_defs_visibility "
            "ON branch_definitions(visibility)"
        )
        # Branch-carried skill snapshots. These are user-authored or
        # copied instruction/rubric artifacts that travel with forks.
        if "skills_json" not in existing_cols:
            conn.execute(
                "ALTER TABLE branch_definitions ADD COLUMN skills_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        # Phase 6 migration: goals.gate_ladder_json inline ladder column.
        goal_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(goals)")
        }
        if "gate_ladder_json" not in goal_cols:
            conn.execute(
                "ALTER TABLE goals ADD COLUMN gate_ladder_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        # PR-129: ordered branch-family protocol/runbook on a Goal.
        # Stored as JSON so it composes with existing Goal + Branch
        # primitives without introducing a new top-level object.
        if "branch_protocol_json" not in goal_cols:
            conn.execute(
                "ALTER TABLE goals ADD COLUMN branch_protocol_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        gate_claim_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(gate_claims)")
        }
        if "conformance_pack_id" not in gate_claim_cols:
            conn.execute(
                "ALTER TABLE gate_claims ADD COLUMN conformance_pack_id "
                "TEXT NOT NULL DEFAULT ''"
            )
        # universe-creation 5.2: founder_home.platform_generated provenance
        # marker. Existing rows default to 0 (provenance unknown → first-contact
        # fails closed until a host-run migration backfills known
        # platform-generated serials). Complete homes early-return before the
        # provenance gate, so this does not disturb live founders; only an
        # incomplete/stale binding is gated.
        founder_home_cols = {
            row["name"]
            for row in conn.execute("PRAGMA table_info(founder_home)")
        }
        if "platform_generated" not in founder_home_cols:
            conn.execute(
                "ALTER TABLE founder_home ADD COLUMN platform_generated "
                "INTEGER NOT NULL DEFAULT 0"
            )
        # Branch-level execution choices. Additive and nullable: NULL means
        # unset/inherit, byte-identical to the pre-migration behaviour, so no
        # existing row is rewritten or backfilled. No index — neither field is
        # a list/filter key. These columns are the ONLY home for the two
        # facts; they are deliberately not also folded into graph_json.
        #
        # Rollback note: `save_branch_definition` uses INSERT OR REPLACE, so an
        # older image whose writer omits these columns would ERASE their values
        # on the next write. Additive columns do not by themselves make a
        # rollback data-preserving. Repair forward; if an older image must be
        # restored, keep a database backup and block definition writes until a
        # compatible writer is back. Never revert the schema.
        if "default_llm_policy_json" not in existing_cols:
            conn.execute(
                "ALTER TABLE branch_definitions ADD COLUMN "
                "default_llm_policy_json TEXT"
            )
        if "concurrency_budget" not in existing_cols:
            conn.execute(
                "ALTER TABLE branch_definitions ADD COLUMN "
                "concurrency_budget INTEGER"
            )
        # fork_from migration: content-addressed lineage tracking.
        if "fork_from" not in existing_cols:
            conn.execute(
                "ALTER TABLE branch_definitions ADD COLUMN fork_from TEXT DEFAULT NULL"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_branch_defs_fork_from "
                "ON branch_definitions(fork_from)"
            )
        # canonical_branch migration: first-experience fork target per Goal.
        if "canonical_branch_version_id" not in goal_cols:
            conn.execute(
                "ALTER TABLE goals ADD COLUMN canonical_branch_version_id "
                "TEXT DEFAULT NULL"
            )
        if "canonical_branch_history_json" not in goal_cols:
            conn.execute(
                "ALTER TABLE goals ADD COLUMN canonical_branch_history_json "
                "TEXT NOT NULL DEFAULT '[]'"
            )
        # PR-127 (M6 cutover Steps 4 + 7) — leaderboard-driven canonical
        # selection. ``auto_canonical_via_leaderboard``: when set, every
        # ``goals action=run_canonical`` call first re-queries
        # ``recommended_parent_for_fork`` and updates the stored
        # ``canonical_branch_version_id`` to the freshest top-ranked
        # entry before dispatching. Default OFF so existing Goals are
        # opt-in. ``min_completed_runs_for_canonical``: security gate
        # against a malicious branch ranking high with zero actual
        # runs; default 5. Both columns are stored as integers (SQLite
        # has no native BOOLEAN).
        if "auto_canonical_via_leaderboard" not in goal_cols:
            conn.execute(
                "ALTER TABLE goals ADD COLUMN auto_canonical_via_leaderboard "
                "INTEGER NOT NULL DEFAULT 0"
            )
        if "min_completed_runs_for_canonical" not in goal_cols:
            conn.execute(
                "ALTER TABLE goals ADD COLUMN min_completed_runs_for_canonical "
                "INTEGER NOT NULL DEFAULT 5"
            )
        # DESIGN-008: user-buildable selector primitive. A Goal can
        # bind a "selector branch" (a published TinyAssets branch) that
        # the substrate dispatches whenever the leaderboard is built;
        # the selector reads input signals + emits ``ranked_entries``,
        # replacing the round-1 platform-opinionated formula.
        #
        # NULL is the post-migration "use platform default" state.
        # The default selector branch is materialized lazily on first
        # leaderboard read (see
        # ``tinyassets.api.selector_dispatch.ensure_default_selector_published``)
        # and stored under a deterministic branch_def_id; the migration
        # at the bottom of this function backfills the column for any
        # Goal that has bound branches.
        if "selector_branch_version_id" not in goal_cols:
            conn.execute(
                "ALTER TABLE goals ADD COLUMN selector_branch_version_id "
                "TEXT DEFAULT NULL"
            )
        # Variant canonicals (Task #61 Step 1) — backfill canonical_bindings
        # from existing goals.canonical_branch_version_id. INSERT OR IGNORE
        # makes the migration idempotent: re-running on an already-backfilled
        # DB hits the (goal_id, scope_token='') primary key collision and
        # skips. Goals with NULL canonical_branch_version_id are excluded.
        conn.execute(
            "INSERT OR IGNORE INTO canonical_bindings "
            "(goal_id, scope_token, branch_version_id, "
            " bound_by_actor_id, bound_at, visibility) "
            "SELECT goal_id, '', canonical_branch_version_id, "
            "       author, updated_at, 'public' "
            "FROM goals "
            "WHERE canonical_branch_version_id IS NOT NULL"
        )
        # Preserve every existing Goal default as the empty-actor row.
        # INSERT OR IGNORE keeps retries idempotent and never overwrites
        # a newer row written by the dual-write path.
        conn.execute(
            "INSERT OR IGNORE INTO goal_canonicals "
            "(goal_id, scope_actor, branch_version_id, set_at, set_by) "
            "SELECT goal_id, '', canonical_branch_version_id, updated_at, author "
            "FROM goals "
            "WHERE canonical_branch_version_id IS NOT NULL"
        )
    ensure_default_author(base_path)
    return db_path(base_path)


def _author_id_for(display_name: str, soul_text: str) -> tuple[str, str]:
    soul_hash = hashlib.sha256(soul_text.encode("utf-8")).hexdigest()
    author_id = f"author::{_slugify(display_name, 'author')}::{soul_hash[:16]}"
    return author_id, soul_hash


def _branch_id_for(universe_id: str, name: str, *, suffix: str = "") -> str:
    branch_id = f"branch::{universe_id}::{_slugify(name, 'branch')}"
    if suffix:
        branch_id = f"{branch_id}::{suffix}"
    return branch_id


def ensure_default_author(base_path: str | Path) -> dict[str, Any]:
    with _connect(base_path) as conn:
        existing = conn.execute(
            "SELECT * FROM author_definitions ORDER BY created_at LIMIT 1"
        ).fetchone()
    if existing is not None:
        return _author_from_row(existing)
    return register_author(
        base_path,
        display_name="House Daemon",
        soul_text="Default house daemon for the host-run command center server.",
        created_by="system",
        metadata={"auto_created": True},
    )


def ensure_universe_registered(
    base_path: str | Path,
    *,
    universe_id: str,
    universe_path: str | Path,
    display_name: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    initialize_author_server(base_path)
    now = _now()
    with _connect(base_path) as conn:
        existing = conn.execute(
            "SELECT created_at FROM universes WHERE universe_id = ?",
            (universe_id,),
        ).fetchone()
        created_at = float(existing["created_at"]) if existing is not None else now
        conn.execute(
            """
            INSERT INTO universes (
                universe_id, display_name, host_path, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(universe_id) DO UPDATE SET
                display_name=excluded.display_name,
                host_path=excluded.host_path,
                metadata_json=excluded.metadata_json
            """,
            (
                universe_id,
                display_name or universe_id,
                str(Path(universe_path).resolve()),
                created_at,
                _json_dumps(metadata or {}),
            ),
        )
    ensure_universe_rules(base_path, universe_id=universe_id)
    ensure_default_branch(base_path, universe_id=universe_id)
    return get_universe(base_path, universe_id=universe_id)


def register_universe_if_absent(
    base_path: str | Path,
    *,
    universe_id: str,
    universe_path: str | Path | None = None,
) -> bool:
    """Make sure a universe has a registry row, WITHOUT touching an existing one.

    Returns ``True`` when this call inserted the row. Use this, not
    :func:`ensure_universe_registered`, whenever you need the universe to exist and
    have nothing to say about its NAME.

    ``ensure_universe_registered`` is an UPSERT whose conflict clause is
    ``display_name=excluded.display_name, metadata_json=excluded.metadata_json``,
    and both of those parameters are optional. Calling it for an ALREADY-registered
    universe without passing them therefore **destroys** them: the display name
    becomes the raw ``universe_id`` and the registry metadata becomes ``{}``, and
    the call reports success. It is the odd one out in this module —
    :func:`ensure_universe_rules` and :func:`ensure_default_branch` both use
    ``DO NOTHING`` and preserve what is already there.

    That is not hypothetical, and it was not rare. The visibility backfill did it
    for every discovered universe on every boot, so a universe its owner had named
    lost that name at the next restart. The notes, work-target and hard-priority
    helpers in this module did it on every call, three of them on READS — so
    listing a universe's notes renamed it.

    **A single atomic statement, deliberately.** An earlier cut checked for the row
    and then called ``ensure_universe_registered`` when absent, which left a window:
    between the check and the write another caller could register the universe WITH
    a name, and this call's UPSERT would then erase it. Reproduced by the
    cross-family review of PR #4045 (two threads, pause after the absence check) and
    judged floor-class, because registry metadata has no other copy in this write
    path. ``INSERT ... ON CONFLICT DO NOTHING`` has no such window: a row that
    appears in the meantime simply wins.

    Registration is all those callers need. Renaming has its own caller,
    :func:`set_universe_display_name`, so "never touch an existing row" loses
    nothing.

    ``universe_path`` defaults to ``base_path / universe_id``. Pass it when you
    already hold the real path rather than letting this re-derive one.
    """
    uid = str(universe_id or "").strip()
    if not uid:
        return False
    initialize_author_server(base_path)
    resolved = universe_path if universe_path is not None else Path(base_path) / uid
    with _connect(base_path) as conn:
        cursor = conn.execute(
            """
            INSERT INTO universes (
                universe_id, display_name, host_path, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, '{}')
            ON CONFLICT(universe_id) DO NOTHING
            """,
            (uid, uid, str(Path(resolved).resolve()), _now()),
        )
        inserted = cursor.rowcount == 1
    # Always, not only when inserted: these are the rows that make the registry row
    # usable, `universe_rules` is what actually carries the FK onto `universes`, and
    # both are `DO NOTHING` inserts that preserve anything already recorded. Running
    # them unconditionally also repairs a universe whose row exists without them.
    ensure_universe_rules(base_path, universe_id=uid)
    ensure_default_branch(base_path, universe_id=uid)
    return inserted


def set_universe_display_name(
    base_path: str | Path,
    *,
    universe_id: str,
    display_name: str,
) -> None:
    """Project a learned self-name onto the universe index row's display name.

    universe-creation task 5.3: the root universe index is keyed by the
    immutable ``universe_id``. An accepted learned-name change in the
    universe's ``identity.md`` updates ONLY the ``display_name`` projection for
    that same row — the key, ``created_at``, ``host_path``, and runtime
    operation id are untouched. A no-op when no index row exists for the id.
    """
    name = (display_name or "").strip()
    if not name:
        return
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            "UPDATE universes SET display_name = ? WHERE universe_id = ?",
            (name, universe_id),
        )


def sync_universes_from_filesystem(base_path: str | Path) -> None:
    """Index every directory under the data root by path.

    Deliberately NOT filtered by ownership. This table is a path INDEX, not the
    definition of a universe: a self-hoster restoring a universe directory from
    a backup needs it indexed before anything can grant on it. What makes a
    directory a universe is an owner, and that is enforced where it is READ --
    :func:`owned_universe_ids` and every reader routed through it (founder,
    2026-09-02). Indexing an unowned directory shows it to nobody.
    """
    initialize_author_server(base_path)
    root = Path(base_path)
    if not root.exists():
        return
    for entry in root.iterdir():
        if not entry.is_dir() or entry.name.startswith("."):
            continue
        display_name = entry.name
        meta_path = entry / "universe.json"
        try:
            raw_meta = read_data_path(meta_path, max_bytes=MAX_CONFIG_BYTES)
            if raw_meta is not None:
                display_name = str(json.loads(raw_meta.decode("utf-8")).get("name", entry.name))
        except (OSError, ValueError, AttributeError):
            display_name = entry.name
        ensure_universe_registered(
            base_path,
            universe_id=entry.name,
            universe_path=entry,
            display_name=display_name,
        )


def get_universe(base_path: str | Path, *, universe_id: str) -> dict[str, Any]:
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM universes WHERE universe_id = ?",
            (universe_id,),
        ).fetchone()
    if row is None:
        raise KeyError(universe_id)
    result = dict(row)
    result["metadata"] = _json_loads(result.pop("metadata_json", None), {})
    result["rules"] = get_universe_rules(base_path, universe_id=universe_id)
    return result


def ensure_universe_rules(
    base_path: str | Path,
    *,
    universe_id: str,
) -> dict[str, Any]:
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO universe_rules (
                universe_id, public_read, public_fork, branch_mode,
                quick_vote_seconds, updated_at, metadata_json
            ) VALUES (?, 1, 1, ?, ?, ?, '{}')
            ON CONFLICT(universe_id) DO NOTHING
            """,
            (universe_id, DEFAULT_BRANCH_MODE, DEFAULT_QUICK_VOTE_SECONDS, _now()),
        )
    return get_universe_rules(base_path, universe_id=universe_id)


def get_universe_rules(
    base_path: str | Path,
    *,
    universe_id: str,
) -> dict[str, Any]:
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM universe_rules WHERE universe_id = ?",
            (universe_id,),
        ).fetchone()
    if row is None:
        raise KeyError(universe_id)
    result = dict(row)
    result["public_read"] = bool(result["public_read"])
    result["public_fork"] = bool(result["public_fork"])
    result["metadata"] = _json_loads(result.pop("metadata_json", None), {})
    return result


def update_universe_rules(
    base_path: str | Path,
    *,
    universe_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    current = get_universe_rules(base_path, universe_id=universe_id)
    merged_metadata = dict(current.get("metadata", {}))
    merged_metadata.update(dict(updates.get("metadata", {})))
    with _connect(base_path) as conn:
        conn.execute(
            """
            UPDATE universe_rules
            SET public_read = ?, public_fork = ?, branch_mode = ?,
                quick_vote_seconds = ?, updated_at = ?, metadata_json = ?
            WHERE universe_id = ?
            """,
            (
                1 if bool(updates.get("public_read", current["public_read"])) else 0,
                1 if bool(updates.get("public_fork", current["public_fork"])) else 0,
                str(updates.get("branch_mode", current["branch_mode"])),
                int(updates.get("quick_vote_seconds", current["quick_vote_seconds"])),
                _now(),
                _json_dumps(merged_metadata),
                universe_id,
            ),
        )
    return get_universe_rules(base_path, universe_id=universe_id)


def ensure_default_branch(base_path: str | Path, *, universe_id: str) -> dict[str, Any]:
    branch_id = _branch_id_for(universe_id, "free-roam")
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO branches (
                branch_id, universe_id, name, parent_branch_id, is_public,
                status, created_by, created_at, updated_at, metadata_json
            ) VALUES (?, ?, 'free-roam', NULL, 1, 'active', 'system', ?, ?, ?)
            ON CONFLICT(branch_id) DO NOTHING
            """,
            (
                branch_id,
                universe_id,
                _now(),
                _now(),
                _json_dumps({"default": True, "branch_mode": DEFAULT_BRANCH_MODE}),
            ),
        )
        conn.execute(
            """
            INSERT INTO branch_heads (branch_id, snapshot_id, updated_at, metadata_json)
            VALUES (?, NULL, ?, '{}')
            ON CONFLICT(branch_id) DO NOTHING
            """,
            (branch_id, _now()),
        )
    return get_branch(base_path, branch_id=branch_id)


def create_branch(
    base_path: str | Path,
    *,
    universe_id: str,
    name: str,
    created_by: str,
    parent_branch_id: str | None = None,
    is_public: bool = True,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    branch_id = _branch_id_for(universe_id, name, suffix=uuid.uuid4().hex[:8])
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO branches (
                branch_id, universe_id, name, parent_branch_id, is_public,
                status, created_by, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, 'active', ?, ?, ?, ?)
            """,
            (
                branch_id,
                universe_id,
                name,
                parent_branch_id,
                1 if is_public else 0,
                created_by,
                _now(),
                _now(),
                _json_dumps(metadata or {}),
            ),
        )
        conn.execute(
            """
            INSERT INTO branch_heads (branch_id, snapshot_id, updated_at, metadata_json)
            VALUES (?, NULL, ?, '{}')
            """,
            (branch_id, _now()),
        )
    return get_branch(base_path, branch_id=branch_id)


def list_universe_forks(
    base_path: str | Path, *, universe_id: str,
) -> list[dict[str, Any]]:
    """List all git-style forks of a universe (the `branches` SQL table).

    Distinct from `list_branch_definitions` — BranchDefinition is the
    community-workflow concept; a universe "branch" here is a snapshot
    lineage with `branch_heads` + `snapshot_id`. Phase A rename
    (2026-04-14) disambiguated the two — the old name `list_branches`
    collided with `_ext_branch_list` / `extensions action=list_branches`
    which operate on BranchDefinitions.
    """
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT b.*, h.snapshot_id
            FROM branches AS b
            LEFT JOIN branch_heads AS h ON h.branch_id = b.branch_id
            WHERE b.universe_id = ?
            ORDER BY b.created_at, b.branch_id
            """,
            (universe_id,),
        ).fetchall()
    return [_branch_from_row(row) for row in rows]


def get_branch(base_path: str | Path, *, branch_id: str) -> dict[str, Any]:
    with _connect(base_path) as conn:
        row = conn.execute(
            """
            SELECT b.*, h.snapshot_id
            FROM branches AS b
            LEFT JOIN branch_heads AS h ON h.branch_id = b.branch_id
            WHERE b.branch_id = ?
            """,
            (branch_id,),
        ).fetchone()
    if row is None:
        raise KeyError(branch_id)
    return _branch_from_row(row)


def mark_branch_status(
    base_path: str | Path,
    *,
    branch_id: str,
    status: str,
) -> dict[str, Any]:
    with _connect(base_path) as conn:
        conn.execute(
            "UPDATE branches SET status = ?, updated_at = ? WHERE branch_id = ?",
            (status, _now(), branch_id),
        )
    return get_branch(base_path, branch_id=branch_id)


def create_snapshot(
    base_path: str | Path,
    *,
    universe_id: str,
    branch_id: str,
    label: str,
    created_by: str,
    artifact_ref: str = "",
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    snapshot_id = f"snapshot::{uuid.uuid4().hex}"
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO universe_snapshots (
                snapshot_id, universe_id, branch_id, label, artifact_ref,
                created_by, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                snapshot_id,
                universe_id,
                branch_id,
                label,
                artifact_ref,
                created_by,
                _now(),
                _json_dumps(metadata or {}),
            ),
        )
        conn.execute(
            """
            INSERT INTO branch_heads (branch_id, snapshot_id, updated_at, metadata_json)
            VALUES (?, ?, ?, '{}')
            ON CONFLICT(branch_id) DO UPDATE SET
                snapshot_id=excluded.snapshot_id,
                updated_at=excluded.updated_at
            """,
            (branch_id, snapshot_id, _now()),
        )
    return get_snapshot(base_path, snapshot_id=snapshot_id)


def set_branch_head(
    base_path: str | Path,
    *,
    branch_id: str,
    snapshot_id: str,
) -> dict[str, Any]:
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO branch_heads (branch_id, snapshot_id, updated_at, metadata_json)
            VALUES (?, ?, ?, '{}')
            ON CONFLICT(branch_id) DO UPDATE SET
                snapshot_id=excluded.snapshot_id,
                updated_at=excluded.updated_at
            """,
            (branch_id, snapshot_id, _now()),
        )
    return get_branch(base_path, branch_id=branch_id)


def get_snapshot(base_path: str | Path, *, snapshot_id: str) -> dict[str, Any]:
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM universe_snapshots WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
    if row is None:
        raise KeyError(snapshot_id)
    result = dict(row)
    result["metadata"] = _json_loads(result.pop("metadata_json", None), {})
    return result


def list_snapshots(
    base_path: str | Path,
    *,
    universe_id: str,
    branch_id: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM universe_snapshots WHERE universe_id = ?"
    params: list[Any] = [universe_id]
    if branch_id:
        query += " AND branch_id = ?"
        params.append(branch_id)
    query += " ORDER BY created_at DESC, snapshot_id DESC"
    with _connect(base_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [
        {**dict(row), "metadata": _json_loads(row["metadata_json"], {})}
        for row in rows
    ]


def register_author(
    base_path: str | Path,
    *,
    display_name: str,
    soul_text: str,
    created_by: str,
    lineage_parent_id: str | None = None,
    metadata: dict[str, Any] | None = None,
    vote_id: str | None = None,
    reason: str = "",
) -> dict[str, Any]:
    author_id, soul_hash = _author_id_for(display_name, soul_text)
    with _connect(base_path) as conn:
        existing = conn.execute(
            "SELECT author_id FROM author_definitions WHERE display_name = ? COLLATE NOCASE",
            (display_name,),
        ).fetchone()
        if existing is not None and str(existing["author_id"]) != author_id:
            raise ValueError(f"Daemon display name already exists: {display_name}")
        conn.execute(
            """
            INSERT INTO author_definitions (
                author_id, display_name, soul_text, soul_hash, lineage_parent_id,
                reputation_score, created_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, 0.0, ?, ?)
            ON CONFLICT(author_id) DO NOTHING
            """,
            (
                author_id,
                display_name,
                soul_text,
                soul_hash,
                lineage_parent_id,
                _now(),
                _json_dumps(metadata or {}),
            ),
        )
        if lineage_parent_id:
            conn.execute(
                """
                INSERT OR IGNORE INTO author_forks (
                    fork_id, parent_author_id, child_author_id, proposed_by,
                    vote_id, reason, created_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    f"author-fork::{uuid.uuid4().hex}",
                    lineage_parent_id,
                    author_id,
                    created_by,
                    vote_id,
                    reason,
                    _now(),
                    _json_dumps(metadata or {}),
                ),
            )
    return get_author(base_path, author_id=author_id)


def list_authors(base_path: str | Path) -> list[dict[str, Any]]:
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT * FROM author_definitions ORDER BY created_at, author_id"
        ).fetchall()
    return [_author_from_row(row) for row in rows]


def get_author(base_path: str | Path, *, author_id: str) -> dict[str, Any]:
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM author_definitions WHERE author_id = ?",
            (author_id,),
        ).fetchone()
        if row is None:
            raise KeyError(author_id)
        lineage = conn.execute(
            """
            SELECT * FROM author_forks
            WHERE parent_author_id = ? OR child_author_id = ?
            ORDER BY created_at
            """,
            (author_id, author_id),
        ).fetchall()
    result = _author_from_row(row)
    result["lineage"] = [
        {**dict(item), "metadata": _json_loads(item["metadata_json"], {})}
        for item in lineage
    ]
    return result


def update_author_metadata(
    base_path: str | Path,
    *,
    author_id: str,
    metadata_patch: dict[str, Any],
) -> dict[str, Any]:
    """Merge metadata onto a daemon/author identity and return the row."""
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT metadata_json FROM author_definitions WHERE author_id = ?",
            (author_id,),
        ).fetchone()
        if row is None:
            raise KeyError(author_id)
        metadata = _json_loads(row["metadata_json"], {})
        metadata.update(metadata_patch)
        conn.execute(
            "UPDATE author_definitions SET metadata_json = ? WHERE author_id = ?",
            (_json_dumps(metadata), author_id),
        )
    return get_author(base_path, author_id=author_id)


def spawn_runtime_instance(
    base_path: str | Path,
    *,
    universe_id: str,
    author_id: str,
    provider_name: str,
    model_name: str,
    created_by: str,
    branch_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    instance_id = f"runtime::{uuid.uuid4().hex}"
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO author_runtime_instances (
                instance_id, universe_id, author_id, provider_name, model_name,
                branch_id, status, created_by, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, 'provisioned', ?, ?, ?, ?)
            """,
            (
                instance_id,
                universe_id,
                author_id,
                provider_name,
                model_name,
                branch_id,
                created_by,
                _now(),
                _now(),
                _json_dumps(metadata or {}),
            ),
        )
    return get_runtime_instance(base_path, instance_id=instance_id)


def ensure_worker_runtime_instance(
    base_path: str | Path,
    *,
    universe_id: str,
    author_id: str,
    provider_name: str,
    model_name: str,
    created_by: str,
    worker_id: str,
    branch_id: str | None = None,
    metadata: dict[str, Any],
) -> dict[str, Any]:
    """Atomically reuse, adopt, or create one stable worker runtime slot."""
    if not worker_id or metadata.get("worker_id") != worker_id:
        raise ValueError("worker_id metadata mismatch")
    with _connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            """
            SELECT * FROM author_runtime_instances
            WHERE universe_id = ?
              AND author_id = ?
              AND provider_name = ?
              AND model_name = ?
            ORDER BY created_at DESC
            """,
            (universe_id, author_id, provider_name, model_name),
        ).fetchall()
        provisioned_match: sqlite3.Row | None = None
        controlled_match: sqlite3.Row | None = None
        adoptable: sqlite3.Row | None = None
        for row in rows:
            row_metadata = _json_loads(row["metadata_json"], {})
            if not isinstance(row_metadata, dict):
                row_metadata = {}
            row_worker = str(row_metadata.get("worker_id") or "")
            if row_worker == worker_id:
                if row["status"] == "provisioned":
                    provisioned_match = row
                    break
                if row["status"] != "retired" and controlled_match is None:
                    controlled_match = row
            elif (
                not row_worker
                and row["status"] == "provisioned"
                and adoptable is None
            ):
                adoptable = row

        if provisioned_match is None and controlled_match is not None:
            raise ValueError(
                f"worker_runtime_not_provisioned:{controlled_match['status']}"
            )
        selected = provisioned_match or adoptable
        if selected is not None:
            selected_metadata = _json_loads(selected["metadata_json"], {})
            if not isinstance(selected_metadata, dict):
                selected_metadata = {}
            selected_metadata.update(metadata)
            conn.execute(
                """
                UPDATE author_runtime_instances
                SET updated_at = ?, metadata_json = ?
                WHERE instance_id = ?
                """,
                (
                    _now(),
                    _json_dumps(selected_metadata),
                    selected["instance_id"],
                ),
            )
            result = conn.execute(
                """
                SELECT * FROM author_runtime_instances
                WHERE instance_id = ?
                """,
                (selected["instance_id"],),
            ).fetchone()
        else:
            instance_id = f"runtime::{uuid.uuid4().hex}"
            now = _now()
            conn.execute(
                """
                INSERT INTO author_runtime_instances (
                    instance_id, universe_id, author_id, provider_name,
                    model_name, branch_id, status, created_by, created_at,
                    updated_at, metadata_json
                ) VALUES (?, ?, ?, ?, ?, ?, 'provisioned', ?, ?, ?, ?)
                """,
                (
                    instance_id,
                    universe_id,
                    author_id,
                    provider_name,
                    model_name,
                    branch_id,
                    created_by,
                    now,
                    now,
                    _json_dumps(metadata),
                ),
            )
            result = conn.execute(
                """
                SELECT * FROM author_runtime_instances
                WHERE instance_id = ?
                """,
                (instance_id,),
            ).fetchone()
    if result is None:  # pragma: no cover - guarded by the transaction writes
        raise RuntimeError("runtime_registration_missing_result")
    return _runtime_from_row(result)


def retire_runtime_instance(
    base_path: str | Path,
    *,
    instance_id: str,
) -> dict[str, Any]:
    with _connect(base_path) as conn:
        return _retire_runtime_instance_locked(conn, instance_id=instance_id)


def retire_runtime_instance_if_stale(
    base_path: str | Path,
    *,
    instance_id: str,
    cutoff: datetime,
    expected_updated_at: float,
    expected_row_digest: str,
) -> dict[str, Any] | None:
    """Retire one exact reviewed stale cloud-worker runtime under CAS."""
    if cutoff.tzinfo is None:
        cutoff = cutoff.replace(tzinfo=timezone.utc)
    cutoff = cutoff.astimezone(timezone.utc)
    with _connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT * FROM author_runtime_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            return None
        metadata = _json_loads(row["metadata_json"], {})
        worker_id = (
            str(metadata.get("worker_id") or "").strip()
            if isinstance(metadata, dict)
            else ""
        )
        if not all((
            row["status"] == "provisioned",
            isinstance(metadata, dict),
            metadata.get("runtime_registration") == "cloud_worker",
            bool(worker_id),
            float(row["updated_at"]) == float(expected_updated_at),
            float(row["updated_at"]) <= cutoff.timestamp(),
            _runtime_reconcile_digest(row) == expected_row_digest,
        )):
            return None
        active_ownership = conn.execute(
            """
            SELECT 1 FROM branch_tasks_v2
            WHERE claimed_by = ?
              AND status IN ('running', 'cancel_requested')
            LIMIT 1
            """,
            (worker_id,),
        ).fetchone()
        if active_ownership is not None:
            return None
        from tinyassets.daemon_registry import _runtime_heartbeat_is_stale

        if not _runtime_heartbeat_is_stale(
            base_path,
            row,
            cutoff=cutoff,
            now=datetime.now(timezone.utc),
        ):
            return None
        return _retire_runtime_instance_locked(conn, instance_id=instance_id)


def _retire_runtime_instance_locked(
    conn: sqlite3.Connection,
    *,
    instance_id: str,
) -> dict[str, Any]:
    conn.execute(
        """
        UPDATE author_runtime_instances
        SET status = 'retired', updated_at = ?
        WHERE instance_id = ?
        """,
        (_now(), instance_id),
    )
    row = conn.execute(
        "SELECT * FROM author_runtime_instances WHERE instance_id = ?",
        (instance_id,),
    ).fetchone()
    if row is None:
        raise KeyError(instance_id)
    return _runtime_from_row(row)


def _runtime_reconcile_digest(row: Mapping[str, Any]) -> str:
    payload = {
        key: row[key]
        for key in (
            "instance_id",
            "universe_id",
            "author_id",
            "provider_name",
            "model_name",
            "branch_id",
            "status",
            "created_by",
            "created_at",
            "updated_at",
            "metadata_json",
        )
    }
    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def update_runtime_instance_status(
    base_path: str | Path,
    *,
    instance_id: str,
    status: str,
    metadata_patch: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Update runtime status and merge control metadata."""
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT metadata_json FROM author_runtime_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
        if row is None:
            raise KeyError(instance_id)
        metadata = _json_loads(row["metadata_json"], {})
        metadata.update(metadata_patch or {})
        conn.execute(
            """
            UPDATE author_runtime_instances
            SET status = ?, updated_at = ?, metadata_json = ?
            WHERE instance_id = ?
            """,
            (status, _now(), _json_dumps(metadata), instance_id),
        )
    return get_runtime_instance(base_path, instance_id=instance_id)


def update_runtime_instance_metadata(
    base_path: str | Path,
    *,
    instance_id: str,
    metadata_patch: dict[str, Any],
    forbidden_statuses: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Atomically merge runtime metadata without changing its control status."""
    with _connect(base_path) as conn:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT status, metadata_json
            FROM author_runtime_instances
            WHERE instance_id = ?
            """,
            (instance_id,),
        ).fetchone()
        if row is None:
            raise KeyError(instance_id)
        if row["status"] in forbidden_statuses:
            raise ValueError(
                f"runtime_status_forbids_metadata_update:{row['status']}"
            )
        metadata = _json_loads(row["metadata_json"], {})
        metadata.update(metadata_patch)
        conn.execute(
            """
            UPDATE author_runtime_instances
            SET updated_at = ?, metadata_json = ?
            WHERE instance_id = ?
            """,
            (_now(), _json_dumps(metadata), instance_id),
        )
    return get_runtime_instance(base_path, instance_id=instance_id)


def get_runtime_instance(base_path: str | Path, *, instance_id: str) -> dict[str, Any]:
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM author_runtime_instances WHERE instance_id = ?",
            (instance_id,),
        ).fetchone()
    if row is None:
        raise KeyError(instance_id)
    return _runtime_from_row(row)


def list_runtime_instances(
    base_path: str | Path,
    *,
    universe_id: str | None = None,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM author_runtime_instances"
    params: tuple[Any, ...] = ()
    if universe_id:
        query += " WHERE universe_id = ?"
        params = (universe_id,)
    query += " ORDER BY created_at, instance_id"
    with _connect(base_path) as conn:
        rows = conn.execute(query, params).fetchall()
    return [_runtime_from_row(row) for row in rows]


def create_user_request(
    base_path: str | Path,
    *,
    universe_id: str,
    user_id: str,
    request_type: str,
    text: str,
    branch_id: str | None = None,
    preferred_author_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request_id = f"request::{uuid.uuid4().hex}"
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO user_requests (
                request_id, universe_id, branch_id, user_id, request_type, text,
                preferred_author_id, status, created_at, updated_at, metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'open', ?, ?, ?)
            """,
            (
                request_id,
                universe_id,
                branch_id,
                user_id,
                request_type,
                text,
                preferred_author_id,
                _now(),
                _now(),
                _json_dumps(metadata or {}),
            ),
        )
    return get_user_request(base_path, request_id=request_id)


def get_user_request(base_path: str | Path, *, request_id: str) -> dict[str, Any]:
    with _connect(base_path) as conn:
        row = conn.execute(
            """
            SELECT r.*, a.username, a.display_name
            FROM user_requests AS r
            JOIN user_accounts AS a ON a.user_id = r.user_id
            WHERE r.request_id = ?
            """,
            (request_id,),
        ).fetchone()
    if row is None:
        raise KeyError(request_id)
    return _request_from_row(row)


def list_user_requests(base_path: str | Path, *, universe_id: str) -> list[dict[str, Any]]:
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT r.*, a.username, a.display_name
            FROM user_requests AS r
            JOIN user_accounts AS a ON a.user_id = r.user_id
            WHERE r.universe_id = ?
            ORDER BY r.created_at DESC, r.request_id DESC
            """,
            (universe_id,),
        ).fetchall()
    return [_request_from_row(row) for row in rows]


def list_active_user_ids(
    base_path: str | Path,
    *,
    max_idle_seconds: int = 30 * 24 * 3600,
) -> list[str]:
    threshold = _now() - max_idle_seconds
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT DISTINCT user_id
            FROM user_sessions
            WHERE last_seen >= ?
            ORDER BY user_id
            """,
            (threshold,),
        ).fetchall()
    return [str(row["user_id"]) for row in rows]


def create_vote_window(
    base_path: str | Path,
    *,
    universe_id: str,
    vote_type: str,
    subject_type: str,
    subject_id: str,
    created_by: str,
    duration_seconds: int,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    vote_id = f"vote::{uuid.uuid4().hex}"
    full_payload = dict(payload or {})
    eligible_user_ids = list_active_user_ids(base_path)
    if created_by not in eligible_user_ids:
        eligible_user_ids.append(created_by)
    full_payload["eligible_user_ids"] = eligible_user_ids
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO vote_windows (
                vote_id, universe_id, vote_type, subject_type, subject_id,
                created_by, opens_at, closes_at, status, payload_json, result_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'open', ?, '{}')
            """,
            (
                vote_id,
                universe_id,
                vote_type,
                subject_type,
                subject_id,
                created_by,
                _now(),
                _now() + duration_seconds,
                _json_dumps(full_payload),
            ),
        )
    return get_vote(base_path, vote_id=vote_id)


def propose_author_fork(
    base_path: str | Path,
    *,
    universe_id: str,
    author_id: str,
    display_name: str,
    soul_text: str,
    proposed_by: str,
    duration_seconds: int | None = None,
    metadata: dict[str, Any] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    rules = get_universe_rules(base_path, universe_id=universe_id)
    return create_vote_window(
        base_path,
        universe_id=universe_id,
        vote_type="author_fork",
        subject_type="author_definition",
        subject_id=author_id,
        created_by=proposed_by,
        duration_seconds=duration_seconds or int(rules["quick_vote_seconds"]),
        payload={
            "display_name": display_name,
            "soul_text": soul_text,
            "lineage_parent_id": author_id,
            "metadata": dict(metadata or {}),
            "reason": reason,
        },
    )


def cast_vote(
    base_path: str | Path,
    *,
    vote_id: str,
    user_id: str,
    choice: str,
    comment: str = "",
) -> dict[str, Any]:
    normalized = choice.strip().lower()
    if normalized not in {"yes", "no", "abstain"}:
        raise ValueError("Invalid vote choice")
    with _connect(base_path) as conn:
        vote = conn.execute(
            "SELECT * FROM vote_windows WHERE vote_id = ?",
            (vote_id,),
        ).fetchone()
        if vote is None:
            raise KeyError(vote_id)
        if str(vote["status"]) != "open":
            raise ValueError("Vote is closed")
        conn.execute(
            """
            INSERT INTO vote_ballots (vote_id, user_id, choice, comment, created_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(vote_id, user_id) DO UPDATE SET
                choice=excluded.choice,
                comment=excluded.comment,
                created_at=excluded.created_at
            """,
            (vote_id, user_id, normalized, comment, _now()),
        )
    return get_vote(base_path, vote_id=vote_id)


def resolve_vote_if_due(
    base_path: str | Path,
    *,
    vote_id: str,
    force: bool = False,
) -> dict[str, Any]:
    with _connect(base_path) as conn:
        vote = conn.execute(
            "SELECT * FROM vote_windows WHERE vote_id = ?",
            (vote_id,),
        ).fetchone()
    if vote is None:
        raise KeyError(vote_id)
    if str(vote["status"]) != "open":
        return get_vote(base_path, vote_id=vote_id)
    if not force and float(vote["closes_at"]) > _now():
        return get_vote(base_path, vote_id=vote_id)
    with _connect(base_path) as conn:
        ballots = conn.execute(
            "SELECT choice FROM vote_ballots WHERE vote_id = ?",
            (vote_id,),
        ).fetchall()
    counts = {"yes": 0, "no": 0, "abstain": 0}
    for ballot in ballots:
        counts[str(ballot["choice"])] += 1
    result: dict[str, Any] = {
        "counts": counts,
        "passed": counts["yes"] > counts["no"] and counts["yes"] > 0,
        "resolved_at": _now(),
    }
    payload = _json_loads(vote["payload_json"], {})
    if str(vote["vote_type"]) == "author_fork" and result["passed"]:
        created = register_author(
            base_path,
            display_name=str(payload.get("display_name", "Forked Daemon")),
            soul_text=str(payload.get("soul_text", "")),
            created_by=str(vote["created_by"]),
            lineage_parent_id=str(payload.get("lineage_parent_id") or vote["subject_id"]),
            metadata=dict(payload.get("metadata", {})),
            vote_id=str(vote["vote_id"]),
            reason=str(payload.get("reason", "")),
        )
        result["created_author_id"] = created["author_id"]
    with _connect(base_path) as conn:
        conn.execute(
            "UPDATE vote_windows SET status = 'resolved', result_json = ? WHERE vote_id = ?",
            (_json_dumps(result), vote_id),
        )
    return get_vote(base_path, vote_id=vote_id)


def get_vote(base_path: str | Path, *, vote_id: str) -> dict[str, Any]:
    with _connect(base_path) as conn:
        vote = conn.execute(
            "SELECT * FROM vote_windows WHERE vote_id = ?",
            (vote_id,),
        ).fetchone()
    if vote is None:
        raise KeyError(vote_id)
    if str(vote["status"]) == "open" and float(vote["closes_at"]) <= _now():
        return resolve_vote_if_due(base_path, vote_id=vote_id, force=True)
    with _connect(base_path) as conn:
        ballots = conn.execute(
            """
            SELECT b.*, a.username, a.display_name
            FROM vote_ballots AS b
            JOIN user_accounts AS a ON a.user_id = b.user_id
            WHERE b.vote_id = ?
            ORDER BY b.created_at, b.user_id
            """,
            (vote_id,),
        ).fetchall()
    return {
        "vote_id": str(vote["vote_id"]),
        "universe_id": str(vote["universe_id"]),
        "vote_type": str(vote["vote_type"]),
        "subject_type": str(vote["subject_type"]),
        "subject_id": str(vote["subject_id"]),
        "created_by": str(vote["created_by"]),
        "opens_at": float(vote["opens_at"]),
        "closes_at": float(vote["closes_at"]),
        "status": str(vote["status"]),
        "payload": _json_loads(vote["payload_json"], {}),
        "result": _json_loads(vote["result_json"], {}),
        "ballots": [
            {
                "user_id": str(row["user_id"]),
                "username": str(row["username"]),
                "display_name": str(row["display_name"]),
                "choice": str(row["choice"]),
                "comment": str(row["comment"]),
                "created_at": float(row["created_at"]),
            }
            for row in ballots
        ],
    }


def record_action(
    base_path: str | Path,
    *,
    universe_id: str | None,
    actor_type: str,
    actor_id: str,
    action_type: str,
    target_type: str,
    target_id: str,
    summary: str,
    payload: dict[str, Any] | None = None,
    visibility: str = "public",
) -> dict[str, Any]:
    action_id = f"action::{uuid.uuid4().hex}"
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO action_records (
                action_id, universe_id, visibility, actor_type, actor_id,
                action_type, target_type, target_id, summary, created_at, payload_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                action_id,
                universe_id,
                visibility,
                actor_type,
                actor_id,
                action_type,
                target_type,
                target_id,
                summary,
                _now(),
                _json_dumps(payload or {}),
            ),
        )
    return {
        "action_id": action_id,
        "universe_id": universe_id,
        "actor_type": actor_type,
        "actor_id": actor_id,
        "action_type": action_type,
        "target_type": target_type,
        "target_id": target_id,
        "summary": summary,
        "payload": dict(payload or {}),
        "visibility": visibility,
    }


def list_actions(
    base_path: str | Path,
    *,
    universe_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    query = "SELECT * FROM action_records"
    params: list[Any] = []
    if universe_id:
        query += " WHERE universe_id = ?"
        params.append(universe_id)
    query += " ORDER BY created_at DESC, action_id DESC LIMIT ?"
    params.append(limit)
    with _connect(base_path) as conn:
        rows = conn.execute(query, tuple(params)).fetchall()
    return [
        {**dict(row), "payload": _json_loads(row["payload_json"], {})}
        for row in rows
    ]


def _notes_json_path(universe_path: str | Path) -> Path:
    return Path(universe_path) / "notes.json"


def list_note_dicts(
    universe_path: str | Path,
    *,
    source: str | None = None,
    category: str | None = None,
    status: str | None = None,
) -> list[dict[str, Any]]:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    _bootstrap_notes_from_json(universe_path)
    clauses = ["universe_id = ?"]
    params: list[Any] = [universe_id]
    if source:
        clauses.append("source = ?")
        params.append(source)
    if category:
        clauses.append("category = ?")
        params.append(category)
    if status:
        clauses.append("status = ?")
        params.append(status)
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM universe_notes
            WHERE {}
            ORDER BY timestamp, note_id
            """.format(" AND ".join(clauses)),
            tuple(params),
        ).fetchall()
    return [_note_from_row(row) for row in rows]


def add_note_dict(universe_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    _bootstrap_notes_from_json(universe_path)
    note = {
        "id": str(payload.get("id", uuid.uuid4())),
        "source": str(payload.get("source", "system")),
        "text": str(payload.get("text", "")),
        "category": str(payload.get("category", "observation")),
        "status": str(payload.get("status", "unread")),
        "target": payload.get("target"),
        "clearly_wrong": bool(payload.get("clearly_wrong", False)),
        "quoted_passage": str(payload.get("quoted_passage", "")),
        "tags": list(payload.get("tags", []))
        if isinstance(payload.get("tags"), list) else [],
        "metadata": dict(payload.get("metadata", {}))
        if isinstance(payload.get("metadata"), dict) else {},
        "timestamp": float(payload.get("timestamp", _now())),
    }
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO universe_notes (
                note_id, universe_id, source, text, category, status, target,
                clearly_wrong, quoted_passage, tags_json, metadata_json,
                timestamp, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                note["id"],
                universe_id,
                note["source"],
                note["text"],
                note["category"],
                note["status"],
                note["target"],
                1 if note["clearly_wrong"] else 0,
                note["quoted_passage"],
                _json_dumps(note["tags"]),
                _json_dumps(note["metadata"]),
                note["timestamp"],
                _now(),
            ),
        )
    _mirror_notes_json(universe_path)
    return note


def add_note_dicts_bulk(universe_path: str | Path, payloads: list[dict[str, Any]]) -> None:
    if not payloads:
        return
    for payload in payloads:
        add_note_dict(universe_path, payload)


def update_note_status_record(
    universe_path: str | Path,
    note_id: str,
    status: str,
) -> bool:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    _bootstrap_notes_from_json(universe_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            """
            SELECT note_id
            FROM universe_notes
            WHERE universe_id = ? AND note_id = ?
            """,
            (universe_id, note_id),
        ).fetchone()
        if row is None:
            return False
        conn.execute(
            """
            UPDATE universe_notes
            SET status = ?, updated_at = ?
            WHERE universe_id = ? AND note_id = ?
            """,
            (status, _now(), universe_id, note_id),
        )
    _mirror_notes_json(universe_path)
    return True


def bulk_update_note_status_records(
    universe_path: str | Path,
    note_ids: list[str],
    status: str,
) -> int:
    if not note_ids:
        return 0
    count = 0
    for note_id in note_ids:
        if update_note_status_record(universe_path, note_id, status):
            count += 1
    return count


def delete_note_record(universe_path: str | Path, note_id: str) -> bool:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    _bootstrap_notes_from_json(universe_path)
    with _connect(base_path) as conn:
        result = conn.execute(
            "DELETE FROM universe_notes WHERE universe_id = ? AND note_id = ?",
            (universe_id, note_id),
        )
    deleted = result.rowcount > 0
    if deleted:
        _mirror_notes_json(universe_path)
    return deleted


def list_work_target_dicts(universe_path: str | Path) -> list[dict[str, Any]]:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    _bootstrap_payload_table_from_json(
        universe_path,
        table="universe_work_targets",
        id_field="target_id",
        path=Path(universe_path) / "work_targets.json",
    )
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM universe_work_targets
            WHERE universe_id = ?
            ORDER BY updated_at, target_id
            """,
            (universe_id,),
        ).fetchall()
    return [_json_loads(row["payload_json"], {}) for row in rows]


def upsert_work_target_dict(universe_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    _bootstrap_payload_table_from_json(
        universe_path,
        table="universe_work_targets",
        id_field="target_id",
        path=Path(universe_path) / "work_targets.json",
    )
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO universe_work_targets (
                universe_id, target_id, payload_json, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                universe_id,
                str(payload.get("target_id", "")),
                _json_dumps(payload),
                _now(),
            ),
        )
    _mirror_payload_table_to_json(
        universe_path,
        table="universe_work_targets",
        path=Path(universe_path) / "work_targets.json",
    )
    return payload


def replace_work_target_dicts(universe_path: str | Path, payloads: list[dict[str, Any]]) -> None:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    with _connect(base_path) as conn:
        conn.execute(
            "DELETE FROM universe_work_targets WHERE universe_id = ?",
            (universe_id,),
        )
        for payload in payloads:
            conn.execute(
                """
                INSERT INTO universe_work_targets (
                    universe_id, target_id, payload_json, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    universe_id,
                    str(payload.get("target_id", "")),
                    _json_dumps(payload),
                    _now(),
                ),
            )
    _mirror_payload_table_to_json(
        universe_path,
        table="universe_work_targets",
        path=Path(universe_path) / "work_targets.json",
    )


def list_hard_priority_dicts(universe_path: str | Path) -> list[dict[str, Any]]:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    _bootstrap_payload_table_from_json(
        universe_path,
        table="universe_hard_priorities",
        id_field="priority_id",
        path=Path(universe_path) / "hard_priorities.json",
    )
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT payload_json
            FROM universe_hard_priorities
            WHERE universe_id = ?
            ORDER BY updated_at, priority_id
            """,
            (universe_id,),
        ).fetchall()
    return [_json_loads(row["payload_json"], {}) for row in rows]


def upsert_hard_priority_dict(universe_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    _bootstrap_payload_table_from_json(
        universe_path,
        table="universe_hard_priorities",
        id_field="priority_id",
        path=Path(universe_path) / "hard_priorities.json",
    )
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO universe_hard_priorities (
                universe_id, priority_id, payload_json, updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (
                universe_id,
                str(payload.get("priority_id", "")),
                _json_dumps(payload),
                _now(),
            ),
        )
    _mirror_payload_table_to_json(
        universe_path,
        table="universe_hard_priorities",
        path=Path(universe_path) / "hard_priorities.json",
    )
    return payload


def replace_hard_priority_dicts(universe_path: str | Path, payloads: list[dict[str, Any]]) -> None:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    register_universe_if_absent(
        base_path,
        universe_id=universe_id,
        universe_path=universe_path,
    )
    with _connect(base_path) as conn:
        conn.execute(
            "DELETE FROM universe_hard_priorities WHERE universe_id = ?",
            (universe_id,),
        )
        for payload in payloads:
            conn.execute(
                """
                INSERT INTO universe_hard_priorities (
                    universe_id, priority_id, payload_json, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (
                    universe_id,
                    str(payload.get("priority_id", "")),
                    _json_dumps(payload),
                    _now(),
                ),
            )
    _mirror_payload_table_to_json(
        universe_path,
        table="universe_hard_priorities",
        path=Path(universe_path) / "hard_priorities.json",
    )


def _bootstrap_notes_from_json(universe_path: str | Path) -> None:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    path = _notes_json_path(universe_path)
    with _connect(base_path) as conn:
        existing = conn.execute(
            "SELECT 1 FROM universe_notes WHERE universe_id = ? LIMIT 1",
            (universe_id,),
        ).fetchone()
        if existing is not None:
            return
        # Link-free: a planted ``<name>.json -> /data/<other>/...`` must not
        # import another universe's file. A REFUSED read raises: the caller is
        # about to add a row and mirror the table back over this file, so
        # "nothing to import" would replace its contents with one record.
        data = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
        if data is None:
            return
        try:
            raw = json.loads(data.decode("utf-8"))
        except ValueError as exc:
            _logger.warning("not importing %s: %s", path.name, exc)
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            conn.execute(
                """
                INSERT OR IGNORE INTO universe_notes (
                    note_id, universe_id, source, text, category, status,
                    target, clearly_wrong, quoted_passage, tags_json,
                    metadata_json, timestamp, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(item.get("id", uuid.uuid4())),
                    universe_id,
                    str(item.get("source", "system")),
                    str(item.get("text", "")),
                    str(item.get("category", "observation")),
                    str(item.get("status", "unread")),
                    item.get("target"),
                    1 if item.get("clearly_wrong", False) else 0,
                    str(item.get("quoted_passage", "")),
                    _json_dumps(item.get("tags", [])),
                    _json_dumps(item.get("metadata", {})),
                    float(item.get("timestamp", _now())),
                    _now(),
                ),
            )


def _bootstrap_payload_table_from_json(
    universe_path: str | Path,
    *,
    table: str,
    id_field: str,
    path: Path,
) -> None:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    with _connect(base_path) as conn:
        existing = conn.execute(
            f"SELECT 1 FROM {table} WHERE universe_id = ? LIMIT 1",
            (universe_id,),
        ).fetchone()
        if existing is not None:
            return
        # Link-free: a planted ``<name>.json -> /data/<other>/...`` must not
        # import another universe's file. A REFUSED read raises: the caller is
        # about to add a row and mirror the table back over this file, so
        # "nothing to import" would replace its contents with one record.
        data = read_data_path(path, max_bytes=MAX_PLATFORM_FILE_BYTES)
        if data is None:
            return
        try:
            raw = json.loads(data.decode("utf-8"))
        except ValueError as exc:
            _logger.warning("not importing %s: %s", path.name, exc)
            return
        if not isinstance(raw, list):
            return
        for item in raw:
            if not isinstance(item, dict):
                continue
            identifier = str(item.get(id_field, ""))
            if not identifier:
                continue
            conn.execute(
                f"""
                INSERT OR IGNORE INTO {table} (
                    universe_id, {id_field}, payload_json, updated_at
                ) VALUES (?, ?, ?, ?)
                """,
                (universe_id, identifier, _json_dumps(item), _now()),
            )


def _mirror_notes_json(universe_path: str | Path) -> None:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT *
            FROM universe_notes
            WHERE universe_id = ?
            ORDER BY timestamp, note_id
            """,
            (universe_id,),
        ).fetchall()
    payload = [_note_from_row(row) for row in rows]
    write_data_path(_notes_json_path(universe_path), json.dumps(payload, indent=2) + "\n")


def _mirror_payload_table_to_json(
    universe_path: str | Path,
    *,
    table: str,
    path: Path,
) -> None:
    base_path = base_path_from_universe(universe_path)
    universe_id = universe_id_from_path(universe_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT payload_json
            FROM {table}
            WHERE universe_id = ?
            ORDER BY updated_at
            """,
            (universe_id,),
        ).fetchall()
    payload = [_json_loads(row["payload_json"], {}) for row in rows]
    write_data_path(path, json.dumps(payload, indent=2) + "\n")


def _note_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "id": str(row["note_id"]),
        "source": str(row["source"]),
        "text": str(row["text"]),
        "category": str(row["category"]),
        "status": str(row["status"]),
        "target": row["target"],
        "clearly_wrong": bool(row["clearly_wrong"]),
        "quoted_passage": str(row["quoted_passage"]),
        "tags": _json_loads(row["tags_json"], []),
        "metadata": _json_loads(row["metadata_json"], {}),
        "timestamp": float(row["timestamp"]),
    }


def _branch_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["is_public"] = bool(result["is_public"])
    result["metadata"] = _json_loads(result.pop("metadata_json", None), {})
    return result


def _author_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _json_loads(result.pop("metadata_json", None), {})
    return result


def _runtime_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _json_loads(result.pop("metadata_json", None), {})
    return result


def _request_from_row(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["metadata"] = _json_loads(result.pop("metadata_json", None), {})
    return result


def _branch_def_from_row(row: sqlite3.Row) -> dict[str, Any]:
    # goal_id is Phase 5; visibility is Phase 6.2.2. Older rows
    # created before the ADD COLUMN migration may surface the column
    # but with NULL. Accessing sqlite3.Row by key raises IndexError
    # when the column is absent from the SELECT, so guard via
    # ``.keys()``.
    row_keys = row.keys() if hasattr(row, "keys") else []
    goal_id = row["goal_id"] if "goal_id" in row_keys else None
    # A missing, NULL or blank visibility is PRIVATE (private by default,
    # founder 2026-09-26). Reading it as "public" here made every reader's
    # fail-closed check dead code: they only ever saw the normalized value.
    visibility = (
        row["visibility"] if "visibility" in row_keys else None
    ) or "private"
    fork_from = row["fork_from"] if "fork_from" in row_keys else None
    skills = _json_loads(row["skills_json"], []) if "skills_json" in row_keys else []
    # Branch-level execution choices. Same guard: a row predating the
    # ADD COLUMN migration (or a SELECT that omits them) reads as unset.
    default_llm_policy = (
        _json_loads(row["default_llm_policy_json"], None)
        if "default_llm_policy_json" in row_keys
        else None
    )
    concurrency_budget = (
        row["concurrency_budget"] if "concurrency_budget" in row_keys else None
    )
    return {
        "branch_def_id": row["branch_def_id"],
        "name": row["name"],
        "description": row["description"],
        "author": row["author"],
        "domain_id": row["domain_id"],
        "tags": _json_loads(row["tags_json"], []),
        "version": row["version"],
        "skills": skills,
        "parent_def_id": row["parent_def_id"],
        "entry_point": row["entry_point"],
        "graph": _json_loads(row["graph_json"], {}),
        "node_defs": _json_loads(row["node_defs_json"], []),
        "state_schema": _json_loads(row["state_schema_json"], []),
        "published": bool(row["published"]),
        "stats": _json_loads(row["stats_json"], {}),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "goal_id": goal_id,
        "visibility": visibility,
        "fork_from": fork_from,
        "default_llm_policy": default_llm_policy,
        "concurrency_budget": concurrency_budget,
    }


# ═══════════════════════════════════════════════════════════════════════════
# Community Branches — CRUD
# ═══════════════════════════════════════════════════════════════════════════


def _branch_definition_insert(
    branch_def: dict[str, Any],
) -> tuple[str, tuple[Any, ...]]:
    """Normalize one Branch definition into its persisted row values."""
    now = _now()
    branch_def_id = branch_def.get("branch_def_id", uuid.uuid4().hex[:12])

    # Build graph topology JSON (LangGraph-native shape)
    graph = {
        "nodes": branch_def.get("graph_nodes", []),
        "edges": branch_def.get("edges", []),
        "conditional_edges": branch_def.get("conditional_edges", []),
        "entry_point": branch_def.get("entry_point", ""),
    }
    if branch_def.get("io_manifest") is not None:
        from tinyassets.branches import normalize_branch_io_manifest

        graph["io_manifest"] = normalize_branch_io_manifest(branch_def["io_manifest"])

    # Legacy compat: if "nodes" key exists and graph_nodes doesn't,
    # store nodes in graph_json (migration path from old format)
    if not graph["nodes"] and "nodes" in branch_def:
        graph["nodes"] = branch_def["nodes"]

    # Node definitions — separate from graph topology
    node_defs = branch_def.get("node_defs", [])
    from tinyassets.branches import normalize_branch_skill_snapshots

    skills = normalize_branch_skill_snapshots(branch_def.get("skills", []))

    # Phase 6.2.2: visibility defaults to 'public' when absent or
    # falsy. Anything other than 'private' normalizes to 'public' so
    # new values don't sneak in without a deliberate schema change.
    visibility_in = (branch_def.get("visibility") or "public").strip().lower()
    visibility = "private" if visibility_in == "private" else "public"

    # Branch-level execution choices → their own columns. NULL is the single
    # unset representation (cleared and never-set are the same row). Refuse an
    # unrepresentable budget here too rather than let sqlite3 bind it to a
    # float or raise a bare OverflowError from deep inside the writer.
    default_llm_policy = branch_def.get("default_llm_policy")
    policy_json = (
        _json_dumps(default_llm_policy) if default_llm_policy is not None else None
    )
    concurrency_budget = branch_def.get("concurrency_budget")
    if concurrency_budget is not None:
        from tinyassets.branches import validate_concurrency_budget

        budget_errors = validate_concurrency_budget(concurrency_budget)
        if budget_errors:
            raise ValueError(budget_errors[0])

    return branch_def_id, (
        branch_def_id,
        branch_def.get("name", ""),
        branch_def.get("description", ""),
        branch_def.get("author", ""),
        branch_def.get("domain_id", "workflow"),
        _json_dumps(branch_def.get("tags", [])),
        branch_def.get("version", 1),
        _json_dumps(skills),
        branch_def.get("parent_def_id"),
        branch_def.get("entry_point", ""),
        _json_dumps(graph),
        _json_dumps(node_defs),
        _json_dumps(branch_def.get("state_schema", [])),
        1 if branch_def.get("published") else 0,
        _json_dumps(branch_def.get("stats", {})),
        branch_def.get("created_at", now),
        now,
        branch_def.get("goal_id") or None,
        visibility,
        branch_def.get("fork_from") or None,
        policy_json,
        concurrency_budget,
    )


_BRANCH_DEFINITION_INSERT_SQL = """
            INSERT INTO branch_definitions (
                branch_def_id, name, description, author, domain_id,
                tags_json, version, skills_json, parent_def_id, entry_point,
                graph_json, node_defs_json, state_schema_json,
                published, stats_json, created_at, updated_at, goal_id,
                visibility, fork_from, default_llm_policy_json,
                concurrency_budget
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                      ?, ?, ?)
            """


def _charge_branch_write(base_path: str | Path, author: str, values: Any) -> None:
    """Gate a branch-definition write on the AUTHOR's account storage
    (account-storage-quota D7: branch/version writes). Raises
    `storage_accounting.StorageRefused` at the quota before anything is written;
    an author with no account (platform) is not gated."""
    from tinyassets import storage_accounting

    nbytes = sum(len(v.encode("utf-8")) for v in values if isinstance(v, str))
    storage_accounting.charge_now(
        base_path,
        account_id=storage_accounting.account_for_actor(base_path, author),
        store="branches",
        nbytes=nbytes,
    )


def save_branch_definition(
    base_path: str | Path,
    *,
    branch_def: dict[str, Any],
) -> dict[str, Any]:
    """Insert or replace a branch definition."""
    branch_def_id, values = _branch_definition_insert(branch_def)
    _charge_branch_write(base_path, str(branch_def.get("author") or ""), values)
    with _connect(base_path) as conn:
        conn.execute(
            _BRANCH_DEFINITION_INSERT_SQL.replace("INSERT INTO", "INSERT OR REPLACE INTO", 1),
            values,
        )
    saved = get_branch_definition(base_path, branch_def_id=branch_def_id)
    from tinyassets.request_answers import remember_workflow

    remember_workflow(base_path, saved)
    return saved


def create_branch_definition_once(
    base_path: str | Path,
    *,
    branch_def: dict[str, Any],
) -> tuple[dict[str, Any], bool]:
    """Atomically create an idempotent Branch definition.

    The primary key is the request-derived identity. Concurrent callers race
    through SQLite's unique constraint; only the first definition is stored.
    The returned boolean is true only for that winning insert.
    """
    branch_def_id, values = _branch_definition_insert(branch_def)
    _charge_branch_write(base_path, str(branch_def.get("author") or ""), values)
    with _connect(base_path) as conn:
        cursor = conn.execute(
            _BRANCH_DEFINITION_INSERT_SQL
            + " ON CONFLICT(branch_def_id) DO NOTHING",
            values,
        )
        row = conn.execute(
            "SELECT * FROM branch_definitions WHERE branch_def_id = ?",
            (branch_def_id,),
        ).fetchone()
    if row is None:  # pragma: no cover - insert/select share one transaction
        raise RuntimeError("branch definition insert was not observable")
    saved = _branch_def_from_row(row)
    if cursor.rowcount == 1:
        from tinyassets.request_answers import remember_workflow

        remember_workflow(base_path, saved)
    return saved, cursor.rowcount == 1


def get_branch_definition(
    base_path: str | Path,
    *,
    branch_def_id: str,
) -> dict[str, Any]:
    """Retrieve a single branch definition by ID."""
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM branch_definitions WHERE branch_def_id = ?",
            (branch_def_id,),
        ).fetchone()
    if row is None:
        raise KeyError(branch_def_id)
    return _branch_def_from_row(row)


def list_branch_definitions(
    base_path: str | Path,
    *,
    published_only: bool = False,
    author: str = "",
    domain_id: str = "",
    tag: str = "",
    name_contains: str = "",
    goal_id: str = "",
    viewer: str = "",
    include_private: bool = False,
) -> list[dict[str, Any]]:
    """List branch definitions with optional filters.

    Args:
        published_only: If True, return only published branches.
        author: Filter by author name (exact match).
        domain_id: Filter by domain (exact match).
        tag: Filter by tag (substring match in JSON array).
        name_contains: Filter by name (case-insensitive substring).
        goal_id: Filter by bound Goal (exact match). Phase 5.
        viewer: Actor doing the listing. Phase 6.2.2. When given,
            private Branches whose ``author != viewer`` are hidden.
            Empty string means "no viewer context" — returns only
            public Branches unless ``include_private`` is set.
        include_private: Phase 6.2.2. If True, return all rows
            regardless of visibility (host / internal callers only).
    """
    clauses: list[str] = []
    params: list[Any] = []

    if published_only:
        clauses.append("published = 1")
    if author:
        clauses.append("author = ?")
        params.append(author)
    if domain_id:
        clauses.append("domain_id = ?")
        params.append(domain_id)
    if tag:
        clauses.append("tags_json LIKE ?")
        params.append(f'%"{tag}"%')
    if name_contains:
        clauses.append("name LIKE ?")
        params.append(f"%{name_contains}%")
    if goal_id:
        clauses.append("goal_id = ?")
        params.append(goal_id)
    if not include_private:
        # Public rows OR rows authored by the viewer. No viewer =
        # strictly public. Mirrors the filter semantics used by the
        # Phase 6.2 gate-claim helpers.
        if viewer:
            clauses.append("(visibility = 'public' OR author = ?)")
            params.append(viewer)
        else:
            clauses.append("visibility = 'public'")

    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM branch_definitions
            {where}
            ORDER BY updated_at DESC
            """,
            params,
        ).fetchall()
    return [_branch_def_from_row(row) for row in rows]


def update_branch_definition(
    base_path: str | Path,
    *,
    branch_def_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Update specific fields of a branch definition.

    Supports updating: name, description, domain_id, tags, version,
    entry_point, graph_nodes, edges, conditional_edges, node_defs,
    state_schema, published, stats. Also accepts legacy "nodes" key.
    """
    now = _now()
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [now]

    simple_fields = {
        "name": "name",
        "description": "description",
        "author": "author",
        "domain_id": "domain_id",
        "version": "version",
        "entry_point": "entry_point",
        # Phase 5: goal binding. `None` or empty string unbinds.
        "goal_id": "goal_id",
    }
    for key, col in simple_fields.items():
        if key in updates:
            value = updates[key]
            # Normalize empty-string goal_id to NULL for unbind semantics.
            if key == "goal_id" and not value:
                value = None
            sets.append(f"{col} = ?")
            params.append(value)

    if "visibility" in updates:
        # Phase 6.2.2. Default to public for any unrecognized string
        # so the column never holds an unknown state.
        incoming = (updates["visibility"] or "public").strip().lower()
        normalized = "private" if incoming == "private" else "public"
        sets.append("visibility = ?")
        params.append(normalized)

    if "fork_from" in updates:
        # fork_from is immutable-after-set. Only write if not already set.
        existing_row = get_branch_definition(base_path, branch_def_id=branch_def_id)
        if existing_row.get("fork_from") is not None:
            raise ValueError(
                f"fork_from is immutable after set on branch '{branch_def_id}'."
            )
        sets.append("fork_from = ?")
        params.append(updates["fork_from"] or None)

    json_fields = {
        "tags": "tags_json",
        "skills": "skills_json",
        "stats": "stats_json",
        "state_schema": "state_schema_json",
    }
    for key, col in json_fields.items():
        if key in updates:
            if key == "skills":
                from tinyassets.branches import normalize_branch_skill_snapshots

                updates[key] = normalize_branch_skill_snapshots(updates[key])
            sets.append(f"{col} = ?")
            params.append(_json_dumps(updates[key]))

    if "published" in updates:
        sets.append("published = ?")
        params.append(1 if updates["published"] else 0)

    # Update node_defs separately from graph topology
    if "node_defs" in updates:
        sets.append("node_defs_json = ?")
        params.append(_json_dumps(updates["node_defs"]))

    # If graph topology fields are updated, rebuild graph_json
    graph_keys = {"graph_nodes", "edges", "conditional_edges", "nodes", "io_manifest"}
    if graph_keys & updates.keys():
        existing = get_branch_definition(base_path, branch_def_id=branch_def_id)
        graph = existing.get("graph", {})
        if "graph_nodes" in updates:
            graph["nodes"] = updates["graph_nodes"]
        elif "nodes" in updates:
            # Legacy compat
            graph["nodes"] = updates["nodes"]
        if "edges" in updates:
            graph["edges"] = updates["edges"]
        if "conditional_edges" in updates:
            graph["conditional_edges"] = updates["conditional_edges"]
        if "entry_point" in updates:
            graph["entry_point"] = updates["entry_point"]
        if "io_manifest" in updates:
            from tinyassets.branches import normalize_branch_io_manifest

            manifest = normalize_branch_io_manifest(updates["io_manifest"])
            if manifest is None:
                graph.pop("io_manifest", None)
            else:
                graph["io_manifest"] = manifest
        sets.append("graph_json = ?")
        params.append(_json_dumps(graph))

    params.append(branch_def_id)

    author = str(
        updates.get("author")
        or get_branch_definition(base_path, branch_def_id=branch_def_id).get("author")
        or ""
    )
    _charge_branch_write(base_path, author, params)
    with _connect(base_path) as conn:
        conn.execute(
            f"UPDATE branch_definitions SET {', '.join(sets)} "
            f"WHERE branch_def_id = ?",
            params,
        )
    return get_branch_definition(base_path, branch_def_id=branch_def_id)


def delete_branch_definition(
    base_path: str | Path,
    *,
    branch_def_id: str,
) -> bool:
    """Delete a branch definition. Returns True if a row was deleted."""
    with _connect(base_path) as conn:
        cursor = conn.execute(
            "DELETE FROM branch_definitions WHERE branch_def_id = ?",
            (branch_def_id,),
        )
    return cursor.rowcount > 0


def fork_branch_definition(
    base_path: str | Path,
    *,
    branch_def_id: str,
    new_name: str = "",
    author: str = "",
) -> dict[str, Any]:
    """Fork an existing branch definition.

    Creates a new branch with a new ID, version reset to 1, and
    parent_def_id set to the source branch for lineage tracking.
    """
    from tinyassets.branches import BranchDefinition

    source = get_branch_definition(base_path, branch_def_id=branch_def_id)

    # from_dict handles the DB row shape (nested "graph" dict) directly
    branch = BranchDefinition.from_dict(source)
    forked = branch.fork(new_name=new_name, author=author)

    # Increment fork count on source
    stats = source.get("stats", {})
    stats["fork_count"] = stats.get("fork_count", 0) + 1
    update_branch_definition(
        base_path, branch_def_id=branch_def_id, updates={"stats": stats}
    )

    return save_branch_definition(base_path, branch_def=forked.to_dict())


# ═══════════════════════════════════════════════════════════════════════════
# Phase 5: Goals — first-class shared primitive above Branches
# ═══════════════════════════════════════════════════════════════════════════


_logger = logging.getLogger(__name__)

# Variant canonicals (Task #64 — Step 3 reader cutover). Counter
# incremented every time a get_goal/list_goals/search_goals reader falls
# back to the legacy goals.canonical_branch_version_id column because no
# default-scope row exists in canonical_bindings. Should remain 0 in
# healthy operation post-Step-2 (dual-write keeps both stores in sync).
# Tests assert this counter directly. Step 4 deprecation will remove the
# counter + fallback path entirely.
_LEGACY_FALLBACK_HITS: dict[str, int] = {"count": 0}


def _apply_canonical_bindings_cutover(
    conn: sqlite3.Connection,
    goals: list[dict[str, Any]],
) -> None:
    """Patch ``goals[*]["canonical_branch_version_id"]`` from canonical_bindings.

    Step 3 reader cutover: prefer the new ``canonical_bindings`` table over
    the legacy ``goals.canonical_branch_version_id`` column. Falls back to
    the legacy column (with a loud warning + ``_LEGACY_FALLBACK_HITS``
    increment) when the new table has no default-scope row for a goal but
    the legacy column does — that's a Step-2-dual-write-broke signal.

    Single batch query for all N goals; no N+1.
    """
    if not goals:
        return
    goal_ids = [g["goal_id"] for g in goals]
    placeholders = ",".join("?" for _ in goal_ids)
    rows = conn.execute(
        f"SELECT goal_id, branch_version_id FROM canonical_bindings "
        f"WHERE scope_token = '' AND goal_id IN ({placeholders})",
        goal_ids,
    ).fetchall()
    bindings_by_goal = {r["goal_id"]: r["branch_version_id"] for r in rows}
    for goal in goals:
        bound = bindings_by_goal.get(goal["goal_id"])
        if bound is not None:
            goal["canonical_branch_version_id"] = bound
        elif goal.get("canonical_branch_version_id") is not None:
            # Legacy column has a value but new table does not — Step-2
            # dual-write skipped this goal somewhere. Log loudly so any
            # post-Step-3 hit surfaces as a real bug.
            _LEGACY_FALLBACK_HITS["count"] += 1
            _logger.warning(
                "canonical_bindings fallback to legacy column for goal %s; "
                "investigate Step-2 dual-write coverage.",
                goal["goal_id"],
            )


def _goal_from_row(row: sqlite3.Row) -> dict[str, Any]:
    ladder_raw = ""
    try:
        ladder_raw = row["gate_ladder_json"]
    except (IndexError, KeyError):
        ladder_raw = "[]"
    try:
        protocol_raw = row["branch_protocol_json"]
    except (IndexError, KeyError):
        protocol_raw = "[]"
    try:
        canonical_bvid = row["canonical_branch_version_id"]
    except (IndexError, KeyError):
        canonical_bvid = None
    try:
        canonical_history_raw = row["canonical_branch_history_json"]
    except (IndexError, KeyError):
        canonical_history_raw = "[]"
    # PR-127 — leaderboard-driven canonical selection. Defaults match
    # the schema (auto OFF, threshold 5) so pre-migration rows behave
    # as if they were freshly created.
    try:
        auto_flag = int(row["auto_canonical_via_leaderboard"] or 0)
    except (IndexError, KeyError, TypeError, ValueError):
        auto_flag = 0
    try:
        min_runs = int(row["min_completed_runs_for_canonical"] or 5)
    except (IndexError, KeyError, TypeError, ValueError):
        min_runs = 5
    # DESIGN-008 — user-buildable selector primitive. NULL post-migration
    # is the "use platform default selector" signal; the leaderboard
    # call resolves the default on demand.
    try:
        selector_bvid = row["selector_branch_version_id"]
    except (IndexError, KeyError):
        selector_bvid = None
    return {
        "goal_id": row["goal_id"],
        "name": row["name"],
        "description": row["description"],
        "author": row["author"],
        "tags": _json_loads(row["tags_json"], []),
        "visibility": row["visibility"],
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "gate_ladder": _json_loads(ladder_raw or "[]", []),
        "branch_protocol": _json_loads(protocol_raw or "[]", []),
        "canonical_branch_version_id": canonical_bvid,
        "canonical_branch_history": _json_loads(
            canonical_history_raw or "[]", []
        ),
        "auto_canonical_via_leaderboard": bool(auto_flag),
        "min_completed_runs_for_canonical": min_runs,
        "selector_branch_version_id": selector_bvid,
    }


def save_goal(
    base_path: str | Path,
    *,
    goal: dict[str, Any],
) -> dict[str, Any]:
    """Insert or update a Goal. Returns the stored row as a dict."""
    initialize_author_server(base_path)
    now = _now()
    goal_id = goal.get("goal_id") or uuid.uuid4().hex[:12]
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT OR REPLACE INTO goals (
                goal_id, name, description, author, tags_json,
                visibility, created_at, updated_at, gate_ladder_json,
                branch_protocol_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                goal_id,
                goal.get("name", ""),
                goal.get("description", ""),
                goal.get("author", ""),
                _json_dumps(list(goal.get("tags", []) or [])),
                goal.get("visibility", "public"),
                goal.get("created_at", now),
                now,
                _json_dumps(list(goal.get("gate_ladder", []) or [])),
                _json_dumps(list(goal.get("branch_protocol", []) or [])),
            ),
        )
    return get_goal(base_path, goal_id=goal_id)


def get_goal(
    base_path: str | Path,
    *,
    goal_id: str,
) -> dict[str, Any]:
    """Fetch a Goal by id. Raises KeyError if missing."""
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM goals WHERE goal_id = ?",
            (goal_id,),
        ).fetchone()
        if row is None:
            raise KeyError(goal_id)
        result = _goal_from_row(row)
        _apply_canonical_bindings_cutover(conn, [result])
    return result


def update_goal(
    base_path: str | Path,
    *,
    goal_id: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    """Patch mutable fields on a Goal. Returns the updated row.

    Supported fields: name, description, tags, visibility,
    auto_canonical_via_leaderboard (bool — PR-127), and
    min_completed_runs_for_canonical (int — PR-127). Author is
    immutable; timestamps are server-managed.
    """
    initialize_author_server(base_path)
    now = _now()
    sets: list[str] = ["updated_at = ?"]
    params: list[Any] = [now]
    simple = {"name": "name", "description": "description",
              "visibility": "visibility"}
    for key, col in simple.items():
        if key in updates:
            sets.append(f"{col} = ?")
            params.append(updates[key])
    if "tags" in updates:
        sets.append("tags_json = ?")
        params.append(_json_dumps(list(updates["tags"] or [])))
    if "branch_protocol" in updates:
        sets.append("branch_protocol_json = ?")
        params.append(_json_dumps(list(updates["branch_protocol"] or [])))
    # PR-127 — leaderboard-driven canonical opt-in + threshold knob.
    # Booleans land as 0/1 to match the SQLite INTEGER column.
    if "auto_canonical_via_leaderboard" in updates:
        sets.append("auto_canonical_via_leaderboard = ?")
        params.append(1 if updates["auto_canonical_via_leaderboard"] else 0)
    if "min_completed_runs_for_canonical" in updates:
        try:
            threshold = int(updates["min_completed_runs_for_canonical"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                "min_completed_runs_for_canonical must be an integer"
            ) from exc
        if threshold < 0:
            raise ValueError(
                "min_completed_runs_for_canonical must be >= 0"
            )
        sets.append("min_completed_runs_for_canonical = ?")
        params.append(threshold)
    # DESIGN-008 — selector branch_version pointer. Storage helpers
    # do NOT enforce active-version validation here; the dedicated
    # set_selector_branch() helper does. update_goal accepts the
    # field for host scripts / migration backfill that bypass the
    # MCP auth+validate path.
    if "selector_branch_version_id" in updates:
        bvid = updates["selector_branch_version_id"]
        sets.append("selector_branch_version_id = ?")
        params.append(bvid if bvid else None)
    params.append(goal_id)
    with _connect(base_path) as conn:
        conn.execute(
            f"UPDATE goals SET {', '.join(sets)} WHERE goal_id = ?",
            params,
        )
    return get_goal(base_path, goal_id=goal_id)


def set_canonical_branch(
    base_path: str | Path,
    *,
    goal_id: str,
    branch_version_id: str | None,
    set_by: str,
) -> dict[str, Any]:
    """Set (or unset) the canonical branch version for a Goal.

    Only the Goal author or a host-level actor may set canonical.
    Caller must validate authority before calling this function.

    Args:
        branch_version_id: A published branch_version_id to designate
            as canonical, or None to unset.
        set_by: Actor making the change (for history audit trail).

    The previous canonical (if any) is recorded in
    canonical_branch_history before the new value is written.
    Raises KeyError if goal_id not found.
    Raises ValueError if branch_version_id is provided but does not exist
        in branch_versions table (not a published version), OR if the
        supplied version's status is not 'active' (PR-127 round 2 P1.1
        defense-in-depth — rolled_back / superseded versions cannot be
        promoted to canonical even if a caller bypasses the auto-refresh
        filter in :func:`tinyassets.api.canonical_dispatch._latest_published_version_id`).
    """
    initialize_author_server(base_path)
    now = _now()
    goal = get_goal(base_path, goal_id=goal_id)

    _validate_canonical_branch_version(base_path, branch_version_id)

    # Build history entry for previous canonical (if any).
    prev_bvid = goal.get("canonical_branch_version_id")
    history: list[dict[str, Any]] = list(goal.get("canonical_branch_history") or [])
    if prev_bvid is not None:
        history.append({
            "branch_version_id": prev_bvid,
            "unset_at": now,
            "replaced_by": branch_version_id,
        })

    with _connect(base_path) as conn:
        conn.execute(
            """
            UPDATE goals
               SET canonical_branch_version_id = ?,
                   canonical_branch_history_json = ?,
                   updated_at = ?
             WHERE goal_id = ?
            """,
            (branch_version_id, _json_dumps(history), now, goal_id),
        )
        # Variant canonicals (Task #63 — Step 2 dual-write). Mirror the
        # default-scope binding into canonical_bindings so future readers
        # can prefer the new table while the legacy column is still
        # written above. When branch_version_id is None we delete any
        # existing default-scope binding to keep the two stores in sync.
        if branch_version_id is None:
            conn.execute(
                "DELETE FROM canonical_bindings "
                "WHERE goal_id = ? AND scope_token = ''",
                (goal_id,),
            )
        else:
            conn.execute(
                "INSERT INTO canonical_bindings "
                "(goal_id, scope_token, branch_version_id, "
                " bound_by_actor_id, bound_at, visibility) "
                "VALUES (?, '', ?, ?, ?, 'public') "
                "ON CONFLICT(goal_id, scope_token) DO UPDATE SET "
                "    branch_version_id = excluded.branch_version_id, "
                "    bound_by_actor_id = excluded.bound_by_actor_id, "
                "    bound_at          = excluded.bound_at",
                (goal_id, branch_version_id, set_by, now),
            )
        if branch_version_id is None:
            conn.execute(
                "DELETE FROM goal_canonicals "
                "WHERE goal_id = ? AND scope_actor = ''",
                (goal_id,),
            )
        else:
            conn.execute(
                "INSERT INTO goal_canonicals "
                "(goal_id, scope_actor, branch_version_id, set_at, set_by) "
                "VALUES (?, '', ?, ?, ?) "
                "ON CONFLICT(goal_id, scope_actor) DO UPDATE SET "
                "    branch_version_id = excluded.branch_version_id, "
                "    set_at = excluded.set_at, "
                "    set_by = excluded.set_by",
                (goal_id, branch_version_id, now, set_by),
            )
    return get_goal(base_path, goal_id=goal_id)


def _validate_canonical_branch_version(
    base_path: str | Path,
    branch_version_id: str | None,
) -> None:
    """Require an active published version when a canonical is supplied."""
    if branch_version_id is None:
        return
    from tinyassets.branch_versions import get_branch_version

    version = get_branch_version(base_path, branch_version_id)
    if version is None:
        raise ValueError(
            f"branch_version_id {branch_version_id!r} not found "
            "in branch_versions — only published versions may be canonical."
        )
    version_status = getattr(version, "status", "active") or "active"
    if version_status != "active":
        raise ValueError(
            f"branch_version_id {branch_version_id!r} has "
            f"status={version_status!r}; only versions with "
            "status='active' may be promoted to canonical. "
            "Re-publish a fresh version or restore the rolled-back "
            "one before retrying."
        )


def set_goal_canonical(
    base_path: str | Path,
    *,
    goal_id: str,
    scope_actor: str,
    branch_version_id: str | None,
    set_by: str,
) -> dict[str, Any]:
    """Set or unset one actor's personal canonical for a Goal.

    Authorization belongs at the action boundary. A non-empty actor scope is
    required so default writes cannot bypass ``set_canonical_branch`` and its
    legacy dual-write/history behavior.
    """
    scope_actor = scope_actor.strip()
    if not scope_actor:
        raise ValueError(
            "scope_actor is required for a personal canonical; "
            "use set_canonical_branch for the Goal default."
        )
    initialize_author_server(base_path)
    get_goal(base_path, goal_id=goal_id)
    _validate_canonical_branch_version(base_path, branch_version_id)
    now = _now()

    with _connect(base_path) as conn:
        if branch_version_id is None:
            conn.execute(
                "DELETE FROM goal_canonicals "
                "WHERE goal_id = ? AND scope_actor = ?",
                (goal_id, scope_actor),
            )
        else:
            conn.execute(
                "INSERT INTO goal_canonicals "
                "(goal_id, scope_actor, branch_version_id, set_at, set_by) "
                "VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(goal_id, scope_actor) DO UPDATE SET "
                "    branch_version_id = excluded.branch_version_id, "
                "    set_at = excluded.set_at, "
                "    set_by = excluded.set_by",
                (goal_id, scope_actor, branch_version_id, now, set_by),
            )
    return {
        "goal_id": goal_id,
        "scope_actor": scope_actor,
        "branch_version_id": branch_version_id,
        "set_at": now,
        "set_by": set_by,
    }


def get_goal_canonical(
    base_path: str | Path,
    *,
    goal_id: str,
    scope_actor: str,
) -> dict[str, Any] | None:
    """Return the exact actor/default canonical row without fallback."""
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        goal = conn.execute(
            "SELECT 1 FROM goals WHERE goal_id = ?",
            (goal_id,),
        ).fetchone()
        if goal is None:
            raise KeyError(goal_id)
        row = conn.execute(
            "SELECT goal_id, scope_actor, branch_version_id, set_at, set_by "
            "FROM goal_canonicals WHERE goal_id = ? AND scope_actor = ?",
            (goal_id, scope_actor.strip()),
        ).fetchone()
    return dict(row) if row is not None else None


def resolve_goal_canonical(
    base_path: str | Path,
    *,
    goal_id: str,
    scope_actor: str = "",
) -> str | None:
    """Resolve actor row, then default row, then the legacy Goal column."""
    initialize_author_server(base_path)
    scope_actor = scope_actor.strip()
    with _connect(base_path) as conn:
        goal = conn.execute(
            "SELECT canonical_branch_version_id FROM goals WHERE goal_id = ?",
            (goal_id,),
        ).fetchone()
        if goal is None:
            raise KeyError(goal_id)
        if scope_actor:
            actor_row = conn.execute(
                "SELECT branch_version_id FROM goal_canonicals "
                "WHERE goal_id = ? AND scope_actor = ?",
                (goal_id, scope_actor),
            ).fetchone()
            if actor_row is not None:
                return str(actor_row["branch_version_id"])
        default_row = conn.execute(
            "SELECT branch_version_id FROM goal_canonicals "
            "WHERE goal_id = ? AND scope_actor = ''",
            (goal_id,),
        ).fetchone()
        if default_row is not None:
            return str(default_row["branch_version_id"])
        legacy = goal["canonical_branch_version_id"]
    return str(legacy) if legacy else None


def get_canonical_branch_history(
    base_path: str | Path,
    *,
    goal_id: str,
) -> list[dict[str, Any]]:
    """Return the canonical branch history for a Goal.

    Each entry: {branch_version_id, unset_at, replaced_by}.
    Returns [] if no history exists or goal is not found.
    """
    try:
        goal = get_goal(base_path, goal_id=goal_id)
    except KeyError:
        return []
    return list(goal.get("canonical_branch_history") or [])


def list_goals(
    base_path: str | Path,
    *,
    author: str = "",
    tag: str = "",
    limit: int = 50,
) -> list[dict[str, Any]]:
    """List public Goals with optional filters."""
    initialize_author_server(base_path)
    clauses: list[str] = ["visibility = 'public'"]
    params: list[Any] = []
    if author:
        clauses.append("author = ?")
        params.append(author)
    if tag:
        clauses.append("tags_json LIKE ?")
        params.append(f'%"{tag}"%')
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM goals {where}
            ORDER BY updated_at DESC LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
        results = [_goal_from_row(row) for row in rows]
        _apply_canonical_bindings_cutover(conn, results)
    return results


def public_goal_ids(base_path: str | Path) -> set[str]:
    """Return IDs for Goals whose visibility is exactly public."""
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT goal_id FROM goals WHERE visibility = 'public'"
        ).fetchall()
    return {str(row["goal_id"]) for row in rows}


def non_public_goal_ids(base_path: str | Path) -> set[str]:
    """Return IDs for every Goal whose visibility is not exactly public."""
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            "SELECT goal_id, visibility FROM goals"
        ).fetchall()
    return {
        str(row["goal_id"])
        for row in rows
        if row["visibility"] != "public"
    }


_GOAL_SEARCH_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _goal_search_tokens(value: str) -> list[str]:
    """Return lowercase alphanumeric query tokens for goal search."""
    return _GOAL_SEARCH_TOKEN_RE.findall(value.lower())


def _goal_search_haystack(goal: dict[str, Any]) -> str:
    fields = [
        goal.get("name") or "",
        goal.get("description") or "",
        " ".join(goal.get("tags") or []),
    ]
    return " ".join(_goal_search_tokens(" ".join(fields)))


def search_goals(
    base_path: str | Path,
    *,
    query: str,
    limit: int = 20,
) -> list[dict[str, Any]]:
    """Token-based full-field search over name + description + tags.

    Per spec §Search: tokenized LIKE for v1, FTS5 if we hit scale.
    Multi-word queries are split into individual tokens; each token is
    matched case-insensitively against name + description + tags_json.
    Rows that match at least one token are returned, ranked by how many
    tokens matched (descending).

    Single-token queries behave identically to the original LIKE search.
    Only Goals with exactly ``visibility='public'`` are searched.
    """
    initialize_author_server(base_path)
    tokens = _goal_search_tokens(query or "")
    if not tokens:
        return []

    with _connect(base_path) as conn:
        # Fetch public goals then score in Python.
        # For v1 scale this is fine; swap to FTS5 if row count grows large.
        all_rows = conn.execute(
            "SELECT * FROM goals WHERE visibility = 'public'",
        ).fetchall()

        scored: list[tuple[int, dict[str, Any]]] = []
        for row in all_rows:
            g = _goal_from_row(row)
            haystack = _goal_search_haystack(g)
            hit_count = sum(1 for t in tokens if t in haystack)
            if hit_count > 0:
                scored.append((hit_count, g))

        scored.sort(key=lambda x: -x[0])
        top = [g for _, g in scored[:max(1, int(limit))]]
        _apply_canonical_bindings_cutover(conn, top)
    return top


def delete_goal(
    base_path: str | Path,
    *,
    goal_id: str,
) -> dict[str, Any]:
    """Soft-delete a Goal by flipping visibility to 'deleted'.

    Per AGENTS.md "nothing is lost, nothing is deleted": bound Branches
    keep their ``goal_id`` reference; `get_goal(...)` still resolves
    deleted Goals so lineage is inspectable.
    """
    return update_goal(
        base_path, goal_id=goal_id, updates={"visibility": "deleted"},
    )


class SelectorHasEffectsError(ValueError):
    """Raised when a selector branch is not pure / read-only.

    DESIGN-008 round-2/3 purity guard: selector branches run on every
    leaderboard read (``quality_leaderboard`` /
    ``recommend_parent_for_fork``). The substrate forbids any selector
    branch that could trigger external writes, including transitively:

    * **Round 2 P1.3** — node declares non-empty ``effects``
      (e.g. ``authenticated_external_call``). Each read would fire the
      effector directly.
    * **Round 3 P1.B** — node declares ``invoke_branch_spec`` or
      ``invoke_branch_version_spec``. The child run executes through
      the normal graph path, and child completion fires the child's
      effectors — bypassing the direct-effects scan. Worse,
      ``invoke_branch_spec`` resolves at run time against a live
      ``branch_def_id`` that the operator can mutate AFTER bind, so
      transitive scanning at bind time is not a reliable safeguard.
      Reject child-invocation nodes outright; a pure selector is a
      single graph.

    Bind-time rejection is the loud + early gate. The single error
    type covers both classes because the failure mode is the same:
    "read became a write."
    """


def set_selector_branch(
    base_path: str | Path,
    *,
    goal_id: str,
    branch_version_id: str | None,
    set_by: str,
) -> dict[str, Any]:
    """Bind (or unbind) the selector branch_version for a Goal.

    DESIGN-008 — user-buildable selector primitive. The selector
    branch is the published TinyAssets branch that synthesizes the
    Goal's leaderboard rankings. ``branch_version_id=None`` unsets
    the binding, falling back to the platform default selector.

    Authority: the caller must enforce "only Goal author or host"
    before calling this; the helper does NOT re-check authority
    (matches the contract of ``set_canonical_branch``).

    Raises
    ------
    KeyError
        ``goal_id`` is not in the goals table.
    ValueError
        ``branch_version_id`` is provided but the version row does
        not exist in ``branch_versions`` OR its status is not
        ``'active'`` (defense in depth — rolled-back / superseded
        versions cannot be promoted to selector, mirroring the
        PR-127 round-2 P1.1 contract on ``set_canonical_branch``).
    SelectorHasEffectsError
        ``branch_version_id`` is provided but the snapshot contains
        a node with non-empty ``effects`` (DESIGN-008 round-2 P1.3).
        Reads must not silently fire external writes.
    """
    initialize_author_server(base_path)
    now = _now()
    # Validate goal exists — get_goal raises KeyError when missing.
    # Authority enforcement is the caller's responsibility (see docstring).
    get_goal(base_path, goal_id=goal_id)

    if branch_version_id is not None:
        from tinyassets.branch_versions import get_branch_version
        version = get_branch_version(base_path, branch_version_id)
        if version is None:
            raise ValueError(
                f"branch_version_id {branch_version_id!r} not found in "
                "branch_versions — only published versions may be "
                "selector branches."
            )
        version_status = getattr(version, "status", "active") or "active"
        if version_status != "active":
            raise ValueError(
                f"branch_version_id {branch_version_id!r} has "
                f"status={version_status!r}; only versions with "
                "status='active' may be promoted to selector."
            )
        # Selector purity scan (rounds 2 + 3). Selector branches run
        # on every leaderboard read; the substrate forbids ANY shape
        # that could result in a real external write, directly or
        # transitively.
        node_defs = (version.snapshot or {}).get("node_defs") or []
        # P1.3 (round 2) — direct effects declarations.
        effectful: list[str] = []
        # P1.B (round 3) — child-branch invocation specs. A node with
        # invoke_branch_spec / invoke_branch_version_spec spawns a
        # child run; the child's normal completion path fires its
        # OWN effectors, bypassing the direct-effects scan above.
        # ``invoke_branch_spec`` resolves at run time against a live
        # branch_def_id that operators can mutate AFTER bind, so
        # transitive scanning of the bind-time graph is not reliable.
        # Reject child-invocation outright — selectors must be a
        # single graph.
        child_invokers: list[str] = []
        for nd in node_defs:
            if not isinstance(nd, dict):
                continue
            node_id = nd.get("node_id") or "(unnamed)"
            effects = nd.get("effects") or []
            if effects:
                effectful.append(f"{node_id}:{list(effects)}")
            ibs = nd.get("invoke_branch_spec")
            if ibs:
                target = ""
                if isinstance(ibs, dict):
                    target = ibs.get("branch_def_id") or ""
                child_invokers.append(
                    f"{node_id}:invoke_branch_spec"
                    + (f"({target})" if target else "")
                )
            ivs = nd.get("invoke_branch_version_spec")
            if ivs:
                target = ""
                if isinstance(ivs, dict):
                    target = ivs.get("branch_version_id") or ""
                child_invokers.append(
                    f"{node_id}:invoke_branch_version_spec"
                    + (f"({target})" if target else "")
                )
        # Both classes route through the same error type because the
        # failure mode is identical: "read became a write."
        violations: list[str] = []
        if effectful:
            violations.append(
                "declares external-write effects "
                f"({', '.join(effectful)})"
            )
        if child_invokers:
            violations.append(
                "declares child-branch invocation "
                f"({', '.join(child_invokers)})"
            )
        if violations:
            raise SelectorHasEffectsError(
                f"branch_version_id {branch_version_id!r} cannot be "
                "bound as a selector: selector branches run on every "
                "leaderboard read and MUST be pure (no external "
                "writes, no child-branch invocation). Violations: "
                + "; ".join(violations)
                + ". Remove the offending fields on those nodes (or "
                "fork into a pure selector branch) before binding. "
                "If you need rich ranking that requires running other "
                "graphs, build a selector that emits a recommendation "
                "and let a separate Goal / canonical handler perform "
                "the side-effecting work."
            )

    with _connect(base_path) as conn:
        conn.execute(
            "UPDATE goals SET selector_branch_version_id = ?, "
            "    updated_at = ? "
            "WHERE goal_id = ?",
            (branch_version_id, now, goal_id),
        )
    return get_goal(base_path, goal_id=goal_id)


def branches_for_goal(
    base_path: str | Path,
    *,
    goal_id: str,
    limit: int = 100,
    viewer: str = "",
    include_private: bool = False,
) -> list[dict[str, Any]]:
    """Return Branches bound to a Goal (full branch-def dicts).

    Phase 6.2.2: visibility-aware by default. With no ``viewer`` and
    ``include_private=False`` the result is public-only — the safe
    default for any caller that surfaces results to an end user.
    Internal stats that need the full Branch population (e.g.
    leaderboard) must opt in explicitly with ``include_private=True``
    and re-filter at the presentation boundary.

    Args:
        viewer: Actor doing the listing. With ``include_private=False``,
            adds the viewer's own private Branches to the public set.
        include_private: When True, returns ALL visibility states
            (internal aggregation callers only).
    """
    return list_branch_definitions(
        base_path,
        goal_id=goal_id,
        viewer=viewer,
        include_private=include_private,
    )[:max(1, int(limit))]


def _branch_feature_tokens(branch: dict[str, Any]) -> set[str]:
    """Return coarse feature tokens for parent diversity ranking."""
    fields: list[str] = [
        branch.get("name") or "",
        branch.get("description") or "",
        branch.get("author") or "",
        " ".join(branch.get("tags") or []),
    ]
    for node in branch.get("node_defs") or []:
        if not isinstance(node, dict):
            continue
        fields.extend([
            node.get("node_id") or "",
            node.get("display_name") or "",
        ])
    return set(_goal_search_tokens(" ".join(fields)))


def _branch_quality_score(branch: dict[str, Any]) -> float:
    stats = branch.get("stats") or {}
    try:
        score = float(stats.get("avg_quality_score", 0.0) or 0.0)
    except (TypeError, ValueError):
        score = 0.0
    return max(0.0, min(1.0, score))


def _feature_distance(tokens: set[str], selected: list[set[str]]) -> float:
    if not selected:
        return 1.0
    if not tokens:
        return 0.0
    max_similarity = 0.0
    for other in selected:
        if not other:
            continue
        union = tokens | other
        if not union:
            continue
        max_similarity = max(max_similarity, len(tokens & other) / len(union))
    return max(0.0, min(1.0, 1.0 - max_similarity))


def goal_archive_consultation(
    base_path: str | Path,
    *,
    goal_id: str,
    query: str = "",
    limit: int = 20,
    viewer: str = "",
) -> dict[str, Any]:
    """Rank Goal-bound Branches as fork parents with gate outcome signal."""
    branches = branches_for_goal(
        base_path,
        goal_id=goal_id,
        limit=500,
        viewer=viewer,
    )
    query_tokens = set(_goal_search_tokens(query or ""))
    outcome_entries = gates_leaderboard(
        base_path,
        goal_id=goal_id,
        limit=500,
    )
    outcome_by_branch = {
        entry["branch_def_id"]: entry
        for entry in outcome_entries
        if entry.get("branch_def_id")
    }
    ladder_len = len(get_goal_ladder(base_path, goal_id=goal_id))
    max_rung_index = max(0, ladder_len - 1)

    pool: list[dict[str, Any]] = []
    for branch in branches:
        feature_tokens = _branch_feature_tokens(branch)
        if query_tokens and not (query_tokens & feature_tokens):
            continue
        outcome = outcome_by_branch.get(branch["branch_def_id"], {})
        try:
            rung_index = int(outcome.get("highest_rung_index", -1))
        except (TypeError, ValueError):
            rung_index = -1
        outcome_score = (
            max(0.0, min(1.0, rung_index / max_rung_index))
            if max_rung_index > 0 else 0.0
        )
        pool.append({
            "branch": branch,
            "feature_tokens": feature_tokens,
            "quality_score": _branch_quality_score(branch),
            "outcome_score": outcome_score,
            "outcome_signal": {
                "highest_rung_key": outcome.get("highest_rung_key", ""),
                "highest_rung_index": rung_index,
                "claimed_at": outcome.get("claimed_at", ""),
                "evidence_url": outcome.get("evidence_url", ""),
            },
        })

    selected_tokens: list[set[str]] = []
    ranked: list[dict[str, Any]] = []
    remaining = pool[:]
    while remaining and len(ranked) < max(1, int(limit)):
        best_index = 0
        best_payload: dict[str, Any] | None = None
        for index, item in enumerate(remaining):
            diversity = _feature_distance(item["feature_tokens"], selected_tokens)
            score = (
                0.4 * item["quality_score"]
                + 0.4 * item["outcome_score"]
                + 0.2 * diversity
            )
            payload = {
                **item["branch"],
                "quality_score": round(item["quality_score"], 3),
                "outcome_score": round(item["outcome_score"], 3),
                "diversity_score": round(diversity, 3),
                "parent_rank_score": round(score, 3),
                "outcome_signal": item["outcome_signal"],
                "selection_basis": (
                    "0.4 quality + 0.4 gate leaderboard outcome + "
                    "0.2 diversity"
                ),
            }
            if best_payload is None:
                best_index = index
                best_payload = payload
                continue
            current_key = (
                payload["parent_rank_score"],
                payload["outcome_score"],
                payload["quality_score"],
                payload.get("updated_at") or "",
                payload.get("branch_def_id") or "",
            )
            best_key = (
                best_payload["parent_rank_score"],
                best_payload["outcome_score"],
                best_payload["quality_score"],
                best_payload.get("updated_at") or "",
                best_payload.get("branch_def_id") or "",
            )
            if current_key > best_key:
                best_index = index
                best_payload = payload
        chosen = remaining.pop(best_index)
        selected_tokens.append(chosen["feature_tokens"])
        if best_payload is not None:
            best_payload["rank"] = len(ranked) + 1
            ranked.append(best_payload)

    visible_ids = {entry["branch_def_id"] for entry in ranked}
    visible_outcomes = [
        entry for entry in outcome_entries
        if entry.get("branch_def_id") in visible_ids
    ]
    return {
        "candidates": ranked,
        "outcome_leaderboard": visible_outcomes,
        "selection_basis": (
            "parent candidates ranked by quality, gates leaderboard "
            "outcome, and diversity"
        ),
    }


# ═══════════════════════════════════════════════════════════════════════════
# Phase 6: Outcome gates — ladder on goals, claims per branch
# ═══════════════════════════════════════════════════════════════════════════


def _gate_claim_from_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "claim_id": row["claim_id"],
        "branch_def_id": row["branch_def_id"],
        "goal_id": row["goal_id"],
        "rung_key": row["rung_key"],
        "evidence_url": row["evidence_url"],
        "evidence_note": row["evidence_note"],
        "conformance_pack_id": row["conformance_pack_id"],
        "claimed_by": row["claimed_by"],
        "claimed_at": row["claimed_at"],
        "retracted_at": row["retracted_at"],
        "retracted_reason": row["retracted_reason"],
    }


def set_goal_ladder(
    base_path: str | Path,
    *,
    goal_id: str,
    ladder: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace a Goal's gate ladder. Returns the updated Goal row.

    Raises KeyError if the Goal doesn't exist.
    """
    initialize_author_server(base_path)
    # Ensure goal exists.
    get_goal(base_path, goal_id=goal_id)
    now = _now()
    with _connect(base_path) as conn:
        conn.execute(
            "UPDATE goals SET gate_ladder_json = ?, updated_at = ? "
            "WHERE goal_id = ?",
            (_json_dumps(list(ladder or [])), now, goal_id),
        )
    return get_goal(base_path, goal_id=goal_id)


def get_goal_ladder(
    base_path: str | Path,
    *,
    goal_id: str,
) -> list[dict[str, Any]]:
    """Return the ladder attached to a Goal (may be empty)."""
    goal = get_goal(base_path, goal_id=goal_id)
    return list(goal.get("gate_ladder") or [])


_PROTOCOL_COMPLETED_STATES = {"completed", "skipped", "superseded"}


def current_goal_protocol_step(protocol: list[dict[str, Any]]) -> dict[str, Any] | None:
    """Return the first protocol step that is not terminal."""
    for step in protocol:
        status = str(step.get("status") or "pending").strip().lower()
        if status not in _PROTOCOL_COMPLETED_STATES:
            return step
    return None


def set_goal_branch_protocol(
    base_path: str | Path,
    *,
    goal_id: str,
    protocol: list[dict[str, Any]],
) -> dict[str, Any]:
    """Replace a Goal's ordered Branch protocol/runbook.

    Each step references an existing Branch already bound to the Goal.
    The step payload remains lightweight and domain-agnostic: labels,
    required gate/verdict hints, rollback policy, and next-step
    conditions are carried as JSON for the chatbot/daemon runbook layer.
    """
    initialize_author_server(base_path)
    get_goal(base_path, goal_id=goal_id)
    normalized: list[dict[str, Any]] = []
    for index, raw_step in enumerate(protocol or [], start=1):
        if not isinstance(raw_step, dict):
            raise ValueError(f"protocol step {index} must be an object")
        branch_def_id = str(raw_step.get("branch_def_id") or "").strip()
        if not branch_def_id:
            raise ValueError(f"protocol step {index} missing branch_def_id")
        try:
            branch = get_branch_definition(base_path, branch_def_id=branch_def_id)
        except KeyError as exc:
            raise ValueError(
                f"protocol step {index} references missing Branch '{branch_def_id}'"
            ) from exc
        if branch.get("goal_id") != goal_id:
            raise ValueError(
                f"protocol step {index} Branch '{branch_def_id}' is not bound "
                f"to Goal '{goal_id}'"
            )
        try:
            order = int(raw_step.get("order") or index)
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"protocol step {index} order must be an integer"
            ) from exc
        step = dict(raw_step)
        step["order"] = order
        step["branch_def_id"] = branch_def_id
        step.setdefault("step_id", f"step-{index}")
        step.setdefault("status", "pending")
        step.setdefault("input_artifact_labels", [])
        step.setdefault("output_artifact_labels", [])
        step.setdefault("source_label", "")
        step.setdefault("required_rung_key", "")
        step.setdefault("required_verdict", "")
        step.setdefault("rollback_policy", "")
        step.setdefault("next_step_conditions", [])
        normalized.append(step)
    normalized.sort(key=lambda item: int(item.get("order") or 0))
    return update_goal(
        base_path,
        goal_id=goal_id,
        updates={"branch_protocol": normalized},
    )


def get_goal_branch_protocol(
    base_path: str | Path,
    *,
    goal_id: str,
) -> list[dict[str, Any]]:
    """Return the ordered Branch protocol attached to a Goal."""
    goal = get_goal(base_path, goal_id=goal_id)
    return list(goal.get("branch_protocol") or [])


class BranchRebindError(Exception):
    """Raised when claim_gate would silently rewrite a claim's goal_id.

    Indicates the caller's passed-in goal_id differs from the existing
    claim's denormalized goal_id — i.e. the Branch was rebound to a
    different Goal since the claim landed. Callers must retract the
    stale claim and re-claim under the new Goal, so the old Goal's
    leaderboard keeps its history. Spec §6.2 Debt #2 Option 1.
    """

    def __init__(self, *, original_goal_id: str, current_goal_id: str):
        self.original_goal_id = original_goal_id
        self.current_goal_id = current_goal_id
        super().__init__(
            "claim exists under a different Goal; retract first"
        )


def claim_gate(
    base_path: str | Path,
    *,
    branch_def_id: str,
    goal_id: str,
    rung_key: str,
    evidence_url: str,
    evidence_note: str = "",
    claimed_by: str,
    conformance_pack_id: str = "",
) -> dict[str, Any]:
    """Self-report a rung reached. Idempotent on (branch, rung).

    If an active claim exists for this (branch, rung), update evidence
    and claimed_at and return the row. If a retracted claim exists,
    clear the retraction and reactivate.

    Raises ``BranchRebindError`` when an ACTIVE claim exists under a
    different ``goal_id`` than the caller passed in. Retracted claims
    are resolved intent; re-claim reactivates them under the new Goal.
    """
    initialize_author_server(base_path)
    now_iso = _utc_iso_now()
    with _connect(base_path) as conn:
        existing = conn.execute(
            "SELECT * FROM gate_claims WHERE branch_def_id = ? "
            "AND rung_key = ?",
            (branch_def_id, rung_key),
        ).fetchone()
        if existing is not None:
            existing_goal = existing["goal_id"] or ""
            existing_retracted = existing["retracted_at"]
            # Defense-in-depth rebind guard: reject rather than
            # silently rewriting goal_id on an ACTIVE claim. Handler
            # layer guards too, but the storage layer is the
            # authoritative check so direct callers don't bypass it.
            if (
                not existing_retracted
                and existing_goal != goal_id
            ):
                raise BranchRebindError(
                    original_goal_id=existing_goal,
                    current_goal_id=goal_id,
                )
            conn.execute(
                """
                UPDATE gate_claims
                SET evidence_url = ?, evidence_note = ?, claimed_at = ?,
                    conformance_pack_id = ?, claimed_by = ?, goal_id = ?,
                    retracted_at = NULL, retracted_reason = ''
                WHERE claim_id = ?
                """,
                (
                    evidence_url, evidence_note, now_iso,
                    conformance_pack_id, claimed_by, goal_id,
                    existing["claim_id"],
                ),
            )
            claim_id = existing["claim_id"]
        else:
            claim_id = uuid.uuid4().hex[:16]
            conn.execute(
                """
                INSERT INTO gate_claims (
                    claim_id, branch_def_id, goal_id, rung_key,
                    evidence_url, evidence_note, conformance_pack_id,
                    claimed_by, claimed_at, retracted_at, retracted_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, '')
                """,
                (
                    claim_id, branch_def_id, goal_id, rung_key,
                    evidence_url, evidence_note, conformance_pack_id,
                    claimed_by, now_iso,
                ),
            )
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id = ?",
            (claim_id,),
        ).fetchone()
    return _gate_claim_from_row(row)


def _utc_iso_now() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def get_gate_claim(
    base_path: str | Path,
    *,
    branch_def_id: str,
    rung_key: str,
) -> dict[str, Any] | None:
    """Return the single claim row for (branch, rung), or None."""
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE branch_def_id = ? "
            "AND rung_key = ?",
            (branch_def_id, rung_key),
        ).fetchone()
    if row is None:
        return None
    return _gate_claim_from_row(row)


def retract_gate_claim(
    base_path: str | Path,
    *,
    branch_def_id: str,
    rung_key: str,
    reason: str,
) -> dict[str, Any]:
    """Soft-delete a gate claim. Row stays with retracted_at populated.

    Raises KeyError if no matching claim exists.
    """
    initialize_author_server(base_path)
    now_iso = _utc_iso_now()
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT * FROM gate_claims WHERE branch_def_id = ? "
            "AND rung_key = ?",
            (branch_def_id, rung_key),
        ).fetchone()
        if row is None:
            raise KeyError((branch_def_id, rung_key))
        conn.execute(
            "UPDATE gate_claims SET retracted_at = ?, "
            "retracted_reason = ? WHERE claim_id = ?",
            (now_iso, reason, row["claim_id"]),
        )
        updated = conn.execute(
            "SELECT * FROM gate_claims WHERE claim_id = ?",
            (row["claim_id"],),
        ).fetchone()
    return _gate_claim_from_row(updated)


def list_gate_claims(
    base_path: str | Path,
    *,
    branch_def_id: str = "",
    goal_id: str = "",
    include_retracted: bool = False,
    public_only: bool = False,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List gate claims filtered by branch OR goal.

    Exactly one of ``branch_def_id`` / ``goal_id`` must be provided.
    Claims whose ``rung_key`` is no longer present in the Goal's ladder
    are tagged ``orphaned=True`` in the response.
    """
    if not branch_def_id and not goal_id:
        raise ValueError(
            "list_gate_claims requires branch_def_id or goal_id."
        )
    initialize_author_server(base_path)
    clauses: list[str] = []
    params: list[Any] = []
    if branch_def_id:
        clauses.append("branch_def_id = ?")
        params.append(branch_def_id)
    if goal_id:
        clauses.append("goal_id = ?")
        params.append(goal_id)
    if public_only:
        clauses.append(
            "goal_id IN ("
            "SELECT goal_id FROM goals WHERE visibility = 'public'"
            ")"
        )
    if not include_retracted:
        clauses.append("retracted_at IS NULL")
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(base_path) as conn:
        rows = conn.execute(
            f"""
            SELECT * FROM gate_claims {where}
            ORDER BY claimed_at DESC LIMIT ?
            """,
            (*params, max(1, int(limit))),
        ).fetchall()
    claims = [_gate_claim_from_row(r) for r in rows]
    # Tag orphaned claims (rung no longer in its Goal's ladder).
    ladders: dict[str, set[str]] = {}
    for claim in claims:
        gid = claim.get("goal_id") or ""
        if not gid:
            continue
        if gid not in ladders:
            try:
                ladder = get_goal_ladder(base_path, goal_id=gid)
            except KeyError:
                ladder = []
            ladders[gid] = {
                (r.get("rung_key") or "") for r in ladder
            }
        claim["orphaned"] = claim["rung_key"] not in ladders[gid]
    return claims


def gates_leaderboard(
    base_path: str | Path,
    *,
    goal_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """Rank Branches under a Goal by highest rung reached.

    - Non-retracted claims only.
    - Claims whose rung is no longer in the Goal's ladder are ignored.
    - Tiebreak: earliest ``claimed_at`` wins among equal-rung Branches.
    - Output entries carry rung index so callers can sort / display.

    Returns an empty list if the Goal has no ladder or no claims.
    """
    initialize_author_server(base_path)
    try:
        ladder = get_goal_ladder(base_path, goal_id=goal_id)
    except KeyError:
        return []
    rung_index = {
        (r.get("rung_key") or ""): idx
        for idx, r in enumerate(ladder)
        if r.get("rung_key")
    }
    if not rung_index:
        return []
    # Internal aggregation — include private branches so the
    # branch_name lookup is complete; the MCP handler filters
    # private rows from non-owners at the presentation boundary.
    branches = branches_for_goal(
        base_path, goal_id=goal_id, include_private=True,
    )
    branch_by_id = {b["branch_def_id"]: b for b in branches}
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT * FROM gate_claims
            WHERE goal_id = ? AND retracted_at IS NULL
            """,
            (goal_id,),
        ).fetchall()
    best: dict[str, dict[str, Any]] = {}
    for row in rows:
        bid = row["branch_def_id"]
        rung = row["rung_key"]
        if rung not in rung_index:
            continue
        idx = rung_index[rung]
        claimed_at = row["claimed_at"] or ""
        current = best.get(bid)
        if current is None or idx > current["highest_rung_index"] or (
            idx == current["highest_rung_index"]
            and claimed_at < current["claimed_at"]
        ):
            best[bid] = {
                "branch_def_id": bid,
                "branch_name": branch_by_id.get(bid, {}).get("name", ""),
                "highest_rung_key": rung,
                "highest_rung_index": idx,
                "claimed_at": claimed_at,
                "evidence_url": row["evidence_url"],
            }
    ranked = sorted(
        best.values(),
        key=lambda e: (-e["highest_rung_index"], e["claimed_at"]),
    )
    return ranked[:max(1, int(limit))]


def goal_gate_summary(
    base_path: str | Path,
    *,
    goal_id: str,
) -> dict[str, Any]:
    """Aggregate gate-claim stats for a Goal.

    Phase 6.4. Returns ``{ladder_length, claims_total,
    branches_with_claims, highest_rung_reached}``. Non-retracted,
    non-orphaned claims only — mirrors the leaderboard invariants
    Phase 6.2 ships.

    ``highest_rung_reached`` is the ``rung_key`` with the maximum
    ladder index reached by any Branch on this Goal. Empty string
    when no qualifying claims exist.

    Raises KeyError if the Goal doesn't exist.
    """
    initialize_author_server(base_path)
    ladder = get_goal_ladder(base_path, goal_id=goal_id)
    rung_index = {
        (r.get("rung_key") or ""): idx
        for idx, r in enumerate(ladder)
        if r.get("rung_key")
    }
    ladder_length = len(rung_index)
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT branch_def_id, rung_key
            FROM gate_claims
            WHERE goal_id = ? AND retracted_at IS NULL
            """,
            (goal_id,),
        ).fetchall()
    # Filter orphaned (rung no longer in ladder).
    active = [
        (r["branch_def_id"], r["rung_key"])
        for r in rows
        if r["rung_key"] in rung_index
    ]
    claims_total = len(active)
    branches_with_claims = len({bid for bid, _ in active})
    if active:
        highest_idx = max(rung_index[rk] for _, rk in active)
        # Resolve idx → rung_key via the ladder order. Multiple rungs
        # can share an index only if ladder is malformed; guard with
        # first-match semantics.
        highest_rung_reached = next(
            (rk for rk, idx in rung_index.items() if idx == highest_idx),
            "",
        )
    else:
        highest_rung_reached = ""
    return {
        "ladder_length": ladder_length,
        "claims_total": claims_total,
        "branches_with_claims": branches_with_claims,
        "highest_rung_reached": highest_rung_reached,
    }


def goal_leaderboard(
    base_path: str | Path,
    *,
    goal_id: str,
    metric: str = "run_count",
    limit: int = 20,
    viewer: str = "",
) -> list[dict[str, Any]]:
    """Rank Branches under a Goal by the requested metric.

    v1 metrics:
    - ``run_count`` — Phase 3 ``runs`` table, grouped by ``branch_def_id``.
    - ``forks`` — walk of ``parent_def_id`` chains.
    - ``outcome`` — Phase 6 stub. Returns empty list (the handler turns
      this into a spec-compliant forward-compat response).

    Phase 6.2.2: visibility-aware. ``viewer`` (the actor) is threaded
    to ``branches_for_goal`` so private Branches owned by other actors
    are excluded from the ranked list.

    Unknown metric raises ValueError so the handler can translate it
    into a ``{error, available_metrics}`` response.
    """
    initialize_author_server(base_path)
    metric = (metric or "run_count").strip().lower()
    if metric == "run_count":
        runs_db = Path(base_path) / ".runs.db"
        if not runs_db.exists():
            branches = branches_for_goal(
                base_path, goal_id=goal_id, viewer=viewer,
            )
            return [
                {**b, "metric": metric, "value": 0}
                for b in branches[:limit]
            ]
        # Join requires attaching the runs DB. Use a fresh connection
        # with ATTACH for the scope of this query.
        branches = branches_for_goal(
            base_path, goal_id=goal_id, viewer=viewer,
        )
        branch_ids = [b["branch_def_id"] for b in branches]
        if not branch_ids:
            return []
        placeholders = ",".join("?" for _ in branch_ids)
        import sqlite3 as _sqlite3

        conn = _sqlite3.connect(runs_db, timeout=10.0)
        conn.row_factory = _sqlite3.Row
        try:
            rows = conn.execute(
                f"""
                SELECT branch_def_id, COUNT(*) AS n
                FROM runs
                WHERE branch_def_id IN ({placeholders})
                GROUP BY branch_def_id
                """,
                branch_ids,
            ).fetchall()
            counts = {r["branch_def_id"]: r["n"] for r in rows}
        finally:
            conn.close()
        ranked = sorted(
            branches,
            key=lambda b: counts.get(b["branch_def_id"], 0),
            reverse=True,
        )
        return [
            {**b, "metric": metric, "value": counts.get(b["branch_def_id"], 0)}
            for b in ranked[:limit]
        ]
    if metric == "forks":
        branches = branches_for_goal(
            base_path, goal_id=goal_id, viewer=viewer,
        )
        branch_ids = {b["branch_def_id"] for b in branches}
        # Count Branches anywhere in the DB whose parent_def_id is in
        # our goal's Branch set.
        with _connect(base_path) as conn:
            if branch_ids:
                placeholders = ",".join("?" for _ in branch_ids)
                rows = conn.execute(
                    f"""
                    SELECT parent_def_id, COUNT(*) AS n
                    FROM branch_definitions
                    WHERE parent_def_id IN ({placeholders})
                    GROUP BY parent_def_id
                    """,
                    list(branch_ids),
                ).fetchall()
                counts = {r["parent_def_id"]: r["n"] for r in rows}
            else:
                counts = {}
        ranked = sorted(
            branches,
            key=lambda b: counts.get(b["branch_def_id"], 0),
            reverse=True,
        )
        return [
            {**b, "metric": metric, "value": counts.get(b["branch_def_id"], 0)}
            for b in ranked[:limit]
        ]
    if metric == "outcome":
        entries = gates_leaderboard(
            base_path, goal_id=goal_id, limit=limit,
        )
        return [{**e, "metric": metric, "value": e["highest_rung_index"]}
                for e in entries]
    raise ValueError(f"Unknown metric '{metric}'")


def goal_common_nodes(
    base_path: str | Path,
    *,
    goal_id: str,
    min_branches: int = 2,
    limit: int = 20,
    viewer: str = "",
) -> list[dict[str, Any]]:
    """Return NodeDefinitions that appear in at least ``min_branches``
    Branches under this Goal. Compares on ``node_id`` equality.

    Shape: ``[{node_id, display_name, occurrence_count, branch_ids,
    first_seen_in}]`` ordered by occurrence_count desc.

    Phase 6.2.2: ``viewer`` filters private Branches owned by other
    actors out of the aggregation. Without this filter, the
    ``branch_ids`` / ``first_seen_in`` fields would leak the
    existence of private Branches on shared Goals.
    """
    branches = branches_for_goal(
        base_path, goal_id=goal_id, viewer=viewer,
    )
    counters: dict[str, dict[str, Any]] = {}
    for branch in branches:
        seen_this_branch: set[str] = set()
        for node in branch.get("node_defs") or []:
            nid = node.get("node_id") or ""
            if not nid or nid in seen_this_branch:
                continue
            seen_this_branch.add(nid)
            entry = counters.setdefault(nid, {
                "node_id": nid,
                "display_name": node.get("display_name", nid),
                "occurrence_count": 0,
                "branch_ids": [],
                "first_seen_in": branch["branch_def_id"],
            })
            entry["occurrence_count"] += 1
            entry["branch_ids"].append(branch["branch_def_id"])
    survivors = [
        v for v in counters.values()
        if v["occurrence_count"] >= max(1, int(min_branches))
    ]
    survivors.sort(key=lambda v: v["occurrence_count"], reverse=True)
    return survivors[:max(1, int(limit))]


def goal_common_nodes_all(
    base_path: str | Path,
    *,
    min_branches: int = 2,
    limit: int = 20,
    viewer: str = "",
    public_goals_only: bool = False,
) -> list[dict[str, Any]]:
    """Return NodeDefinitions that appear in at least ``min_branches``
    Branches across ALL Goals (cross-Goal aggregation).

    Same shape as ``goal_common_nodes`` plus a ``goal_ids`` field listing
    every Goal under which the node has been seen. Unbound branches
    (goal_id is None) count toward the aggregate but contribute an empty
    goal entry rather than being dropped, so nodes in unbound pipelines
    still surface. See #62 — bot failed to reuse ``rigor_checker`` across
    research-paper-pipeline and prosecutorial-brief because the per-Goal
    ``goal_common_nodes`` didn't see both.

    Phase 6.2.2: ``viewer`` filters private Branches owned by other
    actors out of the aggregation. Public action callers set
    ``public_goals_only`` so branches bound to non-public Goals cannot
    contribute IDs, nodes, counts, or ranks; unbound branches still count.
    """
    branches = list_branch_definitions(base_path, viewer=viewer)
    public_goal_ids: set[str] | None = None
    if public_goals_only:
        with _connect(base_path) as conn:
            rows = conn.execute(
                "SELECT goal_id FROM goals WHERE visibility = 'public'",
            ).fetchall()
        public_goal_ids = {str(row["goal_id"]) for row in rows}
    counters: dict[str, dict[str, Any]] = {}
    for branch in branches:
        seen_this_branch: set[str] = set()
        branch_goal = branch.get("goal_id") or ""
        if (
            public_goal_ids is not None
            and branch_goal
            and branch_goal not in public_goal_ids
        ):
            continue
        for node in branch.get("node_defs") or []:
            nid = node.get("node_id") or ""
            if not nid or nid in seen_this_branch:
                continue
            seen_this_branch.add(nid)
            entry = counters.setdefault(nid, {
                "node_id": nid,
                "display_name": node.get("display_name", nid),
                "description": node.get("description", ""),
                "occurrence_count": 0,
                "branch_ids": [],
                "goal_ids": [],
                "first_seen_in": branch["branch_def_id"],
            })
            entry["occurrence_count"] += 1
            entry["branch_ids"].append(branch["branch_def_id"])
            if branch_goal and branch_goal not in entry["goal_ids"]:
                entry["goal_ids"].append(branch_goal)
    survivors = [
        v for v in counters.values()
        if v["occurrence_count"] >= max(1, int(min_branches))
    ]
    survivors.sort(key=lambda v: v["occurrence_count"], reverse=True)
    return survivors[:max(1, int(limit))]


def search_nodes(
    base_path: str | Path,
    *,
    query: str = "",
    role: str = "",
    limit: int = 20,
    viewer: str | None = None,
    public_goals_only: bool = False,
) -> list[dict[str, Any]]:
    """Search NodeDefinitions across every Branch by free-text ``query``
    and optional ``role`` (phase) filter.

    Returns cards for the calling tool surface to render as reuse
    candidates. A node is scored by substring hits in ``node_id``,
    ``display_name``, ``description``, and ``prompt_template``; the
    ``reuse_count`` across Branches acts as a tiebreaker.

    Shape: ``[{node_id, display_name, description, phase,
    branch_def_id, reuse_count, prompt_template_preview, goal_ids}]``
    ordered by score desc, reuse_count desc.

    Dedupes on ``node_id``: the first-seen definition wins, and
    ``branch_def_id`` points at the canonical branch (earliest
    published, then first registered). Callers who need all branches
    using a node_id can intersect ``reuse_count`` with the full
    branch list via ``list_branch_definitions``.
    """
    branches = list_branch_definitions(base_path, viewer=viewer or "")
    public_goal_ids: set[str] | None = None
    if public_goals_only:
        with _connect(base_path) as conn:
            rows = conn.execute(
                "SELECT goal_id FROM goals WHERE visibility = 'public'",
            ).fetchall()
        public_goal_ids = {str(row["goal_id"]) for row in rows}
    q = (query or "").strip().lower()
    q_tokens = [t for t in q.split() if t]
    phase_filter = (role or "").strip().lower()

    cards: dict[str, dict[str, Any]] = {}
    reuse_counts: dict[str, int] = {}
    node_goals: dict[str, list[str]] = {}
    for branch in branches:
        branch_goal = branch.get("goal_id") or ""
        if (
            public_goal_ids is not None
            and branch_goal
            and branch_goal not in public_goal_ids
        ):
            continue
        seen_in_branch: set[str] = set()
        for node in branch.get("node_defs") or []:
            nid = node.get("node_id") or ""
            if not nid:
                continue
            if nid not in seen_in_branch:
                seen_in_branch.add(nid)
                reuse_counts[nid] = reuse_counts.get(nid, 0) + 1
                if branch_goal:
                    goals_for_nid = node_goals.setdefault(nid, [])
                    if branch_goal not in goals_for_nid:
                        goals_for_nid.append(branch_goal)
            if nid in cards:
                continue
            cards[nid] = {
                "node_id": nid,
                "display_name": node.get("display_name", nid),
                "description": node.get("description", ""),
                "phase": node.get("phase", ""),
                "branch_def_id": branch["branch_def_id"],
                "prompt_template_preview": _preview(
                    node.get("prompt_template", ""), 160,
                ),
                "source_code_preview": _preview(
                    node.get("source_code", ""), 160,
                ),
            }

    results: list[dict[str, Any]] = []
    for nid, card in cards.items():
        if phase_filter and (card["phase"] or "").lower() != phase_filter:
            continue
        score = 0
        if q_tokens:
            haystack = " ".join([
                nid,
                card["display_name"],
                card["description"],
                card["prompt_template_preview"],
            ]).lower()
            for token in q_tokens:
                hits = haystack.count(token)
                score += hits
                if token == nid.lower():
                    score += 5
                if token in card["display_name"].lower():
                    score += 2
            if score == 0:
                continue
        card["reuse_count"] = reuse_counts.get(nid, 0)
        card["goal_ids"] = list(node_goals.get(nid, []))
        card["_score"] = score
        results.append(card)

    results.sort(
        key=lambda c: (c.get("_score", 0), c.get("reuse_count", 0)),
        reverse=True,
    )
    for card in results:
        card.pop("_score", None)
    return results[:max(1, int(limit))]


def _preview(text: str, max_len: int) -> str:
    """Return a short single-line preview of a longer string."""
    if not text:
        return ""
    collapsed = " ".join(text.split())
    if len(collapsed) <= max_len:
        return collapsed
    return collapsed[: max_len - 1] + "…"


# ═══════════════════════════════════════════════════════════════════════════
# Memory-scope Stage 2a — universe_acl CRUD
# ═══════════════════════════════════════════════════════════════════════════
#
# A universe with zero ACL rows is public; a universe with at least one
# row is private and only the listed actors may access it. Enforcement
# lands in Stage 2b (flag-gated ``TINYASSETS_TIERED_SCOPE``); these
# helpers are infrastructure only for the 2a landing.


_ALLOWED_PERMISSIONS = frozenset({"read", "write", "admin"})


def grant_universe_access(
    base_path: str | Path,
    *,
    universe_id: str,
    actor_id: str,
    permission: str,
    granted_by: str = "",
) -> dict[str, Any]:
    """Grant an actor access to a universe.

    ``permission`` must be one of 'read', 'write', 'admin'. Grants are
    idempotent — regranting updates ``granted_at`` + ``granted_by``.
    Raises ``ValueError`` on unknown permission or empty universe/actor.
    """
    if not universe_id or not actor_id:
        raise ValueError(
            "grant_universe_access requires universe_id and actor_id."
        )
    permission = (permission or "").strip().lower()
    if permission not in _ALLOWED_PERMISSIONS:
        raise ValueError(
            f"Unknown permission {permission!r}; expected one of "
            f"{sorted(_ALLOWED_PERMISSIONS)}."
        )
    now = _now()
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO universe_acl
              (universe_id, actor_id, permission, granted_at, granted_by)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(universe_id, actor_id) DO UPDATE SET
                permission = excluded.permission,
                granted_at = excluded.granted_at,
                granted_by = excluded.granted_by
            """,
            (universe_id, actor_id, permission, now, granted_by or ""),
        )
    return {
        "universe_id": universe_id,
        "actor_id": actor_id,
        "permission": permission,
        "granted_at": now,
        "granted_by": granted_by or "",
    }


def grant_universe_ownership(
    base_path: str | Path,
    *,
    universe_id: str,
    owner_id: str,
) -> None:
    """Create-time grant: the creator's admin row AND the universe's owner row,
    in ONE transaction, so no universe is ever granted but unowned
    (account-storage-quota D2). Raises `OwnershipConflict` when the universe
    already belongs to another account; the admin grant then rolls back too.
    """
    from tinyassets.universe_owner import record_creation

    owner = named_principal(owner_id)
    uid = (universe_id or "").strip()
    if not owner or not uid:
        raise ValueError("grant_universe_ownership requires universe_id and owner_id.")
    now = _now()
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO universe_acl
              (universe_id, actor_id, permission, granted_at, granted_by)
            VALUES (?, ?, 'admin', ?, ?)
            ON CONFLICT(universe_id, actor_id) DO UPDATE SET
                permission = excluded.permission,
                granted_at = excluded.granted_at,
                granted_by = excluded.granted_by
            """,
            (uid, owner, now, owner),
        )
        record_creation(conn, universe_id=uid, owner_id=owner)


def revoke_universe_ownership(
    base_path: str | Path,
    *,
    universe_id: str,
    owner_id: str,
) -> None:
    """Undo `grant_universe_ownership` for a create that failed: the grant and
    the owner row go together, and only if this account is the recorded owner."""
    owner = named_principal(owner_id)
    uid = (universe_id or "").strip()
    if not owner or not uid:
        return
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            "DELETE FROM universe_acl WHERE universe_id = ? AND actor_id = ?",
            (uid, owner),
        )
        conn.execute(
            "DELETE FROM universe_owner WHERE universe_id = ? AND owner_id = ?",
            (uid, owner),
        )


def set_founder_home(
    base_path: str | Path,
    *,
    founder_sub: str,
    universe_id: str,
    platform_generated: bool = False,
) -> None:
    """Bind a founder (WorkOS ``sub``) to their home universe (D10).

    Idempotent — re-binding updates the universe_id. No-op for an empty or
    unbound founder (unsigned callers have no home) or empty universe.

    ``platform_generated`` records whether ``universe_id`` was generated by the
    platform (the caller called ``new_universe_id()`` itself) rather than a
    caller-influenced value. It defaults to ``False``: a general binding setter
    cannot vouch for provenance it did not create, and first-contact must fail
    closed on an unproven binding (universe-creation 5.2). Only pass ``True``
    from a path that provably generated the id. The marker is (re)written on
    both insert and rebind so it always describes the currently bound id.
    """
    founder = named_principal(founder_sub)
    uid = (universe_id or "").strip()
    if not founder or not uid:
        return
    now = _now()
    flag = 1 if platform_generated else 0
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO founder_home (
                founder_sub, universe_id, created_at, platform_generated
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(founder_sub) DO UPDATE SET
                universe_id = excluded.universe_id,
                platform_generated = excluded.platform_generated
            """,
            (founder, uid, now, flag),
        )


def get_founder_home(base_path: str | Path, founder_sub: str) -> str:
    """Return the founder's bound home universe id, or ``""`` if unbound."""
    founder = named_principal(founder_sub)
    if not founder:
        return ""
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT universe_id FROM founder_home WHERE founder_sub = ?",
            (founder,),
        ).fetchone()
    return str(row[0]) if row and row[0] else ""


def founder_home_is_platform_generated(
    base_path: str | Path,
    *,
    founder_sub: str,
    universe_id: str,
) -> bool:
    """Return whether the founder's home binding for ``universe_id`` is a proven
    platform-generated id (universe-creation 5.2 provenance gate).

    Reads the ``platform_generated`` marker of the row keyed by ``founder_sub``
    AND matching ``universe_id`` — matching both ensures the marker describes
    exactly the value the caller is about to trust, not a different id the row
    was later rebound to. Returns ``False`` when no such row exists, so an
    unrecorded or caller-influenced binding fails closed.
    """
    founder = named_principal(founder_sub)
    uid = (universe_id or "").strip()
    if not founder or not uid:
        return False
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT platform_generated FROM founder_home "
            "WHERE founder_sub = ? AND universe_id = ?",
            (founder, uid),
        ).fetchone()
    return bool(row and row[0])


def claim_founder_home(
    base_path: str | Path,
    founder_sub: str,
    candidate_universe_id: str,
) -> str:
    """Atomically reserve ``candidate_universe_id`` as the founder's home iff they
    have none bound yet, and return the WINNING id.

    This serializes concurrent first-contact births (auto-birth on first
    authenticated connect): the ``INSERT ... ON CONFLICT(founder_sub) DO NOTHING``
    lets exactly one caller win the binding under SQLite's write lock, even across
    the ASGI worker threads that run sync MCP tool calls. A caller that loses the
    race (or finds a pre-existing binding) gets the already-bound id back and must
    NOT create a second universe. Returns "" for an unbound founder or an
    empty candidate (no home to claim).

    The fresh reservation is stamped ``platform_generated=1`` iff ``candidate``
    is a well-formed platform serial (``is_universe_serial``): this function's
    contract is to reserve a freshly generated serial, so an inserted serial is
    provably platform-generated. A conflict leaves the existing row's marker
    untouched, so the winner's provenance is whatever was recorded when it was
    first bound (read it via :func:`founder_home_is_platform_generated`).
    """
    from tinyassets.ids import is_universe_serial

    founder = named_principal(founder_sub)
    candidate = (candidate_universe_id or "").strip()
    if not founder or not candidate:
        return ""
    now = _now()
    fresh_flag = 1 if is_universe_serial(candidate) else 0
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        conn.execute(
            """
            INSERT INTO founder_home (
                founder_sub, universe_id, created_at, platform_generated
            )
            VALUES (?, ?, ?, ?)
            ON CONFLICT(founder_sub) DO NOTHING
            """,
            (founder, candidate, now, fresh_flag),
        )
        row = conn.execute(
            "SELECT universe_id FROM founder_home WHERE founder_sub = ?",
            (founder,),
        ).fetchone()
    return str(row[0]) if row and row[0] else candidate


def revoke_universe_access(
    base_path: str | Path,
    *,
    universe_id: str,
    actor_id: str,
) -> bool:
    """Remove an actor's grant from a universe. Returns True if a row
    was deleted, False if none existed.
    """
    if not universe_id or not actor_id:
        return False
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        cursor = conn.execute(
            "DELETE FROM universe_acl "
            "WHERE universe_id = ? AND actor_id = ?",
            (universe_id, actor_id),
        )
    return cursor.rowcount > 0


def list_universe_acl(
    base_path: str | Path,
    *,
    universe_id: str,
) -> list[dict[str, Any]]:
    """Return all grants on a universe, ordered by ``granted_at`` ASC."""
    if not universe_id:
        return []
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        rows = conn.execute(
            """
            SELECT universe_id, actor_id, permission, granted_at, granted_by
            FROM universe_acl
            WHERE universe_id = ?
            ORDER BY granted_at ASC
            """,
            (universe_id,),
        ).fetchall()
    return [
        {
            "universe_id": r["universe_id"],
            "actor_id": r["actor_id"],
            "permission": r["permission"],
            "granted_at": r["granted_at"],
            "granted_by": r["granted_by"],
        }
        for r in rows
    ]


def owned_universe_ids(base_path: str | Path) -> set[str]:
    """Every universe id somebody owns: an ACL grant, or a founder's home.

    THE DEFINITION of a universe (founder, 2026-09-02: "a universe should only
    exist if it belongs to a user"). It used to be "a directory under the data
    root that is not one of four hardcoded names", so the platform's own
    backups, a past prune's archive, ``scratch`` and ``cloud-automation-inputs``
    were all universes -- enumerated, declared ``public`` by the boot backfill,
    and readable by id -- and every new operational directory silently became
    one. The denylist could not be completed: ``lancedb`` (not the listed
    ``lance``), daemon memory, retained inputs, the workspace pool and stored
    offers were already missing from it.

    A home binding counts alongside an ACL row because first contact binds the
    home BEFORE any grant is written; a universe with a live founder must never
    depend on which of the two landed first.

    Ownership here is "somebody has a row", not "somebody is admin" -- a
    universe shared write-only is still somebody's. Callers needing a specific
    permission ask :func:`universe_access_permission`.
    """
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        owned = {
            str(row["universe_id"])
            for row in conn.execute("SELECT DISTINCT universe_id FROM universe_acl")
            if str(row["universe_id"] or "").strip()
        }
        owned |= {
            str(row["universe_id"])
            for row in conn.execute("SELECT DISTINCT universe_id FROM founder_home")
            if str(row["universe_id"] or "").strip()
        }
    return owned


def owned_universe_id(base_path: str | Path, name: str) -> str:
    """The owned universe id ``name`` refers to, or ``""`` when nobody owns it.

    EXACT MATCH, deliberately. An earlier revision resolved case-insensitively so
    that a directory restored from a backup as ``U-Mine`` would still be the
    ownership row's ``u-mine``. That is a trap, because a universe id is TWO
    things at once: a path component (`_universe_dir`) and an authority key
    (`universe_access_permission` matches it with exact SQL). Resolving them to
    different spellings breaks whichever one gets the other's answer --
    returning the row's spelling opens a path that does not exist on a
    case-sensitive filesystem, and returning the directory's spelling denies an
    owner's write and, worse, makes `universe_is_private` find no rows, so the
    other spelling reads as a PUBLIC universe (Codex review, 2026-09-26, P0).

    Requiring the two to be identical is the only arrangement in which they
    cannot disagree. A directory restored under a different case is therefore
    unowned: invisible, never deleted, and named by
    `scripts/universe_ownership_inventory.py` as at-risk so the missing row gets
    written rather than guessed at.

    A dotted name is never a universe, whatever a row says: ``.deleting/`` is
    account deletion's staging directory, and a row naming it must not make it
    readable.
    """
    candidate = (name or "").strip()
    if not candidate or candidate.startswith("."):
        return ""
    return candidate if candidate in owned_universe_ids(base_path) else ""


def universe_is_private(base_path: str | Path, *, universe_id: str) -> bool:
    """True if the universe has any ACL rows (= private).

    Public universes have zero rows. The Stage 2b enforcement path
    short-circuits on public universes; this helper is the single
    source of truth for that check.
    """
    if not universe_id:
        return False
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT 1 FROM universe_acl WHERE universe_id = ? LIMIT 1",
            (universe_id,),
        ).fetchone()
    return row is not None


def universe_access_permission(
    base_path: str | Path,
    *,
    universe_id: str,
    actor_id: str,
) -> str:
    """Return the actor's permission on a universe, or '' if no grant.

    Public universes (no ACL rows) return 'read' by convention so the
    Stage 2b enforcement path can treat "public universe, any actor"
    uniformly with "private universe, granted reader."
    """
    if not universe_id or not actor_id:
        return ""
    if not universe_is_private(base_path, universe_id=universe_id):
        return "read"
    initialize_author_server(base_path)
    with _connect(base_path) as conn:
        row = conn.execute(
            "SELECT permission FROM universe_acl "
            "WHERE universe_id = ? AND actor_id = ?",
            (universe_id, actor_id),
        ).fetchone()
    if row is None:
        return ""
    return row["permission"] or ""
