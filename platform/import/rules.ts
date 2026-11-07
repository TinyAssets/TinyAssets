/**
 * Rule 0 of the cutover import (`import_research.md` §2): a reviewed rule for every store,
 * table and entry the old stack keeps, so the census and the bundle builder never guess.
 * A store, table or entry no rule names stops that owner loudly; it is never read as a
 * default. Every rule says what the import does with it:
 *
 * - `carry`: written live into the owner's object (admission, tier, the policy and the
 *   collections), and kept in the owner's archive beside it;
 * - `archive`: kept verbatim, read-only, under `/workspace/imported/` as the owner's files;
 * - `drop`: kept only in the final backup (credentials and sign-ins, which the owner
 *   connects again; run internals; the platform's own state).
 *
 * A store's tables have a rule each, or the store one rule for all of them: `archive`, a
 * byte copy that keeps every table, or `drop`, for a store that is the platform's own
 * whole (credentials, locks, sessions, receipts). Where the old stack lays it out:
 * `SHARED` is the shared data volume's root (`_data/`); `ACCOUNT` an owner's broker stores
 * (`.broker/accounts/<sha256(principal)>/`); `ROOT` the owner's volume image; `CENTER` one
 * command center in it; `SESSIONS` a center's `.agent-sessions/<center>/`.
 *
 * Sources: the old stack's own reviewed tables (`tinyassets/box_layout_migration.py`
 * `_census_rules`, `command_center_layout.py` `classify`, `storage_accounting.py`
 * `ROOT_ENTRIES`), read 2026-11-07 at `57e89b46b`, then swept: every `CREATE TABLE` of
 * `tinyassets/` placed in the store its module opens (`guards/old-tables.json`, which the
 * guards hold every table-mapped store to), and every name the old code joins onto a data
 * root or a center. The old code is deleted at the cutover; these rules are this job's own
 * from then on.
 */

export type Use = "carry" | "archive" | "drop";

/** A SQLite store's rule: one for each of its tables, or one for the store whole. */
export interface StoreRule {
  readonly kind: "store";
  readonly tables: Readonly<Record<string, Use>> | "archive" | "drop";
  readonly why: string;
}

/** An entry the import carries as the owner's files, every name below it walked. */
export interface UserRule {
  readonly kind: "user";
  readonly why: string;
}

/** An entry kept only in the final backup, not walked. */
export interface DropRule {
  readonly kind: "drop";
  readonly why: string;
}

/** A folder whose own entries each have a rule. */
export interface DirRule {
  readonly kind: "dir";
  readonly entries: Level;
  readonly why: string;
}

/**
 * A folder that must be absent or empty: what it holds is an unfinished erasure, refused
 * loudly (`unfinished_erasure`), never dropped and never carried.
 */
export interface EmptyRule {
  readonly kind: "empty";
  readonly why: string;
}

export type EntryRule = StoreRule | UserRule | DropRule | DirRule | EmptyRule;
export type Level = Readonly<Record<string, EntryRule>>;

/** Each name in each group with that group's use; a name in two groups is refused. */
function uses(groups: Partial<Record<Use, string>>): Readonly<Record<string, Use>> {
  const found: Record<string, Use> = {};
  for (const [use, names] of Object.entries(groups) as [Use, string][]) {
    for (const name of names.trim().split(/ +/)) {
      if (Object.hasOwn(found, name)) {
        throw new Error(`rule 0 names the table ${name} twice`);
      }
      found[name] = use;
    }
  }
  return Object.freeze(found);
}

/** One level from its parts; a name two parts give is refused. */
function level(...parts: readonly (readonly [string, EntryRule])[][]): Level {
  const found: Record<string, EntryRule> = {};
  for (const [name, rule] of parts.flat()) {
    if (Object.hasOwn(found, name)) {
      throw new Error(`rule 0 names the entry ${name} twice`);
    }
    found[name] = rule;
  }
  return Object.freeze(found);
}

/** Each name in `names` (split on spaces) with `rule`. */
function named(names: string, rule: EntryRule): (readonly [string, EntryRule])[] {
  return names.trim().split(/ +/).map((name) => [name, rule] as const);
}

