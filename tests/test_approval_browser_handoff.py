"""Separate WebView/browser jars, real /app routing and real request dismissal."""

import asyncio
import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.applications import Starlette
from starlette.testclient import TestClient

from tests.test_pending_requests import _CRED, _make_universe
from tinyassets import onboarding
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import approval_handoff, owner_sessions
from tinyassets.storage.pending_requests import create_request, get_request

ORIGIN = "https://tinyassets.io"
NAV = {"sec-fetch-mode": "navigate", "sec-fetch-dest": "document"}
JSON = {"origin": ORIGIN, "content-type": "application/json"}


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    identity = Identity(
        user_id="alice", username="alice", capabilities=["tinyassets.universe.write"]
    )
    cfg = {
        "configured": True,
        "resource": ORIGIN + "/mcp",
        "client_id": "app",
        "scopes": "openid offline_access",
        "authorization_endpoint": "https://idp.test/authorize",
        "token_endpoint": "https://idp.test/token",
    }
    monkeypatch.setattr(onboarding, "app_config", lambda **kw: cfg)
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(
        "tinyassets.auth.middleware._get_provider",
        lambda: SimpleNamespace(
            resolve_token=lambda token: identity if token == "issued-access" else None
        ),
    )
    home = _make_universe(tmp_path, "u-1", admin="alice")
    card = create_request(home, **_CRED, dedupe_key="connect")
    app = Starlette(routes=onboarding.onboarding_routes())
    exchanges = []
    authorize = {}

    def idp(request):
        form = parse_qs(request.content.decode())
        assert form["redirect_uri"] == authorize["redirect_uri"] == [ORIGIN + "/app"]
        challenge = (
            base64.urlsafe_b64encode(hashlib.sha256(form["code_verifier"][0].encode()).digest())
            .decode()
            .rstrip("=")
        )
        assert [challenge] == authorize["code_challenge"]
        assert form["resource"] == [ORIGIN + "/mcp"]
        assert form["code"] == ["idp-code"]
        exchanges.append(form)
        return httpx.Response(
            200, json={"access_token": "issued-access", "refresh_token": "renewal"}
        )

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        httpx, "AsyncClient", lambda **kw: real_client(transport=httpx.MockTransport(idp), **kw)
    )
    return SimpleNamespace(
        app=app, identity=identity, home=home, card=card, authorize=authorize, exchanges=exchanges
    )


def login(browser, flow, path="/app/owner-sign-in?app=1"):
    response = browser.get(path, headers=NAV, follow_redirects=False)
    flow.authorize.update(parse_qs(urlsplit(response.headers["location"]).query))
    assert owner_sessions.FLOW_COOKIE in browser.cookies
    callback = browser.get(
        "/app",
        params={"state": flow.authorize["state"][0], "code": "idp-code"},
        follow_redirects=False,
    )
    assert callback.status_code == 303
    assert owner_sessions.COOKIE in browser.cookies
    assert len(flow.exchanges) == 1
    return callback


def test_real_app_roundtrip_then_clear_without_transferring_webview_proof(flow):
    payload = {"universe_id": "u-1", "request_id": flow.card["request_id"], "dismiss": True}
    with (
        identity_context(flow.identity),
        TestClient(flow.app, base_url=ORIGIN) as webview,
        TestClient(flow.app, base_url=ORIGIN) as browser,
    ):
        assert webview.post("/app/approvals/answer", headers=JSON, json=payload).status_code == 403
        started = webview.post(
            "/app/approval-handoff",
            headers=JSON,
            json={"request_id": flow.card["request_id"], "client": "android-debug"},
        )
        path = started.json()["launch_path"]
        launch = browser.get(path, follow_redirects=False)
        assert launch.headers["location"] == "/app/owner-sign-in?app=1"
        callback = login(browser, flow, launch.headers["location"])
        assert callback.headers["location"] == path
        ready = browser.get(path, follow_redirects=False)
        assert ready.headers["location"].startswith("/app?approval=")
        ref = path.rsplit("/", 1)[1]
        details = browser.post("/app/approval-handoff?ref=" + ref, headers=JSON).json()
        assert details["request_id"] == payload["request_id"]
        assert "package=io.tinyassets.app.debug" in details["return_url"]
        assert "completion=" + ref in details["return_url"]
        done = browser.post("/app/approvals/answer", headers=JSON, json=payload)
        assert done.status_code == 200, done.text
        assert done.json()["status"] == "dismissed"
        assert get_request(flow.home, payload["request_id"])["status"] == "dismissed"
        assert owner_sessions.COOKIE not in webview.cookies
        assert (
            browser.get(path, follow_redirects=False)
            .headers["location"]
            .startswith("/app?approval=")
        )
        assert len(flow.exchanges) == 1  # Second open reuses browser proof.


