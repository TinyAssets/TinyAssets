"""Real hosted flow store, callback binding and authenticated exchange boundary."""

import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import httpx
import pytest
from starlette.applications import Starlette

from tests.test_model_bootstrap import rig  # noqa: F401
from tests.test_onboarding_model_connect import ingress, post  # noqa: F401
from tinyassets import onboarding
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import hosted_model_auth as hosted
from tinyassets.onboarding import inline_model_connect as inline
from tinyassets.onboarding import owner_sessions

pytestmark = pytest.mark.usefixtures("rig", "ingress")
PRESET = "openrouter_user_models_v1"


@pytest.fixture(autouse=True)
def protected_browser(request):
    request.getfixturevalue("rig")
    with owner_sessions.store() as conn:
        conn.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                     (owner_sessions.hashed("owner-cookie"), json.dumps({"user_id": "owner"}),
                      time.time() + 600))
        conn.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                     (owner_sessions.hashed("stranger-cookie"),
                      json.dumps({"user_id": "stranger"}), time.time() + 600))


def start():
    result = post("inline_begin", {"preset_id": PRESET},
                  cookie="__Host-ta-owner=owner-cookie")
    assert result.status_code == 200, result.text
    assert "verifier" not in result.text
    return result.json()


def callback(flow, query="code=synthetic-code", *, cookie=True):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())),
            base_url="https://tinyassets.io", follow_redirects=False,
        ) as client:
            client.cookies.set(owner_sessions.COOKIE, "owner-cookie")
            launch = await client.get(flow["launch_path"], headers={"sec-fetch-dest": "document"})
            assert launch.status_code == 307, launch.text
            assert "code_challenge=" in launch.headers["location"]
            assert "HttpOnly" in launch.headers["set-cookie"]
            if not cookie:
                client.cookies.clear()
            else:
                # The provider return retains its per-flow browser binding,
                # but does not require the owner login cookie a second time.
                client.cookies.delete(owner_sessions.COOKIE)
            path = urlsplit(flow["launch_path"]).path
            return await client.get(path + "?" + query)
    return asyncio.run(run())


def test_popup_callback_and_once_only_server_held_exchange(monkeypatch):
    flow = start()
    assert post("inline_poll", {"flow": flow["flow"]}).json() == {"status": "waiting"}
    calls = []

    async def exchange(**kw):
        calls.append(kw)
        return "synthetic-private-key"

    monkeypatch.setattr(hosted, "exchange_key", exchange)
    assert callback(flow).status_code == 200
    reply = post("inline_poll", {"flow": flow["flow"]})
    assert reply.status_code == 200, reply.text
    assert reply.json()["status"] == "confirmation_required"
    assert reply.json()["answer"]["status"] == "answered"
    assert "owner_session" not in reply.text
    assert "synthetic-private-key" not in reply.text
    assert "verifier" not in reply.text
    assert len(calls) == 1
    assert post("inline_poll", {"flow": flow["flow"]}).json() == {"status": "consumed"}
    assert len(calls) == 1


@pytest.mark.parametrize("operation", ["inline_poll", "inline_cancel"])
def test_foreign_user_cannot_see_consume_or_cancel_flow(operation, monkeypatch):
    flow = start()
    assert callback(flow).status_code == 200

    async def forbidden(**kw):
        raise AssertionError("no foreign exchange")

    monkeypatch.setattr(hosted, "exchange_key", forbidden)
    stranger = Identity(user_id="stranger", username="stranger", capabilities=["write"])
    with identity_context(stranger):
        assert post(operation, {"flow": flow["flow"]}).status_code == 409
    # Also exercise storage isolation even if the caller has its own valid home.
    with pytest.raises(hosted.HostedAuthError):
        inline.take(owner="stranger", home="u-stranger", handle=flow["flow"])
    with inline.flows() as conn:
        assert conn.execute("SELECT status FROM inline_model_flows").fetchone()[0] == "ready"


