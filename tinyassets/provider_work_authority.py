"""Dark, non-bearer authority for requester-owned provider work.

Bindings record server-owned intent. Receipts, execution claims, and invocation
reservations remain inert ledger records: none can launch a provider, resolve a
credential, authorize quota, or enable provider-authority V2 by itself.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import threading
import weakref
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import Enum
from typing import Any, Callable, ContextManager, Protocol, runtime_checkable

from tinyassets.execution_subject import ExecutionSubject, ExecutionSubjectKind

_SHA256_RE = re.compile(r"^sha256:[0-9a-f]{64}$")
_REFERENCE_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:@/-]{0,254}$")
_PLACEHOLDER_DIGEST = f"sha256:{'0' * 64}"


def _required(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value


def _reference(value: object, name: str) -> str:
    value = _required(value, name)
    if _REFERENCE_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical reference")
    return value


def _digest(value: object, name: str) -> str:
    value = _required(value, name)
    if _SHA256_RE.fullmatch(value) is None:
        raise ValueError(f"{name} must be a canonical sha256 digest")
    return value


def _integer(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")
    return value


def _timestamp(value: object, name: str) -> str:
    value = _required(value, name)
    if not value.endswith("Z") or "T" not in value:
        raise ValueError(f"{name} must be a canonical UTC timestamp")
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except ValueError as exc:
        raise ValueError(f"{name} must be a canonical UTC timestamp") from exc
    if parsed.utcoffset() is None:
        raise ValueError(f"{name} must be a canonical UTC timestamp")
    return value


def _parsed_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value.removesuffix("Z") + "+00:00")


def _closed_tuple(value: object, name: str) -> tuple[str, ...]:
    if not isinstance(value, (tuple, list)):
        raise ValueError(f"{name} must be a sequence")
    items = tuple(_reference(item, name) for item in value)
    if not items:
        raise ValueError(f"{name} must not be empty")
    if len(set(items)) != len(items):
        raise ValueError(f"{name} must not contain duplicates")
    return items


def _canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _content_digest(value: object) -> str:
    return f"sha256:{hashlib.sha256(_canonical_json(value).encode('utf-8')).hexdigest()}"


class ProviderWorkBindingState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"


class ProviderWorkAuthorityWriteOutcome(str, Enum):
    APPLIED = "applied"
    REPLAYED = "replayed"
    MISSING = "missing"
    GENERATION_MISMATCH = "generation_mismatch"
    CONFLICT = "conflict"
    STALE = "stale"
    EXHAUSTED = "exhausted"


@dataclass(frozen=True, slots=True)
class ProviderWorkBindingRoot:
    """Non-authorizing lookup key for a server-owned provider assignment."""

    owner_user_id: str
    universe_id: str
    provider: str

    def __post_init__(self) -> None:
        for name in ("owner_user_id", "universe_id", "provider"):
            _reference(getattr(self, name), name)


@dataclass(frozen=True, slots=True)
class ProviderWorkBindingSeed:
    """Fresh, secret-free assignment facts from a trusted server resolver."""

    owner_user_id: str
    universe_id: str
    provider: str
    credential_reference_digest: str
    allowed_operations: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    assignment_generation: int
    assignment_digest: str
    max_invocations: int
    max_tokens: int
    max_cost_microunits: int
    expires_at: str

    def __post_init__(self) -> None:
        for name in ("owner_user_id", "universe_id", "provider"):
            _reference(getattr(self, name), name)
        _digest(self.credential_reference_digest, "credential_reference_digest")
        _digest(self.assignment_digest, "assignment_digest")
        object.__setattr__(
            self,
            "allowed_operations",
            _closed_tuple(self.allowed_operations, "allowed_operations"),
        )
        object.__setattr__(
            self,
            "allowed_roles",
            _closed_tuple(self.allowed_roles, "allowed_roles"),
        )
        _integer(self.assignment_generation, "assignment_generation", minimum=1)
        _integer(self.max_invocations, "max_invocations", minimum=1)
        _integer(self.max_tokens, "max_tokens", minimum=0)
        _integer(self.max_cost_microunits, "max_cost_microunits", minimum=0)
        _timestamp(self.expires_at, "expires_at")


@runtime_checkable
class ProviderWorkBindingResolver(Protocol):
    """Resolve a binding seed from the canonical assignment/custody owner."""

    def resolve(
        self,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingSeed | None: ...


@runtime_checkable
class ProviderWorkTransactionalBindingResolver(Protocol):
    """Resolve the canonical current assignment inside the caller's fence."""

    def resolve_current_in_transaction(
        self,
        connection: object,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingSeed | None: ...


@dataclass(frozen=True, slots=True)
class ProviderWorkBinding:
    schema_version: int
    binding_id: str
    generation: int
    binding_digest: str
    state: ProviderWorkBindingState
    owner_user_id: str
    universe_id: str
    provider: str
    credential_reference_digest: str
    allowed_operations: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    assignment_generation: int
    assignment_digest: str
    revocation_generation: int
    max_invocations: int
    max_tokens: int
    max_cost_microunits: int
    expires_at: str
    created_at: str
    updated_at: str

    _FIELDS = frozenset(
        {
            "schema_version",
            "binding_id",
            "generation",
            "binding_digest",
            "state",
            "owner_user_id",
            "universe_id",
            "provider",
            "credential_reference_digest",
            "allowed_operations",
            "allowed_roles",
            "assignment_generation",
            "assignment_digest",
            "revocation_generation",
            "max_invocations",
            "max_tokens",
            "max_cost_microunits",
            "expires_at",
            "created_at",
            "updated_at",
        }
    )

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        for name in ("binding_id", "owner_user_id", "universe_id", "provider"):
            _reference(getattr(self, name), name)
        _integer(self.generation, "generation", minimum=1)
        _digest(self.binding_digest, "binding_digest")
        if not isinstance(self.state, ProviderWorkBindingState):
            raise ValueError("state must be typed")
        _digest(self.credential_reference_digest, "credential_reference_digest")
        object.__setattr__(
            self,
            "allowed_operations",
            _closed_tuple(self.allowed_operations, "allowed_operations"),
        )
        object.__setattr__(
            self,
            "allowed_roles",
            _closed_tuple(self.allowed_roles, "allowed_roles"),
        )
        _integer(self.assignment_generation, "assignment_generation", minimum=1)
        _digest(self.assignment_digest, "assignment_digest")
        _integer(self.revocation_generation, "revocation_generation", minimum=0)
        _integer(self.max_invocations, "max_invocations", minimum=1)
        _integer(self.max_tokens, "max_tokens", minimum=0)
        _integer(self.max_cost_microunits, "max_cost_microunits", minimum=0)
        _timestamp(self.expires_at, "expires_at")
        _timestamp(self.created_at, "created_at")
        _timestamp(self.updated_at, "updated_at")
        if self.state is ProviderWorkBindingState.ACTIVE:
            if self.revocation_generation != 0:
                raise ValueError("active binding cannot carry revocation")
        elif self.revocation_generation < 1:
            raise ValueError("revoked binding requires revocation generation")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "binding_id": self.binding_id,
            "generation": self.generation,
            "binding_digest": self.binding_digest,
            "state": self.state.value,
            "owner_user_id": self.owner_user_id,
            "universe_id": self.universe_id,
            "provider": self.provider,
            "credential_reference_digest": self.credential_reference_digest,
            "allowed_operations": list(self.allowed_operations),
            "allowed_roles": list(self.allowed_roles),
            "assignment_generation": self.assignment_generation,
            "assignment_digest": self.assignment_digest,
            "revocation_generation": self.revocation_generation,
            "max_invocations": self.max_invocations,
            "max_tokens": self.max_tokens,
            "max_cost_microunits": self.max_cost_microunits,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderWorkBinding:
        if not isinstance(data, dict) or set(data) != cls._FIELDS:
            raise ValueError("ProviderWorkBinding fields do not match schema")
        values = dict(data)
        values["state"] = ProviderWorkBindingState(values["state"])
        return cls(**values)

    def expected_digest(self) -> str:
        payload = self.to_dict()
        del payload["binding_digest"]
        return _content_digest(payload)


@dataclass(frozen=True, slots=True)
class ProviderWorkBindingFence:
    expected_record: ProviderWorkBinding

    def __post_init__(self) -> None:
        if not isinstance(self.expected_record, ProviderWorkBinding):
            raise ValueError("expected_record must be a ProviderWorkBinding")


@dataclass(frozen=True, slots=True)
class ProviderWorkBindingWriteResult:
    outcome: ProviderWorkAuthorityWriteOutcome
    record: ProviderWorkBinding | None


class ProviderWorkReceiptState(str, Enum):
    ACTIVE = "active"
    REVOKED = "revoked"
    EXPIRED = "expired"
    FENCED = "fenced"


class ProviderWorkExecutionClaimState(str, Enum):
    ACTIVE = "active"
    RELEASED = "released"
    INVALIDATED = "invalidated"


class ProviderInvocationReservationState(str, Enum):
    RESERVED = "reserved"
    LAUNCH_STARTED = "launch_started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED_BEFORE_LAUNCH = "cancelled_before_launch"
    INDETERMINATE = "indeterminate"


class ProviderInvocationSettlementOwner(str, Enum):
    """Component responsible for durably settling a consumed carrier."""

    ROUTER = "router"
    CONSUMER = "consumer"


_WORK_ITEM_KINDS = frozenset({"background_attempt", "branch_task", "run"})


@dataclass(frozen=True, slots=True)
class ProviderUniverseWorkRoot:
    work_item_kind: str
    work_item_id: str

    def __post_init__(self) -> None:
        if self.work_item_kind not in _WORK_ITEM_KINDS:
            raise ValueError("work_item_kind is not server-classified")
        _reference(self.work_item_id, "work_item_id")


def _validate_work_lineage(
    *,
    work_item_kind: str,
    execution_subject: ExecutionSubject,
    branch_def_id: str | None,
    branch_version_id: str | None,
    agent_invocation_command_id: str | None,
    agent_invocation_command_digest: str | None,
    agent_invocation_generation: int | None,
) -> None:
    # The agent runtime (and its ``agent_invocation`` receipts) is retired; the
    # three ``agent_invocation_*`` fields stay in the persisted receipt payload,
    # always None, so existing receipt digests still verify.
    if execution_subject.kind is not ExecutionSubjectKind.BRANCH_VERSION:
        raise ValueError("Branch provider work requires a branch version subject")
    _reference(branch_def_id, "branch_def_id")
    _reference(branch_version_id, "branch_version_id")
    if any(
        value is not None
        for value in (
            agent_invocation_command_id,
            agent_invocation_command_digest,
            agent_invocation_generation,
        )
    ):
        raise ValueError("Branch provider work cannot carry agent invocation lineage")


@dataclass(frozen=True, slots=True)
class ProviderUniverseWorkAuthority:
    """Transient facts returned only by a trusted server-owned resolver."""

    root: ProviderUniverseWorkRoot
    binding: ProviderWorkBinding
    principal_id: str
    actor_id: str
    operation: str
    role: str
    executor_class: str
    max_invocations: int
    max_tokens: int
    max_cost_microunits: int
    expires_at: str
    execution_subject: ExecutionSubject
    allowed_roles: tuple[str, ...] | None = None
    branch_def_id: str | None = None
    branch_version_id: str | None = None
    agent_invocation_command_id: str | None = None
    agent_invocation_command_digest: str | None = None
    agent_invocation_generation: int | None = None
    parent_binding_id: str | None = None
    parent_binding_generation: int | None = None
    parent_binding_digest: str | None = None
    parent_binding_revocation_generation: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.root, ProviderUniverseWorkRoot):
            raise ValueError("root must be a ProviderUniverseWorkRoot")
        if not isinstance(self.binding, ProviderWorkBinding):
            raise ValueError("binding must be a ProviderWorkBinding")
        for name in ("principal_id", "actor_id", "operation", "role"):
            _reference(getattr(self, name), name)
        allowed_roles = (
            (self.role,)
            if self.allowed_roles is None
            else _closed_tuple(self.allowed_roles, "allowed_roles")
        )
        if self.role not in allowed_roles:
            raise ValueError("role must be included in allowed_roles")
        object.__setattr__(self, "allowed_roles", allowed_roles)
        if not isinstance(self.execution_subject, ExecutionSubject):
            raise ValueError("execution_subject must be typed")
        _validate_work_lineage(
            work_item_kind=self.root.work_item_kind,
            execution_subject=self.execution_subject,
            branch_def_id=self.branch_def_id,
            branch_version_id=self.branch_version_id,
            agent_invocation_command_id=self.agent_invocation_command_id,
            agent_invocation_command_digest=self.agent_invocation_command_digest,
            agent_invocation_generation=self.agent_invocation_generation,
        )
        if self.executor_class != "cloud":
            raise ValueError("executor_class must be cloud")
        _integer(self.max_invocations, "max_invocations", minimum=1)
        _integer(self.max_tokens, "max_tokens", minimum=0)
        _integer(
            self.max_cost_microunits,
            "max_cost_microunits",
            minimum=0,
        )
        _timestamp(self.expires_at, "expires_at")
        parent = (
            self.parent_binding_id,
            self.parent_binding_generation,
            self.parent_binding_digest,
            self.parent_binding_revocation_generation,
        )
        if any(value is not None for value in parent):
            if any(value is None for value in parent):
                raise ValueError("parent binding identity must be complete")
            _reference(self.parent_binding_id, "parent_binding_id")
            _integer(self.parent_binding_generation, "parent_binding_generation", minimum=1)
            _digest(self.parent_binding_digest, "parent_binding_digest")
            _integer(
                self.parent_binding_revocation_generation,
                "parent_binding_revocation_generation",
                minimum=0,
            )