def test_missing_flow_cookie_logs_reason_without_secrets(flow, caplog):
    with TestClient(flow.app, base_url=ORIGIN) as browser:
        response = browser.get("/app/owner-sign-in", headers=NAV, follow_redirects=False)
        state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
        browser.cookies.clear()  # Real browser omits the cookie on the cross-site return.
        browser.cookies.set("diagnostic-cookie", "secret-cookie")
        params = {"state": state, "code": "secret-code"}
        staged = browser.get("/app", params=params, follow_redirects=False)
        assert staged.status_code == 303
        assert staged.headers["location"].startswith("/app#owner_completion=")
        result = browser.get("/app", params=params, headers={
            "sec-fetch-site": "cross-site", "sec-fetch-mode": "navigate",
            "referer": "https://idp.test/callback?code=referer-secret",
        })
        assert result.status_code == 403
        assert "callback_already_received" in caplog.text
        assert state not in caplog.text and "secret-code" not in caplog.text
        record = caplog.records[-1]
        assert record.cookie_names == ["diagnostic-cookie"]
        assert record.sec_fetch_site == "cross-site" and record.sec_fetch_mode == "navigate"
        assert record.referer_origin == "https://idp.test"
        assert 0 <= record.flow_age_seconds < 600
        assert "secret-cookie" not in caplog.text and "referer-secret" not in caplog.text
        assert not flow.exchanges


def test_handoff_wrong_owner_expiry_and_bearer_are_not_proof(flow):
    with identity_context(flow.identity), TestClient(flow.app, base_url=ORIGIN) as browser:
        path = browser.post(
            "/app/approval-handoff",
            headers=JSON,
            json={"request_id": flow.card["request_id"], "client": "ios"},
        ).json()["launch_path"]
        ref = path.rsplit("/", 1)[1]
        assert browser.post("/app/approval-handoff?ref=" + ref, headers=JSON).status_code == 403
        login(browser, flow)
        with approval_handoff.flows() as conn:
            conn.execute("UPDATE approval_handoffs SET owner='bob'")
        assert browser.get(path, follow_redirects=False).status_code == 403
        assert browser.post("/app/approval-handoff?ref=" + ref, headers=JSON).status_code == 409
        with approval_handoff.flows() as conn:
            conn.execute("UPDATE approval_handoffs SET expires=0")
        assert browser.get(path, follow_redirects=False).status_code == 410


def test_frontend_proxies_owner_callback_and_preserves_cookie_headers(monkeypatch):
    from tinyassets.frontend import Frontend

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    seen = []

    async def owner(request):
        seen.append(request)
        return httpx.Response(
            303,
            headers=[
                ("location", "/app?owner_login=1"),
                ("set-cookie", "__Host-ta-owner=proof; Secure; HttpOnly; Path=/; SameSite=lax"),
                ("set-cookie", "__Host-ta-owner-login=; Secure; HttpOnly; Path=/; Max-Age=0"),
            ],
            stream=httpx.ByteStream(b""),
        )

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(owner)) as upstream:
            frontend = Frontend(upstream, "build", "blue")
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=frontend), base_url=ORIGIN
            ) as client:
                response = await client.get(
                    "/app?state=oa_test&code=test", headers={"cookie": "flow=cookie"}
                )
                assert response.status_code == 303
                assert len(response.headers.get_list("set-cookie")) == 2
        assert seen[0].url.path == "/app" and seen[0].url.params["state"] == "oa_test"
        assert seen[0].headers["cookie"] == "flow=cookie"

    asyncio.run(run())
