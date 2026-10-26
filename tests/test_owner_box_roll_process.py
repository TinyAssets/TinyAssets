"""Actual owner daemon processes with the slice 4 runtime/XFS doubles."""
from __future__ import annotations

import asyncio
import threading

import httpx
import pytest

from tests.support.admission import drive
from tests.test_broker_server import broker  # noqa: F401
from tests.test_owner_box_roll import image
from tests.test_owner_router import admitted, boxes, cloud_runtime, router  # noqa: F401
from tinyassets.boxes.images import Images


def test_deploy_drains_real_request_then_router_wakes_published_image(
    router, boxes, admitted, tmp_path, broker,  # noqa: F811
):
    manager = boxes.manager
    root = tmp_path / "harnesses"
    old, new = image(root, "1"), image(root, "2")
    manager.images = Images(root, tmp_path / "host-images.json", old)
    for box in manager.boxes.values():
        box.image = old.name
    uid = admitted.owner_machine("alice")
    other_uid = admitted.owner_machine("bob")
    original, other = boxes.runtime.processes[uid], boxes.runtime.processes[other_uid]
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
                await asyncio.to_thread(thread.join, 10)
                assert not thread.is_alive()
                # The busy owner is never drained: it keeps serving new requests.
                async with httpx.AsyncClient(transport=httpx.ASGITransport(router),
                                             base_url="http://test") as client:
                    response = await client.post("/app/api/file", headers={
                        "authorization": "Bearer alice"}, json={"graph_id": "cc-alice",
                                                                 "path": "hello.txt"})
                    assert response.status_code == 200
                assert original.poll() is None
                await incoming.put({"type": "http.disconnect"})

        thread = threading.Thread(
            target=lambda: results.append(drive(broker.server.routing.deploy(new.name))))
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "GET", "scheme": "http", "path": "/app/api/test-stream",
                 "raw_path": b"/app/api/test-stream", "query_string": b"", "root_path": "",
                 "headers": [(b"authorization", b"Bearer alice")],
                 "server": ("test", 80), "client": ("127.0.0.1", 1)}
        await asyncio.wait_for(router(scope, incoming.get, send), 10)
        await asyncio.to_thread(thread.join, 30)
        assert not thread.is_alive()

    asyncio.run(exercise())
    assert results == [{"slept": 1, "asleep": 0, "busy": 1, "unresponsive": 0,
                         "incomplete": 0,
        "incomplete_status": 0, "incomplete_sleep": 0}]
    # Alice was left serving; her own ordinary sleep comes later. Bob slept.
    drive(broker.server.routing.sleep("alice"))
    assert original.poll() == 0 and other.poll() == 0
    assert not manager.boxes[uid].awake and not manager.boxes[other_uid].awake
    assert boxes.runtime.processes[uid] is original

    async def wake_through_router():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(router),
                                     base_url="http://test") as client:
            response = await client.post("/app/api/file", headers={
                "authorization": "Bearer alice"}, json={"graph_id": "cc-alice",
                                                            "path": "hello.txt"})
            assert response.status_code == 200
    asyncio.run(wake_through_router())
    assert boxes.runtime.processes[uid].pid != original.pid
    assert manager.boxes[uid].image == new.name
    assert not manager.boxes[other_uid].awake


pytestmark = pytest.mark.verifies('owner-daemon-in-box:C12')