const store = (tables: StoreRule["tables"], why: string): StoreRule =>
  Object.freeze({ kind: "store", tables, why });
const user = (why: string): UserRule => Object.freeze({ kind: "user", why });
const drop = (why: string): DropRule => Object.freeze({ kind: "drop", why });
const dir = (entries: Level, why: string): DirRule => Object.freeze({ kind: "dir", entries, why });
const empty = (why: string): EmptyRule => Object.freeze({ kind: "empty", why });

/**
 * `account_deletion.py`'s two folders. Nothing writes them today (account deletion runs
 * through the broker's deletion job), so what one holds was left by earlier code: a home
 * staged for removal (`.deleting/<home>/`), or the receipt of an account deletion that did
 * not finish every phase (`.account-deletions/<fingerprint>-<time>.json`), whose person's
 * rows may still be in the stores the import would archive.
 */
const UNFINISHED_ERASURE = empty("an account deletion earlier code left unfinished " +
                                 "(`account_deletion.py`): fine absent or empty, refused " +
                                 "with anything in it");

const CREDENTIALS = "credentials and sign-ins: every owner connects again (research §2)";
const RUNTIME = "the platform's own runtime state, locks and receipts";
const MEMORY = "the owner's memory and history: kept verbatim (research §2)";

/**
 * The main store's tables (`storage/schema.py` `STORES` and every module that opens
 * `storage.db_path`): in the image root (the owner-box move's owner rows), in a center
 * (`conversation_custody.py` keeps a center's own, `<center>/.tinyassets.db`), and under its
 * two older names (`storage/__init__.py` `_LEGACY_DB_FILENAMES`). Definitions, notes,
 * requests, turns and conversations the owner or their agent wrote are archived; branches,
 * queues, grants, receipts and the run machinery drop.
 */
const MAIN_TABLES = Object.freeze({
  archive: "branch_definitions goals universe_notes universe_rules user_requests " +
             "universe_work_targets universe_hard_priorities founder_home scheduled_work " +
             "agent_definitions author_definitions agent_turns agent_turn_rounds " +
             "agent_turn_tools universe_app_ui universe_app_ui_asset" +
           " conversation_custody_threads conversation_custody_messages inbound_receipts",
  drop: "deleted_principals llm_credential_deposit_owners llm_credential_custody " +
          "llm_credential_deposit_commits llm_credential_refresh_state author_forks " +
          "agent_component_lineage commons_bundles commons_bundle_sources " +
          "commons_bundle_versions command_center_release_series command_center_releases " +
          "command_center_release_evidence universes author_runtime_instances branches " +
          "universe_snapshots vote_windows action_records universe_acl request_admissions " +
          "branch_tasks_v2 branch_tasks_v2_quarantine request_admission_rollouts " +
          "automation_activations cloud_automation_controls cloud_automation_slice_triggers " +
          "cloud_automation_terminal_receipts background_branch_bindings " +
          "cloud_automation_continuations cloud_execution_continuations " +
          "app_principal_mappings app_channel_bindings operation_scopes action_approvals " +
          "agent_bindings action_result_outbox webhook_admissions webhook_inflight " +
          "assigned_queue_refusals provider_work_receipts provider_assignment_candidates " +
          "universe_owner request_storage_migration provider_work_bindings " +
          "provider_assignments universe_model_preferences connection_disconnections " +
          "native_shortlist_cache owner_view_receipts learned_model_evidence " +
          "learned_model_source_evidence refused_model_marks account_timezone owner_ui_prefs " +
          "command_center_adoptions command_center_update_requests " +
          "command_center_update_policies command_center_policy_requests " +
          "command_center_auto_receipts command_center_auto_status agent_request_usage " +
          "agent_request_attempts agent_request_usage_links agent_request_dispatches " +
          "agent_import_stages agent_interchange_idempotency agent_conversion_receipts " +
          "agent_conversion_receipt_links agent_conversion_receipt_owners capability_grants " +
          "webhook_hooks branch_heads vote_ballots request_admission_events " +
          "provider_work_execution_claims provider_invocation_reservations " +
          "served_provider_budget_reservations background_branch_attempts gate_claims " +
          "canonical_bindings goal_canonicals unreconciled_writes " +
          "branch_tasks_v2_maintenance_state scoped_reset_leases scoped_reset_operations " +
          "background_branch_authority_owners action_result_receipts app_event_admissions " +
          "webhook_deliveries owner_fence external_agent_catalogue_receipts " +
          "external_agent_cli_invocations external_agent_job_receipts user_accounts " +
          "user_sessions" +
        " agent_model_launches inbound_bindings inbound_installation_view " +
        "inbound_shared_bindings automation_activations_new " +
        "conversation_custody_database_binding conversation_custody_consumed_grants " +
        "conversation_custody_deletions conversation_custody_idempotency",
});
const MAIN = store(uses(MAIN_TABLES), "definitions, notes, requests, turns and conversations " +
                   "the owner or their agent wrote are archived; branches, queues, grants, " +
                   "receipts and the run machinery drop");
