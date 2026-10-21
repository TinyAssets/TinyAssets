"""Separate WebView/browser jars, real /app routing and real request dismissal."""

import base64
import hashlib
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from starlette.applications import Starlette

from tests.test_pending_requests import _CRED, _make_universe
from tinyassets import onboarding
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import owner_sessions
from tinyassets.storage.pending_requests import create_request

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
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(tmp_path, principal="alice", center="u-1")
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
