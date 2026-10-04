"""Assigned-provider authority for daemon-owned background Branch execution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import sqlite3
import threading
from contextlib import contextmanager
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterator

from tinyassets.background_branch_authority import (
    BackgroundBranchAttemptLifecycle,
    BackgroundBranchBindingStatus,
    BackgroundBranchExecutorClass,
)
from tinyassets.branch_tasks_v2 import AssignedConsumerLease, Epoch2BranchTask
from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind
from tinyassets.platform_runtime_provenance import (
    admitted_cloud_executor_class as _admitted_cloud_class,
)
from tinyassets.platform_runtime_provenance import (
    platform_not_cloud_message,
    resolve_process_cloud_admission,
)
from tinyassets.provider_assignment import (
    load_provider_assignment_in_transaction,
    provider_assignment_admission,
)
from tinyassets.provider_work_authority import (
    ProviderInvocationCarrier,
    ProviderInvocationSelection,
    ProviderUniverseWorkAuthority,
    ProviderUniverseWorkReceipt,
    ProviderUniverseWorkRoot,
    ProviderWorkAuthorityWriteOutcome,
    ProviderWorkBindingFence,
    ProviderWorkBindingRoot,
    ProviderWorkBindingSeed,
    ProviderWorkBindingService,
    ProviderWorkBindingState,
    ProviderWorkReceiptState,
    provider_work_receipt_id,
)
from tinyassets.runtime.claimed_branch_execution import ClaimedBranchExecutorIdentity

BACKGROUND_BRANCH_RUN_OPERATION = "background_branch_run"
_DEFAULT_MAX_INVOCATIONS = 64
_DEFAULT_MAX_TOKENS = 250_000
_DEFAULT_MAX_COST_MICROUNITS = 25_000_000
_SUPPORTED_BRANCH_ROLES = frozenset({"writer", "judge", "extract", "embed"})
logger = logging.getLogger(__name__)


class BackgroundExecutorIdentityError(PermissionError):
    """A claimed task has no current owner-authorized executor identity."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


def load_background_executor_identity(
    base_path: str | Path,
    claimed_task: Epoch2BranchTask,
    consumer_lease: AssignedConsumerLease,
    *,
    heartbeat: Callable[[], None] | None = None,
) -> ClaimedBranchExecutorIdentity:
    """Reuse the daemon/runtime identity authorized by the background binding."""

    from tinyassets.cloud_automation_continuation import build_request_task_attempt_key
    from tinyassets.storage.background_branch_authority import (
        SQLiteBackgroundBranchAuthorityStore,
    )
    from tinyassets.storage.request_admissions import RequestAdmissionStore

    if claimed_task.claimed_by != consumer_lease.consumer_id:
        raise BackgroundExecutorIdentityError("background_consumer_lease_mismatch")
    root = Path(base_path)
    admission_store = RequestAdmissionStore(root)
    background_store = SQLiteBackgroundBranchAuthorityStore(root)
    with admission_store.connection() as conn:
        conn.execute("BEGIN")
        row = conn.execute(
            """
            SELECT body_digest, grant_generation
            FROM request_admissions
            WHERE admission_id = ? AND request_id = ? AND branch_task_id = ?
            """,
            (
                claimed_task.admission_id,
                claimed_task.request_id,
                claimed_task.branch_task_id,
            ),
        ).fetchone()
        if row is None:
            conn.rollback()
            raise BackgroundExecutorIdentityError("background_binding_absent")
        logical_key = build_request_task_attempt_key(
            tenant_id=claimed_task.actor_id,
            request_id=claimed_task.request_id,
            admission_id=claimed_task.admission_id,
            task_id=claimed_task.branch_task_id,
            body_digest=str(row["body_digest"]),
            admission_generation=int(row["grant_generation"]),
        )
        authority = background_store.read_authority_in_transaction(
            conn,
            logical_attempt_key=logical_key,
        )
        conn.rollback()
    if authority is None:
        raise BackgroundExecutorIdentityError("background_binding_absent")
    binding, attempt = authority
    now = datetime.now(timezone.utc)
    if binding.status is not BackgroundBranchBindingStatus.ACTIVE:
        raise BackgroundExecutorIdentityError("background_binding_inactive")
    if binding.expires_at is None or _utc(binding.expires_at) <= now:
        raise BackgroundExecutorIdentityError("background_binding_expired")
    if attempt.lifecycle not in {
        BackgroundBranchAttemptLifecycle.CLAIMED,
        BackgroundBranchAttemptLifecycle.RUNNING,
    }:
        raise BackgroundExecutorIdentityError("background_attempt_inactive")
    if attempt.lease_expires_at is None or _utc(attempt.lease_expires_at) <= now:
        raise BackgroundExecutorIdentityError("background_attempt_lease_expired")
    if (
        binding.universe_id != claimed_task.universe_id
        or binding.branch_def_id != claimed_task.branch_def_id
        or binding.pinned_branch_version_id != claimed_task.automation_branch_version
    ):
        raise BackgroundExecutorIdentityError("background_binding_target_mismatch")
    if binding.authorizing_principal_id != claimed_task.actor_id:
        raise BackgroundExecutorIdentityError("background_binding_principal_mismatch")
    if BackgroundBranchExecutorClass.CLOUD not in binding.permitted_executor_classes:
        raise BackgroundExecutorIdentityError("background_binding_executor_class_unavailable")
    if not binding.daemon_id or not binding.runtime_id:
        raise BackgroundExecutorIdentityError("background_binding_executor_identity_missing")
    return ClaimedBranchExecutorIdentity(
        daemon_id=binding.daemon_id,
        worker_id=consumer_lease.consumer_id,
        runtime_instance_id=binding.runtime_id,
        heartbeat=heartbeat,
    )


def _hold_background_authority(base_path: Path, task: Epoch2BranchTask) -> None:
    """Project the exact queue authority owner to a retryable held state."""

    from tinyassets.background_branch_authority import BackgroundBranchBindingStatus
    from tinyassets.background_branch_authority_service import (
        BackgroundBranchAuthorityFailureKind,
        BackgroundBranchAuthorityHoldService,
        BackgroundBranchAuthorityOwnerFence,
        BackgroundBranchAuthorityOwnerKind,
        BackgroundBranchAuthorityOwnerState,
    )
    from tinyassets.storage.background_branch_authority import (
        SQLiteBackgroundBranchAuthorityStore,
    )

    class _ExitResolver:
        def resolve(self, _request: Any) -> None:
            return None

    store = SQLiteBackgroundBranchAuthorityStore(base_path)
    owner = store.get_owner(
        owner_kind=BackgroundBranchAuthorityOwnerKind.QUEUE_TASK,
        owner_id=task.branch_task_id,
    )
    if owner is None or (owner.state is BackgroundBranchAuthorityOwnerState.TARGET_AUTHORITY_HELD):
        return
    failure = BackgroundBranchAuthorityFailureKind.UNAUTHORIZED
    binding = owner.binding.expected_record if owner.binding is not None else None
    held_at = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    if binding is None:
        failure = BackgroundBranchAuthorityFailureKind.MISSING
    elif binding.status is BackgroundBranchBindingStatus.REVOKED:
        failure = BackgroundBranchAuthorityFailureKind.REVOKED
    elif binding.status is BackgroundBranchBindingStatus.EXHAUSTED:
        failure = BackgroundBranchAuthorityFailureKind.EXHAUSTED
    elif (
        binding.status is BackgroundBranchBindingStatus.EXPIRED
        or binding.expires_at is not None
        and _utc(binding.expires_at) <= _utc(held_at)
    ):
        failure = BackgroundBranchAuthorityFailureKind.EXPIRED
    service = BackgroundBranchAuthorityHoldService(store, _ExitResolver())
    service.hold(
        expected=BackgroundBranchAuthorityOwnerFence(owner),
        failure=failure,
        held_at=held_at,
    )


