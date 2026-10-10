"""Which stores an execution owner writes, and which are fenced yet.

Change ``execution-owner-lease`` D3: every owner-written store must commit only
under its owner's fence, or a stale owner can still write it. Slice B1 fences the
agent turn journal (the turn rows reconciliation keys on); every other writer is
listed here as owed. A second owner process can only exist once slice C2 ships
the handover, and C2 must not ship while anything here is owed:
``tests/test_owner_stores.py`` fails when :data:`HANDOVER_ENABLED` is set with a
non-empty :data:`FENCE_BEFORE_C2`, and fails on any new SQLite writer that is in
none of these sets.
"""

from __future__ import annotations

#: Writers that commit only under :func:`tinyassets.storage.owner_fence.check_fence`,
#: mapped to their store kind (``tinyassets.owner_lease.STORE_ENUMERATORS``).
FENCED: dict[str, str] = {
    "tinyassets/agent_loop/box_ta.py": "remote_ta_receipts",
    "tinyassets/storage/agent_turn_journal.py": "agent_turn_journal",
}

#: The lease and fence machinery itself: it writes the lease store and fence
#: rows, which is what everything else is fenced BY.
INFRASTRUCTURE: frozenset[str] = frozenset({
    "tinyassets/owner_lease.py",
    "tinyassets/storage/owner_fence.py",
})