@runtime_checkable
class ProviderUniverseWorkResolver(Protocol):
    def resolve(
        self,
        root: ProviderUniverseWorkRoot,
    ) -> ProviderUniverseWorkAuthority | None: ...


@dataclass(frozen=True, slots=True)
class ProviderUniverseWorkReceipt:
    schema_version: int
    receipt_id: str
    receipt_digest: str
    generation: int
    state: ProviderWorkReceiptState
    work_item_kind: str
    work_item_id: str
    binding_id: str | None
    binding_generation: int | None
    binding_digest: str | None
    binding_revocation_generation: int | None
    principal_id: str
    actor_id: str
    universe_id: str
    branch_def_id: str | None
    branch_version_id: str | None
    provider: str | None
    credential_reference_digest: str | None
    assignment_generation: int
    assignment_digest: str
    executor_class: str
    allowed_operations: tuple[str, ...]
    allowed_roles: tuple[str, ...]
    max_invocations: int
    max_tokens: int
    max_cost_microunits: int
    expires_at: str
    created_at: str
    execution_subject: ExecutionSubject | None = None
    agent_invocation_command_id: str | None = None
    agent_invocation_command_digest: str | None = None
    agent_invocation_generation: int | None = None
    parent_binding_id: str | None = None
    parent_binding_generation: int | None = None
    parent_binding_digest: str | None = None
    parent_binding_revocation_generation: int | None = None
    # Version4 separates a work's aggregate allowance from the provider selected
    # by each invocation. This record remains inert, never launch authority.
    authority_scope: str = "provider"
    manifest_digest: str | None = None
    admitted_sources: tuple[str, ...] | None = None

    _FIELDS_V1 = frozenset(
        {
            "schema_version",
            "receipt_id",
            "receipt_digest",
            "generation",
            "state",
            "work_item_kind",
            "work_item_id",
            "binding_id",
            "binding_generation",
            "binding_digest",
            "binding_revocation_generation",
            "principal_id",
            "actor_id",
            "universe_id",
            "branch_def_id",
            "branch_version_id",
            "provider",
            "credential_reference_digest",
            "assignment_generation",
            "assignment_digest",
            "executor_class",
            "allowed_operations",
            "allowed_roles",
            "max_invocations",
            "max_tokens",
            "max_cost_microunits",
            "expires_at",
            "created_at",
        }
    )
    _FIELDS_V2 = _FIELDS_V1 | frozenset(
        {
            "execution_subject",
            "agent_invocation_command_id",
            "agent_invocation_command_digest",
            "agent_invocation_generation",
        }
    )
    _FIELDS_V3 = _FIELDS_V2 | frozenset(
        {
            "parent_binding_id",
            "parent_binding_generation",
            "parent_binding_digest",
            "parent_binding_revocation_generation",
        }
    )
    _FIELDS_V4 = _FIELDS_V3 | frozenset({"authority_scope", "manifest_digest"})
    _FIELDS_V5 = _FIELDS_V4 | frozenset({"admitted_sources"})

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3, 4, 5}:
            raise ValueError("unsupported schema_version")
        if (
            type(self.authority_scope) is not str
            or self.authority_scope not in {"provider", "manifest"}
        ):
            raise ValueError("unsupported work authority scope")
        manifest_scope = self.authority_scope == "manifest"
        if self.schema_version == 5:
            if (not manifest_scope or type(self.admitted_sources) is not tuple
                    or not self.admitted_sources
                    or self.admitted_sources != tuple(sorted(set(self.admitted_sources)))
                    or any(not isinstance(source, str) or not source.strip()
                           for source in self.admitted_sources)):
                raise ValueError("connected admission requires sorted owned sources")
            if self.manifest_digest != _content_digest(self.admitted_sources):
                raise ValueError("connected admission digest does not match its sources")
        elif self.admitted_sources is not None:
            raise ValueError("legacy receipt cannot carry connected admission")
        if self.schema_version < 4 and (manifest_scope or self.manifest_digest is not None):
            raise ValueError("legacy receipts cannot carry manifest authority")
        if manifest_scope:
            _digest(self.manifest_digest, "manifest_digest")
            member_facts = (
                self.provider, self.binding_id, self.binding_generation,
                self.binding_digest, self.binding_revocation_generation,
                self.credential_reference_digest, self.parent_binding_id,
                self.parent_binding_generation, self.parent_binding_digest,
                self.parent_binding_revocation_generation,
            )
            if any(value is not None for value in member_facts):
                raise ValueError("manifest work receipts cannot carry member authority")
        elif self.manifest_digest is not None:
            raise ValueError("provider work receipts cannot carry a manifest digest")
        if not isinstance(self.state, ProviderWorkReceiptState):
            raise ValueError("state must be typed")
        if self.work_item_kind not in _WORK_ITEM_KINDS:
            raise ValueError("work_item_kind is not server-classified")
        for name in (
            "receipt_id",
            "work_item_id",
            "principal_id",
            "actor_id",
            "universe_id",
        ):
            _reference(getattr(self, name), name)
        if not manifest_scope:
            _reference(self.binding_id, "binding_id")
            _reference(self.provider, "provider")
        if self.schema_version == 1:
            if self.work_item_kind == "agent_invocation":
                raise ValueError("agent invocation receipts require schema_version 2")
            _reference(self.branch_def_id, "branch_def_id")
            _reference(self.branch_version_id, "branch_version_id")
            if any(
                value is not None
                for value in (
                    self.execution_subject,
                    self.agent_invocation_command_id,
                    self.agent_invocation_command_digest,
                    self.agent_invocation_generation,
                )
            ):
                raise ValueError("schema_version 1 cannot carry typed lineage")
        else:
            if not isinstance(self.execution_subject, ExecutionSubject):
                raise ValueError("execution_subject must be typed")
            _validate_work_lineage(
                work_item_kind=self.work_item_kind,
                execution_subject=self.execution_subject,
                branch_def_id=self.branch_def_id,
                branch_version_id=self.branch_version_id,
                agent_invocation_command_id=self.agent_invocation_command_id,
                agent_invocation_command_digest=self.agent_invocation_command_digest,
                agent_invocation_generation=self.agent_invocation_generation,
            )
        parent = (
            self.parent_binding_id,
            self.parent_binding_generation,
            self.parent_binding_digest,
            self.parent_binding_revocation_generation,
        )
        if self.schema_version < 3:
            if any(value is not None for value in parent):
                raise ValueError("legacy receipts cannot carry a parent binding")
        elif not manifest_scope:
            if any(value is None for value in parent):
                raise ValueError("run receipt parent binding identity is incomplete")
            _reference(self.parent_binding_id, "parent_binding_id")
            _integer(self.parent_binding_generation, "parent_binding_generation", minimum=1)
            _digest(self.parent_binding_digest, "parent_binding_digest")
            _integer(
                self.parent_binding_revocation_generation,
                "parent_binding_revocation_generation",
                minimum=0,
            )
        _digest(self.receipt_digest, "receipt_digest")
        _integer(self.generation, "generation", minimum=1)
        if not manifest_scope:
            _integer(self.binding_generation, "binding_generation", minimum=1)
            _digest(self.binding_digest, "binding_digest")
            _integer(
                self.binding_revocation_generation,
                "binding_revocation_generation",
                minimum=0,
            )
            _digest(
                self.credential_reference_digest,
                "credential_reference_digest",
            )
        _integer(self.assignment_generation, "assignment_generation", minimum=1)
        _digest(self.assignment_digest, "assignment_digest")
        if self.executor_class != "cloud":
            raise ValueError("executor_class must be cloud")
        object.__setattr__(
            self,
            "allowed_operations",
            _closed_tuple(self.allowed_operations, "allowed_operations"),
        )
        object.__setattr__(
            self,
            "allowed_roles",
            _closed_tuple(self.allowed_roles, "allowed_roles"),
        )
        _integer(self.max_invocations, "max_invocations", minimum=1)
        _integer(self.max_tokens, "max_tokens", minimum=0)
        _integer(
            self.max_cost_microunits,
            "max_cost_microunits",
            minimum=0,
        )
        _timestamp(self.expires_at, "expires_at")
        _timestamp(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "schema_version": self.schema_version,
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
            "generation": self.generation,
            "state": self.state.value,
            "work_item_kind": self.work_item_kind,
            "work_item_id": self.work_item_id,
            "binding_id": self.binding_id,
            "binding_generation": self.binding_generation,
            "binding_digest": self.binding_digest,
            "binding_revocation_generation": self.binding_revocation_generation,
            "principal_id": self.principal_id,
            "actor_id": self.actor_id,
            "universe_id": self.universe_id,
            "branch_def_id": self.branch_def_id,
            "branch_version_id": self.branch_version_id,
            "provider": self.provider,
            "credential_reference_digest": self.credential_reference_digest,
            "assignment_generation": self.assignment_generation,
            "assignment_digest": self.assignment_digest,
            "executor_class": self.executor_class,
            "allowed_operations": list(self.allowed_operations),
            "allowed_roles": list(self.allowed_roles),
            "max_invocations": self.max_invocations,
            "max_tokens": self.max_tokens,
            "max_cost_microunits": self.max_cost_microunits,
            "expires_at": self.expires_at,
            "created_at": self.created_at,
        }
        if self.schema_version >= 2:
            assert self.execution_subject is not None
            payload.update(
                {
                    "execution_subject": self.execution_subject.to_dict(),
                    "agent_invocation_command_id": self.agent_invocation_command_id,
                    "agent_invocation_command_digest": self.agent_invocation_command_digest,
                    "agent_invocation_generation": self.agent_invocation_generation,
                }
            )
        if self.schema_version >= 3:
            payload.update(
                {
                    "parent_binding_id": self.parent_binding_id,
                    "parent_binding_generation": self.parent_binding_generation,
                    "parent_binding_digest": self.parent_binding_digest,
                    "parent_binding_revocation_generation": (
                        self.parent_binding_revocation_generation
                    ),
                }
            )
        if self.schema_version >= 4:
            payload.update(
                authority_scope=self.authority_scope,
                manifest_digest=self.manifest_digest,
            )
        if self.schema_version == 5:
            payload["admitted_sources"] = list(self.admitted_sources)
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderUniverseWorkReceipt:
        if not isinstance(data, dict):
            raise ValueError("ProviderUniverseWorkReceipt fields do not match schema")
        schema_version = data.get("schema_version")
        if type(schema_version) is not int:
            raise ValueError("unsupported schema_version")
        expected = {
            1: cls._FIELDS_V1,
            2: cls._FIELDS_V2,
            3: cls._FIELDS_V3,
            4: cls._FIELDS_V4,
            5: cls._FIELDS_V5,
        }.get(schema_version, cls._FIELDS_V3)
        if set(data) != expected:
            raise ValueError("ProviderUniverseWorkReceipt fields do not match schema")
        values = dict(data)
        values["state"] = ProviderWorkReceiptState(values["state"])
        values["allowed_operations"] = tuple(values["allowed_operations"])
        values["allowed_roles"] = tuple(values["allowed_roles"])
        if schema_version == 5:
            values["admitted_sources"] = tuple(values["admitted_sources"])
        if schema_version == 1:
            values.update(
                execution_subject=None,
                agent_invocation_command_id=None,
                agent_invocation_command_digest=None,
                agent_invocation_generation=None,
                parent_binding_id=None,
                parent_binding_generation=None,
                parent_binding_digest=None,
                parent_binding_revocation_generation=None,
            )
        else:
            values["execution_subject"] = ExecutionSubject.from_dict(values["execution_subject"])
            if schema_version == 2:
                values.update(
                    parent_binding_id=None,
                    parent_binding_generation=None,
                    parent_binding_digest=None,
                    parent_binding_revocation_generation=None,
                )
        return cls(**values)

    def expected_digest(self) -> str:
        payload = self.to_dict()
        del payload["receipt_digest"]
        return _content_digest(payload)


