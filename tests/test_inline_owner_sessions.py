"""Interactive owner proof cannot be minted from an app/MCP bearer."""

import asyncio
import json
import os
import time
from contextlib import closing
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.support.broker_double import seed_owner_admission
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
    assert refused.status_code == 303  # Stages completion only; still no owner proof.
    assert refused.headers["location"].startswith("/app#owner_completion=")
    assert b"copied-code" not in refused.body


def test_bearer_fetch_cannot_start_interactive_login(setup):
    response = asyncio.run(sessions.begin(request()))
    assert response.status_code == 403


def test_policy_bearer_write_cannot_bypass_owner_session(
        setup, monkeypatch, tmp_path):
    from tinyassets.broker.client import BrokerRefused
    from tinyassets.broker.policy import PolicyRefused, PolicyStore

    admissions = SimpleNamespace(admissions_after=lambda _generation: [
        SimpleNamespace(center="alpha", principal="alice", event="admit")])
    seed_owner_admission(tmp_path, principal="alice", center="alpha")
    store = PolicyStore(tmp_path, admissions)
    store.bootstrap()
    current = store.read("alice")

    def broker(_base, owner, action, **fields):
        if action == "receipts":
            return {"receipts": store.receipts(owner)}
        assert action == "write"
        try:
            assert "cookie" not in fields
            return store.write(owner, fields["session_proof"], fields["expected_generation"],
                               fields["policy"]).projection()
        except PolicyRefused as exc:
            raise BrokerRefused("broker refused policy write") from exc

    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "_read_home", lambda _identity: "alpha")
    async def payload(_request):
        return {"expected_generation": current.generation, "policy": current.document}
    monkeypatch.setattr(onboarding, "_read_small_json", payload)
    monkeypatch.setattr("tinyassets.broker.client.owner_policy_operation", broker)
    req = request(cookie="")
    req.method = "POST"
    with identity_context(Identity(user_id="alice", username="alice")):
        response = asyncio.run(onboarding._handle_policy(req))
    assert response.status_code == 403
    assert store.read("alice").generation == current.generation
    owner_cookie = "interactive-" + "p" * 43
    with sessions.store() as conn:
        conn.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                     (sessions.hashed(owner_cookie), json.dumps({"user_id": "alice"}),
                      time.time() + 300))
    req.cookies[sessions.COOKIE] = owner_cookie
    with identity_context(Identity(user_id="alice", username="alice")):
        accepted = asyncio.run(onboarding._handle_policy(req))
    assert accepted.status_code == 200, accepted.body
    assert store.read("alice").generation == current.generation + 1
    assert any(row["kind"] == "write" for row in json.loads(accepted.body)["receipts"])


def test_revoke_owner_cancels_all_connect_flows_only_for_that_owner(setup, tmp_path):
    from tinyassets.connection_oauth.pkce import flows_db
    from tinyassets.onboarding.inline_model_connect import flows, take

    with flows_db(tmp_path) as (conn, now):
        for owner in ("alice", "bob"):
            conn.execute("INSERT INTO hosted_model_flows VALUES (?,?,?,?,?,?,?,?,?,?)",
                         (owner, owner, "home", "preset", "digest", "challenge", "origin",
                          now, now + 300, "opaque-request"))
            conn.execute("INSERT INTO connection_oauth_flows VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                         (owner, owner, "home", "request", "digest", "challenge", "client",
                          "redirect", now, now + 300, "{}", owner))
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


