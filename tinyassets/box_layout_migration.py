"""One-shot owner-split -> owner-box move, run only by deploy_fail_safe.

The old trees stay in place. The new image serves only after the marker is
published. Any failure after ``migrating`` requires the paired snapshot restore;
there is deliberately no resume, startup hook, or reverse move.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import stat
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from deploy.box_project_readback import verify_owner, verify_tree
from tinyassets import storage_layout
from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST
from tinyassets.sqlite_readonly import readonly_db, readonly_snapshot
from tinyassets.universe_owner import account_type_of
from tinyassets.usage_policy import limits_for

PENDING = 10
ABSENT = 4
UNREADABLE = 3
MAX_BACKUP_AGE_S = 3600
_CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CREDENTIAL_ENTRIES = (".credential-vault.json", ".credentials", ".credentials.json",
                       ".oauth-refresh")
_EPHEMERAL_ENTRIES = (".runtime",)


@dataclass(frozen=True)
class CensusRule:
    """A reviewed table placement, never inferred from a column's spelling.

    References name the database, table and unique parent column. All populated
    ownership fields must agree; an unknown explicit owner is not repaired by
    an adjacent row. Empty tables still need a rule.
    """

    placement: str
    principals: tuple[str, ...] = ()
    centers: tuple[str, ...] = ()
    references: tuple[tuple[str, str, str, str], ...] = ()
    drop_unowned: bool = False


def _census_rules() -> dict[str, dict[str, CensusRule]]:
    rules: dict[str, dict[str, CensusRule]] = {}

    def add(database, tables, placement, *, principals=(), centers=(), references=(),
            drop_unowned=False):
        target = rules.setdefault(database, {})
        for table in tables.split():
            if table in target:
                raise AssertionError(f"duplicate census rule: {database}/{table}")
            target[table] = CensusRule(placement, principals, centers, references, drop_unowned)

    main = ".tinyassets.db"
    add(main, "user_accounts user_sessions", "router")
    add(main, "deleted_principals llm_credential_deposit_owners llm_credential_custody "
        "llm_credential_deposit_commits llm_credential_refresh_state", "broker")
    add(main, "author_definitions author_forks agent_definitions agent_component_lineage "
        "commons_bundles commons_bundle_sources commons_bundle_versions "
        "command_center_release_series command_center_releases command_center_release_evidence",
        "published")
    # branch_definitions and goals contain both published and private rows; the
    # census resolves their visibility per row before applying the owner rule.
    add(main, "branch_definitions goals", "owner", principals=("author",))
    add(main, "universes universe_rules author_runtime_instances branches universe_snapshots "
        "user_requests vote_windows action_records universe_notes universe_work_targets "
        "universe_hard_priorities universe_acl founder_home request_admissions branch_tasks_v2 "
        "branch_tasks_v2_quarantine request_admission_rollouts automation_activations "
        "cloud_automation_controls cloud_automation_slice_triggers "
        "cloud_automation_terminal_receipts "
        "background_branch_bindings cloud_automation_continuations cloud_execution_continuations "
        "app_principal_mappings app_channel_bindings scheduled_work operation_scopes "
        "action_approvals "
        "agent_bindings action_result_outbox webhook_admissions webhook_inflight "
        "assigned_queue_refusals provider_work_receipts provider_assignment_candidates "
        "universe_owner request_storage_migration", "owner", centers=("universe_id",))
    rules[main]["universes"] = CensusRule("owner", centers=("id", "universe_id"))
    add(main, "provider_work_bindings provider_assignments universe_model_preferences "
        "agent_turns agent_turn_rounds agent_turn_tools connection_disconnections "
        "universe_app_ui universe_app_ui_asset native_shortlist_cache owner_view_receipts",
        "owner", principals=("owner_user_id",), centers=("universe_id",))
    add(main, "learned_model_evidence learned_model_source_evidence refused_model_marks "
        "account_timezone owner_ui_prefs", "owner", principals=("owner_user_id",))
    add(main, "command_center_adoptions command_center_update_requests "
        "command_center_update_policies "
        "command_center_policy_requests command_center_auto_receipts command_center_auto_status",
        "owner", principals=("owner_id",), centers=("universe_id",))
    add(main, "agent_request_usage agent_request_attempts agent_request_usage_links "
        "agent_request_dispatches", "owner", principals=("owner",), centers=("universe",))
    add(main, "agent_import_stages agent_interchange_idempotency agent_conversion_receipts "
        "agent_conversion_receipt_links agent_conversion_receipt_owners", "owner",
        principals=("actor_id",))
    add(main, "capability_grants", "owner", principals=("user_id",))
    add(main, "webhook_hooks", "owner", principals=("owner_principal_id",),
        centers=("universe_id",))
    for table, column, parent, key in (
        ("branch_heads", "branch_id", "branches", "branch_id"),
        ("vote_ballots", "vote_id", "vote_windows", "vote_id"),
        ("request_admission_events", "admission_id", "request_admissions", "admission_id"),
        ("provider_work_execution_claims", "receipt_id", "provider_work_receipts", "receipt_id"),
        ("provider_invocation_reservations", "receipt_id", "provider_work_receipts", "receipt_id"),
        ("served_provider_budget_reservations", "binding_id",
         "provider_work_bindings", "binding_id"),
        ("background_branch_attempts", "binding_id", "background_branch_bindings", "binding_id"),
    ):
        add(main, table, "owner", references=((column, main, parent, key),))
    # These historical tables need an attribution implementation before any
    # nonempty instance can move. Classifying them does not authorize dropping
    # or replicating their rows. The census therefore refuses populated rows.
    add(main, "gate_claims canonical_bindings goal_canonicals unreconciled_writes "
        "branch_tasks_v2_maintenance_state scoped_reset_leases scoped_reset_operations "
        "background_branch_authority_owners action_result_receipts", "owner")
    add(main, "app_event_admissions webhook_deliveries", "owner",
        centers=("universe_id", "center_id"), drop_unowned=True)
    add(main, "owner_fence", "regenerated")
    add(main, "external_agent_catalogue_receipts external_agent_cli_invocations "
        "external_agent_job_receipts", "dropped", principals=("owner_user_id", "owner"),
        centers=("universe_id",))
    add(".auth.db", "oauth_clients authorization_codes access_tokens refresh_tokens", "router")
    add(".hosted-model-auth.db", "hosted_model_flows connection_oauth_flows", "router")
    add(".api-keys.sqlite3", "api_keys api_key_request_counts", "router")
    add(".hook-owners.sqlite3", "hook_owners", "router")
    add(".router-deleted-owners.sqlite3", "deleted_owners", "router")
    add(".runtime/owner-sessions.db", "owner_sessions owner_login_flows owner_login_completions "
        "owner_refresh_sessions owner_approval_handoffs canary_sign_in_attempts "
        "approval_handoffs inline_model_flows", "router")
    add(".outside-client-authority.sqlite3", "authenticated_connections outside_audit "
        "outside_effects outside_families outside_grants outside_revocations outside_switch",
        "owner", principals=("owner",))
    add(".owner_devices.db", "owner_devices owner_notify_settings request_notifications", "owner",
        principals=("owner_user_id",))
    add(".source_channel_policy.db", "source_channel_policy", "owner", centers=("universe_id",))
    add(".broker/state/owner-identities.db", "owner_identities center_admissions box_records "
        "owner_box_secrets owner_box_revocations", "broker")
    add(".broker/plans.db", "plans", "broker")
    add(".broker/state/next-wake.db", "next_wakes", "broker")
    add(".broker/credential-custody.db", "llm_credential_deposit_owners llm_credential_custody "
        "llm_credential_deposit_commits llm_credential_refresh_state deleted_principals", "broker")
    add("outbound.db", "outbound_connections outbound_connection_grants "
        "outbound_connector_artifacts outbound_connector_artifact_edges connection_capabilities "
        "broker_schema_migrations box_model_sources", "broker")
    rules[".broker/outbound.db"] = dict(rules["outbound.db"])
    add(".broker/outbound.db", "agent_model_launches agent_request_attempts "
        "agent_request_dispatches agent_request_usage agent_request_usage_links", "broker")
    add(".broker/accounts/*/policy.db", "once_approvals policy receipts spend_reservations",
        "broker")
    add(".broker/state/ops.db", "ops meta", "broker")
    add(".control_plane.db", "triggers", "owner", principals=("owner_principal_id",),
        centers=("command_center_id",))
    add(".control_plane.db", "trigger_fires", "owner", principals=("owner_principal_id",),
        references=(("trigger_key", ".control_plane.db", "triggers", "trigger_key"),))
    add(".automations.db", "automations", "owner", principals=("owner_principal_id",),
        centers=("universe_id",))
    add(".automations.db", "automation_attempts", "owner",
        references=(("automation_id", ".automations.db", "automations", "automation_id"),))
    add(".automations.db", "universe_leases", "owner", centers=("universe_id",),
        drop_unowned=True)
    add(".account_seats.db", "account_seats seat_waiters chat_turn_failures", "owner",
        principals=("account_id",), centers=("universe_id",))
    add(".effector_consents.db", "effector_consents", "owner", principals=("granted_by",))
    add(".engine_run_admissions.db", "admissions dispatch_budget", "owner",
        centers=("universe_id",))
    add(".engine_run_admissions.db", "settlements", "owner",
        references=(("run_id", ".runs.db", "runs", "run_id"),))
    packages = ".command-center-packages/packages.db"
    add(packages, "blobs package_versions", "published")
    add(packages, "pins extension_activations extension_bindings extension_revisions", "owner",
        principals=("owner_id",), centers=("universe_id",))
    add(".runs.db", "runs", "owner", principals=("owner_user_id",),
        centers=("queue_universe_id",))
    add(".runs.db", "run_events run_cancels run_terminal_outbox run_judgments run_receipts "
        "run_file_operations run_file_allocations run_file_objects run_file_bindings "
        "run_file_cleanup "
        "run_input_admissions", "owner", references=(("run_id", ".runs.db", "runs", "run_id"),))
    add(".runs.db", "run_lineage", "owner",
        references=(("run_id", ".runs.db", "runs", "run_id"),))
    add(".runs.db", "teammate_messages", "owner",
        references=(("from_run_id", ".runs.db", "runs", "run_id"),))
    add(".runs.db", "run_child_attachments", "owner",
        references=(("parent_run_id", ".runs.db", "runs", "run_id"),))
    add(".runs.db", "node_edit_audit", "owner")
    add(".runs.db", "branch_versions_migrations", "regenerated")
    add(".runs.db", "workspace_push_intents", "owner", centers=("universe_id",),
        references=(("run_id", ".runs.db", "runs", "run_id"),))
    add(".runs.db", "branch_versions", "owner", principals=("publisher",))
    add(".runs.db", "branch_schedules branch_subscriptions", "owner",
        principals=("owner_actor", "owner_principal_id"), centers=("universe_id",))
    add(".runs.db", "scheduler_delivered_events", "owner", references=(
        ("subscription_id", ".runs.db", "branch_subscriptions", "subscription_id"),))
    add(".runs.db", "contribution_events", "owner", principals=("owner_user_id",),
        references=(("source_run_id", ".runs.db", "runs", "run_id"),))
    add(".runs.db", "conversation_run_admissions", "owner", principals=("owner_user_id",),
        centers=("universe_id",))
    add(".runs.db", "graph_receivers graph_output_links", "owner", principals=("owner_id",),
        centers=("universe_id",))
    add(".runs.db", "graph_deliveries", "owner", principals=("sender_id", "receiver_owner_id"),
        centers=("sender_universe_id", "receiver_universe_id"))
    add(".runs.db", "graph_delivery_attempts graph_delivery_files graph_delivery_answers", "owner",
        references=(("delivery_id", ".runs.db", "graph_deliveries", "delivery_id"),))
    add(".runs.db", "gate_event", "owner", principals=("attested_by",))
    add(".runs.db", "gate_event_cite", "owner",
        references=(("event_id", ".runs.db", "gate_event", "event_id"),))
    add(".runs.db", "workspace_generations workspace_leases workspace_ledger "
        "workspace_outbox workspace_waiters", "owner", centers=("universe_id",))
    add(".runs.db", "workspace_locks", "owner",
        references=(("run_id", ".runs.db", "runs", "run_id"),))
    add(".langgraph_runs.db", "checkpoints writes", "owner",
        references=(("thread_id", ".runs.db", "runs", "thread_id"),))
    add(".owner_leases.db", "owner_lease owner_store_catalog fence_high_water", "regenerated")
    add(".owner_leases.db", "restore_state", "owner")
    add(".storage_accounting.db", "measurements counter commons_log_bytes", "regenerated")
    add(".storage_accounting.db", "pending commons_writers", "owner")
    add("wiki_trigger_attempts.db", "wiki_trigger_attempts", "owner",
        centers=("universe_id", "center_id"), drop_unowned=True)
    # Only these reviewed legacy sets can lack every attribution key. Unknown
    # explicit principals and conflicting keys still refuse.
    from dataclasses import replace

    for database, names in {
        main: "branches branch_heads universe_rules served_provider_budget_reservations "
              "owner_ui_prefs provider_work_bindings goals agent_conversion_receipts "
              "agent_conversion_receipt_links agent_conversion_receipt_owners "
              "agent_import_stages agent_interchange_idempotency assigned_queue_refusals "
              "branch_definitions",
        ".owner_devices.db": "owner_devices",
        ".runs.db": "runs run_events run_lineage contribution_events run_file_operations "
                    "run_file_allocations run_file_objects run_file_bindings run_file_cleanup "
                    "branch_versions branch_schedules",
        ".langgraph_runs.db": "checkpoints writes",
    }.items():
        for name in names.split():
            rules[database][name] = replace(rules[database][name], drop_unowned=True)
    return rules


CENSUS_RULES = _census_rules()


def _census_rule(database: str, table: str) -> CensusRule | None:
    parts = PurePosixPath(database).parts
    if len(parts) == 4 and parts[:2] == (".broker", "accounts") and parts[3] == "policy.db":
        database = ".broker/accounts/*/policy.db"
    return CENSUS_RULES.get(database, {}).get(table)


def _census_columns(database: str, table: str) -> set[str]:
    """Read attribution metadata only; never materialize history or token blobs."""
    rule = _census_rule(database, table)
    columns = set()
    if rule:
        columns.update(rule.principals + rule.centers)
        columns.update(reference[0] for reference in rule.references)
    for rules in CENSUS_RULES.values():
        for candidate in rules.values():
            for _, parent_db, parent_table, parent_column in candidate.references:
                if (database, table) == (parent_db, parent_table):
                    columns.add(parent_column)
    if database == ".tinyassets.db" and table in {"branch_definitions", "goals"}:
        columns.update(("published", "visibility"))
    if (database, table) == (".tinyassets.db", "universes"):
        columns.add("id")
    if (database, table) == (".runs.db", "branch_versions"):
        columns.add("public")
    if database in {"outbound.db", ".broker/outbound.db"} and table == "outbound_connections":
        columns.update(("destination", "owner_user_id"))
    if (database, table) == (".api-keys.sqlite3", "api_keys"):
        columns.update(("owner", "revoked_at"))
    if (database, table) == (".hook-owners.sqlite3", "hook_owners"):
        columns.add("owner")
    return columns


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def census(data: Path, *, _inventory: dict | None = None) -> dict:
    """Read-only, count-only cutover audit, including all refusal evidence.

    No store initializers, live runtime readers, or default-plan readers run
    here. Even WAL recovery happens only in readonly_db's external scratch.
    Account labels are report-local ordinals, never identities or credential
    material. A refused audit is useful evidence, not permission to migrate.
    """
    from collections import Counter

    from tinyassets.principals import named_principal

    data = Path(data).resolve()
    refusals: Counter = Counter()
    tables: dict[tuple[str, str], list[dict]] = {}
    database_files: set[Path] = set()
    known_centers: dict[str, set[str]] = {}
    bindings: dict[str, set[str]] = {}
    universe_centers: set[str] = set()
    owners: set[str] = set()
    permanent: dict[str, int] = {}
    identities = data / ".broker/state/owner-identities.db"
    for relative in (".tinyassets.db", ".broker", ".broker/state",
                     ".broker/state/owner-identities.db", ".layout.json"):
        if (data / relative).is_symlink():
            raise RuntimeError("census shared authority path is a symlink")
    with readonly_snapshot():
        marker_path = data / ".layout.json"
        marker = json.loads(marker_path.read_text()) if marker_path.is_file() else {}
        if (not isinstance(marker, dict) or marker.get("layout") != 2
                or marker.get("state") != "stable" or marker.get("split") != "owner-split"
                or marker.get("box_layout") not in (None, "owner-split")):
            refusals["not_production_owner_split_layout"] += 1
        # Bindings corroborate admissions; they never create an owner or admit
        # a center. The broker is the sole authority for the cutover.
        main = data / ".tinyassets.db"
        if main.is_file():
            with readonly_db(main) as db:
                names = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if "universes" in names:
                    columns = {row[1] for row in db.execute("PRAGMA table_info(universes)")}
                    column = "id" if "id" in columns else "universe_id"
                    universe_centers = {row[0] for row in db.execute(
                        f"SELECT {_quoted(column)} FROM universes")}
                for table, principal in (("universe_owner", "owner_id"),
                                         ("founder_home", "founder_sub")):
                    if table in names:
                        for center, owner in db.execute(
                            f"SELECT universe_id,{principal} FROM {table}"
                        ):
                            if named_principal(owner) and _CENTER.fullmatch(center or ""):
                                bindings.setdefault(center, set()).add(owner)
                            else:
                                refusals["invalid_explicit_ownership"] += 1
        admitted: dict[str, str] = {}
        if identities.is_file():
            with readonly_db(identities) as db:
                permanent = dict(db.execute("SELECT principal,machine_id FROM owner_identities"))
                if (len(set(permanent.values())) != len(permanent) or any(
                    not named_principal(owner) or type(uid) is not int
                    or not OWNER_ID_FIRST <= uid <= OWNER_ID_LAST
                    for owner, uid in permanent.items()
                )):
                    refusals["invalid_permanent_identities"] += 1
                owners.update(permanent)
                retired = set()
                for event, owner, center, machine in db.execute(
                    "SELECT event,principal,center,machine FROM center_admissions "
                    "ORDER BY generation"
                ):
                    if (owner not in permanent or permanent[owner] != machine
                            or not _CENTER.fullmatch(center or "")):
                        refusals["invalid_admission"] += 1
                    elif event == "admit" and center not in admitted and center not in retired:
                        admitted[center] = owner
                    elif event == "retire" and admitted.get(center) == owner:
                        del admitted[center]
                        retired.add(center)
                    else:
                        refusals["invalid_admission_history"] += 1
            known_centers = {center: {owner} for center, owner in admitted.items()}
            for center, candidates in bindings.items():
                if admitted.get(center) not in candidates or len(candidates) != 1:
                    refusals["ownership_not_live_admitted"] += 1
            for center in admitted:
                if (data / center).is_symlink() or not (data / center).is_dir():
                    refusals["admitted_center_tree_absent"] += 1
        else:
            refusals["broker_admission_database_absent"] += 1
        dropped_centers = universe_centers - set(admitted) - set(bindings)
        if any(not _CENTER.fullmatch(center or "") for center in dropped_centers):
            refusals["invalid_unadmitted_center"] += 1

        # Enumerate actual shared files, including SQLite with an unfamiliar
        # filename. A suffix-only scan would silently miss a renamed store.
        shared_roots = [data]
        for directory in (".broker", ".runtime", ".published", ".command-center-packages"):
            root = data / directory
            if root.is_dir() and not root.is_symlink():
                shared_roots.extend(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink())
                shared_roots.append(root)
        for root in shared_roots:
            for path in sorted(root.iterdir()):
                if path.is_symlink() or not path.is_file():
                    continue
                if path == data / ".tinyassets.db.prebudgetrepair.bak":
                    continue  # Explicitly out of scope; do not open the stale copy.
                with path.open("rb") as stream:
                    is_database = stream.read(16) == b"SQLite format 3\x00"
                if not is_database:
                    continue
                database_files.add(path)
                relative = path.relative_to(data).as_posix()
                with readonly_db(path) as db:
                    if db.execute("PRAGMA integrity_check").fetchall() != [("ok",)]:
                        refusals["database_integrity"] += 1
                    if db.execute("PRAGMA foreign_key_check").fetchone() is not None:
                        refusals["database_foreign_keys"] += 1
                    db.row_factory = sqlite3.Row
                    for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                        table = row[0]
                        # SQLite's autoincrement cursor is schema machinery,
                        # retained/rebuilt with the schema, not a tenant row.
                        if table == "sqlite_sequence":
                            continue
                        available = {info[1] for info in db.execute(
                            f"PRAGMA table_info({_quoted(table)})")}
                        selected = sorted(_census_columns(relative, table) & available)
                        projection = ",".join(map(_quoted, selected)) if selected else "1"
                        if _inventory is not None:
                            projection = "*"
                        tables[relative, table] = [dict(item) for item in db.execute(
                            f"SELECT {projection} FROM {_quoted(table)}")]

        labels = {owner: f"account-{index:04d}"
                  for index, owner in enumerate(sorted(owners), start=1)}
        memo: dict[tuple[str, str, int], frozenset[str]] = {}
        indexes: dict[tuple[str, str, str], dict[object, list[int]]] = {}

        def row_owners(database, table, index, active=frozenset()):
            key = (database, table, index)
            if key in memo:
                return memo[key]
            if key in active:
                return frozenset()
            rule = _census_rule(database, table)
            if rule is None:
                return frozenset()
            row = tables[database, table][index]
            claims: list[set[str]] = []
            for column in rule.principals:
                if row.get(column):
                    value = row[column]
                    claims.append({value} if value in owners else set())
            for column in rule.centers:
                if row.get(column):
                    value = row[column]
                    claims.append({"dropped"} if value in dropped_centers or (
                        rule.drop_unowned and value not in known_centers)
                                  else known_centers.get(value, set()))
            for column, parent_db, parent, parent_column in rule.references:
                if row.get(column):
                    index_key = parent_db, parent, parent_column
                    if index_key not in indexes:
                        lookup: dict[object, list[int]] = {}
                        for i, candidate in enumerate(tables.get((parent_db, parent), [])):
                            lookup.setdefault(candidate.get(parent_column), []).append(i)
                        indexes[index_key] = lookup
                    matches = indexes[index_key].get(row[column], [])
                    if len(matches) != 1:
                        claims.append({"dropped"} if not matches and rule.drop_unowned else set())
                    else:
                        claims.append(set(row_owners(
                            parent_db, parent, matches[0], active | {key})))
            if not claims and rule.drop_unowned:
                claims.append({"dropped"})
            if any(c and c != {"dropped"} for c in claims):
                claims = [c for c in claims if c != {"dropped"}]
            resolved = (frozenset.union(*(frozenset(c) for c in claims))
                        if claims and all(len(c) == 1 for c in claims) else frozenset())
            memo[key] = resolved if len(resolved) == 1 else frozenset()
            return memo[key]

        table_report = {}
        totals: Counter = Counter(dict.fromkeys(
            ("owner", "router", "broker", "published", "dropped", "regenerated"), 0))
        assignments = {}
        for (database, table), rows in sorted(tables.items()):
            rule = _census_rule(database, table)
            placement = rule.placement if rule else "unknown"
            counts: Counter = Counter()
            public = 0
            dropped = len(rows) if placement == "dropped" else 0
            targets = [placement] * len(rows)
            if rule is None:
                refusals["unknown_table"] += 1
            elif placement == "owner":
                if rows and not (rule.principals or rule.centers or rule.references
                                 or rule.drop_unowned):
                    refusals["attribution_rule_pending"] += 1
                for index, row in enumerate(rows):
                    if ((database == ".tinyassets.db" and table in {"branch_definitions", "goals"}
                            and row.get("visibility") == "public"
                            and (table == "goals" or row.get("published") == 1))
                            or ((database, table) == (".runs.db", "branch_versions")
                                and row.get("public") == 1)):
                        public += 1
                        targets[index] = "published"
                        continue
                    candidates = row_owners(database, table, index)
                    if len(candidates) != 1:
                        counts["unattributed"] += 1
                        targets[index] = "unattributed"
                    else:
                        owner = next(iter(candidates))
                        targets[index] = owner
                        if owner == "dropped":
                            dropped += 1
                        else:
                            counts[labels[owner]] += 1
                refusals["unattributed_owner_rows"] += counts.get("unattributed", 0)
            elif (database, table) in {
                (".api-keys.sqlite3", "api_keys"), (".hook-owners.sqlite3", "hook_owners"),
            }:
                for row in rows:
                    if row.get("revoked_at") is None and row.get("owner") not in owners:
                        refusals["router_credential_without_owner"] += 1
            totals[placement] += len(rows) - public - (dropped if placement != "dropped" else 0)
            if placement != "dropped":
                totals["dropped"] += dropped
            totals["published"] += public
            assignments[database, table] = targets
            table_report[f"{database}/{table}"] = {
                "class": placement, "total": len(rows), "per_owner": dict(counts),
                "published": public, "dropped": dropped,
                "regenerated": len(rows) if placement == "regenerated" else 0,
                "attribution": ("pending" if rule and placement == "owner"
                                and not (rule.principals or rule.centers or rule.references)
                                else "specified"),
            }

        plan_report, credential_report = _census_accounts(
            data, known_centers, labels, tables, refusals)
        for (database, table), rows in tables.items():
            rule = _census_rule(database, table)
            if rule and rule.placement == "dropped":
                for index in range(len(rows)):
                    candidates = row_owners(database, table, index)
                    if len(candidates) == 1 and next(iter(candidates)) in labels:
                        credential_report[labels[next(iter(candidates))]]["dropped_cli"] += 1
        file_report = _census_files(data, known_centers, labels, database_files, refusals,
                                    dropped_centers)
        if _inventory is not None:
            _inventory.update(tables=tables, assignments=assignments, owners=permanent,
                              centers=admitted, dropped_centers=dropped_centers, labels=labels)
        return {
            "version": 1, "ready": not any(refusals.values()), "owners": len(owners),
            "admitted_centers": len(admitted), "explicit_centers": len(known_centers),
            "databases": len(database_files), "tables": table_report, "row_totals": dict(totals),
            "plans": plan_report, "credentials": credential_report, "files": file_report,
            "refusals": {key: value for key, value in sorted(refusals.items()) if value},
        }


def _census_accounts(data, centers, labels, tables, refusals):
    """Read Stripe's actual tier rows and raw vault shapes without loading secrets into reports."""
    from collections import Counter

    plans = {label: {"tier": "free", "paid_sources": 0} for label in labels.values()}
    credentials = {label: {"dropped_cli": 0, "carried_http": 0} for label in labels.values()}
    # Scan every center, including unowned centers: a paid orphan must refuse.
    directory_names = {path.name for path in data.iterdir()
                       if path.is_dir() and not path.is_symlink()
                       and not path.name.startswith(".")}
    custody = data / ".platform/credential-vaults"
    if custody.is_symlink() or (data / ".platform").is_symlink():
        raise RuntimeError("census credential custody path is a symlink")
    if custody.is_dir():
        directory_names.update(path.name for path in custody.iterdir())
    paid: Counter = Counter()
    for name in sorted(directory_names):
        directory = data / name
        owners = centers.get(directory.name, set())
        label = labels[next(iter(owners))] if len(owners) == 1 else None
        path = directory / ".subscription_state.db"
        if path.is_file():
            with readonly_db(path) as db:
                names = {row[0] for row in db.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'")}
                if names != {"subscription_meta"}:
                    refusals["unknown_subscription_table"] += 1
                    continue
                meta = dict(db.execute("SELECT key,value FROM subscription_meta"))
            tier = meta.get("tier", "free")  # Legacy schema: absence means no subscription.
            if tier not in {"free", "paid"}:
                refusals["invalid_tier"] += 1
            elif tier == "paid":
                if label is None:
                    refusals["unattributed_paid_tier"] += 1
                else:
                    paid[label] += 1
                    plans[label] = {"tier": "paid", "paid_sources": paid[label]}
        vaults = [directory / ".credential-vault.json",
                  data / ".platform/credential-vaults" / directory.name / ".credential-vault.json"]
        if any(path.is_symlink() or path.parent.is_symlink() for path in vaults):
            raise RuntimeError("census vault path is a symlink")
        present = [path for path in vaults if path.is_file()]
        if len(present) > 1:
            refusals["duplicate_vault_locations"] += 1
        for path in present:
            raw = json.loads(path.read_text(encoding="utf-8"))
            records = raw.get("credentials") if isinstance(raw, dict) else None
            if not isinstance(records, list):
                refusals["invalid_vault_document"] += 1
                continue
            for record in records:
                if not isinstance(record, dict):
                    refusals["invalid_vault_record"] += 1
                    continue
                kind = record.get("credential_type")
                if label is None:
                    refusals["unattributed_credential"] += 1
                elif kind == "llm_subscription":
                    credentials[label]["dropped_cli"] += 1
                elif kind == "http":
                    connections = [row for database in ("outbound.db", ".broker/outbound.db")
                                   for row in tables.get((database, "outbound_connections"), [])]
                    matching = [row for row in connections
                                if row.get("destination") == record.get("destination")
                                and row.get("owner_user_id") in owners]
                    if len(matching) != 1:
                        refusals["http_slot_without_unique_owner_connection"] += 1
                    else:
                        credentials[label]["carried_http"] += 1
                else:
                    # VCS/social custody needs an explicit carry/drop contract;
                    # never silently treat those secrets as HTTP slots.
                    refusals["unclassified_credential_type"] += 1
    return plans, credentials


def _census_files(data, centers, labels, databases, refusals, dropped_centers=()):
    from collections import Counter

    counts: Counter = Counter()
    shared = {
        ".layout.json": "broker", ".layout.lock": "broker",
        "platform-expected-instance.json": "router", "release-state.json": "router",
        ".billing_webhook_verified.json": "router",
    }
    published = {"wiki", ".wiki", ".published", ".command-center-packages"}
    for directory, dirs, files in os.walk(data, followlinks=False):
        root = Path(directory)
        # os.walk lists directory symlinks in dirs but never descends them.
        for name in [*files, *(name for name in dirs if (root / name).is_symlink())]:
            path = root / name
            relative = path.relative_to(data)
            parts = relative.parts
            if (relative.as_posix() == ".tinyassets.db.prebudgetrepair.bak"
                    or parts[0] in dropped_centers):
                counts["dropped"] += 1
            elif parts[0] in centers and len(centers[parts[0]]) == 1:
                label = labels[next(iter(centers[parts[0]]))]
                counts[label] += 1
            elif path in databases or any(
                path == database.with_name(database.name + suffix)
                for database in databases for suffix in ("-wal", "-shm")
            ):
                counts["database_files"] += 1
            elif len(parts) == 1 and name in shared:
                counts[shared[name]] += 1
            elif parts[0] in published:
                counts["published"] += 1
            elif (len(parts) >= 4 and parts[:2] == (".platform", "credential-vaults")
                  and len(centers.get(parts[2], set())) == 1):
                counts["broker"] += 1
            elif (len(parts) >= 3 and parts[0] in {".agent-sessions", ".universe-sidecars"}
                  and len(centers.get(parts[1], set())) == 1):
                counts[labels[next(iter(centers[parts[1]]))]] += 1
            elif (len(parts) >= 3 and parts[0] in {".agent-sessions", ".universe-sidecars"}
                  and parts[1] in dropped_centers):
                counts["dropped"] += 1
            else:
                counts["unattributed"] += 1
    refusals["unattributed_files"] += counts["unattributed"]
    return dict(counts)


@dataclass(frozen=True)
class OwnerMove:
    principal: str
    uid: int
    centers: tuple[str, ...]
    quota: int


def _marker(data: Path) -> dict:
    marker = storage_layout.read_marker(data)
    if (not isinstance(marker, dict) or marker.get("layout") not in
            storage_layout.KNOWN_LAYOUTS or marker.get("state") != "stable"):
        raise RuntimeError("stable data layout marker required")
    return marker


def _count_or_none(path: Path) -> bool | None:
    try:
        return path.is_file()
    except OSError:
        return None


def layout_census(data: Path) -> dict:
    """Counts on disk for each layout. ``None`` means that count was not readable.

    A missing marker is ``box_layout=absent`` and ``pending=None``. That is not
    evidence the volume is already one box per owner, and it is not evidence
    there is nothing to move.
    """
    data = Path(data)
    counts: dict[str, dict[str, object]] = {
        "owner-split": {"centers": None, "admitted_owners": None},
        "owner-box": {"registered_boxes": None, "registry_present": False},
    }
    unreadable: list[str] = []
    identity_error = None
    try:
        centers = 0
        for entry in data.iterdir():
            try:
                is_center = entry.is_dir() and not entry.is_symlink()
            except OSError:
                unreadable.append(entry.name)
                continue
            if entry.name.startswith(".") or not is_center:
                continue
            centers += 1
        counts["owner-split"]["centers"] = centers
    except OSError as exc:
        unreadable.append(type(exc).__name__)

    identity_db = data / ".broker" / "state" / "owner-identities.db"
    present = _count_or_none(identity_db)
    if present is None:
        identity_error = "identity database unreadable"
    elif present:
        try:
            with readonly_db(identity_db) as db:
                row = db.execute("SELECT COUNT(*) FROM owner_identities").fetchone()
            counts["owner-split"]["admitted_owners"] = int(row[0]) if row else 0
        except sqlite3.Error as exc:
            identity_error = type(exc).__name__
    else:
        identity_error = "identity database absent"

    registry = data / ".broker" / "box-registry.json"
    registry_present = _count_or_none(registry)
    if registry_present is None:
        unreadable.append("box-registry")
    else:
        counts["owner-box"]["registry_present"] = registry_present
        if registry_present:
            try:
                document = json.loads(registry.read_text(encoding="utf-8"))
                boxes = document.get("boxes") if isinstance(document, dict) else None
                if isinstance(boxes, list):
                    counts["owner-box"]["registered_boxes"] = len(boxes)
                else:
                    unreadable.append("box-registry")
            except (OSError, ValueError):
                unreadable.append("box-registry")

    base = {
        "counts": counts,
        "unreadable_entries": unreadable,
        "identity_error": identity_error,
    }
    marker_path = storage_layout.marker_path(data)
    try:
        raw = marker_path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return {
            **base, "marker_layout": None, "marker_state": None,
            "box_layout": "absent", "pending": None,
        }
    except OSError as exc:
        return {
            **base, "marker_layout": None, "marker_state": None,
            "box_layout": "unreadable", "pending": None,
            "identity_error": identity_error or type(exc).__name__,
        }
    try:
        marker = json.loads(raw)
    except ValueError:
        return {
            **base, "marker_layout": None, "marker_state": None,
            "box_layout": "unreadable", "pending": None,
        }
    if not isinstance(marker, dict):
        return {
            **base, "marker_layout": None, "marker_state": None,
            "box_layout": "unreadable", "pending": None,
        }
    layout = marker.get("box_layout", "owner-split")
    state = marker.get("state")
    pending = True if layout == "owner-split" and state == "stable" else (
        False if layout == "owner-box" and state == "stable" else None
    )
    return {
        **base,
        "marker_layout": marker.get("layout"),
        "marker_state": state,
        "box_layout": layout,
        "pending": pending,
    }


def check(data: Path) -> int:
    """Print a census; 10 also covers the one-time Activities cutover."""
    census = layout_census(data)
    layout = census["box_layout"]
    state = census["marker_state"]
    activity_pending = []
    if layout == "owner-box" and state == "stable":
        from tinyassets.activity_shape_migration import _pending

        if _marker(data).get("activity_shape") != "v1":
            activity_pending = _pending(data)
            census["pending"] = bool(activity_pending)
    census["activity_shape_pending"] = activity_pending
    print(json.dumps(census, sort_keys=True))
    if layout == "absent":
        return ABSENT
    if state != "stable" or layout not in ("owner-split", "owner-box"):
        return UNREADABLE
    if layout == "owner-box":
        return PENDING if activity_pending else 0
    return PENDING


def _verified_archive(path: Path) -> None:
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise RuntimeError(f"missing complete snapshot {path}")
    if not 0 <= time.time() - path.stat().st_mtime <= MAX_BACKUP_AGE_S:
        raise RuntimeError(f"snapshot {path} is not fresh")
    digest = path.with_name(path.name + ".sha256")
    expected = digest.read_text(encoding="ascii").split()
    if len(expected) != 2 or expected[1] != path.name or not re.fullmatch(
        r"[a-f0-9]{64}", expected[0]
    ):
        raise RuntimeError(f"invalid snapshot digest {digest}")
    sha = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            sha.update(chunk)
    if sha.hexdigest() != expected[0]:
        raise RuntimeError(f"snapshot digest mismatch {path}")


def _verified_pair(data_backup: Path, box_backup: Path) -> None:
    if not data_backup.name.startswith("tinyassets-snapshot-"):
        raise RuntimeError("data backup is not a full cutover snapshot")
    suffix = data_backup.name.removeprefix("tinyassets-snapshot-")
    if box_backup.name != "tinyassets-box-store-" + suffix:
        raise RuntimeError("box backup does not pair with data backup")
    _verified_archive(data_backup)
    _verified_archive(box_backup)


def _owner_plan(data: Path) -> list[OwnerMove]:
    identity_db = data / ".broker" / "state" / "owner-identities.db"
    with readonly_db(identity_db) as db:
        identities = dict(db.execute(
            "SELECT principal, machine_id FROM owner_identities"
        ))
        admissions = db.execute(
            "SELECT event, principal, center, machine FROM center_admissions "
            "ORDER BY generation"
        ).fetchall()
    owners: dict[str, set[str]] = {principal: set() for principal in identities}
    retired: set[str] = set()
    for event, principal, center, machine in admissions:
        if (principal not in identities or identities[principal] != machine
                or not _CENTER.fullmatch(center)
                or type(machine) is not int
                or not OWNER_ID_FIRST <= machine <= OWNER_ID_LAST):
            raise RuntimeError("invalid broker owner admission")
        if event == "admit":
            if center in retired or any(center in names for names in owners.values()):
                raise RuntimeError("duplicate or recycled center admission")
            owners[principal].add(center)
        elif event == "retire":
            if center not in owners[principal]:
                raise RuntimeError("retire without matching owner admission")
            owners[principal].remove(center)
            retired.add(center)
        else:
            raise RuntimeError("invalid owner admission event")
    plan = []
    used = set()
    for principal, uid in sorted(identities.items(), key=lambda pair: pair[1]):
        if type(uid) is not int or not OWNER_ID_FIRST <= uid <= OWNER_ID_LAST or uid in used:
            raise RuntimeError("invalid permanent owner identity")
        used.add(uid)
        centers = tuple(sorted(owners[principal]))
        for center in centers:
            source = data / center
            info = source.lstat()
            if (not stat.S_ISDIR(info.st_mode) or info.st_uid != 1001
                    or info.st_gid != uid):
                raise RuntimeError(f"noncanonical owner center {center}")
        account = account_type_of(data, principal, db_reader=readonly_db)
        quota = int(limits_for(account).storage_bytes)
        quota = (quota // 1024) * 1024
        if quota <= 0:
            raise RuntimeError("owner quota is not positive")
        plan.append(OwnerMove(principal, uid, centers, quota))
    # Refuse silently orphaned owner roots; the new image could never reach them.
    for source in data.iterdir():
        if source.name.startswith(".") or not source.is_dir() or source.is_symlink():
            continue
        info = source.lstat()
        if OWNER_ID_FIRST <= info.st_gid <= OWNER_ID_LAST and source.name not in {
            center for owner in plan for center in owner.centers
        }:
            raise RuntimeError(f"unadmitted owner center {source.name}")
    return plan


def _boxed_activity_roots(data: Path, store: Path) -> tuple[dict[str, Path], dict[str, str]]:
    """Resolve each owner's centers independently through committed box records."""
    from tinyassets.activity_shape_migration import _centers, _pending

    identity_db = data / ".broker" / "state" / "owner-identities.db"
    with readonly_db(identity_db) as db:
        records = dict(db.execute("SELECT principal, machine FROM box_records"))
    centers = _centers(data)
    by_owner: dict[str, list[str]] = {}
    for center in _pending(data):
        if not _CENTER.fullmatch(center) or center not in centers:
            raise RuntimeError(f"activity center has no admitted owner: {center}")
        by_owner.setdefault(centers[center], []).append(center)
    roots = {}
    failures = {}
    for owner, owner_centers in by_owner.items():
        uid = records.get(owner)
        if type(uid) is not int or not OWNER_ID_FIRST <= uid <= OWNER_ID_LAST:
            reason = "Activities conversion refused: owner has no box record"
        else:
            owner_roots = {center: store / str(uid) / center for center in owner_centers}
            absent = [center for center, root in owner_roots.items()
                      if root.is_symlink() or not root.is_dir()]
            reason = (f"Activities conversion refused: owner box is absent: {absent[0]}"
                      if absent else "")
        if reason:
            failures.update({center: reason for center in owner_centers})
        else:
            roots.update(owner_roots)
    return roots, failures


def _mounted(store: Path) -> None:
    result = subprocess.run(
        ["findmnt", "--mountpoint", str(store), "-no", "FSTYPE,OPTIONS"],
        check=True, capture_output=True, text=True,
    )
    fields = result.stdout.strip().split(None, 1)
    if (len(fields) != 2 or fields[0] != "xfs"
            or not {"pquota", "prjquota"}.intersection(fields[1].split(","))
            or "discard" in fields[1].split(",")):
        raise RuntimeError("owner store must be mounted XFS with project quota, no discard")


def _refuse_nested_mounts(
    data: Path, mountinfo: Path = Path("/proc/self/mountinfo"),
) -> None:
    """Refuse every mount below the source root, including file bind mounts."""
    root = PurePosixPath(data)
    if not root.is_absolute():
        raise RuntimeError(f"owner data root must be absolute: {data}")
    with mountinfo.open(encoding="utf-8", errors="surrogateescape") as stream:
        for line in stream:
            fields = line.split()
            if len(fields) < 10 or "-" not in fields[6:]:
                raise RuntimeError("invalid migration mountinfo")
            mount_point = PurePosixPath(re.sub(
                r"\\([0-7]{3})", lambda match: chr(int(match[1], 8)), fields[4],
            ))
            if mount_point != root and mount_point.is_relative_to(root):
                raise RuntimeError(f"mount beneath owner data root: {mount_point}")


def _owner_inode_preflight(data: Path, plan: list[OwnerMove]) -> None:
    """Census before the first write, while all source writers are stopped."""
    seen: dict[tuple[int, int], tuple[str, Path, Path, int, int]] = {}
    link_counts: dict[tuple[int, int], int] = {}
    _refuse_nested_mounts(data)
    for owner in plan:
        sources = [data / center for center in owner.centers]
        home = data / ".platform" / "owners" / str(owner.uid) / "agent-home"
        if home.exists() or home.is_symlink():
            sources.append(home)
        for source in sources:
            info = source.lstat()
            if not stat.S_ISDIR(info.st_mode):
                raise RuntimeError(f"owner source is not a real directory: {source}")
            for directory, dirs, files, fd in os.fwalk(source, follow_symlinks=False):
                for name in (".", *dirs, *files):
                    info = os.stat(name, dir_fd=fd, follow_symlinks=False)
                    path = Path(directory) if name == "." else Path(directory) / name
                    if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)
                            or stat.S_ISLNK(info.st_mode)):
                        raise RuntimeError(f"unsupported owner inode (FIFO/socket/device): {path}")
                    key = (info.st_dev, info.st_ino)
                    previous = seen.setdefault(
                        key, (owner.principal, source, path, info.st_mode, info.st_nlink))
                    if previous[0] != owner.principal:
                        raise RuntimeError(f"cross-owner inode: {previous[2]} and {path}")
                    if previous[1] != source and not stat.S_ISDIR(info.st_mode):
                        raise RuntimeError(f"cross-copy hard link: {previous[2]} and {path}")
                    if not stat.S_ISDIR(info.st_mode):
                        link_counts[key] = link_counts.get(key, 0) + 1
    for key, (_, _, path, mode, links) in seen.items():
        if not stat.S_ISDIR(mode) and link_counts.get(key, 0) != links:
            raise RuntimeError(f"owner hard link leaves the move sources: {path}")


