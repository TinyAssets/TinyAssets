"""Real hosted flow store, callback binding and authenticated exchange boundary."""

import asyncio
import json
import subprocess
import time
from pathlib import Path
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
from tinyassets.frontend import Frontend
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


@pytest.mark.parametrize("keep_binding", [True, False])
def test_hosted_roundtrip_through_worker_frontend_and_owner(monkeypatch, keep_binding):
    flow = start()
    worker_file = Path(__file__).resolve().parents[1] / "deploy/cloudflare-worker/worker.js"
    worker_url = worker_file.as_uri()
    calls = []

    async def exchange(**kwargs):
        calls.append(kwargs)
        return "synthetic-private-key"

    monkeypatch.setattr(hosted, "exchange_key", exchange)
    # Execute the actual Worker against the actual frontend/owner response.
    # No public service is contacted; only the external provider exchange is stubbed.
    program = """
import {proxyToTunnel} from WORKER;
let input = '';
for await (const chunk of process.stdin) input += chunk;
const data = JSON.parse(input);
globalThis.fetch = async () => new Response(null, {status:data.status, headers:data.headers});
const response = await proxyToTunnel(new Request(data.url));
const headers = [...response.headers].filter(([k]) => k !== 'set-cookie');
for (const cookie of response.headers.getSetCookie()) headers.push(['set-cookie', cookie]);
console.log(JSON.stringify(headers));
""".replace("WORKER", json.dumps(worker_url))

    async def run():
        owner_transport = httpx.ASGITransport(app=Starlette(routes=onboarding.onboarding_routes()))

        async def owner(request):
            response = await owner_transport.handle_async_request(request)
            return httpx.Response(response.status_code, headers=response.headers,
                                  stream=httpx.ByteStream(await response.aread()))

        async with httpx.AsyncClient(transport=httpx.MockTransport(owner)) as upstream:
            frontend = httpx.ASGITransport(app=Frontend(upstream, "test", "blue"))

            async def edge(request):
                response = await frontend.handle_async_request(request)
                result = subprocess.run(
                    ["node", "--input-type=module", "-e", program],
                    input=json.dumps({"url": str(request.url), "status": response.status_code,
                                      "headers": response.headers.multi_items()}),
                    text=True, capture_output=True, check=True, timeout=30,
                )
                return httpx.Response(response.status_code, headers=json.loads(result.stdout),
                                      content=await response.aread())

            async with httpx.AsyncClient(transport=httpx.MockTransport(edge),
                                         base_url="https://tinyassets.io") as browser:
                browser.cookies.set(owner_sessions.COOKIE, "owner-cookie")
                launched = await browser.get(
                    flow["launch_path"], headers={"sec-fetch-dest": "document"})
                assert launched.status_code == 307
                assert "code_challenge=" in launched.headers["location"]
                binding = "__Host-ta-model-" + flow["flow"]
                assert binding in browser.cookies
                browser.cookies.delete(owner_sessions.COOKIE)
                if not keep_binding:
                    browser.cookies.clear()
                returned = await browser.get(
                    urlsplit(flow["launch_path"]).path + "?code=synthetic-code")
                assert returned.status_code == (200 if keep_binding else 409)
                assert binding not in browser.cookies

    asyncio.run(run())
    reply = post("inline_poll", {"flow": flow["flow"]})
    assert reply.json()["status"] == ("confirmation_required" if keep_binding else "waiting")
    assert len(calls) == int(keep_binding)
    assert post("inline_poll", {"flow": flow["flow"]}).json() == {
        "status": "consumed" if keep_binding else "waiting"
    }
    assert len(calls) == int(keep_binding)


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
