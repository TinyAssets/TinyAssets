from __future__ import annotations

import importlib
import json
from dataclasses import FrozenInstanceError, replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

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
    ProviderWorkBindingFence,
    ProviderWorkBindingSeed,
    ProviderWorkBindingService,
)
from tinyassets.storage.outbound_connections import ActionCap, ConnectionLedger
from tinyassets.storage.provider_work_authority import (
    SQLiteProviderWorkAuthorityStore,
)


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
        universe_id="universe_alice",
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


def test_work_definition_is_generic_immutable_and_content_addressed() -> None:
    automation = _automation()
    payload = _definition_payload()

    definition = automation.RepositorySpecWorkDefinition.from_dict(payload)

    assert definition.to_dict() == payload
    assert definition.definition_digest.startswith("sha256:")
    assert automation.RepositorySpecWorkDefinition.from_dict(payload) == definition
    with pytest.raises(FrozenInstanceError):
        definition.repository = "other/project"


@pytest.mark.parametrize("value", (0, 65))
def test_work_definition_rejects_invalid_provider_invocation_budget(value: int) -> None:
    payload = _definition_payload()
    payload["max_provider_invocations"] = value

    with pytest.raises(ValueError, match="max_provider_invocations"):
        _automation().RepositorySpecWorkDefinition.from_dict(payload)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("max_tokens", 3, "max_tokens.*max_provider_invocations"),
        ("max_cost_microunits", 0, "max_cost_microunits.*>= 1"),
        (
            "max_cost_microunits",
            3,
            "max_cost_microunits.*max_provider_invocations",
        ),
    ),
)
def test_work_definition_requires_positive_budget_for_every_provider_call(
    field: str,
    value: int,
    message: str,
) -> None:
    payload = _definition_payload()
    payload[field] = value

    with pytest.raises(ValueError, match=message):
        _automation().RepositorySpecWorkDefinition.from_dict(payload)


@pytest.mark.parametrize(
    "runtime_field",
    [
        "activation_epoch",
        "lease_generation",
        "attempt_state",
        "provider_reservation_id",
        "effect_reservation_id",
        "terminal_state",
    ],
)
def test_work_definition_rejects_user_authored_runtime_authority(
    runtime_field: str,
) -> None:
    automation = _automation()
    payload = _definition_payload()
    payload[runtime_field] = "caller-controlled"

    with pytest.raises(ValueError, match="unknown fields"):
        automation.RepositorySpecWorkDefinition.from_dict(payload)


def test_admission_fails_closed_when_evaluator_can_execute_tenant_code() -> None:
    automation = _automation()
    scenario = _scenario(
        allowed_tools=["shell"],
        evaluator_chain=["evaluator:repository-tests-v1"],
    )
    payload = _definition_payload()
    payload["acceptance_scenario_digest"] = automation.acceptance_scenario_digest(
        scenario
    )
    definition = automation.RepositorySpecWorkDefinition.from_dict(payload)

    with pytest.raises(
        automation.AutomationAdmissionError,
        match="sandbox_unavailable",
    ) as exc_info:
        automation.admit_work_definition(
            definition,
            scenario,
        )

    assert exc_info.value.code == "sandbox_unavailable"


def test_admission_freezes_typed_deterministic_scenario() -> None:
    automation = _automation()
    scenario = _scenario()
    payload = _definition_payload()
    payload["acceptance_scenario_digest"] = automation.acceptance_scenario_digest(
        scenario
    )
    definition = automation.RepositorySpecWorkDefinition.from_dict(payload)

    admitted = automation.admit_work_definition(
        definition,
        scenario,
    )
    scenario.evaluator_chain.append("evaluator:mutated")
    scenario.artifact_requirements[0]["required"] = False
    scenario.cost_budget["max_tokens"] = 100_000

    assert admitted.definition == definition
    assert admitted.evaluator_chain == ("evaluator:coding-trajectory-v1",)
    assert admitted.acceptance_scenario_digest == (
        definition.acceptance_scenario_digest
    )
    frozen = json.loads(admitted.acceptance_scenario_json)
    assert frozen["artifact_requirements"][0]["required"] is True
    assert frozen["cost_budget"]["max_tokens"] == 0
    assert admitted.scenario_max_tokens == 0
    assert admitted.scenario_max_wall_time_seconds == 10


def test_admission_rejects_executable_scenario_surface_even_when_evaluator_is_known() -> None:
    automation = _automation()
    scenario = _scenario(target_surface="mcp_call")
    payload = _definition_payload()
    payload["acceptance_scenario_digest"] = automation.acceptance_scenario_digest(
        scenario
    )
    definition = automation.RepositorySpecWorkDefinition.from_dict(payload)

    with pytest.raises(
        automation.AutomationAdmissionError,
        match="sandbox_unavailable",
    ):
        automation.admit_work_definition(definition, scenario)


