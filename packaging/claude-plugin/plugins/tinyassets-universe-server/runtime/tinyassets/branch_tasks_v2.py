"""Protocol-v2 BranchTask adapter over transactional admission storage.

This module is the only queue-facing adapter for epoch 2. Legacy file-queue
code never receives its store handle. A successful claim is an internal
scheduling reservation only; it does not grant provider or execution
authority.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
import sqlite3
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from tinyassets.branch_tasks import BranchTask
from tinyassets.execution_subject import ExecutionSubject
from tinyassets.platform_runtime_provenance import (
    PLATFORM_NOT_CLOUD_REASON,
    cached_process_is_cloud_admitted,
    resolve_process_cloud_admission,
)
from tinyassets.sqlite_connection import ClosingConnection
from tinyassets.storage import DB_FILENAME
from tinyassets.storage.automation_activations import (
    AutomationActivationExecutor,
    AutomationActivationStore,
)
from tinyassets.storage.request_admissions import (
    OPERATOR_CAPABILITY,
    QUEUE_EPOCH,
    QUEUE_PROTOCOL_VERSION,
    RequestAdmissionStore,
    _quarantine_row_digest,
    _stale_task_integrity_digest,
    expected_idempotency_hash_re,
)


@dataclass
class Epoch2BranchTask(BranchTask):
    """BranchTask-compatible read model that retains epoch-2 identity."""

    admission_id: str = ""
    request_id: str = ""
    actor_id: str = ""
    queue_epoch: int = QUEUE_EPOCH
    protocol_version: int = QUEUE_PROTOCOL_VERSION
    automation_id: str = ""
    automation_activation_epoch: int = 0
    automation_executor_class: str = ""
    automation_subject_kind: str = ""
    automation_subject_ref: str = ""
    automation_subject_digest: str = ""
    automation_branch_version: str = ""
    automation_lease_id: str = ""
    claimed_at: str = ""


@dataclass(frozen=True)
class WorkerClaimDescriptor:
    """Release-derived worker identity presented for a conditional claim."""

    queue_protocol_version: int
    capabilities: frozenset[str] = field(default_factory=frozenset)
    worker_id: str = ""
    runtime_instance_id: str = ""
    boot_id: str = ""
    build_sha: str = ""
    config_hash: str = ""
    universe_id: str = ""
    expires_at: str = ""
    executor_class: AutomationActivationExecutor | None = None


@dataclass(frozen=True)
class WorkerClaimContext:
    """Canonical runtime identity paired with its immutable daemon owner."""

    descriptor: WorkerClaimDescriptor
    daemon_id: str


@dataclass(frozen=True)
class AssignedConsumerLease:
    """Boot-scoped daemon lease used only to fence queue ownership."""

    consumer_id: str
    lease_id: str
    expires_at: str

    def __post_init__(self) -> None:
        if not self.consumer_id.strip() or not self.lease_id.strip():
            raise ValueError("assigned consumer lease identity is required")
        if _parse_timestamp(self.expires_at) is None:
            raise ValueError("assigned consumer lease expiry is invalid")


DescriptorReader = Callable[
    [sqlite3.Connection, str],
    WorkerClaimDescriptor | None,
]
DESCRIPTOR_VALIDITY_SECONDS = 90
# A provider node can legally run for 15 minutes.  Claims therefore retain
# the established v1 30-minute safety envelope between node-boundary
# heartbeats; the short descriptor TTL still fences new claim authority.
EPOCH2_TASK_LEASE_SECONDS = 1800
# Flip only in the same change that wires the supervised daemon's selector,
# claim, and lifecycle paths to this adapter.  This constant is bundled with
# every runtime mirror, so capability publication never depends on an
# optional domain package.
EPOCH2_QUEUE_CONSUMER_READY = True
logger = logging.getLogger(__name__)
_BODY_DIGEST_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_SOUL_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_BODY_DIGEST_VERSION = "rfc8785-v1"
_PRIORITY_POLICY_VERSION = "operator-priority-v1"
_DIRECTED_AUTHORITY_SCOPES = frozenset({
    "owner",
    "delegated_host",
})
_TERMINAL_STATUSES = frozenset({"cancelled", "succeeded", "failed"})
_REQUEST_BODY_SCHEMA_VERSION = "request-admission-v2"
_TASK_INPUT_KEYS = frozenset({
    "branch_id",
    "directed_daemon_instruction",
    "pickup_incentive",
    "request_id",
    "request_type",
})
_BRANCH_TASK_ID_RE = re.compile(r"^bt2_[0-9a-f]{32}$")
_ADMISSION_ID_RE = re.compile(r"^adm_[0-9a-f]{32}$")
_REQUEST_ID_RE = re.compile(r"^req_[0-9a-f]{32}$")
_OPERATIONAL_STATES = (
    "awaiting_compatible_capacity",
    "invalid_operator_admission",
    "quarantined",
    "policy_parked",
)
_TASK_STATUSES = (
    "pending",
    "running",
    "cancel_requested",
    "cancelled",
    "succeeded",
    "failed",
)
_OPERATIONAL_DIAGNOSTIC_LIMIT = 100


@dataclass(frozen=True)
class QuarantineReceipt:
    row_digest: str
    branch_task_id: str
    reason: str
    first_seen_at: str
    last_seen_at: str


@dataclass(frozen=True)
class QuarantineMaintenanceResult:
    health: str
    scanned: int
    quarantined: int
    receipts: tuple[QuarantineReceipt, ...] = ()
    error_code: str = ""


@dataclass(frozen=True)
class Epoch2OperationalRead:
    summary: dict[str, Any]
    candidates: tuple[Epoch2BranchTask, ...]


@dataclass(frozen=True)
class StaleCapacityCancellationPlan:
    """CAS evidence for one reviewed stale epoch-2 task."""

    branch_task_id: str
    universe_id: str
    queued_at: str
    grant_generation: int
    body_digest: str
    row_digest: str


@dataclass(frozen=True)
class Epoch2ClaimedRequest:
    """Minimal private Request/task view bound to one live reservation."""

    request_id: str
    admission_id: str
    branch_task_id: str
    universe_id: str
    branch_def_id: str
    request_type: str
    text: str
    branch_id: str
    actor_id: str
    trigger_source: str
    accepted_priority_weight: float
    directed_daemon_id: str
    pickup_incentive: str
    directed_daemon_instruction: str
    claimed_by: str
    claimed_at: str
    lease_expires_at: str
    queued_at: str


class Epoch2BranchTaskAdapter:
    """Typed lifecycle operations for the epoch-2 transactional queue."""

    def __init__(
        self,
        base_path: Path | str,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.base_path = Path(base_path)
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._store = RequestAdmissionStore(
            self.base_path,
            clock=self._clock,
        )

    def list_candidates(
        self,
        *,
        universe_id: str = "",
        limit: int = 1000,
    ) -> list[Epoch2BranchTask]:
        return [
            _as_epoch2_task(row)
            for row in self._store.list_v2_candidates(
                universe_id=universe_id,
                limit=limit,
                integrity_check=lambda row: (
                    _classify_epoch2_row(row) is None
                ),
            )
        ]

    def plan_stale_capacity_cancellation(
        self,
        *,
        cutoff: datetime,
        capacity_matcher: Callable[[Epoch2BranchTask], bool],
        policy_matcher: Callable[[Epoch2BranchTask], bool] | None = None,
    ) -> list[StaleCapacityCancellationPlan]:
        """Read a mutation-free plan for retired cloud-capacity tasks."""
        cutoff = _as_utc(cutoff)
        database = self.base_path / DB_FILENAME
        if not database.is_file():
            return []
        uri = f"{database.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, factory=ClosingConnection) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            rows = self._store._v2_integrity_cursor(
                conn,
                pending_only=True,
                include_disabled=False,
            ).fetchall()

        planned: list[StaleCapacityCancellationPlan] = []
        for stored in rows:
            row = dict(stored)
            queued_at = _parse_timestamp(str(row.get("queued_at") or ""))
            if (
                queued_at is None
                or queued_at > cutoff
                or row.get("automation_executor_class") != "cloud"
                or _classify_epoch2_row(row) is not None
            ):
                continue
            task = _as_epoch2_task(row)
            if (
                policy_matcher is not None
                and not policy_matcher(task)
            ) or capacity_matcher(task):
                continue
            planned.append(StaleCapacityCancellationPlan(
                branch_task_id=task.branch_task_id,
                universe_id=task.universe_id,
                queued_at=task.queued_at,
                grant_generation=int(
                    row["linked_admission_grant_generation"]
                ),
                body_digest=str(row["linked_admission_body_digest"]),
                row_digest=_stale_task_integrity_digest(row),
            ))
        return sorted(planned, key=lambda item: item.branch_task_id)

    def list_live_claimed_requests(
        self,
        *,
        universe_id: str,
        worker_id: str,
        limit: int = 10,
    ) -> list[Epoch2ClaimedRequest]:
        """Read only canonical requests reserved by this worker right now."""
        return [
            Epoch2ClaimedRequest(
                request_id=str(row["request_id"]),
                admission_id=str(row["admission_id"]),
                branch_task_id=str(row["branch_task_id"]),
                universe_id=str(row["universe_id"]),
                branch_def_id=str(row["branch_def_id"]),
                request_type=str(row["request_type"]),
                text=str(row["text"]),
                branch_id=str(row["branch_id"]),
                actor_id=str(row["actor_id"]),
                trigger_source=str(row["trigger_source"]),
                accepted_priority_weight=float(
                    row["accepted_priority_weight"]
                ),
                directed_daemon_id=str(row["directed_daemon_id"]),
                pickup_incentive=str(row["pickup_incentive"]),
                directed_daemon_instruction=str(
                    row["directed_daemon_instruction"]
                ),
                claimed_by=str(row["claimed_by"]),
                claimed_at=str(row["claimed_at"]),
                lease_expires_at=str(row["lease_expires_at"]),
                queued_at=str(row["queued_at"]),
            )
            for row in self._store.list_live_claimed_v2_requests(
                universe_id=universe_id,
                worker_id=worker_id,
                limit=limit,
                integrity_check=lambda row: (
                    _classify_epoch2_row(row) is None
                ),
            )
        ]

    def has_active_claim(
        self,
        *,
        universe_id: str,
        worker_id: str,
    ) -> bool:
        """Return whether this worker owns running epoch-2 lifecycle work."""
        return self._store.has_active_v2_claim(
            universe_id=universe_id,
            worker_id=worker_id,
        )

    def list_worker_active_tasks(
        self,
        *,
        universe_id: str,
        worker_id: str,
        limit: int = 2,
    ) -> list[Epoch2BranchTask]:
        """Return live running/cancel-requested tasks for reconciliation."""
        return [
            _as_epoch2_task(row)
            for row in self._store.list_live_owned_v2_tasks(
                universe_id=universe_id,
                worker_id=worker_id,
                limit=limit,
                integrity_check=lambda row: (
                    _classify_epoch2_row(row) is None
                ),
            )
        ]

    def worker_claim_context(
        self,
        *,
        worker_id: str,
        runtime_instance_id: str,
        universe_id: str,
    ) -> WorkerClaimContext | None:
        """Read the exact server-owned worker tuple from canonical storage."""
        with self._store.connection() as conn:
            context = read_worker_claim_context(conn, worker_id)
        if context is None:
            return None
        descriptor = context.descriptor
        if (
            descriptor.runtime_instance_id != runtime_instance_id
            or descriptor.universe_id != universe_id
            or not _descriptor_is_live(
                descriptor,
                transaction_at=self._clock().isoformat(),
            )
        ):
            return None
        return context

    def get(self, branch_task_id: str) -> Epoch2BranchTask | None:
        row = self._store.get_v2_task(branch_task_id)
        return self._as_execution_task(row) if row is not None else None

    def _as_execution_task(
        self,
        row: Mapping[str, Any],
    ) -> Epoch2BranchTask:
        hydrated = dict(row)
        hydrated["linked_admission_actor_id"] = (
            self._store.get_v2_task_actor_id(str(row["branch_task_id"]))
        )
        task = _as_epoch2_task(hydrated)
        # Executor identity (worker + runtime) is what the cloud provider path
        # (prepare_claimed_cloud_provider_call -> runtime_matches_worker_provider)
        # authorizes against. It is NOT a persisted task column: derive it on every
        # execution-task read from the claimant's persisted worker queue descriptor,
        # so claim(), claim_assigned() AND a later get() all carry it. Before this
        # only a test set these two fields by hand, and every production claim
        # produced a task the cloud path rejected ("executor_worker_id must be a
        # non-empty string") — the concrete reason background execution never ran.
        claimed_by = str(row.get("claimed_by") or "").strip()
        if claimed_by and not task.executor_worker_id:
            task.executor_worker_id = claimed_by
            try:
                with self._store.connection() as conn:
                    context = read_worker_claim_context(conn, claimed_by)
            except Exception:  # noqa: BLE001 - identity hydration must never break a read
                context = None
            if context is not None:
                task.executor_runtime_id = context.descriptor.runtime_instance_id
        return task

    def claim(
        self,
        branch_task_id: str,
        *,
        descriptor: WorkerClaimDescriptor,
        descriptor_reader: DescriptorReader,
        lease_seconds: int = EPOCH2_TASK_LEASE_SECONDS,
    ) -> Epoch2BranchTask | None:
        if not _descriptor_shape_is_valid(descriptor):
            return None

        # Resolve platform admission BEFORE the write transaction opens, the
        # same ordering `claim_assigned` uses: the bounded metadata read must
        # never run under the SQLite write lock. This call decides nothing --
        # the single gate is the non-optional cloud-activation predicate in
        # `_transaction_allows_epoch2_lifecycle`, which reads only the cached
        # result.
        resolve_process_cloud_admission()

        def transaction_check(
            conn: sqlite3.Connection,
            task: Mapping[str, Any],
            transaction_at: str,
        ) -> bool:
            return _transaction_allows_epoch2_lifecycle(
                conn,
                task,
                transaction_at=transaction_at,
                descriptor=descriptor,
                descriptor_reader=descriptor_reader,
            )

        row = self._store.claim_v2_task(
            branch_task_id,
            worker_id=descriptor.worker_id,
            queue_protocol_version=descriptor.queue_protocol_version,
            capabilities=descriptor.capabilities,
            lease_seconds=lease_seconds,
            claim_check=transaction_check,
        )
        return self._as_execution_task(row) if row is not None else None

    def claim_assigned(
        self,
        candidate: Epoch2BranchTask,
        *,
        consumer_lease: AssignedConsumerLease,
        lease_seconds: int = EPOCH2_TASK_LEASE_SECONDS,
        authority_claim: Callable[..., bool] | None = None,
    ) -> Epoch2BranchTask | None:
        """CAS one exact automation task to a boot-scoped daemon consumer."""

        if not isinstance(candidate, Epoch2BranchTask) or not isinstance(
            consumer_lease, AssignedConsumerLease
        ):
            return None

        # Resolve platform admission BEFORE the write transaction opens, so the
        # bounded metadata read can never run under the SQLite write lock. The
        # CAS predicate below re-reads only the cached result. This call decides
        # nothing — there is exactly one gate, in the non-optional predicate, so
        # a refusal has a single definition and a single reason token.
        resolve_process_cloud_admission()

        def transaction_check(
            conn: sqlite3.Connection,
            task: Mapping[str, Any],
            transaction_at: str,
        ) -> bool:
            allowed = _transaction_allows_assigned_consumer(
                conn,
                task,
                transaction_at=transaction_at,
                candidate=candidate,
                consumer_lease=consumer_lease,
            )
            if not allowed or authority_claim is None:
                return allowed
            claimed_at = _parse_timestamp(transaction_at)
            if claimed_at is None:
                return False
            return authority_claim(
                conn,
                candidate,
                consumer_lease,
                claimed_at=transaction_at,
                lease_expires_at=(
                    claimed_at + timedelta(seconds=lease_seconds)
                ).isoformat(),
            )

        row = self._store.claim_v2_task(
            candidate.branch_task_id,
            worker_id=consumer_lease.consumer_id,
            queue_protocol_version=QUEUE_PROTOCOL_VERSION,
            capabilities=(OPERATOR_CAPABILITY,),
            lease_seconds=lease_seconds,
            claim_check=transaction_check,
        )
        return self._as_execution_task(row) if row is not None else None

    def explain_assigned_refusal(
        self,
        candidate: Epoch2BranchTask,
        *,
        consumer_lease: AssignedConsumerLease,
        lease_seconds: int = EPOCH2_TASK_LEASE_SECONDS,
    ) -> str | None:
        """Explain one still-pending refusal through a query-only snapshot."""

        database = self.base_path / DB_FILENAME
        if not database.is_file():
            return None
        uri = f"{database.resolve().as_uri()}?mode=ro"
        with sqlite3.connect(uri, uri=True, factory=ClosingConnection) as conn:
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA query_only = ON")
            conn.execute("BEGIN")
            transaction_at = self._clock().astimezone(timezone.utc).isoformat()
            row = self._store._v2_integrity_cursor(
                conn,
                pending_only=True,
                branch_task_id=candidate.branch_task_id,
                limit=1,
            ).fetchone()
            if row is None:
                conn.rollback()
                return None
            reason = _assigned_consumer_refusal_reason(
                conn,
                dict(row),
                transaction_at=transaction_at,
                candidate=candidate,
                consumer_lease=consumer_lease,
            )
            if reason is None:
                from tinyassets.background_served_provider import (
                    explain_background_queue_authority_in_transaction,
                )

                claimed_at = _parse_timestamp(transaction_at)
                assert claimed_at is not None
                reason = explain_background_queue_authority_in_transaction(
                    conn,
                    candidate,
                    consumer_lease,
                    claimed_at=transaction_at,
                    lease_expires_at=(
                        claimed_at + timedelta(seconds=lease_seconds)
                    ).isoformat(),
                )
            conn.rollback()
        return reason

    def release_assigned(
        self,
        claimed_task: Epoch2BranchTask,
        *,
        consumer_lease: AssignedConsumerLease,
        reason: str,
    ) -> bool:
        """Release only the exact live claim; never mint another attempt."""

        if claimed_task.claimed_by != consumer_lease.consumer_id:
            return False
        return self._store.release_v2_task_claim(
            claimed_task.branch_task_id,
            worker_id=consumer_lease.consumer_id,
            claimed_at=claimed_task.claimed_at,
            reason=reason,
        )

    def resume(
        self,
        branch_task_id: str,
        *,
        descriptor: WorkerClaimDescriptor,
        descriptor_reader: DescriptorReader,
    ) -> Epoch2BranchTask | None:
        """Revalidate a live claim before material work resumes."""
        if not _descriptor_shape_is_valid(descriptor):
            return None

        # Resolve platform admission BEFORE the write transaction opens, the
        # same ordering `claim_assigned` uses: the bounded metadata read must
        # never run under the SQLite write lock. This call decides nothing --
        # the single gate is the non-optional cloud-activation predicate in
        # `_transaction_allows_epoch2_lifecycle`, which reads only the cached
        # result.
        resolve_process_cloud_admission()

        def transaction_check(
            conn: sqlite3.Connection,
            task: Mapping[str, Any],
            transaction_at: str,
        ) -> bool:
            return _transaction_allows_epoch2_lifecycle(
                conn,
                task,
                transaction_at=transaction_at,
                descriptor=descriptor,
                descriptor_reader=descriptor_reader,
            )

        row = self._store.read_live_v2_task_for_resume(
            branch_task_id,
            worker_id=descriptor.worker_id,
            resume_check=transaction_check,
        )
        return self._as_execution_task(row) if row is not None else None

    def heartbeat(
        self,
        branch_task_id: str,
        *,
        worker_id: str,
        lease_seconds: int = EPOCH2_TASK_LEASE_SECONDS,
    ) -> Epoch2BranchTask | None:
        row = self._store.heartbeat_v2_task(
            branch_task_id,
            worker_id=worker_id,
            lease_seconds=lease_seconds,
        )
        return _as_epoch2_task(row) if row is not None else None

    def request_cancel(
        self,
        branch_task_id: str,
    ) -> Epoch2BranchTask:
        return _as_epoch2_task(
            self._store.request_v2_cancel(branch_task_id)
        )

    def finish(
        self,
        branch_task_id: str,
        *,
        worker_id: str,
        status: str,
        detail: Mapping[str, Any] | None = None,
    ) -> Epoch2BranchTask:
        if status not in {"cancelled", "succeeded", "failed"}:
            raise ValueError("status must be a terminal epoch-2 state")
        return _as_epoch2_task(
            self._store.transition_task(
                branch_task_id,
                expected_statuses={"running", "cancel_requested"},
                new_status=status,
                detail=detail,
                worker_id=worker_id,
                cancel_wins=True,
            )
        )

    def recover_expired(
        self,
        *,
        target_recovery_guard: (
            Callable[[Epoch2BranchTask], bool] | None
        ) = None,
    ) -> list[Epoch2BranchTask]:
        approved_rows = None
        if target_recovery_guard is not None:
            snapshots = self._store.list_expired_v2_tasks()
            approved_rows = {
                str(row["branch_task_id"]): row
                for row in snapshots
                if target_recovery_guard(_as_epoch2_task(row))
            }
            if not approved_rows:
                return []
        return [
            _as_epoch2_task(row)
            for row in self._store.recover_expired_v2_tasks(
                approved_rows=approved_rows,
            )
        ]

    def maintain_quarantine(
        self,
        *,
        limit: int = 1000,
        fault_injector: (
            Callable[[str, sqlite3.Connection], None] | None
        ) = None,
    ) -> QuarantineMaintenanceResult:
        """Run the separate invalid-row maintenance pass with red health."""
        try:
            result = self._store.maintain_v2_quarantine(
                classifier=_classify_epoch2_row,
                limit=limit,
                fault_injector=fault_injector,
            )
        except Exception:  # noqa: BLE001 - health must stay bounded and red
            logger.exception("epoch-2 quarantine maintenance failed")
            return QuarantineMaintenanceResult(
                health="red",
                scanned=0,
                quarantined=0,
                error_code="quarantine_persistence_failed",
            )
        receipts = tuple(
            QuarantineReceipt(
                row_digest=str(receipt["row_digest"]),
                branch_task_id=str(receipt["branch_task_id"]),
                reason=str(receipt["reason"]),
                first_seen_at=str(receipt["first_seen_at"]),
                last_seen_at=str(receipt["last_seen_at"]),
            )
            for receipt in result["receipts"]
        )
        return QuarantineMaintenanceResult(
            health="green",
            scanned=int(result["scanned"]),
            quarantined=len(receipts),
            receipts=receipts,
        )

    def compact_terminal_details(
        self,
        *,
        terminal_before: str,
        compacted_at: str,
        limit: int = 1000,
    ) -> int:
        return self._store.compact_terminal_details(
            terminal_before=terminal_before,
            compacted_at=compacted_at,
            classifier=_classify_epoch2_row,
            limit=limit,
        )

    def operational_read(
        self,
        *,
        universe_id: str,
        capacity_matcher: Callable[[Epoch2BranchTask], bool],
        policy_matcher: Callable[[Epoch2BranchTask], bool] | None = None,
        authority_refusal_matcher: Callable[[Epoch2BranchTask], str] | None = None,
        include_unscoped_invalid: bool = False,
        snapshot_hook: Callable[[], None] | None = None,
    ) -> Epoch2OperationalRead:
        """Return one bounded, transactionally consistent operational read."""
        stored = self._store.read_v2_operational_data(
            universe_id=universe_id,
            include_unscoped_invalid=include_unscoped_invalid,
            snapshot_hook=snapshot_hook,
        )
        lifecycle_counts = {
            status: 0 for status in (*_TASK_STATUSES, "unknown")
        }
        lifecycle_oldest_age_s = {
            status: 0 for status in (*_TASK_STATUSES, "unknown")
        }
        unknown_lifecycle_status_counts: dict[str, int] = {}
        state_counts = {state: 0 for state in _OPERATIONAL_STATES}
        oldest_age_s = {state: 0 for state in _OPERATIONAL_STATES}
        reason_counts: dict[str, dict[str, int]] = {
            state: {} for state in _OPERATIONAL_STATES
        }
        diagnostics: list[dict[str, str]] = []
        candidates: list[Epoch2BranchTask] = []
        eligible_pending_count = 0
        valid_pending_count = 0
        now = self._clock()
        if now.tzinfo is None:
            now = now.replace(tzinfo=timezone.utc)
        now = now.astimezone(timezone.utc)
        for status, data in stored["lifecycle"].items():
            lifecycle_status = (
                status if status in _TASK_STATUSES else "unknown"
            )
            count = int(data["count"])
            lifecycle_counts[lifecycle_status] += count
            if lifecycle_status == "unknown":
                unknown_lifecycle_status_counts[status] = count
            queued_at = _parse_timestamp(data["oldest_queued_at"])
            if queued_at is not None:
                lifecycle_oldest_age_s[lifecycle_status] = max(
                    lifecycle_oldest_age_s[lifecycle_status],
                    int(max(0.0, (now - queued_at).total_seconds())),
                )

        for row in stored["active_rows"]:
            queued_at = _parse_timestamp(str(row.get("queued_at") or ""))
            age_s = (
                int(max(0.0, (now - queued_at).total_seconds()))
                if queued_at is not None
                else 0
            )
            invalid_reason = _classify_epoch2_row(row)
            task = (
                _as_epoch2_task(row)
                if invalid_reason is None
                else None
            )
            has_receipt = bool(
                row.get("linked_quarantine_receipt_exists")
                and row.get("linked_quarantine_row_digest")
            )
            authority_refusal = (
                authority_refusal_matcher(task)
                if task is not None and authority_refusal_matcher is not None
                else ""
            )
            if row.get("disabled") and has_receipt:
                operational_state = "quarantined"
                reason = str(
                    row.get("linked_quarantine_reason")
                    or row.get("quarantine_reason")
                    or invalid_reason
                    or "quarantined"
                )
            elif invalid_reason:
                operational_state = "invalid_operator_admission"
                reason = invalid_reason
            elif (
                row.get("disabled")
                or (
                    task is not None
                    and policy_matcher is not None
                    and not policy_matcher(task)
                )
            ):
                operational_state = "policy_parked"
                reason = (
                    "disabled" if row.get("disabled") else "policy_disabled"
                )
            elif (
                row.get("status") == "pending"
                and task is not None
                and authority_refusal
            ):
                operational_state = _refusal_operational_state(authority_refusal)
                reason = authority_refusal
            elif (
                row.get("status") == "pending"
                and task is not None
                and not capacity_matcher(task)
            ):
                operational_state = "awaiting_compatible_capacity"
                reason = "no_live_compatible_worker"
            else:
                operational_state = ""
                reason = ""
            if (
                task is not None
                and not row.get("disabled")
                and (policy_matcher is None or policy_matcher(task))
                and task.status == "pending"
            ):
                candidates.append(task)
                valid_pending_count += 1
                if not authority_refusal and capacity_matcher(task):
                    eligible_pending_count += 1

            if not operational_state:
                continue
            state_counts[operational_state] = (
                state_counts.get(operational_state, 0) + 1
            )
            state_reasons = reason_counts.setdefault(operational_state, {})
            state_reasons[reason] = state_reasons.get(reason, 0) + 1
            oldest_age_s[operational_state] = max(
                oldest_age_s.get(operational_state, 0),
                age_s,
            )
            if len(diagnostics) >= _OPERATIONAL_DIAGNOSTIC_LIMIT:
                continue
            digest = str(
                row.get("linked_quarantine_row_digest")
                or _quarantine_row_digest(row)
            )
            branch_task_id = str(row.get("branch_task_id") or "")
            if _BRANCH_TASK_ID_RE.fullmatch(branch_task_id) is None:
                branch_task_id = f"quarantined-task-{digest[:32]}"
            diagnostics.append({
                "branch_task_id": branch_task_id,
                "row_digest": digest,
                "operational_state": operational_state,
                "reason": reason,
            })

        active_count = sum(
            lifecycle_counts[status]
            for status in (
                "pending",
                "running",
                "cancel_requested",
                "unknown",
            )
        )
        integrity_scope_complete = bool(
            stored["integrity_scope_complete"]
        )
        summary = {
            "available": True,
            "queue_epoch": QUEUE_EPOCH,
            "depth": sum(lifecycle_counts.values()),
            "lifecycle_counts": lifecycle_counts,
            "lifecycle_oldest_age_s": lifecycle_oldest_age_s,
            "unknown_lifecycle_status_counts": (
                unknown_lifecycle_status_counts
            ),
            "operational_state_counts": state_counts,
            "operational_oldest_age_s": oldest_age_s,
            "operational_reason_counts": reason_counts,
            "valid_pending_count": valid_pending_count,
            "eligible_pending_count": eligible_pending_count,
            "operational_counts_authoritative": (
                not stored["active_scan_overflow"]
                and integrity_scope_complete
                and lifecycle_counts["unknown"] == 0
            ),
            "integrity_scope_complete": integrity_scope_complete,
            "unclassified_active_count": max(
                0,
                active_count - len(stored["active_rows"]),
            ),
            "active_scan_limit": int(stored["active_scan_limit"]),
            "diagnostics": diagnostics,
            "diagnostics_truncated": (
                sum(state_counts.values()) > len(diagnostics)
                or stored["active_scan_overflow"]
            ),
        }
        if "unscoped_invalid_count" in stored:
            summary["unscoped_invalid_count"] = int(
                stored["unscoped_invalid_count"]
            )
        return Epoch2OperationalRead(
            summary=summary,
            candidates=tuple(candidates),
        )

    def operational_snapshot(
        self,
        *,
        universe_id: str,
        compatible_capacity: bool,
    ) -> dict[str, Any]:
        """Compatibility wrapper for callers that have one capacity class."""
        return self.operational_read(
            universe_id=universe_id,
            capacity_matcher=lambda _task: compatible_capacity,
        ).summary

    def delete_universe(self, universe_id: str) -> int:
        return self._store.delete_universe(universe_id)


def _descriptor_shape_is_valid(
    descriptor: WorkerClaimDescriptor,
) -> bool:
    if descriptor.queue_protocol_version != QUEUE_PROTOCOL_VERSION:
        return False
    if OPERATOR_CAPABILITY not in descriptor.capabilities:
        return False
    required = (
        descriptor.worker_id,
        descriptor.runtime_instance_id,
        descriptor.boot_id,
        descriptor.build_sha,
        descriptor.config_hash,
        descriptor.universe_id,
        descriptor.expires_at,
    )
    if not all(str(value or "").strip() for value in required):
        return False
    if (
        descriptor.executor_class is not None
        and not isinstance(
            descriptor.executor_class,
            AutomationActivationExecutor,
        )
    ):
        return False
    return _parse_timestamp(descriptor.expires_at) is not None


def read_worker_claim_context(
    conn: sqlite3.Connection,
    worker_id: str,
) -> WorkerClaimContext | None:
    """Resolve one live-capable runtime using the caller's transaction.

    Queue possession never creates this identity.  The runtime registry and
    its supervisor-refreshed release descriptor are read inside the same
    transaction that conditionally claims the task.
    """
    clean_worker_id = str(worker_id or "").strip()
    if not clean_worker_id:
        return None
    try:
        rows = conn.execute(
            """
            SELECT instance_id, universe_id, author_id, status, metadata_json
            FROM author_runtime_instances
            WHERE status = 'provisioned'
            ORDER BY created_at DESC, instance_id DESC
            """
        ).fetchall()
    except sqlite3.OperationalError:
        return None

    owned: list[
        tuple[Mapping[str, Any], Mapping[str, Any], Any]
    ] = []
    for row in rows:
        try:
            metadata = json.loads(str(row["metadata_json"] or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if not isinstance(metadata, dict):
            continue
        if str(metadata.get("worker_id") or "").strip() != clean_worker_id:
            continue
        raw_descriptor = metadata.get("queue_protocol_descriptor")
        if raw_descriptor is not None:
            owned.append((row, metadata, raw_descriptor))
    if len(owned) != 1:
        return None
    row, metadata, raw_descriptor = owned[0]
    if not isinstance(raw_descriptor, dict):
        return None
    try:
        executor_class = AutomationActivationExecutor(
            str(metadata.get("automation_executor_class") or "")
        )
        capabilities = raw_descriptor["capabilities"]
        if not isinstance(capabilities, list):
            return None
        descriptor = WorkerClaimDescriptor(
            queue_protocol_version=int(
                raw_descriptor["queue_protocol_version"]
            ),
            capabilities=frozenset(str(value) for value in capabilities),
            worker_id=str(raw_descriptor["worker_id"]),
            runtime_instance_id=str(raw_descriptor["runtime_instance_id"]),
            boot_id=str(raw_descriptor["boot_id"]),
            build_sha=str(raw_descriptor["build_sha"]),
            config_hash=str(raw_descriptor["config_hash"]),
            universe_id=str(raw_descriptor["universe_id"]),
            expires_at=str(raw_descriptor["expires_at"]),
            executor_class=executor_class,
        )
    except (KeyError, TypeError, ValueError):
        return None
    instance_id = str(row["instance_id"])
    universe_id = str(row["universe_id"])
    author_id = str(row["author_id"])
    daemon_id = (
        "daemon::" + author_id[len("author::"):]
        if author_id.startswith("author::")
        else author_id
    )
    if (
        descriptor.worker_id != clean_worker_id
        or descriptor.runtime_instance_id != instance_id
        or descriptor.universe_id != universe_id
        or not daemon_id
    ):
        return None
    return WorkerClaimContext(descriptor=descriptor, daemon_id=daemon_id)


def read_worker_claim_descriptor(
    conn: sqlite3.Connection,
    worker_id: str,
) -> WorkerClaimDescriptor | None:
    context = read_worker_claim_context(conn, worker_id)
    return context.descriptor if context is not None else None


def _transaction_allows_epoch2_lifecycle(
    conn: sqlite3.Connection,
    task: Mapping[str, Any],
    *,
    transaction_at: str,
    descriptor: WorkerClaimDescriptor,
    descriptor_reader: DescriptorReader,
) -> bool:
    if _classify_epoch2_row(task) is not None:
        return False
    if task["universe_id"] != descriptor.universe_id:
        return False
    trusted = descriptor_reader(conn, descriptor.worker_id)
    if not (
        trusted == descriptor
        and trusted is not None
        and _descriptor_is_live(
            trusted,
            transaction_at=transaction_at,
        )
    ):
        return False
    activation_fields = (
        task.get("automation_id"),
        task.get("automation_activation_epoch"),
        task.get("automation_executor_class"),
        task.get("automation_subject_kind"),
        task.get("automation_subject_ref"),
        task.get("automation_subject_digest"),
        task.get("automation_branch_version"),
        task.get("automation_lease_id"),
    )
    if not any(value is not None for value in activation_fields):
        return True
    # Cloud-class activations only. Two arms are deliberately NOT gated here:
    # a generic task with no activation fields returned above, and a `tray`
    # activation falls through this predicate untouched. Measured at base
    # e389505b: a tray-class task with a matching tray descriptor claims
    # successfully on a not_cloud AND on an unobserved process, so refusing it
    # here would be a silent policy expansion this change does not own. The
    # row's own `automation_executor_class` is the discriminator, and
    # `_classify_epoch2_row` above has already proved it is exactly one of
    # `tray`/`cloud` with every activation field populated.
    #
    # Cached-only, because this runs inside the claim write transaction; every
    # caller resolves before opening it. An unobserved process peeks `None` and
    # is refused -- "we never looked" is not cloud.
    #
    # Non-optional on purpose: the persisted descriptor below is trusted for
    # worker identity and liveness, and `descriptor_reader` is caller-supplied,
    # so descriptor trust alone would let a live cloud-stamped row authorize a
    # cloud-class claim from an unadmitted process.
    if (
        task["automation_executor_class"] == AutomationActivationExecutor.CLOUD.value
        and not cached_process_is_cloud_admitted()
    ):
        return False
    if (
        trusted.executor_class is None
        or trusted.executor_class.value != task["automation_executor_class"]
    ):
        return False
    active = conn.execute(
        """
        SELECT 1
        FROM branch_tasks_v2
        WHERE universe_id = ? AND automation_id = ?
          AND branch_task_id != ?
          AND status IN ('running', 'cancel_requested')
          AND disabled = 0
        LIMIT 1
        """,
        (
            task["universe_id"],
            task["automation_id"],
            task["branch_task_id"],
        ),
    ).fetchone()
    if active is not None:
        return False
    try:
        authority_owner = conn.execute(
            """
            SELECT state FROM background_branch_authority_owners
            WHERE owner_kind = 'queue_task' AND owner_id = ? LIMIT 1
            """,
            (task["branch_task_id"],),
        ).fetchone()
    except sqlite3.OperationalError as exc:
        # The background-authority schema is created lazily by the assigned-queue
        # consumer's own store, and ONLY when the consumer is enabled and writes an
        # owner. When the consumer is dark the table need not exist — a missing table
        # means no owner holds authority, so the shared epoch-2 claim proceeds. This is
        # what lets the dark consumer create ZERO schema at migrate time (Codex #6, #2516).
        if "no such table" not in str(exc).lower():
            raise
        authority_owner = None
    if authority_owner is not None and authority_owner["state"] == "target_authority_held":
        return False
    return AutomationActivationStore.validate_claim_in_transaction(
        conn,
        universe_id=str(task["universe_id"]),
        automation_id=str(task["automation_id"]),
        epoch=int(task["automation_activation_epoch"]),
        executor_class=trusted.executor_class,
        subject=ExecutionSubject.from_dict({
            "kind": task["automation_subject_kind"],
            "ref": task["automation_subject_ref"],
            "digest": task["automation_subject_digest"],
        }),
        lease_id=str(task["automation_lease_id"]),
    )


def _transaction_allows_assigned_consumer(
    conn: sqlite3.Connection,
    task: Mapping[str, Any],
    *,
    transaction_at: str,
    candidate: Epoch2BranchTask,
    consumer_lease: AssignedConsumerLease,
) -> bool:
    """Validate immutable task + activation facts inside the claim transaction."""

    return _assigned_consumer_refusal_reason(
        conn,
        task,
        transaction_at=transaction_at,
        candidate=candidate,
        consumer_lease=consumer_lease,
    ) is None


def _refusal_operational_state(reason: str) -> str:
    """Type a consumer-recorded refusal for the status view: only authority
    predicates mean 'awaiting_background_authority'; a task this consumer is not
    the executor for is 'awaiting_compatible_executor'; a consumer failure is a
    'consumer_error' (Codex ADAPT on #2543: a tray task is not awaiting authority)."""
    if reason.startswith(("claim_error:", "explain_error:")):
        return "consumer_error"
    if reason == "refusal_unexplained":
        return "consumer_error"
    if reason.startswith(("requires_executor_class:", "consumer_not_applicable:")):
        return "awaiting_compatible_executor"
    return "awaiting_background_authority"


def _assigned_consumer_refusal_reason(
    conn: sqlite3.Connection,
    task: Mapping[str, Any],
    *,
    transaction_at: str,
    candidate: Epoch2BranchTask,
    consumer_lease: AssignedConsumerLease,
) -> str | None:
    """Return the first assigned-claim predicate that fails."""

    # Platform admission, read from the already-resolved process-owned verdict.
    # Cached-only on purpose: this runs inside the claim write transaction, so
    # it must not open a socket (design.md § Enforcement sites (A)). The
    # resolution itself happens in `claim_assigned` before the transaction is
    # opened. An unobserved process peeks `None` and is refused — "we never
    # looked" is not cloud. This predicate is the non-optional one, shared with
    # `explain_assigned_refusal`, so the diagnostic reports the same token.
    if not cached_process_is_cloud_admitted():
        return PLATFORM_NOT_CLOUD_REASON

    now = _parse_timestamp(transaction_at)
    consumer_expiry = _parse_timestamp(consumer_lease.expires_at)
    if now is None or consumer_expiry is None or consumer_expiry <= now:
        return "consumer_lease_invalid"
    if _classify_epoch2_row(task) is not None:
        return "task_not_claimable"
    exact_fields = {
        "branch_task_id": candidate.branch_task_id,
        "admission_id": candidate.admission_id,
        "request_id": candidate.request_id,
        "universe_id": candidate.universe_id,
        "branch_def_id": candidate.branch_def_id,
        "automation_id": candidate.automation_id,
        "automation_activation_epoch": candidate.automation_activation_epoch,
        "automation_executor_class": candidate.automation_executor_class,
        "automation_subject_kind": candidate.automation_subject_kind,
        "automation_subject_ref": candidate.automation_subject_ref,
        "automation_subject_digest": candidate.automation_subject_digest,
        "automation_branch_version": candidate.automation_branch_version,
        "automation_lease_id": candidate.automation_lease_id,
    }
    if any(task.get(field) != value for field, value in exact_fields.items()):
        return "candidate_mismatch"
    if not candidate.automation_id or candidate.automation_executor_class != "cloud":
        return "not_cloud_automation"
    active = conn.execute(
        """
        SELECT 1 FROM branch_tasks_v2
        WHERE universe_id = ? AND automation_id = ?
          AND branch_task_id != ?
          AND status IN ('running', 'cancel_requested') AND disabled = 0
        LIMIT 1
        """,
        (candidate.universe_id, candidate.automation_id, candidate.branch_task_id),
    ).fetchone()
    if active is not None:
        return "automation_already_active"
    if not AutomationActivationStore.validate_claim_in_transaction(
        conn,
        universe_id=candidate.universe_id,
        automation_id=candidate.automation_id,
        epoch=candidate.automation_activation_epoch,
        executor_class=AutomationActivationExecutor.CLOUD,
        subject=ExecutionSubject.from_dict(
            {
                "kind": candidate.automation_subject_kind,
                "ref": candidate.automation_subject_ref,
                "digest": candidate.automation_subject_digest,
            }
        ),
        lease_id=candidate.automation_lease_id,
    ):
        return "activation_claim_invalid"
    return None


def _descriptor_is_live(
    descriptor: WorkerClaimDescriptor,
    *,
    transaction_at: str,
) -> bool:
    if not _descriptor_shape_is_valid(descriptor):
        return False
    now = _parse_timestamp(transaction_at)
    expires = _parse_timestamp(descriptor.expires_at)
    if now is None or expires is None:
        return False
    remaining = expires - now
    return timedelta(0) < remaining <= timedelta(
        seconds=DESCRIPTOR_VALIDITY_SECONDS
    )


def _parse_timestamp(value: str) -> datetime | None:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _classify_epoch2_row(
    row: Mapping[str, Any],
) -> str | None:
    if (
        row.get("linked_quarantine_receipt_exists")
        and not row.get("disabled")
    ):
        return "invalid_operator_admission"
    if (
        type(row.get("queue_epoch")) is not int
        or type(row.get("protocol_version")) is not int
        or row.get("queue_epoch") != QUEUE_EPOCH
        or row.get("protocol_version") != QUEUE_PROTOCOL_VERSION
    ):
        return "unsupported_protocol"
    required = (
        "branch_task_id",
        "admission_id",
        "request_id",
        "universe_id",
        "branch_def_id",
        "queued_at",
    )
    if not all(
        isinstance(row.get(field), str) and bool(row[field].strip())
        for field in required
    ):
        return "incomplete"
    if (
        _BRANCH_TASK_ID_RE.fullmatch(row["branch_task_id"]) is None
        or _ADMISSION_ID_RE.fullmatch(row["admission_id"]) is None
        or _REQUEST_ID_RE.fullmatch(row["request_id"]) is None
        or not _is_path_safe_universe_id(row["universe_id"])
    ):
        return "incomplete"
    if _parse_timestamp(row["queued_at"]) is None:
        return "incomplete"
    if row.get("trigger_source") not in {
        "operator_request",
        "user_request",
        "owner_queued",
    }:
        return "incomplete"
    activation_fields = (
        row.get("automation_id"),
        row.get("automation_activation_epoch"),
        row.get("automation_executor_class"),
        row.get("automation_subject_kind"),
        row.get("automation_subject_ref"),
        row.get("automation_subject_digest"),
        row.get("automation_branch_version"),
        row.get("automation_lease_id"),
    )
    populated_activation_fields = tuple(
        value is not None for value in activation_fields
    )
    if any(populated_activation_fields) and not all(
        populated_activation_fields
    ):
        return "incomplete"
    if all(populated_activation_fields):
        if (
            not isinstance(row["automation_id"], str)
            or not row["automation_id"].strip()
            or type(row["automation_activation_epoch"]) is not int
            or row["automation_activation_epoch"] < 0
            or row["automation_executor_class"] not in {"tray", "cloud"}
            or row["automation_subject_kind"] != "branch_version"
            or not isinstance(row["automation_subject_ref"], str)
            or not row["automation_subject_ref"].strip()
            or not isinstance(row["automation_subject_digest"], str)
            or not _BODY_DIGEST_RE.fullmatch(row["automation_subject_digest"])
            or not isinstance(row["automation_branch_version"], str)
            or not row["automation_branch_version"].strip()
            or row["automation_branch_version"] != row["automation_subject_ref"]
            or not isinstance(row["automation_lease_id"], str)
            or not row["automation_lease_id"].strip()
        ):
            return "incomplete"
    if row.get("status") not in {
        "pending",
        "running",
        "cancel_requested",
        "cancelled",
        "succeeded",
        "failed",
    }:
        return "incomplete"
    weight = row.get("priority_weight")
    if (
        isinstance(weight, bool)
        or not isinstance(weight, (int, float))
        or not math.isfinite(float(weight))
        or not 0 <= float(weight) <= 100
    ):
        return "incomplete"
    decoded_documents: dict[str, dict[str, Any]] = {}
    for raw_field, decoded_field in (
        ("inputs_json", "inputs"),
        ("detail_json", "detail"),
    ):
        if raw_field in row:
            try:
                value = json.loads(str(row[raw_field]))
            except (TypeError, ValueError, json.JSONDecodeError):
                return "incomplete"
        else:
            value = row.get(decoded_field)
        if not isinstance(value, dict):
            return "incomplete"
        decoded_documents[decoded_field] = value
    linked_weight = row.get("linked_admission_priority_weight")
    linked_generation = row.get("linked_admission_grant_generation")
    key_hash = row.get("linked_admission_key_hash")
    body_digest = row.get("linked_admission_body_digest")
    linkage_matches = (
        row.get("linked_admission_id") == row.get("admission_id")
        and row.get("linked_admission_request_id") == row.get("request_id")
        and row.get("linked_admission_task_id") == row.get("branch_task_id")
        and row.get("linked_admission_universe_id")
        == row.get("universe_id")
        and row.get("linked_admission_trigger_source")
        == row.get("trigger_source")
        and isinstance(linked_weight, (int, float))
        and not isinstance(linked_weight, bool)
        and math.isfinite(float(linked_weight))
        and float(linked_weight) == float(weight)
        and row.get("linked_request_id") == row.get("request_id")
        and row.get("linked_request_universe_id") == row.get("universe_id")
        and row.get("linked_admission_actor_id")
        == row.get("linked_request_user_id")
        and row.get("linked_request_preferred_author_id")
        == (row.get("directed_daemon_id") or None)
        and row.get("linked_admission_state") == "committed"
        and row.get("linked_request_status") == row.get("status")
        and isinstance(key_hash, str)
        and expected_idempotency_hash_re(
            server_derived=all(populated_activation_fields),
        ).fullmatch(key_hash) is not None
        and isinstance(body_digest, str)
        and _BODY_DIGEST_RE.fullmatch(body_digest) is not None
        and row.get("linked_admission_body_digest_version")
        == _BODY_DIGEST_VERSION
        and row.get("linked_admission_policy_version")
        == _PRIORITY_POLICY_VERSION
        and all(
            isinstance(row.get(field), str) and bool(row[field].strip())
            for field in (
                "linked_admission_tenant_id",
                "linked_admission_actor_id",
            )
        )
        and type(linked_generation) is int
        and linked_generation >= 0
        and (float(weight) == 0 or linked_generation > 0)
    )
    if not linkage_matches:
        return "invalid_operator_admission"
    if not _terminal_lifecycle_matches(row):
        return "invalid_operator_admission"
    if row.get("linked_admission_compacted_at") is not None:
        return (
            None
            if _compacted_terminal_matches(
                row,
                inputs=decoded_documents["inputs"],
                detail=decoded_documents["detail"],
            )
            else "invalid_operator_admission"
        )
    try:
        receipt = json.loads(str(row.get("linked_admission_receipt_json")))
        result = json.loads(str(row.get("linked_admission_result_json")))
        request_metadata = json.loads(
            str(row.get("linked_request_metadata_json"))
        )
    except (TypeError, ValueError, json.JSONDecodeError):
        return "invalid_operator_admission"
    if (
        not isinstance(receipt, dict)
        or not receipt
        or not isinstance(result, dict)
        or not isinstance(request_metadata, dict)
    ):
        return "invalid_operator_admission"
    receipt_activation = receipt.get("_automation_activation")
    if receipt_activation is None:
        if any(populated_activation_fields):
            return "invalid_operator_admission"
    elif (
        not all(populated_activation_fields)
        or not isinstance(receipt_activation, dict)
        or receipt_activation
        != {
            "automation_id": row["automation_id"],
            "epoch": row["automation_activation_epoch"],
            "executor_class": row["automation_executor_class"],
            "subject": {
                "kind": row["automation_subject_kind"],
                "ref": row["automation_subject_ref"],
                "digest": row["automation_subject_digest"],
            },
            "lease_id": row["automation_lease_id"],
        }
    ):
        return "invalid_operator_admission"
    metadata_matches = (
        request_metadata.get("tenant_id")
        == row["linked_admission_tenant_id"]
        and request_metadata.get("admission_id") == row["admission_id"]
        and request_metadata.get("queue_epoch") == QUEUE_EPOCH
        and request_metadata.get("branch_def_id") == row["branch_def_id"]
    )
    if not metadata_matches or not _authority_receipt_matches(row, receipt):
        return "invalid_operator_admission"
    if not _canonical_request_body_matches(
        row,
        inputs=decoded_documents["inputs"],
    ):
        return "invalid_operator_admission"
    if not _public_admission_result_matches(row, result):
        return "invalid_operator_admission"
    return None


def _is_path_safe_universe_id(value: Any) -> bool:
    """Match create-time existing/dev ID path safety without narrowing IDs."""

    return bool(
        isinstance(value, str)
        and value
        and value == value.strip()
        and len(value) <= 255
        and not value.startswith(".")
        and "/" not in value
        and "\\" not in value
        and "\x00" not in value
    )


def _terminal_lifecycle_matches(row: Mapping[str, Any]) -> bool:
    task_terminal = _parse_timestamp(row.get("terminal_at"))
    admission_terminal = _parse_timestamp(
        row.get("linked_admission_terminal_at")
    )
    if row.get("status") in _TERMINAL_STATUSES:
        return bool(
            task_terminal is not None
            and admission_terminal is not None
            and task_terminal == admission_terminal
        )
    return (
        row.get("terminal_at") is None
        and row.get("linked_admission_terminal_at") is None
    )


def _compacted_terminal_matches(
    row: Mapping[str, Any],
    *,
    inputs: Mapping[str, Any],
    detail: Mapping[str, Any],
) -> bool:
    if (
        row.get("status") not in _TERMINAL_STATUSES
        or inputs
        or detail
        or row.get("directed_daemon_id") != ""
        or row.get("claimed_by") != ""
        or row.get("linked_request_text") != ""
        or row.get("linked_request_preferred_author_id") is not None
    ):
        return False
    task_terminal = _parse_timestamp(row.get("terminal_at"))
    admission_terminal = _parse_timestamp(
        row.get("linked_admission_terminal_at")
    )
    compacted_at = _parse_timestamp(
        row.get("linked_admission_compacted_at")
    )
    if (
        task_terminal is None
        or admission_terminal is None
        or compacted_at is None
        or task_terminal != admission_terminal
        or compacted_at < task_terminal
    ):
        return False
    try:
        receipt = json.loads(str(row.get("linked_admission_receipt_json")))
        result = json.loads(str(row.get("linked_admission_result_json")))
        metadata = json.loads(str(row.get("linked_request_metadata_json")))
    except (TypeError, ValueError, json.JSONDecodeError):
        return False
    expected_result = {
        "admission_id": row["admission_id"],
        "branch_task_id": row["branch_task_id"],
        "request_id": row["request_id"],
        "request_status": row["status"],
        "universe_id": row["universe_id"],
    }
    return bool(
        receipt == {}
        and result == expected_result
        and metadata == {"compacted": True, "queue_epoch": QUEUE_EPOCH}
    )


def _canonical_request_body_matches(
    row: Mapping[str, Any],
    *,
    inputs: Mapping[str, Any],
) -> bool:
    request_text = row.get("linked_request_text")
    request_type = row.get("linked_request_type")
    request_branch_id = row.get("linked_request_branch_id")
    directed_daemon_id = row.get("directed_daemon_id")
    pickup_incentive = inputs.get("pickup_incentive")
    directed_instruction = inputs.get("directed_daemon_instruction")
    if (
        not isinstance(request_text, str)
        or not isinstance(request_type, str)
        or request_branch_id is not None
        and not isinstance(request_branch_id, str)
        or not isinstance(directed_daemon_id, str)
        or not isinstance(pickup_incentive, str)
        or not isinstance(directed_instruction, str)
        or set(inputs) != _TASK_INPUT_KEYS
        or inputs.get("request_id") != row.get("request_id")
        or inputs.get("request_type") != request_type
        or inputs.get("branch_id") != (request_branch_id or "")
    ):
        return False
    import rfc8785

    canonical = rfc8785.dumps({
        "branch_id": request_branch_id or "",
        "directed_daemon_id": directed_daemon_id,
        "directed_daemon_instruction": directed_instruction,
        "pickup_incentive": pickup_incentive,
        "priority_weight": row["priority_weight"],
        "request_type": request_type,
        "schema_version": _REQUEST_BODY_SCHEMA_VERSION,
        "text": request_text,
        "universe_id": row["universe_id"],
    })
    expected = f"sha256:{hashlib.sha256(canonical).hexdigest()}"
    return row.get("linked_admission_body_digest") == expected


def _authority_receipt_matches(
    row: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> bool:
    directed = receipt.get("directed_assignment")
    directed_daemon_id = row.get("directed_daemon_id") or ""
    if not isinstance(directed, dict):
        return False
    if directed_daemon_id:
        try:
            daemon_metadata = json.loads(
                str(row.get("linked_directed_daemon_metadata_json"))
            )
        except (TypeError, ValueError, json.JSONDecodeError):
            return False
        if not isinstance(daemon_metadata, dict):
            return False
        actor_id = row.get("linked_admission_actor_id")
        owner_id = (
            daemon_metadata.get("owner_user_id")
            or daemon_metadata.get("created_by")
            or "host"
        )
        delegated = daemon_metadata.get("delegated_hosts")
        delegated_hosts = delegated if isinstance(delegated, list) else []
        if actor_id == owner_id:
            expected_scope = "owner"
        elif actor_id in delegated_hosts:
            expected_scope = "delegated_host"
        else:
            expected_scope = "none"
        directed_matches = (
            directed.get("daemon_id") == directed_daemon_id
            and isinstance(directed.get("daemon_soul_hash"), str)
            and _SOUL_HASH_RE.fullmatch(directed["daemon_soul_hash"])
            is not None
            and directed["daemon_soul_hash"]
            == row.get("linked_directed_daemon_soul_hash")
            and directed.get("authority_scope") == expected_scope
            and expected_scope in _DIRECTED_AUTHORITY_SCOPES
        )
    else:
        directed_matches = directed == {}
    return bool(
        receipt.get("authority") == "request-local"
        and receipt.get("branch_def_id") == row.get("branch_def_id")
        and type(receipt.get("grant_generation")) is int
        and receipt.get("grant_generation")
        == row["linked_admission_grant_generation"]
        and receipt.get("priority_policy_version")
        == _PRIORITY_POLICY_VERSION
        and directed_matches
    )


def _public_admission_result_matches(
    row: Mapping[str, Any],
    result: Mapping[str, Any],
) -> bool:
    result_weight = result.get("accepted_priority_weight")
    result_cap = result.get("priority_weight_cap")
    if (
        isinstance(result_weight, bool)
        or not isinstance(result_weight, (int, float))
        or not math.isfinite(float(result_weight))
        or float(result_weight) != float(row["priority_weight"])
        or isinstance(result_cap, bool)
        or not isinstance(result_cap, (int, float))
        or not math.isfinite(float(result_cap))
        or float(result_cap) != 100.0
    ):
        return False
    exact = {
        "universe_id": row["universe_id"],
        "admission_id": row["admission_id"],
        "admission_state": "committed",
        "request_id": row["request_id"],
        "branch_task_id": row["branch_task_id"],
        "request_status": "pending",
        "trigger_source": row["trigger_source"],
        "priority_policy_version": row["linked_admission_policy_version"],
        "idempotent_replay": False,
        "directed_daemon_id": row.get("directed_daemon_id") or "",
    }
    return all(result.get(field) == value for field, value in exact.items())


def _as_epoch2_task(row: Mapping[str, Any]) -> Epoch2BranchTask:
    inputs = row.get("inputs")
    if not isinstance(inputs, dict):
        try:
            inputs = json.loads(str(row.get("inputs_json") or "{}"))
        except (TypeError, ValueError, json.JSONDecodeError):
            inputs = {}
    inputs = dict(inputs)
    return Epoch2BranchTask(
        branch_task_id=str(row["branch_task_id"]),
        branch_def_id=str(row["branch_def_id"]),
        universe_id=str(row["universe_id"]),
        inputs=inputs,
        trigger_source=str(row["trigger_source"]),
        priority_weight=float(row["priority_weight"]),
        queued_at=str(row["queued_at"]),
        claimed_by=str(row.get("claimed_by") or ""),
        status=str(row["status"]),
        directed_daemon_id=str(row.get("directed_daemon_id") or ""),
        request_type=str(inputs.get("request_type") or "branch_run"),
        lease_expires_at=str(row.get("lease_expires_at") or ""),
        heartbeat_at=str(row.get("heartbeat_at") or ""),
        terminal_at=str(row.get("terminal_at") or ""),
        admission_id=str(row["admission_id"]),
        request_id=str(row["request_id"]),
        actor_id=str(row.get("linked_admission_actor_id") or ""),
        queue_epoch=int(row["queue_epoch"]),
        protocol_version=int(row["protocol_version"]),
        automation_id=str(row.get("automation_id") or ""),
        automation_activation_epoch=int(
            row.get("automation_activation_epoch") or 0
        ),
        automation_executor_class=str(
            row.get("automation_executor_class") or ""
        ),
        automation_subject_kind=str(
            row.get("automation_subject_kind") or ""
        ),
        automation_subject_ref=str(row.get("automation_subject_ref") or ""),
        automation_subject_digest=str(
            row.get("automation_subject_digest") or ""
        ),
        automation_branch_version=str(
            row.get("automation_branch_version") or ""
        ),
        automation_lease_id=str(row.get("automation_lease_id") or ""),
        claimed_at=str(row.get("claimed_at") or ""),
    )


__all__ = [
    "Epoch2BranchTask",
    "AssignedConsumerLease",
    "Epoch2BranchTaskAdapter",
    "Epoch2ClaimedRequest",
    "Epoch2OperationalRead",
    "EPOCH2_TASK_LEASE_SECONDS",
    "QuarantineMaintenanceResult",
    "QuarantineReceipt",
    "WorkerClaimDescriptor",
    "WorkerClaimContext",
    "read_worker_claim_context",
    "read_worker_claim_descriptor",
]