const MAIN_STORE_NAMES = Object.freeze([".tinyassets.db", ".workflow.db", ".author_server.db"]);

/**
 * The runs store's tables (`runs.py`, `runs_db_path` and the modules that open it: handoffs,
 * outcomes, attribution, conformance packs, branch versions, schedules, workspaces, run
 * files, deliveries, gate events): what the owner's agent defined, claimed or handed off is
 * archived (none goes live); run internals drop.
 */
const RUNS = store(uses({
  archive: "branch_versions branch_schedules branch_subscriptions teammate_messages handoff " +
           "handoff_transition outcome_event outcome_evidence outcome_evidence_transition " +
           "outcome_artifact outcome_artifact_source attribution_credit attribution_edge " +
           "conformance_pack handoff_artifacts",
  drop: "runs run_events run_cancels run_terminal_outbox run_judgments run_receipts " +
        "run_file_operations run_file_allocations run_file_objects run_file_bindings " +
        "run_file_cleanup run_input_admissions run_lineage run_child_attachments " +
        "node_edit_audit branch_versions_migrations workspace_push_intents " +
        "scheduler_delivered_events contribution_events conversation_run_admissions " +
        "conversation_terminal_projections conversation_projection_cutover " +
        "graph_receivers graph_output_links graph_deliveries graph_delivery_attempts " +
        "graph_delivery_files graph_delivery_answers gate_event gate_event_cite " +
        "workspace_generations workspace_leases workspace_ledger workspace_outbox " +
        "workspace_waiters workspace_locks handoff_confirmation",
}), "what the owner's agent defined, claimed or handed off is archived (none goes live); run " +
    "internals drop");

// ---------------------------------------------------------------------------------------
// The shared data volume
// ---------------------------------------------------------------------------------------

/** The broker's fleet state: who the owners are and what they hold. */
const BROKER_STATE: Level = level([
  ["owner-identities.db", store(uses({
    carry: "owner_identities owner_admission",
    drop: "owner_volumes owner_snapshots box_records owner_box_secrets owner_box_revocations " +
          "routing_sequence deletion_jobs center_admissions",
  }), "who the owners are (the principal is the owner's name) and their admission; the " +
      "volume bindings are read by the census's backup gate, and the deletion jobs and the " +
      "deleted revocations are erasure facts (`erasure.ts`)")],
  ["next-wake.db", store("drop", "old wake times: no schedule goes live (research §2)")],
  ["ops.db", store("drop", RUNTIME)],
], named("fence.json architecture-signing.json", drop(RUNTIME)));

/** One owner's own broker stores (`.broker/accounts/<sha256(principal)>/`). */
export const ACCOUNT: Level = level([
  ["policy.db", store(uses({
    carry: "policy", archive: "receipts proposals", drop: "once_approvals spend_reservations",
  }), "the policy document carries (the new validator decides); its receipts and proposals " +
      "are audit history; one-time approvals expire in minutes and the spend windows start " +
      "fresh")],
  ["message_ledger.db", store("archive", "mail to outside agents whose grants do not carry " +
                              "(ADR-036), kept as the owner's record")],
  ["conversations.db", store(uses({
    carry: "collection_rows", archive: "message_bodies", drop: "collection_seqs",
  }), "collections carry through the object's write path, their old writers and times in " +
      "the archive; the high-water seq is the new object's own")],
  ["credentials", drop(CREDENTIALS)],
  [".deleted", drop("an erased owner's marker: the census counts it")],
]);