#: Owner-written stores not fenced yet. Safe while exactly one owner tree runs
#: (today's deploy shape); each must move to :data:`FENCED` before C2.
FENCE_BEFORE_C2: frozenset[str] = frozenset({
    "tinyassets/account_deletion.py",
    "tinyassets/agent_activities.py",
    "tinyassets/agent_activity.py",
    "tinyassets/agent_interchange.py",
    "tinyassets/agent_review.py",
    "tinyassets/agent_rules.py",
    "tinyassets/agent_steering.py",
    "tinyassets/api/command_center_updates.py",
    "tinyassets/api/market.py",
    "tinyassets/api/publish_requests.py",
    "tinyassets/approval_scopes.py",
    "tinyassets/auth/provider.py",
    "tinyassets/outside_authority.py",
    "tinyassets/extension_state.py",
    "tinyassets/extension_hooks.py",
    "tinyassets/authoring/store.py",
    "tinyassets/automations.py",
    "tinyassets/bound_requests.py",
    "tinyassets/boxes/state.py",
    "tinyassets/branch_versions.py",
    "tinyassets/broker/account_erasure.py",
    "tinyassets/broker/browser_vault.py",
    "tinyassets/broker/disconnect.py",
    "tinyassets/broker/ops.py",
    "tinyassets/broker/owner_identities.py",
    "tinyassets/catalog/backend.py",
    "tinyassets/checkpointing/sqlite_saver.py",
    "tinyassets/command_center_agent_templates.py",
    "tinyassets/command_center_packages.py",
    "tinyassets/command_center_release_series.py",
    "tinyassets/command_center_update_executor.py",
    "tinyassets/command_center_update_policy.py",
    # Shares custom_agents' store; author checks are not owner-generation fences.
    "tinyassets/commons_bundles.py",
    "tinyassets/conformance_packs.py",
    "tinyassets/connection_continuations.py",
    "tinyassets/connection_oauth/flow.py",
    "tinyassets/connection_oauth/pkce.py",
    "tinyassets/context/compaction.py",
    "tinyassets/control_plane/triggers.py",
    "tinyassets/conversation_store.py",
    "tinyassets/credential_vault.py",
    "tinyassets/custom_agents.py",
    "tinyassets/daemon_brain.py",
    "tinyassets/daemon_server.py",
    "tinyassets/effectors/wiki_write_back.py",
    "tinyassets/engine_admissions.py",
    "tinyassets/gate_events/store.py",
    "tinyassets/gates/actions.py",
    "tinyassets/handoffs/store.py",
    "tinyassets/harness_history.py",
    "tinyassets/idempotency.py",
    "tinyassets/knowledge/knowledge_graph.py",
    "tinyassets/memory/episodic.py",
    "tinyassets/memory/project.py",
    "tinyassets/memory/promises.py",
    "tinyassets/memory/temporal.py",
    "tinyassets/memory/versioning.py",
    "tinyassets/node_eval.py",
    "tinyassets/onboarding/hosted_model_auth.py",
    "tinyassets/onboarding/inline_model_connect.py",
    "tinyassets/onboarding/approval_handoff.py",
    "tinyassets/onboarding/owner_sessions.py",
    "tinyassets/onboarding/native_sign_in.py",
    "tinyassets/outcomes/schema.py",
    "tinyassets/payments/actions.py",
    "tinyassets/payments/escrow.py",
    "tinyassets/payments/funding.py",
    "tinyassets/payments/schema.py",
    "tinyassets/payments/settlement_ledger.py",
    "tinyassets/payments/wallets.py",
    "tinyassets/provider_assignment.py",
    "tinyassets/provider_assignment_manifest.py",
    "tinyassets/providers/connection_lifecycle.py",
    "tinyassets/request_continuations.py",
    "tinyassets/request_answers.py",
    "tinyassets/reset.py",
    "tinyassets/run_admission_envelope.py",
    "tinyassets/runs.py",
    "tinyassets/scheduler.py",
    "tinyassets/scoped_reset.py",
    "tinyassets/starter_seeds.py",
    "tinyassets/storage/account_timezone.py",
    "tinyassets/storage/accounts.py",
    "tinyassets/storage/action_result_outbox.py",
    "tinyassets/storage/agent_request_usage.py",
    "tinyassets/storage/assigned_queue_refusals.py",
    "tinyassets/storage/automation_activations.py",
    "tinyassets/storage/background_branch_authority.py",
    "tinyassets/storage/cloud_automation_continuation.py",
    "tinyassets/storage/cloud_automation_control.py",
    "tinyassets/storage/conversation_custody.py",
    "tinyassets/storage/conversation_run_admissions.py",
    "tinyassets/storage/deliveries.py",
    "tinyassets/storage/effector_consents.py",
    "tinyassets/storage/external_write_receipts.py",
    "tinyassets/storage/ingress_journal.py",
    "tinyassets/storage/model_preferences.py",
    "tinyassets/storage/outbound_connections.py",
    "tinyassets/storage/owner_devices.py",
    "tinyassets/storage/owner_ui_prefs.py",
    "tinyassets/storage/pending_requests.py",
    "tinyassets/storage/provider_work_authority.py",
    "tinyassets/storage/receiver_links.py",
    "tinyassets/storage/refused_models.py",
    "tinyassets/storage/request_admissions.py",
    "tinyassets/storage/request_migration.py",
    "tinyassets/storage/run_files.py",
    "tinyassets/storage/run_input_admissions.py",
    "tinyassets/storage/subscription_state.py",
    "tinyassets/storage/webhook_hooks.py",
    "tinyassets/storage_accounting.py",
    "tinyassets/universe_owner.py",
    "tinyassets/universe_seats.py",
    "tinyassets/workspace_family.py",
    "tinyassets/workspace_intents.py",
    "tinyassets/workspace_pool.py",
})

#: Connection helpers that still migrate a schema when a store is OPENED, outside
#: a startup migration boundary (design D3). Harmless with one owner tree; with a
#: standby owner process (C2), a stale process opening a store could rebuild or
#: alter it. Each must move to the startup migration step, or be shown additive
#: and atomic, before C2.
MIGRATES_ON_OPEN_BEFORE_C2: dict[str, str] = {
    "tinyassets/storage/provider_work_authority.py":
        "connection() runs executescript, a receipt-table rebuild that commits on "
        "its own, and column migrations on every open",
    "tinyassets/storage/agent_turn_journal.py":
        "ensure_schema adds owner_generation: additive, re-checked under BEGIN "
        "IMMEDIATE, so race-free; listed until the startup boundary exists",
}

#: Set by slice C2 when a second owner process can exist. Never before
#: :data:`FENCE_BEFORE_C2` and :data:`MIGRATES_ON_OPEN_BEFORE_C2` are empty.
HANDOVER_ENABLED = False
