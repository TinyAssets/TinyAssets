"""Inert contracts for a user-authored repository-to-spec composition.

The immutable definition carries no runtime authority. Admission is a pure
validation step, and projections are derived from records owned elsewhere.
This module performs no persistence, queue mutation, provider call, tenant
code execution, credential lookup, or external effect.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from types import MappingProxyType
from typing import Any

from tinyassets.background_branch_authority import (
    BackgroundBranchAttempt,
    BackgroundBranchBinding,
    BackgroundBranchExecutorClass,
    BackgroundBranchTargetMode,
)
from tinyassets.broker.connection_authority import (
    ConnectionAuthority,
    read_authority,
    require_connection_authority,
)
from tinyassets.broker.supervisor import broker_selected
from tinyassets.evaluation.scenario_runner import AcceptanceScenario
from tinyassets.provider_work_authority import ProviderWorkBindingState
from tinyassets.storage.outbound_connections import ConnectionLedger
from tinyassets.storage.provider_work_authority import (
    SQLiteProviderWorkAuthorityStore,
)

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REPOSITORY_RE = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
SERVER_DETERMINISTIC_EVALUATOR_POLICY = MappingProxyType(
    {
        "session_trace_summary": frozenset(
            {"evaluator:coding-trajectory-v1"}
        ),
    }
)


def _canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(payload: Any) -> str:
    encoded = _canonical_json(payload).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _text(value: Any, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _sha256(value: Any, name: str) -> str:
    value = _text(value, name)
    if not _SHA256_RE.fullmatch(value):
        raise ValueError(f"{name} must be a sha256 digest")
    return value


def _positive_int(value: Any, name: str, *, minimum: int = 1) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


@dataclass(frozen=True, slots=True)
class RepositorySpecWorkDefinition:
    """Immutable inputs for one ordinary Branch composition."""

    schema_version: int
    principal_id: str
    universe_id: str
    repository: str
    accepted_spec_ref: str
    accepted_spec_digest: str
    branch_def_id: str
    branch_version_id: str
    branch_content_digest: str
    acceptance_scenario_id: str
    acceptance_scenario_digest: str
    input_artifact_digests: tuple[str, ...]
    provider_binding_id: str
    destination_grant_id: str
    destination_purpose: str
    max_attempts: int
    max_provider_invocations: int
    max_wall_time_seconds: int
    max_tokens: int
    max_cost_microunits: int

    _FIELD_ORDER = (
        "schema_version",
        "principal_id",
        "universe_id",
        "repository",
        "accepted_spec_ref",
        "accepted_spec_digest",
        "branch_def_id",
        "branch_version_id",
        "branch_content_digest",
        "acceptance_scenario_id",
        "acceptance_scenario_digest",
        "input_artifact_digests",
        "provider_binding_id",
        "destination_grant_id",
        "destination_purpose",
        "max_attempts",
        "max_provider_invocations",
        "max_wall_time_seconds",
        "max_tokens",
        "max_cost_microunits",
    )
    _FIELDS = frozenset(_FIELD_ORDER)

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        for name in (
            "principal_id",
            "universe_id",
            "accepted_spec_ref",
            "branch_def_id",
            "branch_version_id",
            "acceptance_scenario_id",
            "provider_binding_id",
            "destination_grant_id",
        ):
            _text(getattr(self, name), name)
        if not _REPOSITORY_RE.fullmatch(self.repository):
            raise ValueError("repository must be an exact owner/name")
        for name in (
            "accepted_spec_digest",
            "branch_content_digest",
            "acceptance_scenario_digest",
        ):
            _sha256(getattr(self, name), name)
        digests = tuple(
            _sha256(value, "input_artifact_digests")
            for value in self.input_artifact_digests
        )
        if not digests or len(set(digests)) != len(digests):
            raise ValueError("input_artifact_digests must be non-empty and unique")
        object.__setattr__(self, "input_artifact_digests", digests)
        if self.destination_purpose != "pull_request":
            raise ValueError("destination_purpose must be pull_request")
        attempts = _positive_int(self.max_attempts, "max_attempts")
        if attempts > 2:
            raise ValueError("max_attempts must be <= 2")
        provider_invocations = _positive_int(
            self.max_provider_invocations,
            "max_provider_invocations",
        )
        if provider_invocations > 64:
            raise ValueError("max_provider_invocations must be <= 64")
        _positive_int(self.max_wall_time_seconds, "max_wall_time_seconds")
        max_tokens = _positive_int(self.max_tokens, "max_tokens")
        max_cost_microunits = _positive_int(
            self.max_cost_microunits,
            "max_cost_microunits",
        )
        if max_tokens < provider_invocations:
            raise ValueError("max_tokens must be >= max_provider_invocations")
        if max_cost_microunits < provider_invocations:
            raise ValueError(
                "max_cost_microunits must be >= max_provider_invocations"
            )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RepositorySpecWorkDefinition:
        if not isinstance(data, dict):
            raise ValueError("RepositorySpecWorkDefinition must be an object")
        unknown = sorted(set(data) - cls._FIELDS)
        missing = sorted(cls._FIELDS - set(data))
        if unknown:
            raise ValueError(f"RepositorySpecWorkDefinition unknown fields: {unknown}")
        if missing:
            raise ValueError(f"RepositorySpecWorkDefinition missing fields: {missing}")
        values = dict(data)
        raw_digests = values["input_artifact_digests"]
        if not isinstance(raw_digests, (list, tuple)):
            raise ValueError("input_artifact_digests must be a list")
        values["input_artifact_digests"] = tuple(raw_digests)
        return cls(**values)

    def to_dict(self) -> dict[str, Any]:
        result = {name: getattr(self, name) for name in self._FIELD_ORDER}
        result["input_artifact_digests"] = list(self.input_artifact_digests)
        return result

    @property
    def definition_digest(self) -> str:
        return _digest(self.to_dict())


class AutomationAdmissionError(ValueError):
    """Stable fail-closed result for a non-admissible work definition."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def repository_spec_automation_id(
    definition: RepositorySpecWorkDefinition,
) -> str:
    """Derive the sole activation identity for one repository/spec lineage."""
    if not isinstance(definition, RepositorySpecWorkDefinition):
        raise ValueError("definition must be a RepositorySpecWorkDefinition")
    identity = {
        "domain": "repository-spec-automation-identity-v1",
        "principal_id": definition.principal_id,
        "universe_id": definition.universe_id,
        "repository": definition.repository.lower(),
        "accepted_spec_ref": definition.accepted_spec_ref,
        "branch_def_id": definition.branch_def_id,
    }
    return f"automation_repo_{_digest(identity).removeprefix('sha256:')[:32]}"


