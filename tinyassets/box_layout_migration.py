"""One-shot owner-split -> owner-box move, run only by deploy_fail_safe.

The new image serves only after verified copies and shared stores are published.
A durable journal resumes build or publication after a crash; rollback still
requires the paired snapshot. No runtime startup hook or reverse move exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sqlite3
import stat
import subprocess
import sys
import time
import uuid
from contextlib import ExitStack, closing, contextmanager
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from tinyassets import storage_layout
from tinyassets.boxes.layout import OWNER_BOX_MIGRATION
from tinyassets.broker.owner_identities import OWNER_ID_FIRST, OWNER_ID_LAST
from tinyassets.sqlite_readonly import readonly_db as _private_db
from tinyassets.sqlite_readonly import readonly_snapshot
from tinyassets.usage_policy import account_type, limits_for

PENDING = 10
ABSENT = 4
UNREADABLE = 3
MAX_BACKUP_AGE_S = 3600
_CENTER = re.compile(r"[A-Za-z0-9_-]{1,128}")
_CREDENTIAL_ENTRIES = (".credential-vault.json", ".credentials", ".credentials.json",
                       ".oauth-refresh")
_EPHEMERAL_ENTRIES = (".runtime", ".subscription_state.db",
                      ".subscription_state.db-wal", ".subscription_state.db-shm")
ROW_REASONS = ("conflicting_owners", "orphan_reference", "unadmitted_principal",
               "unknown_center", "no_owner_key")


def _count_loss(summary, label, reason, description, unit):
    group = summary[label].setdefault(reason, {})
    counts = group.setdefault(description, {"class": "dropped", "rows": 0, "files": 0})
    counts[unit] += 1


@dataclass(frozen=True)
class CensusRule:
    """A reviewed table placement, never inferred from a column's spelling.

    References name the database, table and unique parent column. Admitted
    centers win over unadmitted actors, including through parents; competing
    admitted owners still refuse. Empty tables still need a rule.
    """

    placement: str
    principals: tuple[str, ...] = ()
    centers: tuple[str, ...] = ()
    references: tuple[tuple[str, str, str, str], ...] = ()


def _census_rules() -> dict[str, dict[str, CensusRule]]:
    rules: dict[str, dict[str, CensusRule]] = {}

    def add(database, tables, placement, *, principals=(), centers=(), references=()):
        target = rules.setdefault(database, {})
        for table in tables.split():
            if table in target:
                raise AssertionError(f"duplicate census rule: {database}/{table}")
            target[table] = CensusRule(placement, principals, centers, references)

    main = ".tinyassets.db"
    add(main, "user_accounts user_sessions", "router")
    add(main, "deleted_principals", "broker")
    add(main, "llm_credential_deposit_owners llm_credential_custody "
        "llm_credential_deposit_commits llm_credential_refresh_state", "dropped")
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
    # Tables with no reviewed attribution columns still require schema review.
    # Report no_owner_key for their rows but retain attribution_rule_pending.
    add(main, "gate_claims canonical_bindings goal_canonicals unreconciled_writes "
        "branch_tasks_v2_maintenance_state scoped_reset_leases scoped_reset_operations "
        "background_branch_authority_owners action_result_receipts", "owner")
    add(main, "app_event_admissions webhook_deliveries", "owner",
        centers=("universe_id", "center_id"))
    add(main, "owner_fence", "regenerated")
    add(main, "external_agent_catalogue_receipts external_agent_cli_invocations "
        "external_agent_job_receipts", "dropped", principals=("owner_user_id", "owner"),
        centers=("universe_id",))
    add(".auth.db", "oauth_clients authorization_codes access_tokens refresh_tokens", "router")
    add(".hosted-model-auth.db", "hosted_model_flows connection_oauth_flows", "router")
    add(".api-keys.sqlite3", "api_keys api_key_request_counts", "router")
    add(".hook-owners.sqlite3", "hook_owners", "router")
    add(".router-deleted-owners.sqlite3", "deleted_owners", "router")
    add(".erasure-ledger.sqlite3", "events initialized", "router")
    # Legacy destruction facts are seeded before the shared journal is discarded.
    add(".boxhost/boxhost.db", "host boxes ops execs owner_destroys", "dropped")
    add(".runtime/owner-sessions.db", "owner_sessions owner_login_flows owner_login_completions "
        "owner_refresh_sessions owner_approval_handoffs canary_sign_in_attempts "
        "approval_handoffs inline_model_flows", "router")
    add(".outside-client-authority.sqlite3", "authenticated_connections", "owner",
        principals=("owner",))
    add(".outside-client-authority.sqlite3", "outside_switch", "router")
    add(".outside-client-authority.sqlite3", "outside_audit "
        "outside_effects outside_families outside_grants outside_revocations",
        "owner", principals=("owner",))
    add(".owner_devices.db", "owner_devices owner_notify_settings request_notifications", "owner",
        principals=("owner_user_id",))
    add(".broker/state/owner-identities.db", "owner_identities box_records "
        "owner_box_secrets owner_box_revocations owner_volumes owner_snapshots owner_admission "
        "routing_sequence deletion_jobs",
        "broker")
    add(".broker/state/owner-identities.db", "center_admissions", "dropped")
    add(".broker/plans.db", "plans checkout_settlements", "broker")
    add(".broker/state/next-wake.db", "next_wakes", "broker")
    add(".broker/credential-custody.db", "deleted_principals", "broker")
    add(".broker/credential-custody.db", "llm_credential_deposit_owners llm_credential_custody "
        "llm_credential_deposit_commits llm_credential_refresh_state", "dropped")
    add("outbound.db", "outbound_connections outbound_connection_grants "
        "outbound_connector_artifacts outbound_connector_artifact_edges connection_capabilities "
        "broker_schema_migrations box_model_sources browser_vault", "dropped")
    rules[".broker/outbound.db"] = dict(rules["outbound.db"])
    add(".broker/outbound.db", "agent_model_launches agent_request_attempts "
        "agent_request_dispatches agent_request_usage agent_request_usage_links", "broker")
    add(".broker/accounts/*/policy.db", "policy receipts spend_reservations",
        "broker")
    add(".broker/state/ops.db", "ops meta", "dropped")
    add(".broker/accounts/*/policy.db", "once_approvals", "dropped")
    add(".control_plane.db", "triggers", "owner", principals=("owner_principal_id",),
        centers=("command_center_id",))
    add(".control_plane.db", "trigger_fires", "owner", principals=("owner_principal_id",),
        references=(("trigger_key", ".control_plane.db", "triggers", "trigger_key"),))
    add(".automations.db", "automations", "owner", principals=("owner_principal_id",),
        centers=("universe_id",))
    add(".automations.db", "automation_attempts", "owner",
        references=(("automation_id", ".automations.db", "automations", "automation_id"),))
    add(".automations.db", "universe_leases", "owner", centers=("universe_id",))
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
    add(".runs.db", "branch_versions", "owner", principals=("publisher",),
        references=(("branch_def_id", main, "branch_definitions", "branch_def_id"),))
    add(".runs.db", "branch_schedules branch_subscriptions", "owner",
        principals=("owner_actor", "owner_principal_id"), centers=("universe_id",),
        references=(("branch_def_id", main, "branch_definitions", "branch_def_id"),))
    rules[".runs.db"]["branch_schedules"] = CensusRule(
        "owner", principals=("owner_actor", "owner_principal_id"), centers=("universe_id",),
        references=(("branch_def_id", main, "branch_definitions", "branch_def_id"),
                    ("branch_def_id", ".runs.db", "branch_versions", "branch_def_id"),
                    ("branch_version_id", ".runs.db", "branch_versions", "branch_version_id")))
    rules[".runs.db"]["run_file_objects"] = CensusRule(
        "owner", references=(("run_id", ".runs.db", "runs", "run_id"),
                             ("operation_id", ".runs.db", "run_file_operations", "operation_id")))
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
        centers=("universe_id", "center_id"))
    # Preserve the reviewed historical principal/center spellings and joins.
    from dataclasses import replace

    rules[main]["agent_definitions"] = replace(
        rules[main]["agent_definitions"], principals=("author_id",))
    # Historical interchange DDL/writers (removed with the file-based agents):
    # receipts -> stage, links -> stage/receipt, owners -> receipt. Import stages
    # also retain their created definition's author after publication.
    for table, references in {
        "agent_import_stages": (
            ("published_definition_id", main, "agent_definitions", "agent_definition_id"),),
        "agent_conversion_receipts": (("stage_id", main, "agent_import_stages", "stage_id"),),
        "agent_conversion_receipt_links": (
            ("stage_id", main, "agent_import_stages", "stage_id"),
            ("receipt_digest", main, "agent_conversion_receipts", "receipt_digest")),
        "agent_conversion_receipt_owners": (
            ("receipt_digest", main, "agent_conversion_receipts", "receipt_digest"),),
        "agent_interchange_idempotency": (
            ("resource_id", main, "agent_import_stages", "stage_id"),
            ("resource_id", main, "agent_conversion_receipts", "receipt_digest"),
            ("resource_id", main, "agent_definitions", "agent_definition_id")),
    }.items():
        rules[main][table] = replace(rules[main][table], references=references)

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
            rule = rules[database][name]
            rules[database][name] = replace(
                rule,
                principals=tuple(dict.fromkeys((*rule.principals, "owner_user_id", "owner_id"))),
                centers=tuple(dict.fromkeys((*rule.centers, "universe_id", "center_id"))))
    return rules


CENSUS_RULES = _census_rules()

#: Root stores whose code was deleted before cutover. Nothing reads them, so
#: their rows drop by reason and are counted; a name here is never a reader.
RETIRED_STORES = frozenset({(".source_channel_policy.db", "source_channel_policy")})

WORKFLOW_INSTANCE_TABLES = ((".tinyassets.db", "branches"),
                            (".tinyassets.db", "background_branch_bindings"),
                            (".tinyassets.db", "branch_tasks_v2"),
                            (".automations.db", "automations"))


def _census_rule(database: str, table: str) -> CensusRule | None:
    parts = PurePosixPath(database).parts
    if len(parts) == 4 and parts[:2] == (".broker", "accounts") and parts[3] == "policy.db":
        database = ".broker/accounts/*/policy.db"
    return CENSUS_RULES.get(database, {}).get(table)


def _census_columns(database: str, table: str) -> set[str]:
    """Read attribution metadata only; never materialize history or token blobs."""
    rule = _census_rule(database, table)
    columns = set()
    if (database, table) in WORKFLOW_INSTANCE_TABLES:
        columns.update(("branch_def_id", "retired_at"))
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
    if (database, table) == (".runs.db", "run_file_objects"):
        columns.update(("storage_key", "file_id"))
    if database in {"outbound.db", ".broker/outbound.db"} and table == "outbound_connections":
        columns.update(("destination", "owner_user_id"))
    if (database, table) == (".api-keys.sqlite3", "api_keys"):
        columns.update(("owner", "revoked_at"))
    if (database, table) == (".hook-owners.sqlite3", "hook_owners"):
        columns.add("owner")
    if (database, table) == (".broker/plans.db", "plans"):
        columns.update(("owner", "tier", "event_created", "ends_at", "customer_id",
                        "subscription_id", "attempt"))
    if (database, table) == (".broker/plans.db", "checkout_settlements"):
        columns.update(("owner", "attempt"))
    if (database, table) == (".tinyassets.db", "webhook_hooks"):
        columns.update(("token_hash", "revoked_at"))
    if (database, table) == (".tinyassets.db", "agent_interchange_idempotency"):
        columns.add("operation")
    if database.startswith(".broker/accounts/") and table == "policy":
        columns.update(("document", "digest", "generation"))
    return columns


def _quoted(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


@contextmanager
def readonly_db(path):
    """Every migration read verifies and repairs its disposable staged copy."""
    from tinyassets.sqlite_integrity import check

    with _private_db(path) as db:
        check(db, repair=True)
        yield db


def census(data: Path, *, _inventory: dict | None = None,
           _source_marker: dict | None = None) -> dict:
    from tinyassets.sqlite_integrity import check

    repairs = []
    failures = 0
    with readonly_snapshot():
        # Include whole-store knowledge and center-local databases which are
        # copied verbatim, before any indexed read can affect attribution.
        for path in Path(data).rglob("*"):
            if path.is_symlink() or not path.is_file():
                continue
            with path.open("rb") as stream:
                if stream.read(16) != b"SQLite format 3\x00":
                    continue
            try:
                with _private_db(path) as db:
                    repairs.extend(f"repairable index: {path.relative_to(data).as_posix()}/{name}"
                                   for name in check(db, repair=True))
            except (ValueError, sqlite3.DatabaseError):
                failures += 1
        if failures:
            # Corrupt stores cannot supply an honest row/owner census.
            return {"version": 1, "ready": False, "owners": 0, "tables": {},
                    "repairable_indexes": repairs, "refusals": {"database_integrity": failures}}
        report = _census(data, _inventory=_inventory, _source_marker=_source_marker)
        report["repairable_indexes"] = sorted(repairs)
        return report


def _census(data: Path, *, _inventory: dict | None = None,
           _source_marker: dict | None = None) -> dict:
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
    repairable_indexes = []
    tables: dict[tuple[str, str], list[dict]] = {}
    database_files: set[Path] = set()
    foreign_key_rows: list[tuple[str, str, int | None]] = []
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
        marker = (_source_marker if _source_marker is not None else
                  json.loads(marker_path.read_text()) if marker_path.is_file() else {})
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
        dropped_centers = universe_centers - set(admitted)
        if any(not _CENTER.fullmatch(center or "") for center in dropped_centers):
            refusals["invalid_unadmitted_center"] += 1

        # Enumerate actual shared files, including SQLite with an unfamiliar
        # filename. A suffix-only scan would silently miss a renamed store.
        shared_roots = [data]
        for directory in (".broker", ".runtime", ".published",
                          ".command-center-packages", ".boxhost"):
            root = data / directory
            if root.is_dir() and not root.is_symlink():
                shared_roots.extend(p for p in root.rglob("*") if p.is_dir() and not p.is_symlink())
                shared_roots.append(root)
        for root in shared_roots:
            for path in sorted(root.iterdir()):
                if path.is_symlink() or not path.is_file():
                    continue
                if path.name in {".tinyassets.db.prebudgetrepair.bak",
                                 ".tinyassets.db.prebudgetrepair.bak-shm",
                                 ".tinyassets.db.prebudgetrepair.bak-wal"}:
                    continue  # Explicitly out of scope; do not open the stale copy.
                if path == data / "knowledge.db":
                    continue  # Whole-store attribution below; never reconstruct its rows.
                with path.open("rb") as stream:
                    is_database = stream.read(16) == b"SQLite format 3\x00"
                if not is_database:
                    continue
                database_files.add(path)
                relative = path.relative_to(data).as_posix()
                with readonly_db(path) as db:
                    for child, rowid, _, _ in db.execute("PRAGMA foreign_key_check"):
                        positions = ({value[0]: index for index, value in enumerate(db.execute(
                            f"SELECT rowid FROM {_quoted(child)} NOT INDEXED"))}
                                     if rowid is not None else {})
                        foreign_key_rows.append((relative, child, positions.get(rowid)))
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
                        tables[relative, table] = [dict(item) for item in db.execute(
                            f"SELECT {projection} FROM {_quoted(table)} NOT INDEXED")]

        labels = {owner: f"account-{index:04d}"
                  for index, owner in enumerate(sorted(owners), start=1)}
        # Keep candidate owners even when a key fails, so a missing parent can
        # never hide a conflict. One ordered reason is selected only at reporting.
        memo: dict[tuple[str, str, int], tuple[
            frozenset[str], frozenset[str], frozenset[str]]] = {}
        indexes: dict[tuple[str, str, str], dict[object, list[int]]] = {}
        admitted_principals = set(admitted.values()) | set().union(*bindings.values())

        def evidence(database, table, index, active=frozenset()):
            key = (database, table, index)
            if key in memo:
                return memo[key]
            if key in active:
                raise RuntimeError("cyclic census ownership reference")
            rule = _census_rule(database, table)
            row = tables[database, table][index]
            candidates, reasons, center_owners = set(), set(), set()
            workflow_owners = set()
            placeholder = False

            def center_claim(value):
                center_owners.update(known_centers.get(value, set()))
                candidates.update(known_centers.get(value, set()))
                candidates.update(bindings.get(value, set()))
                if value not in known_centers:
                    reasons.add("unadmitted_principal" if value in universe_centers
                                else "unknown_center")

            for column in rule.principals:
                value = row.get(column)
                if not value:
                    continue
                if value in owners and value in admitted_principals:
                    candidates.add(value)
                    if table == "branch_versions" and column == "publisher":
                        workflow_owners.add(value)
                elif value.startswith("universe:"):
                    center_claim(value.partition(":")[2])
                elif not named_principal(value) or value in {"host", "system"}:
                    # A retired non-account actor is not a contradictory claim.
                    placeholder = True
                else:
                    reasons.add("unadmitted_principal")
            for column in rule.centers:
                if row.get(column):
                    center_claim(row[column])
            for column, parent_db, parent, parent_column in rule.references:
                if not row.get(column):
                    continue
                if table == "agent_interchange_idempotency":
                    expected_parent = {"stage_import": "agent_import_stages",
                                       "convert_export": "agent_conversion_receipts",
                                       "publish_stage": "agent_definitions"}.get(
                                           row.get("operation"))
                    if expected_parent is None:
                        reasons.add("orphan_reference")
                    if parent != expected_parent:
                        continue
                index_key = parent_db, parent, parent_column
                if index_key not in indexes:
                    lookup: dict[object, list[int]] = {}
                    for i, candidate in enumerate(tables.get((parent_db, parent), [])):
                        lookup.setdefault(candidate.get(parent_column), []).append(i)
                    indexes[index_key] = lookup
                matches = indexes[index_key].get(row[column], [])
                if not matches:
                    reasons.add("orphan_reference")
                for match in matches:
                    parent_owners, parent_reasons, parent_centers = evidence(
                        parent_db, parent, match, active | {key})
                    candidates.update(parent_owners)
                    reasons.update(parent_reasons)
                    center_owners.update(parent_centers)
                    if database == ".runs.db" and table in {"branch_versions", "branch_schedules"}:
                        # Versions carry their own snapshot. Either admitted
                        # author or publisher is sufficient, but both remain
                        # evidence so contradictory admitted owners refuse.
                        if not parent_reasons:
                            workflow_owners.update(parent_owners)
            if workflow_owners:
                reasons.difference_update({"orphan_reference", "unadmitted_principal",
                                           "unknown_center", "no_owner_key"})
            if center_owners:
                reasons.difference_update({
                    "unadmitted_principal", "unknown_center", "no_owner_key"})
            if len(candidates) > 1:
                reasons.add("conflicting_owners")
            if not candidates and not reasons:
                reasons.add("unadmitted_principal" if placeholder else "no_owner_key")
            memo[key] = (frozenset(candidates), frozenset(reasons), frozenset(center_owners))
            return memo[key]

        def row_reason(database, table, index):
            _, reasons, _ = evidence(database, table, index)
            return next((reason for reason in ROW_REASONS if reason in reasons), None)

        def row_owners(database, table, index):
            candidates, _, _ = evidence(database, table, index)
            reason = row_reason(database, table, index)
            if reason == "conflicting_owners":
                return frozenset()
            return frozenset({None}) if reason else candidates

        policy_accounts = sorted({PurePosixPath(database).parts[2]
                                  for database, _ in tables
                                  if database.startswith(".broker/accounts/")})
        policy_labels = {account: f"policy-account-{index:04d}"
                         for index, account in enumerate(policy_accounts, 1)}
        table_report = {}
        custody_drops = Counter(dict.fromkeys(("credentials", "connections", "grants",
                                               "policy_scopes", "source_descriptors",
                                               "replay_operations", "custody_metadata",
                                               "policy_approvals"), 0))
        totals: Counter = Counter(dict.fromkeys(
            ("owner", "router", "broker", "published", "dropped", "regenerated"), 0))
        assignments = {}
        published_owner_copies = {}
        for (database, table), rows in sorted(tables.items()):
            rule = _census_rule(database, table)
            placement = rule.placement if rule else "unknown"
            counts: Counter = Counter()
            reasons: Counter = Counter()
            public = 0
            published_per_owner: Counter = Counter()
            if rule is None and (database, table) in RETIRED_STORES:
                placement = "dropped"
                reasons["retired_store"] += len(rows)
            dropped = len(rows) if placement == "dropped" else 0
            if database.startswith(".broker/accounts/") and table == "policy":
                from tinyassets.broker.policy import _digest

                for row in rows:
                    document = json.loads(row["document"])
                    if _digest(document) != row["digest"]:
                        refusals["policy_digest_mismatch"] += 1
                    custody_drops["policy_scopes"] += sum(
                        entry["scope"] != "account" for entry in document["entries"])
            if placement == "dropped":
                kind = {"outbound_connections": "connections",
                        "outbound_connection_grants": "grants",
                        "box_model_sources": "source_descriptors", "browser_vault": "credentials",
                        "ops": "replay_operations",
                        "once_approvals": "policy_approvals"}.get(table)
                if table.startswith("llm_credential_"):
                    kind = "custody_metadata"
                if kind:
                    custody_drops[kind] += len(rows)
                    reasons["center_custody_reconnect"] += len(rows)
            targets = [placement] * len(rows)
            if rule is None and placement != "dropped":
                refusals["unknown_table"] += 1
            elif placement == "owner":
                if rows and not (rule.principals or rule.centers or rule.references):
                    refusals["attribution_rule_pending"] += 1
                for index, row in enumerate(rows):
                    if ((database == ".tinyassets.db" and table in {"branch_definitions", "goals"}
                            and row.get("visibility") == "public"
                            and (table == "goals" or row.get("published") == 1))
                            or ((database, table) == (".runs.db", "branch_versions")
                                and row.get("public") == 1)):
                        public += 1
                        targets[index] = "published"
                        if (database, table) == (".tinyassets.db", "branch_definitions"):
                            candidates = row_owners(database, table, index)
                            if row_reason(database, table, index) == "conflicting_owners":
                                refusals["unattributed_owner_rows"] += 1
                            elif len(candidates) == 1 and next(iter(candidates)) in owners:
                                owner = next(iter(candidates))
                                published_owner_copies[database, table, index] = owner
                                published_per_owner[labels[owner]] += 1
                        continue
                    reason = row_reason(database, table, index)
                    if reason:
                        reasons[reason] += 1
                    candidates = row_owners(database, table, index)
                    if len(candidates) != 1:
                        counts["unattributed"] += 1
                        targets[index] = "unattributed"
                    else:
                        owner = next(iter(candidates))
                        targets[index] = "dropped" if owner is None else ("owner", owner)
                        if owner is None:
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
            report_database = database
            if database.startswith(".broker/accounts/"):
                parts = PurePosixPath(database).parts
                report_database = f".broker/accounts/{policy_labels[parts[2]]}/policy.db"
            table_report[f"{report_database}/{table}"] = {
                "class": placement, "total": len(rows), "per_owner": dict(counts),
                "published": public, "published_per_owner": dict(published_per_owner),
                "dropped": dropped, "reasons": dict(reasons),
                "regenerated": len(rows) if placement == "regenerated" else 0,
                "attribution": ("pending" if rule and placement == "owner"
                                and not (rule.principals or rule.centers or rule.references)
                                else "specified"),
            }

        workflow_instances = {}
        retired_automations = {}
        for owner, label in labels.items():
            local = {}
            for key in (*WORKFLOW_INSTANCE_TABLES, (".tinyassets.db", "branch_definitions")):
                local[key] = [row for index, row in enumerate(tables.get(key, []))
                              if assignments[key][index] == ("owner", owner)
                              or published_owner_copies.get((*key, index)) == owner]
            counts, history = _workflow_resolution(local)
            unresolved = counts["instances"] - counts["resolved"]
            workflow_instances[label] = {**counts, "unresolved_workflow_instance": unresolved}
            retired_automations[label] = history
            refusals["unresolved_workflow_instance"] += unresolved

        for database, table, index in foreign_key_rows:
            targets = assignments.get((database, table), [])
            if index is None or targets[index] != "dropped":
                refusals["database_foreign_keys"] += 1

        plan_report, credential_report, billing, billing_refusals = _census_accounts(
            data, known_centers, labels, tables, refusals)
        custody_drops["credentials"] = sum(
            sum(row.values()) for row in credential_report.values())
        for (database, table), rows in tables.items():
            rule = _census_rule(database, table)
            if rule and rule.placement == "dropped" and table in {
                    "external_agent_catalogue_receipts", "external_agent_cli_invocations",
                    "external_agent_job_receipts"}:
                for index in range(len(rows)):
                    candidates = row_owners(database, table, index)
                    if len(candidates) == 1 and next(iter(candidates)) in labels:
                        credential_report[labels[next(iter(candidates))]]["dropped_cli"] += 1
        run_owners = {}
        for index, row in enumerate(tables.get((".runs.db", "runs"), [])):
            run_id = row.get("run_id")
            if run_id:
                candidates = (evidence(".runs.db", "runs", index)[0]
                              if row_reason(".runs.db", "runs", index) == "conflicting_owners"
                              else row_owners(".runs.db", "runs", index))
                run_owners.setdefault(run_id, set()).update(candidates)
        file_targets = {}
        patterns = {}
        file_kinds = {}
        loss_summary = {label: {} for label in (*labels.values(), "unattributed")}
        losses = {label: {"rows": {}, "files": {}} for label in labels.values()}
        losses["unattributed"] = {"rows": {}, "files": {}}
        for (database, table), targets in assignments.items():
            for index, target in enumerate(targets):
                if target != "dropped":
                    continue
                if (database, table) in RETIRED_STORES:
                    # No attribution rule exists for a deleted store's rows.
                    _count_loss(loss_summary, "unattributed", "retired_store",
                                "retired store", "rows")
                    losses["unattributed"]["rows"][f"{database}/{table}"] = (
                        losses["unattributed"]["rows"].get(f"{database}/{table}", 0) + 1)
                    continue
                candidates, _, _ = evidence(database, table, index)
                label = (labels[next(iter(candidates))] if len(candidates) == 1
                         and next(iter(candidates)) in labels else "unattributed")
                key = f"{database}/{table}"
                losses[label]["rows"][key] = losses[label]["rows"].get(key, 0) + 1
                reason = row_reason(database, table, index) or "retired_cli_state"
                description = ("usage reservation history"
                               if table == "served_provider_budget_reservations" else
                               "provider bindings" if table == "provider_work_bindings" else
                               "workflow history" if table in {
                                   "branch_versions", "branch_schedules"} else
                               "CLI login state" if _census_rule(database, table).placement
                               == "dropped" else table.replace("_", " "))
                _count_loss(loss_summary, label, reason, description, "rows")
                if description == "workflow history":
                    unit = "versions" if table == "branch_versions" else "schedules"
                    workflow_counts = loss_summary[label][reason][description]
                    workflow_counts[unit] = workflow_counts.get(unit, 0) + 1
        blob_targets = {}
        for index, row in enumerate(tables.get((".runs.db", "run_file_objects"), [])):
            storage_key = row.get("storage_key") or row.get("file_id")
            key = str(storage_key) + ".body" if storage_key else None
            if key:
                target = assignments[".runs.db", "run_file_objects"][index]
                if key in blob_targets and blob_targets[key] != target:
                    refusals["conflicting_blob_owners"] += 1
                    target = "unattributed"
                blob_targets[key] = target
        histories, history_report = _census_histories(data, admitted, labels, loss_summary)
        knowledge_report = {label: {"description": "knowledge stores", "files": 0, "bytes": 0}
                            for label in labels.values()}
        file_report = _census_files(data, known_centers, labels, database_files, refusals,
                                    dropped_centers, run_owners, file_targets, patterns, losses,
                                    {str(uid): owner for owner, uid in permanent.items()},
                                    file_kinds, loss_summary, blob_targets, knowledge_report)
        if _inventory is not None:
            _inventory.update(tables=tables, assignments=assignments, owners=permanent,
                              published_owner_copies=published_owner_copies,
                              centers=admitted, dropped_centers=dropped_centers, labels=labels,
                              file_targets=file_targets, histories=histories, billing=billing)
        from tinyassets.cutover_deletions import census_count

        return {
            "legacy_deletions": census_count(data),
            "version": 1, "ready": not any(refusals.values()), "owners": len(owners),
            "admitted_centers": len(admitted), "explicit_centers": len(known_centers),
            "databases": len(database_files), "tables": table_report, "row_totals": dict(totals),
            "plans": plan_report, "credentials": credential_report, "files": file_report,
            "center_custody_drops": dict(custody_drops),
            "billing_refusals": billing_refusals,
            "repairable_indexes": repairable_indexes,
            "file_patterns": patterns, "file_pattern_kinds": file_kinds,
            "losses": losses, "loss_summary": loss_summary,
            "history_splits": history_report,
            "knowledge_stores": knowledge_report,
            "workflow_instances": workflow_instances,
            "workflow_history": {
                label: {**{unit: table_report.get(f".runs.db/{table}", {}).get(
                    "per_owner", {}).get(label, 0)
                        for table, unit in (("branch_versions", "versions"),
                                            ("branch_schedules", "schedules"))},
                        **({"automations with non-empty retired_at kept as history":
                            retired_automations[label]}
                           if retired_automations[label] else {})}
                for label in labels.values()},
            "accepted_user_costs": [
                "Every account reconnects a model source; CLI connections are retired.",
                "Keys pasted into chat are no longer accepted (tap-to-connect).",
                "/mcp/hooks/ integrations re-point to /hooks/inbound/.",
                "Local image descriptions are dropped.",
                "Billing status no longer shows usage figures.",
            ],
            "account_model_source_summary": "every account reconnects a model source",
            "restore_write_loss": "Restoring the paired pre-cutover backup loses all "
                                  "post-cutover owner writes, including chats, requests and pause.",
            "reconnect_path": "shared door model-connect, then model-use confirmation",
            "credential_custody": {
                "outside_broker_files_dropped": sum(
                    1 for relative, target in file_targets.items()
                    if target == "dropped" and len(PurePosixPath(relative).parts) == 2
                    and PurePosixPath(relative).name == ".credential-vault.json"),
                "explanation": "All center-keyed custody is dropped. No credential, "
                    "connection, grant or policy scope is carried; owners reconnect.",
            },
            "refusals": {key: value for key, value in sorted(refusals.items()) if value},
        }


_HISTORY_FILES = {".scheduler_fires.jsonl": "scheduled-run history",
                  "ledger.json": "activity ledger"}


def _history_entries(path):
    if path.is_symlink():
        raise RuntimeError("history source is a symlink")
    if path.name == ".scheduler_fires.jsonl":
        entries = [(json.loads(line), line)
                   for line in path.read_text(encoding="utf-8").splitlines()]
    else:
        document = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(document, list):
            raise RuntimeError("activity ledger must be a list")
        entries = [(entry, entry) for entry in document]
    if any(not isinstance(entry, dict) for entry, _ in entries):
        raise RuntimeError("history entries must be objects")
    return entries


def _census_histories(data, centers, labels, loss_summary):
    histories, report = {}, {}
    admitted = set(centers.values())
    for name, description in _HISTORY_FILES.items():
        path = data / name
        if not path.exists() and not path.is_symlink():
            continue
        targets = []
        counts = dict.fromkeys(labels.values(), 0)
        for entry, _ in _history_entries(path):
            if name == ".scheduler_fires.jsonl":
                owner = centers.get(entry.get("universe_id"))
            else:
                actor, target = entry.get("actor"), entry.get("target")
                owner = actor if actor in admitted else target if target in admitted else None
            targets.append(owner)
            if owner is not None:
                counts[labels[owner]] += 1
        dropped = targets.count(None)
        if dropped:
            group = loss_summary["unattributed"].setdefault("no_admitted_owner", {})
            group[description] = {"class": "dropped", "entries": dropped}
        counts_report = {"description": description, "total": len(targets),
                         "per_owner": counts, "dropped": dropped}
        report[name] = counts_report
        histories[name] = {"targets": targets, "report": counts_report,
                           "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return histories, report


def _census_accounts(data, centers, labels, tables, refusals):
    """Read Stripe's actual tier rows and raw vault shapes without loading secrets into reports."""
    import math
    from collections import Counter

    plans = {label: {"tier": "free", "paid_sources": 0} for label in labels.values()}
    credentials = {label: {"dropped_cli": 0, "dropped_http": 0, "dropped_other": 0}
                   for label in labels.values()}
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
    legacy = {owner: [] for owner in labels}
    billing_refusals = {}

    def refuse(label, reason):
        refusals[reason] += 1
        counts = billing_refusals.setdefault(label or "unattributed", {})
        counts[reason] = counts.get(reason, 0) + 1

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
                entries = db.execute("SELECT key,value FROM subscription_meta").fetchall()
                meta = dict(entries)
                if len(meta) != len(entries):
                    refuse(label, "ambiguous_subscription_metadata")
            if label is not None:
                legacy[next(iter(owners))].append(meta)
            elif meta.get("checkout_attempt_v1") or meta.get("checkout_claim_at"):
                refuse(None, "unattributed_checkout_attempt")
            tier = meta.get("tier", "free")  # Legacy schema: absence means no subscription.
            if tier not in {"free", "paid"}:
                refusals["invalid_tier"] += 1
            elif tier == "paid":
                if label is None:
                    refusals["unattributed_paid_tier"] += 1
                else:
                    paid[label] += 1
                    plans[label] = {"tier": "paid", "paid_sources": paid[label]}
                    if paid[label] > 1:
                        refusals["multiple_paid_sources"] += 1
        # C11: count platform slots, then drop every kind without ownership joins.
        # Center-local legacy files remain counted file losses, never opened.
        vault = data / ".platform/credential-vaults" / directory.name / ".credential-vault.json"
        if vault.is_symlink() or vault.parent.is_symlink():
            raise RuntimeError("census vault path is a symlink")
        if vault.is_file():
            raw = json.loads(vault.read_text(encoding="utf-8"))
            records = raw.get("credentials") if isinstance(raw, dict) else None
            if not isinstance(records, list) or any(not isinstance(r, dict) for r in records):
                refusals["invalid_vault_document"] += 1
                continue
            counts = credentials.setdefault(label or "unattributed", {
                "dropped_cli": 0, "dropped_http": 0, "dropped_other": 0})
            for record in records:
                key = {"llm_subscription": "dropped_cli", "http": "dropped_http"}.get(
                    record.get("credential_type"), "dropped_other")
                counts[key] += 1
    for row in tables.get((".broker/plans.db", "plans"), []):
        owner, tier = row.get("owner"), row.get("tier")
        if owner not in labels and row.get("attempt") is not None:
            refuse(None, "unattributed_checkout_attempt")
        if tier not in {"free", "paid"}:
            refusals["invalid_tier"] += 1
        elif tier == "paid":
            if owner not in labels:
                refusals["unattributed_paid_tier"] += 1
            else:
                label = labels[owner]
                paid[label] = max(1, paid[label])
                plans[label] = {"tier": "paid", "paid_sources": paid[label]}
    # The legacy tier-event writer never persisted subscription/customer IDs,
    # and webhook delivery markers hold no mapping. Only owner-keyed Plans
    # records (written from verified deliveries) can supply that authority.
    shared = {row["owner"]: row for row in tables.get((".broker/plans.db", "plans"), [])}
    settlements = tables.get((".broker/plans.db", "checkout_settlements"), [])
    for row in settlements:
        if row["owner"] not in labels:
            refuse(None, "unattributed_checkout_attempt")
        try:
            attempt = _cutover_checkout(row["attempt"])
            if any(attempt[key] != row[key] for key in ("session_id", "attempt_id")):
                refuse(labels.get(row["owner"]), "conflicting_checkout_attempts")
        except (ValueError, TypeError, KeyError):
            refuse(labels.get(row["owner"]), "malformed_checkout_attempt")
    billing = {}
    references = {}
    import time

    now = time.time()
    for owner, label in labels.items():
        plan = shared.get(owner, {})
        tier = plans[label]["tier"]
        customer, subscription = plan.get("customer_id"), plan.get("subscription_id")
        if tier == "paid" and not (_billing_id(customer, "cus_")
                                    and _billing_id(subscription, "sub_")):
            refuse(label, "paid_subscription_mapping_missing")
        for value in (customer, subscription):
            if value:
                if value in references and references[value] != owner:
                    refuse(label, "billing_mapping_conflict")
                references[value] = owner
        sources = [dict(event_created=m.get("tier_event_at", 0),
                        ends_at=m.get("tier_ends_at")) for m in legacy[owner]
                   if m.get("tier", "free") == tier]
        if plan.get("tier") == tier:
            sources.append(plan)
        try:
            latest = max(sources, key=lambda m: float(m["event_created"]),
                         default={"event_created": 0})
            event = float(latest["event_created"])
            end = float(latest["ends_at"]) if latest.get("ends_at") else None
            if not math.isfinite(event) or (end is not None and not math.isfinite(end)):
                raise ValueError("invalid event time")
        except (ValueError, TypeError, KeyError):
            refuse(label, "invalid_billing_event")
            event, end = 0, None
        attempts = []
        for raw in ([m.get("checkout_attempt_v1") for m in legacy[owner]]
                    + [plan.get("attempt")]
                    + [row["attempt"] for row in settlements
                       if row["owner"] == owner]):
            if raw is None:
                continue
            try:
                attempt = _cutover_checkout(raw)
            except (ValueError, TypeError, KeyError):
                refuse(label, "malformed_checkout_attempt")
                continue
            if attempt["session_id"]:
                if any(previous != attempt and any(previous[key] == attempt[key]
                       for key in ("session_id", "attempt_id")) for previous in attempts):
                    refuse(label, "conflicting_checkout_attempts")
                if attempt not in attempts:
                    attempts.append(attempt)
                for value in (attempt["attempt_id"], attempt["session_id"]):
                    if value:
                        if value in references and references[value] != owner:
                            refuse(label, "conflicting_checkout_attempts")
                        references[value] = owner
            else:
                plans[label]["dropped_sessionless_attempts"] = (
                    plans[label].get("dropped_sessionless_attempts", 0) + 1)
        for meta in legacy[owner]:
            if "checkout_claim_at" in meta:
                try:
                    claim = float(meta["checkout_claim_at"])
                    if not math.isfinite(claim) or claim + 2220 > now:
                        refuse(label, "ambiguous_checkout_attempt")
                except (TypeError, ValueError):
                    refuse(label, "malformed_checkout_attempt")
        live_attempts = [attempt for attempt in attempts if attempt["expires_at"] > now]
        if len(live_attempts) > 1:
            refuse(label, "conflicting_checkout_attempts")
        billing[owner] = dict(tier=tier, event_created=event, ends_at=end,
                              customer_id=customer, subscription_id=subscription,
                              attempt=live_attempts[0] if len(live_attempts) == 1 else None,
                              settlements=attempts)
    return plans, credentials, billing, billing_refusals


