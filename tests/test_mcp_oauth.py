"""MCP OAuth against a local HTTP authorization server, using real egress code."""
# ruff: noqa: F811
from dataclasses import replace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from tests.test_generic_oauth_connections import (
    API,
    AUTH,
    CHALLENGE,
    OWNER,
    REDIRECT,
    TASKS_ASK,
    UID,
    VERIFIER,
    _as,
    _ask,
    _broker,
    _call,
    _connected,
    _post,
    _vault_bundle,
    app,  # noqa: F401
    provider,  # noqa: F401
    universes,  # noqa: F401
)
from tinyassets.connection_oauth import mcp, tokens
from tinyassets.connection_oauth.transport import OAuthError

ENDPOINT = f"https://{API}/mcp"


@pytest.fixture
def mcp_provider(provider, monkeypatch):
    route = provider.route
    seen = []

    def handle(method, host, path, headers, body):
        seen.append((method, host, path, body))
        if path == "/.well-known/oauth-protected-resource/mcp":
            return 200, {"resource": ENDPOINT, "authorization_servers": [f"https://{AUTH}"]}
        return route(method, host, path, headers, body)

    monkeypatch.setattr(provider, "route", handle)
    provider.seen = seen
    return provider


def test_resource_discovery_dcr_pkce_exchange_and_refresh(mcp_provider):
    offer = mcp.discover(ENDPOINT, {"scopes": ["tasks.write"]})
    assert offer["resource"] == ENDPOINT
    client = mcp.select_client(offer, redirect_uri=REDIRECT)
    redirect = mcp_provider.authorize(offer["authorize_url"] + "?" + urlencode({
        "client_id": client, "response_type": "code", "redirect_uri": REDIRECT,
        "state": "state", "code_challenge": CHALLENGE, "code_challenge_method": "S256",
        "resource": ENDPOINT,
    }))
    code = parse_qs(urlsplit(redirect).query)["code"][0]
    bundle = tokens.exchange_code(token_url=offer["token_url"], client_id=client, code=code,
                                  verifier=VERIFIER, redirect_uri=REDIRECT, resource=ENDPOINT)
    assert tokens.decode(tokens.encode(bundle)).resource == ENDPOINT
    refreshed = tokens.refresh(bundle)
    assert refreshed.resource == ENDPOINT
    forms = [parse_qs(body.decode()) for method, _, path, body in mcp_provider.seen
             if method == "POST" and path == "/token"]
    assert len(forms) == 2
    assert all(form["resource"] == [ENDPOINT] for form in forms)
    assert forms[0]["code_verifier"] == [VERIFIER]


@pytest.mark.parametrize("cimd,static,expected", [
    (True, "static", "https://tinyassets.io/app/oauth/client-metadata.json"),
    (False, "static", "static"),
])
def test_registration_fallback_order(monkeypatch, cimd, static, expected):
    def failed(*args, **kwargs):
        raise OAuthError("client_registration_failed")

    monkeypatch.setattr(mcp, "register_public_client", failed)
    assert mcp.select_client({"registration_url": "https://auth.example.com/register",
        "cimd_supported": cimd, "client_id": static}, redirect_uri=REDIRECT) == expected


def test_no_supported_client_is_actionable():
    with pytest.raises(OAuthError, match="mcp_client_registration_required"):
        mcp.select_client({}, redirect_uri=REDIRECT)


