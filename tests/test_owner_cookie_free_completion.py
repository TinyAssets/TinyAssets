"""Real auth middleware + app routes, fake PKCE IdP, separate browser jars."""

import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest
from starlette.testclient import TestClient

from tests.test_approval_browser_handoff import JSON, NAV, ORIGIN
from tests.test_approval_browser_handoff import flow as _flow
from tinyassets.auth.middleware import AuthContextMiddleware
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import approval_handoff, owner_sessions
from tinyassets.storage.pending_requests import get_request

AUTH = {**JSON, "authorization": "Bearer issued-access"}
ENDPOINT = "/app/owner-sign-in/complete"
flow = _flow


def stage(browser, flow):
    start = browser.get("/app/owner-sign-in?app=1", headers=NAV, follow_redirects=False)
    flow.authorize.update(parse_qs(urlsplit(start.headers["location"]).query))
    assert flow.authorize["prompt"] == ["login"]
    # Omit all cookies only on the cross-site hop. Same-origin fetches recover them.
    saved = list(browser.cookies.jar)
    browser.cookies.clear()
    callback = browser.get("/app", params={
        "state": flow.authorize["state"][0], "code": "idp-code",
    }, follow_redirects=False)
    for cookie in saved:
        browser.cookies.jar.set_cookie(cookie)
    assert callback.status_code == 303
    assert "idp-code" not in callback.text + callback.headers["location"]
    assert owner_sessions.COOKIE not in browser.cookies
    return parse_qs(urlsplit(callback.headers["location"]).fragment)["owner_completion"][0]


@pytest.mark.parametrize("client", ["web", "desktop", "android", "android-debug", "ios"])
def test_cookie_free_completion_returns_to_approval_and_real_answer(flow, client):
    app = AuthContextMiddleware(flow.app)
    with TestClient(app, base_url=ORIGIN) as browser, TestClient(app, base_url=ORIGIN) as shell:
        path = shell.post("/app/approval-handoff", headers=AUTH, json={
            "request_id": flow.card["request_id"], "client": client,
        }).json()["launch_path"]
        browser.get(path, follow_redirects=False)
        handle = stage(browser, flow)
        payload = {"universe_id": "u-1", "request_id": flow.card["request_id"], "dismiss": True}
        assert browser.post("/app/approvals/answer", headers=AUTH, json=payload).status_code == 403
        result = browser.post(ENDPOINT, headers=AUTH, json={"completion": handle})
        assert result.status_code == 200, result.text
        assert result.json() == {"redirect": path}
        assert owner_sessions.COOKIE in browser.cookies
        assert owner_sessions.COOKIE not in shell.cookies
        assert len(flow.exchanges) == 1
        assert all(secret not in result.text for secret in ("idp-code", "issued-access", "renewal"))
        ref = path.rsplit("/", 1)[1]
        ready = browser.get(path, follow_redirects=False)
        assert ready.headers["location"] == "/app?approval=" + ref
        details = browser.post("/app/approval-handoff?ref=" + ref, headers=AUTH).json()
        assert details["return_url"] == approval_handoff.completion_url(ref, client)
        assert browser.post("/app/approvals/answer", headers=AUTH, json=payload).status_code == 200
        assert get_request(flow.home, payload["request_id"])["status"] == "dismissed"
        assert browser.post(ENDPOINT, headers=AUTH, json={"completion": handle}).status_code == 403
        replay = browser.get("/app", params={"state": flow.authorize["state"][0],
                                            "code": "idp-code"}, follow_redirects=False)
        assert replay.status_code == 403
        assert len(flow.exchanges) == 1


@pytest.mark.parametrize("failure", ["missing_bearer", "wrong_identity", "wrong_origin", "expired"])
def test_cookie_free_refusals_cannot_create_owner_proof(flow, monkeypatch, failure, caplog):
    with TestClient(AuthContextMiddleware(flow.app), base_url=ORIGIN) as browser:
        handle = stage(browser, flow)
        headers = dict(AUTH)
        if failure == "missing_bearer":
            headers.pop("authorization")
        elif failure == "wrong_identity":
            headers["authorization"] = "Bearer bob-access"
            monkeypatch.setattr("tinyassets.auth.middleware._get_provider", lambda: SimpleNamespace(
                resolve_token=lambda token: (Identity(user_id="bob", username="bob")
                                             if token == "bob-access" else flow.identity)))
        elif failure == "wrong_origin":
            headers["origin"] = "https://evil.test"
        else:
            with owner_sessions.store() as conn:
                conn.execute("UPDATE owner_login_flows SET expires_at=?", (time.time() - 1,))
        response = browser.post(ENDPOINT, headers=headers, json={"completion": handle})
        assert response.status_code == (401 if failure == "missing_bearer" else 403)
        assert owner_sessions.COOKIE not in browser.cookies
        assert len(flow.exchanges) == (1 if failure == "wrong_identity" else 0)
        if failure == "expired":
            assert "sign-in link expired" in response.json()["message"]
            assert caplog.records[-1].flow_age_seconds >= 600
        if failure == "wrong_identity":
            assert response.json()["error"] == "identity_mismatch"
            replay = browser.post(ENDPOINT, headers=AUTH, json={"completion": handle})
            assert replay.status_code == 403
        assert handle not in caplog.text and "idp-code" not in caplog.text


def test_expired_cookie_callback_has_actionable_message_and_diagnostics(flow, caplog):
    with TestClient(AuthContextMiddleware(flow.app), base_url=ORIGIN) as browser:
        start = browser.get("/app/owner-sign-in", headers=NAV, follow_redirects=False)
        state = parse_qs(urlsplit(start.headers["location"]).query)["state"][0]
        with owner_sessions.store() as conn:
            conn.execute("UPDATE owner_login_flows SET expires_at=?", (time.time() - 1,))
        response = browser.get("/app", params={"state": state, "code": "secret"})
        assert response.status_code == 403
        assert "sign-in link expired. Start sign-in again" in response.text
        assert caplog.records[-1].flow_age_seconds >= 600
        assert caplog.records[-1].cookie_names == [owner_sessions.FLOW_COOKIE]
        assert state not in caplog.text and "secret" not in caplog.text