def _billing_id(value, prefix):
    import re

    return isinstance(value, str) and re.fullmatch(re.escape(prefix) + r"[A-Za-z0-9_]+", value)


def _cutover_checkout(raw):
    """Validate the historical writer's complete record before preserving it."""
    import math

    attempt = json.loads(raw)
    if not isinstance(attempt, dict) or set(attempt) != {
            "attempt_id", "created_at", "expires_at", "mode", "params", "session_id", "url"}:
        raise ValueError("invalid attempt shape")
    params = attempt["params"]
    if (not isinstance(params, dict) or set(params) != {
            "price_id", "success_url", "cancel_url", "expires_at",
            "entitlement_version", "entitlement_claim"}
            or not isinstance(attempt["attempt_id"], str) or not attempt["attempt_id"]
            or attempt["mode"] not in {"live", "test"}):
        raise ValueError("invalid frozen checkout")
    for value in (attempt["created_at"], attempt["expires_at"], params["expires_at"]):
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError("invalid checkout expiry")
    if not attempt["created_at"] < params["expires_at"] <= attempt["expires_at"]:
        raise ValueError("checkout outlives its guard")
    if any(not isinstance(params[key], str) or not params[key] for key in (
            "price_id", "success_url", "cancel_url", "entitlement_version", "entitlement_claim")):
        raise ValueError("invalid checkout parameters")
    if attempt["session_id"] is not None and not _billing_id(attempt["session_id"], "cs_"):
        raise ValueError("invalid checkout session")
    if attempt["url"] is not None and (not isinstance(attempt["url"], str)
                                       or not attempt["url"].startswith("https://")):
        raise ValueError("invalid checkout URL")
    return attempt