class AutomationProjectionError(ValueError):
    """A supplied authority record does not belong to this definition."""


@dataclass(frozen=True, slots=True)
class AdmittedWorkDefinition:
    definition: RepositorySpecWorkDefinition
    acceptance_scenario_digest: str
    acceptance_scenario_json: str
    target_surface: str
    evaluator_chain: tuple[str, ...]
    input_artifact_digests: tuple[str, ...]
    privacy_scope: str
    scenario_max_tokens: int
    scenario_max_wall_time_seconds: int


@dataclass(frozen=True, slots=True)
class RepositorySpecOperationalProjection:
    """Ephemeral read-only view over independently owned authority records."""

    definition_digest: str
    principal_id: str
    universe_id: str
    repository: str
    accepted_spec_ref: str
    branch_version_id: str
    acceptance_scenario_id: str
    binding_id: str | None
    binding_generation: int | None
    binding_status: str
    attempt_id: str | None
    logical_attempt_key: str | None
    source_generation: int | None
    claim_generation: int | None
    lease_generation: int | None
    attempt_lifecycle: str | None
    blocker: str | None
    provider_work_receipt_id: str | None
    provider_attempt_receipt_id: str | None
    effect_receipt_id: str | None


@dataclass(frozen=True, slots=True)
class InactiveCloudAuthorityResolution:
    """Secret-free exact authority references; not a provider receipt."""

    authority_source: str
    provider_binding_id: str
    provider_binding_generation: int
    provider_binding_digest: str
    provider: str
    destination_grant_id: str
    destination_connection_id: str
    destination: str
    destination_scope: str