/** The shared volume's root: the router's and the broker's stores, never an owner's files. */
export const SHARED: Level = level([
  [".broker", dir(level([
    ["state", dir(BROKER_STATE, "the broker's fleet state")],
    ["plans.db", store(uses({ carry: "plans", drop: "checkout_settlements" }),
                       "the tier carries; the Stripe ids go to the private cutover record")],
    ["credential-custody.db", store(uses({
      carry: "deleted_principals",
      drop: "llm_credential_deposit_owners llm_credential_custody " +
            "llm_credential_deposit_commits llm_credential_refresh_state box_model_sources",
    }), "erasure tombstones carry where their principal is known; credential custody drops")],
    ["outbound.db", store("drop", CREDENTIALS)],
    ["inbound-installations.db", store("drop", "shared-app installs are made again")],
    ["mcp-events.db", store("drop", "event subscriptions are made again by each client")],
    ["accounts", drop("each owner's own stores, read by `ACCOUNT`")],
  ], named("credential-vault browser-vault", drop(CREDENTIALS))), "the credential broker's")],
  ...MAIN_STORE_NAMES.map((name) => [name, store(uses({
    drop: `${MAIN_TABLES.archive} ${MAIN_TABLES.drop}`,
  }), "the router's copy of the main store (its accounts and sessions, and whatever the " +
      "store's modules make in it): every owner's own rows are in their image, and AuthKit " +
      "signs each owner in")] as const),
  [".api-keys.sqlite3", store(uses({ drop: "api_keys api_key_request_counts" }),
                              "API keys are minted again (#5343: nothing is imported)")],
  [".hook-owners.sqlite3", store(uses({ drop: "hook_owners inbound_endpoints" }),
                                 "capability URLs target branches, which are gone; the census " +
                                 "counts them for the founder")],
  [".router-deleted-owners.sqlite3", store(uses({ carry: "deleted_owners" }),
                                           "erasure tombstones, by principal")],
  [".erasure-ledger.sqlite3", store(uses({ drop: "events initialized" }),
                                    "the router's erasure work log: each entry is an erasure " +
                                    "fact (`erasure.ts`)")],
  [".workos-revocations.sqlite3", store(uses({ drop: "revocations" }),
                                        "bearer revocations: sign-ins start again, and a " +
                                        "deleted one is an erasure fact (`erasure.ts`)")],
  [".boxhost", dir(level([
    ["boxhost.db", store(uses({ drop: "host boxes ops execs owner_destroys" }),
                         "the box host's generations and receipts: each owner destroy is an " +
                         "erasure fact (`erasure.ts`)")],
  ], named("boxhost.lock", drop(RUNTIME))), "the box host's private state")],
  [".owner_devices.db", store("drop", "the app registers again")],
  [".outside-client-authority.sqlite3", store("drop", "outside clients are made at each " +
                                              "client's first call (#5343)")],
  [".runtime", drop("sign-in and approval sessions: " + CREDENTIALS)],
  [".published", drop("shared copies of published definitions; there is no commons now")],
], named(".auth.db .hosted-model-auth.db", store("drop", CREDENTIALS)),
   named(".inbound-routing.sqlite3 .remote-ta-receipts.sqlite3 metering.db",
         store("drop", RUNTIME)),
   named(".source_channel_policy.db", store("drop", "a retired store nothing reads " +
                                             "(`box_layout_migration.py` `RETIRED_STORES`)")),
   named(".connect", drop(CREDENTIALS)),
   named(".locks .layout.json .layout.lock .owner-cutover.json " +
         ".ordinary-admission.json .router-credentials.json .disaster-restore.json " +
         ".platform-recovery.json platform-expected-instance.json release-state.json " +
         ".billing_webhook_verified.json .custody.lock .deploy-pending.json", drop(RUNTIME)),
   named(".deleting .account-deletions", UNFINISHED_ERASURE));

// ---------------------------------------------------------------------------------------
// The owner's volume image
// ---------------------------------------------------------------------------------------