def _legacy_knowledge_owner(path, centers):
    """Attribute the entire legacy store using its actual memory-scope columns."""
    candidates = set()
    if not path.is_file() or path.is_symlink():
        return None, "no_owner_key"
    admitted = set().union(*centers.values()) if centers else set()
    with readonly_db(path) as db:
        for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'"):
            if table == "sqlite_sequence":
                continue
            if table not in {"entities", "edges", "facts", "communities"}:
                return None, "no_owner_key"
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({_quoted(table)})")}
            scope = [name for name in ("universe_id", "user_id") if name in columns]
            projection = ",".join(map(_quoted, scope)) or "1"
            for values in db.execute(f"SELECT {projection} FROM {_quoted(table)}"):
                row = dict(zip(scope, values))
                owners = centers.get(row.get("universe_id"), set())
                principal = row.get("user_id")
                if principal in admitted:
                    owners = owners | {principal}
                if not owners:
                    return None, "no_owner_key"
                candidates.update(owners)
    if len(candidates) != 1:
        return None, "conflicting_owners" if candidates else "no_owner_key"
    return next(iter(candidates)), None


def _census_files(data, centers, labels, databases, refusals, dropped_centers=(),
                  run_owners=None, file_targets=None, patterns=None, losses=None, uid_owners=None,
                  pattern_kinds=None, loss_summary=None, blob_targets=None, knowledge_report=None):
    from collections import Counter

    counts: Counter = Counter()
    legacy_owner, legacy_reason = _legacy_knowledge_owner(data / "knowledge.db", centers)
    shared = {
        ".layout.json": "broker", ".layout.lock": "broker",
        ".owner-cutover.json": "broker", ".ordinary-admission.json": "regenerated",
        ".router-credentials.json": "router", ".disaster-restore.json": "dropped",
        "platform-expected-instance.json": "router", "release-state.json": "router",
        ".billing_webhook_verified.json": "router",
        ".broker/state/fence.json": "broker",
        ".broker/state/architecture-signing.json": "broker",
        ".connect/client_id": "router", ".connect/client_secret": "router",
        ".connect/session_secret": "router",
    }
    # Creator-backed SQLite filenames also classify empty files and detached
    # sidecars. Real databases still go through table-by-table schema review.
    for database, rules in CENSUS_RULES.items():
        placements = {rule.placement for rule in rules.values()}
        if placements <= {"broker", "router"} and len(placements) == 1 and "*" not in database:
            shared[database] = next(iter(placements))
    published = {"wiki", ".wiki", ".published", ".command-center-packages"}
    # Exact creator-backed roots: storage_accounting.ROOT_ENTRIES and the
    # LanceDB index writers. No suffix-wide rule can drop arbitrary user files.
    regenerated = {
        ".runtime", ".workspace-staging", ".custody.lock",
        ".agent-turn-runners", ".consumer_liveness", ".owner_tree",
        ".universe-tool-slots", ".outbound-proxy", ".run-execution-locks",
        ".run-file-operation-locks", ".delivery-locks", ".workspace-family-locks",
        ".run_recovery.lock", ".run_recovery.lock.pid", ".ui-preview.lock",
        ".deploy-pending.json",
        ".runtime_status.json", ".worker_supervisor.json",
        ".engine_mcp_http_routes.json", ".scoped-reset.barrier",
    }
    dropped_roots = {"scratch", ".active_universe", "activity.log", "activity.log.1"}
    from tinyassets.storage_accounting import ROOT_ENTRIES

    def directory_kind(parts):
        if len(parts) == 1:
            return "root_file"
        if parts[0] in centers:
            return "admitted_center"
        if parts[0] in dropped_centers:
            return "unadmitted_center"
        if parts[:2] == (".broker", "accounts") and len(parts) > 2:
            return "broker_account"
        if re.fullmatch(r"[a-f0-9]{64}", parts[0]):
            return "account_hash"
        if parts[0] in ROOT_ENTRIES or parts[0] in {"runs", ".published"}:
            return "platform_store"
        return "unknown_dir"

    def classify(path, relative):
        parts = relative.parts
        if parts[0] == "lancedb":
            return "dropped"
        if parts[0] in {"knowledge.db", "knowledge.db-wal", "knowledge.db-shm",
                         "knowledge.db-journal"}:
            return ("owner", legacy_owner) if legacy_owner else "dropped"
        if relative.as_posix() in {".tinyassets.db.prebudgetrepair.bak",
                                  ".tinyassets.db.prebudgetrepair.bak-shm",
                                  ".tinyassets.db.prebudgetrepair.bak-wal"}:
            return "dropped"
        if relative.as_posix() in _HISTORY_FILES:
            return "split"
        if len(parts) == 2 and parts[0] == ".run-file-custody":
            if parts[1] == ".blob-root.lock":
                return "regenerated"
            if parts[1].endswith(".body"):
                return (blob_targets or {}).get(parts[1], "dropped")
        if (len(parts) == 4 and parts[:2] == (".broker", ".outbound-proxy")
                and parts[3] == "audit.jsonl"):
            return "broker"
        if ((len(parts) == 4 and parts[0] == ".executors"
             and parts[2:] == (".codex", "config.toml")) or
                (len(parts) == 2 and parts[0] == ".platform"
                 and re.fullmatch(r"external-agent-refresh-[a-f0-9]+", parts[1]))):
            return "dropped"
        if parts[0] in dropped_centers:
            return "dropped"
        if len(parts) > 1 and parts[1] in _CREDENTIAL_ENTRIES:
            return "dropped"
        if parts[0] in centers and len(parts) > 1:
            if parts[1] in {".runtime", ".consumer_liveness", ".workspace-staging"}:
                return "regenerated"
            if parts[1].startswith(".subscription_state.db"):
                return "broker"
            return ("owner", next(iter(centers[parts[0]])))
        if (len(parts) >= 5 and parts[:2] == (".platform", "owners")
                and parts[2].isdecimal() and parts[3] == "agent-home"):
            return "dropped"
        if parts[0] == "runs" and len(parts) > 1:
            position = 2 if parts[1] == "archived" and len(parts) > 2 else 1
            component = parts[position]
            names = {component}
            if position == len(parts) - 1:
                while any(component.endswith(suffix)
                          for suffix in (".jsonl", ".json", ".log", ".txt", ".gz")):
                    component = component.rsplit(".", 1)[0]
                    names.add(component)
            candidates = set().union(*(run_owners[name] for name in names
                                       if run_owners and name in run_owners))
            if not candidates or candidates == {None}:
                return "dropped"
            if len(candidates) == 1 and next(iter(candidates)) in labels:
                return ("owner", next(iter(candidates)))
            return "unattributed"
        if any(path == database.with_name(database.name + suffix)
               for database in databases for suffix in ("-wal", "-shm", "-journal")):
            return "regenerated"
        if path in databases:
            return "database_files"
        name = relative.as_posix()
        if name == ".boxhost/boxhost.lock":
            return "dropped"
        if name in shared:
            return shared[name]
        if any(name == database + suffix for database in CENSUS_RULES
               for suffix in ("-wal", "-shm", "-journal")):
            return "regenerated"
        if (len(parts) == 4 and parts[:2] == (".broker", "accounts")
                and parts[3] in {"policy.db", "policy.db-wal", "policy.db-shm",
                                 "policy.db-journal"}):
            return "broker" if parts[3] == "policy.db" else "regenerated"
        # browser_vault._owner_key still reads this legacy key before moving
        # it to the separate secret volume. Never infer arbitrary key custody.
        if ((len(parts) == 3 and parts[:2] == (".broker", "browser-vault") or
             len(parts) == 2 and parts[0] == "browser-vault")
                and re.fullmatch(r"[a-f0-9]{64}\.key", parts[-1])):
            return "broker"
        if parts[0] in published:
            return "published"
        if len(parts) >= 4 and parts[:2] == (".platform", "credential-vaults"):
            # credential_vault and credential_refresh are the only writers.
            if len(parts) == 4 and parts[3] == ".credential-vault.json":
                return "dropped"
            if ((len(parts) == 4 and parts[3] == ".custody.lock") or
                    (len(parts) == 5 and parts[3] == ".oauth-refresh"
                     and re.fullmatch(r"[a-f0-9]{32}\.lock", parts[4]))):
                return "regenerated"
        if parts[0] in regenerated:
            return "regenerated"
        # control_plane.tick and api.universe write one heartbeat per consumer.
        if len(parts) == 1 and re.fullmatch(r"\.worker_supervisor\.[A-Za-z0-9_.-]+\.json",
                                           parts[0]):
            return "regenerated"
        # Interrupted storage_layout / cutover / fence / custody publications.
        if ((len(parts) == 1 and re.fullmatch(r"\.(?:layout|owner-cutover)\.json\..+\.tmp",
                                             parts[0])) or
                (parts[:2] == (".broker", "state") and len(parts) == 3
                 and re.fullmatch(r"\.fence\..+", parts[2])) or
                (parts[:2] == (".platform", "credential-vaults") and len(parts) == 4
                 and re.fullmatch(r"\.vault-.+\.tmp", parts[3]))):
            return "regenerated"
        if parts[0] in dropped_roots:
            return "dropped"
        if (len(parts) == 4 and parts[:2] == (".broker", "accounts")
                and parts[3] == ".deleted"):
            return "regenerated"
        if relative.as_posix() == ".broker/state/fence.json":
            return "broker"
        if len(parts) >= 3 and parts[0] in {".agent-sessions", ".universe-sidecars"}:
            if stat.S_ISSOCK(path.lstat().st_mode):
                return "regenerated"
            candidates = centers.get(parts[1], set())
            return ("owner", next(iter(candidates))) if len(candidates) == 1 else "dropped"
        if directory_kind(parts) in {"unadmitted_center", "unknown_dir"}:
            return "dropped"
        return "unattributed"

    # Preserve only fixed vocabulary from code, at its known layout position.
    # Unknown basenames never escape. Only a bounded conventional extension
    # survives; a private dotted suffix must not become a report channel.
    fixed_roots = ({name for name in shared if "/" not in name}
                   | published | regenerated | dropped_roots)
    fixed_roots |= {
        ".platform", ".broker", ".agent-sessions", ".universe-sidecars", "runs",
        ".tinyassets.db.prebudgetrepair.bak",
    } | {Path(database).name for database in CENSUS_RULES if "/" not in database}
    fixed_files = {PurePosixPath(name).name for name in shared} | {
        "policy.db", "policy.db-wal", "policy.db-shm", "policy.db-journal",
        ".deleted", ".credential-vault.json", ".custody.lock",
    } | {PurePosixPath(database).name + suffix for database in CENSUS_RULES
         for suffix in ("", "-wal", "-shm", "-journal")}

    def masked(parts):
        if parts[0] == ".connect":
            return "<router-private>/<file>"
        result = []
        for index, part in enumerate(parts):
            value = "<file>" if index == len(parts) - 1 else "<id>"
            if index == 0:
                value = part if part in fixed_roots else (
                    "<center>" if part in centers or part in dropped_centers else value)
            elif index == 1 and parts[0] in set(centers) | set(dropped_centers):
                if part in _CREDENTIAL_ENTRIES or part in regenerated or part == ".agent-sessions":
                    value = part
            elif index == 1 and parts[0] in {".agent-sessions", ".universe-sidecars"}:
                value = "<center>"
            elif parts[0] == ".platform":
                if index == 1 and part in {"owners", "credential-vaults"}:
                    value = part
                elif index == 2:
                    value = "<owner>" if parts[1] == "owners" else "<center>"
                elif index == 3 and parts[1] == "owners" and part == "agent-home":
                    value = part
            result.append(value)
        if parts[-1] in fixed_files or (len(parts) == 1 and parts[-1] in fixed_roots):
            result[-1] = parts[-1]
        else:
            suffix = re.search(r"\.(db-(?:wal|shm|journal)|[A-Za-z0-9]{1,10})$", parts[-1])
            result[-1] = "<file>" + ("." + suffix[1].lower() if suffix else "")
        return "/".join(result)

    for directory, dirs, files in os.walk(data, followlinks=False):
        root = Path(directory)
        # os.walk lists directory symlinks in dirs but never descends them.
        for name in [*files, *(name for name in dirs if (root / name).is_symlink())]:
            path = root / name
            relative = path.relative_to(data)
            parts = relative.parts
            target = classify(path, relative)
            knowledge = (parts[0] in {"knowledge.db", "knowledge.db-wal", "knowledge.db-shm",
                                      "knowledge.db-journal", "lancedb"} or
                         (parts[0] in centers and ("lancedb" in parts[1:] or
                          any("knowledge" in part and (".db" in part or ".sqlite" in part)
                              for part in parts[1:]))))
            if knowledge and isinstance(target, tuple) and knowledge_report is not None:
                group = knowledge_report[labels[target[1]]]
                group["files"] += 1
                group["bytes"] += path.lstat().st_size
            placement = "owner" if isinstance(target, tuple) or target == "split" else target
            counts[labels[target[1]] if isinstance(target, tuple) else placement] += 1
            if file_targets is not None:
                file_targets[relative.as_posix()] = target
            pattern = masked(parts)
            kind = directory_kind(parts)
            if patterns is not None:
                group = patterns.setdefault(placement, {})
                group[pattern] = group.get(pattern, 0) + 1
            if pattern_kinds is not None:
                group = pattern_kinds.setdefault(placement, {}).setdefault(pattern, {})
                group[kind] = group.get(kind, 0) + 1
            if losses is not None and placement == "dropped":
                candidates = centers.get(parts[0], set())
                if parts[0] == ".agent-sessions" and len(parts) > 1:
                    candidates = centers.get(parts[1], set())
                if len(parts) > 2 and parts[:2] == (".platform", "owners"):
                    principal = (uid_owners or {}).get(parts[2])
                    candidates = {principal} if principal else set()
                label = labels[next(iter(candidates))] if len(candidates) == 1 else "unattributed"
                group = losses[label]["files"]
                group[pattern] = group.get(pattern, 0) + 1
                if loss_summary is not None:
                    cli = (len(parts) > 1 and parts[1] in _CREDENTIAL_ENTRIES) or (
                        len(parts) >= 5 and parts[:2] == (".platform", "owners")
                        and parts[3] == "agent-home") or parts[0] == ".executors" or (
                        parts[0] == ".platform" and len(parts) == 2
                        and parts[1].startswith("external-agent-refresh-"))
                    reason = "retired_cli_state" if cli else kind
                    description = "CLI login state" if cli else {
                        "unadmitted_center": "never-admitted center files",
                        "unknown_dir": "unknown directory files",
                    }.get(kind, "run-file uploads" if parts[0] == ".run-file-custody"
                          else "retired files")
                    if knowledge:
                        reason, description = legacy_reason or "no_owner_key", "knowledge stores"
                    if parts[0] == "lancedb":
                        reason = "unattributed_root_vectors"
                    _count_loss(loss_summary, label, reason, description, "files")
                    if knowledge:
                        group = loss_summary[label][reason][description]
                        group["bytes"] = group.get("bytes", 0) + path.lstat().st_size
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
    """Print the layout census; 10 requests the single final-shape cutover."""
    census = layout_census(data)
    layout = census["box_layout"]
    state = census["marker_state"]
    print(json.dumps(census, sort_keys=True))
    if layout == "absent":
        return ABSENT
    if state != "stable" or layout not in ("owner-split", "owner-box"):
        return UNREADABLE
    if layout == "owner-box":
        return 0 if _marker(data).get("owner_daemon") == 1 else UNREADABLE
    return PENDING