def _positive_env(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be an integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be positive")
    return value


def _utc(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc)


def _background_timestamp(value: str | datetime, *after: str) -> str:
    parsed = value if isinstance(value, datetime) else _utc(value)
    parsed = parsed.astimezone(timezone.utc)
    for prior in after:
        prior_value = _utc(prior)
        if parsed <= prior_value:
            parsed = prior_value + timedelta(microseconds=1)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def claim_background_queue_authority_in_transaction(
    conn: sqlite3.Connection,
    task: Epoch2BranchTask,
    consumer_lease: AssignedConsumerLease,
    *,
    claimed_at: str,
    lease_expires_at: str,
) -> bool:
    """Claim the canonical attempt and create its queue owner with the task CAS."""

    reason = explain_background_queue_authority_in_transaction(
        conn,
        task,
        consumer_lease,
        claimed_at=claimed_at,
        lease_expires_at=lease_expires_at,
    )
    if reason is not None:
        return False

    from tinyassets.background_branch_authority import (
        BackgroundBranchAttemptFence,
        BackgroundBranchBindingFence,
    )
    from tinyassets.background_branch_authority_service import (
        BackgroundBranchAuthorityOwnerKind,
        BackgroundBranchAuthorityOwnerRecord,
        BackgroundBranchAuthorityOwnerState,
    )
    from tinyassets.cloud_automation_continuation import build_request_task_attempt_key
    from tinyassets.storage.background_branch_authority import (
        SQLiteBackgroundBranchAuthorityStore,
        _SQLiteBackgroundBranchAuthorityTransaction,
    )

    row = conn.execute(
        """
        SELECT body_digest, grant_generation, actor_id
        FROM request_admissions
        WHERE admission_id = ? AND request_id = ? AND branch_task_id = ?
        """,
        (task.admission_id, task.request_id, task.branch_task_id),
    ).fetchone()
    assert row is not None
    logical_key = build_request_task_attempt_key(
        tenant_id=str(row["actor_id"]),
        request_id=task.request_id,
        admission_id=task.admission_id,
        task_id=task.branch_task_id,
        body_digest=str(row["body_digest"]),
        admission_generation=int(row["grant_generation"]),
    )
    try:
        authority = SQLiteBackgroundBranchAuthorityStore.read_authority_in_transaction(
            conn,
            logical_attempt_key=logical_key,
        )
    except sqlite3.OperationalError as exc:
        if "no such table" not in str(exc).lower():
            raise
        authority = None
    assert authority is not None
    binding, attempt = authority
    transaction = _SQLiteBackgroundBranchAuthorityTransaction(conn)
    transitioned_at = _background_timestamp(claimed_at, attempt.updated_at)
    claimed_attempt = replace(
        attempt,
        lease_generation=attempt.lease_generation + 1,
        lease_expires_at=_background_timestamp(lease_expires_at),
        lifecycle=BackgroundBranchAttemptLifecycle.CLAIMED,
        updated_at=transitioned_at,
    )
    attempt_result = transaction.compare_and_swap_attempt(
        attempt_id=attempt.attempt_id,
        expected=BackgroundBranchAttemptFence(attempt),
        replacement=claimed_attempt,
    )
    if attempt_result.record != claimed_attempt:
        return False
    owner = BackgroundBranchAuthorityOwnerRecord(
        owner_kind=BackgroundBranchAuthorityOwnerKind.QUEUE_TASK,
        owner_id=task.branch_task_id,
        universe_id=binding.universe_id,
        authorizing_principal_id=binding.authorizing_principal_id,
        source_generation=attempt.source_generation,
        transition_generation=1,
        state=BackgroundBranchAuthorityOwnerState.PENDING,
        binding=BackgroundBranchBindingFence(binding),
        attempt=BackgroundBranchAttemptFence(claimed_attempt),
        hold_reason=None,
        updated_at=transitioned_at,
    )
    return transaction.insert_owner(owner).record == owner


def explain_background_queue_authority_in_transaction(
    conn: sqlite3.Connection,
    task: Epoch2BranchTask,
    consumer_lease: AssignedConsumerLease,
    *,
    claimed_at: str,
    lease_expires_at: str,
) -> str | None:
    """Return the first queue-authority claim predicate without writing."""

    from tinyassets.background_branch_authority_service import (
        BackgroundBranchAuthorityOwnerKind,
    )
    from tinyassets.cloud_automation_continuation import build_request_task_attempt_key
    from tinyassets.storage.background_branch_authority import (
        SQLiteBackgroundBranchAuthorityStore,
    )

    if not isinstance(conn, sqlite3.Connection) or not conn.in_transaction:
        raise ValueError("background queue authority explain requires a transaction")
    if task.claimed_by:
        return "task_already_claimed"
    row = conn.execute(
        """
        SELECT body_digest, grant_generation, actor_id
        FROM request_admissions
        WHERE admission_id = ? AND request_id = ? AND branch_task_id = ?
        """,
        (task.admission_id, task.request_id, task.branch_task_id),
    ).fetchone()
    if row is None:
        return "no_admission_row"
    logical_key = build_request_task_attempt_key(
        tenant_id=str(row["actor_id"]),
        request_id=task.request_id,
        admission_id=task.admission_id,
        task_id=task.branch_task_id,
        body_digest=str(row["body_digest"]),
        admission_generation=int(row["grant_generation"]),
    )
    authority = SQLiteBackgroundBranchAuthorityStore.read_authority_in_transaction(
        conn,
        logical_attempt_key=logical_key,
    )
    if authority is None:
        return "no_background_authority"
    binding, attempt = authority
    now = _utc(claimed_at)
    lease_expiry = _utc(lease_expires_at)
    predicates = (
        (binding.status is BackgroundBranchBindingStatus.ACTIVE, "binding_not_active"),
        (binding.expires_at is not None, "binding_expired"),
        (
            binding.expires_at is not None and _utc(binding.expires_at) > now,
            "binding_expired",
        ),
        (
            binding.expires_at is not None and lease_expiry <= _utc(binding.expires_at),
            "binding_lease_too_short",
        ),
        (
            binding.authorizing_principal_id == str(row["actor_id"]),
            "binding_principal_mismatch",
        ),
        (binding.universe_id == task.universe_id, "binding_universe_mismatch"),
        (binding.branch_def_id == task.branch_def_id, "binding_branch_mismatch"),
        (
            binding.pinned_branch_version_id == task.automation_branch_version,
            "binding_version_mismatch",
        ),
        (
            BackgroundBranchExecutorClass.CLOUD in binding.permitted_executor_classes,
            "binding_executor_class_missing",
        ),
        (bool(binding.daemon_id), "binding_daemon_missing"),
        (bool(binding.runtime_id), "binding_runtime_missing"),
        (
            attempt.lifecycle is BackgroundBranchAttemptLifecycle.RESERVED,
            "attempt_not_reserved",
        ),
        (attempt.lease_expires_at is None, "attempt_lease_present"),
        (
            attempt.branch_version_id == task.automation_branch_version,
            "attempt_version_mismatch",
        ),
        (
            attempt.branch_content_digest == task.automation_subject_digest,
            "attempt_digest_mismatch",
        ),
        (
            attempt.executor_audience.daemon_id == binding.daemon_id,
            "attempt_daemon_mismatch",
        ),
        (
            attempt.executor_audience.runtime_id == binding.runtime_id,
            "attempt_runtime_mismatch",
        ),
        (lease_expiry > now, "claim_lease_invalid"),
        (
            consumer_lease.consumer_id.startswith(("assigned-consumer:", "worker_assigned_")),
            "consumer_identity_invalid",
        ),
        (
            _utc(consumer_lease.expires_at) >= lease_expiry,
            "consumer_lease_too_short",
        ),
    )
    for allowed, reason in predicates:
        if not allowed:
            return reason
    owner = SQLiteBackgroundBranchAuthorityStore.read_queue_owner_in_transaction(
        conn,
        owner_id=task.branch_task_id,
    )
    if owner is not None and owner.owner_kind is BackgroundBranchAuthorityOwnerKind.QUEUE_TASK:
        return "queue_owner_exists"
    return None


def start_background_queue_authority(
    base_path: str | Path,
    task: Epoch2BranchTask,
    consumer_lease: AssignedConsumerLease,
) -> None:
    """CAS one claimed queue owner and attempt into their running states."""

    from tinyassets.background_branch_authority import BackgroundBranchAttemptFence
    from tinyassets.background_branch_authority_service import (
        BackgroundBranchAuthorityOwnerFence,
        BackgroundBranchAuthorityOwnerKind,
        BackgroundBranchAuthorityOwnerState,
    )
    from tinyassets.storage.background_branch_authority import (
        SQLiteBackgroundBranchAuthorityStore,
    )

    if task.claimed_by != consumer_lease.consumer_id:
        raise BackgroundExecutorIdentityError("background_consumer_lease_mismatch")
    store = SQLiteBackgroundBranchAuthorityStore(base_path)
    with store.transaction() as transaction:
        owner = transaction.get_owner(
            owner_kind=BackgroundBranchAuthorityOwnerKind.QUEUE_TASK,
            owner_id=task.branch_task_id,
        )
        if owner is None:
            raise BackgroundExecutorIdentityError("background_queue_owner_missing")
        if (
            owner.state is not BackgroundBranchAuthorityOwnerState.PENDING
            or owner.binding is None
            or owner.attempt is None
        ):
            raise BackgroundExecutorIdentityError("background_queue_owner_inactive")
        binding = transaction.get_binding(owner.binding.expected_record.binding_id)
        attempt = transaction.get_attempt_by_logical_key(
            owner.attempt.expected_record.logical_attempt_key
        )
        now = datetime.now(timezone.utc)
        if binding != owner.binding.expected_record:
            raise BackgroundExecutorIdentityError("background_binding_stale")
        if binding.expires_at is None or _utc(binding.expires_at) <= now:
            raise BackgroundExecutorIdentityError("background_binding_expired")
        if attempt != owner.attempt.expected_record:
            raise BackgroundExecutorIdentityError("background_attempt_stale")
        if attempt.lifecycle is not BackgroundBranchAttemptLifecycle.CLAIMED:
            raise BackgroundExecutorIdentityError("background_attempt_inactive")
        if attempt.lease_expires_at is None or _utc(attempt.lease_expires_at) <= now:
            raise BackgroundExecutorIdentityError("background_attempt_lease_expired")
        transitioned_at = _background_timestamp(now, owner.updated_at, attempt.updated_at)
        running_attempt = replace(
            attempt,
            lifecycle=BackgroundBranchAttemptLifecycle.RUNNING,
            updated_at=transitioned_at,
        )
        attempt_result = transaction.compare_and_swap_attempt(
            attempt_id=attempt.attempt_id,
            expected=BackgroundBranchAttemptFence(attempt),
            replacement=running_attempt,
        )
        if attempt_result.record != running_attempt:
            raise BackgroundExecutorIdentityError("background_attempt_stale")
        running_owner = replace(
            owner,
            transition_generation=owner.transition_generation + 1,
            state=BackgroundBranchAuthorityOwnerState.RUNNING,
            attempt=BackgroundBranchAttemptFence(running_attempt),
            updated_at=transitioned_at,
        )
        owner_result = transaction.compare_and_swap_owner(
            expected=BackgroundBranchAuthorityOwnerFence(owner),
            replacement=running_owner,
        )
        if owner_result.record != running_owner:
            raise BackgroundExecutorIdentityError("background_queue_owner_changed")


def terminalize_background_queue_authority(
    base_path: str | Path,
    task: Epoch2BranchTask,
    *,
    status: str,
    reason: str,
) -> None:
    """CAS the queue authority and its attempt to one conclusive terminal state."""

    from tinyassets.background_branch_authority import BackgroundBranchAttemptFence
    from tinyassets.background_branch_authority_service import (
        BackgroundBranchAuthorityOwnerFence,
        BackgroundBranchAuthorityOwnerKind,
        BackgroundBranchAuthorityOwnerState,
    )
    from tinyassets.storage.background_branch_authority import (
        SQLiteBackgroundBranchAuthorityStore,
    )

    owner_states = {
        "succeeded": BackgroundBranchAuthorityOwnerState.SUCCEEDED,
        "failed": BackgroundBranchAuthorityOwnerState.FAILED,
        "cancelled": BackgroundBranchAuthorityOwnerState.CANCELLED,
    }
    attempt_states = {
        "succeeded": BackgroundBranchAttemptLifecycle.SUCCEEDED,
        "failed": BackgroundBranchAttemptLifecycle.FAILED,
        "cancelled": BackgroundBranchAttemptLifecycle.CANCELLED,
    }
    if status not in owner_states or not reason.strip():
        raise ValueError("background terminal status and reason are required")
    store = SQLiteBackgroundBranchAuthorityStore(base_path)
    with store.transaction() as transaction:
        owner = transaction.get_owner(
            owner_kind=BackgroundBranchAuthorityOwnerKind.QUEUE_TASK,
            owner_id=task.branch_task_id,
        )
        if owner is None:
            raise BackgroundExecutorIdentityError("background_queue_owner_missing")
        if (
            owner.state
            not in {
                BackgroundBranchAuthorityOwnerState.PENDING,
                BackgroundBranchAuthorityOwnerState.RUNNING,
            }
            or owner.attempt is None
        ):
            raise BackgroundExecutorIdentityError("background_queue_owner_inactive")
        attempt = transaction.get_attempt_by_logical_key(
            owner.attempt.expected_record.logical_attempt_key
        )
        if attempt != owner.attempt.expected_record:
            raise BackgroundExecutorIdentityError("background_attempt_stale")
        transitioned_at = _background_timestamp(
            datetime.now(timezone.utc), owner.updated_at, attempt.updated_at
        )
        attempt_state = attempt_states[status]
        if attempt.lifecycle is BackgroundBranchAttemptLifecycle.CLAIMED:
            attempt_state = BackgroundBranchAttemptLifecycle.CANCELLED
        terminal_attempt = replace(
            attempt,
            lease_generation=attempt.lease_generation + 1,
            lease_expires_at=None,
            lifecycle=attempt_state,
            hold_reason=None,
            terminal_reason=reason,
            updated_at=transitioned_at,
        )
        attempt_result = transaction.compare_and_swap_attempt(
            attempt_id=attempt.attempt_id,
            expected=BackgroundBranchAttemptFence(attempt),
            replacement=terminal_attempt,
        )
        if attempt_result.record != terminal_attempt:
            raise BackgroundExecutorIdentityError("background_attempt_stale")
        terminal_owner = replace(
            owner,
            transition_generation=owner.transition_generation + 1,
            state=owner_states[status],
            attempt=BackgroundBranchAttemptFence(terminal_attempt),
            hold_reason=None,
            updated_at=transitioned_at,
        )
        owner_result = transaction.compare_and_swap_owner(
            expected=BackgroundBranchAuthorityOwnerFence(owner),
            replacement=terminal_owner,
        )
        if owner_result.record != terminal_owner:
            raise BackgroundExecutorIdentityError("background_queue_owner_changed")


def _normalize_content_digest(value: str) -> str:
    """Both forms occur: branch_versions.content_hash is bare hex, but a task's
    automation_subject_digest is `sha256:<hex>`. Normalize
    to the prefixed form so the authority compare doesn't fail every real version
    (Codex #2, PR #2516). Empty stays empty so a missing digest never matches."""
    text = (value or "").strip()
    if not text:
        return ""
    return text if text.startswith("sha256:") else f"sha256:{text}"


def _branch_snapshot(base_path: Path, task: Epoch2BranchTask) -> dict:
    from tinyassets.branch_versions import get_branch_version

    version = get_branch_version(base_path, task.automation_branch_version)
    if (
        version is None
        or version.status != "active"
        or version.branch_def_id != task.branch_def_id
        or _normalize_content_digest(version.content_hash)
        != _normalize_content_digest(task.automation_subject_digest)
    ):
        raise PermissionError("immutable Branch version is not current authority")
    return version.snapshot


def _branch_roles(base_path: Path, task: Epoch2BranchTask) -> tuple[str, ...]:
    node_defs = _branch_snapshot(base_path, task).get("node_defs", {})
    if isinstance(node_defs, dict):
        nodes = node_defs.values()
    elif isinstance(node_defs, list):
        nodes = node_defs
    else:
        raise PermissionError("immutable Branch node definitions are invalid")
    roles = {
        str(node.get("model_hint") or "writer").strip() or "writer"
        for node in nodes
        if isinstance(node, dict) and str(node.get("node_type") or "prompt") == "prompt"
    }
    if not roles:
        roles = {"writer"}
    if not roles.issubset(_SUPPORTED_BRANCH_ROLES):
        raise PermissionError("immutable Branch requests an unsupported provider role")
    return tuple(sorted(roles))


class _SeedResolver:
    def __init__(self, seed: ProviderWorkBindingSeed) -> None:
        self.seed = seed

    def resolve(self, root: ProviderWorkBindingRoot) -> ProviderWorkBindingSeed | None:
        return self.seed if self._matches(root) else None

    def resolve_current_in_transaction(
        self, _conn: sqlite3.Connection, root: ProviderWorkBindingRoot
    ) -> ProviderWorkBindingSeed | None:
        return self.resolve(root)

    def _matches(self, root: ProviderWorkBindingRoot) -> bool:
        return (
            root.owner_user_id == self.seed.owner_user_id
            and root.universe_id == self.seed.universe_id
            and root.provider == self.seed.provider
        )


def _current_background_binding(
    conn: sqlite3.Connection,
    *,
    provider_store: Any,
    seed: ProviderWorkBindingSeed,
) -> Any:
    root = ProviderWorkBindingRoot(
        owner_user_id=seed.owner_user_id,
        universe_id=seed.universe_id,
        provider=seed.provider,
    )
    service = ProviderWorkBindingService(provider_store, _SeedResolver(seed))
    issued = service.issue_in_transaction(conn, root)
    if (
        issued.outcome
        in {
            ProviderWorkAuthorityWriteOutcome.APPLIED,
            ProviderWorkAuthorityWriteOutcome.REPLAYED,
        }
        and issued.record is not None
    ):
        return issued.record
    from tinyassets.provider_work_authority import provider_work_binding_id

    binding_id = provider_work_binding_id(
        owner_user_id=seed.owner_user_id,
        universe_id=seed.universe_id,
        provider=seed.provider,
        binding_class=BACKGROUND_BRANCH_RUN_OPERATION,
    )
    current = provider_store.get_binding_in_transaction(conn, binding_id=binding_id)
    if current is None:
        raise PermissionError("background provider binding is unavailable")
    exact = (
        current.state is ProviderWorkBindingState.ACTIVE,
        _utc(current.expires_at) > datetime.now(timezone.utc),
        current.credential_reference_digest == seed.credential_reference_digest,
        current.allowed_operations == seed.allowed_operations,
        current.allowed_roles == seed.allowed_roles,
        current.assignment_generation == seed.assignment_generation,
        current.assignment_digest == seed.assignment_digest,
        current.max_invocations == seed.max_invocations,
        current.max_tokens == seed.max_tokens,
        current.max_cost_microunits == seed.max_cost_microunits,
    )
    if all(exact):
        return current
    rebound = service.rebind_in_transaction(conn, ProviderWorkBindingFence(current), root)
    if rebound.outcome is not ProviderWorkAuthorityWriteOutcome.APPLIED or rebound.record is None:
        raise PermissionError("background provider binding rotation conflicted")
    return rebound.record


class _BackgroundAssignedProviderSession:
    def __init__(
        self,
        base_path: Path,
        task: Epoch2BranchTask,
        consumer_lease: AssignedConsumerLease,
        provider_call: Callable[..., str],
    ) -> None:
        self._base_path = base_path
        self._task = task
        self._universe_dir = base_path / task.universe_id
        self._sign_ins_refreshed = False
        self._consumer_lease = consumer_lease
        self._provider_call = provider_call
        from tinyassets.request_budget import TurnRequestBudget, current_request_budget

        self._request_budget = current_request_budget() or TurnRequestBudget(
            task.actor_id, task.universe_id,
        )
        self._request_budget.check_scope(task.actor_id, task.universe_id)
        self._call_index = 0
        self._lock = threading.Lock()

    @staticmethod
    def _declared_policy_providers(policy: dict[str, Any] | None) -> set[str]:
        providers: set[str] = set()
        if not policy:
            return providers
        for value in policy.values():
            entries = value if isinstance(value, list) else [value]
            for entry in entries:
                use = entry.get("use") if isinstance(entry, dict) else None
                candidate = use if isinstance(use, dict) else entry
                if isinstance(candidate, dict) and candidate.get("provider"):
                    providers.add(str(candidate["provider"]))
        return providers

    def call_with_policy_sync(
        self,
        role: str,
        prompt: str,
        system: str,
        policy: dict[str, Any] | None,
        config: Any = None,
        difficulty: str = "",
        **kwargs: Any,
    ) -> tuple[str, str, dict[str, Any]]:
        del difficulty
        response, provider = self._call(role, prompt, system, config, policy, kwargs)
        return response, provider, {"authority": BACKGROUND_BRANCH_RUN_OPERATION, "attempts": 1}

    def __call__(
        self, prompt: str, system: str = "", *, role: str = "writer", **kwargs: Any
    ) -> str:
        config = kwargs.pop("config", None)
        policy = kwargs.pop("policy", None)
        response, _provider = self._call(role, prompt, system, config, policy, kwargs)
        return response

    def _call(
        self,
        role: str,
        prompt: str,
        system: str,
        config: Any,
        policy: dict[str, Any] | None,
        kwargs: dict[str, Any],
    ) -> tuple[str, str]:
        supplied_operation = kwargs.pop("operation", BACKGROUND_BRANCH_RUN_OPERATION)
        supplied_context = kwargs.pop("universe_context", None)
        if supplied_operation != BACKGROUND_BRANCH_RUN_OPERATION:
            raise PermissionError("background provider operation cannot be substituted")
        if (
            supplied_context is not None
            and Path(supplied_context.universe_dir) != self._universe_dir
        ):
            raise PermissionError("background provider command center cannot be substituted")
        if supplied_context is not None and any(
            getattr(supplied_context, field, None) is not None
            for field in ("provider_request", "provider_invocation", "served_provider",
                          "agent_model_plan", "model_selection")
        ):
            raise PermissionError("background provider authority cannot be substituted")
        from tinyassets.providers.base import ModelConfig

        if config is None:
            config = ModelConfig()
        if isinstance(config, ModelConfig):
            if config.request_budget not in (None, self._request_budget):
                raise PermissionError("background parent request budget cannot be substituted")
            config = replace(config, request_budget=self._request_budget,
                             request_purpose="helper")
        declared_providers = self._declared_policy_providers(policy)
        # Enforcement site (C), served half (design.md § Enforcement sites).
        # Both public entries land here — `__call__` (raw prompt/system) and
        # `call_with_policy_sync` (structured role/policy) — so one gate covers
        # both shapes. It has to be *before* the shared-self branch below:
        # that branch calls `load_background_executor_identity` and
        # `prepare_shared_self_turn` and then hands the turn to the workflow
        # agent, all before `_authorize_launch` is ever reached.
        #
        # Ordering: resolve here, outside every transaction in this module. The
        # in-transaction sites read the cached peek only.
        provenance = resolve_process_cloud_admission()
        if not provenance.is_cloud:
            raise PermissionError(
                platform_not_cloud_message(
                    provenance, surface="background served provider authority"
                )
            )
        with self._lock:
            from tinyassets.exceptions import ProviderAuthorityHeldError
            from tinyassets.shared_self import agent_node, prepare_shared_self_turn

            self._refresh_sign_ins()
            try:
                node = agent_node(
                    _branch_snapshot(self._base_path, self._task),
                    getattr(config, "agent_node_id", ""), self._task.actor_id,
                    node_key=getattr(config, "agent_node_key", ""),
                )
            except Exception as exc:
                # Preserve the existing typed hold when the earlier opt-in read
                # detects the same invalid subject that launch admission rejects.
                try:
                    _hold_background_authority(self._base_path, self._task)
                except Exception:
                    logger.exception("background subject hold projection failed")
                raise ProviderAuthorityHeldError(
                    "Assigned background provider authority is unavailable; retry after repair.",
                ) from exc
            if node is not None:
                if role != "writer" or kwargs:
                    raise PermissionError("workflow agent cannot substitute execution context")
                load_background_executor_identity(self._base_path, self._task, self._consumer_lease)
                prompt, system, config = prepare_shared_self_turn(
                    self._base_path, self._task.universe_id, self._task.actor_id, prompt, config,
                    node,
                )
                from tinyassets.workflow_agent import call_background_work_agent

                return call_background_work_agent(
                    self, prompt=prompt, system=system, config=config, policy=policy,
                )
            invocation_index = self._call_index + 1
            with self._authorize_launch(
                role=role,
                prompt=prompt,
                system=system,
                invocation_index=invocation_index,
                declared_providers=declared_providers,
                policy=policy,
            ) as launch:
                from tinyassets.config import load_universe_config
                from tinyassets.providers.base import ModelConfig, UniverseContext

                carrier, snapshot_dir, provider = launch
                universe_dir = self._base_path / self._task.universe_id
                call_kwargs = dict(kwargs)
                call_config = config
                if snapshot_dir is not None:
                    if call_config is None:
                        call_config = ModelConfig()
                    if not isinstance(call_config, ModelConfig):
                        raise TypeError("background provider config must be a ModelConfig")
                    call_config = replace(
                        call_config,
                        credential_snapshot_dir=snapshot_dir,
                    )
                if isinstance(call_config, ModelConfig):
                    # A workflow node call: each provider confines it to the
                    # owner's universe in its own way (ModelConfig.workflow_node).
                    call_config = replace(call_config, workflow_node=True)
                call_kwargs.update(
                    operation=BACKGROUND_BRANCH_RUN_OPERATION,
                    universe_context=UniverseContext(
                        universe_dir=universe_dir,
                        config=load_universe_config(universe_dir),
                        provider_invocation=carrier,
                    ),
                )
                result = self._provider_call(
                    prompt, system, role=role, config=call_config, **call_kwargs
                )
                self._call_index = invocation_index
                return result, provider

    def _refresh_sign_ins(self) -> None:
        """Bring the owner's stored sign-ins current before the attempt pins them.

        Once per session, at its first node call: the work receipt is the whole
        ATTEMPT's, replayed by every later node and agent round, and a refresh
        renews the accepted source, which moves the assignment that receipt
        names. Refreshing at each node let a second node's renewal void the
        first node's receipt (Codex refute-review on #4082, reproduced: two
        spends, one call, task back to pending). The owner is the task's actor,
        proven to be the background binding's authorizing principal; a task
        that proves nothing refreshes nothing and is refused by the launch.
        """
        if self._sign_ins_refreshed:
            return
        self._sign_ins_refreshed = True
        try:
            load_background_executor_identity(self._base_path, self._task, self._consumer_lease)
        except (BackgroundExecutorIdentityError, PermissionError):
            return
        from tinyassets.subscription_refresh import refresh_deposited_subscriptions

        refresh_deposited_subscriptions(
            base_path=self._base_path,
            universe_dir=self._universe_dir,
            owner_user_id=self._task.actor_id,
            universe_id=self._task.universe_id,
        )

    @contextmanager
    def _authorize_attempt(self, *, role, prompt, system, policy):
        """One agent inference, under the existing queue session lock and cap."""
        invocation_index = self._call_index + 1
        with self._authorize_launch(
            role=role, prompt=prompt, system=system, policy=policy,
            invocation_index=invocation_index,
            declared_providers=self._declared_policy_providers(policy),
        ) as launch:
            # Once armed, even failed attempts cannot reuse this round's key.
            self._call_index = invocation_index
            yield launch

    def _check_agent_authority(self, carrier: ProviderInvocationCarrier) -> str:
        """Fresh queue/work authority for tools, without reserving another call."""
        import hmac

        from tinyassets.branch_versions import compute_content_hash
        from tinyassets.provider_serving_binding import (
            _current_selected_member_authority,
            _current_serving_authority,
            resolve_serving_agent_binding,
        )
        from tinyassets.provider_work_authority import _provider_invocation_carrier_seal
        from tinyassets.shared_self import require_founder_home, shared_self_requested
        from tinyassets.storage.current_home import check_current_home
        from tinyassets.storage.provider_work_authority import (
            SQLiteProviderWorkAuthorityStore,
            _background_receipt_authority,
            _claim_record,
            _current_work_member,
            _receipt_record,
            _record,
            _reservation_record,
        )

        if (type(carrier) is not ProviderInvocationCarrier
                or carrier._issuer_pid != os.getpid()
                or not hmac.compare_digest(
                    carrier._seal, _provider_invocation_carrier_seal(carrier),
                )):
            raise PermissionError("background agent carrier is invalid")
        snapshot = _branch_snapshot(self._base_path, self._task)
        if (not shared_self_requested(snapshot)
                or _normalize_content_digest(compute_content_hash(snapshot))
                != _normalize_content_digest(self._task.automation_subject_digest)):
            raise PermissionError("background agent immutable subject changed")
        principal = self._task.actor_id
        require_founder_home(self._base_path, self._task.universe_id, principal)
        store = SQLiteProviderWorkAuthorityStore(self._base_path)
        agent = resolve_serving_agent_binding(
            self._base_path, universe_id=self._task.universe_id, owner_user_id=principal,
        )
        with provider_assignment_admission().shared(self._universe_dir):
            with store.connection() as conn:
                conn.execute("BEGIN")
                now = store._now()
                check_current_home(conn, principal, self._task.universe_id)
                self._check_agent_task(conn, now)
                receipt_row = conn.execute(
                    "SELECT * FROM provider_work_receipts WHERE receipt_id = ?",
                    (carrier.work_receipt_id,),
                ).fetchone()
                claim_row = conn.execute(
                    "SELECT * FROM provider_work_execution_claims WHERE claim_id = ?",
                    (carrier._claim.claim_id,),
                ).fetchone()
                reservation_row = conn.execute(
                    "SELECT * FROM provider_invocation_reservations WHERE reservation_id = ?",
                    (carrier.reservation_id,),
                ).fetchone()
                if receipt_row is None or claim_row is None or reservation_row is None:
                    raise PermissionError("background agent progress authority is unavailable")
                receipt, claim = _receipt_record(receipt_row), _claim_record(claim_row)
                reservation = _reservation_record(reservation_row)
                if not all((
                    receipt == carrier._receipt, claim == carrier._claim,
                    receipt.state is ProviderWorkReceiptState.ACTIVE,
                    receipt.work_item_kind == "background_attempt",
                    receipt.principal_id == principal,
                    receipt.universe_id == self._task.universe_id,
                    claim.state.value == "active", _utc(receipt.expires_at) > now,
                    _utc(claim.lease_expires_at) > now,
                    reservation.receipt_id == receipt.receipt_id,
                    reservation.receipt_digest == receipt.receipt_digest,
                    reservation.claim_id == claim.claim_id,
                    reservation.claim_digest == claim.claim_digest,
                    reservation.claim_generation == claim.generation,
                    reservation.selection == carrier._reservation.selection,
                    reservation.operation == carrier.operation == BACKGROUND_BRANCH_RUN_OPERATION,
                    reservation.role == carrier.role == "writer",
                    reservation.state.value in {"launch_started", "succeeded"},
                )):
                    raise PermissionError("background agent receipt, claim or invocation changed")
                _, _, owner = _background_receipt_authority(conn, receipt, now=now, claim=claim)
                if owner.owner_id != self._task.branch_task_id:
                    raise PermissionError("background agent queue owner changed")
                if receipt.authority_scope == "manifest":
                    _current_selected_member_authority(
                        conn, store=store, universe_dir=self._universe_dir,
                        base_path=self._base_path, owner_user_id=principal,
                        universe_id=self._task.universe_id, agent=agent, provider=carrier.provider,
                    )
                    _current_work_member(conn, receipt, reservation.selection, now)
                else:
                    child_row = conn.execute(
                        "SELECT * FROM provider_work_bindings WHERE binding_id = ?",
                        (receipt.binding_id,),
                    ).fetchone()
                    child = _record(child_row) if child_row is not None else None
                    assignment, _, _ = _current_serving_authority(
                        conn, store=store, universe_dir=self._universe_dir,
                        owner_user_id=principal, universe_id=self._task.universe_id, agent=agent,
                    )
                    if child is None or not all((
                        child.state is ProviderWorkBindingState.ACTIVE,
                        child.generation == receipt.binding_generation,
                        child.binding_digest == receipt.binding_digest,
                        child.revocation_generation == receipt.binding_revocation_generation,
                        child.owner_user_id == principal, child.universe_id == receipt.universe_id,
                        child.provider == receipt.provider == carrier.provider
                        == assignment.provider,
                        child.assignment_generation == receipt.assignment_generation
                        == assignment.generation,
                        child.assignment_digest == receipt.assignment_digest
                        == assignment.assignment_digest,
                        child.credential_reference_digest == receipt.credential_reference_digest
                        == assignment.credential_reference_digest,
                        _utc(child.expires_at) > now,
                        carrier.operation in child.allowed_operations,
                        carrier.role in child.allowed_roles,
                    )):
                        raise PermissionError("background agent child or assigned provider changed")
        return principal

    def _check_agent_task(self, conn, now):
        row = conn.execute("SELECT * FROM branch_tasks_v2 WHERE branch_task_id = ?",
                           (self._task.branch_task_id,)).fetchone()
        fields = ("universe_id", "branch_def_id", "request_id", "admission_id", "automation_id",
                  "automation_activation_epoch", "automation_subject_kind",
                  "automation_subject_ref",
                  "automation_subject_digest", "automation_branch_version", "automation_lease_id",
                  "automation_executor_class", "claimed_at")
        if row is None or not all((
            row["status"] == "running", row["disabled"] == 0,
            row["claimed_by"] == self._task.claimed_by == self._consumer_lease.consumer_id,
            _utc(str(row["lease_expires_at"])) > now, _utc(self._consumer_lease.expires_at) > now,
            all(row[key] == getattr(self._task, key) for key in fields),
        )):
            raise PermissionError("background agent task cancelled, changed or lease expired")
        activation = conn.execute(
            "SELECT * FROM automation_activations WHERE universe_id = ? AND automation_id = ?",
            (self._task.universe_id, self._task.automation_id),
        ).fetchone()
        if activation is None or not all((
            activation["state"] == "active",
            activation["epoch"] == self._task.automation_activation_epoch,
            activation["executor_class"] == self._task.automation_executor_class == "cloud",
            activation["subject_kind"] == self._task.automation_subject_kind,
            activation["subject_ref"] == self._task.automation_subject_ref,
            activation["subject_digest"] == self._task.automation_subject_digest,
            activation["immutable_branch_version"] == self._task.automation_branch_version,
            activation["lease_id"] == self._task.automation_lease_id,
        )):
            raise PermissionError("background agent activation changed")

    @contextmanager
    def _authorize_launch(
        self,
        *,
        role: str,
        prompt: str,
        system: str,
        invocation_index: int,
        declared_providers: set[str],
        policy: dict[str, Any] | None = None,
    ) -> Iterator[tuple[ProviderInvocationCarrier, Path | None, str]]:
        from tinyassets.cloud_automation_continuation import build_request_task_attempt_key
        from tinyassets.credential_vault import snapshot_llm_subscription_credential
        from tinyassets.exceptions import ProviderAuthorityHeldError
        from tinyassets.provider_serving_binding import (
            _current_serving_authority,
            _is_open_provider,
            resolve_serving_agent_binding,
        )
        from tinyassets.shared_self import shared_self_requested
        from tinyassets.storage.background_branch_authority import (
            SQLiteBackgroundBranchAuthorityStore,
        )
        from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore
        from tinyassets.storage.request_admissions import RequestAdmissionStore

        held = "Assigned background provider authority is unavailable; retry after repair."
        # `_authorize_launch` is the single mint path for this lane: `_call`
        # above and `_authorize_attempt` (the workflow-agent tool loop) both
        # funnel through it, and both `executor_class="cloud"` literals live
        # inside its transaction. Re-resolving here rather than trusting the
        # `_call` gate means an agent tool round entering through
        # `_authorize_attempt` is admitted on its own, not on its caller's.
        #
        # Still outside the transaction: `admission_store.connection()` /
        # `BEGIN IMMEDIATE` opens further down, and `load_background_executor
        # _identity` and `snapshot_llm_subscription_credential` both run after
        # this point, so an unadmitted process reaches neither the credential
        # snapshot nor the write lock.
        launch_provenance = resolve_process_cloud_admission()
        if not launch_provenance.is_cloud:
            raise PermissionError(
                platform_not_cloud_message(
                    launch_provenance, surface="background served provider launch"
                )
            )
        universe_dir = self._base_path / self._task.universe_id
        # NO pre-launch credential refresh here, deliberately. This attempt's
        # authority is already minted and PINS the assignment generation, and a
        # refresh renews the accepted source, which advances it -- so refreshing
        # here fails the very check it was meant to help, and the PermissionError
        # is swallowed into ProviderAuthorityHeldError below, which cannot fall
        # back either. Codex refute-review P1 #3 reproduced both halves on the
        # foreground twin of this lane.
        #
        # The refresh belongs where nothing is pinned yet: for this lane that is
        # `_refresh_sign_ins`, once, at the attempt's first node call. A finished
        # sign-in still surfaces from the launch itself, now typed as a sign-in
        # failure rather than an outage
        # (providers/codex_provider._terminal_auth_failure).
        snapshot = None
        carrier = None
        try:
            needs_tools = shared_self_requested(_branch_snapshot(self._base_path, self._task))
            roles = _branch_roles(self._base_path, self._task)
            if role not in roles:
                raise PermissionError("provider role is outside immutable Branch authority")
            admission_store = RequestAdmissionStore(self._base_path)
            provider_store = SQLiteProviderWorkAuthorityStore(self._base_path)
            background_store = SQLiteBackgroundBranchAuthorityStore(self._base_path)
            from tinyassets.providers.work_model_selection import prepare_work_model_snapshot

            policy = self._resolved_policy(policy)
            preferred = (policy or {}).get("preferred", {})
            if not isinstance(preferred, dict):
                raise PermissionError("background model preference is invalid")
            # Validate the task before any discovery IO, then revalidate every
            # durable task/activation/member fact below after discovery finishes.
            load_background_executor_identity(self._base_path, self._task, self._consumer_lease)
            model_snapshot = prepare_work_model_snapshot(
                base_path=self._base_path, universe_id=self._task.universe_id,
                provider=preferred.get("provider"),
                model_id=preferred.get("model_id", preferred.get("model", "")),
            )
            with provider_assignment_admission().shared(universe_dir):
                with admission_store.connection() as conn:
                    conn.execute("BEGIN IMMEDIATE")
                    try:
                        row = conn.execute(
                            """
                            SELECT t.*, a.actor_id, a.body_digest, a.grant_generation
                            FROM branch_tasks_v2 AS t JOIN request_admissions AS a
                              ON a.admission_id=t.admission_id AND a.request_id=t.request_id
                             AND a.branch_task_id=t.branch_task_id
                            WHERE t.branch_task_id=? LIMIT 1
                            """,
                            (self._task.branch_task_id,),
                        ).fetchone()
                        if row is None:
                            raise PermissionError("task admission is unavailable")
                        now = datetime.now(timezone.utc)
                        exact_task = (
                            row["status"] in ({"running"} if needs_tools
                                              else {"running", "cancel_requested"}),
                            row["claimed_by"] == self._consumer_lease.consumer_id,
                            row["claimed_at"] == self._task.claimed_at,
                            _utc(str(row["lease_expires_at"])) > now,
                            _utc(self._consumer_lease.expires_at) > now,
                            row["universe_id"] == self._task.universe_id,
                            row["automation_activation_epoch"]
                            == self._task.automation_activation_epoch,
                            row["automation_subject_ref"] == self._task.automation_subject_ref,
                            row["automation_subject_digest"]
                            == self._task.automation_subject_digest,
                            row["automation_branch_version"]
                            == self._task.automation_branch_version,
                        )
                        if not all(exact_task):
                            raise PermissionError("task lease or immutable target changed")
                        activation = conn.execute(
                            """
                            SELECT * FROM automation_activations
                            WHERE universe_id=? AND automation_id=? LIMIT 1
                            """,
                            (self._task.universe_id, self._task.automation_id),
                        ).fetchone()
                        if activation is None or not all(
                            (
                                activation["state"] == "active",
                                activation["epoch"] == self._task.automation_activation_epoch,
                                activation["subject_ref"] == self._task.automation_subject_ref,
                                activation["subject_digest"]
                                == self._task.automation_subject_digest,
                                activation["immutable_branch_version"]
                                == self._task.automation_branch_version,
                                activation["lease_id"] == self._task.automation_lease_id,
                            )
                        ):
                            raise PermissionError("automation activation changed")
                        assignment = load_provider_assignment_in_transaction(
                            conn, universe_id=self._task.universe_id
                        )
                        if assignment is None or assignment.state != "ready":
                            raise PermissionError("assigned provider is unavailable")
                        if not assignment.manifest_digest and declared_providers - {
                            assignment.provider
                        }:
                            raise PermissionError(
                                "background policy provider is outside assigned authority"
                            )
                        agent = resolve_serving_agent_binding(
                            self._base_path,
                            universe_id=self._task.universe_id,
                            owner_user_id=assignment.owner_user_id,
                        )
                        selection = None
                        manifest_bindings = ()
                        provider = assignment.provider
                        if assignment.manifest_digest:
                            manifest_bindings, serving_binding, custody, selection = (
                                self._manifest_member(
                                    conn,
                                    provider_store,
                                    universe_dir,
                                    assignment,
                                    agent,
                                    roles,
                                    declared_providers,
                                    policy,
                                )
                            )
                            provider = selection.provider
                            current_assignment = assignment
                        else:
                            current_assignment, serving_binding, custody = (
                                _current_serving_authority(
                                    conn,
                                    store=provider_store,
                                    universe_dir=universe_dir,
                                    owner_user_id=assignment.owner_user_id,
                                    universe_id=self._task.universe_id,
                                    agent=agent,
                                )
                            )
                        if current_assignment != assignment:
                            raise PermissionError("assigned provider rotated")
                        logical_key = build_request_task_attempt_key(
                            tenant_id=assignment.owner_user_id,
                            request_id=self._task.request_id,
                            admission_id=self._task.admission_id,
                            task_id=self._task.branch_task_id,
                            body_digest=str(row["body_digest"]),
                            admission_generation=int(row["grant_generation"]),
                        )
                        background_authority = background_store.read_authority_in_transaction(
                            conn, logical_attempt_key=logical_key
                        )
                        if background_authority is None:
                            raise PermissionError("background Branch attempt is unavailable")
                        binding, attempt = background_authority
                        if binding.status is BackgroundBranchBindingStatus.ACTIVE and (
                            binding.expires_at is None or _utc(binding.expires_at) <= now
                        ):
                            raise BackgroundExecutorIdentityError("background_binding_expired")
                        if not all(
                            (
                                binding.status is BackgroundBranchBindingStatus.ACTIVE,
                                binding.expires_at is not None,
                                binding.expires_at is not None and _utc(binding.expires_at) > now,
                                binding.authorizing_principal_id == assignment.owner_user_id,
                                binding.universe_id == self._task.universe_id,
                                binding.branch_def_id == self._task.branch_def_id,
                                binding.pinned_branch_version_id
                                == self._task.automation_branch_version,
                                BackgroundBranchExecutorClass.CLOUD
                                in binding.permitted_executor_classes,
                                bool(binding.daemon_id),
                                bool(binding.runtime_id),
                                attempt.binding_id == binding.binding_id,
                                attempt.binding_generation == binding.generation,
                                attempt.binding_digest == binding.binding_digest,
                                attempt.authorizing_principal_id == assignment.owner_user_id,
                                attempt.universe_id == self._task.universe_id,
                                attempt.branch_def_id == self._task.branch_def_id,
                                attempt.branch_version_id == self._task.automation_branch_version,
                                attempt.branch_content_digest
                                == self._task.automation_subject_digest,
                                attempt.lifecycle
                                in {
                                    BackgroundBranchAttemptLifecycle.CLAIMED,
                                    BackgroundBranchAttemptLifecycle.RUNNING,
                                },
                                attempt.lease_expires_at is not None,
                                attempt.lease_expires_at is not None
                                and _utc(attempt.lease_expires_at) > now,
                                attempt.remaining_count > 0,
                                attempt.remaining_cost_microunits > 0,
                            )
                        ):
                            raise PermissionError("background Branch attempt is unavailable")
                        max_invocations = min(
                            attempt.remaining_count,
                            _positive_env(
                                "TINYASSETS_ASSIGNED_QUEUE_UNIVERSE_MAX_INVOCATIONS",
                                _DEFAULT_MAX_INVOCATIONS,
                            ),
                        )
                        max_tokens = _positive_env(
                            "TINYASSETS_ASSIGNED_QUEUE_UNIVERSE_MAX_TOKENS", _DEFAULT_MAX_TOKENS
                        )
                        max_cost = min(
                            attempt.remaining_cost_microunits,
                            _positive_env(
                                "TINYASSETS_ASSIGNED_QUEUE_UNIVERSE_MAX_COST_MICROUNITS",
                                _DEFAULT_MAX_COST_MICROUNITS,
                            ),
                        )
                        if manifest_bindings:
                            max_invocations = min(
                                max_invocations, *(b.max_invocations for b in manifest_bindings)
                            )
                            max_tokens = min(max_tokens, *(b.max_tokens for b in manifest_bindings))
                            max_cost = min(
                                max_cost, *(b.max_cost_microunits for b in manifest_bindings)
                            )
                        attempt_expiry = _utc(
                            attempt.lease_expires_at or str(row["lease_expires_at"])
                        )
                        expires_at = (
                            min(
                                attempt_expiry,
                                _utc(binding.expires_at),
                                *(
                                    _utc(b.expires_at)
                                    for b in (manifest_bindings or (serving_binding,))
                                ),
                            )
                            .isoformat()
                            .replace("+00:00", "Z")
                        )
                        seed = None if manifest_bindings else ProviderWorkBindingSeed(
                            owner_user_id=assignment.owner_user_id,
                            universe_id=self._task.universe_id,
                            provider=assignment.provider,
                            credential_reference_digest=custody.reference_digest,
                            allowed_operations=(BACKGROUND_BRANCH_RUN_OPERATION,),
                            allowed_roles=roles,
                            assignment_generation=assignment.generation,
                            assignment_digest=assignment.assignment_digest,
                            max_invocations=max_invocations,
                            max_tokens=max_tokens,
                            max_cost_microunits=max_cost,
                            expires_at=expires_at,
                        )
                        provider_binding = (
                            None
                            if manifest_bindings
                            else _current_background_binding(
                                conn, provider_store=provider_store, seed=seed
                            )
                        )
                        if _is_open_provider(provider):
                            snapshot = None
                        else:
                            snapshot = snapshot_llm_subscription_credential(
                                universe_dir=universe_dir,
                                custody=custody,
                            )
                        authority = (
                            self._manifest_authority(
                                assignment,
                                binding,
                                attempt,
                                roles,
                                max_invocations,
                                max_tokens,
                                max_cost,
                                expires_at,
                                provider_store.timestamp(),
                            )
                            if manifest_bindings
                            else ProviderUniverseWorkAuthority(
                                root=ProviderUniverseWorkRoot(
                                    work_item_kind="background_attempt",
                                    work_item_id=attempt.attempt_id,
                                ),
                                binding=provider_binding,
                                principal_id=binding.authorizing_principal_id,
                                actor_id=str(binding.daemon_id),
                                operation=BACKGROUND_BRANCH_RUN_OPERATION,
                                role=role,
                                allowed_roles=roles,
                                # Cached-only peek inside the open transaction:
                                # the class is derived from the process verdict
                                # rather than written as a literal, and no
                                # socket can open under the write lock.
                                executor_class=_admitted_cloud_class(),
                                max_invocations=max_invocations,
                                max_tokens=max_tokens,
                                max_cost_microunits=max_cost,
                                expires_at=provider_binding.expires_at,
                                execution_subject=ExecutionSubject(
                                    kind=ExecutionSubjectKind.BRANCH_VERSION,
                                    ref=self._task.automation_branch_version,
                                    digest=self._task.automation_subject_digest,
                                ),
                                branch_def_id=self._task.branch_def_id,
                                branch_version_id=self._task.automation_branch_version,
                            )
                        )
                        prompt_digest = hashlib.sha256(
                            json.dumps(
                                [role, prompt, system],
                                ensure_ascii=False,
                            ).encode("utf-8")
                        ).hexdigest()
                        invocation_key = (
                            f"background-branch:{self._task.branch_task_id}:"
                            f"{invocation_index}:{prompt_digest}"
                        )
                        token_base, token_remainder = divmod(
                            max_tokens,
                            max_invocations,
                        )
                        cost_base, cost_remainder = divmod(
                            max_cost,
                            max_invocations,
                        )
                        token_share = token_base + int(invocation_index <= token_remainder)
                        cost_share = cost_base + int(invocation_index <= cost_remainder)
                        if token_share < 1 or cost_share < 1:
                            raise PermissionError(
                                "background provider invocation budget is exhausted"
                            )
                        claim_nonce_digest = (
                            "sha256:"
                            + hashlib.sha256(
                                json.dumps(
                                    [
                                        binding.binding_id,
                                        binding.binding_digest,
                                        attempt.attempt_id,
                                        authority.receipt_id
                                        if manifest_bindings
                                        else provider_binding.binding_id,
                                        self._task.branch_task_id,
                                    ],
                                    separators=(",", ":"),
                                ).encode("utf-8")
                            ).hexdigest()
                        )
                        lease_seconds = min(
                            3600,
                            int((_utc(str(row["lease_expires_at"])) - now).total_seconds()),
                        )
                        if lease_seconds < 1:
                            raise PermissionError("background provider claim lease is expired")
                        arm = (
                            provider_store._reserve_and_arm_background_branch_carrier_in_transaction
                        )
                        carrier = arm(
                            conn,
                            authority=authority,
                            worker_id=str(binding.daemon_id),
                            runtime_id=str(binding.runtime_id),
                            claim_nonce_digest=claim_nonce_digest,
                            lease_seconds=lease_seconds,
                            invocation_key=invocation_key,
                            role=role,
                            max_tokens=token_share,
                            max_cost_microunits=cost_share,
                            manifest_bindings=manifest_bindings,
                            selection=selection,
                            model_snapshot=model_snapshot,
                            needs_tools=needs_tools,
                        )
                        conn.commit()
                    except Exception:
                        conn.rollback()
                        raise
            assert carrier is not None
            yield carrier, snapshot.directory if snapshot else None, provider
        except ProviderAuthorityHeldError:
            raise
        except Exception as exc:
            if carrier is not None:
                raise
            if isinstance(exc, BackgroundExecutorIdentityError):
                try:
                    terminalize_background_queue_authority(
                        self._base_path,
                        self._task,
                        status="failed",
                        reason=exc.reason,
                    )
                except Exception:  # noqa: BLE001 - preserve the primary failure reason
                    logger.exception("background authority terminal projection failed")
            else:
                try:
                    _hold_background_authority(self._base_path, self._task)
                except Exception:  # noqa: BLE001 - preserve the primary hold reason
                    logger.exception("background authority hold projection failed")
            raise ProviderAuthorityHeldError(held) from exc
        finally:
            from tinyassets.credential_vault import cleanup_llm_credential_snapshot

            cleanup_llm_credential_snapshot(snapshot)

    def _resolved_policy(self, policy):
        """``policy`` with a bare access-method pin resolved to one accepted source.

        ``{"provider": "api_key_http", "model_id": ...}`` names the universe's
        single accepted source of that method, or the one of them offering the
        model (``providers.model_pins``). Resolved here, BEFORE the model
        snapshot, which needs the exact source to discover. Discovery runs only
        when several accepted sources share the method and a model must decide
        between them -- the read the owner's own turn already makes. An exact
        ref passes unchanged; `_manifest_member` still decides admission.
        """
        preferred = (policy or {}).get("preferred")
        if not isinstance(preferred, dict) or not str(preferred.get("provider") or "").strip():
            return policy
        provider = str(preferred["provider"]).strip()
        if ":" in provider:
            return policy
        from tinyassets.provider_assignment import (
            load_provider_assignment_in_transaction,
            provider_assignment_admission,
        )
        from tinyassets.providers.model_pins import resolve_pin_source
        from tinyassets.storage.provider_work_authority import SQLiteProviderWorkAuthorityStore

        universe = self._task.universe_id
        store = SQLiteProviderWorkAuthorityStore(self._base_path)
        with provider_assignment_admission().shared(self._base_path / universe):
            with store.connection() as conn:
                conn.execute("BEGIN")
                assignment = load_provider_assignment_in_transaction(conn, universe_id=universe)
        if assignment is None:
            return policy
        accepted = [member.provider for member in assignment.candidates]
        if provider in accepted:
            return policy
        model_id = str(preferred.get("model_id", preferred.get("model", "")) or "").strip()
        sources = dict.fromkeys(accepted)
        same_method = [ref for ref in accepted if ref.split(":", 1)[0] == provider]
        if len(same_method) > 1 and model_id:
            from tinyassets.providers.discovery_snapshot import refresh_model_discovery

            for ref in same_method:
                if not ref.startswith("api_key_http:"):
                    continue
                try:
                    found = refresh_model_discovery(
                        owner_user_id=assignment.owner_user_id, universe_id=universe,
                        definition_id=ref.removeprefix("api_key_http:"),
                    )
                except Exception:  # noqa: BLE001 - unknown models, so it cannot decide
                    continue
                sources[ref] = tuple(model.model_id for model in found.models.models)
        resolved = resolve_pin_source(provider, model_id, sources)
        return {**policy, "preferred": {**preferred, "provider": resolved}}

    def _manifest_member(
        self, conn, store, universe_dir, assignment, agent, roles, declared_providers, policy
    ):
        """Resolve actual owned members; the structural default is not a grant."""
        from tinyassets.provider_serving_binding import _current_selected_member_authority

        current = {}
        for member in assignment.candidates:
            try:
                observed, binding, custody = _current_selected_member_authority(
                    conn,
                    store=store,
                    universe_dir=universe_dir,
                    base_path=self._base_path,
                    owner_user_id=assignment.owner_user_id,
                    universe_id=assignment.universe_id,
                    agent=agent,
                    provider=member.provider,
                )
            except PermissionError:
                continue
            if observed != assignment:
                raise PermissionError("background model assignment changed")
            if set(roles) <= set(binding.allowed_roles):
                current[member.provider] = (member, binding, custody)
        if not current:
            raise PermissionError("background workflow requests an unavailable accepted provider")
        from tinyassets.providers.model_pins import resolve_pin_source

        # A bare access method (``api_key_http``) names this universe's single
        # source of that method; anything unresolvable refuses with the refs.
        sources = dict.fromkeys(current)
        # Read off the policy itself: `_authorize_launch` already resolved its
        # preferred pin model-aware (`_resolved_policy`), and the caller's set
        # still carries the bare name it was given.
        for declared in self._declared_policy_providers(policy):
            if resolve_pin_source(declared, "", sources) not in sources:
                raise PermissionError(
                    "background workflow requests an unavailable accepted provider"
                )
        preferred = (policy or {}).get("preferred", {})
        if not isinstance(preferred, dict):
            raise PermissionError("background model preference is invalid")
        provider = (
            resolve_pin_source(preferred["provider"], "", sources)
            if preferred.get("provider") else
            assignment.provider if assignment.provider in current else next(iter(current))
        )
        if provider not in current:
            raise PermissionError("background model source is unavailable")
        model_id = preferred.get("model_id", preferred.get("model", ""))
        if (
            "model_id" in preferred
            and "model" in preferred
            and preferred["model_id"] != preferred["model"]
        ):
            raise PermissionError("background model preference is conflicting")
        member, binding, custody = current[provider]
        selection = ProviderInvocationSelection(
            provider=provider,
            binding_id=binding.binding_id,
            binding_generation=binding.generation,
            binding_digest=binding.binding_digest,
            binding_revocation_generation=binding.revocation_generation,
            credential_reference_id=custody.reference_id,
            credential_reference_generation=custody.generation,
            credential_reference_digest=custody.reference_digest,
            assignment_generation=assignment.generation,
            assignment_digest=assignment.assignment_digest,
            manifest_digest=assignment.manifest_digest,
            member_digest=member.digest(assignment.universe_id, assignment.generation),
            model_id=model_id,
            executor_id=provider,
        )
        return tuple(value[1] for value in current.values()), binding, custody, selection

    def _manifest_authority(
        self,
        assignment,
        binding,
        attempt,
        roles,
        max_invocations,
        max_tokens,
        max_cost,
        expires_at,
        created_at,
    ):
        root = ProviderUniverseWorkRoot(
            work_item_kind="background_attempt", work_item_id=attempt.attempt_id
        )
        candidate = ProviderUniverseWorkReceipt(
            schema_version=4,
            authority_scope="manifest",
            manifest_digest=assignment.manifest_digest,
            receipt_id=provider_work_receipt_id(universe_id=self._task.universe_id, root=root),
            receipt_digest="sha256:" + "0" * 64,
            generation=1,
            state=ProviderWorkReceiptState.ACTIVE,
            work_item_kind=root.work_item_kind,
            work_item_id=root.work_item_id,
            binding_id=None,
            binding_generation=None,
            binding_digest=None,
            binding_revocation_generation=None,
            provider=None,
            credential_reference_digest=None,
            principal_id=binding.authorizing_principal_id,
            actor_id=str(binding.daemon_id),
            universe_id=self._task.universe_id,
            branch_def_id=self._task.branch_def_id,
            branch_version_id=self._task.automation_branch_version,
            assignment_generation=assignment.generation,
            assignment_digest=assignment.assignment_digest,
            # Manifest path: same cached-only admission as the non-manifest
            # branch above. `_manifest_authority` is called from inside
            # `_authorize_launch`'s transaction, so this is a peek, never a
            # resolve.
            executor_class=_admitted_cloud_class(),
            allowed_operations=(BACKGROUND_BRANCH_RUN_OPERATION,),
            allowed_roles=roles,
            max_invocations=max_invocations,
            max_tokens=max_tokens,
            max_cost_microunits=max_cost,
            expires_at=expires_at,
            created_at=created_at,
            execution_subject=ExecutionSubject(
                kind=ExecutionSubjectKind.BRANCH_VERSION,
                ref=self._task.automation_branch_version,
                digest=self._task.automation_subject_digest,
            ),
        )
        return replace(candidate, receipt_digest=candidate.expected_digest())


def authorize_background_served_provider_call(
    base_path: str | Path,
    claimed_task: Epoch2BranchTask,
    consumer_lease: AssignedConsumerLease,
) -> Any:
    """Return an exact-universe provider call; actual authority is fenced per call."""

    if not isinstance(claimed_task, Epoch2BranchTask):
        raise ValueError("claimed_task must be an Epoch2BranchTask")
    if not isinstance(consumer_lease, AssignedConsumerLease):
        raise ValueError("consumer_lease must be an AssignedConsumerLease")
    if claimed_task.claimed_by != consumer_lease.consumer_id:
        raise PermissionError("consumer lease does not own the claimed task")
    try:
        from domains.fantasy_daemon.phases._provider_stub import call_provider
    except ImportError as exc:
        raise PermissionError("provider bridge is unavailable") from exc
    from tinyassets.config import load_universe_config
    from tinyassets.providers.base import UniverseContext
    from tinyassets.providers.call import UniverseBoundProviderCall

    root = Path(base_path)
    universe_dir = root / claimed_task.universe_id
    session = _BackgroundAssignedProviderSession(root, claimed_task, consumer_lease, call_provider)
    return UniverseBoundProviderCall(
        session,
        UniverseContext(
            universe_dir=universe_dir,
            config=load_universe_config(universe_dir),
        ),
        BACKGROUND_BRANCH_RUN_OPERATION,
    )


__all__ = [
    "BACKGROUND_BRANCH_RUN_OPERATION",
    "BackgroundExecutorIdentityError",
    "authorize_background_served_provider_call",
    "load_background_executor_identity",
]