@pytest.mark.parametrize("kind", ["provider", "app", "missing_cookie"])
def test_cancel_or_wrong_callback_browser_never_exchanges(kind, monkeypatch):
    flow = start()

    async def forbidden(**kw):
        raise AssertionError("must not exchange")

    monkeypatch.setattr(hosted, "exchange_key", forbidden)
    if kind == "app":
        assert post("inline_cancel", {"flow": flow["flow"]}).json()["status"] == "cancelled"
    else:
        response = callback(flow, "error=access_denied" if kind == "provider" else "code=stolen",
                            cookie=kind != "missing_cookie")
        assert response.status_code == (409 if kind == "missing_cookie" else 200)
    result = post("inline_poll", {"flow": flow["flow"]})
    assert result.json()["status"] == ("waiting" if kind == "missing_cookie" else "cancelled")


def test_inline_begin_requires_same_origin():
    response = post("inline_begin", {"preset_id": PRESET}, origin="https://evil.invalid")
    assert response.status_code == 403


@pytest.mark.parametrize("cookie", ["", "stranger-cookie"])
def test_copied_launch_link_cannot_acquire_recipient_credentials(cookie):
    flow = start()

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(
            app=Starlette(routes=onboarding.onboarding_routes())),
            base_url="https://tinyassets.io",
        ) as client:
            if cookie:
                client.cookies.set(owner_sessions.COOKIE, cookie)
            return await client.get(flow["launch_path"], headers={"sec-fetch-dest": "document"})

    response = asyncio.run(run())
    assert response.headers["location"] == "/app/owner-sign-in"
    assert "openrouter" not in response.text
    req = SimpleNamespace(cookies={inline.RETURN_COOKIE: flow["flow"]})
    assert inline.return_path(req, "stranger") == "/app"
    assert inline.return_path(req, "owner") == flow["launch_path"]
    with inline.flows() as conn:
        assert conn.execute("SELECT browser_hash FROM inline_model_flows").fetchone()[0] == ""


def test_cancelling_releases_provider_flow_limit():
    for _ in range(hosted.MAX_PER_OWNER + 1):
        flow = start()
        assert post("inline_cancel", {"flow": flow["flow"]}).status_code == 200


def test_failed_exchange_is_not_replayed(monkeypatch):
    flow = start()
    callback(flow)
    calls = []

    async def unavailable(**kwargs):
        calls.append(kwargs)
        raise hosted.HostedAuthError("model_authorization_not_completed", 503)

    monkeypatch.setattr(hosted, "exchange_key", unavailable)
    assert post("inline_poll", {"flow": flow["flow"]}).status_code == 503
    assert post("inline_poll", {"flow": flow["flow"]}).json() == {"status": "consumed"}
    assert len(calls) == 1


@pytest.mark.parametrize("cookie", ["", "__Host-ta-owner=forged",
                                  "__Host-ta-owner=stranger-cookie"])
def test_inline_start_without_owner_proof_creates_no_flow(cookie):
    result = post("inline_begin", {"preset_id": PRESET}, cookie=cookie)
    assert result.status_code == 403
    assert result.json()["error"] == "interactive_approval_required"
    with inline.flows() as conn:
        assert conn.execute("SELECT COUNT(*) FROM inline_model_flows").fetchone()[0] == 0


def test_legacy_inline_flow_without_bound_proof_cannot_complete():
    flow = start()
    with inline.flows() as conn:
        row = conn.execute("SELECT sealed FROM inline_model_flows").fetchone()
        data = inline.unseal(flow["flow"], row["sealed"])
        data.pop("owner_session")
        conn.execute("UPDATE inline_model_flows SET sealed=?",
                     (inline.seal(flow["flow"], data),))
    assert callback(flow).status_code == 409
    assert post("inline_poll", {"flow": flow["flow"]}).json() == {"status": "waiting"}