def test_mcp_pkce_is_server_held_and_bound_to_initiating_session(mcp_provider, app, monkeypatch):
    import json

    from tinyassets.connection_oauth import discovery, flow, pkce
    from tinyassets.onboarding import owner_sessions, session_store

    monkeypatch.setattr(session_store, "_key", b"s" * 32)
    offer = mcp.discover(ENDPOINT, {"scopes": ["tasks.write"]})
    monkeypatch.setattr(discovery, "resolve_offer", lambda *_: (offer, ""))
    with _as(OWNER):
        asked = _ask({**TASKS_ASK, "path_template": "/mcp"})
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                     "code_challenge": CHALLENGE})
        assert begun.status_code == 200, begun.text
        handle = begun.json()["flow"]
        query = parse_qs(urlsplit(begun.json()["authorize_url"]).query)
        assert query["code_challenge"] != [CHALLENGE]
        assert query["resource"] == [ENDPOINT]
        with pkce.flows_db(app) as (conn, _):
            stored = dict(conn.execute("SELECT * FROM connection_oauth_flows").fetchone())
        assert stored["sealed_verifier"] and VERIFIER.encode() not in stored["sealed_verifier"]
        assert stored["owner_session_hash"]
        back = urlsplit(mcp_provider.authorize(begun.json()["authorize_url"]))
        code = parse_qs(back.query)["code"][0]
        # Another valid session for the same owner cannot redeem this flow.
        with owner_sessions.store() as conn:
            conn.execute("INSERT INTO owner_sessions VALUES (?,?,?)",
                         ("different-session", json.dumps({"user_id": OWNER}), 4102444800))
        with pytest.raises(flow.FlowError, match="initiating_session_changed"):
            flow.complete(owner=OWNER, universe_id=UID, handle=handle, code=code,
                          verifier=VERIFIER, owner_session={"session_hash": "different-session"})
        assert not mcp_provider.api_calls
        assert not [x for x in mcp_provider.seen if x[2] == "/token"]
        # Browser verifier is not authority: the server uses its sealed verifier.
        done = _post("oauth_exchange", {"flow": handle, "code": code,
                                       "code_verifier": VERIFIER})
        assert done.status_code == 200, done.text
        assert _vault_bundle(app).resource == ENDPOINT
        repeated = _post("oauth_exchange", {"flow": handle, "code": code,
                                           "code_verifier": VERIFIER})
        assert repeated.status_code == 404
        assert len([x for x in mcp_provider.seen if x[2] == "/token"]) == 1


def test_mcp_begin_requires_live_owner_session_before_registration(mcp_provider, app, monkeypatch):
    from tinyassets.connection_oauth import discovery, flow

    offer = mcp.discover(ENDPOINT, {})
    monkeypatch.setattr(discovery, "resolve_offer", lambda *_: (offer, ""))
    with _as(OWNER):
        asked = _ask({**TASKS_ASK, "path_template": "/mcp"})
        with pytest.raises(flow.FlowError, match="interactive_approval_required"):
            flow.begin(owner=OWNER, universe_id=UID, request_id=asked["request_id"],
                       challenge=CHALLENGE, public_resource="https://tinyassets.io/mcp")
    assert not [x for x in mcp_provider.seen if x[2] == "/register"]


def test_logout_during_mcp_token_exchange_prevents_deposit(mcp_provider, app, monkeypatch):
    from tinyassets.connection_oauth import discovery, flow
    from tinyassets.onboarding import owner_sessions, session_store

    monkeypatch.setattr(session_store, "_key", b"s" * 32)
    offer = mcp.discover(ENDPOINT, {})
    monkeypatch.setattr(discovery, "resolve_offer", lambda *_: (offer, ""))
    original_exchange = flow.exchange_code

    def exchange(**kwargs):
        result = original_exchange(**kwargs)
        owner_sessions.revoke_owner(OWNER)
        return result

    monkeypatch.setattr(flow, "exchange_code", exchange)
    with _as(OWNER):
        asked = _ask({**TASKS_ASK, "path_template": "/mcp"})
        begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                     "code_challenge": CHALLENGE}).json()
        back = urlsplit(mcp_provider.authorize(begun["authorize_url"]))
        code = parse_qs(back.query)["code"][0]
        done = _post("oauth_exchange", {"flow": begun["flow"], "code": code,
                                       "code_verifier": VERIFIER})
        assert done.status_code == 403, done.text
        from tinyassets.credential_vault import load_credential_vault
        from tinyassets.storage.pending_requests import get_request

        assert not load_credential_vault(app / UID)
        assert get_request(app / UID, asked["request_id"])["status"] == "pending"


