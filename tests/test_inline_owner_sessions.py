"""Interactive owner proof cannot be minted from an app/MCP bearer."""

import asyncio
import json
import time
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from tinyassets import onboarding
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.onboarding import owner_sessions as sessions


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    cfg = {
        "configured": True,
        "resource": "https://tinyassets.io/mcp",
        "client_id": "client",
        "scopes": "openid profile",
        "authorization_endpoint": "https://identity.example/authorize",
        "token_endpoint": "https://identity.example/token",
    }
    monkeypatch.setattr(onboarding, "app_config", lambda: cfg)
    with sessions.store() as conn:
        conn.execute(
            "INSERT INTO owner_sessions VALUES (?,?,?)",
            (sessions.hashed("protected"), json.dumps({"user_id": "alice"}), time.time() + 300),
        )
    return cfg


def request(cookie="protected", origin="https://tinyassets.io"):
    return SimpleNamespace(
        cookies={sessions.COOKIE: cookie},
        headers={
            "origin": origin,
            "host": "tinyassets.io",
            "content-type": "application/json",
            "authorization": "Bearer owners-own-chatbot-token",
        },
    )


@pytest.mark.parametrize(
    "cookie,origin,owner",
    [
        ("", "https://tinyassets.io", "alice"),
        ("forged", "https://tinyassets.io", "alice"),
        ("protected", "https://evil.example", "alice"),
        ("protected", "http://tinyassets.io", "alice"),
        ("protected", "null", "alice"),
        ("protected", "https://tinyassets.io", "bob"),
    ],
)
def test_bearer_origin_spoof_or_other_owner_is_not_approval(setup, cookie, origin, owner):
    with pytest.raises(PermissionError):
        sessions.require(request(cookie, origin), owner=owner)


def test_live_cookie_is_required_and_logout_revokes_it(setup):
    assert sessions.require(request(), owner="alice")
    sessions.revoke("protected")
    with pytest.raises(PermissionError):
        sessions.require(request(), owner="alice")


def test_login_pins_query_response_mode_and_keeps_verifier_in_server_custody(setup):
    req = request()
    req.headers.update({"sec-fetch-mode": "navigate", "sec-fetch-dest": "document"})
    response = asyncio.run(sessions.begin(req))
    params = parse_qs(urlsplit(response.headers["location"]).query)
    assert params["response_mode"] == ["query"]
    assert params["prompt"] == ["login"]
    assert params["code_challenge_method"] == ["S256"]
    assert "code_verifier" not in params
    assert all(
        value in response.headers["set-cookie"] for value in ("HttpOnly", "Secure", "SameSite=lax")
    )
    req.query_params = {"state": params["state"][0], "code": "copied-code"}
    refused = asyncio.run(sessions.callback(req))
    assert refused.status_code == 403  # Correct state, missing flow cookie: no exchange.
    assert b"copied-code" not in refused.body


def test_bearer_fetch_cannot_start_interactive_login(setup):
    response = asyncio.run(sessions.begin(request()))
    assert response.status_code == 403


def test_rules_bearer_write_cannot_bypass_action_approval(setup, monkeypatch):
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    req = request(cookie="")
    req.method = "POST"
    with identity_context(Identity(user_id="alice", username="alice")):
        response = asyncio.run(onboarding._handle_rules(req))
    assert response.status_code == 403


def test_revoke_owner_cancels_all_connect_flows_only_for_that_owner(setup, tmp_path):
    from tinyassets.connection_oauth.pkce import flows_db
    from tinyassets.onboarding.inline_model_connect import flows, take

    with flows_db(tmp_path) as (conn, now):
        for owner in ("alice", "bob"):
            conn.execute("INSERT INTO hosted_model_flows VALUES (?,?,?,?,?,?,?,?,?)",
                         (owner, owner, "home", "preset", "digest", "challenge", "origin",
                          now, now + 300))
            conn.execute("INSERT INTO connection_oauth_flows VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                         (owner, owner, "home", "request", "digest", "challenge", "client",
                          "redirect", now, now + 300, owner))
    with flows() as conn:
        conn.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                     (sessions.hashed("bob-cookie"), json.dumps({"user_id": "bob"}),
                      time.time() + 300))
        for owner in ("alice", "bob"):
            for state in ("waiting", "ready"):
                conn.execute("INSERT INTO inline_model_flows VALUES (?,?,?,?,?,?,?)",
                             (sessions.hashed(owner + state), owner, "home", b"sealed-code", "",
                              state, time.time() + 300))
    sessions.revoke_owner("alice")
    sessions.revoke_owner("alice")  # Repeated logout is safe.
    assert sessions.lookup("protected") is None
    assert sessions.lookup("bob-cookie") is not None
    with flows_db(tmp_path) as (conn, _):
        for table in ("hosted_model_flows", "connection_oauth_flows"):
            assert [r[0] for r in conn.execute(f"SELECT owner_user_id FROM {table}")] == ["bob"]
    with flows() as conn:
        rows = conn.execute("SELECT owner,status,sealed FROM inline_model_flows").fetchall()
    assert [(r[1], r[2]) for r in rows if r[0] == "alice"] == [("cancelled", b"")] * 2
    assert [(r[1], r[2]) for r in rows if r[0] == "bob"] == [
        ("waiting", b"sealed-code"), ("ready", b"sealed-code")]
    assert take(owner="alice", home="home", handle="aliceready")["status"] == "cancelled"
