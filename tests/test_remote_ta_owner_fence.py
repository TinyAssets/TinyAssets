"""Receipt mutations cannot outlive their execution-owner generation."""
import asyncio
import sqlite3
from types import SimpleNamespace

import pytest

from tinyassets import owner_lease
from tinyassets.agent_loop.box_ta import UNKNOWN, TurnBridge
from tinyassets.storage.owner_fence import stored_fences


def test_stale_bridge_cannot_insert_or_dispatch(tmp_path):
    async def run():
        calls = []

        async def dispatch(message):
            calls.append(message)
            return {"ok": True}

        handle = SimpleNamespace(account_id="owner", command_center_id="center", turn_id="turn")
        path = tmp_path / ".remote-ta-receipts.sqlite3"
        bridge = TurnBridge(owner="owner", center="center", turn="turn", handle=handle,
                            database=path, dispatch=dispatch)
        assert owner_lease.release(bridge._lease)
        successor = owner_lease.acquire(tmp_path, owner_lease.key_for("center"))
        assert successor.generation > bridge._lease.generation
        with pytest.raises(owner_lease.LeaseLost):
            await asyncio.to_thread(bridge.request, handle, "exec", "a" * 32, {})
        assert calls == []
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT count(*) FROM ta_receipts").fetchone()[0] == 0
        assert owner_lease.STORE_ENUMERATORS["remote_ta_receipts"](tmp_path) == [path]
        assert stored_fences(path)[successor.owner_key] == successor.generation
        bridge.close()

    asyncio.run(run())


def test_handover_during_dispatch_preserves_unknown_receipt(tmp_path):
    async def run():
        entered, finish = asyncio.Event(), asyncio.Event()
        calls = []

        async def dispatch(message):
            calls.append(message)
            entered.set()
            await finish.wait()
            return {"effect": "completed"}

        handle = SimpleNamespace(account_id="owner", command_center_id="center", turn_id="turn")
        path = tmp_path / ".remote-ta-receipts.sqlite3"
        bridge = TurnBridge(owner="owner", center="center", turn="turn", handle=handle,
                            database=path, dispatch=dispatch)
        pending = asyncio.create_task(asyncio.to_thread(
            bridge.request, handle, "exec", "b" * 32, {}))
        await asyncio.wait_for(entered.wait(), timeout=10)
        assert owner_lease.release(bridge._lease)
        successor = TurnBridge(owner="owner", center="center", turn="turn", handle=handle,
                               database=path, dispatch=dispatch)
        assert successor._lease.generation > bridge._lease.generation
        finish.set()
        assert await pending == UNKNOWN
        with sqlite3.connect(path) as db:
            assert db.execute("SELECT answer FROM ta_receipts").fetchall() == [(None,)]
        assert await asyncio.to_thread(successor.request, handle, "exec", "b" * 32, {}) == UNKNOWN
        assert calls == [{}]
        # The new owner can write fresh intent and its completed answer.
        assert await asyncio.to_thread(successor.request, handle, "exec", "c" * 32, {}) == {
            "effect": "completed"}
        assert calls == [{}, {}]
        bridge.close()
        successor.close()

    asyncio.run(run())
