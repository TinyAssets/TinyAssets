"""Dark server-owned lifecycle transitions and background attempt issuance.

The service resolves canonical authority through a trusted server adapter and
is the only layer that constructs binding IDs, digests, generations, or status
replacements. Attempt issuance remains inert: it reserves typed authority state
but does not claim an attempt or activate background execution.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum
from typing import Any, Protocol, runtime_checkable

from tinyassets.background_branch_authority import (
    BackgroundBranchAttempt,
    BackgroundBranchAttemptFence,
    BackgroundBranchAttemptLifecycle,
    BackgroundBranchAttemptWriteResult,
    BackgroundBranchAuthorityStore,
    BackgroundBranchAuthorityWriteOutcome,
    BackgroundBranchBinding,
    BackgroundBranchBindingFence,
    BackgroundBranchBindingStatus,
    BackgroundBranchBindingWriteResult,
    BackgroundBranchChildDelegation,
    BackgroundBranchExecutorAudience,
    BackgroundBranchExecutorClass,
    BackgroundBranchHoldReason,
    BackgroundBranchOperation,
    BackgroundBranchProvenance,
    BackgroundBranchReceiptRefs,
    BackgroundBranchSourceKind,
    BackgroundBranchTargetMode,
)

_BINDING_ROOTS = frozenset(
    {
        BackgroundBranchSourceKind.SCHEDULE,
        BackgroundBranchSourceKind.SUBSCRIPTION,
        BackgroundBranchSourceKind.PINNED_SOUL,
        BackgroundBranchSourceKind.ROOT_RUN,
        BackgroundBranchSourceKind.REQUEST_ADMISSION,
        BackgroundBranchSourceKind.PRODUCER_SUBSCRIPTION,
        BackgroundBranchSourceKind.ACCEPTED_MARKET_CONTRACT,
        BackgroundBranchSourceKind.RESUMED_RUN,
        BackgroundBranchSourceKind.DIRECT_CHILD,
        BackgroundBranchSourceKind.PARENT_ATTEMPT,
    }
)
_PLACEHOLDER_DIGEST = f"sha256:{'0' * 64}"
_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _digest(value: object) -> str:
    encoded = _canonical_json(value).encode("utf-8")
    return f"sha256:{hashlib.sha256(encoded).hexdigest()}"


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty reference")
    return value


def _positive_integer(value: object, name: str) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 digest")
    return value


def _utc_timestamp(value: object, name: str) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical UTC timestamp") from exc
    if parsed.isoformat().replace("+00:00", "Z") != value:
        raise ValueError(f"{name} must be a canonical UTC timestamp")
    return parsed


@dataclass(frozen=True, slots=True)
class BackgroundBranchBindingRoot:
    """Non-authorizing lookup reference for one closed issuance root."""

    source_kind: BackgroundBranchSourceKind
    source_id: str

    def __post_init__(self) -> None:
        if self.source_kind not in _BINDING_ROOTS:
            raise ValueError("source_kind is not a binding issuance root")
        _required(self.source_id, "source_id")


@dataclass(frozen=True, slots=True)
class BackgroundBranchBindingSeed:
    """Canonical authority facts returned by a trusted server resolver."""

    authorizing_principal_id: str
    universe_id: str
    branch_def_id: str
    operation: BackgroundBranchOperation
    source_kind: BackgroundBranchSourceKind
    source_id: str
    source_revision: str
    source_digest: str
    target_mode: BackgroundBranchTargetMode
    pinned_branch_version_id: str | None
    permitted_executor_classes: tuple[BackgroundBranchExecutorClass, ...]
    daemon_id: str | None
    runtime_id: str | None
    expires_at: str | None
    max_attempts: int
    remaining_depth: int
    remaining_count: int
    remaining_cost_microunits: int
    child_delegation: BackgroundBranchChildDelegation

    def __post_init__(self) -> None:
        validated = _binding_from_seed(
            self,
            binding_id="bnd_validation",
            status=BackgroundBranchBindingStatus.ACTIVE,
            generation=1,
            revocation_generation=0,
        )
        object.__setattr__(
            self,
            "permitted_executor_classes",
            validated.permitted_executor_classes,
        )


@runtime_checkable
class BackgroundBranchBindingResolver(Protocol):
    """Trusted adapter over canonical principal/source/target read models."""

    def resolve(
        self,
        root: BackgroundBranchBindingRoot,
    ) -> BackgroundBranchBindingSeed | None:
        """Return fresh canonical facts, or ``None`` when authority is absent."""


class BackgroundBranchBindingTransitionError(ValueError):
    """Stable fail-closed result for an inadmissible server transition."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


@dataclass(frozen=True, slots=True)
class BackgroundBranchAttemptIssuanceRequest:
    """Non-authorizing references supplied to one JIT reservation."""

    binding_id: str
    binding_generation: int
    binding_digest: str
    logical_attempt_key: str
    physical_universe_id: str
    executor_audience: BackgroundBranchExecutorAudience

    def __post_init__(self) -> None:
        _required(self.binding_id, "binding_id")
        _positive_integer(self.binding_generation, "binding_generation")
        _sha256(self.binding_digest, "binding_digest")
        _required(self.logical_attempt_key, "logical_attempt_key")
        _required(self.physical_universe_id, "physical_universe_id")
        if not isinstance(
            self.executor_audience,
            BackgroundBranchExecutorAudience,
        ):
            raise ValueError("executor_audience must be typed")


@dataclass(frozen=True, slots=True)
class BackgroundBranchAttemptIssuanceResolution:
    """Fresh canonical state returned only by a trusted server resolver."""

    binding: BackgroundBranchBinding
    branch_version_id: str
    branch_content_digest: str
    source_generation: int
    executor_audience: BackgroundBranchExecutorAudience
    resolved_at: str
    parent_attempt_id: str | None
    origin_attempt_id: str | None
    audit_correlation_ids: tuple[str, ...]
    receipt_refs: BackgroundBranchReceiptRefs

    def __post_init__(self) -> None:
        if not isinstance(self.binding, BackgroundBranchBinding):
            raise ValueError("binding must be typed")
        _required(self.branch_version_id, "branch_version_id")
        _sha256(self.branch_content_digest, "branch_content_digest")
        if (
            not isinstance(self.source_generation, int)
            or isinstance(self.source_generation, bool)
            or self.source_generation < 0
        ):
            raise ValueError("source_generation must be a non-negative integer")
        if not isinstance(
            self.executor_audience,
            BackgroundBranchExecutorAudience,
        ):
            raise ValueError("executor_audience must be typed")
        _utc_timestamp(self.resolved_at, "resolved_at")
        if self.parent_attempt_id is not None:
            _required(self.parent_attempt_id, "parent_attempt_id")
        if self.origin_attempt_id is not None:
            _required(self.origin_attempt_id, "origin_attempt_id")
        correlations = tuple(self.audit_correlation_ids)
        if not correlations:
            raise ValueError("audit_correlation_ids must not be empty")
        for correlation_id in correlations:
            _required(correlation_id, "audit_correlation_ids")
        if len(set(correlations)) != len(correlations):
            raise ValueError("audit_correlation_ids must not contain duplicates")
        object.__setattr__(self, "audit_correlation_ids", correlations)
        if not isinstance(self.receipt_refs, BackgroundBranchReceiptRefs):
            raise ValueError("receipt_refs must be typed")


@runtime_checkable
class BackgroundBranchAttemptResolver(Protocol):
    """Trusted adapter that revalidates every canonical issuance fact."""

    def resolve(
        self,
        request: BackgroundBranchAttemptIssuanceRequest,
    ) -> BackgroundBranchAttemptIssuanceResolution | None:
        """Return fresh canonical state, or ``None`` when authority is absent."""


