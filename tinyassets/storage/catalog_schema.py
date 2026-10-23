"""Durable catalog tables; declarations owned by the storage layer."""

TABLES = {
    'branch_definitions': """CREATE TABLE IF NOT EXISTS branch_definitions (
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
        updated_at REAL NOT NULL,
        goal_id TEXT,
        visibility TEXT NOT NULL DEFAULT 'public',
        default_llm_policy_json TEXT,
        concurrency_budget INTEGER,
        fork_from TEXT DEFAULT NULL)""",
    'goals': """CREATE TABLE IF NOT EXISTS goals (
        goal_id     TEXT PRIMARY KEY,
        name        TEXT NOT NULL,
        description TEXT NOT NULL DEFAULT '',
        author      TEXT NOT NULL DEFAULT '',
        tags_json   TEXT NOT NULL DEFAULT '[]',
        visibility  TEXT NOT NULL DEFAULT 'public',
        created_at  REAL NOT NULL,
        updated_at  REAL NOT NULL,
        gate_ladder_json TEXT NOT NULL DEFAULT '[]',
        branch_protocol_json TEXT NOT NULL DEFAULT '[]',
        canonical_branch_version_id TEXT DEFAULT NULL,
        canonical_branch_history_json TEXT NOT NULL DEFAULT '[]',
        auto_canonical_via_leaderboard INTEGER NOT NULL DEFAULT 0,
        min_completed_runs_for_canonical INTEGER NOT NULL DEFAULT 5,
        selector_branch_version_id TEXT DEFAULT NULL)""",
    'gate_claims': """CREATE TABLE IF NOT EXISTS gate_claims (
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
    )""",
    'canonical_bindings': """CREATE TABLE IF NOT EXISTS canonical_bindings (
        goal_id            TEXT NOT NULL,
        scope_token        TEXT NOT NULL DEFAULT '',
        branch_version_id  TEXT NOT NULL,
        bound_by_actor_id  TEXT NOT NULL,
        bound_at           REAL NOT NULL,
        visibility         TEXT NOT NULL DEFAULT 'public',
        PRIMARY KEY (goal_id, scope_token),
        FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
    )""",
    'goal_canonicals': """CREATE TABLE IF NOT EXISTS goal_canonicals (
        goal_id            TEXT NOT NULL,
        scope_actor        TEXT NOT NULL DEFAULT '',
        branch_version_id  TEXT NOT NULL,
        set_at             REAL NOT NULL,
        set_by             TEXT NOT NULL,
        PRIMARY KEY (goal_id, scope_actor),
        FOREIGN KEY (goal_id) REFERENCES goals(goal_id)
    )""",
    'unreconciled_writes': """CREATE TABLE IF NOT EXISTS unreconciled_writes (
        id           INTEGER PRIMARY KEY AUTOINCREMENT,
        recorded_at  TEXT NOT NULL,
        helper_name  TEXT NOT NULL,
        paths_json   TEXT NOT NULL,
        row_ref      TEXT NOT NULL DEFAULT '',
        git_error    TEXT NOT NULL DEFAULT ''
    )""",
}

INDEXES = (
    "CREATE INDEX IF NOT EXISTS idx_branch_defs_fork_from ON branch_definitions(fork_from)",
    """CREATE INDEX IF NOT EXISTS idx_branch_defs_author
        ON branch_definitions(author)""",
    """CREATE INDEX IF NOT EXISTS idx_branch_defs_published
        ON branch_definitions(published)""",
    """CREATE INDEX IF NOT EXISTS idx_branch_defs_parent
        ON branch_definitions(parent_def_id)""",
    """CREATE INDEX IF NOT EXISTS idx_branch_defs_domain
        ON branch_definitions(domain_id)""",
    """CREATE INDEX IF NOT EXISTS idx_goals_author ON goals(author)""",
    """CREATE INDEX IF NOT EXISTS idx_goals_visibility ON goals(visibility)""",
    """CREATE INDEX IF NOT EXISTS idx_gate_claims_goal
        ON gate_claims(goal_id)""",
    """CREATE INDEX IF NOT EXISTS idx_gate_claims_branch
        ON gate_claims(branch_def_id)""",
    """CREATE INDEX IF NOT EXISTS idx_canonical_bindings_goal
        ON canonical_bindings(goal_id)""",
    """CREATE INDEX IF NOT EXISTS idx_canonical_bindings_actor
        ON canonical_bindings(bound_by_actor_id)""",
    """CREATE INDEX IF NOT EXISTS idx_canonical_bindings_branch_ver
        ON canonical_bindings(branch_version_id)""",
    """CREATE INDEX IF NOT EXISTS idx_canonical_bindings_scope_goal
        ON canonical_bindings(scope_token, goal_id)""",
    """CREATE INDEX IF NOT EXISTS idx_goal_canonicals_branch_version
        ON goal_canonicals(branch_version_id)""",
    """CREATE INDEX IF NOT EXISTS idx_unreconciled_writes_at
        ON unreconciled_writes(recorded_at)""",
)
