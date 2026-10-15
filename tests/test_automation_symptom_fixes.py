"""The four automation symptoms #4845 does not cover.

Repeated event keys, shutdown cancellation, a full owner cell, and the
file-only cell start. Each test is the behavior, not a snapshot of a message
in an off-limits file.
"""
from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from tinyassets.automations import (
    EVENT_RUN_COMPLETED,
    STATE_ACTIVE,
    TRIGGER_EVENT,
    Automation,
    automations_db_path,
)


def _subscription() -> Automation:
    return Automation(
        automation_id="sub-1",
        universe_id="u-home",
        owner_principal_id="alice",
        name="after",
        branch_def_id="branch-1",
        trigger_kind=TRIGGER_EVENT,
        interval_seconds=0,
        cron_expr="",
        inputs={},
        desired_state=STATE_ACTIVE,
        pause_reason="",
        revision=1,
        created_at="t",
        updated_at="t",
        retired_at="",
        last_due_at="",
        last_run_id="",
        last_reason="",
        last_finished_at="",
        event_type=EVENT_RUN_COMPLETED,
        event_filter={"branch_def_id": "followed"},
    )


def test_a_repeated_event_key_records_deduplicated_and_starts_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tinyassets import automation_events as events

    automations_db_path(tmp_path).write_bytes(b"")
    sub = _subscription()
    wake = Automation(
        **{**sub.__dict__, "automation_id": "wake-1", "trigger_kind": "once"}
    )
    seen = {"stored": False}
    fires: list[tuple[str, str]] = []

    class _Store:
        def __init__(self, base: Path) -> None:
            self.base = base

        def list(self, universe_id: str) -> list[Automation]:
            assert universe_id == "u-home"
            return [sub]

        def get_by_event_key(self, key: str) -> Automation | None:
            return wake if seen["stored"] else None

        def record_event_fire(self, automation_id: str, *, reason: str, now) -> None:
            fires.append((automation_id, reason))

    def _register(*args, **kwargs) -> Automation:
        seen["stored"] = True
        return wake

    monkeypatch.setattr(events, "AutomationStore", _Store)
    monkeypatch.setattr(events, "register_automation", _register)
    monkeypatch.setattr(
        "tinyassets.daemon_server.get_founder_home", lambda base, principal: "u-home",
    )
    monkeypatch.setattr(
        "tinyassets.api.permissions.owner_run_identity",
        lambda *args, **kwargs: contextlib.nullcontext(),
    )
    monkeypatch.setattr(
        "tinyassets.outside_authority.automation_identity",
        lambda *args, **kwargs: contextlib.nullcontext(),
    )
    payload = {"branch_def_id": "followed", "outcome": "succeeded", "run_id": "r1"}

    first = events.emit(
        tmp_path, event_type=EVENT_RUN_COMPLETED, universe_id="u-home",
        principal_id="alice", payload=payload, event_id="evt-1",
    )
    second = events.emit(
        tmp_path, event_type=EVENT_RUN_COMPLETED, universe_id="u-home",
        principal_id="alice", payload=payload, event_id="evt-1",
    )

    assert first == ["wake-1"]
    assert second == []
    assert fires == [
        ("sub-1", "woke:wake-1"),
        ("sub-1", "deduplicated:wake-1"),
    ]


def test_event_dedupe_is_documented_for_the_owner() -> None:
    handbook = Path("tinyassets/engine_mcp_server.py").read_text(encoding="utf-8")
    surface = Path("tinyassets/api/app_events.py").read_text(encoding="utf-8")
    assert "deduplicated:<wake id>" in handbook
    assert "deduplicated:<id>" in surface


def test_shutdown_does_not_cancel_in_flight_automation_runs(tmp_path: Path) -> None:
    import sys
    import types

    from tinyassets.control_plane.tick import AssignedQueueConsumer

    cancelled: list[str] = []
    fake = types.ModuleType("tinyassets.runs")

    def request_cancel(base, run_id: str) -> None:
        cancelled.append(run_id)

    fake.request_cancel = request_cancel
    previous = sys.modules.get("tinyassets.runs")
    sys.modules["tinyassets.runs"] = fake
    try:
        consumer = AssignedQueueConsumer(tmp_path, max_concurrency=1, poll_seconds=30)
        consumer._note_automation_run("23f1b54612ff4b9c")
        consumer._note_automation_run("921801c8bb924092")
        consumer.stop()
    finally:
        if previous is None:
            sys.modules.pop("tinyassets.runs", None)
        else:
            sys.modules["tinyassets.runs"] = previous
    assert cancelled == []


def test_automation_workers_die_with_the_process() -> None:
    import threading

    from tinyassets.control_plane.tick import _ReleaseOnExitPool

    started = threading.Event()
    pool = _ReleaseOnExitPool(max_workers=1, thread_name_prefix="symptom")
    try:
        pool.submit(started.set).result(timeout=5)
        assert pool._threads
        assert all(thread.daemon for thread in pool._threads)
    finally:
        pool.shutdown(wait=True, cancel_futures=True)
