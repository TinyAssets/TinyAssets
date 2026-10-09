"""The first web app login supplies interactive proof, never a bearer upgrade."""

import asyncio
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.applications import Starlette

from tests.test_model_bootstrap import rig  # noqa: F401
from tests.test_onboarding_model_connect import ingress, post  # noqa: F401
from tinyassets import onboarding
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import owner_sessions as sessions

CLIENT = httpx.AsyncClient
NAV = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}


@pytest.fixture
def login(rig, ingress, monkeypatch):  # noqa: F811
    cfg = {
        "configured": True, "resource": "https://tinyassets.io/mcp", "client_id": "client",
        "scopes": "openid offline_access", "authorization_endpoint": "https://id.example/auth",
        "token_endpoint": "https://id.example/token",
    }
    monkeypatch.setattr(onboarding, "app_config", lambda: cfg)
    identity = Identity(user_id="owner", username="owner")
    monkeypatch.setattr("tinyassets.auth.middleware._get_provider",
                        lambda: SimpleNamespace(resolve_token=lambda _: identity))
    calls = []
    payload = {"access_token": "test-access", "refresh_token": "test-refresh", "expires_in": 300}

    def exchange(request):
        calls.append(parse_qs(request.content.decode()))
        return httpx.Response(200, json=payload)

    class Upstream(CLIENT):
        def __init__(self, *args, **kwargs):
            kwargs.setdefault("transport", httpx.MockTransport(exchange))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Upstream)
    return calls, payload


def browser():
    return CLIENT(transport=httpx.ASGITransport(app=Starlette(
        routes=onboarding.onboarding_routes())), base_url="https://tinyassets.io")


async def begin(client, app=True):
    response = await client.get("/app/owner-sign-in" + ("?app=1" if app else ""), headers=NAV)
    assert response.status_code == 307
    query = parse_qs(urlsplit(response.headers["location"]).query)
    assert query["code_challenge_method"] == ["S256"]
    return {"state": query["state"][0], "code": "test-code"}


def test_fresh_app_sign_in_renews_and_first_connect_goes_directly_to_provider(login):
    calls, _ = login

    async def run():
        async with browser() as client:
            assert not client.cookies
            params = await begin(client)
            response = await client.get("/app", params=params)
            assert response.headers["location"] == "/app?owner_login=1"
            assert response.headers["cache-control"] == "no-store"
            for cookie in response.headers.get_list("set-cookie"):
                assert "Secure" in cookie and "HttpOnly" in cookie
            cookie = client.cookies.get(sessions.COOKIE)
            assert json.loads(sessions.lookup(cookie)["identity_json"])["user_id"] == "owner"
            assert calls[0]["grant_type"] == ["authorization_code"]
            assert "code_verifier" in calls[0]
            renewed = await client.post("/app/token", json={"grant_type": "refresh_token"},
                                        headers={"origin": "https://tinyassets.io"})
            assert renewed.status_code == 200
            assert renewed.json()["access_token"] == "test-access"
            assert calls[1]["refresh_token"] == ["test-refresh"]
            assert sessions.lookup(cookie)  # Renewal must not revoke owner proof.
            flow = await client.post("/app/model-connect/inline_begin",
                                     json={"preset_id": "openrouter_user_models_v1"},
                                     headers={"origin": "https://tinyassets.io"})
            assert flow.status_code == 200, flow.text
            launch = await client.get(flow.json()["launch_path"], headers=NAV)
            assert urlsplit(launch.headers["location"]).netloc == "openrouter.ai"
            replay = await client.get("/app", params=params)
            assert replay.status_code == 403
            assert len(calls) == 2
    asyncio.run(run())


@pytest.mark.parametrize("kind", ["missing_cookie", "wrong_cookie", "expired", "changed_purpose"])
def test_app_callback_requires_original_browser_and_unexpired_purpose(login, kind):
    calls, _ = login

    async def run():
        async with browser() as client:
            params = await begin(client)
            if kind == "missing_cookie":
                client.cookies.clear()
            elif kind == "wrong_cookie":
                client.cookies.clear()
                client.cookies.set(sessions.FLOW_COOKIE, "other-browser")
            elif kind == "expired":
                with sessions.store() as conn:
                    conn.execute("UPDATE owner_login_flows SET expires_at=0")
            else:
                params["state"] = params["state"].replace("oa_app_", "oa_")
            response = await client.get("/app", params=params)
            assert response.status_code == (303 if kind == "missing_cookie" else 403)
            if kind == "missing_cookie":
                assert response.headers["location"].startswith("/app#owner_completion=")
            assert not client.cookies.get(sessions.COOKIE)
            assert not client.cookies.get(onboarding._REFRESH_COOKIE)
            assert not calls
    asyncio.run(run())