/**
 * The stores the owner-box move extracted into the owner's image root, each table by
 * `_census_rules`. Run internals, leases and checkpoints drop; tables that hold what the
 * owner or their agent wrote (definitions, notes, requests, schedules, turns) are archived,
 * since nothing fires with a new meaning (research §2: no schedule goes live).
 */
const ROOT_STORES: (readonly [string, EntryRule])[] = [
  [".tinyassets.db.prebudgetrepair.bak", store("drop", "a stale copy the old stack " +
                                              "dropped by name (`ROOT_ENTRIES`)")],
  [".centers.db", store(uses({ drop: "centers" }),
                        "the centers list: each center is carried as a folder")],
  ...MAIN_STORE_NAMES.map((name) => [name, MAIN] as const),
  [".runs.db", RUNS],
  [".langgraph_runs.db", store("drop", "run checkpoints (LangGraph's own tables)")],
  [".control_plane.db", store(uses({ archive: "triggers trigger_fires" }),
                              "old triggers, archived: none fires with a new meaning")],
  [".automations.db", store(uses({
    archive: "automations automation_attempts",
    drop: "universe_leases automations__rebuild command_center_auto_receipts " +
          "command_center_auto_status",
  }), "old automations, archived: the agent may make them again")],
  [".account_seats.db", store(uses({ drop: "account_seats seat_waiters chat_turn_failures" }),
                              RUNTIME)],
  [".engine_run_admissions.db", store(uses({ drop: "admissions dispatch_budget settlements" }),
                                      RUNTIME)],
  [".effector_consents.db", store("drop", "old consents: the new policy decides")],
  [".storage_accounting.db", store("drop", "the old quota ledger")],
  [".owner_devices.db", store("drop", "the app registers again")],
  [".outside-client-authority.sqlite3", store("drop", "outside clients are made again")],
  ["checkpoints.db", store("drop", "run checkpoints")],
  [".command-center-packages", dir(level([
    ["packages.db", store(uses({
      archive: "pins extension_activations extension_bindings extension_revisions",
      drop: "blobs package_versions extension_hook_events extension_ui_projections",
    }), "the owner's installed packages and pins, archived; published blobs drop with the " +
        "commons")],
  ]), "the owner's packages")],
  ...named(".owner_leases.db wiki_trigger_attempts.db .node_eval.db " +
           ".external_write_receipts.db .idempotency.db .universe_seats.db",
           store("drop", RUNTIME)),
  ...named(".project_memory.db daemon_brain.db episodic.db promises.db temporal.db " +
           "versions.db draft_versions.db knowledge.db story.db", store("archive", MEMORY)),
];

/** A center's harness sessions (`.agent-sessions/<center>/`). */
export const SESSIONS: Level = level([
  ["history.db", store("archive", "the harness's change history: " + MEMORY)],
  ["agent-activities.db", store("drop", "the agent's activity feed")],
  ["steering.db", store("drop", "mid-turn messages, emptied at every turn's end")],
  ["activity.db", store("drop", "the live view's recent tool calls")],
  ["owner-control.lock", drop("the lock every owner-control write takes, never removed " +
                              "(`owner_control.py`): empty")],
  ["request-continuation.lock", drop("the bound answers' wake sweep's lock " +
                                     "(`control_plane/continuations.py`): empty")],
]);

/**
 * The names a level's own code mints, each family by its exact pattern (the minted part's
 * own grammar), never a looser one.
 */
const MINTED_IN: ReadonlyMap<Level, readonly (readonly [RegExp, EntryRule])[]> = new Map([
  [SESSIONS, [
    // `bound_requests.py` `execution_attempt`: `request-<sha256 of the request id>.lock`.
    [/^request-[0-9a-f]{64}\.lock$/, drop("one bound request's execution lock: empty")],
    // `agent_sessions.py` `_record_path`: `<sha256 of the thread key, 32 hex>.json`, which
    // native session a thread resumed. An engine's session cannot be carried (Fact 8), and
    // only readers of these remain: records earlier code left.
    [/^[0-9a-f]{32}\.json$/, drop("which engine session a thread resumed: an engine's " +
                                  "session is never carried (Fact 8)")],
  ]],
]);

