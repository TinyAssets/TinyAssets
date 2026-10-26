-- Pre-PR DDL from eb582d82d08d5de30464c44bbc4e8c557d873756.

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
    -- visibility='deleted'. See openspec/specs/community-patch-loop/spec.md.
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
    -- docs/architecture.md.
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
