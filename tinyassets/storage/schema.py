"""Initialize the in-box application stores and their explicit legacy upgrades.

The one-shot box_layout_migration owns physical placement and copies existing
rows before cutover. These declarations never traverse another owner's store.
"""
from __future__ import annotations

import threading
from pathlib import Path

from tinyassets.sqlite_schema import ensure_table
from tinyassets.storage import (
    _connect,
    accounts_schema,
    authors_schema,
    catalog_schema,
    db_path,
    deletion_schema,
    notes_schema,
    requests_schema,
    universes_schema,
)

STORES = (
    accounts_schema,
    universes_schema,
    authors_schema,
    requests_schema,
    notes_schema,
    catalog_schema,
    deletion_schema,
)
TABLES = {name: ddl for store in STORES for name, ddl in store.TABLES.items()}
INDEXES = tuple(ddl for store in STORES for ddl in store.INDEXES)

_AUTHOR_SERVER_INIT_LOCK = threading.Lock()
_AUTHOR_SERVER_INITIALIZED: set[str] = set()


def initialize_author_server(base_path: str | Path) -> Path:
    """Ensure the in-box application database exists and is migrated.

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
    with _connect(base_path) as conn:
        # Missing tables start at the current layout. Existing durable tables
        # pass through the explicit additive migrations below before validation.
        for name, ddl in TABLES.items():
            if conn.execute("SELECT 1 FROM sqlite_schema WHERE type='table' AND name=?",
                            (name,)).fetchone() is None:
                ensure_table(conn, ddl, kind="durable")
        # NOT in the schema script: `executescript` autocommits, so the table
        # would exist before its backfill committed, and a crash between the two
        # would skip the backfill forever (PR #4139). The table and
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
        # from existing goals.canonical_branch_version_id. ON CONFLICT DO NOTHING
        # makes the migration idempotent: re-running on an already-backfilled
        # DB hits the (goal_id, scope_token='') primary key collision and
        # skips. Goals with NULL canonical_branch_version_id are excluded.
        conn.execute(
            "INSERT INTO canonical_bindings "
            "(goal_id, scope_token, branch_version_id, "
            " bound_by_actor_id, bound_at, visibility) "
            "SELECT goal_id, '', canonical_branch_version_id, "
            "       author, updated_at, 'public' "
            "FROM goals "
            "WHERE canonical_branch_version_id IS NOT NULL ON CONFLICT DO NOTHING"
        )
        # Preserve every existing Goal default as the empty-actor row.
        # ON CONFLICT DO NOTHING keeps retries idempotent and never overwrites
        # a newer row written by the dual-write path.
        conn.execute(
            "INSERT INTO goal_canonicals "
            "(goal_id, scope_actor, branch_version_id, set_at, set_by) "
            "SELECT goal_id, '', canonical_branch_version_id, updated_at, author "
            "FROM goals "
            "WHERE canonical_branch_version_id IS NOT NULL ON CONFLICT DO NOTHING"
        )
        for ddl in TABLES.values():
            ensure_table(conn, ddl, kind="durable")
        for ddl in INDEXES:
            conn.execute(ddl)
    from tinyassets.daemon_server import ensure_default_author

    ensure_default_author(base_path)
    return db_path(base_path)