@dataclass(frozen=True, slots=True)
class ProviderWorkReceiptWriteResult:
    outcome: ProviderWorkAuthorityWriteOutcome
    record: ProviderUniverseWorkReceipt | None


@dataclass(frozen=True, slots=True)
class ProviderWorkExecutionClaimRequest:
    receipt_id: str
    receipt_digest: str
    worker_id: str
    runtime_id: str
    claim_nonce_digest: str
    lease_seconds: int

    def __post_init__(self) -> None:
        for name in ("receipt_id", "worker_id", "runtime_id"):
            _reference(getattr(self, name), name)
        _digest(self.receipt_digest, "receipt_digest")
        _digest(self.claim_nonce_digest, "claim_nonce_digest")
        seconds = _integer(self.lease_seconds, "lease_seconds", minimum=1)
        if seconds > 3600:
            raise ValueError("lease_seconds must be <= 3600")


@dataclass(frozen=True, slots=True)
class ProviderWorkExecutionClaim:
    schema_version: int
    claim_id: str
    claim_digest: str
    generation: int
    state: ProviderWorkExecutionClaimState
    receipt_id: str
    receipt_digest: str
    worker_id: str
    runtime_id: str
    claim_nonce_digest: str
    lease_expires_at: str
    created_at: str

    _FIELDS = frozenset(
        {
            "schema_version",
            "claim_id",
            "claim_digest",
            "generation",
            "state",
            "receipt_id",
            "receipt_digest",
            "worker_id",
            "runtime_id",
            "claim_nonce_digest",
            "lease_expires_at",
            "created_at",
        }
    )

    def __post_init__(self) -> None:
        if self.schema_version != 1:
            raise ValueError("unsupported schema_version")
        if not isinstance(self.state, ProviderWorkExecutionClaimState):
            raise ValueError("state must be typed")
        for name in ("claim_id", "receipt_id", "worker_id", "runtime_id"):
            _reference(getattr(self, name), name)
        _digest(self.claim_digest, "claim_digest")
        _integer(self.generation, "generation", minimum=1)
        _digest(self.receipt_digest, "receipt_digest")
        _digest(self.claim_nonce_digest, "claim_nonce_digest")
        _timestamp(self.lease_expires_at, "lease_expires_at")
        _timestamp(self.created_at, "created_at")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "claim_id": self.claim_id,
            "claim_digest": self.claim_digest,
            "generation": self.generation,
            "state": self.state.value,
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
            "worker_id": self.worker_id,
            "runtime_id": self.runtime_id,
            "claim_nonce_digest": self.claim_nonce_digest,
            "lease_expires_at": self.lease_expires_at,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderWorkExecutionClaim:
        if not isinstance(data, dict) or set(data) != cls._FIELDS:
            raise ValueError("ProviderWorkExecutionClaim fields do not match schema")
        values = dict(data)
        values["state"] = ProviderWorkExecutionClaimState(values["state"])
        return cls(**values)

    def expected_digest(self) -> str:
        payload = self.to_dict()
        del payload["claim_digest"]
        return _content_digest(payload)