def test_operational_projection_is_derived_and_read_only() -> None:
    automation = _automation()
    definition = automation.RepositorySpecWorkDefinition.from_dict(
        _definition_payload()
    )
    binding, attempt = _binding_and_attempt()

    projection = automation.project_operational_state(
        definition,
        binding=binding,
        attempt=attempt,
    )

    assert projection.definition_digest == definition.definition_digest
    assert projection.binding_generation == 3
    assert projection.binding_status == "active"
    assert projection.source_generation == 4
    assert projection.claim_generation == 2
    assert projection.lease_generation == 5
    assert projection.attempt_lifecycle == "claimed"
    assert projection.provider_work_receipt_id == "pwr_01"
    assert projection.provider_attempt_receipt_id == "pat_01"
    assert projection.effect_receipt_id == "eff_01"
    with pytest.raises(FrozenInstanceError):
        projection.binding_generation = 4


def test_operational_projection_can_show_inactive_definition() -> None:
    automation = _automation()
    definition = automation.RepositorySpecWorkDefinition.from_dict(
        _definition_payload()
    )

    projection = automation.project_operational_state(definition)

    assert projection.binding_id is None
    assert projection.attempt_id is None
    assert projection.binding_status == "inactive"


def test_operational_projection_rejects_cross_definition_attempt() -> None:
    automation = _automation()
    payload = _definition_payload()
    payload["principal_id"] = "acct_other"
    definition = automation.RepositorySpecWorkDefinition.from_dict(payload)
    binding, attempt = _binding_and_attempt()

    with pytest.raises(
        automation.AutomationProjectionError,
        match="principal",
    ):
        automation.project_operational_state(
            definition,
            binding=binding,
            attempt=attempt,
        )


def test_operational_projection_requires_binding_for_attempt() -> None:
    automation = _automation()
    definition = automation.RepositorySpecWorkDefinition.from_dict(
        _definition_payload()
    )
    _binding, attempt = _binding_and_attempt()

    with pytest.raises(
        automation.AutomationProjectionError,
        match="binding",
    ):
        automation.project_operational_state(definition, attempt=attempt)


@pytest.mark.parametrize(
    "relation",
    [
        "binding_id",
        "binding_digest",
        "binding_generation",
        "universe",
        "branch_def",
        "branch_version",
        "branch_content",
        "operation",
        "source_kind",
        "source_id",
        "source_generation",
        "executor",
        "daemon",
        "runtime",
        "binding_attempt_budget",
        "binding_cost_budget",
        "attempt_cost_budget",
        "attempt_count_budget",
        "attempt_depth_budget",
    ],
)
def test_operational_projection_rejects_cross_record_relation(
    relation: str,
) -> None:
    automation = _automation()
    definition = automation.RepositorySpecWorkDefinition.from_dict(
        _definition_payload()
    )
    binding, attempt = _binding_and_attempt()
    if relation == "binding_id":
        attempt = replace(attempt, binding_id="bnd_other")
    elif relation == "binding_digest":
        attempt = replace(attempt, binding_digest=f"sha256:{'9' * 64}")
    elif relation == "binding_generation":
        attempt = replace(attempt, binding_generation=4)
    elif relation == "universe":
        attempt = replace(attempt, universe_id="universe_other")
    elif relation == "branch_def":
        attempt = replace(attempt, branch_def_id="branch_other")
    elif relation == "branch_version":
        attempt = replace(attempt, branch_version_id="branch_other@abc12345")
    elif relation == "branch_content":
        attempt = replace(attempt, branch_content_digest=f"sha256:{'9' * 64}")
    elif relation == "operation":
        attempt = replace(
            attempt,
            operation=BackgroundBranchOperation.INVOKE_BRANCH,
        )
    elif relation in {"source_kind", "source_id"}:
        source_kind = (
            BackgroundBranchSourceKind.SCHEDULE
            if relation == "source_kind"
            else attempt.source_kind
        )
        source_id = "schedule_other" if relation == "source_id" else attempt.source_id
        provenance = replace(
            attempt.provenance,
            source_kind=source_kind,
            source_id=source_id,
        )
        attempt = replace(
            attempt,
            source_kind=source_kind,
            source_id=source_id,
            provenance=provenance,
        )
    elif relation == "source_generation":
        attempt = replace(attempt, source_generation=5)
    elif relation == "executor":
        audience = replace(
            attempt.executor_audience,
            executor_class=BackgroundBranchExecutorClass.HOST,
        )
        provenance = replace(
            attempt.provenance,
            executor_class=BackgroundBranchExecutorClass.HOST,
        )
        attempt = replace(
            attempt,
            executor_audience=audience,
            provenance=provenance,
        )
    elif relation == "daemon":
        binding = replace(binding, daemon_id="daemon_expected")
    elif relation == "runtime":
        binding = replace(binding, runtime_id="runtime_expected")
    elif relation == "binding_attempt_budget":
        binding = replace(binding, max_attempts=3)
    elif relation == "binding_cost_budget":
        binding = replace(
            binding,
            remaining_cost_microunits=5_000_001,
        )
    elif relation == "attempt_cost_budget":
        attempt = replace(
            attempt,
            remaining_cost_microunits=5_000_001,
        )
    elif relation == "attempt_count_budget":
        attempt = replace(attempt, remaining_count=2)
    else:
        attempt = replace(attempt, remaining_depth=1)

    with pytest.raises(automation.AutomationProjectionError):
        automation.project_operational_state(
            definition,
            binding=binding,
            attempt=attempt,
        )