def _atomic_json(path: Path, value: dict) -> None:
    temporary = path.with_name(path.name + f".{os.getpid()}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        if path.exists():
            info = path.stat()
            if hasattr(os, "chown"):
                os.chown(temporary, info.st_uid, info.st_gid)
            os.chmod(temporary, stat.S_IMODE(info.st_mode))
        os.replace(temporary, path)
        if hasattr(os, "O_DIRECTORY"):
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        temporary.unlink(missing_ok=True)


def _copy(source: Path, target: Path, uid: int, *, extra_excludes=()) -> None:
    command = [
        "rsync", "-aH", "--xattrs", "--one-file-system", "--numeric-ids",
        "--chown", f"{uid}:{uid}", f"{source}/", f"{target}/",
    ]
    command[-2:-2] = [f"--exclude=/{name}" for name in
                      (*_CREDENTIAL_ENTRIES, *_EPHEMERAL_ENTRIES, *extra_excludes)]
    subprocess.run(command, check=True)
    result = subprocess.run(
        [*command[:-2], "--checksum", "--dry-run", "--itemize-changes", *command[-2:]],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip():
        raise RuntimeError(
            f"rsync verification differs for {source}: {result.stdout.strip()[:2000]}")


def _check_credential_sources(source: Path) -> None:
    for name in _CREDENTIAL_ENTRIES:
        entry = source / name
        if not entry.exists() and not entry.is_symlink():
            continue
        for directory, dirs, files in os.walk(entry, followlinks=False):
            for child in (Path(directory), *(Path(directory) / item for item in dirs + files)):
                info = child.lstat()
                if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
                    raise RuntimeError(f"non-regular credential source: {child}")
        info = entry.lstat()
        if not (stat.S_ISDIR(info.st_mode) or stat.S_ISREG(info.st_mode)):
            raise RuntimeError(f"non-regular credential source: {entry}")


def _copy_credentials(source: Path, target: Path) -> None:
    """Copy only recognized credential artifacts into platform custody."""
    from tinyassets.boxes.layout import BROKER_READ_GID

    _check_credential_sources(source)
    target.mkdir(mode=0o710)
    os.chown(target, 1001, BROKER_READ_GID)
    filters = [f"--include=/{name}" for name in _CREDENTIAL_ENTRIES]
    filters += [f"--include=/{name}/***" for name in _CREDENTIAL_ENTRIES]
    command = ["rsync", "-aH", "--xattrs", "--one-file-system", "--numeric-ids",
               "--chown", f"1001:{BROKER_READ_GID}", "--chmod=D710,F640",
               *filters, "--exclude=*", f"{source}/", f"{target}/"]
    subprocess.run(command, check=True)
    result = subprocess.run(
        [*command[:-2], "--checksum", "--dry-run", "--itemize-changes", *command[-2:]],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip():
        raise RuntimeError(
            f"credential verification differs for {source}: {result.stdout.strip()[:2000]}")


def _xfs_quota(store: Path, command: str) -> str:
    # xfs_quota exits zero even when it cannot resolve a mounted block device.
    # A successful process status alone did not establish any project quota.
    result = subprocess.run(
        ["xfs_quota", "-x", "-c", command, str(store)],
        check=True, capture_output=True, text=True,
    )
    if result.stderr.strip():
        raise RuntimeError(f"xfs_quota {command}: {result.stderr.strip()}")
    return result.stdout


def _owner_move_preconditions(
    data: Path, store: Path, marker: dict,
) -> list[OwnerMove] | None:
    """Read-only checks shared by the live preflight and locked apply."""
    _mounted(store)
    if marker.get("box_layout") == "owner-box":
        from tinyassets.activity_shape_migration import _pending

        if not _pending(data):
            raise RuntimeError("owner-box has no pending activity conversion")
        _boxed_activity_roots(data, store)
        return None
    if marker.get("box_layout", "owner-split") != "owner-split":
        raise RuntimeError("one-shot box migration requires owner-split")
    plan = _owner_plan(data)
    from tinyassets.activity_shape_migration import _pending

    admitted_centers = {center for owner in plan for center in owner.centers}
    if set(_pending(data)) - admitted_centers:
        raise RuntimeError("activity center has no admitted owner")
    _owner_inode_preflight(data, plan)
    for owner in plan:
        for center in owner.centers:
            _check_credential_sources(data / center)
    if any(entry.name != "lost+found" for entry in store.iterdir()):
        raise RuntimeError("box store must be empty before the one-shot move")
    custody_root = data / ".platform" / "credential-vaults"
    if custody_root.exists() or custody_root.is_symlink():
        raise RuntimeError("private credential custody already exists")
    custody_parent = custody_root.parent
    if custody_parent.is_symlink() or (custody_parent.exists() and not custody_parent.is_dir()):
        raise RuntimeError("platform credential parent is invalid")
    journal = data / ".boxhost"
    if journal.exists() or journal.is_symlink():
        raise RuntimeError("boxhost journal already exists")
    identity_db = data / ".broker" / "state" / "owner-identities.db"
    with readonly_db(identity_db) as db:
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='box_records'").fetchone():
            raise RuntimeError("box records already exist")
    if not _xfs_quota(store, "report -p -n").strip():
        raise RuntimeError("xfs_quota project report unavailable")
    return plan


def preflight(data: Path, store: Path) -> None:
    if os.geteuid() != 0:
        raise PermissionError("box layout preflight requires the one-shot root container")
    lock = _open_layout_lock(data, os.O_RDONLY)
    os.close(lock)
    with readonly_snapshot():
        _owner_move_preconditions(data, store, _marker(data))


def _open_layout_lock(data: Path, access: int) -> int:
    fd = os.open(data / storage_layout.LOCK, access | os.O_NOFOLLOW)
    if not stat.S_ISREG(os.fstat(fd).st_mode):
        os.close(fd)
        raise RuntimeError("owner layout lock is not a regular file")
    return fd


def apply(data: Path, store: Path, data_backup: Path, box_backup: Path) -> None:
    import fcntl

    if os.geteuid() != 0:
        raise PermissionError("box layout apply requires the one-shot root container")
    lock = _open_layout_lock(data, os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        marker = _marker(data)
        if marker.get("box_layout") == "owner-box" and marker.get("activity_shape") == "v1":
            return
        _verified_pair(data_backup, box_backup)
        plan = _owner_move_preconditions(data, store, marker)
        from tinyassets.activity_shape_migration import _pending, convert

        if marker.get("box_layout") == "owner-box":
            roots, failures = _boxed_activity_roots(data, store)
            _atomic_json(storage_layout.marker_path(data), {
                **marker, "state": "migrating", "migration": "owner-box",
            })
            convert(data, roots, unavailable=failures)
            for center, reason in sorted(failures.items()):
                print(f"box layout activity cutover ended work for {center}: {reason}",
                      file=sys.stderr)
            _atomic_json(storage_layout.marker_path(data), {
                **marker, "state": "stable", "activity_shape": "v1",
            })
            return
        assert plan is not None
        pending_activities = _pending(data)
        custody_root = data / ".platform" / "credential-vaults"
        # This is the point of no automatic restart. A failed copy remains
        # visibly migrating until the paired snapshot is restored by an operator.
        _atomic_json(storage_layout.marker_path(data), {
            **marker, "state": "migrating", "migration": "owner-box",
        })
        if pending_activities:
            convert(data, {center: data / center for center in pending_activities})
        from tinyassets.boxes.layout import BROKER_READ_GID

        custody_root.parent.mkdir(mode=0o711, exist_ok=True)
        if custody_root.parent.is_symlink() or not custody_root.parent.is_dir():
            raise RuntimeError("platform credential parent is invalid")
        os.chown(custody_root.parent, 1001, 1001)
        custody_root.mkdir(mode=0o710)
        os.chown(custody_root, 1001, BROKER_READ_GID)
        for owner in plan:
            tree = store / str(owner.uid)
            tree.mkdir(mode=0o700)
            os.chown(tree, owner.uid, owner.uid)
            _xfs_quota(store, f"project -s -p {tree} {owner.uid}")
            verify_tree(tree, owner.uid, True)
            _xfs_quota(store, f"limit -p bhard={owner.quota // 1024}k {owner.uid}")
            for center in owner.centers:
                _copy_credentials(data / center, custody_root / center)
                destination = tree / center
                destination.mkdir(mode=0o700)
                os.chown(destination, owner.uid, owner.uid)
                _copy(data / center, destination, owner.uid)
            agent_home = data / ".platform" / "owners" / str(owner.uid) / "agent-home"
            if agent_home.exists():
                if not agent_home.is_dir() or agent_home.is_symlink():
                    raise RuntimeError("owner agent home is not a real directory")
                destination = tree / ".agent-home"
                destination.mkdir(mode=0o700)
                os.chown(destination, owner.uid, owner.uid)
                _copy(agent_home, destination, owner.uid,
                      extra_excludes=("codex/auth.json",))
            verify_owner(store, tree, owner.uid, owner.quota)
        broker_state = data / ".broker" / "state"
        with sqlite3.connect(broker_state / "owner-identities.db") as db:
            db.execute("PRAGMA synchronous=FULL")
            db.execute("BEGIN IMMEDIATE")
            db.execute("CREATE TABLE box_records (principal TEXT PRIMARY KEY, "
                       "machine INTEGER UNIQUE NOT NULL, project_id INTEGER UNIQUE NOT NULL, "
                       "bhard_bytes INTEGER NOT NULL)")
            db.executemany(
                "INSERT INTO box_records VALUES (?, ?, ?, ?)",
                ((owner.principal, owner.uid, owner.uid, owner.quota) for owner in plan),
            )
        from tinyassets.boxes.state import BoxHostState

        journal = data / ".boxhost"
        journal.mkdir(mode=0o700)  # one-shot migration; never adopt an existing receipt root
        host_state = BoxHostState(journal / "boxhost.db", max_bytes=16 * 1024 * 1024)
        host_state.close()
        os.chmod(journal / "boxhost.db", 0o600)
        _atomic_json(storage_layout.marker_path(data), {
            **marker, "box_layout": "owner-box", "state": "stable",
            "activity_shape": "v1",
        })
    finally:
        os.close(lock)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "preflight", "apply", "census"))
    parser.add_argument("data", type=Path)
    parser.add_argument("--dry-run", action="store_true",
                        help="only read and report the detailed cutover census")
    parser.add_argument("--store", type=Path)
    parser.add_argument("--backup", type=Path)
    parser.add_argument("--box-backup", type=Path)
    args = parser.parse_args()
    try:
        if args.dry_run or args.mode == "census":
            report = census(args.data)
            print(json.dumps(report, indent=2, sort_keys=True))
            return 0 if report["ready"] else UNREADABLE
        if args.mode == "check":
            return check(args.data)
        if args.mode == "preflight":
            if args.store is None:
                parser.error("preflight requires store")
            preflight(args.data, args.store)
            return 0
        if not all((args.store, args.backup, args.box_backup)):
            parser.error("apply requires store and both paired backups")
        apply(args.data, args.store, args.backup, args.box_backup)
        return 0
    except (OSError, ValueError, RuntimeError, sqlite3.Error,
            subprocess.CalledProcessError) as error:
        print(f"box layout migration refused: {error}", file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