def acceptance_scenario_digest(scenario: AcceptanceScenario) -> str:
    if not isinstance(scenario, AcceptanceScenario):
        raise ValueError("scenario must be an AcceptanceScenario")
    return _digest(asdict(scenario))


def repository_spec_baseline_scenario() -> AcceptanceScenario:
    """Return the immutable tenant-code-free baseline admitted by this release."""

    return AcceptanceScenario(
        scenario_id="scenario:repo-spec-baseline-v1",
        target_surface="session_trace_summary",
        user_story=(
            "A repository owner needs a deterministic preflight that checks "
            "immutable repository and OpenSpec evidence before any provider "
            "or GitHub effect is authorized. The preflight must be safe for "
            "multi-tenant cloud execution and preserve exact evidence."
        ),
        allowed_tools=[],
        evaluator_chain=["evaluator:coding-trajectory-v1"],
        artifact_requirements=[{"kind": "content_digest", "required": True}],
        pass_threshold={"min_score": 1.0},
        cost_budget={"max_tokens": 0, "max_wall_time_seconds": 10},
        privacy_scope="universe_only",
        idempotency_key_constructor="scenario+candidate+artifact-digests",
        setup=[],
    )


def admit_work_definition(
    definition: RepositorySpecWorkDefinition,
    scenario: AcceptanceScenario,
) -> AdmittedWorkDefinition:
    """Freeze a no-tenant-code evaluation policy before provider spend."""

    if scenario.scenario_id != definition.acceptance_scenario_id:
        raise AutomationAdmissionError("scenario_mismatch", "scenario ID changed")
    digest = acceptance_scenario_digest(scenario)
    if digest != definition.acceptance_scenario_digest:
        raise AutomationAdmissionError("scenario_mismatch", "scenario digest changed")
    admitted = SERVER_DETERMINISTIC_EVALUATOR_POLICY.get(
        scenario.target_surface,
        frozenset(),
    )
    unsafe = (
        bool(scenario.allowed_tools)
        or bool(scenario.setup)
        or any(evaluator not in admitted for evaluator in scenario.evaluator_chain)
    )
    if unsafe:
        raise AutomationAdmissionError(
            "sandbox_unavailable",
            "tenant-code evaluator requires production confinement",
        )
    required_artifacts = {
        definition.accepted_spec_digest,
        definition.branch_content_digest,
    }
    if not required_artifacts.issubset(definition.input_artifact_digests):
        raise AutomationAdmissionError(
            "artifact_mismatch",
            "baseline inputs must include the accepted spec and Branch version digests",
        )
    if scenario.cost_budget["max_tokens"] > definition.max_tokens:
        raise AutomationAdmissionError(
            "budget_mismatch",
            "scenario token budget exceeds definition",
        )
    if scenario.cost_budget["max_wall_time_seconds"] > definition.max_wall_time_seconds:
        raise AutomationAdmissionError(
            "budget_mismatch",
            "scenario wall-time budget exceeds definition",
        )
    return AdmittedWorkDefinition(
        definition=definition,
        acceptance_scenario_digest=digest,
        acceptance_scenario_json=_canonical_json(asdict(scenario)),
        target_surface=scenario.target_surface,
        evaluator_chain=tuple(scenario.evaluator_chain),
        input_artifact_digests=definition.input_artifact_digests,
        privacy_scope=scenario.privacy_scope,
        scenario_max_tokens=scenario.cost_budget["max_tokens"],
        scenario_max_wall_time_seconds=(
            scenario.cost_budget["max_wall_time_seconds"]
        ),
    )


