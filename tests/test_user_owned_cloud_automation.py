from __future__ import annotations

import importlib
from datetime import datetime, timezone
from pathlib import Path


from tinyassets.background_branch_authority import (
    BackgroundBranchAttempt,
    BackgroundBranchAttemptLifecycle,
    BackgroundBranchBinding,
    BackgroundBranchBindingStatus,
    BackgroundBranchChildDelegation,
    BackgroundBranchExecutorAudience,
    BackgroundBranchExecutorClass,
    BackgroundBranchOperation,
    BackgroundBranchProvenance,
    BackgroundBranchReceiptRefs,
    BackgroundBranchSourceKind,
    BackgroundBranchTargetMode,
)
from tinyassets.evaluation.scenario_runner import AcceptanceScenario
from tinyassets.provider_work_authority import (
    ProviderWorkBindingSeed,
)
from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger
from tinyassets.storage.provider_work_authority import (
    SQLiteProviderWorkAuthorityStore,
)


def _authority(ledger):
    from tinyassets.broker.connection_authority import BrokerConnectionAuthority

    return BrokerConnectionAuthority(ledger._data_root, 'universe_alice',
                                     ledger.require_authenticated_principal_id)


def _automation():
    return importlib.import_module("tinyassets.user_owned_cloud_automation")


_DEFAULT_ACTION_CAP = object()


def _definition_payload() -> dict[str, object]:
    return {
        "schema_version": 1,
        "principal_id": "acct_alice",
        "universe_id": "universe_alice",
        "repository": "example/project",
        "accepted_spec_ref": "openspec/specs/example/spec.md",
        "accepted_spec_digest": f"sha256:{'a' * 64}",
        "branch_def_id": "branch_repo_spec_loop",
        "branch_version_id": "branch_repo_spec_loop@abc12345",
        "branch_content_digest": f"sha256:{'b' * 64}",
        "acceptance_scenario_id": "scenario:repo-spec-baseline-v1",
        "acceptance_scenario_digest": f"sha256:{'c' * 64}",
        "input_artifact_digests": [
            f"sha256:{'a' * 64}",
            f"sha256:{'b' * 64}",
        ],
        "provider_binding_id": "provider_binding_alice",
        "destination_grant_id": "destination_grant_project",
        "destination_purpose": "pull_request",
        "max_attempts": 2,
        "max_provider_invocations": 4,
        "max_wall_time_seconds": 3600,
        "max_tokens": 100_000,
        "max_cost_microunits": 5_000_000,
    }


def _scenario(**overrides: object) -> AcceptanceScenario:
    payload: dict[str, object] = {
        "scenario_id": "scenario:repo-spec-baseline-v1",
        "target_surface": "session_trace_summary",
        "user_story": (
            "A repository owner needs a deterministic preflight that checks "
            "immutable repository and OpenSpec evidence before any provider "
            "or GitHub effect is authorized. The preflight must be safe for "
            "multi-tenant cloud execution and preserve exact evidence."
        ),
        "allowed_tools": [],
        "evaluator_chain": ["evaluator:coding-trajectory-v1"],
        "artifact_requirements": [{"kind": "content_digest", "required": True}],
        "pass_threshold": {"min_score": 1.0},
        "cost_budget": {"max_tokens": 0, "max_wall_time_seconds": 10},
        "privacy_scope": "universe_only",
        "idempotency_key_constructor": "scenario+candidate+artifact-digests",
        "setup": [],
    }
    payload.update(overrides)
    return AcceptanceScenario(**payload)  # type: ignore[arg-type]


def _cloud_authority_fixture(
    tmp_path: Path,
    *,
    provider_overrides: dict[str, object] | None = None,
    connection_scopes: tuple[str, ...] = (
        "pull_requests:write",
        "pull_requests:read_for_commit",
    ),
    action_cap: ActionCap | None | object = _DEFAULT_ACTION_CAP,
) -> tuple[
    object,
    SQLiteProviderWorkAuthorityStore,
    ConnectionLedger,
]:
    def clock() -> datetime:
        return datetime(2026, 7, 31, 20, 0, tzinfo=timezone.utc)

    provider_store = SQLiteProviderWorkAuthorityStore(
        tmp_path,
        clock=clock,
        allow_test_fixtures=True,
    )
    seed_values: dict[str, object] = {
        "owner_user_id": "acct_alice",
        "universe_id": "universe_alice",
        "provider": "codex",
        "credential_reference_digest": f"sha256:{'9' * 64}",
        "allowed_operations": ("repository_spec_delivery",),
        "allowed_roles": ("writer",),
        "assignment_generation": 2,
        "assignment_digest": f"sha256:{'8' * 64}",
        "max_invocations": 4,
        "max_tokens": 100_000,
        "max_cost_microunits": 5_000_000,
        "expires_at": "2026-08-02T00:00:00Z",
    }
    seed_values.update(provider_overrides or {})
    seed = ProviderWorkBindingSeed(**seed_values)  # type: ignore[arg-type]

    binding = provider_store.install_test_binding(seed).record
    assert binding is not None
    payload = _definition_payload()
    payload["provider_binding_id"] = binding.binding_id
    definition = _automation().RepositorySpecWorkDefinition.from_dict(payload)
    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "acct_alice",
    )
    ledger.create_connection(
        connection_id="conn_tinyassets",
        owner_user_id="acct_alice",
        connection_class="pull-request-writer",
        scopes=connection_scopes,
        provider="github",
        destination="github.com/example/project",
        credential_ref="vault://github/example-project",
    )
    ledger.grant_connection(
        grant_id="destination_grant_project",
        connection_id="conn_tinyassets",
        owner_user_id="acct_alice",

        granted_at=1.0,
        unprompted_action_cap=(
            action_cap
            if action_cap is not _DEFAULT_ACTION_CAP
            else ActionCap("one_pull_request", 1, "pull_requests")
        ),  # type: ignore[arg-type]
    )
    return definition, provider_store, ledger


