"""Real authenticated bootstrap ingress; hosted exchange uses synthetic keys."""

import asyncio

import httpx
import pytest
from starlette.applications import Starlette

from tests.support.model_connect import rig  # noqa: F401 - shared real-store fixture
from tests.test_hosted_model_auth import CHALLENGE, VERIFIER, pending
from tinyassets import onboarding
from tinyassets.broker import acquisition
from tinyassets.onboarding import hosted_model_auth as hosted

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


def test_begin_exchange_and_resume_real_store_composition(monkeypatch):
    first = begin()
    assert first.status_code == 200, first.text
    assert first.headers["cache-control"] == "no-store"
    calls = []
    async def exchange(**kwargs):
        calls.append(kwargs)
        return "synthetic-oauth-key"
    monkeypatch.setattr(acquisition, "exchange_key", exchange)
    payload = {"flow": first.json()["flow"], "code": "synthetic-code", "code_verifier": VERIFIER}
    result = post("exchange", payload)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "provisioned"
    assert "synthetic-oauth-key" not in result.text
    assert len(calls) == 1
    replay = post("exchange", payload)
    assert replay.status_code == 404
    assert len(calls) == 1
    resume = post("resume", {"preset_id": "openrouter_user_models_v1"})
    assert resume.status_code == 200, resume.text
    assert resume.json()["grant_id"] == result.json()["grant_id"]


@pytest.mark.parametrize("origin", ["", "http://tinyassets.io", "https://elsewhere.invalid",
    "https://tinyassets.io/path", "https://tinyassets.io?query=1"])
def test_begin_requires_exact_canonical_origin_before_state_changes(origin):
    response = post("begin", {"preset_id": "openrouter_user_models_v1",
                              "code_challenge": CHALLENGE}, origin=origin)
    assert response.status_code == 403
    assert not pending()


def test_foreign_home_cannot_redeem_or_start(monkeypatch):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    first = begin()
    async def forbidden(**kwargs):
        raise AssertionError("must not contact provider")
    monkeypatch.setattr(acquisition, "exchange_key", forbidden)
    stranger = Identity(user_id="stranger", username="stranger", capabilities=["write"])
    with identity_context(stranger):
        result = post("exchange", {"flow": first.json()["flow"], "code": "code",
                                   "code_verifier": VERIFIER})
        assert result.status_code == 404
        assert begin().status_code == 404
    assert pending(first.json()["flow"])


def test_only_callback_shell_is_exempt_from_bearer_challenge():
    from tinyassets.auth.middleware import _auth_challenge_path

    assert not _auth_challenge_path(hosted.CALLBACK_PREFIX + "a" * 43)
    assert _auth_challenge_path(hosted.CALLBACK_PREFIX + "a" * 42)
    assert _auth_challenge_path(hosted.CALLBACK_PREFIX + "a" * 43 + "/exchange")
    assert _auth_challenge_path("/app/model-connect/begin")
    assert _auth_challenge_path("/app/model-connect/exchange")


def test_bad_challenge_does_not_bootstrap_a_home(monkeypatch):
    monkeypatch.setattr(onboarding, "_read_home", lambda *a, **kw: "")
    def forbidden(*args):
        raise AssertionError("invalid begin must not provision home")
    monkeypatch.setattr(onboarding, "_bootstrap_home", forbidden)
    result = post("begin", {"preset_id": "openrouter_user_models_v1", "code_challenge": "bad"})
    assert result.status_code == 400
    assert result.json()["error"] == "invalid_pkce_challenge"


def test_preset_change_during_exchange_cannot_deposit(monkeypatch):
    from dataclasses import replace

    first = begin()
    preset = hosted.load_preset("openrouter_user_models_v1")
    async def exchange(**kwargs):
        monkeypatch.setattr(hosted, "load_preset", lambda _: replace(preset, digest="changed"))
        return "synthetic-key"
    monkeypatch.setattr(acquisition, "exchange_key", exchange)
    response = post("exchange", {"flow": first.json()["flow"], "code": "synthetic-code",
                                 "code_verifier": VERIFIER})
    assert response.status_code == 502
    assert response.json()["error"] == "model_connection_preset_changed"


def test_new_owner_without_admission_cannot_create_a_home(request):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity

    base = request.getfixturevalue("rig")
    before = set(base.iterdir())
    new_owner = Identity(user_id="brand-new-owner", username="new-owner", capabilities=["write"])
    with identity_context(new_owner):
        assert begin().status_code == 404
    assert set(base.iterdir()) == before


def test_anonymous_cannot_start_or_resume_or_exchange():
    from tinyassets.auth.middleware import identity_context

    with identity_context(None):
        for operation, data in [
            ("begin", {"preset_id": "openrouter_user_models_v1", "code_challenge": CHALLENGE}),
            ("resume", {"preset_id": "openrouter_user_models_v1"}),
            ("exchange", {"flow": "f" * 43, "code": "code", "code_verifier": VERIFIER}),
        ]:
            assert post(operation, data).status_code == 401
    assert not pending()


def test_callback_is_nonmutating_shell_with_private_headers(monkeypatch):
    from starlette.responses import HTMLResponse

    async def shell(request):
        return HTMLResponse("<html>synthetic app shell</html>")
    monkeypatch.setattr(onboarding, "_handle_app", shell)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())), base_url="https://tinyassets.io",
        ) as client:
            return await client.get(hosted.CALLBACK_PREFIX + "f" * 43 + "?code=synthetic-code")
    response = asyncio.run(run())
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.headers["referrer-policy"] == "no-referrer"
    assert "synthetic-code" not in response.text
    assert not pending()


def test_retirement_between_ingress_and_durable_begin_cannot_create_flow(monkeypatch):
    from tinyassets.broker.owner_identities import OwnerIdentities
    from tinyassets.storage import data_dir

    original = hosted.begin_flow

    def delayed_begin(**kwargs):
        identities = OwnerIdentities(data_dir() / ".broker/owner-identities.db")
        identities.admission("retire", kwargs["owner"], kwargs["universe_id"])
        return original(**kwargs)

    monkeypatch.setattr(hosted, "begin_flow", delayed_begin)
    response = begin()
    assert response.status_code == 404
    assert not (data_dir() / ".hosted-model-auth.db").exists()