/** The image root: its own entries and stores; each center is matched by name (`CENTER`). */
export const ROOT: Level = level(ROOT_STORES, [
  [".agent-home", drop("the engine CLIs' HOME: sign-ins and caches (research §2)")],
  [".ta-cli", drop(CREDENTIALS)],
  [".universe-sidecars", drop("per-center daemon sockets and consents")],
  [".agent-sessions", drop("each center's sessions, read by `SESSIONS`")],
  [".source_channel_policy.db", store("drop", "a retired store nothing reads " +
                                      "(`box_layout_migration.py` `RETIRED_STORES`)")],
  [".remote-ta-receipts.sqlite3", store("drop", "a remote box's call receipts for retries")],
], named("wiki .wiki daemon_wikis", user("pages the owner and their agent wrote")),
   named("knowledge", user("the owner's knowledge as files (`knowledge_authority.py`: " +
                           "`<owner root>/knowledge/`)")),
   named("apps", user("the owner's apps' documents (`app_room.py`: " +
                      "`<root>/apps/<slug>/app.json`)")),
   named("starter", user("the starter's owner files (`starter/muse/checkins.py`: settings, " +
                         "goals, monitors)")),
   named("founder_offers", drop("market offers (`scoped_reset.py`): no market now")),
   named(".deleting .account-deletions", UNFINISHED_ERASURE),
   named(".owner-boxd .runtime .workspace-staging .agent-turn-runners .consumer_liveness " +
         ".owner_tree .universe-tool-slots .outbound-proxy .run-execution-locks " +
         ".run-file-operation-locks .delivery-locks .workspace-family-locks " +
         ".run_recovery.lock .run_recovery.lock.pid .ui-preview.lock .deploy-pending.json " +
         ".runtime_status.json .worker_supervisor.json .engine_mcp_http_routes.json " +
         ".scoped-reset.barrier .scoped-reset-journal .scoped-reset-staging .locks " +
         ".layout.json .layout.lock .custody.lock .run-file-custody .executors " +
         ".node_registry.json .active_universe ledger.json scratch activity.log " +
         "activity.log.1 .platform .cutover-shared", drop(RUNTIME)));

// ---------------------------------------------------------------------------------------
// A command center (`command_center_layout.classify`)
// ---------------------------------------------------------------------------------------

/** What the person or their agent wrote in a center: carried as files (Fact 9). */
const USER_NAMES = "AGENTS.md soul.md soul_versions voice.md identity.md founder.md MEMORY.md " +
  "settings.yaml origin.md body.md orgchart.md projects.md goals.md index.md log.md skills " +
  "prompts extensions workflows bin notes notes.json wiki config.yaml workspace workspaces " +
  ".agent-workspace canon output artifacts PROGRAM.md progress.md design-proposals " +
  "feature-requests patch-requests timeline.json promises.json facts.json characters.json";

/** What the old daemon trusted in a center: its own records, credentials and runtime. */
const PLATFORM_NAMES = "soul.edit.md dispatcher_config.yaml activity.log status.json " +
  "universe.json work_targets.json ledger.json provider_definitions.json " +
  "hard_priorities.json requests.json branch_tasks.json branch_tasks_archive.json " +
  "auto_ship_attempts.jsonl bid_execution_log.json enrichment_signals.json " +
  "worldbuild_signals.json platform-expected-instance.json executions runs reviews " +
  "discarded_targets archived app_refresh_sessions import import-verify verify lancedb " +
  ".provider-authority .credentials .credentials.json .credential-vault.json .oauth-refresh " +
  ".runtime .runtime_status.json .engine_mcp_config.json .engine_mcp_http_routes.json .pause " +
  ".agent-sessions .consumer_liveness .quarantine .workspace-staging .authoring_blobs " +
  ".worker_supervisor.json subscriptions.json .idle_cycle_stamp.json bid_ledger.json " +
  "settlements uptime-probe .provider-assignment-admission.lock .soul.lock " +
  ".auto_ship_attempts.jsonl.lock .provider_definitions.json.lock .subscriptions.json.lock " +
  ".branch_tasks.json.lock .bid_execution_log.json.lock";