def _binding_and_attempt() -> tuple[BackgroundBranchBinding, BackgroundBranchAttempt]:
    receipts = BackgroundBranchReceiptRefs(
        b2_execution_grant_id=None,
        provider_work_receipt_id="pwr_01",
        provider_attempt_receipt_id="pat_01",
        payment_receipt_id=None,
        effect_receipt_id="eff_01",
    )
    binding = BackgroundBranchBinding(
        schema_version=1,
        binding_id="bnd_01",
        status=BackgroundBranchBindingStatus.ACTIVE,
        generation=3,
        binding_digest=f"sha256:{'f' * 64}",
        authorizing_principal_id="acct_alice",
        universe_id="universe_alice",
        branch_def_id="branch_repo_spec_loop",
        operation=BackgroundBranchOperation.INVOKE_BRANCH_VERSION,
        source_kind=BackgroundBranchSourceKind.REQUEST_ADMISSION,
        source_id="request_17",
        source_revision="4",
        source_digest=f"sha256:{'1' * 64}",
        revocation_generation=0,
        target_mode=BackgroundBranchTargetMode.PINNED_VERSION,
        pinned_branch_version_id="branch_repo_spec_loop@abc12345",
        permitted_executor_classes=(BackgroundBranchExecutorClass.CLOUD,),
        daemon_id=None,
        runtime_id=None,
        expires_at=None,
        max_attempts=2,
        remaining_depth=0,
        remaining_count=1,
        remaining_cost_microunits=5_000_000,
        child_delegation=BackgroundBranchChildDelegation(
            allowed_branch_def_ids=(),
            allowed_operations=(),
            max_depth=0,
            max_count=0,
            max_cost_microunits=0,
        ),
    )
    audience = BackgroundBranchExecutorAudience(
        executor_class=BackgroundBranchExecutorClass.CLOUD,
        daemon_id=None,
        runtime_id="runtime_cloud_1",
        worker_id="worker_1",
    )
    provenance = BackgroundBranchProvenance(
        authorizing_principal_id="acct_alice",
        source_kind=BackgroundBranchSourceKind.REQUEST_ADMISSION,
        source_id="request_17",
        executor_class=BackgroundBranchExecutorClass.CLOUD,
        daemon_id=None,
        runtime_id="runtime_cloud_1",
        worker_id="worker_1",
        parent_attempt_id=None,
        origin_attempt_id="att_01",
        audit_correlation_ids=("trace:17",),
        receipt_refs=receipts,
    )
    attempt = BackgroundBranchAttempt(
        schema_version=1,
        attempt_id="att_01",
        logical_attempt_key="request:17:g4",
        binding_id=binding.binding_id,
        binding_digest=binding.binding_digest,
        binding_generation=binding.generation,
        authorizing_principal_id="acct_alice",
        universe_id="universe_alice",
        branch_def_id="branch_repo_spec_loop",
        branch_version_id="branch_repo_spec_loop@abc12345",
        branch_content_digest=f"sha256:{'b' * 64}",
        operation=BackgroundBranchOperation.INVOKE_BRANCH_VERSION,
        source_kind=BackgroundBranchSourceKind.REQUEST_ADMISSION,
        source_id="request_17",
        source_generation=4,
        executor_audience=audience,
        claim_generation=2,
        lease_generation=5,
        lease_expires_at="2026-07-31T00:00:00Z",
        remaining_depth=0,
        remaining_count=1,
        remaining_cost_microunits=5_000_000,
        lifecycle=BackgroundBranchAttemptLifecycle.CLAIMED,
        hold_reason=None,
        terminal_reason=None,
        created_at="2026-07-30T20:00:00Z",
        updated_at="2026-07-30T20:01:00Z",
        provenance=provenance,
    )
    return binding, attempt




































