"""Retained SQLite columns whose defining writer is no longer in tinyassets/.

The cited Git revisions and source lines are the review evidence. These names
are code/schema vocabulary, never values copied from a data store.
"""

REVIEWED_COLUMNS: dict[str, list[str]] = {
    # cea945a5fa9^:tinyassets/storage/agent_request_usage.py:28,33,40,44,51
    # C6 removed the old universe scope from all five broker accounting tables.
    "agent_request_usage": ["universe"],
    "agent_request_attempts": ["universe"],
    "agent_request_usage_links": ["universe"],
    "agent_request_dispatches": ["universe"],
    "agent_model_launches": ["universe"],
    # cea945a5fa9^:tinyassets/storage/outbound_connections.py:4830
    "outbound_connection_grants": ["universe_id"],
    # cea945a5fa9^:tinyassets/connection_oauth/pkce.py:55
    "hosted_model_flows": ["bound_home_id"],
    # cea945a5fa9^:tinyassets/onboarding/inline_model_connect.py:48
    "inline_model_flows": ["home"],
    # 3fb4fa577a5^:tinyassets/storage/action_approvals.py:42
    "action_approvals": (
        "universe_id action_key state standing detail decided_by updated_at"
    ).split(),
    # 5cbed3aca9f^:tinyassets/storage/app_channel_bindings.py:41
    "app_channel_bindings": (
        "binding_row_id provider installation_id workspace_id channel_id universe_id "
        "agent_binding_id binding_revision bound_by created_at"
    ).split(),
    # 5cbed3aca9f^:tinyassets/storage/app_events.py:22
    "app_event_admissions": (
        "admission_id record_digest provider installation_id external_event_id event_type "
        "request_timestamp body_sha256 admitted_at record_json"
    ).split(),
    # 5cbed3aca9f^:tinyassets/storage/app_principal_mappings.py:24
    "app_principal_mappings": (
        "mapping_id record_digest provider installation_id workspace_id external_sender_id "
        "subject_id universe_id agent_binding_id binding_revision membership_generation "
        "mapping_generation status created_at revoked_at record_json"
    ).split(),
    # 508338725fa^:tinyassets/engine_admissions.py:164
    "dispatch_budget": ("universe_id ts dispatches bytes").split(),
    # 3fb4fa577a5^:tinyassets/storage/operation_scopes.py:78
    "operation_scopes": (
        "universe_id operation scopes_json defined_by defined_at"
    ).split(),
    # 3fb4fa577a5^:tinyassets/storage/scheduled_work.py:107
    "scheduled_work": (
        "work_id universe_id name kind branch_def_id inputs_json cadence_seconds "
        "declared_operations_json deliver_to state revision owner_id created_at "
        "updated_at last_run_at last_run_id"
    ).split(),
    # dadfbc01aa2^:tinyassets/agent_interchange.py:67
    "agent_import_stages": (
        "stage_id actor_id status direction source_media_type sanitized_source_digest "
        "source_commitment adapter_ref adapter_version adapter_digest candidate_json "
        "report_json receipt_json created_at expires_at published_definition_id"
    ).split(),
    # dadfbc01aa2^:tinyassets/agent_interchange.py:91
    "agent_interchange_idempotency": (
        "actor_id operation idempotency_key request_digest resource_id created_at"
    ).split(),
    # dadfbc01aa2^:tinyassets/agent_interchange.py:101
    "agent_conversion_receipts": (
        "receipt_digest actor_id stage_id receipt_json created_at"
    ).split(),
    # dadfbc01aa2^:tinyassets/agent_interchange.py:110
    "agent_conversion_receipt_links": (
        "stage_id receipt_digest actor_id created_at"
    ).split(),
    # dadfbc01aa2^:tinyassets/agent_interchange.py:123
    "agent_conversion_receipt_owners": (
        "actor_id receipt_digest operation created_at"
    ).split(),
    # dadfbc01aa2^:tinyassets/storage/cloud_automation_continuation.py:32
    "cloud_automation_continuations": (
        "continuation_id universe_id automation_id generation state continuation_digest record_json"
    ).split(),
    # dadfbc01aa2^:tinyassets/storage/cloud_automation_continuation.py:43
    "cloud_execution_continuations": (
        "continuation_id work_item_kind work_item_id universe_id automation_id "
        "generation state continuation_digest record_json"
    ).split(),
    # cea945a5fa9^:tinyassets/external_agents/receipts.py:18
    "external_agent_catalogue_receipts": (
        "id owner_user_id universe_id agent package_digest custody_digest admitted_at "
        "started_at completed_at outcome model_count reason excerpt"
    ).split(),
    # cea945a5fa9^:tinyassets/external_agents/receipts.py:45
    "external_agent_cli_invocations": (
        "id owner_user_id universe_id logical_key operation_id custody_digest "
        "admitted_at acknowledged_at"
    ).split(),
    # cea945a5fa9^:tinyassets/external_agents/receipts.py:157
    "external_agent_job_receipts": (
        "id owner_user_id universe_id execution native_agent package_digest custody_digest "
        "requested_model resolved_model reported_model model_status native_session "
        "input_tokens output_tokens usage_provenance admitted_at completed_at outcome reason"
    ).split(),
    # 03c478140bc^:tinyassets/credential_vault.py:695,729,735
    "llm_credential_deposit_owners": (
        "universe_id service owner_user_id connected_at deposit_id"
    ).split(),
    # 03c478140bc^:tinyassets/credential_vault.py:707
    "llm_credential_deposit_commits": ("deposit_id committed_at").split(),
    # dadfbc01aa2^:tinyassets/credential_vault.py:1013
    "llm_credential_refresh_state": (
        "universe_id service rejected_at"
    ).split(),
    # dadfbc01aa2^:tinyassets/providers/shortlist_store.py:20
    "native_shortlist_cache": (
        "owner_user_id universe_id provider custody_digest document"
    ).split(),
    # scripts/retire_cheat_loop_deploy_fence.py:193, RECEIPT_COLUMNS
    "wiki_trigger_attempts": (
        "trigger_attempt_id request_id request_kind request_page status attempted_at "
        "goal_id branch_def_id queued_at run_id dispatcher_request_id error_class error_message"
    ).split(),
    # langgraph/checkpoint/sqlite/__init__.py:135, checkpoint-sqlite 3.x
    "checkpoints": (
        "thread_id checkpoint_ns checkpoint_id parent_checkpoint_id type checkpoint metadata"
    ).split(),
    # langgraph/checkpoint/sqlite/__init__.py:145, checkpoint-sqlite 3.x
    # Upstream's newer schema/migration adds task_path: same file, lines 1777,1801:
    # https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint-sqlite/langgraph/checkpoint/sqlite/__init__.py#L1777
    "writes": (
        "thread_id checkpoint_ns checkpoint_id task_id task_path idx channel type value"
    ).split(),
}