@pytest.mark.parametrize("bad_refresh", [None, "", "x" * 4097])
def test_app_login_fails_without_usable_renewal(login, bad_refresh):
    _, payload = login
    payload["refresh_token"] = bad_refresh

    async def run():
        async with browser() as client:
            response = await client.get("/app", params=await begin(client))
            assert response.status_code == 403
            assert not client.cookies.get(sessions.COOKIE)
    asyncio.run(run())


def test_protected_only_login_does_not_replace_app_identity(login):
    async def run():
        async with browser() as client:
            params = {**await begin(client, app=False), "app": "1"}
            response = await client.get("/app", params=params)
            assert response.headers["location"] == "/app"
            assert client.cookies.get(sessions.COOKIE)
            assert not client.cookies.get(onboarding._REFRESH_COOKIE)
    asyncio.run(run())


@pytest.mark.parametrize("grant", ["authorization_code", "refresh_token"])
def test_client_pkce_or_bearer_renewal_cannot_mint_owner_proof(login, grant):
    async def run():
        async with browser() as client:
            client.cookies.set(onboarding._REFRESH_COOKIE, "old-refresh")
            response = await client.post("/app/token", json={
                "grant_type": grant, "code": "client-code", "code_verifier": "v" * 48,
                "redirect_uri": "https://tinyassets.io/app",
            }, headers={"origin": "https://tinyassets.io", "authorization": "Bearer test-access"})
            assert response.status_code == 200
            assert not client.cookies.get(sessions.COOKIE)
            with sessions.store() as conn:
                assert conn.execute("SELECT count(*) FROM owner_sessions").fetchone()[0] == 0
    asyncio.run(run())


def test_app_account_switch_revokes_previous_protected_session(login, monkeypatch):
    async def run():
        async with browser() as client:
            await client.get("/app", params=await begin(client))
            old = client.cookies.get(sessions.COOKIE)
            monkeypatch.setattr("tinyassets.auth.middleware._get_provider", lambda: SimpleNamespace(
                resolve_token=lambda _: Identity(user_id="other", username="other")))
            await client.get("/app", params=await begin(client))
            assert sessions.lookup(old) is None
            new = client.cookies.get(sessions.COOKIE)
            assert new != old
            assert json.loads(sessions.lookup(new)["identity_json"])["user_id"] == "other"
    asyncio.run(run())


@pytest.mark.parametrize("pending", ["none", "completed", "failed"])
def test_web_login_and_callback_use_protected_navigation_and_cookie_renewal(tmp_path, pending):
    html = Path("tinyassets/onboarding/app.html").read_text(encoding="utf-8")
    start = html.split("  async function beginSignIn(){", 1)[1].split(
        "  // The AuthKit callback", 1)[0]
    finish = html.split("  async function completeSignInIfCallback(){", 1)[1].split(
        "  async function finishExchange", 1)[0]
    script = tmp_path / "login.cjs"
    script.write_text("const pending=" + json.dumps(pending) + ";\n" + """
const assert=require('node:assert/strict');
const NATIVE=false, CFG={configured:true}, TOKEN_KEY='token', EXP_KEY='expiry';
const sessionStorage=new Map([['token','old-account'],['expiry','old-expiry']]);
sessionStorage.removeItem=key=>sessionStorage.delete(key);
const localStorage=new Map(pending==='none'?[]:[['ta_logout_pending','1']]);
localStorage.getItem=key=>localStorage.get(key);
let logoutPending=false, logoutCalls=0;
async function endServerSession(){
  logoutCalls++;
  if(pending==='failed')return false;
  localStorage.delete('ta_logout_pending');
  return true;
}
const notice={textContent:''}, document={getElementById:()=>notice};
let ref='old-account-handle', cookieOnly=false;
function clearSessionRef(){ref='';}
async function refreshAccessToken(value){cookieOnly=value; return true;}
const window={location:{href:'',search:'?owner_login=1'}};
const history={replaceState:()=>{}};
function redirectUri(){return 'https://tinyassets.io/app';}
""" + "async function beginSignIn(){" + start +
        "async function completeSignInIfCallback(){" + finish + """
(async()=>{
  await beginSignIn();
  assert.equal(logoutCalls,pending==='none'?0:1);
  if(pending==='failed'){
    assert.equal(window.location.href,'');
    assert.match(notice.textContent,/Sign-out could not finish/);
    assert.equal(localStorage.getItem('ta_logout_pending'),'1');
    return;
  }
  assert.equal(window.location.href,'/app/owner-sign-in?app=1');
  assert.equal(localStorage.getItem('ta_logout_pending'),undefined);
  assert.equal(await completeSignInIfCallback(),true);
  assert.equal(cookieOnly,true);
  assert.equal(ref,'');
  assert.equal(sessionStorage.has('token'),false);
  // A copied completion marker may not erase a later outstanding logout.
  localStorage.set('ta_logout_pending','1');
  await completeSignInIfCallback();
  assert.equal(localStorage.getItem('ta_logout_pending'),'1');
})().catch(e=>{console.error(e);process.exit(1);});
""", encoding="utf-8")
    result = subprocess.run(["node", str(script)], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
