"""An admitted converse turn outlives its caller and gets one terminal."""

import asyncio
import json
import threading
import time

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


def test_codex_silent_turn_ends_at_protocol_idle_watchdog():
    from types import SimpleNamespace

    import pytest

    from tinyassets.exceptions import ProviderIdleTimeoutError
    from tinyassets.external_agents.jsonrpc_job import JsonRpcJob
    from tinyassets.external_agents.registry import resolve
    from tinyassets.providers.base import ModelConfig

    async def never_line():
        await asyncio.Event().wait()

    turn = JsonRpcJob(
        SimpleNamespace(stdout=SimpleNamespace(readline=never_line), returncode=None),
        job=resolve("codex").row["job"], agent="codex", tools=None,
        profile=ModelConfig(idle_timeout_s=0.02).stream_timeout_profile(),
        start=time.monotonic(), turn_wait=900, tool_wait=900,
    )
    turn._in_turn = True

    async def run():
        with pytest.raises(ProviderIdleTimeoutError):
            await asyncio.wait_for(turn.read(), timeout=1)

    asyncio.run(run())
