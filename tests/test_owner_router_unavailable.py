"""Owner transport failures have one retryable public response."""
import asyncio
from pathlib import Path

import httpx
import pytest

from tinyassets.auth.provider import Identity
from tinyassets.owner_router import BoxRoute, forward


@pytest.mark.parametrize("path", ["/app/api/read", "/app/api/status"])
@pytest.mark.parametrize("outcome", [200, 404, 500, 502, 503, 504, "refused", "timeout", "asleep"])
def test_unavailable(path, outcome, monkeypatch):
    from tinyassets.broker import owner_identities

    def upstream(request):
        if outcome in {"refused", "asleep"}:
            raise httpx.ConnectError("socket refused")
        if outcome == "timeout":
            raise httpx.ReadTimeout("box failed")
        return httpx.Response(outcome, headers={"Retry-After": "37", "X-Box": "answer"}, stream=httpx.ByteStream(b'{"error":"owner_read_failed"}'))

    def wake(*args, **kwargs):
        raise RuntimeError("cannot wake")

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", lambda **kw: httpx.MockTransport(upstream))
    monkeypatch.setattr(owner_identities, "wake_owner", wake)

    async def proxy(scope, receive, send):
        await forward(scope, receive, send, Identity(user_id="alice", username="alice"),
                      BoxRoute(Path("absent.sock"), "secret"), cold=outcome == "asleep")

    async def exercise():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(proxy),
                                     base_url="http://router") as client:
            response = await client.post(path, json={})
            if isinstance(outcome, int):
                assert response.status_code == outcome
                assert response.json() == {"error": "owner_read_failed"}
                assert response.headers["Retry-After"] == "37"
                assert response.headers["X-Box"] == "answer"
                return
            assert response.status_code == 503
            assert response.json() == {"error": "owner_box_unavailable"}
            assert int(response.headers["Retry-After"]) > 0

    asyncio.run(exercise())
