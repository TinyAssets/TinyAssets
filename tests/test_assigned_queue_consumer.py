from __future__ import annotations

from pathlib import Path

import pytest

import tinyassets.control_plane.tick as consumer_module
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401

pytestmark = pytest.mark.usefixtures("cloud_runtime")










def test_universe_server_consumer_is_started_and_stopped(
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

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ASSIGNED_QUEUE_CONSUMER", "1")
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
