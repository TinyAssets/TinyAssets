from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

import tinyassets.background_served_provider as background_provider
import tinyassets.platform_runtime_provenance as platform_runtime_provenance
from tinyassets.background_branch_authority import (
    BackgroundBranchAttempt,
    BackgroundBranchAttemptFence,
    BackgroundBranchAttemptLifecycle,
    BackgroundBranchBinding,
    BackgroundBranchBindingFence,
    BackgroundBranchHoldReason,
    build_request_task_attempt_key,
)
from tinyassets.background_branch_authority_service import (
    BackgroundBranchAuthorityOwnerKind,
    BackgroundBranchAuthorityOwnerRecord,
    BackgroundBranchAuthorityOwnerState,
)
from tinyassets.branch_tasks_v2 import AssignedConsumerLease, Epoch2BranchTask
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.platform_runtime_provenance import (
    CLOUD,
    ProcessProvenanceObservation,
    RuntimeProvenance,
)
from tinyassets.providers.base import UniverseContext

#: One admitted verdict for this module, built from the production dataclass.
_ADMITTED_PROVENANCE = RuntimeProvenance(
    verdict=CLOUD,
    reason="instance_match",
    metadata_reachable=True,
    expected_identity_prepared=True,
)


@pytest.fixture(autouse=True)
def _module_local_cloud_admission(monkeypatch):
    """Admit this module's process, through the real observation seam.

    Every test in this file drives the cloud provider lane, which now requires
    an admitted cloud runtime (`cloud-only-runtime-admission`, enforcement site
    (C)). With no verdict these processes are unobserved, and unobserved is a
    refusal -- so without this each test would be asserting the admission gate
    instead of the behaviour it was written for.

    Deliberately module-local and NOT in `tests/conftest.py`: a suite-wide
    autouse admission would silently admit the negative regressions in
    `tests/test_cloud_only_admission_regressions.py` and
    `tests/test_cloud_only_provider_admission_regressions.py` as well, and
    those tests are the whole point. Those files bind their own verdict per
    test through the same seam.

    The seam is the production one -- a `ProcessProvenanceObservation` with an
    injected resolver -- not an env var, not a flag and not a tests-only branch
    in production code. No metadata socket is opened, and a green run here
    establishes no cloud fact and no production authority.
    """
    observation = ProcessProvenanceObservation(resolver=lambda: _ADMITTED_PROVENANCE)
    observation.observe()
    monkeypatch.setattr(
        platform_runtime_provenance, "_PROCESS_OBSERVATION", observation
    )
    return observation



@contextmanager
def _connection(conn: sqlite3.Connection):
    yield conn


class _AdmissionStore:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self._conn = conn

    def connection(self):
        return _connection(self._conn)


class _AdmissionFence:
    @contextmanager
    def shared(self, _universe_dir: Path):
        yield


def _task() -> Epoch2BranchTask:
    return Epoch2BranchTask(
        branch_task_id="bt2_" + "a" * 32,
        admission_id="adm_" + "b" * 32,
        request_id="req_" + "c" * 32,
        branch_def_id="branch_a",
        universe_id="universe_a",
        actor_id="acct_owner_a",
        automation_id="automation-a",
        automation_activation_epoch=7,
        automation_executor_class="cloud",
        automation_subject_ref="branch_version_a",
        automation_subject_digest="sha256:" + "d" * 64,
        automation_branch_version="branch_version_a",
        automation_lease_id="activation-lease-a",
        status="running",
        claimed_by="assigned-consumer:boot-a",
        claimed_at="2099-01-01T00:00:00+00:00",
        lease_expires_at="2099-01-01T01:00:00+00:00",
    )


def _lease() -> AssignedConsumerLease:
    return AssignedConsumerLease(
        consumer_id="assigned-consumer:boot-a",
        lease_id="assigned-lease:boot-a",
        expires_at="2099-01-01T01:00:00+00:00",
    )


