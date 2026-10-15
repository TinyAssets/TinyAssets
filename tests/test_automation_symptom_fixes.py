"""The four automation symptoms #4845 does not cover.

Repeated event keys, shutdown cancellation, a full owner cell, and the
file-only cell start. Each test is the behavior, not a snapshot of a message
in an off-limits file.
"""
from __future__ import annotations

import base64
import contextlib
import os
from pathlib import Path
from types import SimpleNamespace

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



@pytest.mark.role_split
def test_a_read_walks_the_center_once_and_a_write_walks_it_twice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from tinyassets import role_decoder, role_tools, universe_tools

    center = tmp_path / "u-home"
    center.mkdir()
    walks: list[Path] = []
    script: list[dict] = []

    def files(client, **kwargs):
        walks.append(kwargs["center"])
        return {"truncated": False, "skipped": []}

    def read(stream, bound):
        return script.pop(0)

    @contextlib.contextmanager
    def boundary(path):
        yield

    @contextlib.contextmanager
    def slot(path, *, on_wait=None, waited=None):
        if waited is not None:
            waited.append(0.0)
        yield

    @contextlib.contextmanager
    def tool_cell(client, **kwargs):
        yield SimpleNamespace(stream=_Stream(), wait=lambda timeout: 0)

    def open_budget(path, *, min_free_bytes, min_free_inodes):
        return SimpleNamespace(notice="", bound=1, settle=lambda: None,
                               breach=lambda force=False: None)

    def identity(root, *, principal):
        info = center.stat()
        return SimpleNamespace(uid=info.st_uid, gid=info.st_gid)

    monkeypatch.setattr(role_decoder, "_bounded_client", object())
    monkeypatch.setattr(role_tools, "_files", files)
    monkeypatch.setattr(role_tools, "_read", read)
    monkeypatch.setattr(role_tools, "_tool_cell", tool_cell)
    monkeypatch.setattr("tinyassets.broker.supervisor._protect_daemon", lambda: None)
    monkeypatch.setattr("tinyassets.storage.data_dir", lambda: tmp_path)
    monkeypatch.setattr("tinyassets.role_scope.owner_principal", lambda path: "alice")
    monkeypatch.setattr("tinyassets.broker.owner_identities.owner_identity", identity)
    monkeypatch.setattr("tinyassets.workspace_fs.open_dir_nofollow",
                        lambda path: os.open(path, os.O_RDONLY | os.O_DIRECTORY))
    monkeypatch.setattr("tinyassets.starter_seeds.seed_boundary", boundary)
    monkeypatch.setattr(universe_tools, "_slot", slot)
    monkeypatch.setattr("tinyassets.jail_disk.open_budget", open_budget)
    role_tools._note_maintenance(center, clean=False)

    def once(*, reuse: bool) -> None:
        info = center.stat()
        script.append({"cell": {
            "uid": info.st_uid - 300000,
            "gid": info.st_uid - 300000,
            "source": [info.st_dev, info.st_ino],
            "sockets": {},
            "fds": [0, 1, 2],
            "groups": [],
            "caps": "zero",
            "nnp": 1,
            "profile": "cell-nested",
        }})
        script.append({"result": {
            "exit_code": 0,
            "output": base64.b64encode(b"ok").decode(),
            "killed": None,
            "elapsed": 0.01,
        }})
        role_tools.run(
            center, ["/bin/true"], agent_id="main", stdin=None,
            limits=universe_tools.DEFAULT_LIMITS, wall_seconds=None,
            output_bytes=None, on_wait=None, egress_socket=None, ta_socket=None,
            reuse_maintenance=reuse,
        )

    try:
        once(reuse=True)
        assert len(walks) == 1
        once(reuse=True)
        assert len(walks) == 1
        once(reuse=False)
        assert len(walks) == 3
        once(reuse=True)
        assert len(walks) == 3
    finally:
        role_tools._note_maintenance(center, clean=False)


class _Stream:
    def settimeout(self, seconds: float) -> None:
        return None

    def sendall(self, payload: bytes) -> None:
        return None

    def makefile(self, mode: str):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args) -> bool:
        return False


def test_file_reads_reuse_maintenance_and_writes_do_not(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import sys
    import types

    from tinyassets import universe_tools

    calls: list[dict] = []

    def runner(*args, **kwargs):
        calls.append(kwargs)
        return universe_tools.ToolRun(0, b"ok\n", None, 0.0)

    research = types.ModuleType("tinyassets.research_capability")
    research.research_refusal = lambda *args, **kwargs: None
    monkeypatch.setitem(sys.modules, "tinyassets.research_capability", research)
    monkeypatch.setattr(universe_tools, "RUNNER", runner)
    universe = tmp_path / "u-home"
    universe.mkdir()
    assert universe_tools.read_file(universe, "notes/echoes", agent_id="main") == "ok\n"
    shown = universe_tools._read_image(universe, "/u/a.png", universe_tools.DEFAULT_LIMITS,
                                        agent_id="main")
    written = universe_tools.write_file(universe, "notes/echoes", "x", agent_id="main")
    assert calls[0]["reuse_maintenance"] is True
    assert calls[1]["reuse_maintenance"] is True
    assert calls[2].get("reuse_maintenance", False) is False
    assert written.startswith("wrote ")
    assert shown is not None
