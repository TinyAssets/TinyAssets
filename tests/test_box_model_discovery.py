"""Address discovery is owner work; the shared broker has no discovery RPC."""

import pytest


def test_discovery_is_box_work_not_broker_work():
    from tinyassets.broker.model_connect import validate
    from tinyassets.owner_router import forwarded_path

    assert forwarded_path("/app/model-discovery")
    with pytest.raises(ValueError):
        validate(
            {
                "action": "model_discover",
                "address": "https://provider.example",
                "callback": "https://app.example/callback",
            }
        )


def test_probe_uses_only_owner_proxy(monkeypatch):
    from types import SimpleNamespace

    import httpx

    from tinyassets import owner_runtime
    from tinyassets.connection_oauth.model_discovery import transport_fetch
    from tinyassets.owner_public_egress import JAIL_PROXY_URL

    monkeypatch.setattr(
        owner_runtime,
        "admitted",
        lambda: SimpleNamespace(tools=SimpleNamespace(_public_forwarder=None)),
    )
    calls = []

    class Client:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def stream(self, method, url):
            from contextlib import contextmanager

            @contextmanager
            def response():
                yield httpx.Response(200, json={"data": []})

            return response()

    monkeypatch.setattr(httpx, "Client", Client)
    assert transport_fetch("https://provider.example/models")[:2] == (200, {"data": []})
    assert calls[0]["proxy"] == JAIL_PROXY_URL
    assert calls[0]["trust_env"] is False
    assert calls[0]["follow_redirects"] is False


def test_owner_discovery_route_returns_source_without_broker_discovery(tmp_path, monkeypatch):
    import asyncio
    import json
    import time
    from types import SimpleNamespace

    import httpx
    from starlette.applications import Starlette
    from starlette.routing import Route

    from tests.test_model_source_detector import API, CATALOGUE, fetcher
    from tinyassets import onboarding, owner_runtime
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.broker import model_connect
    from tinyassets.connection_oauth import model_discovery
    from tinyassets.onboarding.model_discovery import handle_model_discovery

    runtime = SimpleNamespace(root=tmp_path, principal="owner")
    monkeypatch.setattr(owner_runtime, "current", lambda: runtime)
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://app.example/mcp"})
    monkeypatch.setattr(model_connect, "request", lambda *a, **kw: pytest.fail("broker discovery"))
    original = model_discovery.discover
    monkeypatch.setattr(
        model_discovery,
        "discover",
        lambda address: original(address, fetch=fetcher({API + "/v1/models": (200, CATALOGUE)})),
    )
    app = Starlette(
        routes=[Route("/app/model-discovery", handle_model_discovery, methods=["POST"])]
    )

    async def run():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://app.example"
        ) as client:
            return await client.post(
                "/app/model-discovery",
                json={"address": API},
                headers={"Origin": "https://app.example"},
            )

    with identity_context(Identity(user_id="owner", username="owner", capabilities=["write"])):
        assert asyncio.run(run()).status_code == 403
    proof = {"identity_json": json.dumps({"user_id": "owner"}), "expires_at": time.time() + 60}
    with identity_context(
        Identity(
            user_id="owner",
            username="owner",
            capabilities=["write"],
            metadata={"router_owner_session": proof},
        )
    ):
        response = asyncio.run(run())
    assert response.status_code == 200, response.text
    assert response.json()["source"]["origin"] == "detected"
