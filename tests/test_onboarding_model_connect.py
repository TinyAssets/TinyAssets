"""Real authenticated bootstrap ingress; hosted exchange uses synthetic keys."""

import asyncio

import httpx
import pytest
from starlette.applications import Starlette

from tests.support.model_connect import rig  # noqa: F401 - shared real-store fixture
from tests.test_hosted_model_auth import CHALLENGE
from tinyassets import onboarding

pytestmark = pytest.mark.usefixtures("rig")


@pytest.fixture(autouse=True)
def ingress(monkeypatch, rig):  # noqa: F811 - shared fixture
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(rig, principal="owner", center="u-owner")
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    monkeypatch.setattr(onboarding, "_read_home", lambda identity, **kw: "u-owner")


def post(operation, data, *, origin="https://tinyassets.io", cookie=""):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())), base_url="https://tinyassets.io",
        ) as client:
            return await client.post("/app/model-connect/" + operation,
                                     json=data, headers={"Origin": origin, "Cookie": cookie})
    return asyncio.run(run())


def begin():
    return post("begin", {"preset_id": "openrouter_user_models_v1", "code_challenge": CHALLENGE})
