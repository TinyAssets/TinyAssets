"""An admitted converse turn outlives its caller and gets one terminal."""

import asyncio
import json
import threading
import time

import pytest

from tests.test_converse_handle import _founder_auth
from tinyassets import conversation_store as store


def test_cancelled_mcp_wait_does_not_cancel_admitted_turn(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    started, release = threading.Event(), threading.Event()

    def answer(*args, **kwargs):
        started.set()
        assert release.wait(5)
        return "finished after disconnect"

    monkeypatch.setattr(ui, "converse", answer)

    async def call():
        waiting = asyncio.create_task(us.mcp.call_tool(
            "converse", {"message": "keep working", "graph_id": "u-x"}))
        assert await asyncio.to_thread(started.wait, 5)
        waiting.cancel()
        try:
            await waiting
        except asyncio.CancelledError:
            pass
        release.set()
        for _ in range(200):
            rows = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")
            if len(rows) == 2:
                return rows
            await asyncio.sleep(0.01)
        raise AssertionError("detached turn did not record a terminal")

    rows = asyncio.run(call())
    assert [(row.speaker, row.text) for row in rows] == [
        ("founder", "keep working"), ("universe", "finished after disconnect")]


def test_converse_reports_mcp_progress(tmp_path, monkeypatch):
    import fastmcp.server.dependencies as dependencies

    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    progress = []

    class Context:
        async def report_progress(self, value, **kwargs):
            progress.append((value, kwargs["message"]))

    monkeypatch.setattr(dependencies, "get_context", Context)
    monkeypatch.setattr(us, "_CONVERSE_PROGRESS_INTERVAL_S", 0.01)
    def answer(*args, **kwargs):
        time.sleep(0.04)
        return "ok"

    monkeypatch.setattr(ui, "converse", answer)
    result = asyncio.run(us._mcp_converse.fn(message="hello", graph_id="u-x"))
    assert result.structured_content["reply"] == "ok"
    assert len(progress) >= 2
    assert progress == [(index, "Turn is running")
                        for index in range(1, len(progress) + 1)]


def test_full_turn_executor_records_busy_without_starving_default_pool(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    release = threading.Event()
    entered = threading.Barrier(us._CONVERSE_WORKERS + 1)

    def answer(*args, **kwargs):
        entered.wait(timeout=10)
        assert release.wait(10)
        return "done"

    monkeypatch.setattr(ui, "converse", answer)

    async def call():
        tasks = [asyncio.create_task(us._mcp_converse.fn(
            message=f"turn {i}", graph_id="u-x"))
            for i in range(us._CONVERSE_WORKERS)]
        try:
            await asyncio.to_thread(entered.wait, 10)
            assert await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 1) == "free"
            busy = await asyncio.wait_for(us._mcp_converse.fn(
                message="busy turn", graph_id="u-x"), 5)
            assert busy.structured_content["turn_failure"]["code"] == "platform_busy"
            rows = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")
            assert any(row.speaker == "platform" and "busy" in row.text for row in rows)
        finally:
            release.set()
            await asyncio.gather(*tasks)

    asyncio.run(call())


def test_cancelled_worker_records_failure_terminal(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(ui, "converse", lambda *a, **kw: (_ for _ in ()).throw(
        asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        us.converse(message="cancelled", graph_id="u-x")
    rows = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")
    assert [row.speaker for row in rows] == ["founder", "platform"]


def test_existing_reply_terminal_counts_as_recorded(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(ui, "converse", lambda *a, **kw: "answer")
    original = store.record_exchange_turns

    def existing(*args, **kwargs):
        assert original(*args, **kwargs) is not None
        return None

    monkeypatch.setattr(store, "record_exchange_turns", existing)
    response = json.loads(us.converse(message="hello", graph_id="u-x"))
    assert response["reply"] == "answer"
    rows = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")
    assert [row.speaker for row in rows] == ["founder", "universe"]


def test_cancelled_reply_terminal_records_failure(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(ui, "converse", lambda *a, **kw: "answer")
    monkeypatch.setattr(us, "_settle_steering", lambda *a: (_ for _ in ()).throw(
        asyncio.CancelledError()))
    with pytest.raises(asyncio.CancelledError):
        us.converse(message="hello", graph_id="u-x")
    rows = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")
    assert [row.speaker for row in rows] == ["founder", "platform"]


def test_existing_failure_terminal_counts_as_recorded(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(ui, "converse", lambda *a, **kw: "answer")
    original = store.record_failure

    def existing(*args, **kwargs):
        assert original(*args, **kwargs)
        return False

    monkeypatch.setattr(store, "record_failure", existing)
    monkeypatch.setattr(us, "_settle_steering", lambda *a: (_ for _ in ()).throw(
        RuntimeError("settlement broke")))
    response = json.loads(us.converse(message="hello", graph_id="u-x"))
    assert response["history_saved"] is True
    rows = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")
    assert [row.speaker for row in rows] == ["founder", "platform"]


def test_coordinator_fault_after_reply_records_failure(tmp_path, monkeypatch):
    import tinyassets.universe_intelligence as ui
    import tinyassets.universe_server as us

    _founder_auth(monkeypatch, base=tmp_path)
    monkeypatch.setattr(ui, "converse", lambda *a, **kw: "answer")
    monkeypatch.setattr(us, "_settle_steering", lambda *a: (_ for _ in ()).throw(
        RuntimeError("settlement broke")))
    response = json.loads(us.converse(message="hello", graph_id="u-x"))
    rows = store.load_recent_readonly(tmp_path / "u-x", "principal:founder-1")
    assert response["history_saved"] is True
    assert [row.speaker for row in rows] == ["founder", "platform"]