def test_inactive_cloud_authority_resolves_exact_user_owned_bindings(
    tmp_path: Path,
) -> None:
    automation = _automation()
    definition, provider_store, ledger = _cloud_authority_fixture(tmp_path)

    resolved = automation.resolve_inactive_cloud_authority(
        definition,
        provider_store=provider_store,
        connection_ledger=ledger,
    )

    assert resolved.provider_binding_id == definition.provider_binding_id
    assert resolved.provider == "codex"
    assert resolved.destination_grant_id == definition.destination_grant_id
    assert resolved.destination == "github.com/example/project"
    assert resolved.destination_scope == "pull_requests:write"
    assert resolved.authority_source == "requester_owned"


def test_inactive_cloud_authority_rejects_revoked_provider_binding(
    tmp_path: Path,
) -> None:
    automation = _automation()
    definition, provider_store, ledger = _cloud_authority_fixture(tmp_path)
    binding = provider_store.get(definition.provider_binding_id)
    assert binding is not None
    ProviderWorkBindingService(provider_store).revoke(
        ProviderWorkBindingFence(binding)
    )

    with pytest.raises(
        automation.AutomationAdmissionError,
        match="provider_binding_unavailable",
    ):
        automation.resolve_inactive_cloud_authority(
            definition,
            provider_store=provider_store,
            connection_ledger=ledger,
        )


@pytest.mark.parametrize(
    ("provider_overrides", "connection_scopes", "action_cap"),
    [
        ({"allowed_operations": ("repository_spec_delivery", "admin")}, None, _DEFAULT_ACTION_CAP),
        ({"max_invocations": 5}, None, _DEFAULT_ACTION_CAP),
        ({"max_tokens": 100_001}, None, _DEFAULT_ACTION_CAP),
        ({"max_cost_microunits": 5_000_001}, None, _DEFAULT_ACTION_CAP),
        ({"expires_at": "2026-07-31T19:00:00Z"}, None, _DEFAULT_ACTION_CAP),
        (
            {},
            ("pull_requests:write", "pull_requests:read_for_commit", "secrets:read"),
            _DEFAULT_ACTION_CAP,
        ),
        ({}, None, None),
        ({}, None, ActionCap("deny", 0, "pull_requests")),
        ({}, None, ActionCap("broad", 2, "pull_requests")),
        ({}, None, ActionCap("wrong_unit", 1, "repositories")),
    ],
)
def test_inactive_cloud_authority_rejects_broader_or_unusable_authority(
    tmp_path: Path,
    provider_overrides: dict[str, object],
    connection_scopes: tuple[str, ...] | None,
    action_cap: ActionCap | None | object,
) -> None:
    automation = _automation()
    kwargs: dict[str, object] = {
        "provider_overrides": provider_overrides,
        "action_cap": action_cap,
    }
    if connection_scopes is not None:
        kwargs["connection_scopes"] = connection_scopes
    definition, provider_store, ledger = _cloud_authority_fixture(
        tmp_path,
        **kwargs,  # type: ignore[arg-type]
    )

    with pytest.raises(automation.AutomationAdmissionError):
        automation.resolve_inactive_cloud_authority(
            definition,
            provider_store=provider_store,
            connection_ledger=ledger,
        )