@dataclass(frozen=True, slots=True)
class ProviderWorkExecutionClaimWriteResult:
    outcome: ProviderWorkAuthorityWriteOutcome
    record: ProviderWorkExecutionClaim | None


@dataclass(frozen=True, slots=True)
class ProviderInvocationSelection:
    """Immutable model facts for one attempt; the receipt carries authority."""

    provider: str
    binding_id: str | None
    binding_generation: int | None
    binding_digest: str | None
    binding_revocation_generation: int | None
    credential_reference_id: str | None
    credential_reference_generation: int | None
    credential_reference_digest: str | None
    assignment_generation: int | None
    assignment_digest: str | None
    manifest_digest: str | None
    member_digest: str | None
    model_id: str
    executor_id: str
    model_evidence_json: str | None = None

    _FIELDS = frozenset({
        "provider", "binding_id", "binding_generation", "binding_digest",
        "binding_revocation_generation", "credential_reference_id",
        "credential_reference_generation", "credential_reference_digest",
        "assignment_generation", "assignment_digest", "manifest_digest",
        "member_digest", "model_id", "executor_id", "model_evidence",
    })

    @classmethod
    def connected(cls, provider: str, model_id: str) -> ProviderInvocationSelection:
        return cls(
            provider=provider, model_id=model_id, executor_id=provider,
            binding_id=None, binding_generation=None, binding_digest=None,
            binding_revocation_generation=None, credential_reference_id=None,
            credential_reference_generation=None, credential_reference_digest=None,
            assignment_generation=None, assignment_digest=None,
            manifest_digest=None, member_digest=None,
        )

    def __post_init__(self) -> None:
        if self.binding_id is None:
            if any(getattr(self, name) is not None for name in (
                "binding_generation", "binding_digest", "binding_revocation_generation",
                "credential_reference_id", "credential_reference_generation",
                "credential_reference_digest", "assignment_generation", "assignment_digest",
                "manifest_digest", "member_digest",
            )):
                raise ValueError("connected selection cannot carry assignment authority")
            _reference(self.provider, "provider")
            _reference(self.executor_id, "executor_id")
            if (type(self.model_id) is not str or len(self.model_id) > 200
                    or self.model_id != self.model_id.strip()
                    or (self.model_id and not self.model_id.isprintable())):
                raise ValueError("invalid selected model identifier")
            if self.model_evidence_json is not None:
                self.model_evidence()
            return
        for name in ("provider", "binding_id", "credential_reference_id", "executor_id"):
            _reference(getattr(self, name), name)
        for name in ("binding_generation", "credential_reference_generation",
                     "assignment_generation"):
            _integer(getattr(self, name), name, minimum=1)
        _integer(self.binding_revocation_generation, "binding_revocation_generation", minimum=0)
        for name in ("binding_digest", "credential_reference_digest", "assignment_digest",
                     "manifest_digest", "member_digest"):
            _digest(getattr(self, name), name)
        if (type(self.model_id) is not str or len(self.model_id) > 200
                or self.model_id != self.model_id.strip()
                or (self.model_id and not self.model_id.isprintable())):
            raise ValueError("invalid selected model identifier")
        if self.model_evidence_json is not None:
            self.model_evidence()

    def model_evidence(self) -> dict[str, Any] | None:
        """Decode a fresh copy; the sealed record never exposes mutable internals."""
        raw = self.model_evidence_json
        if raw is None:
            return None
        if type(raw) is not str or len(raw.encode("utf-8")) > 262144:
            raise ValueError("invalid selected model evidence")
        evidence = json.loads(raw)
        if type(evidence) is dict and evidence.get("kind") == "native":
            from tinyassets.providers.native_model_selection import NativeSelection

            native = NativeSelection.from_dict(evidence)
            if (_canonical_json(evidence) != raw or native.provider != self.provider
                    or native.requested_model_id != self.model_id
                    or self.executor_id != self.provider):
                raise ValueError("native model evidence does not match selection")
            return evidence
        if type(evidence) is not dict or set(evidence) != {
            "discovery_protocol", "source_digest", "context_tokens", "supports_tools",
            "cost_caps", "execution_contract", "observed_at", "completed_at",
        } or _canonical_json(evidence) != raw:
            raise ValueError("selected model evidence fields do not match schema")
        _reference(evidence["discovery_protocol"], "discovery_protocol")
        if (type(evidence["source_digest"]) is not str
                or re.fullmatch(r"[0-9a-f]{64}", evidence["source_digest"]) is None):
            raise ValueError("invalid discovery source digest")
        _integer(evidence["context_tokens"], "context_tokens", minimum=1)
        if type(evidence["supports_tools"]) is not bool:
            raise ValueError("selected model tool capability must be boolean")
        caps = evidence["cost_caps"]
        if type(caps) is not dict or not caps:
            raise ValueError("selected model cost caps are required")
        for name, value in caps.items():
            _reference(name, "cost component")
            _integer(value, "cost cap", minimum=0)
        for name in ("observed_at", "completed_at"):
            _timestamp(evidence[name], name)
        if _parsed_timestamp(evidence["observed_at"]) > _parsed_timestamp(evidence["completed_at"]):
            raise ValueError("selected model discovery timestamps are reversed")
        contract = evidence["execution_contract"]
        if type(contract) is not dict or set(contract) != {"kind", "value"}:
            raise ValueError("invalid selected execution contract")
        if contract["kind"] == "installed":
            if contract["value"] != evidence["discovery_protocol"]:
                raise ValueError("selected installed contract identity differs")
            from tinyassets.providers.discovery_protocols import discovery_protocol

            compiled = discovery_protocol(contract["value"])
        elif contract["kind"] == "configured":
            from tinyassets.providers.discovery_contract import SourceContract

            compiled = SourceContract.compile(contract["value"])
        elif contract["kind"] == "declared":
            from tinyassets.providers.declared_models import contract_from_evidence

            compiled = contract_from_evidence(contract["value"])
        else:
            raise ValueError("unsupported selected execution contract")
        if compiled.inference_protocol != self.executor_id:
            raise ValueError("selected execution contract does not match executor")
        if set(caps) != compiled.price_components:
            raise ValueError("selected model cost components do not match executor")
        return evidence

    def to_dict(self) -> dict[str, Any]:
        values = {name: getattr(self, name) for name in self._FIELDS - {"model_evidence"}}
        values["model_evidence"] = self.model_evidence()
        return values

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderInvocationSelection:
        if type(data) is not dict or set(data) != cls._FIELDS:
            raise ValueError("ProviderInvocationSelection fields do not match schema")
        values = dict(data)
        evidence = values.pop("model_evidence")
        values["model_evidence_json"] = None if evidence is None else _canonical_json(evidence)
        return cls(**values)


@dataclass(frozen=True, slots=True)
class ProviderInvocationReservationRequest:
    receipt_id: str
    receipt_digest: str
    claim_id: str
    claim_digest: str
    claim_generation: int
    invocation_key: str
    operation: str
    role: str
    max_tokens: int
    max_cost_microunits: int
    selection: ProviderInvocationSelection | None = None

    def __post_init__(self) -> None:
        if self.selection is not None and type(self.selection) is not ProviderInvocationSelection:
            raise ValueError("selection must be typed")
        for name in (
            "receipt_id",
            "claim_id",
            "invocation_key",
            "operation",
            "role",
        ):
            _reference(getattr(self, name), name)
        _digest(self.receipt_digest, "receipt_digest")
        _digest(self.claim_digest, "claim_digest")
        _integer(self.claim_generation, "claim_generation", minimum=1)
        _integer(self.max_tokens, "max_tokens", minimum=0)
        _integer(
            self.max_cost_microunits,
            "max_cost_microunits",
            minimum=0,
        )