def _authority_fixture(
    tmp_path: Path,
    monkeypatch,
    *,
    binding_expires_at: str = "2099-01-01T02:00:00+00:00",
    attempt_lifecycle: str = "claimed",
    attempt_lease_expires_at: str | None = "2099-01-01T02:00:00+00:00",
    fabricated: bool = False,
    missing_owner: bool = False,
    provider: str = "codex",
):
    binding_expires_at = binding_expires_at.replace("+00:00", "Z")
    if attempt_lease_expires_at is not None:
        attempt_lease_expires_at = attempt_lease_expires_at.replace("+00:00", "Z")
    universe_dir = tmp_path / "universe_a"
    universe_dir.mkdir()
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE branch_tasks_v2 (
            branch_task_id TEXT PRIMARY KEY, admission_id TEXT, request_id TEXT,
            status TEXT, claimed_by TEXT, claimed_at TEXT, lease_expires_at TEXT,
            universe_id TEXT, automation_id TEXT,
            branch_def_id TEXT,
            automation_activation_epoch INTEGER, automation_subject_ref TEXT,
            automation_subject_digest TEXT, automation_branch_version TEXT,
            automation_lease_id TEXT
        );
        CREATE TABLE request_admissions (
            admission_id TEXT, request_id TEXT, branch_task_id TEXT,
            actor_id TEXT, body_digest TEXT, grant_generation INTEGER
        );
        CREATE TABLE automation_activations (
            universe_id TEXT, automation_id TEXT, state TEXT, epoch INTEGER,
            subject_ref TEXT, subject_digest TEXT,
            immutable_branch_version TEXT, lease_id TEXT
        );
        """
    )
    from tinyassets.storage.background_branch_authority import (
        _SCHEMA as _BACKGROUND_SCHEMA,
    )
    from tinyassets.storage.background_branch_authority import (
        _SQLiteBackgroundBranchAuthorityTransaction,
    )
    from tinyassets.storage.provider_work_authority import _SCHEMA

    conn.executescript(_SCHEMA)
    conn.executescript(_BACKGROUND_SCHEMA)
    task = _task()
    conn.execute(
        "INSERT INTO branch_tasks_v2 VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            task.branch_task_id,
            task.admission_id,
            task.request_id,
            task.status,
            task.claimed_by,
            task.claimed_at,
            task.lease_expires_at,
            task.universe_id,
            task.automation_id,
            task.branch_def_id,
            task.automation_activation_epoch,
            task.automation_subject_ref,
            task.automation_subject_digest,
            task.automation_branch_version,
            task.automation_lease_id,
        ),
    )
    conn.execute(
        "INSERT INTO request_admissions VALUES (?,?,?,?,?,?)",
        (
            task.admission_id,
            task.request_id,
            task.branch_task_id,
            task.actor_id,
            "sha256:" + "e" * 64,
            3,
        ),
    )
    conn.execute(
        "INSERT INTO automation_activations VALUES (?,?,?,?,?,?,?,?)",
        (
            task.universe_id,
            task.automation_id,
            "active",
            task.automation_activation_epoch,
            task.automation_subject_ref,
            task.automation_subject_digest,
            task.automation_branch_version,
            task.automation_lease_id,
        ),
    )
    conn.commit()

    assignment = SimpleNamespace(
        state="ready",
        owner_user_id="acct_owner_a",
        provider=provider,
        generation=4,
        assignment_digest="sha256:" + "f" * 64,
        manifest_digest="",
    )
    serving_binding = SimpleNamespace(expires_at="2099-01-01T02:00:00+00:00")
    custody = SimpleNamespace(
        reference_id="credential-a",
        reference_digest="sha256:" + "1" * 64,
        generation=2,
    )
    background_binding = BackgroundBranchBinding.from_dict(
        {
            "schema_version": 1,
            "binding_id": "bnd_background_a",
            "status": "active",
            "generation": 5,
            "binding_digest": "sha256:" + "2" * 64,
            "authorizing_principal_id": task.actor_id,
            "universe_id": task.universe_id,
            "branch_def_id": task.branch_def_id,
            "operation": "invoke_branch_version",
            "source_kind": "request_admission",
            "source_id": task.request_id,
            "source_revision": "3",
            "source_digest": "sha256:" + "e" * 64,
            "revocation_generation": 0,
            "target_mode": "pinned_version",
            "pinned_branch_version_id": task.automation_branch_version,
            "permitted_executor_classes": ["cloud"],
            "daemon_id": "daemon_background_a",
            "runtime_id": "runtime_background_a",
            "expires_at": binding_expires_at,
            "max_attempts": 2,
            "remaining_depth": 1,
            "remaining_count": 2,
            "remaining_cost_microunits": 10_000,
            "child_delegation": {
                "allowed_branch_def_ids": [],
                "allowed_operations": [],
                "max_depth": 0,
                "max_count": 0,
                "max_cost_microunits": 0,
            },
        }
    )
    lifecycle = BackgroundBranchAttemptLifecycle(attempt_lifecycle)
    held = lifecycle is BackgroundBranchAttemptLifecycle.TARGET_AUTHORITY_HELD
    terminal = lifecycle in {
        BackgroundBranchAttemptLifecycle.SUCCEEDED,
        BackgroundBranchAttemptLifecycle.FAILED,
        BackgroundBranchAttemptLifecycle.CANCELLED,
    }
    attempt = BackgroundBranchAttempt.from_dict(
        {
            "schema_version": 1,
            "attempt_id": "att_background_a",
            "logical_attempt_key": build_request_task_attempt_key(
                tenant_id=task.actor_id,
                request_id=task.request_id,
                admission_id=task.admission_id,
                task_id=task.branch_task_id,
                body_digest="sha256:" + "e" * 64,
                admission_generation=3,
            ),
            "binding_id": background_binding.binding_id,
            "binding_digest": background_binding.binding_digest,
            "binding_generation": background_binding.generation,
            "authorizing_principal_id": task.actor_id,
            "universe_id": task.universe_id,
            "branch_def_id": task.branch_def_id,
            "branch_version_id": task.automation_branch_version,
            "branch_content_digest": task.automation_subject_digest,
            "operation": "invoke_branch_version",
            "source_kind": "request_admission",
            "source_id": task.request_id,
            "source_generation": 3,
            "executor_audience": {
                "executor_class": "cloud",
                "daemon_id": background_binding.daemon_id,
                "runtime_id": background_binding.runtime_id,
                "worker_id": "worker_background_a",
            },
            "claim_generation": 1,
            "lease_generation": 1,
            "lease_expires_at": attempt_lease_expires_at,
            "remaining_depth": 1,
            "remaining_count": 2,
            "remaining_cost_microunits": 10_000,
            "lifecycle": lifecycle.value,
            "hold_reason": (
                BackgroundBranchHoldReason.TARGET_UNAUTHORIZED.value if held else None
            ),
            "terminal_reason": "test_terminal" if terminal else None,
            "created_at": "2026-08-22T00:00:00Z",
            "updated_at": "2026-08-22T00:01:00Z",
            "provenance": {
                "authorizing_principal_id": task.actor_id,
                "source_kind": "request_admission",
                "source_id": task.request_id,
                "executor_class": "cloud",
                "daemon_id": background_binding.daemon_id,
                "runtime_id": background_binding.runtime_id,
                "worker_id": "worker_background_a",
                "parent_attempt_id": None,
                "origin_attempt_id": "att_background_a",
                "audit_correlation_ids": [task.request_id],
                "receipt_refs": {
                    "b2_execution_grant_id": None,
                    "provider_work_receipt_id": None,
                    "provider_attempt_receipt_id": None,
                    "payment_receipt_id": None,
                    "effect_receipt_id": None,
                },
            },
        }
    )
    if not fabricated:
        background_txn = _SQLiteBackgroundBranchAuthorityTransaction(conn)
        background_txn.insert_binding(background_binding)
        background_txn.insert_attempt(attempt)
        if not missing_owner:
            background_txn.insert_owner(
                BackgroundBranchAuthorityOwnerRecord(
                    owner_kind=BackgroundBranchAuthorityOwnerKind.QUEUE_TASK,
                    owner_id=task.branch_task_id,
                    universe_id=task.universe_id,
                    authorizing_principal_id=task.actor_id,
                    source_generation=attempt.source_generation,
                    transition_generation=1,
                    state=BackgroundBranchAuthorityOwnerState.RUNNING,
                    binding=BackgroundBranchBindingFence(background_binding),
                    attempt=BackgroundBranchAttemptFence(attempt),
                    hold_reason=None,
                    updated_at="2026-08-22T00:02:00Z",
                )
            )
    conn.commit()

    class _BackgroundStore:
        def __init__(self, _base_path):
            pass

        @staticmethod
        def read_authority_in_transaction(_conn, *, logical_attempt_key):
            assert logical_attempt_key
            return background_binding, attempt

        @staticmethod
        def read_queue_owner_in_transaction(_conn, *, owner_id):
            return SimpleNamespace(owner_id=owner_id, state=SimpleNamespace(value="running"))

    monkeypatch.setattr(background_provider, "_branch_roles", lambda *_a: ("writer",))
    monkeypatch.setattr(background_provider, "_branch_snapshot", lambda *_a: {
        "node_defs": [{"node_type": "prompt", "model_hint": "writer",
                       "prompt_template": "ordinary background work"}],
    })
    monkeypatch.setattr(
        background_provider, "provider_assignment_admission", lambda: _AdmissionFence()
    )
    monkeypatch.setattr(
        background_provider,
        "load_provider_assignment_in_transaction",
        lambda *_a, **_k: assignment,
    )
    import tinyassets.config as config_module
    import tinyassets.credential_vault as vault_module
    import tinyassets.provider_assignment as assignment_module
    import tinyassets.provider_serving_binding as serving_module
    import tinyassets.storage.background_branch_authority as background_store_module
    import tinyassets.storage.request_admissions as admission_store_module

    monkeypatch.setattr(config_module, "load_universe_config", lambda _path: {})
    monkeypatch.setattr(
        assignment_module,
        "load_provider_assignment_in_transaction",
        lambda *_a, **_k: assignment,
    )
    monkeypatch.setattr(
        admission_store_module,
        "RequestAdmissionStore",
        lambda _base_path: _AdmissionStore(conn),
    )
    if fabricated:
        monkeypatch.setattr(
            background_store_module,
            "SQLiteBackgroundBranchAuthorityStore",
            _BackgroundStore,
        )
    monkeypatch.setattr(
        serving_module,
        "resolve_serving_agent_binding",
        # REAL shape (agent_bindings row): the key is "agent_binding_id". The old
        # stub said "binding_id" and hid a KeyError on every real launch.
        lambda *_a, **_k: {"agent_binding_id": "agent-a", "revision": 2},
    )
    current_assignment = {"value": assignment}
    monkeypatch.setattr(
        serving_module,
        "_current_serving_authority",
        lambda *_a, **_k: (
            current_assignment["value"],
            serving_binding,
            custody,
        ),
    )
    monkeypatch.setattr(serving_module, "_is_open_provider", lambda _provider: False)

    events: list[str] = []
    snapshot = SimpleNamespace(
        generation=custody.generation,
        reference_digest=custody.reference_digest,
        directory=tmp_path / "snapshot-a",
    )
    monkeypatch.setattr(
        vault_module,
        "snapshot_llm_subscription_credential",
        lambda **_kwargs: events.append("snapshot") or snapshot,
    )
    monkeypatch.setattr(
        vault_module,
        "cleanup_llm_credential_snapshot",
        lambda value: events.append("cleanup") if value is snapshot else None,
    )
    monkeypatch.setattr(
        background_provider,
        "_hold_background_authority",
        lambda _base_path, _task: events.append("hold"),
    )
    return task, conn, assignment, current_assignment, events


def _reservation_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM provider_invocation_reservations").fetchone()[0]
    )


def test_fabricated_background_objects_cannot_mint_a_carrier(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task, conn, _assignment, _current, events = _authority_fixture(
        tmp_path,
        monkeypatch,
        fabricated=True,
    )
    raw_calls: list[str] = []
    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path,
        task,
        _lease(),
        lambda *_a, **_k: raw_calls.append("raw") or "unexpected",
    )

    with pytest.raises(ProviderAuthorityHeldError):
        session("prompt")

    assert raw_calls == []
    assert _reservation_count(conn) == 0
    assert events == ["snapshot", "hold", "cleanup"]


def test_missing_queue_owner_is_refused_before_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task, conn, _assignment, _current, events = _authority_fixture(
        tmp_path,
        monkeypatch,
        missing_owner=True,
    )
    raw_calls: list[str] = []
    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path,
        task,
        _lease(),
        lambda *_a, **_k: raw_calls.append("raw") or "unexpected",
    )

    with pytest.raises(ProviderAuthorityHeldError):
        session("prompt")

    assert raw_calls == []
    assert _reservation_count(conn) == 0
    assert events == ["snapshot", "hold", "cleanup"]


def test_expired_background_binding_is_refused_before_provider_call(
    tmp_path: Path,
    monkeypatch,
) -> None:
    task, conn, _assignment, _current, events = _authority_fixture(
        tmp_path,
        monkeypatch,
        binding_expires_at="2026-08-23T00:00:00+00:00",
    )
    terminalized: list[tuple[str, str]] = []
    monkeypatch.setattr(
        background_provider,
        "terminalize_background_queue_authority",
        lambda _base_path, _task, *, status, reason: terminalized.append(
            (status, reason)
        ),
    )
    raw_calls: list[str] = []
    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path,
        task,
        _lease(),
        lambda *_a, **_k: raw_calls.append("raw") or "unexpected",
    )

    with pytest.raises(ProviderAuthorityHeldError):
        session("prompt")

    assert raw_calls == []
    assert _reservation_count(conn) == 0
    assert events == []
    assert terminalized == [("failed", "background_binding_expired")]


@pytest.mark.parametrize(
    ("lifecycle", "lease_expires_at"),
    [
        ("claimed", "2026-08-23T00:00:00+00:00"),
        ("target_authority_held", None),
        ("succeeded", None),
    ],
)
def test_stale_held_or_terminal_background_attempt_is_refused_before_provider_call(
    tmp_path: Path,
    monkeypatch,
    lifecycle: str,
    lease_expires_at: str | None,
) -> None:
    task, conn, _assignment, _current, events = _authority_fixture(
        tmp_path,
        monkeypatch,
        attempt_lifecycle=lifecycle,
        attempt_lease_expires_at=lease_expires_at,
    )
    terminalized: list[tuple[str, str]] = []
    monkeypatch.setattr(
        background_provider,
        "terminalize_background_queue_authority",
        lambda _base_path, _task, *, status, reason: terminalized.append((status, reason)),
    )
    raw_calls: list[str] = []
    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path,
        task,
        _lease(),
        lambda *_a, **_k: raw_calls.append("raw") or "unexpected",
    )

    with pytest.raises(ProviderAuthorityHeldError):
        session("prompt")

    assert raw_calls == []
    assert _reservation_count(conn) == 0
    # Identity is checked before discovery or credential snapshotting. Invalid
    # attempts use the existing terminal projection, not a repairable hold.
    assert events == []
    expected_reason = (
        "background_attempt_lease_expired"
        if lifecycle == "claimed" else "background_attempt_inactive"
    )
    assert terminalized == [("failed", expected_reason)]


def test_cross_universe_and_provider_substitution_never_reaches_ambient_call(
    tmp_path: Path, monkeypatch
) -> None:
    task, conn, _assignment, _current, _events = _authority_fixture(tmp_path, monkeypatch)
    raw_calls: list[str] = []
    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path,
        task,
        _lease(),
        lambda *_a, **_k: raw_calls.append("raw") or "unexpected",
    )

    with pytest.raises(PermissionError, match="command center cannot be substituted"):
        session(
            "prompt",
            universe_context=UniverseContext(universe_dir=tmp_path / "universe-b", config={}),
        )
    with pytest.raises((PermissionError, ProviderAuthorityHeldError)):
        session.call_with_policy_sync(
            "writer",
            "prompt",
            "",
            {"writer": {"provider": "claude-code"}},
        )

    assert raw_calls == []
    assert _reservation_count(conn) == 0


def test_assignment_rotation_before_launch_fails_closed(tmp_path: Path, monkeypatch) -> None:
    task, conn, assignment, current, events = _authority_fixture(tmp_path, monkeypatch)
    current["value"] = SimpleNamespace(**{**assignment.__dict__, "generation": 5})
    raw_calls: list[str] = []
    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path,
        task,
        _lease(),
        lambda *_a, **_k: raw_calls.append("raw") or "unexpected",
    )

    with pytest.raises(ProviderAuthorityHeldError):
        session("prompt")

    assert raw_calls == []
    assert _reservation_count(conn) == 0
    assert events == ["hold"]


def test_budget_is_reserved_before_launch_snapshot_is_cleaned_and_retry_does_not_remint(
    tmp_path: Path, monkeypatch
) -> None:
    task, conn, _assignment, _current, events = _authority_fixture(tmp_path, monkeypatch)
    launches: list[int] = []

    def raw_provider(*_args, **_kwargs):
        carrier = _kwargs["universe_context"].provider_invocation
        assert carrier.validate_for_call(
            role="writer", operation=background_provider.BACKGROUND_BRANCH_RUN_OPERATION
        ) == "codex"
        row = conn.execute("SELECT state FROM provider_invocation_reservations").fetchone()
        assert row["state"] == "launch_started"
        launches.append(1)
        events.append("launch")
        return "ok"

    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path, task, _lease(), raw_provider
    )
    assert session("prompt") == "ok"
    assert events == ["snapshot", "launch", "cleanup"]

    restarted = background_provider._BackgroundAssignedProviderSession(
        tmp_path, task, _lease(), raw_provider
    )
    with pytest.raises(ProviderAuthorityHeldError):
        restarted("prompt")

    assert launches == [1]
    assert _reservation_count(conn) == 1


def test_background_binding_invocation_ceiling_is_durable(tmp_path: Path, monkeypatch) -> None:
    task, conn, _assignment, _current, _events = _authority_fixture(tmp_path, monkeypatch)
    launches: list[str] = []

    def raw_provider(*_args, **kwargs):
        carrier = kwargs["universe_context"].provider_invocation
        carrier.validate_for_call(
            role="writer", operation=background_provider.BACKGROUND_BRANCH_RUN_OPERATION
        )
        launches.append("launch")
        return "ok"

    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path, task, _lease(), raw_provider
    )

    assert session("first") == "ok"
    assert session("second") == "ok"
    with pytest.raises(ProviderAuthorityHeldError):
        session("third")

    assert launches == ["launch", "launch"]
    assert _reservation_count(conn) == 2


def test_carrier_reservations_are_unique_ordered_and_armed_before_launch(
    tmp_path: Path, monkeypatch
) -> None:
    task, conn, _assignment, _current, _events = _authority_fixture(tmp_path, monkeypatch)
    launches: list[str] = []

    def raw_provider(*_args, **kwargs):
        carrier = kwargs["universe_context"].provider_invocation
        carrier.validate_for_call(
            role="writer", operation=background_provider.BACKGROUND_BRANCH_RUN_OPERATION
        )
        launches.append("launch")
        return "ok"

    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path, task, _lease(), raw_provider
    )
    assert session("first") == "ok"
    assert session("second") == "ok"
    rows = conn.execute(
        "SELECT invocation_key, ordinal, state "
        "FROM provider_invocation_reservations ORDER BY ordinal"
    ).fetchall()
    assert launches == ["launch", "launch"]
    assert [row["ordinal"] for row in rows] == [1, 2]
    assert len({row["invocation_key"] for row in rows}) == 2
    assert [row["state"] for row in rows] == ["launch_started", "launch_started"]


def test_background_carrier_actuals_are_finalized_by_the_real_router(
    tmp_path: Path, monkeypatch
) -> None:
    """The carrier launch settles actual token and cost accounting."""
    import asyncio

    from tinyassets.providers.base import BaseProvider, ModelConfig, ProviderResponse
    from tinyassets.providers.router import ProviderRouter

    task, conn, _assignment, _current, _events = _authority_fixture(tmp_path, monkeypatch)
    import tinyassets.config as config_module
    from tinyassets.config import UniverseConfig

    monkeypatch.setattr(
        config_module, "load_universe_config",
        lambda _path: UniverseConfig(allowed_providers=["codex"]),
    )

    class _CountingProvider(BaseProvider):
        def __init__(self) -> None:
            self.name = "codex"
            self.family = "codex"
            self.calls = 0

        async def complete(self, prompt, system, config: ModelConfig, *, universe_dir=None):
            self.calls += 1
            return ProviderResponse(
                text="routed-ok", provider="codex", model="fake", family="codex",
                latency_ms=0.0, input_tokens=700, output_tokens=300, cost_microunits=50,
            )

    fake = _CountingProvider()
    router = ProviderRouter({"codex": fake})

    def through_real_router(prompt, system, *, role, config, **kwargs):
        # Exactly what production does: the session's authority-bearing context is
        # handed to the real router, which reserves, launches, and finalizes.
        resp = asyncio.run(
            router.call(role, prompt, system, operation=kwargs["operation"],
                        universe_context=kwargs["universe_context"])
        )
        return resp.text

    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path, task, _lease(), through_real_router
    )
    assert session("first") == "routed-ok"
    assert fake.calls == 1
    reservation = conn.execute(
        "SELECT state, record_json FROM provider_invocation_reservations"
    ).fetchone()
    assert reservation["state"] == "succeeded"
    assert '"actual_total_tokens":1000' in reservation["record_json"]
    assert '"actual_cost_microunits":50' in reservation["record_json"]


def test_branch_roles_normalizes_bare_hex_content_hash(monkeypatch, tmp_path):
    """A real published version's content_hash is bare hex; the task carries
    sha256:<hex>. The authority compare normalizes both, so a genuine version is
    accepted (Codex #2, PR #2516) — and a truly different hash still fails closed."""
    bare = "a" * 64
    version = SimpleNamespace(
        status="active",
        branch_def_id="bdef-1",
        content_hash=bare,
        snapshot={"node_defs": [{"node_type": "prompt", "model_hint": "writer"}]},
    )
    monkeypatch.setattr(
        "tinyassets.branch_versions.get_branch_version",
        lambda *a, **k: version,
    )
    task = SimpleNamespace(
        automation_branch_version="ver-1",
        branch_def_id="bdef-1",
        automation_subject_digest=f"sha256:{bare}",
    )
    assert background_provider._branch_roles(tmp_path, task) == ("writer",)
    task.automation_subject_digest = "sha256:" + "b" * 64
    with pytest.raises(PermissionError):
        background_provider._branch_roles(tmp_path, task)


@pytest.mark.parametrize("seam", ["_branch_roles", "_branch_snapshot"])
def test_branch_version_rollback_before_launch_fails_closed(
    tmp_path: Path, monkeypatch, seam,
) -> None:
    """A rolled-back immutable Branch version cannot mint a launch carrier."""
    task, conn, _assignment, _current, events = _authority_fixture(tmp_path, monkeypatch)
    state = {"n": 0}

    def _roles(*_a):
        state["n"] += 1
        raise PermissionError("immutable Branch version is not current authority")

    monkeypatch.setattr(background_provider, seam, _roles)
    raw_calls: list[str] = []

    def raw_provider(*_a, **_k):
        raw_calls.append("raw")
        return "x"

    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path, task, _lease(), raw_provider
    )
    with pytest.raises(ProviderAuthorityHeldError):
        session("prompt")
    assert raw_calls == []  # fence raised before the provider ran
    launched = conn.execute(
        "SELECT 1 FROM provider_invocation_reservations "
        "WHERE state='launch_started'"
    ).fetchone()
    assert launched is None  # the fence blocked the launch (reservation never armed)
    assert state["n"] == 1