def _verified_archive(path: Path, *, fresh: bool = True) -> str:
    if not path.is_file() or path.is_symlink() or path.stat().st_size == 0:
        raise RuntimeError(f"missing complete snapshot {path}")
    if fresh and not 0 <= time.time() - path.stat().st_mtime <= MAX_BACKUP_AGE_S:
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
    return sha.hexdigest()


def _verified_pair(data_backup: Path, box_backup: Path, *, fresh: bool = True) -> list[str]:
    if not data_backup.name.startswith("tinyassets-snapshot-"):
        raise RuntimeError("data backup is not a full cutover snapshot")
    suffix = data_backup.name.removeprefix("tinyassets-snapshot-")
    if box_backup.name != "tinyassets-box-store-" + suffix:
        raise RuntimeError("box backup does not pair with data backup")
    return [_verified_archive(data_backup, fresh=fresh),
            _verified_archive(box_backup, fresh=fresh)]


def _owner_plan(data: Path) -> list[OwnerMove]:
    if not (data / ".tinyassets.db").is_file():
        raise FileNotFoundError(data / ".tinyassets.db")
    report = census(data)
    if not report["ready"]:
        raise RuntimeError(f"cutover census refused: {report['refusals']}")
    identity_db = data / ".broker" / "state" / "owner-identities.db"
    with readonly_db(identity_db) as db:
        identities = dict(db.execute(
            "SELECT principal, machine_id FROM owner_identities"
        ))
        admissions = db.execute(
            "SELECT event, principal, center, machine FROM center_admissions "
            "ORDER BY generation"
        ).fetchall()
        allowances = {}
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='box_records'").fetchone():
            allowances = {row[0]: row[1:] for row in db.execute(
                "SELECT principal,machine,project_id,bhard_bytes FROM box_records")}
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
        label = f"account-{sorted(identities).index(principal) + 1:04d}"
        # Runtime account_type_of reads the admitted box's broker plan. Cutover
        # instead converts the legacy tier already read by the read-only census.
        account = account_type(report["plans"][label]["tier"])
        quota = int(limits_for(account).storage_bytes)
        quota = (quota // 1024) * 1024
        if principal in allowances:
            machine, project, quota = allowances[principal]
            if (machine, project) != (uid, uid):
                raise RuntimeError("owner allowance does not match permanent identity")
        if type(quota) is not int or quota <= 0 or quota % 1024:
            raise RuntimeError("owner quota is not positive and KiB aligned")
        plan.append(OwnerMove(principal, uid, centers, quota))
    return plan


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
        for directory in (".agent-sessions", ".universe-sidecars"):
            sources.extend(data / directory / center for center in owner.centers
                           if (data / directory / center).exists())
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
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
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


def _copy(source: Path, target: Path, uid: int) -> None:
    command = [
        "rsync", "-aH", "--delete", "--delete-excluded", "--xattrs",
        "--one-file-system", "--numeric-ids",
        "--chown", f"{uid}:{uid}", f"{source}/", f"{target}/",
    ]
    command[-2:-2] = [f"--exclude=/{name}" for name in
                      (*_CREDENTIAL_ENTRIES, *_EPHEMERAL_ENTRIES)]
    subprocess.run(command, check=True)
    result = subprocess.run(
        [*command[:-2], "--checksum", "--dry-run", "--itemize-changes", *command[-2:]],
        check=True, capture_output=True, text=True,
    )
    if result.stdout.strip():
        raise RuntimeError(
            f"rsync verification differs for {source}: {result.stdout.strip()[:2000]}")


def _volumes(store: Path):
    """The one-shot migration alone receives an owner-content mount root (T4)."""
    from tinyassets.boxes.volumes import OwnerVolumes

    return OwnerVolumes(store, store.parent / (store.name + ".cutover-mounts"),
                        os.environ["BACKUP_DEST"])


@contextmanager
def _attached(volumes, plan, *, create=False):
    """Always release migration mounts, including a resumed interrupted attach."""
    with ExitStack() as stack:
        for owner in plan:
            stack.enter_context(volumes.operation(owner.uid))
            volumes.detach(owner.uid)
            if create:
                try:
                    volumes.create(owner.uid, owner.quota)
                except FileExistsError:
                    if volumes.size(owner.uid) != owner.quota:
                        raise RuntimeError("migration volume allowance changed") from None
            stack.callback(volumes.detach, owner.uid)
            volumes.attach(owner.uid)
        yield volumes.mount_root


def _owner_move_preconditions(data: Path, store: Path, marker: dict) -> list[OwnerMove]:
    """Read-only checks; all writers must be stopped before locked apply."""
    _mounted(store)
    if marker.get("box_layout", "owner-split") != "owner-split":
        raise RuntimeError("owner daemon migration requires owner-split")
    plan = _owner_plan(data)
    _owner_inode_preflight(data, plan)
    from tinyassets.cutover_deletions import collect

    legacy = collect(data, store_root=store)
    targets = {store / str(uid) for entry in legacy.entries() for uid in entry["uids"]}
    targets.update(store / target["quarantine"] for entry in legacy.entries()
                   for target in entry["work"].values())

    def known_legacy(path):
        if path in targets:
            return
        if path.is_dir() and not path.is_symlink() and any(
                target.is_relative_to(path) for target in targets):
            for child in path.iterdir():
                known_legacy(child)
            return
        raise RuntimeError("box store must be empty except recorded legacy deletion targets")

    for entry in store.iterdir():
        if entry.name != "lost+found":
            known_legacy(entry)
    report = census(data)
    if not report["ready"]:
        raise RuntimeError(f"cutover census refused: {report['refusals']}")
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


def _database_target(database: str, table: str, placement: str) -> str:
    if placement == "published":
        return ".published/" + database.removeprefix(".published/")
    if placement == "broker" and database == ".tinyassets.db":
        return ".broker/credential-custody.db"
    if database == "outbound.db":
        return ".broker/outbound.db"
    return database


def _build_databases(data: Path, store: Path, shared: Path, inventory: dict) -> dict:
    """Extract using one census snapshot; preserve DDL and every selected value."""
    jobs: dict[Path, dict] = {}
    expected = {}
    owners = inventory["owners"]
    for (database, table), rows in inventory["tables"].items():
        rule = _census_rule(database, table)
        if rule is None and (database, table) in RETIRED_STORES:
            continue  # dropped and counted by the census; no destination
        assert rule is not None
        targets = inventory["assignments"][database, table]
        destinations = {}
        if rule.placement in {"owner", "regenerated"}:
            destinations.update({("owner", owner): store / str(uid) / database
                                 for owner, uid in owners.items()})
        for placement in ("router", "broker", "published"):
            if rule.placement == placement or placement in targets:
                destinations[placement] = shared / _database_target(database, table, placement)
        for destination, path in destinations.items():
            selected = {index for index, target in enumerate(targets) if target == destination}
            if isinstance(destination, tuple):
                selected.update(index for (db, name, index), owner in
                                inventory["published_owner_copies"].items()
                                if (db, name, owner) == (database, table, destination[1]))
            if table in jobs.setdefault(path, {}):
                raise RuntimeError("multiple source tables map to one destination table")
            jobs[path][table] = (database, selected)
            expected[str(path)] = expected.get(str(path), 0) + len(selected)
        total = sum(target in destinations for target in targets)
        total += sum(target in {"dropped", "regenerated"} for target in targets)
        if total != len(rows):
            raise RuntimeError("cutover row reconciliation failed")
    for path, tables in jobs.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        # A killed SQLite writer can leave a hot journal beside the output.
        # Rebuild only our unpublished output generation, including sidecars.
        for suffix in ("", "-wal", "-shm", "-journal"):
            path.with_name(path.name + suffix).unlink(missing_ok=True)
        with closing(sqlite3.connect(path)) as destination, destination:
            destination.execute("PRAGMA synchronous=FULL")
            destination.execute("BEGIN IMMEDIATE")
            deferred = []
            for table, (database, rows) in tables.items():
                with readonly_db(data / database) as source:
                    ddl = source.execute("SELECT sql FROM sqlite_master WHERE type='table' "
                                         "AND name=?", (table,)).fetchone()[0]
                    legacy_tombstones = (database, table) == (
                        ".tinyassets.db", "deleted_principals")
                    if legacy_tombstones:
                        ddl = "CREATE TABLE deleted_principals (digest TEXT PRIMARY KEY)"
                    destination.execute(ddl)
                    columns = [row[1] for row in source.execute(
                        f"PRAGMA table_info({_quoted(table)})")]
                    # Generated columns are not returned by table_info; SQLite
                    # recomputes them from the verbatim stored input columns.
                    metadata = inventory["tables"][database, table]
                    source.row_factory = sqlite3.Row

                    def selected_rows():
                        seen = 0
                        for index, record in enumerate(source.execute(
                                f"SELECT * FROM {_quoted(table)} NOT INDEXED")):
                            if index >= len(metadata):
                                raise RuntimeError("source row count changed after census")
                            for key, value in metadata[index].items():
                                if key != "1" and record[key] != value:
                                    raise RuntimeError("source attribution changed after census")
                            seen += 1
                            if index in rows:
                                yield ((record["founder_sub"],) if legacy_tombstones
                                       else tuple(record[column] for column in columns))
                        if seen != len(metadata):
                            raise RuntimeError("source row count changed after census")

                    output_columns = ["digest"] if legacy_tombstones else columns
                    destination.executemany(
                        f"INSERT INTO {_quoted(table)} ({','.join(map(_quoted, output_columns))}) "
                        f"VALUES ({','.join('?' for _ in output_columns)})", selected_rows())
                    deferred.extend(row[0] for row in source.execute(
                        "SELECT sql FROM sqlite_master WHERE tbl_name=? AND type IN "
                        "('index','trigger') AND sql IS NOT NULL", (table,)))
                    count = destination.execute(
                        f"SELECT COUNT(*) FROM {_quoted(table)}").fetchone()[0]
                    if count != len(rows):
                        raise RuntimeError("cutover destination row count differs")
            for ddl in deferred:
                destination.execute(ddl)
        with closing(sqlite3.connect(path)) as check_db:
            from tinyassets.sqlite_integrity import check

            check(check_db)
    return expected


def _workflow_resolution(tables: dict) -> tuple[dict[str, int], int]:
    """The census and built tree use the same live-instance resolution predicate.

    Missing columns and snapshot forks without definitions are not instances.
    Automations, background bindings and queued tasks require definitions.
    Retirement follows the automation store's truthiness check.
    """
    definitions = {row.get("branch_def_id") for row in
                   tables.get((".tinyassets.db", "branch_definitions"), [])}
    counts = {"instances": 0, "resolved": 0}
    history = 0
    for key in WORKFLOW_INSTANCE_TABLES:
        for row in tables.get(key, []):
            if row.get("retired_at"):
                history += int(key == (".automations.db", "automations"))
                continue
            definition = row.get("branch_def_id")
            if "branch_def_id" not in row or (
                    not definition and key == (".tinyassets.db", "branches")):
                continue
            counts["instances"] += 1
            counts["resolved"] += int(bool(definition) and definition in definitions)
    return counts, history


def _verify_workflow_definitions(tree: Path) -> dict[str, int]:
    """Refuse any live instance without a definition in the built owner's box."""
    tables = {}
    for database, table in (*WORKFLOW_INSTANCE_TABLES,
                            (".tinyassets.db", "branch_definitions")):
        path = tree / database
        if not path.exists():
            continue
        with readonly_db(path) as db:
            columns = {row[1] for row in db.execute(f"PRAGMA table_info({_quoted(table)})")}
            if "branch_def_id" not in columns:
                continue
            selected = sorted(columns & {"branch_def_id", "retired_at"})
            db.row_factory = sqlite3.Row
            tables[database, table] = [dict(row) for row in db.execute(
                f"SELECT {','.join(map(_quoted, selected))} FROM {_quoted(table)}")]
    counts, _ = _workflow_resolution(tables)
    if counts["instances"] != counts["resolved"]:
        raise RuntimeError("migrated workflow instance has no local definition")
    return counts


def _seed_shared(data: Path, shared: Path, inventory: dict, report: dict, plan) -> None:
    from tinyassets.broker.owner_reads import Plans
    plans = Plans(shared)
    plans.initialize()
    for owner in plan:
        tier = report["plans"][inventory["labels"][owner.principal]]["tier"]
        record = dict(inventory["billing"][owner.principal])
        attempt = record.pop("attempt")
        settlements = record.pop("settlements")
        if tier == "paid" and not (record["customer_id"] and record["subscription_id"]):
            raise RuntimeError("paid subscription mapping missing at seed")
        plans.set_plan(owner.principal, **record)
        for settlement in settlements:
            plans.retain_checkout(owner.principal, settlement)
        with closing(plans._connect()) as db, db:
            db.execute("UPDATE plans SET attempt=? WHERE owner=?",
                       (json.dumps(attempt) if attempt else None, owner.principal))
        if plans.read(owner.principal)["tier"] != tier:
            raise RuntimeError("seeded plan differs from census")
    identity = shared / ".broker/state/owner-identities.db"
    from tinyassets.broker.owner_identities import OwnerIdentities

    # Complete the final broker schema before recording restore authority.
    # Startup must not be the first writer of secret/revocation tables or the
    # permanent-identity triggers on an older owner-split source schema.
    identity.parent.chmod(0o700)
    identity.chmod(0o600)
    OwnerIdentities(identity, plans=plans)
    with closing(sqlite3.connect(identity)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS box_records (principal TEXT PRIMARY KEY, "
                   "machine INTEGER UNIQUE NOT NULL, project_id INTEGER UNIQUE NOT NULL, "
                   "bhard_bytes INTEGER NOT NULL)")
        for owner in plan:
            db.execute("INSERT OR IGNORE INTO box_records VALUES (?,?,?,?)",
                       (owner.principal, owner.uid, owner.uid, owner.quota))
            from tinyassets.broker.owner_volumes import provision

            provision(db, owner.principal, owner.uid)
    from types import SimpleNamespace

    from tinyassets.broker.next_wake import NextWakeIndex

    wake = NextWakeIndex(SimpleNamespace(path=identity))
    with closing(sqlite3.connect(wake.path)) as db, db:
        db.execute("DELETE FROM next_wakes")
    hook_path = shared / ".hook-owners.sqlite3"
    with closing(sqlite3.connect(hook_path)) as db, db:
        db.execute("CREATE TABLE IF NOT EXISTS hook_owners ("
                   "token_hash TEXT PRIMARY KEY, owner TEXT NOT NULL)")
        for row, owner in zip(inventory["tables"].get((".tinyassets.db", "webhook_hooks"), []),
                              inventory["assignments"].get((".tinyassets.db", "webhook_hooks"), []),
                              strict=True):
            if isinstance(owner, tuple) and row.get("revoked_at") is None:
                owner = owner[1]
                previous = db.execute("SELECT owner FROM hook_owners WHERE token_hash=?",
                                      (row["token_hash"],)).fetchone()
                if previous and previous != (owner,):
                    raise RuntimeError("hook digest has conflicting owners")
                db.execute("INSERT OR IGNORE INTO hook_owners VALUES (?,?)",
                           (row["token_hash"], owner))
    # C11: retained accounting already records its owner. Remove the obsolete
    # center key in the existing staging transaction; no custody is carried.
    from tinyassets.storage.agent_request_usage import _SCHEMA

    outbound = shared / ".broker/outbound.db"
    with closing(sqlite3.connect(outbound)) as db, db:
        db.execute("BEGIN IMMEDIATE")
        for table in ("agent_request_usage", "agent_request_attempts",
                      "agent_request_usage_links", "agent_request_dispatches",
                      "agent_model_launches"):
            columns = [row[1] for row in db.execute(f"PRAGMA table_info({table})")]
            if "universe" not in columns:
                continue
            columns.remove("universe")
            selected = ",".join(map(_quoted, columns))
            rows = db.execute(f"SELECT {selected} FROM {table}").fetchall()
            db.execute(f"DROP TABLE {table}")
            for statement in _SCHEMA:
                db.execute(statement)
            placeholders = ",".join("?" for _ in columns)
            db.executemany(f"INSERT INTO {table} ({selected}) VALUES ({placeholders})", rows)

    # C11: policy is already owner-keyed. Only center entries are discarded.
    from tinyassets.broker.policy import _canonical, _digest

    for owner in plan:
        account = hashlib.sha256(owner.principal.encode()).hexdigest()
        path = shared / ".broker/accounts" / account / "policy.db"
        if not path.exists():
            continue
        with closing(sqlite3.connect(path)) as db, db:
            db.execute("BEGIN IMMEDIATE")
            generation, old_digest, raw = db.execute(
                "SELECT generation,digest,document FROM policy WHERE singleton=1").fetchone()
            document = json.loads(raw)
            if _digest(document) != old_digest:
                raise RuntimeError("policy digest mismatch")
            document["entries"] = [entry for entry in document["entries"]
                                   if entry["scope"] == "account"]
            digest = _digest(document)
            db.execute("UPDATE policy SET generation=?,digest=?,document=? WHERE singleton=1",
                       (generation + 1, digest, _canonical(document)))
            db.execute("INSERT INTO receipts(at,kind,owner,old_digest,new_digest,"
                       "generation,outcome) VALUES (?,?,?,?,?,?,?)",
                       (time.time(), "migration", owner.principal, old_digest, digest,
                        generation + 1, "committed"))


def _durable_tree(root: Path, *, exclude=()) -> dict[str, str]:
    """Persist files and directory entries before publishing a resume boundary."""
    manifest = {}
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in files + [name for name in dirs if (Path(directory) / name).is_symlink()]:
            path = Path(directory) / name
            key = path.relative_to(root).as_posix()
            if key in exclude:
                continue
            if path.is_symlink():
                manifest[key] = "link:" + os.readlink(path)
            else:
                with path.open("rb") as stream:
                    manifest[key] = hashlib.file_digest(stream, "sha256").hexdigest()
                    os.fsync(stream.fileno())
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    return manifest


def cutover_identity_manifest(root: Path) -> str:
    """Bind all identity authority, permitting only ephemeral ingress secrets.

    Hash schema and rows rather than SQLite pages: a secret rotation can change
    page layout. No identities or secret material appear in the receipt.
    """
    evidence = []
    with readonly_db(root / ".broker/state/owner-identities.db") as db:
        for kind, name, ddl in db.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ):
            evidence.append((kind, name, ddl))
            if kind == "table" and name not in {"owner_box_secrets", "routing_sequence"}:
                rows = [json.dumps(list(row), sort_keys=True) for row in db.execute(
                    f"SELECT * FROM {_quoted(name)}")]
                evidence.append((name, sorted(rows)))
    return hashlib.sha256(json.dumps(evidence, sort_keys=True).encode()).hexdigest()


def _remove_tree(path: Path, root: Path) -> None:
    # Never traverse a link or accept a computed path outside this transaction.
    if path.is_symlink() or not path.resolve().is_relative_to(root.resolve()) or path == root:
        raise RuntimeError("unsafe cutover cleanup path")
    if path.is_dir():
        shutil.rmtree(path)


def _crash_at(boundary: str) -> None:
    """Synthetic rehearsal only; the deploy runner explicitly clears this input."""
    if os.environ.get("CUTOVER_CRASH_AT") == boundary:
        os._exit(137)


def _split_histories(data, store, inventory):
    """Rebuild unpublished files on resume, then read back every entry count."""
    for name, history in inventory["histories"].items():
        path = data / name
        if hashlib.sha256(path.read_bytes()).hexdigest() != history["sha256"]:
            raise RuntimeError("history source changed after census")
        entries = _history_entries(path)
        if len(entries) != len(history["targets"]):
            raise RuntimeError("history source count changed after census")
        kept = 0
        for owner, uid in inventory["owners"].items():
            selected = [value for (_, value), target in zip(
                            entries, history["targets"], strict=True)
                        if target == owner]
            destination = store / str(uid) / name
            if name == ".scheduler_fires.jsonl":
                destination.write_text("".join(line + "\n" for line in selected), encoding="utf-8")
            else:
                destination.write_text(json.dumps(selected) + "\n", encoding="utf-8")
            actual = _history_entries(destination)
            expected = history["report"]["per_owner"][inventory["labels"][owner]]
            if len(actual) != expected or [value for _, value in actual] != selected:
                raise RuntimeError("history destination verification failed")
            kept += len(actual)
        if kept + history["report"]["dropped"] != history["report"]["total"]:
            raise RuntimeError("history split count reconciliation failed")
        _crash_at("after_scheduled_history_split" if name == ".scheduler_fires.jsonl"
                  else "after_activity_ledger_split")


def _claim_sqlite_sidecars(path: Path) -> None:
    """Stopped apply preserves WAL bytes while taking custody of recovery files."""
    with ExitStack() as opened:
        descriptors = []
        for suffix in ("-wal", "-shm"):
            try:
                fd = os.open(path.with_name(path.name + suffix),
                             os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
            except FileNotFoundError:
                continue
            opened.callback(os.close, fd)
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise RuntimeError("SQLite sidecar is not a single-link regular file")
            descriptors.append(fd)
        # SQLite still uses O_CREAT for sidecars even with main-database mode=rw.
        # Validate both before changing either; never truncate committed WAL or
        # change ownership through an alias outside the stopped source tree.
        for fd in descriptors:
            os.fchown(fd, 0, 0)


def _build_cutover(data: Path, store: Path, journal: dict) -> None:
    inventory = {}
    report = census(data, _inventory=inventory, _source_marker=journal["source_marker"])
    if not report["ready"]:
        raise RuntimeError(f"cutover census refused: {report['refusals']}")
    # Census repaired and exhaustively verified private copies; never repair
    # the stopped source. Database construction repeats that verdict on its copy.
    repairs = set(journal.get("reindexed", []))
    repairs.update(item.replace("repairable index: ", "reindexed: ")
                   for item in report["repairable_indexes"])
    journal["reindexed"] = report["reindexed"] = sorted(repairs)
    _atomic_json(data / ".owner-cutover.json", journal)
    plan = [OwnerMove(**{**owner, "centers": tuple(owner["centers"])}) for owner in journal["plan"]]
    shared = store / ".cutover-shared"
    _remove_tree(shared, store)
    shared.mkdir(mode=0o700)
    volumes = _volumes(store)
    with _attached(volumes, plan, create=True) as mounts:
        for index, owner in enumerate(plan, 1):
            tree = mounts / str(owner.uid)
            tree.mkdir(mode=0o700, exist_ok=True)
            for center in owner.centers:
                for relative in (center, f".agent-sessions/{center}",
                                 f".universe-sidecars/{center}"):
                    source = data / relative
                    if source.exists():
                        target = tree / relative
                        target.mkdir(parents=True, exist_ok=True)
                        _copy(source, target, owner.uid)
            _crash_at(f"after_owner_tree:{index}")
        for relative, target in inventory["file_targets"].items():
            if isinstance(target, tuple):
                parts = PurePosixPath(relative).parts
                if (parts[0] in inventory["centers"] or (
                    len(parts) > 1 and parts[0] in {".agent-sessions", ".universe-sidecars"}
                    and parts[1] in inventory["centers"]
                )):
                    continue  # Already copied and checksum-verified by the tree rsync.
                source = data / relative
                destination = mounts / str(inventory["owners"][target[1]]) / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                if not (source.is_file() or source.is_symlink()):
                    raise RuntimeError("unsupported run transcript inode")
                shutil.copy2(source, destination, follow_symlinks=False)
                if (source.is_symlink() and os.readlink(source) != os.readlink(destination)):
                    raise RuntimeError("run transcript symlink copy differs")
                if not source.is_symlink():
                    with source.open("rb") as original, destination.open("rb") as copied:
                        if hashlib.file_digest(original, "sha256").digest() != hashlib.file_digest(
                                copied, "sha256").digest():
                            raise RuntimeError("run transcript copy differs")
            elif target in {"dropped", "regenerated"}:
                # A center rsync can have included a classified cache/index. Match
                # the census disposition in every output, not just in its report.
                for owner in plan:
                    destination = mounts / str(owner.uid) / relative
                    if destination.is_file() or destination.is_symlink():
                        destination.unlink()
        _build_databases(data, mounts, shared, inventory)
        _split_histories(data, mounts, inventory)
        # File-only shared state stays in its authority's custody and is included
        # in the same verified publication as the extracted databases.
        for relative, target in inventory["file_targets"].items():
            if target not in {"broker", "router"} or relative in {
                    ".layout.json", ".layout.lock", ".owner-cutover.json"}:
                continue
            if PurePosixPath(relative).parts[0] in inventory["centers"]:
                continue  # Center-local subscription metadata is seeded as plans.
            source = data / relative
            destination = shared / relative
            if not destination.exists():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, destination)
        _seed_shared(data, shared, inventory, report, plan)
        from tinyassets.cutover_deletions import collect

        legacy = collect(data, store_root=store)
        _reserve_erasures(legacy, shared)
        _seed_owner_admission(shared, legacy)
        _crash_at("after_seeding")
        from tinyassets.boxes.layout import BROKER_READ_GID, BROKER_UID, DAEMON_UID
        from tinyassets.boxes.state import BoxHostState

        owner_manifests = {}
        workflow_counts = {}
        from tinyassets.cutover_sqlite import _repair_staged_databases

        for owner in plan:
            tree = mounts / str(owner.uid)
            from tinyassets.owner_centers import Centers

            centers = Centers(tree)
            for center in owner.centers:
                centers.create(center)
            _repair_staged_databases(tree)
            state = BoxHostState(tree / ".owner-boxd/boxhost.db")
            state.epoch(owner.principal)
            state.close()
            for directory, dirs, files in os.walk(tree, followlinks=False):
                for path in (Path(directory), *(Path(directory) / name for name in dirs + files)):
                    mode = stat.S_IMODE(path.lstat().st_mode)
                    os.chown(path, owner.uid, owner.uid, follow_symlinks=False)
                    if not path.is_symlink():
                        os.chmod(path, mode)
            workflow_counts[inventory["labels"][owner.principal]] = (
                _verify_workflow_definitions(tree))
            owner_manifests[str(owner.uid)] = _durable_tree(tree)
    # The detached image, not a shared directory archive, is the recovery unit.
    from tinyassets.broker import owner_volumes
    from tinyassets.broker.owner_identities import OwnerIdentities

    identities = OwnerIdentities(shared / ".broker/state/owner-identities.db")
    snapshots = journal.setdefault("snapshots", {})
    for owner in plan:
        with volumes.operation(owner.uid):
            snapshot_id = snapshots.setdefault(str(owner.uid), uuid.uuid4().hex)
            _atomic_json(data / ".owner-cutover.json", journal)
            with closing(sqlite3.connect(identities.path)) as db, db:
                db.execute("UPDATE owner_volumes SET work=? WHERE principal=?",
                           (json.dumps({"kind": "snapshot", "snapshot_id": snapshot_id}),
                            owner.principal))
            snapshot_id, digest = volumes.snapshot(owner.uid, snapshot_id)
            owner_volumes.dispatch(identities, {
                "op": "BOX_VOLUME", "principal": owner.principal, "action": "publish",
                "arguments": {"snapshot_id": snapshot_id, "sha256": digest}})
            _crash_at(f"after_owner_snapshot:{owner.uid}")
    from tinyassets.broker.deletion_jobs import JobRows
    from tinyassets.broker.deletion_records import RemoteRecords
    from tinyassets.cutover_deletions import NAME, finish

    remote_jobs = RemoteRecords(volumes.remote)
    job_rows = JobRows(identities, remote_jobs)
    for job in remote_jobs.records():
        job_rows.save(job)
    report["legacy_deletion_receipts"] = finish(legacy, shared, store, job_rows)
    # C6: legacy authorities have been converted and their jobs completed.
    for suffix in ("", "-wal", "-shm", "-journal"):
        (shared / (NAME + suffix)).unlink(missing_ok=True)
    (shared / ".broker/credential-custody.db").unlink(missing_ok=True)
    for marker in (shared / ".broker/accounts").glob("*/.deleted"):
        marker.unlink()
    for directory, dirs, files in os.walk(shared, followlinks=False):
        for path in (Path(directory), *(Path(directory) / name for name in dirs + files)):
            relative = path.relative_to(shared).parts
            uid = (BROKER_UID if relative and relative[0] in
                   {".broker", ".platform", ".published"} else DAEMON_UID)
            if relative == (".platform",):
                uid = DAEMON_UID
            gid = BROKER_READ_GID if relative and relative[0] == ".published" else uid
            os.chown(path, uid, gid)
            private = relative and relative[0] in {".broker", ".platform", ".connect"}
            mode = 0o700 if private else 0o750
            if relative == (".platform",):
                mode = 0o711
            os.chmod(path, mode if path.is_dir() else (0o600 if private else 0o640))
    from tinyassets.cutover_control import protect_root

    protect_root(shared)
    # C4: deployment owns admission. Publish after service-custody assignment
    # so neither the router nor broker can reopen the closed cutover gate.
    from tinyassets.cutover_control import close_admission

    close_admission(shared)
    _repair_staged_databases(shared)
    report["workflow_definitions"] = workflow_counts
    journal.update(phase="publish", report=report, shared=_durable_tree(shared),
                   owner_manifests=owner_manifests,
                   databases=sorted({database for database, _ in inventory["tables"]}),
                   centers=sorted(set(inventory["centers"]) | inventory["dropped_centers"]),
                   legacy_runs=any(PurePosixPath(path).parts[0] == "runs"
                                   for path in inventory["file_targets"]),
                   discarded_files=[path for path, target in inventory["file_targets"].items()
                                    if target in {"dropped", "regenerated", "split"}
                                    or isinstance(target, tuple)],
                   cli_homes=[p.relative_to(data).as_posix()
                              for p in (data / ".platform/owners").glob("*/agent-home")
                              if p.parent.name.isdecimal()])
    _atomic_json(data / ".owner-cutover.json", journal)


def _publish_cutover(data: Path, store: Path, journal: dict) -> None:
    from tinyassets.cutover_control import protect_root

    shared = store / ".cutover-shared"
    protect_root(data)
    if _durable_tree(shared) != journal["shared"]:
        raise RuntimeError("staged shared manifest changed")
    _verify_owner_volumes(store, journal)
    for directory, dirs, _ in os.walk(shared):
        for name in dirs:
            original = Path(directory) / name
            destination = data / original.relative_to(shared)
            destination.mkdir(parents=True, exist_ok=True)
            os.chown(destination, original.stat().st_uid, original.stat().st_gid)
            os.chmod(destination, stat.S_IMODE(original.stat().st_mode))
    for relative, digest in journal["shared"].items():
        source, target = shared / relative, data / relative
        # Copy to a destination-filesystem temporary then replace, so the
        # committed stage survives a crash and cross-device moves are atomic.
        target.parent.mkdir(parents=True, exist_ok=True)
        temporary = target.with_name(target.name + ".cutover")
        shutil.copy2(source, temporary)
        shutil.chown(temporary, user=source.stat().st_uid, group=source.stat().st_gid)
        with temporary.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                raise RuntimeError("staged shared file verification failed")
            os.fsync(stream.fileno())
        for suffix in ("-wal", "-shm"):
            target.with_name(target.name + suffix).unlink(missing_ok=True)
        os.replace(temporary, target)
    for database in journal["databases"]:
        if database not in journal["shared"]:
            for suffix in ("", "-wal", "-shm"):
                (data / (database + suffix)).unlink(missing_ok=True)
    for center in journal["centers"]:
        for relative in (center, f".agent-sessions/{center}", f".universe-sidecars/{center}"):
            _remove_tree(data / relative, data)
    if journal["legacy_runs"]:
        _remove_tree(data / "runs", data)
    for relative in journal["cli_homes"]:
        _remove_tree(data / relative, data)
    for relative in journal["discarded_files"]:
        path = data / relative
        if not path.parent.resolve().is_relative_to(data.resolve()):
            raise RuntimeError("unsafe discarded cutover path")
        path.unlink(missing_ok=True)
    (data / ".tinyassets.db.prebudgetrepair.bak").unlink(missing_ok=True)
    _remove_tree(data / ".platform/credential-vaults", data)
    _remove_tree(data / ".boxhost", data)
    _durable_tree(data)
    _atomic_json(storage_layout.marker_path(data), {
        **journal["source_marker"], "box_layout": "owner-box", "state": "stable",
        "owner_daemon": 1,
    })
    journal["phase"] = "complete"
    journal["identity_manifest"] = cutover_identity_manifest(data)
    journal["final_manifest"] = _durable_tree(data, exclude=(".owner-cutover.json",))
    _atomic_json(data / ".owner-cutover.json", journal)
    _remove_tree(shared, store)


def _recover_atomic_files(data: Path) -> None:
    """Remove only our interrupted atomic writes, under the exclusive lock."""
    for path in data.iterdir():
        match = re.fullmatch(r"(\.(?:layout|owner-cutover)\.json)\.[0-9]+\.tmp", path.name)
        if not match:
            continue
        info = path.lstat()
        original = data / match[1]
        allowed_uid = original.stat().st_uid if original.is_file() else 0
        if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
                or info.st_uid not in {0, allowed_uid}):
            raise RuntimeError("unsafe interrupted cutover atomic write")
        path.unlink()