@dataclass(frozen=True, slots=True)
class ProviderInvocationReservation:
    schema_version: int
    reservation_id: str
    reservation_digest: str
    state: ProviderInvocationReservationState
    receipt_id: str
    receipt_digest: str
    claim_id: str
    claim_digest: str
    claim_generation: int
    invocation_key: str
    ordinal: int
    operation: str
    role: str
    max_tokens: int
    max_cost_microunits: int
    created_at: str
    actual_input_tokens: int | None = None
    actual_output_tokens: int | None = None
    actual_total_tokens: int | None = None
    actual_cost_microunits: int | None = None
    settled_at: str | None = None
    selection: ProviderInvocationSelection | None = None

    _FIELDS_V1 = frozenset(
        {
            "schema_version",
            "reservation_id",
            "reservation_digest",
            "state",
            "receipt_id",
            "receipt_digest",
            "claim_id",
            "claim_digest",
            "claim_generation",
            "invocation_key",
            "ordinal",
            "operation",
            "role",
            "max_tokens",
            "max_cost_microunits",
            "created_at",
        }
    )
    _FIELDS_V2 = _FIELDS_V1 | frozenset(
        {
            "actual_input_tokens",
            "actual_output_tokens",
            "actual_total_tokens",
            "actual_cost_microunits",
            "settled_at",
        }
    )
    _FIELDS_V3 = _FIELDS_V2 | {"selection"}

    def __post_init__(self) -> None:
        if type(self.schema_version) is not int or self.schema_version not in {1, 2, 3}:
            raise ValueError("unsupported schema_version")
        if self.schema_version == 3:
            if type(self.selection) is not ProviderInvocationSelection:
                raise ValueError("version3 reservation requires exact selected-member facts")
        elif self.selection is not None:
            raise ValueError("legacy reservation cannot carry selected-member facts")
        if not isinstance(self.state, ProviderInvocationReservationState):
            raise ValueError("state must be typed")
        for name in (
            "reservation_id",
            "receipt_id",
            "claim_id",
            "invocation_key",
            "operation",
            "role",
        ):
            _reference(getattr(self, name), name)
        _digest(self.reservation_digest, "reservation_digest")
        _digest(self.receipt_digest, "receipt_digest")
        _digest(self.claim_digest, "claim_digest")
        _integer(self.claim_generation, "claim_generation", minimum=1)
        _integer(self.ordinal, "ordinal", minimum=1)
        _integer(self.max_tokens, "max_tokens", minimum=0)
        _integer(
            self.max_cost_microunits,
            "max_cost_microunits",
            minimum=0,
        )
        _timestamp(self.created_at, "created_at")
        actuals = (
            self.actual_input_tokens,
            self.actual_output_tokens,
            self.actual_total_tokens,
            self.actual_cost_microunits,
        )
        if self.schema_version == 1:
            if any(value is not None for value in actuals) or self.settled_at is not None:
                raise ValueError("schema_version 1 cannot carry settlement")
            return
        for name, value in zip(
            (
                "actual_input_tokens",
                "actual_output_tokens",
                "actual_total_tokens",
                "actual_cost_microunits",
            ),
            actuals,
            strict=True,
        ):
            if value is not None:
                _integer(value, name, minimum=0)
        terminal = self.state in {
            ProviderInvocationReservationState.SUCCEEDED,
            ProviderInvocationReservationState.FAILED,
            ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
            ProviderInvocationReservationState.INDETERMINATE,
        }
        if terminal != (self.settled_at is not None):
            raise ValueError("terminal reservations require exactly one settlement timestamp")
        if self.settled_at is not None:
            _timestamp(self.settled_at, "settled_at")
        known = self.state in {
            ProviderInvocationReservationState.SUCCEEDED,
            ProviderInvocationReservationState.FAILED,
            ProviderInvocationReservationState.CANCELLED_BEFORE_LAUNCH,
        }
        if known and any(value is None for value in actuals):
            raise ValueError("known settlement requires complete actual usage")
        if self.state is ProviderInvocationReservationState.INDETERMINATE and any(
            value is not None for value in actuals
        ):
            raise ValueError("indeterminate settlement cannot claim actual usage")
        if (
            self.actual_input_tokens is not None
            and self.actual_output_tokens is not None
            and self.actual_total_tokens
            != self.actual_input_tokens + self.actual_output_tokens
        ):
            raise ValueError("actual_total_tokens must equal input plus output")

    def to_dict(self) -> dict[str, Any]:
        payload = {
            "schema_version": self.schema_version,
            "reservation_id": self.reservation_id,
            "reservation_digest": self.reservation_digest,
            "state": self.state.value,
            "receipt_id": self.receipt_id,
            "receipt_digest": self.receipt_digest,
            "claim_id": self.claim_id,
            "claim_digest": self.claim_digest,
            "claim_generation": self.claim_generation,
            "invocation_key": self.invocation_key,
            "ordinal": self.ordinal,
            "operation": self.operation,
            "role": self.role,
            "max_tokens": self.max_tokens,
            "max_cost_microunits": self.max_cost_microunits,
            "created_at": self.created_at,
        }
        if self.schema_version >= 2:
            payload.update(
                {
                    "actual_input_tokens": self.actual_input_tokens,
                    "actual_output_tokens": self.actual_output_tokens,
                    "actual_total_tokens": self.actual_total_tokens,
                    "actual_cost_microunits": self.actual_cost_microunits,
                    "settled_at": self.settled_at,
                }
            )
        if self.schema_version == 3:
            payload["selection"] = self.selection.to_dict()
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ProviderInvocationReservation:
        if not isinstance(data, dict):
            raise ValueError("ProviderInvocationReservation fields do not match schema")
        version = data.get("schema_version")
        if type(version) is not int or version not in {1, 2, 3}:
            raise ValueError("unsupported schema_version")
        expected = {1: cls._FIELDS_V1, 2: cls._FIELDS_V2, 3: cls._FIELDS_V3}[version]
        if set(data) != expected:
            raise ValueError("ProviderInvocationReservation fields do not match schema")
        values = dict(data)
        values["state"] = ProviderInvocationReservationState(values["state"])
        if version == 3:
            values["selection"] = ProviderInvocationSelection.from_dict(values["selection"])
        if values["schema_version"] == 1:
            values.update(
                actual_input_tokens=None,
                actual_output_tokens=None,
                actual_total_tokens=None,
                actual_cost_microunits=None,
                settled_at=None,
            )
        return cls(**values)

    def expected_digest(self) -> str:
        payload = self.to_dict()
        del payload["reservation_digest"]
        return _content_digest(payload)