def test_mcp_finalization_refuses_busy_owner_before_session_lock(mcp_provider, app, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from contextlib import contextmanager
    from threading import Event

    from tinyassets.connection_oauth import discovery, flow
    from tinyassets.onboarding import session_store
    from tinyassets.owner_control import control

    monkeypatch.setattr(session_store, "_key", b"s" * 32)
    offer = mcp.discover(ENDPOINT, {})
    monkeypatch.setattr(discovery, "resolve_offer", lambda *_: (offer, ""))
    original_exchange, original_guard = flow.exchange_code, flow._session_guard
    held, release = Event(), Event()
    guards = []

    @contextmanager
    def guard(*args):
        guards.append(True)
        with original_guard(*args) as value:
            yield value

    def hold():
        with control(app / UID):
            held.set()
            assert release.wait(10)

    with ThreadPoolExecutor(max_workers=1) as pool:
        def exchange(**kwargs):
            result = original_exchange(**kwargs)
            pool.submit(hold)
            assert held.wait(5)
            return result

        monkeypatch.setattr(flow, "exchange_code", exchange)
        monkeypatch.setattr(flow, "_session_guard", guard)
        try:
            with _as(OWNER):
                asked = _ask({**TASKS_ASK, "path_template": "/mcp"})
                begun = _post("oauth_begin", {"request_id": asked["request_id"],
                                             "code_challenge": CHALLENGE}).json()
                back = urlsplit(mcp_provider.authorize(begun["authorize_url"]))
                code = parse_qs(back.query)["code"][0]
                done = _post("oauth_exchange", {"flow": begun["flow"], "code": code,
                                               "code_verifier": VERIFIER})
                assert done.status_code == 409, done.text
                assert done.json()["error"] == "owner_control_unavailable"
                assert len(guards) == 2  # Begin + flow take; final session lock never acquired.
                from tinyassets.credential_vault import load_credential_vault

                assert not load_credential_vault(app / UID)
        finally:
            release.set()


def test_concurrent_legacy_flow_migration_is_atomic(tmp_path):
    import sqlite3
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tinyassets.connection_oauth import pkce

    with sqlite3.connect(tmp_path / pkce._DB_NAME) as conn:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("CREATE TABLE connection_oauth_flows ("
                     "handle_digest TEXT PRIMARY KEY, owner_user_id TEXT NOT NULL, "
                     "universe_id TEXT NOT NULL, request_id TEXT NOT NULL, "
                     "action_digest TEXT NOT NULL, challenge TEXT NOT NULL, "
                     "client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, "
                     "created_at REAL NOT NULL, expires_at REAL NOT NULL)")
    barrier = Barrier(4)

    def migrate(_):
        barrier.wait(timeout=5)
        with pkce.flows_db(tmp_path) as (conn, _now):
            assert conn.in_transaction
            columns = {row[1] for row in conn.execute("PRAGMA table_info(connection_oauth_flows)")}
            assert {"owner_session_hash", "sealed_verifier"} <= columns

    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(migrate, range(4)))


def test_neighboring_resource_metadata_is_rejected(mcp_provider):
    with pytest.raises(OAuthError, match="protected_resource_mismatch"):
        mcp.discover(f"https://{API}/other", {})


@pytest.mark.parametrize("endpoint", [
    f"https://{API}/mcp?key=secret", f"https://user:secret@{API}/mcp",
    f"http://{API}/mcp", f"https://{API}/mcp#secret",
])
def test_secret_bearing_or_insecure_url_never_reaches_discovery(endpoint, mcp_provider):
    with pytest.raises(OAuthError):
        mcp.discover(endpoint, {})
    assert mcp_provider.seen == []


def test_broker_never_sends_resource_token_to_another_resource(provider, app, tmp_path):
    from tinyassets.storage.outbound_connections import GrantResolutionError

    _, grant_id = _connected(provider, app, tmp_path)
    keeper = tokens.ConnectionTokens(universe_dir=app / UID, owner_user_id=OWNER)
    original = _vault_bundle(app)
    keeper._write("tasklark", replace(original, resource=ENDPOINT))
    dispatch = _broker(app, UID, OWNER, grant_id, tmp_path / "rt")
    with pytest.raises(GrantResolutionError, match="another resource"):
        _call(dispatch, grant_id)
    from tinyassets.storage.outbound_connections import SsrfValidationError

    with pytest.raises(SsrfValidationError):
        dispatch(grant_id, "POST", {"url": ENDPOINT, "body": {}})
    assert provider.api_calls == []
    assert provider.refresh_calls == 0
    keeper._write("tasklark", replace(original, resource=f"https://{API}/v1/tasks"))
    assert _call(dispatch, grant_id)["status"] == 200
    assert provider.api_calls == [original.access_token]