def apply(data: Path, store: Path, data_backup: Path, box_backup: Path) -> None:
    """Copy/verify/publish with a durable boundary before source removal."""
    import fcntl
    from dataclasses import asdict

    if os.geteuid() != 0:
        raise PermissionError("box layout apply requires the one-shot root container")
    lock = _open_layout_lock(data, os.O_RDWR)
    try:
        fcntl.flock(lock, fcntl.LOCK_EX)
        _recover_atomic_files(data)
        marker = storage_layout.read_marker(data)
        if not isinstance(marker, dict):
            raise RuntimeError("cutover requires a readable layout marker")
        journal_path = data / ".owner-cutover.json"
        if marker.get("box_layout") == "owner-box" and marker.get("owner_daemon") == 1:
            if journal_path.exists():
                journal = json.loads(journal_path.read_text())
                if journal.get("store") != str(store.resolve()):
                    raise RuntimeError("cutover store changed after publication")
                if journal.get("phase") != "complete":
                    if _durable_tree(store / ".cutover-shared") != journal["shared"]:
                        raise RuntimeError("staged shared manifest changed")
                    _verify_owner_volumes(store, journal)
                    journal["phase"] = "complete"
                    journal["identity_manifest"] = cutover_identity_manifest(data)
                    journal["final_manifest"] = _durable_tree(
                        data, exclude=(".owner-cutover.json",))
                    _atomic_json(journal_path, journal)
                _remove_tree(store / ".cutover-shared", store)
            return
        if journal_path.exists():
            backup_digests = _verified_pair(data_backup, box_backup, fresh=False)
        else:
            backup_digests = _verified_pair(data_backup, box_backup)
        if journal_path.exists():
            journal = json.loads(journal_path.read_text())
            if (journal.get("version") != 1 or journal.get("phase") not in {"build", "publish"}
                    or journal.get("store") != str(store.resolve())
                    or journal.get("backups") != [data_backup.name, box_backup.name]
                    or journal.get("backup_digests") != backup_digests):
                raise RuntimeError("unrecognized cutover resume journal")
            _mounted(store)
        else:
            plan = _owner_move_preconditions(data, store, _marker(data))
            journal = {"version": 1, "phase": "build", "source_marker": marker,
                       "store": str(store.resolve()), "plan": [asdict(owner) for owner in plan],
                       "backups": [data_backup.name, box_backup.name],
                       "backup_digests": backup_digests,
                       "source_manifest": _durable_tree(
                           data, exclude=(".tinyassets.db.prebudgetrepair.bak",))}
            _atomic_json(journal_path, journal)
        _atomic_json(storage_layout.marker_path(data), {
            **journal["source_marker"], "state": "migrating", "migration": OWNER_BOX_MIGRATION,
        })
        if journal["phase"] == "build":
            _build_cutover(data, store, journal)
        _publish_cutover(data, store, journal)
    finally:
        os.close(lock)