@dataclass(frozen=True, slots=True)
class ProviderInvocationReservationWriteResult:
    outcome: ProviderWorkAuthorityWriteOutcome
    record: ProviderInvocationReservation | None
    receipt: ProviderUniverseWorkReceipt | None = None
    claim: ProviderWorkExecutionClaim | None = None
    mint_proof: object | None = field(default=None, repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class ProviderInvocationLaunchRequest:
    reservation_id: str
    reserved_digest: str
    receipt_id: str
    receipt_digest: str
    claim_id: str
    claim_digest: str
    claim_generation: int
    invocation_key: str

    def __post_init__(self) -> None:
        for name in (
            "reservation_id",
            "receipt_id",
            "claim_id",
            "invocation_key",
        ):
            _reference(getattr(self, name), name)
        _digest(self.reserved_digest, "reserved_digest")
        _digest(self.receipt_digest, "receipt_digest")
        _digest(self.claim_digest, "claim_digest")
        _integer(self.claim_generation, "claim_generation", minimum=1)

    @classmethod
    def from_reservation(
        cls,
        reservation: ProviderInvocationReservation,
    ) -> ProviderInvocationLaunchRequest:
        if not isinstance(reservation, ProviderInvocationReservation):
            raise ValueError("reservation must be a ProviderInvocationReservation")
        if reservation.state is not ProviderInvocationReservationState.RESERVED:
            raise ValueError("reservation must be reserved")
        return cls(
            reservation_id=reservation.reservation_id,
            reserved_digest=reservation.reservation_digest,
            receipt_id=reservation.receipt_id,
            receipt_digest=reservation.receipt_digest,
            claim_id=reservation.claim_id,
            claim_digest=reservation.claim_digest,
            claim_generation=reservation.claim_generation,
            invocation_key=reservation.invocation_key,
        )


class ProviderInvocationCarrier:
    """In-process-only frozen authority for one already-armed provider call."""

    __slots__ = (
        "_carrier_id",
        "_claim",
        "_issuer_pid",
        "_receipt",
        "_reservation",
        "_seal",
        "_settler",
        "_settlement_owner",
        "__weakref__",
    )

    def __init__(self, *_args: object, **_kwargs: object) -> None:
        raise TypeError("ProviderInvocationCarrier must be store-minted")

    def __setattr__(self, _name: str, _value: object) -> None:
        raise AttributeError("ProviderInvocationCarrier is immutable")

    def __reduce__(self):
        raise TypeError("ProviderInvocationCarrier is non-serializable")

    @property
    def provider(self) -> str:
        if self._reservation.selection is not None:
            return self._reservation.selection.provider
        return self._receipt.provider

    # These are inert provenance from the sealed records, not fresh authority.
    # Reading them neither consumes the carrier nor permits another launch.
    @property
    def reservation_id(self) -> str:
        return self._reservation.reservation_id

    @property
    def work_receipt_id(self) -> str:
        return self._receipt.receipt_id

    @property
    def binding_id(self) -> str:
        if self._reservation.selection is not None:
            return self._reservation.selection.binding_id
        return self._receipt.binding_id

    @property
    def binding_generation(self) -> int:
        if self._reservation.selection is not None:
            return self._reservation.selection.binding_generation
        return self._receipt.binding_generation

    @property
    def binding_digest(self) -> str:
        if self._reservation.selection is not None:
            return self._reservation.selection.binding_digest
        return self._receipt.binding_digest

    @property
    def selected_model(self):
        from tinyassets.providers.work_model_selection import selected_work_model

        return selected_work_model(self._reservation.selection)

    @property
    def native_selection(self):
        from tinyassets.providers.native_model_selection import NativeSelection

        selection = self._reservation.selection
        evidence = None if selection is None else selection.model_evidence()
        if evidence is not None and evidence.get("kind") == "native":
            return NativeSelection.from_dict(evidence)
        return None

    @property
    def role(self) -> str:
        return self._reservation.role

    @property
    def operation(self) -> str:
        return self._reservation.operation

    @property
    def max_tokens(self) -> int:
        return self._reservation.max_tokens

    @property
    def max_cost_microunits(self) -> int:
        return self._reservation.max_cost_microunits

    @property
    def assignment_generation(self) -> int:
        return self._receipt.assignment_generation

    @property
    def assignment_digest(self) -> str:
        return self._receipt.assignment_digest

    @property
    def credential_reference_digest(self) -> str:
        if self._reservation.selection is not None:
            return self._reservation.selection.credential_reference_digest
        return self._receipt.credential_reference_digest

    @property
    def binding_revocation_generation(self) -> int:
        if self._reservation.selection is not None:
            return self._reservation.selection.binding_revocation_generation
        return self._receipt.binding_revocation_generation

    @property
    def settlement_owner(self) -> ProviderInvocationSettlementOwner:
        return self._settlement_owner

    def settle(
        self,
        state: ProviderInvocationReservationState,
        *,
        input_tokens: int | None = None,
        output_tokens: int | None = None,
        cost_microunits: int | None = None,
    ) -> None:
        """Durably settle this consumed carrier's exact reservation once."""

        if type(self) is not ProviderInvocationCarrier:
            raise PermissionError("provider invocation carrier is not server-owned")
        with _PROVIDER_INVOCATION_CARRIER_LOCK:
            if self._carrier_id in _ACTIVE_PROVIDER_INVOCATION_CARRIERS:
                raise PermissionError("provider invocation carrier has not launched")
        if self._settlement_owner is not ProviderInvocationSettlementOwner.ROUTER:
            raise PermissionError("provider invocation settlement is consumer-owned")
        if self._settler is None:
            raise PermissionError("provider invocation carrier has no durable settler")
        self._settler(
            self._reservation,
            state,
            input_tokens,
            output_tokens,
            cost_microunits,
        )

    def validate_for_call(self, *, role: str, operation: str) -> str:
        if type(self) is not ProviderInvocationCarrier:
            raise PermissionError("provider invocation carrier is not server-owned")
        if self._issuer_pid != os.getpid():
            raise PermissionError("provider invocation carrier belongs to another process")
        if not hmac.compare_digest(self._seal, _provider_invocation_carrier_seal(self)):
            raise PermissionError("provider invocation carrier seal is invalid")
        if _reference(role, "role") != self.role:
            raise PermissionError("provider invocation role does not match carrier")
        if _reference(operation, "operation") != self.operation:
            raise PermissionError("provider invocation operation does not match carrier")
        with _PROVIDER_INVOCATION_CARRIER_LOCK:
            if self._carrier_id not in _ACTIVE_PROVIDER_INVOCATION_CARRIERS:
                raise PermissionError("provider invocation carrier is already consumed")
            _ACTIVE_PROVIDER_INVOCATION_CARRIERS.remove(self._carrier_id)
        return self.provider


_PROVIDER_INVOCATION_CARRIER_KEY = secrets.token_bytes(32)
_PROVIDER_INVOCATION_CARRIER_LOCK = threading.Lock()
_ACTIVE_PROVIDER_INVOCATION_CARRIERS: set[str] = set()


def _reset_provider_invocation_carrier_state_after_fork() -> None:
    global _PROVIDER_INVOCATION_CARRIER_KEY
    global _PROVIDER_INVOCATION_CARRIER_LOCK
    global _ACTIVE_PROVIDER_INVOCATION_CARRIERS

    _PROVIDER_INVOCATION_CARRIER_KEY = secrets.token_bytes(32)
    _PROVIDER_INVOCATION_CARRIER_LOCK = threading.Lock()
    _ACTIVE_PROVIDER_INVOCATION_CARRIERS = set()


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_provider_invocation_carrier_state_after_fork)


def _discard_provider_invocation_carrier(
    carrier_id: str,
    issuer_pid: int,
) -> None:
    if issuer_pid != os.getpid():
        return
    with _PROVIDER_INVOCATION_CARRIER_LOCK:
        _ACTIVE_PROVIDER_INVOCATION_CARRIERS.discard(carrier_id)