export const CENTER: Level = level(
  named(USER_NAMES, user("what the person or their agent wrote (classify: user)")),
  named(PLATFORM_NAMES, drop("what the old daemon trusted (classify: platform)")),
  [
    [".conversation_memory.db", store(uses({
      archive: "conversation_turns conversation_learned owner_sends",
      drop: "conversation_backfill conversation_run_admissions " +
            "conversation_terminal_projections conversation_projection_cutover " +
            "owner_view_receipts reads",
    }), "the conversations themselves are archived (an engine session cannot be faked, " +
        "Fact 8); their run bookkeeping drops")],
    [".conversation_attention.db", store("drop", "read marks")],
    [".subscription_state.db", store("drop", CREDENTIALS)],
    [".pending_requests.db", store("archive", "the questions the agent asked the owner")],
    [".usage_ledger.db", store("drop", "the old usage ledger")],
    [".authoring.db", store("archive", "drafts and their versions: " + MEMORY)],
    [".effector_consents.db", store("drop", "old consents: the new policy decides")],
    ["rules.db", store("archive", "the owner's legacy rules, kept to read")],
    ["steering.db", store("drop", "mid-turn messages, emptied at every turn's end")],
    // A center's own main and runs stores (`conversation_custody.py`, research §1: a
    // center's stores sit at its top), and their checkpoints.
    ...MAIN_STORE_NAMES.map((name) => [name, MAIN] as const),
    [".runs.db", RUNS],
    [".langgraph_runs.db", store("drop", "run checkpoints (LangGraph's own tables)")],
    [".remote-ta-receipts.sqlite3", store("drop", "a remote box's call receipts for retries")],
    [".lock", drop("the center's own lock (`UNIVERSE_ENTRIES`)")],
    ...named("Activities.error.txt Activities.md Activities.tools.json",
             user("what the activities' conversion wrote the owner " +
                  "(`activity_shape_migration.py` `_write_owner_file`)")),
  ],
  named("apps", user("the owner's apps' documents (`app_room.py`)")),
  named("agents", user("the center's agents as files (`agent_definition.py`: " +
                       "`agents/<slug>/AGENTS.md`, `settings.yaml`)")),
  named("previews", user("the custom UI's rendered previews (`ui_preview.py`)")),
  named("starter", user("the starter's owner files (`starter/muse/checkins.py`)")),
  named(".wiki_write_back_destination_markers.db .external_write_receipts.db .idempotency.db",
        store("drop", RUNTIME)),
);

/** A center's name, as the old stack minted it (`owner_centers.py`). */
export const CENTER_NAME = /^[A-Za-z0-9_-]{1,128}$/;
/** A broker account's folder: the owner's principal's SHA-256. */
export const ACCOUNT_NAME = /^[0-9a-f]{64}$/;
/** SQLite's sidecars beside a store: claimed by the store, never entries of their own. */
export const SIDECAR = /^(.+)-(wal|shm|journal)$/;
/** A store's old backup copy beside it (`classify`'s `.bak-*` family). */
const BACKUP_COPY = /^(.+\.db)\.bak-[A-Za-z0-9][A-Za-z0-9._-]*$/;
/** The minted heartbeat family (`classify`'s one minted pattern). */
const MINTED = /^\.worker_supervisor\.[^.][^/]*\.json$/;
const HEARTBEAT = drop(RUNTIME);

/**
 * The rule of `name` at `at`, by exact name, then a store's backup copy (the store's own
 * rule) or a minted heartbeat (drop); `null` when no rule names it.
 */
export function ruleOf(at: Level, name: string): EntryRule | null {
  if (Object.hasOwn(at, name)) {
    return at[name]!;
  }
  const copy = BACKUP_COPY.exec(name);
  const base = copy === null ? undefined : at[copy[1]!];
  if (base !== undefined && base.kind === "store") {
    return base;
  }
  if (MINTED.test(name)) {
    return HEARTBEAT;
  }
  return (MINTED_IN.get(at) ?? []).find(([pattern]) => pattern.test(name))?.[1] ?? null;
}

/** A table's use under `rule`, or `null` when the rule names no such table. */
export function tableUse(rule: StoreRule, table: string): Use | null {
  if (typeof rule.tables === "string") {
    return rule.tables;
  }
  return Object.hasOwn(rule.tables, table) ? rule.tables[table]! : null;
}