def verify(data: Path, store: Path) -> None:
    """Read back the committed transaction, including its final marker and quotas."""
    marker = _marker(data)
    journal = json.loads((data / ".owner-cutover.json").read_text())
    if (marker.get("owner_daemon") != 1 or marker.get("box_layout") != "owner-box"
            or marker.get("state") != "stable" or journal.get("phase") != "complete"):
        raise RuntimeError("owner cutover is not complete")
    if _durable_tree(data, exclude=(".owner-cutover.json",)) != journal["final_manifest"]:
        raise RuntimeError("committed shared manifest changed")
    _verify_owner_volumes(store, journal)


def _verify_owner_volumes(store, journal):
    """Migration verification only; runtime validates inside the owner boundary."""
    plan = [OwnerMove(**owner) for owner in journal["plan"]]
    volumes = _volumes(store)
    with _attached(volumes, plan) as mounts:
        for owner in plan:
            tree = mounts / str(owner.uid)
            if _durable_tree(tree) != journal["owner_manifests"][str(owner.uid)]:
                raise RuntimeError("committed owner manifest changed")
            _verify_workflow_definitions(tree)
            if volumes.size(owner.uid) != owner.quota:
                raise RuntimeError("committed owner volume allowance changed")


def _reserve_erasures(ledger, staged):
    """An erased permanent UID can never be reassigned to a surviving owner."""
    from tinyassets.restore_recovery import _safe

    entries = [entry for entry in ledger.entries() if entry["uids"]]
    if not entries:
        return
    identity = _safe(staged, ".broker/state/owner-identities.db")
    with closing(sqlite3.connect(identity)) as db, db:
        for entry in entries:
            owner, uids = entry["principal"], entry["uids"]
            if not owner or len(uids) != 1:
                raise ValueError("ambiguous erased UID reservation")
            rows = db.execute("SELECT principal,machine_id FROM owner_identities "
                              "WHERE principal=? OR machine_id=?", (owner, uids[0])).fetchall()
            if any(row != (owner, uids[0]) for row in rows):
                raise ValueError("erased UID conflicts with permanent reservation")
            if not rows:
                db.execute("INSERT INTO owner_identities (principal,machine_id) VALUES (?,?)",
                           (owner, uids[0]))