def _provider_invocation_carrier_payload(
    carrier: ProviderInvocationCarrier,
) -> bytes:
    return json.dumps(
        {
            "carrier_id": carrier._carrier_id,
            "claim": carrier._claim.to_dict(),
            "issuer_pid": carrier._issuer_pid,
            "receipt": carrier._receipt.to_dict(),
            "reservation": carrier._reservation.to_dict(),
            "settlement_owner": carrier._settlement_owner.value,
        },
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def _provider_invocation_carrier_seal(
    carrier: ProviderInvocationCarrier,
) -> bytes:
    return hmac.digest(
        _PROVIDER_INVOCATION_CARRIER_KEY,
        _provider_invocation_carrier_payload(carrier),
        "sha256",
    )


def _mint_provider_invocation_carrier(
    receipt: ProviderUniverseWorkReceipt,
    claim: ProviderWorkExecutionClaim,
    reservation: ProviderInvocationReservation,
    mint_proof: object,
    *,
    settlement_owner: ProviderInvocationSettlementOwner,
    settler: Callable[
        [
            ProviderInvocationReservation,
            ProviderInvocationReservationState,
            int | None,
            int | None,
            int | None,
        ],
        None,
    ]
    | None = None,
) -> ProviderInvocationCarrier:
    """Mint after the authority store atomically wins ``launch_started``."""

    if type(receipt) is not ProviderUniverseWorkReceipt:
        raise TypeError("receipt must be an exact ProviderUniverseWorkReceipt")
    if type(claim) is not ProviderWorkExecutionClaim:
        raise TypeError("claim must be an exact ProviderWorkExecutionClaim")
    if type(reservation) is not ProviderInvocationReservation:
        raise TypeError("reservation must be an exact ProviderInvocationReservation")
    if not isinstance(settlement_owner, ProviderInvocationSettlementOwner):
        raise TypeError("provider invocation settlement owner must be typed")
    if settler is not None and not callable(settler):
        raise TypeError("provider invocation settler must be callable")
    if settlement_owner is ProviderInvocationSettlementOwner.ROUTER and settler is None:
        raise PermissionError("router-settled carrier requires a durable settler")
    if settlement_owner is ProviderInvocationSettlementOwner.CONSUMER and settler is not None:
        raise PermissionError("consumer-settled carrier cannot carry a router settler")
    from tinyassets.storage.provider_work_authority import (
        _ProviderInvocationStoreMintProof,
    )

    if type(mint_proof) is not _ProviderInvocationStoreMintProof:
        raise PermissionError("provider invocation mint proof is not store-issued")
    exact = (
        receipt.state is ProviderWorkReceiptState.ACTIVE,
        claim.state is ProviderWorkExecutionClaimState.ACTIVE,
        reservation.state is ProviderInvocationReservationState.LAUNCH_STARTED,
        (
            receipt.authority_scope == "provider" and reservation.selection is None
        ) or (
            receipt.authority_scope == "manifest" and reservation.selection is not None
            and receipt.admitted_sources is not None
            and reservation.selection.provider in receipt.admitted_sources
            and reservation.selection.binding_id is None
        ),
        receipt.receipt_digest == receipt.expected_digest(),
        claim.claim_digest == claim.expected_digest(),
        reservation.reservation_digest == reservation.expected_digest(),
        claim.receipt_id == receipt.receipt_id,
        claim.receipt_digest == receipt.receipt_digest,
        reservation.receipt_id == receipt.receipt_id,
        reservation.receipt_digest == receipt.receipt_digest,
        reservation.claim_id == claim.claim_id,
        reservation.claim_digest == claim.claim_digest,
        reservation.claim_generation == claim.generation,
        reservation.operation in receipt.allowed_operations,
        reservation.role in receipt.allowed_roles,
        reservation.max_tokens <= receipt.max_tokens,
        reservation.max_cost_microunits <= receipt.max_cost_microunits,
    )
    if not all(exact):
        raise PermissionError("provider invocation carrier is stale or inconsistent")
    mint_proof._consume(reservation.reservation_digest)
    carrier_id = secrets.token_hex(32)
    carrier = object.__new__(ProviderInvocationCarrier)
    object.__setattr__(carrier, "_carrier_id", carrier_id)
    object.__setattr__(carrier, "_issuer_pid", os.getpid())
    object.__setattr__(carrier, "_receipt", receipt)
    object.__setattr__(carrier, "_claim", claim)
    object.__setattr__(carrier, "_reservation", reservation)
    object.__setattr__(carrier, "_settler", settler)
    object.__setattr__(carrier, "_settlement_owner", settlement_owner)
    object.__setattr__(
        carrier,
        "_seal",
        _provider_invocation_carrier_seal(carrier),
    )
    weakref.finalize(
        carrier,
        _discard_provider_invocation_carrier,
        carrier_id,
        carrier._issuer_pid,
    )
    with _PROVIDER_INVOCATION_CARRIER_LOCK:
        _ACTIVE_PROVIDER_INVOCATION_CARRIERS.add(carrier_id)
    return carrier


def _reservation_with_state(
    reservation: ProviderInvocationReservation,
    state: ProviderInvocationReservationState,
) -> ProviderInvocationReservation:
    transitioned = replace(
        reservation,
        state=state,
        reservation_digest=_PLACEHOLDER_DIGEST,
    )
    return replace(transitioned, reservation_digest=transitioned.expected_digest())


def provider_work_receipt_id(
    *,
    universe_id: str,
    root: ProviderUniverseWorkRoot,
) -> str:
    identity = {
        "schema_version": 1,
        "universe_id": _reference(universe_id, "universe_id"),
        "work_item_id": root.work_item_id,
        "work_item_kind": root.work_item_kind,
    }
    return f"pwr_{_content_digest(identity).removeprefix('sha256:')[:32]}"


def provider_work_claim_id(receipt_id: str) -> str:
    identity = {
        "receipt_id": _reference(receipt_id, "receipt_id"),
        "schema_version": 1,
    }
    return f"pwc_{_content_digest(identity).removeprefix('sha256:')[:32]}"


def provider_invocation_reservation_id(
    *,
    receipt_id: str,
    invocation_key: str,
) -> str:
    identity = {
        "invocation_key": _reference(invocation_key, "invocation_key"),
        "receipt_id": _reference(receipt_id, "receipt_id"),
        "schema_version": 1,
    }
    return f"pir_{_content_digest(identity).removeprefix('sha256:')[:32]}"


def _claim_from_request(
    request: ProviderWorkExecutionClaimRequest,
    *,
    created_at: str,
    lease_expires_at: str,
) -> ProviderWorkExecutionClaim:
    provisional = ProviderWorkExecutionClaim(
        schema_version=1,
        claim_id=provider_work_claim_id(request.receipt_id),
        claim_digest=_PLACEHOLDER_DIGEST,
        generation=1,
        state=ProviderWorkExecutionClaimState.ACTIVE,
        receipt_id=request.receipt_id,
        receipt_digest=request.receipt_digest,
        worker_id=request.worker_id,
        runtime_id=request.runtime_id,
        claim_nonce_digest=request.claim_nonce_digest,
        lease_expires_at=lease_expires_at,
        created_at=created_at,
    )
    return replace(provisional, claim_digest=provisional.expected_digest())


def _reservation_from_request(
    request: ProviderInvocationReservationRequest,
    *,
    ordinal: int,
    created_at: str,
) -> ProviderInvocationReservation:
    provisional = ProviderInvocationReservation(
        schema_version=3 if request.selection is not None else 2,
        reservation_id=provider_invocation_reservation_id(
            receipt_id=request.receipt_id,
            invocation_key=request.invocation_key,
        ),
        reservation_digest=_PLACEHOLDER_DIGEST,
        state=ProviderInvocationReservationState.RESERVED,
        receipt_id=request.receipt_id,
        receipt_digest=request.receipt_digest,
        claim_id=request.claim_id,
        claim_digest=request.claim_digest,
        claim_generation=request.claim_generation,
        invocation_key=request.invocation_key,
        ordinal=ordinal,
        operation=request.operation,
        role=request.role,
        max_tokens=request.max_tokens,
        max_cost_microunits=request.max_cost_microunits,
        created_at=created_at,
        selection=request.selection,
    )
    return replace(
        provisional,
        reservation_digest=provisional.expected_digest(),
    )


@runtime_checkable
class ProviderWorkAuthorityStore(Protocol):
    def transaction(self) -> ContextManager[Any]: ...

    def get(self, binding_id: str) -> ProviderWorkBinding | None: ...

    def claim(
        self,
        request: ProviderWorkExecutionClaimRequest,
    ) -> ProviderWorkExecutionClaimWriteResult: ...

    def reserve(
        self,
        request: ProviderInvocationReservationRequest,
    ) -> ProviderInvocationReservationWriteResult: ...

    def arm_launch(
        self,
        request: ProviderInvocationLaunchRequest,
    ) -> ProviderInvocationReservationWriteResult: ...

    def arm_launch_carrier(
        self,
        request: ProviderInvocationLaunchRequest,
    ) -> ProviderInvocationCarrier: ...


def provider_work_binding_id(
    *,
    owner_user_id: str,
    universe_id: str,
    provider: str,
    binding_class: str = "default",
) -> str:
    normalized_class = _reference(binding_class, "binding_class")
    identity = {
        "owner_user_id": _reference(owner_user_id, "owner_user_id"),
        "provider": _reference(provider, "provider"),
        "schema_version": 1,
        "universe_id": _reference(universe_id, "universe_id"),
    }
    if normalized_class != "default":
        identity["binding_class"] = normalized_class
    return f"pwb_{_content_digest(identity).removeprefix('sha256:')[:32]}"


def provider_work_binding_class(
    *,
    allowed_operations: tuple[str, ...],
    allowed_roles: tuple[str, ...],
) -> str:
    """Server-derived identity slot without adding a second binding model."""

    if allowed_operations == ("converse",) and allowed_roles == ("writer",):
        return "serving"
    if allowed_operations == ("background_branch_run",):
        return "background_branch_run"
    if allowed_operations == ("run_graph",):
        return "run_graph"
    return "default"


def _from_seed(seed: ProviderWorkBindingSeed, *, created_at: str) -> ProviderWorkBinding:
    provisional = ProviderWorkBinding(
        schema_version=1,
        binding_id=provider_work_binding_id(
            owner_user_id=seed.owner_user_id,
            universe_id=seed.universe_id,
            provider=seed.provider,
            binding_class=provider_work_binding_class(
                allowed_operations=seed.allowed_operations,
                allowed_roles=seed.allowed_roles,
            ),
        ),
        generation=1,
        binding_digest=_PLACEHOLDER_DIGEST,
        state=ProviderWorkBindingState.ACTIVE,
        owner_user_id=seed.owner_user_id,
        universe_id=seed.universe_id,
        provider=seed.provider,
        credential_reference_digest=seed.credential_reference_digest,
        allowed_operations=seed.allowed_operations,
        allowed_roles=seed.allowed_roles,
        assignment_generation=seed.assignment_generation,
        assignment_digest=seed.assignment_digest,
        revocation_generation=0,
        max_invocations=seed.max_invocations,
        max_tokens=seed.max_tokens,
        max_cost_microunits=seed.max_cost_microunits,
        expires_at=seed.expires_at,
        created_at=created_at,
        updated_at=created_at,
    )
    return replace(provisional, binding_digest=provisional.expected_digest())


class ProviderWorkBindingService:
    """Issue from a trusted assignment resolver and revoke exact bindings."""

    def __init__(
        self,
        store: Any,
        resolver: ProviderWorkBindingResolver | None = None,
    ) -> None:
        required = ("transaction", "timestamp")
        if any(not callable(getattr(store, name, None)) for name in required):
            raise ValueError("store must implement provider authority persistence")
        if resolver is not None and not isinstance(
            resolver,
            ProviderWorkBindingResolver,
        ):
            raise ValueError("resolver must implement ProviderWorkBindingResolver")
        self._store = store
        self._resolver = resolver

    def issue(
        self,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingWriteResult:
        if not isinstance(root, ProviderWorkBindingRoot):
            raise ValueError("root must be a ProviderWorkBindingRoot")
        if self._resolver is None:
            raise PermissionError("server-owned provider assignment is unavailable")
        seed = self._resolver.resolve(root)
        exact_root = (
            isinstance(seed, ProviderWorkBindingSeed),
            isinstance(seed, ProviderWorkBindingSeed) and seed.owner_user_id == root.owner_user_id,
            isinstance(seed, ProviderWorkBindingSeed) and seed.universe_id == root.universe_id,
            isinstance(seed, ProviderWorkBindingSeed) and seed.provider == root.provider,
        )
        if not all(exact_root) or seed is None:
            raise PermissionError("server-owned provider assignment is unavailable")
        issue = getattr(self._store, "_issue_binding", None)
        if not callable(issue):
            raise ValueError("store must implement binding issuance persistence")
        return issue(seed)

    def issue_in_transaction(
        self,
        connection: object,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingWriteResult:
        """Issue through the same service contract inside a caller-owned fence."""

        if not isinstance(root, ProviderWorkBindingRoot):
            raise ValueError("root must be a ProviderWorkBindingRoot")
        if self._resolver is None:
            raise PermissionError("server-owned provider assignment is unavailable")
        transactional = getattr(self._resolver, "resolve_current_in_transaction", None)
        seed = (
            transactional(connection, root)
            if callable(transactional)
            else self._resolver.resolve(root)
        )
        if not isinstance(seed, ProviderWorkBindingSeed) or (
            seed.owner_user_id != root.owner_user_id
            or seed.universe_id != root.universe_id
            or seed.provider != root.provider
        ):
            raise PermissionError("server-owned provider assignment is unavailable")
        issue = getattr(self._store, "_issue_binding_in_transaction", None)
        if not callable(issue):
            raise ValueError("store must implement transactional binding issuance")
        return issue(connection, seed)

    def revoke(self, expected: ProviderWorkBindingFence) -> ProviderWorkBindingWriteResult:
        if not isinstance(expected, ProviderWorkBindingFence):
            raise ValueError("expected must be a ProviderWorkBindingFence")
        current = expected.expected_record
        if current.state is not ProviderWorkBindingState.ACTIVE:
            return ProviderWorkBindingWriteResult(
                ProviderWorkAuthorityWriteOutcome.CONFLICT,
                current,
            )
        provisional = replace(
            current,
            generation=current.generation + 1,
            binding_digest=_PLACEHOLDER_DIGEST,
            state=ProviderWorkBindingState.REVOKED,
            revocation_generation=current.revocation_generation + 1,
            updated_at=self._store.timestamp(),
        )
        replacement = replace(
            provisional,
            binding_digest=provisional.expected_digest(),
        )
        with self._store.transaction() as transaction:
            return transaction.compare_and_swap(expected, replacement)

    def revoke_in_transaction(
        self,
        connection: object,
        expected: ProviderWorkBindingFence,
    ) -> ProviderWorkBindingWriteResult:
        """Revoke an exact binding inside a caller-owned aggregate transaction."""

        if not isinstance(expected, ProviderWorkBindingFence):
            raise ValueError("expected must be a ProviderWorkBindingFence")
        current = expected.expected_record
        if current.state is not ProviderWorkBindingState.ACTIVE:
            return ProviderWorkBindingWriteResult(
                ProviderWorkAuthorityWriteOutcome.CONFLICT,
                current,
            )
        provisional = replace(
            current,
            generation=current.generation + 1,
            binding_digest=_PLACEHOLDER_DIGEST,
            state=ProviderWorkBindingState.REVOKED,
            revocation_generation=current.revocation_generation + 1,
            updated_at=self._store.timestamp(),
        )
        replacement = replace(
            provisional,
            binding_digest=provisional.expected_digest(),
        )
        transition = getattr(self._store, "_compare_and_swap_binding_in_transaction", None)
        if not callable(transition):
            raise ValueError("store must implement transactional binding transitions")
        return transition(connection, expected, replacement)

    def rebind(
        self,
        expected: ProviderWorkBindingFence,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingWriteResult:
        """Atomically replace an active binding's server-resolved assignment.

        Rebinding preserves the deterministic binding identity while advancing
        its generation.  It must never revoke first and then attempt issuance:
        issuance of the same identity is intentionally a conflict.
        """
        if not isinstance(expected, ProviderWorkBindingFence):
            raise ValueError("expected must be a ProviderWorkBindingFence")
        if not isinstance(root, ProviderWorkBindingRoot):
            raise ValueError("root must be a ProviderWorkBindingRoot")
        if self._resolver is None:
            raise PermissionError("server-owned provider assignment is unavailable")
        seed = self._resolver.resolve(root)
        if not isinstance(seed, ProviderWorkBindingSeed) or (
            seed.owner_user_id != root.owner_user_id
            or seed.universe_id != root.universe_id
            or seed.provider != root.provider
        ):
            raise PermissionError("server-owned provider assignment is unavailable")
        current = expected.expected_record
        if current.state not in {
            ProviderWorkBindingState.ACTIVE,
            ProviderWorkBindingState.REVOKED,
        }:
            return ProviderWorkBindingWriteResult(
                ProviderWorkAuthorityWriteOutcome.CONFLICT, current
            )
        replacement = replace(
            current,
            generation=current.generation + 1,
            state=ProviderWorkBindingState.ACTIVE,
            credential_reference_digest=seed.credential_reference_digest,
            allowed_operations=seed.allowed_operations,
            allowed_roles=seed.allowed_roles,
            assignment_generation=seed.assignment_generation,
            assignment_digest=seed.assignment_digest,
            revocation_generation=0,
            max_invocations=seed.max_invocations,
            max_tokens=seed.max_tokens,
            max_cost_microunits=seed.max_cost_microunits,
            expires_at=seed.expires_at,
            updated_at=self._store.timestamp(),
            binding_digest=_PLACEHOLDER_DIGEST,
        )
        replacement = replace(replacement, binding_digest=replacement.expected_digest())
        with self._store.transaction() as transaction:
            return transaction.compare_and_swap(expected, replacement)

    def rebind_in_transaction(
        self,
        connection: object,
        expected: ProviderWorkBindingFence,
        root: ProviderWorkBindingRoot,
    ) -> ProviderWorkBindingWriteResult:
        """Rebind server-derived authority inside a caller-owned transaction."""

        if not isinstance(expected, ProviderWorkBindingFence):
            raise ValueError("expected must be a ProviderWorkBindingFence")
        if not isinstance(root, ProviderWorkBindingRoot):
            raise ValueError("root must be a ProviderWorkBindingRoot")
        if self._resolver is None:
            raise PermissionError("server-owned provider assignment is unavailable")
        transactional = getattr(self._resolver, "resolve_current_in_transaction", None)
        seed = (
            transactional(connection, root)
            if callable(transactional)
            else self._resolver.resolve(root)
        )
        if not isinstance(seed, ProviderWorkBindingSeed) or (
            seed.owner_user_id != root.owner_user_id
            or seed.universe_id != root.universe_id
            or seed.provider != root.provider
        ):
            raise PermissionError("server-owned provider assignment is unavailable")
        current = expected.expected_record
        replacement = replace(
            current,
            generation=current.generation + 1,
            state=ProviderWorkBindingState.ACTIVE,
            credential_reference_digest=seed.credential_reference_digest,
            allowed_operations=seed.allowed_operations,
            allowed_roles=seed.allowed_roles,
            assignment_generation=seed.assignment_generation,
            assignment_digest=seed.assignment_digest,
            revocation_generation=0,
            max_invocations=seed.max_invocations,
            max_tokens=seed.max_tokens,
            max_cost_microunits=seed.max_cost_microunits,
            expires_at=seed.expires_at,
            updated_at=self._store.timestamp(),
            binding_digest=_PLACEHOLDER_DIGEST,
        )
        replacement = replace(replacement, binding_digest=replacement.expected_digest())
        transition = getattr(self._store, "_compare_and_swap_binding_in_transaction", None)
        if not callable(transition):
            raise ValueError("store must implement transactional binding transitions")
        return transition(connection, expected, replacement)


__all__ = [
    "ProviderInvocationCarrier",
    "ProviderInvocationReservation",
    "ProviderInvocationLaunchRequest",
    "ProviderInvocationReservationRequest",
    "ProviderInvocationReservationState",
    "ProviderInvocationSettlementOwner",
    "ProviderInvocationReservationWriteResult",
    "ProviderWorkAuthorityStore",
    "ProviderWorkAuthorityWriteOutcome",
    "ProviderWorkBinding",
    "ProviderWorkBindingFence",
    "ProviderWorkBindingResolver",
    "ProviderWorkTransactionalBindingResolver",
    "ProviderWorkBindingRoot",
    "ProviderWorkBindingSeed",
    "ProviderWorkBindingService",
    "ProviderWorkBindingState",
    "ProviderWorkBindingWriteResult",
    "ProviderWorkExecutionClaim",
    "ProviderWorkExecutionClaimRequest",
    "ProviderWorkExecutionClaimState",
    "ProviderWorkExecutionClaimWriteResult",
    "ProviderWorkReceiptState",
    "ProviderWorkReceiptWriteResult",
    "ProviderUniverseWorkAuthority",
    "ProviderUniverseWorkReceipt",
    "ProviderUniverseWorkResolver",
    "ProviderUniverseWorkRoot",
    "provider_work_binding_id",
    "provider_work_binding_class",
]