@pytest.mark.parametrize("choice", ["approve", "deny"])
@pytest.mark.parametrize("expired", [False, True])
def test_box_approval_decision_uses_forwarded_session(tmp_path, monkeypatch, choice, expired):
    from tinyassets import bound_requests, owner_runtime
    from tinyassets.onboarding.inline_requests import handle_approval
    from tinyassets.storage.pending_requests import create_request

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(owner_runtime, "_runtime", owner_runtime.OwnerRuntime(
        "alice", 1001, tmp_path, os.getpid(), None))
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    home = tmp_path / "home"
    home.mkdir()
    proof = {"session_hash": "a" * 64, "identity_json": json.dumps({"user_id": "alice"}),
             "expires_at": time.time() + (-1 if expired else 300)}
    envelope = {"subject": {"owner": "alice", "home": home.name, "agent": "main",
                            "task_id": "task", "task_generation": 1},
                "arguments": {"connection_id": "connection", "verb": "POST",
                              "request": {"path": "/messages"}},
                "destination": "https://example.com/messages", "action_class": "app.write",
                "expires_at": time.time() + 300, "executor": "authenticated_external_call"}
    row = create_request(home, kind="Approval", title="Send", body="", fields=[],
                         action={"type": "approve_action", "envelope": envelope}, dedupe_key="one")
    decision = {"token_hash": bound_requests.digest("token"), "session_hash": proof["session_hash"],
                "expires_at": time.time() + 300, "revision": 1, "scope": "once"}
    with closing(bound_requests.connect(home)) as db:
        db.execute("UPDATE pending_requests SET action_sha256=?,decision_json=? WHERE request_id=?",
                   (bound_requests.digest(envelope), json.dumps(decision), row["request_id"]))
        db.commit()
    # This regression exercises decision/session admission; broker egress is slice 3.
    monkeypatch.setattr(bound_requests, "_current", bound_requests._owned)
    monkeypatch.setattr(bound_requests, "_wake", lambda *args: None)
    monkeypatch.setattr(
        "tinyassets.effectors.authenticated_external_call.run_authenticated_external_call_effector",
        lambda **kwargs: {"status": "sent"})
    monkeypatch.setattr("tinyassets.api.pending_requests._owner_gate",
                        lambda _: (home.name, home, None))
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    data = {"universe_id": home.name, "request_id": row["request_id"], "decision": choice,
            "expected_revision": 1, "action_sha256": bound_requests.digest(envelope),
            "approval_token": "token", "policy_approval": "p" * 43}

    async def payload(*args, **kwargs):
        return data

    monkeypatch.setattr(onboarding, "_read_small_json", payload)
    req = request(cookie="")
    req.cookies = {}
    req.path_params = {"operation": "decide"}
    with identity_context(Identity(user_id="alice", username="alice",
                                   metadata={"router_owner_session": proof})):
        response = asyncio.run(handle_approval(req))
    assert response.status_code == (403 if expired else 200), response.body
    if not expired:
        assert json.loads(response.body)["status"] == "answered"
    assert not (tmp_path / ".runtime" / "owner-sessions.db").exists()


def test_box_policy_door_forwards_proof_without_cookie(tmp_path, monkeypatch):
    from tinyassets import owner_runtime
    from tinyassets.broker.client import owner_policy_operation

    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(owner_runtime, "_runtime", owner_runtime.OwnerRuntime(
        "alice", 1001, tmp_path, os.getpid(), None))
    monkeypatch.setattr(onboarding, "app_config", lambda: {"resource": "https://tinyassets.io/mcp"})
    monkeypatch.setattr(onboarding, "_app_identity_required", lambda: None)
    monkeypatch.setattr(onboarding, "_read_home", lambda _: "home")
    proof = {"session_hash": "a" * 64, "identity_json": json.dumps({"user_id": "alice"}),
             "expires_at": time.time() + 300}
    seen = []

    def broker(root, owner, action, **fields):
        seen.append((owner, action, fields))
        return {}

    async def payload(_request):
        return {"expected_generation": 1, "policy": {}}

    monkeypatch.setattr("tinyassets.broker.client.owner_policy_operation", broker)
    monkeypatch.setattr(onboarding, "_read_small_json", payload)
    req = request(cookie="")
    req.cookies = {}
    req.method = "POST"
    with identity_context(Identity(user_id="alice", username="alice",
                                   metadata={"router_owner_session": proof})):
        response = asyncio.run(onboarding._handle_policy(req))
    assert response.status_code == 200, response.body
    assert seen[0] == ("alice", "write", {"session_proof": {"session_hash": "a" * 64},
                                         "expected_generation": 1, "policy": {}})
    # Slice 3: the box reaches policy only through its relay channel; with no
    # relay bound it fails loudly, never reading a local policy file.
    with pytest.raises(RuntimeError, match="owner broker channel is unavailable"):
        owner_policy_operation(tmp_path, "alice", "read")
    assert not (tmp_path / ".runtime" / "owner-sessions.db").exists()