def resolve_inactive_cloud_authority(
    definition: RepositorySpecWorkDefinition,
    *,
    provider_store: SQLiteProviderWorkAuthorityStore,
    connection_ledger: ConnectionAuthority,
) -> InactiveCloudAuthorityResolution:
    """Resolve the two independent authority owners without activating work.

    This preflight neither resolves a credential nor mints provider/effect
    authority.  Execution must revalidate both owners just in time.
    """

    if not isinstance(definition, RepositorySpecWorkDefinition):
        raise ValueError("definition must be a RepositorySpecWorkDefinition")
    if not isinstance(provider_store, SQLiteProviderWorkAuthorityStore):
        raise ValueError("provider_store must be a SQLiteProviderWorkAuthorityStore")
    selected = broker_selected()
    if selected:
        require_connection_authority(connection_ledger)
    elif not isinstance(connection_ledger, ConnectionLedger):
        raise ValueError("connection_ledger must be a ConnectionLedger")

    try:
        binding = provider_store.get(definition.provider_binding_id)
        if binding is None:
            raise ValueError("binding missing")
        with provider_store.connection() as conn:
            current = provider_store.validate_in_transaction(
                conn,
                binding_id=binding.binding_id,
                binding_generation=binding.generation,
                binding_digest=binding.binding_digest,
                owner_user_id=definition.principal_id,
                universe_id=definition.universe_id,
                provider=binding.provider,
                operation="repository_spec_delivery",
                role="writer",
            )
        bounded = (
            binding.state is ProviderWorkBindingState.ACTIVE,
            binding.allowed_operations == ("repository_spec_delivery",),
            "writer" in binding.allowed_roles,
            binding.max_invocations == definition.max_provider_invocations,
            binding.max_tokens == definition.max_tokens,
            binding.max_cost_microunits == definition.max_cost_microunits,
        )
        if not current or not all(bounded):
            raise ValueError("binding is stale, revoked, expired, or too narrow")
    except (OSError, ValueError) as exc:
        raise AutomationAdmissionError(
            "provider_binding_unavailable",
            "requester-owned provider binding is not current and sufficient",
        ) from exc

    try:
        if selected:
            principal_id, grant, connection = read_authority(
                connection_ledger, definition.destination_grant_id)
        else:
            principal_id = connection_ledger.require_authenticated_principal_id()
            grant = connection_ledger.require_active_grant(
                definition.destination_grant_id
            )
            connection = connection_ledger.get_connection(grant.connection_id)
        expected_destination = definition.repository.strip().lower()
        actual_destination = (
            connection.destination.strip().lower()
            .removeprefix("https://")
            .removeprefix("http://")
            .removeprefix("github.com/")
            .strip("/")
        ) if connection is not None else ""
        expected_scopes = frozenset(
            {"pull_requests:write", "pull_requests:read_for_commit"}
        )
        cap = grant.unprompted_action_cap
        exact = (
            principal_id == definition.principal_id,
            grant.owner_user_id == definition.principal_id,
            grant.universe_id == definition.universe_id,
            connection is not None,
            connection is not None and connection.owner_user_id == definition.principal_id,
            connection is not None and connection.connection_class == "pull-request-writer",
            connection is not None and connection.provider == "github",
            connection is not None and actual_destination == expected_destination,
            connection is not None
            and len(connection.scopes) == len(expected_scopes)
            and frozenset(connection.scopes) == expected_scopes,
            cap is not None,
            cap is not None and cap.maximum == 1,
            cap is not None and cap.unit == "pull_requests",
            definition.destination_purpose == "pull_request",
        )
        if not all(exact) or connection is None:
            raise ValueError("destination grant does not match definition")
    except (LookupError, PermissionError, RuntimeError, ValueError) as exc:
        raise AutomationAdmissionError(
            "destination_grant_unavailable",
            "requester-owned exact repository grant is not current",
        ) from exc

    return InactiveCloudAuthorityResolution(
        authority_source="requester_owned",
        provider_binding_id=binding.binding_id,
        provider_binding_generation=binding.generation,
        provider_binding_digest=binding.binding_digest,
        provider=binding.provider,
        destination_grant_id=grant.grant_id,
        destination_connection_id=connection.connection_id,
        destination=connection.destination,
        destination_scope="pull_requests:write",
    )


