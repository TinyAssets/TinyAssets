from __future__ import annotations

from pathlib import Path

import pytest

import tinyassets.runtime.assigned_queue_consumer as consumer_module
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.background_served_provider import (
    BACKGROUND_BRANCH_RUN_OPERATION,
    _branch_roles,
    authorize_background_served_provider_call,
)
from tinyassets.branch_tasks_v2 import AssignedConsumerLease, Epoch2BranchTask
from tinyassets.runtime.assigned_queue_consumer import (
    AssignedQueueConsumer,
    assigned_queue_consumer_enabled,
)

pytestmark = pytest.mark.usefixtures("cloud_runtime")


def test_assigned_queue_consumer_flag_is_dark_by_default(monkeypatch) -> None:
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    assert assigned_queue_consumer_enabled() is False


def test_flag_off_poll_performs_zero_claim_work(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    consumer = AssignedQueueConsumer(tmp_path, max_concurrency=1)
    try:
        assert consumer.poll_once() == 0
    finally:
        consumer.stop()


def test_assigned_queue_consumer_flag_requires_explicit_truthy(monkeypatch) -> None:
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "off")
    assert assigned_queue_consumer_enabled() is False
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "on")
    assert assigned_queue_consumer_enabled() is True


def test_background_authorizer_rejects_cross_consumer_before_provider_lookup(
    tmp_path,
) -> None:
    task = Epoch2BranchTask(
        branch_task_id="bt2_" + "a" * 32,
        branch_def_id="branch-a",
        universe_id="universe-a",
        claimed_by="assigned-consumer:one",
        claimed_at="2026-08-23T00:00:00+00:00",
        lease_expires_at="2026-08-23T00:30:00+00:00",
        automation_id="automation-a",
        automation_branch_version="version-a",
    )
    lease = AssignedConsumerLease(
        consumer_id="assigned-consumer:two",
        lease_id="lease-two",
        expires_at="2026-08-23T00:30:00+00:00",
    )

    try:
        authorize_background_served_provider_call(tmp_path, task, lease)
    except PermissionError as exc:
        assert "does not own" in str(exc)
    else:  # pragma: no cover - fail-closed assertion
        raise AssertionError("cross-consumer authority was accepted")


def test_background_operation_is_not_interactive_converse() -> None:
    assert BACKGROUND_BRANCH_RUN_OPERATION == "background_branch_run"
    assert BACKGROUND_BRANCH_RUN_OPERATION != "converse"


def test_background_roles_come_from_exact_immutable_branch(
    tmp_path, monkeypatch
) -> None:
    task = Epoch2BranchTask(
        branch_task_id="bt2_" + "a" * 32,
        branch_def_id="branch-a",
        universe_id="universe-a",
        automation_branch_version="version-a",
        automation_subject_digest="sha256:" + "b" * 64,
    )
    version = type(
        "Version",
        (),
        {
            "status": "active",
            "branch_def_id": "branch-a",
            "content_hash": "sha256:" + "b" * 64,
            "snapshot": {
                "node_defs": {
                    "draft": {"node_type": "prompt", "model_hint": "writer"},
                    "score": {"node_type": "prompt", "model_hint": "judge"},
                }
            },
        },
    )()
    monkeypatch.setattr("tinyassets.branch_versions.get_branch_version", lambda *_a: version)

    assert _branch_roles(tmp_path, task) == ("judge", "writer")

    version.snapshot["node_defs"]["draft"]["model_hint"] = "ambient-provider"
    try:
        _branch_roles(tmp_path, task)
    except PermissionError as exc:
        assert "unsupported provider role" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("unsupported immutable role was accepted")


def test_universe_server_flag_off_constructs_no_consumer(
    tmp_path: Path, monkeypatch, in_process_broker,
) -> None:
    import threading

    import tinyassets.engine_mcp_http as engine_http
    import tinyassets.provider_assignment as provider_assignment
    import tinyassets.universe_server as universe_server

    lifecycle: list[str] = []

    class _NoopThread:
        def __init__(self, **_kwargs):
            pass

        def start(self) -> None:
            pass

    class _MustNotConstruct:
        def __init__(self, _base_path):
            lifecycle.append("constructed")

    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    in_process_broker.supervisor_for(tmp_path)
    monkeypatch.setattr(threading, "Thread", _NoopThread)
    monkeypatch.setattr(
        provider_assignment, "reconcile_orphaned_reservations_on_boot", lambda _root: 0
    )
    monkeypatch.setattr(engine_http, "start_engine_mcp_http_servers", lambda: [])
    monkeypatch.setattr(universe_server, "create_streamable_http_app", object)
    monkeypatch.setattr(universe_server.uvicorn, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(consumer_module, "AssignedQueueConsumer", _MustNotConstruct)

    universe_server.main(host="127.0.0.1", port=0)

    assert lifecycle == []


def test_universe_server_enabled_consumer_is_started_and_stopped(
    tmp_path: Path, monkeypatch, in_process_broker,
) -> None:
    import threading

    import tinyassets.engine_mcp_http as engine_http
    import tinyassets.provider_assignment as provider_assignment
    import tinyassets.universe_server as universe_server

    lifecycle: list[str] = []

    class _NoopThread:
        def __init__(self, **_kwargs):
            pass

        def start(self) -> None:
            pass

    class _FakeConsumer:
        def __init__(self, base_path):
            assert Path(base_path) == tmp_path
            lifecycle.append("constructed")

        def start(self) -> None:
            lifecycle.append("started")

        def stop(self) -> None:
            lifecycle.append("stopped")

    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "on")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    in_process_broker.supervisor_for(tmp_path)
    monkeypatch.setattr(threading, "Thread", _NoopThread)
    monkeypatch.setattr(
        provider_assignment, "reconcile_orphaned_reservations_on_boot", lambda _root: 0
    )
    monkeypatch.setattr(engine_http, "start_engine_mcp_http_servers", lambda: [])
    monkeypatch.setattr(universe_server, "create_streamable_http_app", object)
    monkeypatch.setattr(universe_server.uvicorn, "run", lambda *_a, **_k: None)
    monkeypatch.setattr(consumer_module, "AssignedQueueConsumer", _FakeConsumer)

    universe_server.main(host="127.0.0.1", port=0)

    assert lifecycle == ["constructed", "started", "stopped"]


def test_start_is_a_noop_when_dark(tmp_path, monkeypatch) -> None:
    """With the flag unset, start() spins up NO coordinator thread — the dark guarantee
    is 'no side effect when off', not merely 'no DB writes' (Codex #6, #2516)."""
    monkeypatch.delenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", raising=False)
    c = AssignedQueueConsumer(tmp_path)
    c.start()
    assert c._thread is None
    c.stop()
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "on")
    c.start()
    assert c._thread is not None
    c.stop()


def test_migrate_creates_no_background_authority_schema_when_dark(tmp_path) -> None:
    """A dark consumer leaves ZERO background-authority schema: migrate creates the
    epoch-2 tables but NOT the consumer's authority tables — the shared claim guard is
    missing-table-safe (Codex #6, #2516)."""
    import sqlite3

    from tinyassets.storage.request_admissions import migrate_request_admission_schema

    conn = sqlite3.connect(":memory:")
    try:
        migrate_request_admission_schema(conn)
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        assert "branch_tasks_v2" in tables
        assert "background_branch_authority_owners" not in tables
        assert "background_branch_bindings" not in tables
    finally:
        conn.close()