def test_inactive_cloud_authority_accepts_additional_declared_provider_roles(
    tmp_path: Path,
) -> None:
    automation = _automation()
    definition, provider_store, ledger = _cloud_authority_fixture(
        tmp_path,
        provider_overrides={"allowed_roles": ("writer", "judge", "extract")},
    )

    resolved = automation.resolve_inactive_cloud_authority(
        definition,
        provider_store=provider_store,
        connection_ledger=ledger,
    )

    assert resolved.provider_binding_id == definition.provider_binding_id


@pytest.mark.parametrize(
    "fault",
    [
        "principal_foreign",
        "principal_missing",
        "principal_verifier_error",
        "connection_revoked",
    ],
)
def test_inactive_cloud_authority_rejects_stale_request_or_connection(
    tmp_path: Path,
    fault: str,
) -> None:
    automation = _automation()
    definition, provider_store, ledger = _cloud_authority_fixture(tmp_path)
    if fault == "principal_foreign":
        ledger._verify_authenticated_principal = lambda: "acct_mallory"
    elif fault == "principal_missing":
        ledger._verify_authenticated_principal = None
    elif fault == "principal_verifier_error":
        def unavailable_principal() -> str:
            raise RuntimeError("request principal unavailable")

        ledger._verify_authenticated_principal = unavailable_principal
    else:
        grant = ledger.get_grant(definition.destination_grant_id)
        assert grant is not None
        ledger.revoke_connection(grant.connection_id, revoked_at=2.0)

    with pytest.raises(
        automation.AutomationAdmissionError,
        match="destination_grant_unavailable",
    ):
        automation.resolve_inactive_cloud_authority(
            definition,
            provider_store=provider_store,
            connection_ledger=ledger,
        )


@pytest.mark.parametrize(
    "fault",
    ["grant_revoked", "grant_owner", "grant_universe", "destination"],
)
def test_inactive_cloud_authority_rejects_nonexact_destination_grant(
    tmp_path: Path,
    fault: str,
) -> None:
    automation = _automation()
    definition, provider_store, ledger = _cloud_authority_fixture(tmp_path)
    if fault == "grant_revoked":
        ledger.revoke_grant(definition.destination_grant_id, revoked_at=2.0)
    elif fault == "grant_owner":
        other_ledger = ConnectionLedger(
            tmp_path / "other-owner-outbound.db",
            verify_authenticated_principal=lambda: "acct_alice",
        )
        other_ledger.create_connection(
            connection_id="conn_other_owner",
            owner_user_id="acct_other",
            connection_class="pull-request-writer",
            scopes=("pull_requests:write", "pull_requests:read_for_commit"),
            provider="github",
            destination="github.com/example/project",
            credential_ref="vault://github/other-owner",
        )
        other_ledger.grant_connection(
            grant_id=definition.destination_grant_id,
            connection_id="conn_other_owner",
            owner_user_id="acct_other",
            universe_id="universe_alice",
            granted_at=1.0,
            unprompted_action_cap=ActionCap(
                "one_pull_request",
                1,
                "pull_requests",
            ),
        )
        ledger = other_ledger
    elif fault == "grant_universe":
        grant = ledger.get_grant(definition.destination_grant_id)
        assert grant is not None
        ledger.revoke_grant(grant.grant_id, revoked_at=2.0)
        ledger.grant_connection(
            grant_id="replacement_grant",
            connection_id=grant.connection_id,
            owner_user_id="acct_alice",
            universe_id="universe_other",
            granted_at=3.0,
            unprompted_action_cap=ActionCap(
                "one_pull_request",
                1,
                "pull_requests",
            ),
        )
        definition = replace(definition, destination_grant_id="replacement_grant")
    else:
        other_ledger = ConnectionLedger(
            tmp_path / "other-outbound.db",
            verify_authenticated_principal=lambda: "acct_alice",
        )
        other_ledger.create_connection(
            connection_id="conn_other",
            owner_user_id="acct_alice",
            connection_class="pull-request-writer",
            scopes=("pull_requests:write", "pull_requests:read_for_commit"),
            provider="github",
            destination="github.com/example/other",
            credential_ref="vault://github/other",
        )
        other_ledger.grant_connection(
            grant_id=definition.destination_grant_id,
            connection_id="conn_other",
            owner_user_id="acct_alice",
            universe_id="universe_alice",
            granted_at=1.0,
            unprompted_action_cap=ActionCap(
                "one_pull_request",
                1,
                "pull_requests",
            ),
        )
        ledger = other_ledger

    with pytest.raises(
        automation.AutomationAdmissionError,
        match="destination_grant_unavailable",
    ):
        automation.resolve_inactive_cloud_authority(
            definition,
            provider_store=provider_store,
            connection_ledger=ledger,
        )