class BackgroundBranchAttemptIssuanceError(ValueError):
    """Stable fail-closed result for a refused JIT reservation."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class BackgroundBranchAttemptPredecessorState(str, Enum):
    UNKNOWN = "unknown"
    DEAD = "dead"
    INVALIDATED = "invalidated"


class BackgroundBranchAttemptBoundaryState(str, Enum):
    NOT_CROSSED = "not_crossed"
    CLOSED = "closed"
    INDETERMINATE = "indeterminate"


class BackgroundBranchAttemptClaimAction(str, Enum):
    CLAIM = "claim"
    RENEW = "renew"
    RELEASE = "release"
    RECLAIM = "reclaim"


@dataclass(frozen=True, slots=True)
class BackgroundBranchAttemptClaimRequest:
    """Non-authorizing inputs for a trusted claim-lifecycle resolution."""

    attempt: BackgroundBranchAttempt
    action: BackgroundBranchAttemptClaimAction
    requested_audience: BackgroundBranchExecutorAudience
    transitioned_at: str
    requested_lease_expires_at: str | None

    def __post_init__(self) -> None:
        if not isinstance(self.attempt, BackgroundBranchAttempt):
            raise ValueError("attempt must be typed")
        if not isinstance(self.action, BackgroundBranchAttemptClaimAction):
            raise ValueError("action must be typed")
        if not isinstance(
            self.requested_audience,
            BackgroundBranchExecutorAudience,
        ):
            raise ValueError("requested_audience must be typed")
        transitioned_at = _utc_timestamp(
            self.transitioned_at,
            "transitioned_at",
        )
        lease_expires_at = (
            _utc_timestamp(
                self.requested_lease_expires_at,
                "requested_lease_expires_at",
            )
            if self.requested_lease_expires_at is not None
            else None
        )
        leased_action = self.action in {
            BackgroundBranchAttemptClaimAction.CLAIM,
            BackgroundBranchAttemptClaimAction.RENEW,
        }
        if leased_action != (lease_expires_at is not None):
            raise ValueError("claim and renew require a requested lease expiry")
        if lease_expires_at is not None and lease_expires_at <= transitioned_at:
            raise ValueError("requested lease expiry must follow transition")


@dataclass(frozen=True, slots=True)
class BackgroundBranchAttemptClaimResolution:
    """Fresh server-owned authority and recovery evidence."""

    binding: BackgroundBranchBinding
    executor_audience: BackgroundBranchExecutorAudience
    predecessor: BackgroundBranchAttemptPredecessorState
    boundary: BackgroundBranchAttemptBoundaryState
    resolved_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.binding, BackgroundBranchBinding):
            raise ValueError("binding must be typed")
        if not isinstance(
            self.executor_audience,
            BackgroundBranchExecutorAudience,
        ):
            raise ValueError("executor_audience must be typed")
        if not isinstance(
            self.predecessor,
            BackgroundBranchAttemptPredecessorState,
        ):
            raise ValueError("predecessor must be typed")
        if not isinstance(self.boundary, BackgroundBranchAttemptBoundaryState):
            raise ValueError("boundary must be typed")
        _utc_timestamp(self.resolved_at, "resolved_at")


@runtime_checkable
class BackgroundBranchAttemptClaimResolver(Protocol):
    """Trusted adapter for executor, binding, predecessor, and boundary state."""

    def resolve(
        self,
        request: BackgroundBranchAttemptClaimRequest,
    ) -> BackgroundBranchAttemptClaimResolution | None:
        """Return fresh canonical evidence, or ``None`` when authority is absent."""


class BackgroundBranchAttemptClaimError(ValueError):
    """Stable fail-closed result for an invalid attempt claim transition."""

    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


class BackgroundBranchAuthorityOwnerKind(str, Enum):
    QUEUE_TASK = "queue_task"
    SOURCE = "source"


class BackgroundBranchAuthorityOwnerState(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"
    ACTIVE = "active"
    TARGET_AUTHORITY_HELD = "target_authority_held"


class BackgroundBranchAuthorityFailureKind(str, Enum):
    MISSING = "missing"
    STALE = "stale"
    REVOKED = "revoked"
    EXPIRED = "expired"
    EXHAUSTED = "exhausted"
    UNAUTHORIZED = "unauthorized"
    SOURCE_MISMATCHED = "source_mismatched"
    INDETERMINATE = "indeterminate"


class BackgroundBranchAuthorityExitAction(str, Enum):
    RECOVER = "recover"
    REAUTHORIZE = "reauthorize"


_FAILURE_HOLD_REASONS = {
    BackgroundBranchAuthorityFailureKind.MISSING: (
        BackgroundBranchHoldReason.BINDING_MISSING
    ),
    BackgroundBranchAuthorityFailureKind.STALE: (
        BackgroundBranchHoldReason.BINDING_STALE
    ),
    BackgroundBranchAuthorityFailureKind.REVOKED: (
        BackgroundBranchHoldReason.BINDING_REVOKED
    ),
    BackgroundBranchAuthorityFailureKind.EXPIRED: (
        BackgroundBranchHoldReason.BINDING_EXPIRED
    ),
    BackgroundBranchAuthorityFailureKind.EXHAUSTED: (
        BackgroundBranchHoldReason.BINDING_EXHAUSTED
    ),
    BackgroundBranchAuthorityFailureKind.UNAUTHORIZED: (
        BackgroundBranchHoldReason.TARGET_UNAUTHORIZED
    ),
    BackgroundBranchAuthorityFailureKind.SOURCE_MISMATCHED: (
        BackgroundBranchHoldReason.SOURCE_GENERATION_MISMATCH
    ),
    BackgroundBranchAuthorityFailureKind.INDETERMINATE: (
        BackgroundBranchHoldReason.INDETERMINATE_PRIOR_ATTEMPT
    ),
}


@dataclass(frozen=True, slots=True)
class BackgroundBranchAuthorityOwnerRecord:
    """Dark queue/source authority state; references are non-bearer fences."""

    owner_kind: BackgroundBranchAuthorityOwnerKind
    owner_id: str
    universe_id: str
    authorizing_principal_id: str
    source_generation: int
    transition_generation: int
    state: BackgroundBranchAuthorityOwnerState
    binding: BackgroundBranchBindingFence | None
    attempt: BackgroundBranchAttemptFence | None
    hold_reason: BackgroundBranchHoldReason | None
    updated_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner_kind, BackgroundBranchAuthorityOwnerKind):
            raise ValueError("owner_kind must be typed")
        if not isinstance(self.state, BackgroundBranchAuthorityOwnerState):
            raise ValueError("state must be typed")
        _required(self.owner_id, "owner_id")
        _required(self.universe_id, "universe_id")
        _required(self.authorizing_principal_id, "authorizing_principal_id")
        if (
            not isinstance(self.source_generation, int)
            or isinstance(self.source_generation, bool)
            or self.source_generation < 0
        ):
            raise ValueError("source_generation must be a non-negative integer")
        _positive_integer(self.transition_generation, "transition_generation")
        _utc_timestamp(self.updated_at, "updated_at")
        if self.owner_kind is BackgroundBranchAuthorityOwnerKind.QUEUE_TASK:
            allowed_states = {
                BackgroundBranchAuthorityOwnerState.PENDING,
                BackgroundBranchAuthorityOwnerState.RUNNING,
                BackgroundBranchAuthorityOwnerState.SUCCEEDED,
                BackgroundBranchAuthorityOwnerState.FAILED,
                BackgroundBranchAuthorityOwnerState.CANCELLED,
                BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD,
            }
        else:
            allowed_states = {
                BackgroundBranchAuthorityOwnerState.ACTIVE,
                BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD,
            }
        if self.state not in allowed_states:
            raise ValueError("state is invalid for owner_kind")
        held = (
            self.state
            is BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD
        )
        if held != (self.hold_reason is not None):
            raise ValueError("hold_reason is required only for held owners")
        if self.hold_reason is not None and not isinstance(
            self.hold_reason,
            BackgroundBranchHoldReason,
        ):
            raise ValueError("hold_reason must be typed")
        if self.binding is not None and not isinstance(
            self.binding, BackgroundBranchBindingFence
        ):
            raise ValueError("binding must be a typed fence")
        if self.attempt is not None and not isinstance(
            self.attempt, BackgroundBranchAttemptFence
        ):
            raise ValueError("attempt must be a typed fence")
        if self.attempt is not None and self.binding is None:
            raise ValueError("attempt requires a binding fence")

    def to_dict(self) -> dict[str, object]:
        return {
            "owner_kind": self.owner_kind.value,
            "owner_id": self.owner_id,
            "universe_id": self.universe_id,
            "authorizing_principal_id": self.authorizing_principal_id,
            "source_generation": self.source_generation,
            "transition_generation": self.transition_generation,
            "state": self.state.value,
            "binding": (
                self.binding.expected_record.to_dict()
                if self.binding is not None
                else None
            ),
            "attempt": (
                self.attempt.expected_record.to_dict()
                if self.attempt is not None
                else None
            ),
            "hold_reason": (
                self.hold_reason.value if self.hold_reason is not None else None
            ),
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
    ) -> BackgroundBranchAuthorityOwnerRecord:
        expected = {
            "owner_kind",
            "owner_id",
            "universe_id",
            "authorizing_principal_id",
            "source_generation",
            "transition_generation",
            "state",
            "binding",
            "attempt",
            "hold_reason",
            "updated_at",
        }
        if not isinstance(data, dict) or set(data) != expected:
            raise ValueError("owner record fields are invalid")
        try:
            owner_kind = BackgroundBranchAuthorityOwnerKind(data["owner_kind"])
            state = BackgroundBranchAuthorityOwnerState(data["state"])
            hold_reason = (
                BackgroundBranchHoldReason(data["hold_reason"])
                if data["hold_reason"] is not None
                else None
            )
            binding = (
                BackgroundBranchBindingFence(
                    BackgroundBranchBinding.from_dict(data["binding"])
                )
                if data["binding"] is not None
                else None
            )
            attempt = (
                BackgroundBranchAttemptFence(
                    BackgroundBranchAttempt.from_dict(data["attempt"])
                )
                if data["attempt"] is not None
                else None
            )
            return cls(
                owner_kind=owner_kind,
                owner_id=data["owner_id"],
                universe_id=data["universe_id"],
                authorizing_principal_id=data["authorizing_principal_id"],
                source_generation=data["source_generation"],
                transition_generation=data["transition_generation"],
                state=state,
                binding=binding,
                attempt=attempt,
                hold_reason=hold_reason,
                updated_at=data["updated_at"],
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("owner record is invalid") from exc


@dataclass(frozen=True, slots=True)
class BackgroundBranchAuthorityOwnerFence:
    expected_record: BackgroundBranchAuthorityOwnerRecord

    def __post_init__(self) -> None:
        if not isinstance(
            self.expected_record,
            BackgroundBranchAuthorityOwnerRecord,
        ):
            raise ValueError("expected_record must be typed")


@dataclass(frozen=True, slots=True)
class BackgroundBranchAuthorityOwnerWriteResult:
    outcome: BackgroundBranchAuthorityWriteOutcome
    record: BackgroundBranchAuthorityOwnerRecord | None

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, BackgroundBranchAuthorityWriteOutcome):
            raise ValueError("outcome must be typed")
        if self.record is not None and not isinstance(
            self.record,
            BackgroundBranchAuthorityOwnerRecord,
        ):
            raise ValueError("record must be typed")


@runtime_checkable
class BackgroundBranchAuthorityOwnerStore(Protocol):
    def compare_and_swap(
        self,
        *,
        expected: BackgroundBranchAuthorityOwnerFence,
        replacement: BackgroundBranchAuthorityOwnerRecord,
    ) -> BackgroundBranchAuthorityOwnerWriteResult: ...


@dataclass(frozen=True, slots=True)
class BackgroundBranchAuthorityExitRequest:
    owner: BackgroundBranchAuthorityOwnerRecord
    action: BackgroundBranchAuthorityExitAction
    authentication_context_id: str | None
    transitioned_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.owner, BackgroundBranchAuthorityOwnerRecord):
            raise ValueError("owner must be typed")
        if not isinstance(self.action, BackgroundBranchAuthorityExitAction):
            raise ValueError("action must be typed")
        if self.action is BackgroundBranchAuthorityExitAction.REAUTHORIZE:
            _required(self.authentication_context_id, "authentication_context_id")
        elif self.authentication_context_id is not None:
            raise ValueError("recovery accepts no authentication context")
        _utc_timestamp(self.transitioned_at, "transitioned_at")


@dataclass(frozen=True, slots=True)
class BackgroundBranchAuthorityExitResolution:
    binding: BackgroundBranchBinding
    attempt: BackgroundBranchAttempt | None
    authenticated_principal_id: str | None
    is_universe_admin: bool
    predecessor: BackgroundBranchAttemptPredecessorState
    boundary: BackgroundBranchAttemptBoundaryState
    resolved_at: str

    def __post_init__(self) -> None:
        if not isinstance(self.binding, BackgroundBranchBinding):
            raise ValueError("binding must be typed")
        if self.attempt is not None and not isinstance(
            self.attempt,
            BackgroundBranchAttempt,
        ):
            raise ValueError("attempt must be typed")
        if self.authenticated_principal_id is not None:
            _required(
                self.authenticated_principal_id,
                "authenticated_principal_id",
            )
        if not isinstance(self.is_universe_admin, bool):
            raise ValueError("is_universe_admin must be boolean")
        if not isinstance(
            self.predecessor,
            BackgroundBranchAttemptPredecessorState,
        ):
            raise ValueError("predecessor must be typed")
        if not isinstance(self.boundary, BackgroundBranchAttemptBoundaryState):
            raise ValueError("boundary must be typed")
        _utc_timestamp(self.resolved_at, "resolved_at")


@runtime_checkable
class BackgroundBranchAuthorityExitResolver(Protocol):
    def resolve(
        self,
        request: BackgroundBranchAuthorityExitRequest,
    ) -> BackgroundBranchAuthorityExitResolution | None: ...


@dataclass(frozen=True, slots=True)
class BackgroundBranchAuthorityHoldProjection:
    state: BackgroundBranchAuthorityOwnerState
    reason: BackgroundBranchHoldReason
    automatic_recovery_possible: bool
    authenticated_reauthorization_required: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "state": self.state.value,
            "reason": self.reason.value,
            "automatic_recovery_possible": self.automatic_recovery_possible,
            "authenticated_reauthorization_required": (
                self.authenticated_reauthorization_required
            ),
        }


def project_background_branch_authority_hold(
    owner: BackgroundBranchAuthorityOwnerRecord,
) -> BackgroundBranchAuthorityHoldProjection:
    if not isinstance(owner, BackgroundBranchAuthorityOwnerRecord):
        raise ValueError("owner must be typed")
    if (
        owner.state
        is not BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD
        or owner.hold_reason is None
    ):
        raise ValueError("only held authority owners have a hold projection")
    automatic = (
        owner.hold_reason
        is BackgroundBranchHoldReason.INDETERMINATE_PRIOR_ATTEMPT
    )
    return BackgroundBranchAuthorityHoldProjection(
        state=owner.state,
        reason=owner.hold_reason,
        automatic_recovery_possible=automatic,
        authenticated_reauthorization_required=not automatic,
    )


class BackgroundBranchAuthorityHoldError(ValueError):
    def __init__(self, code: str, detail: str) -> None:
        self.code = code
        super().__init__(f"{code}: {detail}")


def _binding_from_seed(
    seed: BackgroundBranchBindingSeed,
    *,
    binding_id: str,
    status: BackgroundBranchBindingStatus,
    generation: int,
    revocation_generation: int,
) -> BackgroundBranchBinding:
    provisional = BackgroundBranchBinding(
        schema_version=1,
        binding_id=binding_id,
        status=status,
        generation=generation,
        binding_digest=_PLACEHOLDER_DIGEST,
        authorizing_principal_id=seed.authorizing_principal_id,
        universe_id=seed.universe_id,
        branch_def_id=seed.branch_def_id,
        operation=seed.operation,
        source_kind=seed.source_kind,
        source_id=seed.source_id,
        source_revision=seed.source_revision,
        source_digest=seed.source_digest,
        revocation_generation=revocation_generation,
        target_mode=seed.target_mode,
        pinned_branch_version_id=seed.pinned_branch_version_id,
        permitted_executor_classes=seed.permitted_executor_classes,
        daemon_id=seed.daemon_id,
        runtime_id=seed.runtime_id,
        expires_at=seed.expires_at,
        max_attempts=seed.max_attempts,
        remaining_depth=seed.remaining_depth,
        remaining_count=seed.remaining_count,
        remaining_cost_microunits=seed.remaining_cost_microunits,
        child_delegation=seed.child_delegation,
    )
    payload = provisional.to_dict()
    del payload["binding_digest"]
    return replace(provisional, binding_digest=_digest(payload))


def _binding_id(root: BackgroundBranchBindingRoot) -> str:
    identity = {
        "schema_version": 1,
        "source_kind": root.source_kind.value,
        "source_id": root.source_id,
    }
    return f"bnd_{_digest(identity).removeprefix('sha256:')[:32]}"


def _with_server_transition(
    current: BackgroundBranchBinding,
    *,
    status: BackgroundBranchBindingStatus,
    revocation_generation: int,
) -> BackgroundBranchBinding:
    provisional = replace(
        current,
        status=status,
        generation=current.generation + 1,
        binding_digest=_PLACEHOLDER_DIGEST,
        revocation_generation=revocation_generation,
    )
    payload = provisional.to_dict()
    del payload["binding_digest"]
    return replace(provisional, binding_digest=_digest(payload))


class BackgroundBranchBindingTransitionService:
    """Construct and persist only server-derived binding transitions."""

    def __init__(
        self,
        store: BackgroundBranchAuthorityStore,
        resolver: BackgroundBranchBindingResolver,
    ) -> None:
        if not isinstance(store, BackgroundBranchAuthorityStore):
            raise ValueError("store must implement BackgroundBranchAuthorityStore")
        if not isinstance(resolver, BackgroundBranchBindingResolver):
            raise ValueError("resolver must implement BackgroundBranchBindingResolver")
        self._store = store
        self._resolver = resolver

    def _resolve(
        self,
        root: BackgroundBranchBindingRoot,
    ) -> BackgroundBranchBindingSeed:
        if not isinstance(root, BackgroundBranchBindingRoot):
            raise ValueError("root must be a BackgroundBranchBindingRoot")
        seed = self._resolver.resolve(root)
        if seed is None:
            raise BackgroundBranchBindingTransitionError(
                "root_resolution_missing",
                "canonical authority is absent",
            )
        if (
            not isinstance(seed, BackgroundBranchBindingSeed)
            or seed.source_kind is not root.source_kind
            or seed.source_id != root.source_id
        ):
            raise BackgroundBranchBindingTransitionError(
                "root_resolution_mismatch",
                "canonical source does not match the requested root",
            )
        return seed

    def create(
        self,
        root: BackgroundBranchBindingRoot,
    ) -> BackgroundBranchBindingWriteResult:
        seed = self._resolve(root)
        binding = _binding_from_seed(
            seed,
            binding_id=_binding_id(root),
            status=BackgroundBranchBindingStatus.ACTIVE,
            generation=1,
            revocation_generation=0,
        )
        with self._store.transaction() as transaction:
            return transaction.insert_binding(binding)

    def rotate(
        self,
        expected: BackgroundBranchBindingFence,
    ) -> BackgroundBranchBindingWriteResult:
        current = self._expected_binding(expected)
        root = BackgroundBranchBindingRoot(
            source_kind=current.source_kind,
            source_id=current.source_id,
        )
        seed = self._resolve(root)
        immutable_identity_matches = (
            seed.authorizing_principal_id == current.authorizing_principal_id,
            seed.universe_id == current.universe_id,
            seed.source_kind is current.source_kind,
            seed.source_id == current.source_id,
            _binding_id(root) == current.binding_id,
        )
        if not all(immutable_identity_matches):
            raise BackgroundBranchBindingTransitionError(
                "identity_transfer",
                "rotation cannot replace authorizer, command center, or source",
            )
        replacement = _binding_from_seed(
            seed,
            binding_id=current.binding_id,
            status=BackgroundBranchBindingStatus.ACTIVE,
            generation=current.generation + 1,
            revocation_generation=current.revocation_generation,
        )
        return self._compare_and_swap(expected, replacement)

    def pause(
        self,
        expected: BackgroundBranchBindingFence,
    ) -> BackgroundBranchBindingWriteResult:
        return self._status_transition(
            expected,
            target=BackgroundBranchBindingStatus.PAUSED,
            allowed=frozenset({BackgroundBranchBindingStatus.ACTIVE}),
        )

    def revoke(
        self,
        expected: BackgroundBranchBindingFence,
    ) -> BackgroundBranchBindingWriteResult:
        return self._status_transition(
            expected,
            target=BackgroundBranchBindingStatus.REVOKED,
            allowed=frozenset(
                {
                    BackgroundBranchBindingStatus.ACTIVE,
                    BackgroundBranchBindingStatus.PAUSED,
                    BackgroundBranchBindingStatus.EXHAUSTED,
                    BackgroundBranchBindingStatus.EXPIRED,
                }
            ),
            advance_revocation=True,
        )

    def exhaust(
        self,
        expected: BackgroundBranchBindingFence,
    ) -> BackgroundBranchBindingWriteResult:
        return self._status_transition(
            expected,
            target=BackgroundBranchBindingStatus.EXHAUSTED,
            allowed=frozenset(
                {
                    BackgroundBranchBindingStatus.ACTIVE,
                    BackgroundBranchBindingStatus.PAUSED,
                }
            ),
        )

    @staticmethod
    def _expected_binding(
        expected: BackgroundBranchBindingFence,
    ) -> BackgroundBranchBinding:
        if not isinstance(expected, BackgroundBranchBindingFence):
            raise ValueError("expected must be a BackgroundBranchBindingFence")
        return expected.expected_record

    def _status_transition(
        self,
        expected: BackgroundBranchBindingFence,
        *,
        target: BackgroundBranchBindingStatus,
        allowed: frozenset[BackgroundBranchBindingStatus],
        advance_revocation: bool = False,
    ) -> BackgroundBranchBindingWriteResult:
        current = self._expected_binding(expected)
        if current.status is target:
            replacement = current
        elif current.status in allowed:
            replacement = _with_server_transition(
                current,
                status=target,
                revocation_generation=(
                    current.revocation_generation + 1
                    if advance_revocation
                    else current.revocation_generation
                ),
            )
        else:
            stale = self._preflight(expected)
            if stale is not None:
                return stale
            raise BackgroundBranchBindingTransitionError(
                "invalid_transition",
                f"{current.status.value} cannot transition to {target.value}",
            )
        stale = self._preflight(expected, replay=replacement)
        if stale is not None:
            return stale
        return self._compare_and_swap(expected, replacement)

    def _preflight(
        self,
        expected: BackgroundBranchBindingFence,
        *,
        replay: BackgroundBranchBinding | None = None,
    ) -> BackgroundBranchBindingWriteResult | None:
        current = self._store.get_binding(expected.expected_record.binding_id)
        if current is None:
            return BackgroundBranchBindingWriteResult(
                BackgroundBranchAuthorityWriteOutcome.MISSING,
                None,
            )
        if current == expected.expected_record:
            return None
        if replay is not None and current == replay:
            return BackgroundBranchBindingWriteResult(
                BackgroundBranchAuthorityWriteOutcome.REPLAYED,
                current,
            )
        outcome = (
            BackgroundBranchAuthorityWriteOutcome.GENERATION_MISMATCH
            if current.generation != expected.expected_record.generation
            else BackgroundBranchAuthorityWriteOutcome.CONFLICT
        )
        return BackgroundBranchBindingWriteResult(outcome, current)

    def _compare_and_swap(
        self,
        expected: BackgroundBranchBindingFence,
        replacement: BackgroundBranchBinding,
    ) -> BackgroundBranchBindingWriteResult:
        with self._store.transaction() as transaction:
            return transaction.compare_and_swap_binding(
                binding_id=expected.expected_record.binding_id,
                expected=expected,
                replacement=replacement,
            )


def _attempt_id_from_identity(
    binding_id: str,
    logical_attempt_key: str,
) -> str:
    identity = {
        "schema_version": 1,
        "binding_id": binding_id,
        "logical_attempt_key": logical_attempt_key,
    }
    return f"att_{_digest(identity).removeprefix('sha256:')[:32]}"


def _attempt_id(request: BackgroundBranchAttemptIssuanceRequest) -> str:
    return _attempt_id_from_identity(
        request.binding_id,
        request.logical_attempt_key,
    )


class BackgroundBranchAttemptIssuanceService:
    """Atomically revalidate and reserve one dark target attempt."""

    def __init__(
        self,
        store: BackgroundBranchAuthorityStore,
        resolver: BackgroundBranchAttemptResolver,
    ) -> None:
        if not isinstance(store, BackgroundBranchAuthorityStore):
            raise ValueError("store must implement BackgroundBranchAuthorityStore")
        if not isinstance(resolver, BackgroundBranchAttemptResolver):
            raise ValueError("resolver must implement BackgroundBranchAttemptResolver")
        self._store = store
        self._resolver = resolver

    def issue(
        self,
        request: BackgroundBranchAttemptIssuanceRequest,
    ) -> BackgroundBranchAttemptWriteResult:
        if not isinstance(request, BackgroundBranchAttemptIssuanceRequest):
            raise ValueError("request must be a BackgroundBranchAttemptIssuanceRequest")
        with self._store.transaction() as transaction:
            prior = transaction.get_attempt_by_logical_key(request.logical_attempt_key)
            if prior is not None:
                self._validate_replay(request, prior)
                return BackgroundBranchAttemptWriteResult(
                    BackgroundBranchAuthorityWriteOutcome.REPLAYED,
                    prior,
                )

            binding = transaction.get_binding(request.binding_id)
            if binding is None:
                self._fail("binding_missing", "binding does not exist")
            assert binding is not None
            self._validate_request_fence(request, binding)
            self._validate_active_binding(binding)

            resolution = self._resolver.resolve(request)
            if resolution is None:
                self._fail(
                    "attempt_resolution_missing",
                    "canonical authority is absent",
                )
            if not isinstance(
                resolution,
                BackgroundBranchAttemptIssuanceResolution,
            ):
                self._fail(
                    "attempt_resolution_invalid",
                    "resolver returned an invalid resolution",
                )
            assert isinstance(
                resolution,
                BackgroundBranchAttemptIssuanceResolution,
            )
            self._validate_resolution(request, binding, resolution)
            if transaction.count_attempts(binding_id=binding.binding_id) >= (binding.max_attempts):
                self._fail(
                    "binding_attempt_limit",
                    "binding has reached its maximum attempt count",
                )

            attempt_id = _attempt_id(request)
            child_source = binding.source_kind in {
                BackgroundBranchSourceKind.DIRECT_CHILD,
                BackgroundBranchSourceKind.PARENT_ATTEMPT,
            }
            if child_source != (
                resolution.parent_attempt_id is not None
                and resolution.origin_attempt_id is not None
            ):
                self._fail(
                    "lineage_mismatch",
                    "canonical lineage does not match the binding source",
                )
            origin_attempt_id = resolution.origin_attempt_id if child_source else attempt_id
            attempt = BackgroundBranchAttempt(
                schema_version=1,
                attempt_id=attempt_id,
                logical_attempt_key=request.logical_attempt_key,
                binding_id=binding.binding_id,
                binding_digest=binding.binding_digest,
                binding_generation=binding.generation,
                authorizing_principal_id=binding.authorizing_principal_id,
                universe_id=binding.universe_id,
                branch_def_id=binding.branch_def_id,
                branch_version_id=resolution.branch_version_id,
                branch_content_digest=resolution.branch_content_digest,
                operation=binding.operation,
                source_kind=binding.source_kind,
                source_id=binding.source_id,
                source_generation=resolution.source_generation,
                executor_audience=resolution.executor_audience,
                claim_generation=1,
                lease_generation=1,
                lease_expires_at=None,
                remaining_depth=binding.remaining_depth,
                remaining_count=binding.remaining_count,
                remaining_cost_microunits=binding.remaining_cost_microunits,
                lifecycle=BackgroundBranchAttemptLifecycle.RESERVED,
                hold_reason=None,
                terminal_reason=None,
                created_at=resolution.resolved_at,
                updated_at=resolution.resolved_at,
                provenance=BackgroundBranchProvenance(
                    authorizing_principal_id=binding.authorizing_principal_id,
                    source_kind=binding.source_kind,
                    source_id=binding.source_id,
                    executor_class=resolution.executor_audience.executor_class,
                    daemon_id=resolution.executor_audience.daemon_id,
                    runtime_id=resolution.executor_audience.runtime_id,
                    worker_id=resolution.executor_audience.worker_id,
                    parent_attempt_id=resolution.parent_attempt_id,
                    origin_attempt_id=origin_attempt_id,
                    audit_correlation_ids=resolution.audit_correlation_ids,
                    receipt_refs=resolution.receipt_refs,
                ),
            )
            return transaction.insert_attempt(attempt)

    @staticmethod
    def _fail(code: str, detail: str) -> None:
        raise BackgroundBranchAttemptIssuanceError(code, detail)

    def _validate_request_fence(
        self,
        request: BackgroundBranchAttemptIssuanceRequest,
        binding: BackgroundBranchBinding,
    ) -> None:
        if request.binding_generation != binding.generation:
            self._fail(
                "binding_generation_mismatch",
                "binding generation is stale",
            )
        if request.binding_digest != binding.binding_digest:
            self._fail("binding_digest_mismatch", "binding digest is stale")

    def _validate_active_binding(
        self,
        binding: BackgroundBranchBinding,
    ) -> None:
        if binding.status is not BackgroundBranchBindingStatus.ACTIVE:
            self._fail(
                f"binding_{binding.status.value}",
                "binding is not active",
            )

    def _validate_resolution(
        self,
        request: BackgroundBranchAttemptIssuanceRequest,
        binding: BackgroundBranchBinding,
        resolution: BackgroundBranchAttemptIssuanceResolution,
    ) -> None:
        if resolution.binding != binding:
            self._fail(
                "canonical_binding_mismatch",
                "fresh canonical state does not match the stored binding",
            )
        resolved_at = _utc_timestamp(resolution.resolved_at, "resolved_at")
        if binding.expires_at is not None and resolved_at >= _utc_timestamp(
            binding.expires_at, "expires_at"
        ):
            self._fail("binding_expired", "binding has expired")
        if request.physical_universe_id != binding.universe_id:
            self._fail(
                "physical_universe_mismatch",
                "physical command center does not match the binding",
            )
        if resolution.executor_audience != request.executor_audience:
            self._fail(
                "executor_mismatch",
                "fresh executor audience does not match the request",
            )
        audience = resolution.executor_audience
        if audience.executor_class not in binding.permitted_executor_classes:
            self._fail(
                "executor_mismatch",
                "executor class is not permitted by the binding",
            )
        if binding.daemon_id is not None and audience.daemon_id != binding.daemon_id:
            self._fail(
                "executor_mismatch",
                "daemon does not match the binding",
            )
        if binding.runtime_id is not None and audience.runtime_id != binding.runtime_id:
            self._fail(
                "executor_mismatch",
                "runtime does not match the binding",
            )
        if resolution.source_generation != int(binding.source_revision):
            self._fail(
                "source_generation_mismatch",
                "source generation does not match the binding",
            )
        if (
            binding.target_mode is BackgroundBranchTargetMode.PINNED_VERSION
            and resolution.branch_version_id != binding.pinned_branch_version_id
        ):
            self._fail(
                "pinned_target_mismatch",
                "resolved target differs from the pinned binding target",
            )

    def _validate_replay(
        self,
        request: BackgroundBranchAttemptIssuanceRequest,
        prior: BackgroundBranchAttempt,
    ) -> None:
        if (
            prior.binding_id != request.binding_id
            or prior.binding_generation != request.binding_generation
            or prior.binding_digest != request.binding_digest
            or prior.universe_id != request.physical_universe_id
            or prior.executor_audience != request.executor_audience
        ):
            self._fail(
                "prior_attempt_mismatch",
                "logical key belongs to different issuance context",
            )


class BackgroundBranchAttemptClaimService:
    """Apply dark, exact-fence attempt claim lifecycle transitions."""

    def __init__(
        self,
        store: BackgroundBranchAuthorityStore,
        resolver: BackgroundBranchAttemptClaimResolver,
    ) -> None:
        if not isinstance(store, BackgroundBranchAuthorityStore):
            raise ValueError("store must implement BackgroundBranchAuthorityStore")
        if not isinstance(resolver, BackgroundBranchAttemptClaimResolver):
            raise ValueError("resolver must implement BackgroundBranchAttemptClaimResolver")
        self._store = store
        self._resolver = resolver

    def claim(
        self,
        *,
        expected: BackgroundBranchAttemptFence,
        executor_audience: BackgroundBranchExecutorAudience,
        claimed_at: str,
        lease_expires_at: str,
    ) -> BackgroundBranchAttemptWriteResult:
        current = self._expected(expected)
        if current.lifecycle is not BackgroundBranchAttemptLifecycle.RESERVED:
            self._fail("attempt_not_reserved", "only a reserved attempt can be claimed")
        if not isinstance(executor_audience, BackgroundBranchExecutorAudience):
            raise ValueError("executor_audience must be typed")
        if executor_audience != current.executor_audience:
            self._fail(
                "executor_mismatch",
                "ordinary claim cannot rotate its reserved audience",
            )
        replacement = self._replacement(
            current,
            executor_audience=executor_audience,
            claim_generation=current.claim_generation,
            lease_generation=current.lease_generation + 1,
            lease_expires_at=lease_expires_at,
            lifecycle=BackgroundBranchAttemptLifecycle.CLAIMED,
            updated_at=claimed_at,
        )
        return self._compare_and_swap(
            expected,
            replacement,
            action=BackgroundBranchAttemptClaimAction.CLAIM,
            requested_audience=executor_audience,
            transitioned_at=claimed_at,
            requested_lease_expires_at=lease_expires_at,
        )

    def renew(
        self,
        *,
        expected: BackgroundBranchAttemptFence,
        executor_audience: BackgroundBranchExecutorAudience,
        renewed_at: str,
        lease_expires_at: str,
    ) -> BackgroundBranchAttemptWriteResult:
        current = self._expected(expected)
        if current.lifecycle not in {
            BackgroundBranchAttemptLifecycle.CLAIMED,
            BackgroundBranchAttemptLifecycle.RUNNING,
        }:
            self._fail("attempt_not_claimed", "only claimed or running attempts renew")
        if executor_audience != current.executor_audience:
            self._fail(
                "executor_mismatch",
                "only the current claimed executor may renew",
            )
        replacement = replace(
            current,
            lease_generation=current.lease_generation + 1,
            lease_expires_at=lease_expires_at,
            updated_at=renewed_at,
        )
        return self._compare_and_swap(
            expected,
            replacement,
            action=BackgroundBranchAttemptClaimAction.RENEW,
            requested_audience=executor_audience,
            transitioned_at=renewed_at,
            requested_lease_expires_at=lease_expires_at,
        )

    def release(
        self,
        *,
        expected: BackgroundBranchAttemptFence,
        executor_audience: BackgroundBranchExecutorAudience,
        released_at: str,
    ) -> BackgroundBranchAttemptWriteResult:
        current = self._expected(expected)
        if current.lifecycle is not BackgroundBranchAttemptLifecycle.CLAIMED:
            self._fail(
                "attempt_not_releasable",
                "only a claimed pre-execution attempt can be released",
            )
        if executor_audience != current.executor_audience:
            self._fail(
                "executor_mismatch",
                "only the current claimed executor may release",
            )
        replacement = replace(
            current,
            claim_generation=current.claim_generation + 1,
            lease_generation=current.lease_generation + 1,
            lease_expires_at=None,
            lifecycle=BackgroundBranchAttemptLifecycle.RESERVED,
            updated_at=released_at,
        )
        return self._compare_and_swap(
            expected,
            replacement,
            action=BackgroundBranchAttemptClaimAction.RELEASE,
            requested_audience=executor_audience,
            transitioned_at=released_at,
            requested_lease_expires_at=None,
        )

    def reclaim(
        self,
        *,
        expected: BackgroundBranchAttemptFence,
        replacement_audience: BackgroundBranchExecutorAudience,
        reclaimed_at: str,
    ) -> BackgroundBranchAttemptWriteResult:
        current = self._expected(expected)
        if not isinstance(
            replacement_audience,
            BackgroundBranchExecutorAudience,
        ):
            raise ValueError("replacement_audience must be typed")
        if current.lifecycle not in {
            BackgroundBranchAttemptLifecycle.CLAIMED,
            BackgroundBranchAttemptLifecycle.RUNNING,
        }:
            self._fail(
                "attempt_not_reclaimable",
                "only claimed or running attempts can be reclaimed",
            )
        current_domain = (
            current.executor_audience.executor_class,
            current.executor_audience.daemon_id,
            current.executor_audience.runtime_id,
        )
        replacement_domain = (
            replacement_audience.executor_class,
            replacement_audience.daemon_id,
            replacement_audience.runtime_id,
        )
        if replacement_domain != current_domain:
            self._fail(
                "executor_domain_mismatch",
                "recovery may rotate only the worker inside the reserved executor domain",
            )
        replacement = self._replacement(
            current,
            executor_audience=replacement_audience,
            claim_generation=current.claim_generation + 1,
            lease_generation=current.lease_generation + 1,
            lease_expires_at=None,
            lifecycle=BackgroundBranchAttemptLifecycle.RESERVED,
            updated_at=reclaimed_at,
        )
        return self._compare_and_swap(
            expected,
            replacement,
            action=BackgroundBranchAttemptClaimAction.RECLAIM,
            requested_audience=replacement_audience,
            transitioned_at=reclaimed_at,
            requested_lease_expires_at=None,
            require_conclusive_recovery=True,
        )

    @staticmethod
    def _expected(
        expected: BackgroundBranchAttemptFence,
    ) -> BackgroundBranchAttempt:
        if not isinstance(expected, BackgroundBranchAttemptFence):
            raise ValueError("expected must be a BackgroundBranchAttemptFence")
        return expected.expected_record

    @staticmethod
    def _replacement(
        current: BackgroundBranchAttempt,
        *,
        executor_audience: BackgroundBranchExecutorAudience,
        claim_generation: int,
        lease_generation: int,
        lease_expires_at: str | None,
        lifecycle: BackgroundBranchAttemptLifecycle,
        updated_at: str,
    ) -> BackgroundBranchAttempt:
        provenance = replace(
            current.provenance,
            executor_class=executor_audience.executor_class,
            daemon_id=executor_audience.daemon_id,
            runtime_id=executor_audience.runtime_id,
            worker_id=executor_audience.worker_id,
        )
        return replace(
            current,
            executor_audience=executor_audience,
            claim_generation=claim_generation,
            lease_generation=lease_generation,
            lease_expires_at=lease_expires_at,
            lifecycle=lifecycle,
            updated_at=updated_at,
            provenance=provenance,
        )

    def _compare_and_swap(
        self,
        expected: BackgroundBranchAttemptFence,
        replacement: BackgroundBranchAttempt,
        *,
        action: BackgroundBranchAttemptClaimAction,
        requested_audience: BackgroundBranchExecutorAudience,
        transitioned_at: str,
        requested_lease_expires_at: str | None,
        require_conclusive_recovery: bool = False,
    ) -> BackgroundBranchAttemptWriteResult:
        with self._store.transaction() as transaction:
            resolution = self._resolve(
                expected.expected_record,
                action,
                requested_audience,
                transitioned_at,
                requested_lease_expires_at,
            )
            binding = transaction.get_binding(expected.expected_record.binding_id)
            self._validate_binding(
                expected.expected_record,
                binding,
                resolution,
            )
            if require_conclusive_recovery and (
                resolution.predecessor
                not in {
                    BackgroundBranchAttemptPredecessorState.DEAD,
                    BackgroundBranchAttemptPredecessorState.INVALIDATED,
                }
                or resolution.boundary
                not in {
                    BackgroundBranchAttemptBoundaryState.NOT_CROSSED,
                    BackgroundBranchAttemptBoundaryState.CLOSED,
                }
            ):
                self._fail(
                    "recovery_not_conclusive",
                    "reclaim requires conclusive predecessor and boundary proof",
                )
            return transaction.compare_and_swap_attempt(
                attempt_id=expected.expected_record.attempt_id,
                expected=expected,
                replacement=replacement,
            )

    def _resolve(
        self,
        current: BackgroundBranchAttempt,
        action: BackgroundBranchAttemptClaimAction,
        requested_audience: BackgroundBranchExecutorAudience,
        transitioned_at: str,
        requested_lease_expires_at: str | None,
    ) -> BackgroundBranchAttemptClaimResolution:
        if not isinstance(
            requested_audience,
            BackgroundBranchExecutorAudience,
        ):
            raise ValueError("executor_audience must be typed")
        resolution = self._resolver.resolve(
            BackgroundBranchAttemptClaimRequest(
                attempt=current,
                action=action,
                requested_audience=requested_audience,
                transitioned_at=transitioned_at,
                requested_lease_expires_at=requested_lease_expires_at,
            )
        )
        if resolution is None:
            self._fail("claim_resolution_missing", "canonical authority is absent")
        if not isinstance(resolution, BackgroundBranchAttemptClaimResolution):
            self._fail(
                "claim_resolution_invalid",
                "resolver returned invalid canonical evidence",
            )
        assert isinstance(resolution, BackgroundBranchAttemptClaimResolution)
        if resolution.executor_audience != requested_audience:
            self._fail(
                "executor_mismatch",
                "resolved executor audience does not match the request",
            )
        return resolution

    def _validate_binding(
        self,
        attempt: BackgroundBranchAttempt,
        binding: BackgroundBranchBinding | None,
        resolution: BackgroundBranchAttemptClaimResolution,
    ) -> None:
        if binding is None:
            self._fail("binding_missing", "attempt binding does not exist")
        assert binding is not None
        if binding != resolution.binding:
            self._fail(
                "canonical_binding_mismatch",
                "fresh canonical binding does not match the atomic store snapshot",
            )
        if binding.status is not BackgroundBranchBindingStatus.ACTIVE:
            self._fail(
                f"binding_{binding.status.value}",
                "attempt binding is not active",
            )
        if (
            binding.binding_id != attempt.binding_id
            or binding.binding_digest != attempt.binding_digest
            or binding.generation != attempt.binding_generation
        ):
            self._fail(
                "binding_fence_mismatch",
                "attempt was issued under a stale binding fence",
            )
        if (
            binding.authorizing_principal_id != attempt.authorizing_principal_id
            or binding.universe_id != attempt.universe_id
            or binding.branch_def_id != attempt.branch_def_id
            or binding.operation is not attempt.operation
            or binding.source_kind is not attempt.source_kind
            or binding.source_id != attempt.source_id
        ):
            self._fail(
                "binding_authority_mismatch",
                "attempt authority differs from its current binding",
            )
        resolved_at = _utc_timestamp(resolution.resolved_at, "resolved_at")
        if binding.expires_at is not None and resolved_at >= _utc_timestamp(
            binding.expires_at, "expires_at"
        ):
            self._fail("binding_expired", "attempt binding has expired")
        audience = resolution.executor_audience
        if audience.executor_class not in binding.permitted_executor_classes:
            self._fail("executor_mismatch", "executor class is not permitted")
        if binding.daemon_id is not None and audience.daemon_id != binding.daemon_id:
            self._fail("executor_mismatch", "daemon is not permitted")
        if binding.runtime_id is not None and audience.runtime_id != binding.runtime_id:
            self._fail("executor_mismatch", "runtime is not permitted")

    @staticmethod
    def _fail(code: str, detail: str) -> None:
        raise BackgroundBranchAttemptClaimError(code, detail)


class BackgroundBranchAuthorityHoldService:
    """Generation-fenced dark holds for queue rows and source-owned work."""

    def __init__(
        self,
        store: BackgroundBranchAuthorityOwnerStore,
        resolver: BackgroundBranchAuthorityExitResolver,
    ) -> None:
        if not isinstance(store, BackgroundBranchAuthorityOwnerStore):
            raise ValueError("store must implement BackgroundBranchAuthorityOwnerStore")
        if not isinstance(resolver, BackgroundBranchAuthorityExitResolver):
            raise ValueError(
                "resolver must implement BackgroundBranchAuthorityExitResolver"
            )
        self._store = store
        self._resolver = resolver

    def hold(
        self,
        *,
        expected: BackgroundBranchAuthorityOwnerFence,
        failure: BackgroundBranchAuthorityFailureKind,
        held_at: str,
    ) -> BackgroundBranchAuthorityOwnerWriteResult:
        current = self._expected(expected)
        if (
            current.state
            is BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD
        ):
            self._fail("already_held", "authority owner is already held")
        if not isinstance(failure, BackgroundBranchAuthorityFailureKind):
            raise ValueError("failure must be typed")
        _utc_timestamp(held_at, "held_at")
        self._validate_hold_failure(current, failure, held_at)
        replacement = replace(
            current,
            transition_generation=current.transition_generation + 1,
            state=BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD,
            hold_reason=_FAILURE_HOLD_REASONS[failure],
            updated_at=held_at,
        )
        return self._store.compare_and_swap(
            expected=expected,
            replacement=replacement,
        )

    def recover(
        self,
        *,
        expected: BackgroundBranchAuthorityOwnerFence,
        recovered_at: str,
    ) -> BackgroundBranchAuthorityOwnerWriteResult:
        current = self._held(expected)
        if (
            current.hold_reason
            is not BackgroundBranchHoldReason.INDETERMINATE_PRIOR_ATTEMPT
        ):
            self._fail(
                "reauthorization_required",
                "this authority failure requires authenticated repair",
            )
        resolution = self._resolve(
            current,
            BackgroundBranchAuthorityExitAction.RECOVER,
            authentication_context_id=None,
            transitioned_at=recovered_at,
        )
        self._require_conclusive_reconciliation(resolution)
        self._validate_active_binding(resolution.binding, resolution.resolved_at)
        self._validate_same_authority(current, resolution)
        assert resolution.attempt is not None
        replacement = replace(
            current,
            transition_generation=current.transition_generation + 1,
            state=self._resume_state(current.owner_kind),
            attempt=BackgroundBranchAttemptFence(resolution.attempt),
            hold_reason=None,
            updated_at=recovered_at,
        )
        return self._store.compare_and_swap(
            expected=expected,
            replacement=replacement,
        )

    def reauthorize(
        self,
        *,
        expected: BackgroundBranchAuthorityOwnerFence,
        authentication_context_id: str,
        reauthorized_at: str,
    ) -> BackgroundBranchAuthorityOwnerWriteResult:
        current = self._held(expected)
        resolution = self._resolve(
            current,
            BackgroundBranchAuthorityExitAction.REAUTHORIZE,
            authentication_context_id=authentication_context_id,
            transitioned_at=reauthorized_at,
        )
        if (
            current.hold_reason
            is BackgroundBranchHoldReason.INDETERMINATE_PRIOR_ATTEMPT
        ):
            self._require_conclusive_reconciliation(resolution)
        if (
            resolution.authenticated_principal_id
            != current.authorizing_principal_id
            and not resolution.is_universe_admin
        ):
            self._fail(
                "reauthorization_not_authorized",
                "reauthorization requires the canonical principal or command center admin",
            )
        binding = resolution.binding
        self._validate_active_binding(binding, resolution.resolved_at)
        if (
            binding.universe_id != current.universe_id
            or binding.authorizing_principal_id != current.authorizing_principal_id
        ):
            self._fail(
                "binding_authority_mismatch",
                "reauthorized binding changed the authority owner",
            )
        if current.binding is None:
            self._fail(
                "binding_rotation_unprovable",
                "authenticated repair requires the held binding fence",
            )
        held_binding = current.binding.expected_record
        if (
            binding.binding_id != held_binding.binding_id
            or binding.source_kind is not held_binding.source_kind
            or binding.source_id != held_binding.source_id
        ):
            self._fail(
                "binding_lineage_mismatch",
                "authenticated repair cannot replace the binding source lineage",
            )
        if binding.generation <= held_binding.generation:
            self._fail(
                "binding_not_rotated",
                "authenticated repair must advance the binding generation",
            )
        attempt = resolution.attempt
        if (
            current.attempt is not None
            and attempt is not None
            and attempt.attempt_id
            == current.attempt.expected_record.attempt_id
        ):
            self._fail(
                "stale_attempt_revived",
                "reauthorization cannot revive the held attempt",
            )
        if current.owner_kind is BackgroundBranchAuthorityOwnerKind.QUEUE_TASK:
            if attempt is None:
                self._fail(
                    "attempt_missing",
                    "queue reauthorization requires a fresh reserved attempt",
                )
            assert attempt is not None
            self._validate_attempt(binding, attempt, current.universe_id)
            self._validate_fresh_attempt(attempt, resolution.resolved_at)
        elif attempt is not None:
            self._validate_attempt(binding, attempt, current.universe_id)
            self._validate_fresh_attempt(attempt, resolution.resolved_at)
        replacement = replace(
            current,
            source_generation=(
                attempt.source_generation
                if attempt is not None
                else int(binding.source_revision)
            ),
            transition_generation=current.transition_generation + 1,
            state=self._resume_state(current.owner_kind),
            binding=BackgroundBranchBindingFence(binding),
            attempt=(
                BackgroundBranchAttemptFence(attempt)
                if attempt is not None
                else None
            ),
            hold_reason=None,
            updated_at=reauthorized_at,
        )
        return self._store.compare_and_swap(
            expected=expected,
            replacement=replacement,
        )

    @staticmethod
    def _expected(
        expected: BackgroundBranchAuthorityOwnerFence,
    ) -> BackgroundBranchAuthorityOwnerRecord:
        if not isinstance(expected, BackgroundBranchAuthorityOwnerFence):
            raise ValueError("expected must be a BackgroundBranchAuthorityOwnerFence")
        return expected.expected_record

    def _held(
        self,
        expected: BackgroundBranchAuthorityOwnerFence,
    ) -> BackgroundBranchAuthorityOwnerRecord:
        current = self._expected(expected)
        if (
            current.state
            is not BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD
        ):
            self._fail("owner_not_held", "only held authority owners may exit")
        return current

    def _resolve(
        self,
        current: BackgroundBranchAuthorityOwnerRecord,
        action: BackgroundBranchAuthorityExitAction,
        *,
        authentication_context_id: str | None,
        transitioned_at: str,
    ) -> BackgroundBranchAuthorityExitResolution:
        resolution = self._resolver.resolve(
            BackgroundBranchAuthorityExitRequest(
                owner=current,
                action=action,
                authentication_context_id=authentication_context_id,
                transitioned_at=transitioned_at,
            )
        )
        if not isinstance(resolution, BackgroundBranchAuthorityExitResolution):
            self._fail(
                "exit_resolution_missing",
                "fresh canonical exit evidence is absent",
            )
        return resolution

    def _validate_same_authority(
        self,
        current: BackgroundBranchAuthorityOwnerRecord,
        resolution: BackgroundBranchAuthorityExitResolution,
    ) -> None:
        binding = resolution.binding
        if (
            current.binding is None
            or binding != current.binding.expected_record
        ):
            self._fail(
                "recovery_rotated_binding",
                "automatic recovery cannot rotate target authority",
            )
        attempt = resolution.attempt
        if (
            current.attempt is None
            or attempt is None
            or attempt.attempt_id
            != current.attempt.expected_record.attempt_id
            or attempt.claim_generation
            <= current.attempt.expected_record.claim_generation
        ):
            self._fail(
                "recovery_rotated_attempt",
                "automatic recovery must advance the same attempt claim",
            )
        self._validate_recovered_attempt(
            current.attempt.expected_record,
            attempt,
        )
        self._validate_attempt(binding, attempt, current.universe_id)

    def _validate_hold_failure(
        self,
        current: BackgroundBranchAuthorityOwnerRecord,
        failure: BackgroundBranchAuthorityFailureKind,
        held_at: str,
    ) -> None:
        binding = (
            current.binding.expected_record
            if current.binding is not None
            else None
        )
        observed_failure = None
        if binding is not None:
            if binding.status is BackgroundBranchBindingStatus.REVOKED:
                observed_failure = BackgroundBranchAuthorityFailureKind.REVOKED
            elif (
                binding.status is BackgroundBranchBindingStatus.EXPIRED
                or (
                    binding.expires_at is not None
                    and _utc_timestamp(binding.expires_at, "binding.expires_at")
                    <= _utc_timestamp(held_at, "held_at")
                )
            ):
                observed_failure = BackgroundBranchAuthorityFailureKind.EXPIRED
            elif binding.status is BackgroundBranchBindingStatus.EXHAUSTED:
                observed_failure = BackgroundBranchAuthorityFailureKind.EXHAUSTED
        binding_status_failures = {
            BackgroundBranchAuthorityFailureKind.REVOKED,
            BackgroundBranchAuthorityFailureKind.EXPIRED,
            BackgroundBranchAuthorityFailureKind.EXHAUSTED,
        }
        if (
            observed_failure is not None and failure is not observed_failure
        ) or (
            failure in binding_status_failures and failure is not observed_failure
        ):
            self._fail(
                "hold_failure_mismatch",
                "hold failure does not match the fenced binding state",
            )

    def _validate_active_binding(
        self,
        binding: BackgroundBranchBinding,
        resolved_at: str,
    ) -> None:
        if binding.status is not BackgroundBranchBindingStatus.ACTIVE:
            self._fail("binding_not_active", "resumed binding must be active")
        if (
            binding.expires_at is not None
            and _utc_timestamp(binding.expires_at, "binding.expires_at")
            <= _utc_timestamp(resolved_at, "resolution.resolved_at")
        ):
            self._fail("binding_expired", "resumed binding has expired")

    def _validate_recovered_attempt(
        self,
        previous: BackgroundBranchAttempt,
        recovered: BackgroundBranchAttempt,
    ) -> None:
        if (
            recovered.claim_generation <= previous.claim_generation
            or recovered.lease_generation <= previous.lease_generation
            or recovered.lease_expires_at is not None
        ):
            self._fail(
                "recovery_attempt_mutated",
                "recovery must advance claim/lease fences and clear the lease",
            )
        if replace(
            recovered.executor_audience,
            worker_id=previous.executor_audience.worker_id,
        ) != previous.executor_audience:
            self._fail(
                "recovery_attempt_mutated",
                "recovery cannot change the executor domain",
            )
        if replace(
            recovered.provenance,
            worker_id=previous.provenance.worker_id,
        ) != previous.provenance:
            self._fail(
                "recovery_attempt_mutated",
                "recovery cannot change attempt provenance except worker",
            )
        normalized = replace(
            recovered,
            executor_audience=previous.executor_audience,
            claim_generation=previous.claim_generation,
            lease_generation=previous.lease_generation,
            lease_expires_at=previous.lease_expires_at,
            lifecycle=previous.lifecycle,
            updated_at=previous.updated_at,
            provenance=previous.provenance,
        )
        if normalized != previous:
            self._fail(
                "recovery_attempt_mutated",
                "recovery changed immutable attempt authority",
            )

    def _require_conclusive_reconciliation(
        self,
        resolution: BackgroundBranchAuthorityExitResolution,
    ) -> None:
        if (
            resolution.predecessor
            not in {
                BackgroundBranchAttemptPredecessorState.DEAD,
                BackgroundBranchAttemptPredecessorState.INVALIDATED,
            }
            or resolution.boundary
            not in {
                BackgroundBranchAttemptBoundaryState.NOT_CROSSED,
                BackgroundBranchAttemptBoundaryState.CLOSED,
            }
        ):
            self._fail(
                "recovery_not_conclusive",
                "recovery requires a dead/invalidated predecessor and conclusive boundary",
            )

    @staticmethod
    def _resume_state(
        owner_kind: BackgroundBranchAuthorityOwnerKind,
    ) -> BackgroundBranchAuthorityOwnerState:
        if owner_kind is BackgroundBranchAuthorityOwnerKind.QUEUE_TASK:
            return BackgroundBranchAuthorityOwnerState.PENDING
        return BackgroundBranchAuthorityOwnerState.ACTIVE

    @staticmethod
    def _validate_attempt(
        binding: BackgroundBranchBinding,
        attempt: BackgroundBranchAttempt,
        universe_id: str,
    ) -> None:
        if (
            attempt.binding_id != binding.binding_id
            or attempt.binding_digest != binding.binding_digest
            or attempt.binding_generation != binding.generation
            or attempt.universe_id != universe_id
            or attempt.authorizing_principal_id
            != binding.authorizing_principal_id
            or attempt.branch_def_id != binding.branch_def_id
            or attempt.operation is not binding.operation
            or attempt.source_kind is not binding.source_kind
            or attempt.source_id != binding.source_id
            or attempt.source_generation != int(binding.source_revision)
            or attempt.executor_audience.executor_class
            not in binding.permitted_executor_classes
            or (
                binding.daemon_id is not None
                and attempt.executor_audience.daemon_id != binding.daemon_id
            )
            or (
                binding.runtime_id is not None
                and attempt.executor_audience.runtime_id != binding.runtime_id
            )
            or (
                binding.target_mode is BackgroundBranchTargetMode.PINNED_VERSION
                and attempt.branch_version_id != binding.pinned_branch_version_id
            )
            or attempt.remaining_depth > binding.remaining_depth
            or attempt.remaining_count > binding.remaining_count
            or attempt.remaining_cost_microunits
            > binding.remaining_cost_microunits
        ):
            raise BackgroundBranchAuthorityHoldError(
                "attempt_authority_mismatch",
                "attempt does not match the resolved binding and command center",
            )
        if attempt.lifecycle is not BackgroundBranchAttemptLifecycle.RESERVED:
            raise BackgroundBranchAuthorityHoldError(
                "attempt_not_reserved",
                "held work may resume only with a reserved attempt",
            )

    @staticmethod
    def _validate_fresh_attempt(
        attempt: BackgroundBranchAttempt,
        resolved_at: str,
    ) -> None:
        if (
            attempt.attempt_id
            != _attempt_id_from_identity(
                attempt.binding_id,
                attempt.logical_attempt_key,
            )
            or attempt.claim_generation != 1
            or attempt.lease_generation != 1
            or attempt.created_at != resolved_at
            or attempt.updated_at != resolved_at
        ):
            raise BackgroundBranchAuthorityHoldError(
                "attempt_issuance_mismatch",
                "reauthorization attempt was not canonically issued",
            )

    @staticmethod
    def _fail(code: str, detail: str) -> None:
        raise BackgroundBranchAuthorityHoldError(code, detail)


__all__ = [
    "BackgroundBranchAuthorityExitAction",
    "BackgroundBranchAuthorityExitRequest",
    "BackgroundBranchAuthorityExitResolution",
    "BackgroundBranchAuthorityExitResolver",
    "BackgroundBranchAuthorityFailureKind",
    "BackgroundBranchAuthorityHoldError",
    "BackgroundBranchAuthorityHoldProjection",
    "BackgroundBranchAuthorityHoldService",
    "BackgroundBranchAuthorityOwnerFence",
    "BackgroundBranchAuthorityOwnerKind",
    "BackgroundBranchAuthorityOwnerRecord",
    "BackgroundBranchAuthorityOwnerState",
    "BackgroundBranchAuthorityOwnerStore",
    "BackgroundBranchAuthorityOwnerWriteResult",
    "BackgroundBranchAttemptBoundaryState",
    "BackgroundBranchAttemptClaimAction",
    "BackgroundBranchAttemptClaimError",
    "BackgroundBranchAttemptClaimRequest",
    "BackgroundBranchAttemptClaimResolution",
    "BackgroundBranchAttemptClaimResolver",
    "BackgroundBranchAttemptClaimService",
    "BackgroundBranchAttemptIssuanceError",
    "BackgroundBranchAttemptIssuanceRequest",
    "BackgroundBranchAttemptIssuanceResolution",
    "BackgroundBranchAttemptIssuanceService",
    "BackgroundBranchAttemptPredecessorState",
    "BackgroundBranchAttemptResolver",
    "BackgroundBranchBindingResolver",
    "BackgroundBranchBindingRoot",
    "BackgroundBranchBindingSeed",
    "BackgroundBranchBindingTransitionError",
    "BackgroundBranchBindingTransitionService",
    "project_background_branch_authority_hold",
]