def test_scavenge_orphaned_launch_credentials(tmp_path: Path) -> None:
    """Startup reclamation removes stale (crash-orphaned) codex-* credential dirs but
    leaves in-flight (recent) ones and non-codex entries (Codex #4, #2516)."""
    import os
    import time as _t

    from tinyassets.credential_vault import scavenge_orphaned_launch_credentials

    root = tmp_path / ".runtime" / "provider-launch-credentials"
    root.mkdir(parents=True)
    old = root / "codex-staleaaaa"
    old.mkdir()
    (old / "cred.json").write_text("secret")
    recent = root / "codex-freshbbbb"
    recent.mkdir()
    (recent / "cred.json").write_text("secret")
    other = root / "notcodex"
    other.mkdir()
    aged = _t.time() - 7200
    os.utime(old, (aged, aged))
    removed = scavenge_orphaned_launch_credentials(tmp_path, max_age_seconds=3600)
    assert removed == 1
    assert not old.exists()      # stale orphan reclaimed
    assert recent.exists()       # too recent — could back an in-flight call
    assert other.exists()        # not a codex- snapshot dir
    # idempotent: a second sweep with nothing stale removes nothing.
    assert scavenge_orphaned_launch_credentials(tmp_path, max_age_seconds=3600) == 0


@pytest.mark.parametrize("provider", ["claude-code", "codex"])
def test_background_node_call_is_marked_as_a_workflow_node(
    tmp_path: Path, monkeypatch, provider: str,
) -> None:
    """The background lane marks node calls exactly as the foreground does.

    The provider applies its own confinement from the mark; the node's own
    settings pass through unchanged.
    """
    from tinyassets.providers.base import ModelConfig

    task, _conn, _assignment, _current, _events = _authority_fixture(
        tmp_path, monkeypatch, provider=provider,
    )
    seen: list[ModelConfig] = []

    def raw_provider(*_args, **kwargs):
        carrier = kwargs["universe_context"].provider_invocation
        assert carrier.validate_for_call(
            role="writer", operation=background_provider.BACKGROUND_BRANCH_RUN_OPERATION
        ) == provider
        seen.append(kwargs["config"])
        return "ok"

    session = background_provider._BackgroundAssignedProviderSession(
        tmp_path, task, _lease(), raw_provider
    )
    assert session("prompt", config=ModelConfig(timeout=42)) == "ok"
    [config] = seen
    assert config.timeout == 42
    assert config.workflow_node is True
    assert config.sandbox_workspace is False and config.disallowed_tools is None