def project_operational_state(
    definition: RepositorySpecWorkDefinition,
    *,
    binding: BackgroundBranchBinding | None = None,
    attempt: BackgroundBranchAttempt | None = None,
) -> RepositorySpecOperationalProjection:
    """Derive status without persisting or authorizing the projected fields."""

    if attempt is not None and binding is None:
        raise AutomationProjectionError("attempt requires its binding")
    if binding is not None:
        expected_binding = (
            binding.authorizing_principal_id == definition.principal_id,
            binding.universe_id == definition.universe_id,
            binding.branch_def_id == definition.branch_def_id,
            binding.target_mode is BackgroundBranchTargetMode.PINNED_VERSION,
            binding.pinned_branch_version_id == definition.branch_version_id,
            BackgroundBranchExecutorClass.CLOUD
            in binding.permitted_executor_classes,
            binding.max_attempts <= definition.max_attempts,
            binding.remaining_cost_microunits
            <= definition.max_cost_microunits,
        )
        if not expected_binding[0]:
            raise AutomationProjectionError("binding principal does not match definition")
        if not all(expected_binding[1:]):
            raise AutomationProjectionError("binding target does not match definition")
    if attempt is not None and binding is not None:
        attempt_matches = (
            attempt.binding_id == binding.binding_id,
            attempt.binding_digest == binding.binding_digest,
            attempt.binding_generation == binding.generation,
            attempt.authorizing_principal_id == definition.principal_id,
            attempt.universe_id == definition.universe_id,
            attempt.branch_def_id == definition.branch_def_id,
            attempt.branch_version_id == definition.branch_version_id,
            attempt.branch_content_digest == definition.branch_content_digest,
            attempt.operation is binding.operation,
            attempt.source_kind is binding.source_kind,
            attempt.source_id == binding.source_id,
            str(attempt.source_generation) == binding.source_revision,
            attempt.executor_audience.executor_class
            in binding.permitted_executor_classes,
            binding.daemon_id is None
            or attempt.executor_audience.daemon_id == binding.daemon_id,
            binding.runtime_id is None
            or attempt.executor_audience.runtime_id == binding.runtime_id,
            attempt.remaining_cost_microunits
            <= binding.remaining_cost_microunits,
            attempt.remaining_cost_microunits
            <= definition.max_cost_microunits,
            attempt.remaining_count <= binding.remaining_count,
            attempt.remaining_depth <= binding.remaining_depth,
        )
        if not all(attempt_matches):
            raise AutomationProjectionError("attempt does not match definition and binding")

    refs = attempt.provenance.receipt_refs if attempt is not None else None
    blocker = None
    if attempt is not None:
        blocker = (
            attempt.hold_reason.value
            if attempt.hold_reason is not None
            else attempt.terminal_reason
        )
    return RepositorySpecOperationalProjection(
        definition_digest=definition.definition_digest,
        principal_id=definition.principal_id,
        universe_id=definition.universe_id,
        repository=definition.repository,
        accepted_spec_ref=definition.accepted_spec_ref,
        branch_version_id=definition.branch_version_id,
        acceptance_scenario_id=definition.acceptance_scenario_id,
        binding_id=binding.binding_id if binding is not None else None,
        binding_generation=binding.generation if binding is not None else None,
        binding_status=binding.status.value if binding is not None else "inactive",
        attempt_id=attempt.attempt_id if attempt is not None else None,
        logical_attempt_key=(
            attempt.logical_attempt_key if attempt is not None else None
        ),
        source_generation=attempt.source_generation if attempt is not None else None,
        claim_generation=attempt.claim_generation if attempt is not None else None,
        lease_generation=attempt.lease_generation if attempt is not None else None,
        attempt_lifecycle=(
            attempt.lifecycle.value if attempt is not None else None
        ),
        blocker=blocker,
        provider_work_receipt_id=(
            refs.provider_work_receipt_id if refs is not None else None
        ),
        provider_attempt_receipt_id=(
            refs.provider_attempt_receipt_id if refs is not None else None
        ),
        effect_receipt_id=refs.effect_receipt_id if refs is not None else None,
    )


__all__ = [
    "AdmittedWorkDefinition",
    "AutomationAdmissionError",
    "AutomationProjectionError",
    "RepositorySpecWorkDefinition",
    "RepositorySpecOperationalProjection",
    "SERVER_DETERMINISTIC_EVALUATOR_POLICY",
    "acceptance_scenario_digest",
    "admit_work_definition",
    "project_operational_state",
    "repository_spec_automation_id",
    "repository_spec_baseline_scenario",
]
