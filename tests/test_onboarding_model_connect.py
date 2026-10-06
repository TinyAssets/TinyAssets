"""Real authenticated bootstrap ingress; hosted exchange uses synthetic keys."""

import asyncio

import httpx
import pytest
from starlette.applications import Starlette

from tests.test_hosted_model_auth import CHALLENGE, VERIFIER, pending
from tests.test_model_bootstrap import rig  # noqa: F401 - shared real-store fixture
from tinyassets import onboarding
from tinyassets.onboarding import hosted_model_auth as hosted

pytestmark = pytest.mark.usefixtures("rig")


@pytest.fixture(autouse=True)
def ingress(monkeypatch):
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
    monkeypatch.setattr(hosted, "exchange_key", exchange)
    payload = {"flow": first.json()["flow"], "code": "synthetic-code", "code_verifier": VERIFIER}
    result = post("exchange", payload)
    assert result.status_code == 200, result.text
    assert result.json()["status"] == "confirmation_required"
    assert "synthetic-oauth-key" not in result.text
    assert len(calls) == 1
    replay = post("exchange", payload)
    assert replay.status_code == 409
    assert len(calls) == 1
    resume = post("resume", {"preset_id": "openrouter_user_models_v1"})
    assert resume.status_code == 200, resume.text
    assert resume.json()["request_id"] == result.json()["request_id"]


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
    monkeypatch.setattr(hosted, "exchange_key", forbidden)
    stranger = Identity(user_id="stranger", username="stranger", capabilities=["write"])
    with identity_context(stranger):
        result = post("exchange", {"flow": first.json()["flow"], "code": "code",
                                   "code_verifier": VERIFIER})
        assert result.status_code == 409
        assert begin().status_code == 409
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

    from tinyassets.onboarding import model_bootstrap

    first = begin()
    preset = hosted.load_preset("openrouter_user_models_v1")
    async def exchange(**kwargs):
        monkeypatch.setattr(hosted, "load_preset", lambda _: replace(preset, digest="changed"))
        return "synthetic-key"
    def forbidden(**kwargs):
        raise AssertionError("must not deposit against changed preset")
    monkeypatch.setattr(hosted, "exchange_key", exchange)
    monkeypatch.setattr(model_bootstrap, "complete_bootstrap", forbidden)
    response = post("exchange", {"flow": first.json()["flow"], "code": "synthetic-code",
                                 "code_verifier": VERIFIER})
    assert response.status_code == 409
    assert response.json()["error"] == "model_connection_preset_changed"


def test_new_owner_begin_provisions_own_empty_home_without_llm(request, monkeypatch):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.daemon_server import get_founder_home

    base = request.getfixturevalue("rig")
    monkeypatch.setattr(onboarding, "_read_home",
                        lambda identity, **kw: get_founder_home(base, identity.user_id) or "")
    new_owner = Identity(user_id="brand-new-owner", username="new-owner", capabilities=["write"])
    with identity_context(new_owner):
        assert not get_founder_home(base, new_owner.user_id)
        response = begin()
        assert response.status_code == 200, response.text
        home = get_founder_home(base, new_owner.user_id)
        assert home and home != "u-owner"
        record = pending(response.json()["flow"])
        assert record["owner_user_id"] == new_owner.user_id and record["bound_home_id"] == home


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


def test_deletion_between_ingress_scope_and_durable_begin_cannot_recreate_metadata(monkeypatch):
    from tinyassets.account_deletion import delete_account
    from tinyassets.storage import data_dir

    original = hosted.begin_flow

    def delayed_begin(**kwargs):
        receipt = delete_account(data_dir(), founder_sub=kwargs["owner"],
                                 cancel_billing=lambda _: "cancelled",
                                 delete_identity=lambda _: "deleted")
        assert receipt["unfinished_phases"] == []
        return original(**kwargs)

    monkeypatch.setattr(hosted, "begin_flow", delayed_begin)
    response = begin()
    assert response.status_code == 409
    assert response.json()["error"] == "current_home_changed"
    assert not (data_dir() / ".hosted-model-auth.db").exists()