def _seed_owner_admission(shared, ledger):
    """C6 one-shot conversion; old authorities never participate in runtime reads."""
    from tinyassets.broker.admission import digest as principal_digest
    from tinyassets.broker.admission import mutate, schema

    identity = shared / ".broker/state/owner-identities.db"
    with closing(sqlite3.connect(identity)) as db, db:
        db.execute("ATTACH DATABASE ? AS billing", (str(shared / ".broker/plans.db"),))
        db.execute("BEGIN IMMEDIATE")
        schema(db)
        erased = {entry["digest"] for entry in ledger.entries()}
        erased.update(row[0] for row in db.execute(
            "SELECT digest FROM owner_admission WHERE state='erased'"))
        if db.execute("SELECT 1 FROM sqlite_master WHERE name='owner_box_revocations'").fetchone():
            erased.update(principal_digest(row[0]) for row in db.execute(
                "SELECT principal FROM owner_box_revocations WHERE deleted=1"))
        columns = {row[1] for row in db.execute("PRAGMA billing.table_info(plans)")}
        flags = [name if name in columns else "0" for name in
                 ("billing_suspended", "abuse_suspended")]
        for owner, in db.execute("SELECT principal FROM owner_identities").fetchall():
            if principal_digest(owner) in erased:
                continue
            box = db.execute("SELECT 1 FROM box_records WHERE principal=?", (owner,)).fetchone()
            plan = db.execute(f"SELECT {','.join(flags)} FROM billing.plans WHERE owner=?",
                              (owner,)).fetchone()
            if box is None or plan is None:
                continue
            mutate(db, owner, "admit")
            import secrets

            db.execute("INSERT OR IGNORE INTO owner_box_secrets VALUES (?,?)",
                       (owner, secrets.token_urlsafe(32)))
            for reason, suspended in zip(("billing", "abuse"), plan, strict=True):
                mutate(db, owner, "suspend", reason=reason, suspended=bool(suspended))
        for key in erased:
            mutate(db, None, "erase", erased_digest=key)
        db.execute("DROP TABLE IF EXISTS owner_box_revocations")
        for name in ("billing_suspended", "abuse_suspended"):
            if name in columns:
                db.execute(f"ALTER TABLE billing.plans DROP COLUMN {name}")



def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=("check", "preflight", "apply", "census", "verify"))
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
        if args.mode == "verify":
            if args.store is None:
                parser.error("verify requires store")
            verify(args.data, args.store)
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
