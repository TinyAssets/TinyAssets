"""Actual owner daemon processes with the slice 4 runtime/XFS doubles."""
from __future__ import annotations

import asyncio
import threading

import httpx

from tests.test_broker_server import broker  # noqa: F401
from tests.test_owner_box_roll import image
from tests.test_owner_router import admitted, boxes, cloud_runtime, router  # noqa: F401
from tinyassets.boxes.images import Images


def test_roll_drains_real_request_and_restarts_only_its_owner(router, boxes, admitted, tmp_path):  # noqa: F811
    manager = boxes.manager
    root = tmp_path / "harnesses"
    old, new = image(root, "1"), image(root, "2")
    manager.images = Images(root, tmp_path / "host-images.json", old)
    for box in manager.boxes.values():
        box.image = old.name
        manager.images.ran(box.uid, old.name)
    uid = admitted.owner_machine("alice")
    other_uid = admitted.owner_machine("bob")
    original, other = boxes.runtime.processes[uid], boxes.runtime.processes[other_uid]
    manager.roll_wait = 10
    manager.roll(new.name)
    results = []

    async def exercise():
        # This HTTP stream holds the actual Activity gate in another process;
        # disconnect releases it, just as a completing turn does.
        messages = []
        incoming = asyncio.Queue()
        await incoming.put({"type": "http.request", "body": b"", "more_body": False})

        async def send(message):
            messages.append(message)
            if message["type"] == "http.response.body" and message.get("body"):
                assert manager._rpc(manager.boxes[uid])["active"] > 0
                thread.start()
                # A roll waiting for Alice cannot stop Bob or block his reads.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(router),
                                             base_url="http://test") as client:
                    response = await client.post("/app/api/file", headers={
                        "authorization": "Bearer bob"}, json={"graph_id": "cc-bob",
                                                                 "path": "hello.txt"})
                    assert response.status_code == 200
                assert original.poll() is None and not results
                await incoming.put({"type": "http.disconnect"})

        thread = threading.Thread(target=lambda: results.append(manager.roll(new.name, uid)))
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": "/app/api/test-stream",
                 "raw_path": b"/app/api/test-stream", "query_string": b"", "root_path": "",
                 "headers": [(b"authorization", b"Bearer alice")],
                 "server": ("test", 80), "client": ("127.0.0.1", 1)}
        await asyncio.wait_for(router(scope, incoming.get, send), 10)
        await asyncio.to_thread(thread.join, 30)
        assert not thread.is_alive()

    asyncio.run(exercise())
    assert results == [{"uid": uid, "outcome": "rolled"}]
    assert original.poll() == 0 and boxes.runtime.processes[uid].pid != original.pid
    assert boxes.runtime.processes[other_uid] is other and other.poll() is None
    assert manager.images.last[str(uid)] == new.name
    assert manager.images.last[str(other_uid)] == old.name
