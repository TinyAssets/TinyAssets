"""Concurrent endpoints retain owner pins without subprocess or environment state."""
import asyncio
import os
import sys

from tinyassets.engine_endpoint import EngineEndpoint, current_server, grant_key


def test_concurrent_endpoint_identity_and_key_are_request_local():
    before = dict(os.environ)
    endpoints = [EngineEndpoint("alice", "a", "a-secret", "a-key"),
                 EngineEndpoint("bob", "b", "b-secret", "b-key")]
    seen = []

    async def run():
        ready = asyncio.Event()

        async def app(scope, receive, send):
            server = current_server()
            seen.append((server._ACTOR_ID, server._GRAPH_ID, grant_key()))
            if len(seen) == 2:
                ready.set()
            await ready.wait()
            await asyncio.sleep(0)
            assert current_server() is server
            assert grant_key() == server._GRAPH_ID + "-key"

        for endpoint in endpoints:
            endpoint.app = app
        await asyncio.gather(*(
            endpoint({"type": "http", "headers": [
                (b"authorization", ("Bearer " + endpoint.secret).encode()),
            ]}, None, None) for endpoint in endpoints
        ))

    try:
        asyncio.run(run())
        assert seen == [("alice", "a", "a-key"), ("bob", "b", "b-key")]
        assert endpoints[0].module is not endpoints[1].module
        assert dict(os.environ) == before
    finally:
        for endpoint in endpoints:
            endpoint.close()
    assert all(endpoint.module.__name__ not in sys.modules for endpoint in endpoints)


def test_endpoint_refuses_foreign_bearer_before_dispatch():
    endpoint = EngineEndpoint("alice", "a", "a-secret", "a-key")
    messages = []

    async def forbidden(*args):
        raise AssertionError("foreign bearer reached handlers")

    async def send(message):
        messages.append(message)

    endpoint.app = forbidden
    try:
        asyncio.run(endpoint({"type": "http", "headers": [
            (b"authorization", b"Bearer b-secret"),
        ]}, None, send))
        assert messages[0]["status"] == 401
        assert messages[1]["body"] == b"unauthorized"
    finally:
        endpoint.close()
