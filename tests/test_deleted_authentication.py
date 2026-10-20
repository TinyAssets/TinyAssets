"""C15: credential revocation fences authentication without routing tombstones."""

import asyncio
import hashlib
import json
import time
from http.cookies import SimpleCookie
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

from tests.support.admission import drive, projection
from tests.test_onboarding_session_refresh import _drive, _ok
from tests.test_workos_provider import _provider, _sign
from tinyassets.auth import middleware
from tinyassets.broker.admission import local_mutate
from tinyassets.onboarding import owner_sessions, session_store
from tinyassets.router_credentials import revoke_account


@pytest.fixture
def deleted(tmp_path, monkeypatch, in_process_broker):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    live = in_process_broker.supervisor_for(tmp_path)
    drive(local_mutate(live.identities.path, "alice", "erase",
        routing=projection(live.identities.path)))
    revoke_account(tmp_path, "alice")
    return tmp_path


@pytest.fixture(scope="module")
def keypair():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def test_fresh_workos_bearer_and_claims_refuse_deleted_owner(deleted, keypair, monkeypatch):
    from tinyassets import router_account_deletion
    from tinyassets.auth.workos_provider import rejection_category, rejection_description

    monkeypatch.setattr(router_account_deletion, "workos_sessions", lambda owner: (
        [{"id": "session_test"}] if owner == "bob" else []))
    provider = _provider(keypair)
    token = _sign(keypair, sub="alice", sid="brand-new-session")
    assert provider.verified_claims(token)["sub"] == "alice"
    assert provider.resolve_token(token) is None
    assert rejection_category() == "expired"
    assert "expired" in rejection_description(rejection_category())
    assert provider.resolve_token(_sign(keypair, sub="bob")) is not None


@pytest.mark.parametrize("app_login", [False, True])
def test_fresh_workos_callback_stores_no_session(deleted, monkeypatch, keypair, app_login):
    from tinyassets import onboarding

    cfg = {"configured": True, "resource": "https://tinyassets.io/mcp",
           "client_id": "client", "scopes": "openid",
           "authorization_endpoint": "https://identity.example/authorize",
           "token_endpoint": "https://identity.example/token"}
    monkeypatch.setenv("TINYASSETS_ONBOARDING_APP", "1")
    monkeypatch.setattr(onboarding, "app_config", lambda: cfg)
    middleware.set_provider(_provider(keypair))
    req = SimpleNamespace(
        headers={"sec-fetch-mode": "navigate", "sec-fetch-dest": "document"},
        cookies={}, query_params={"app": "1"} if app_login else {})
    response = asyncio.run(owner_sessions.begin(req))
    state = parse_qs(urlsplit(response.headers["location"]).query)["state"][0]
    cookie = SimpleCookie(response.headers["set-cookie"])
    req.cookies[owner_sessions.FLOW_COOKIE] = cookie[owner_sessions.FLOW_COOKIE].value
    req.query_params = {"state": state, "code": "fresh-code"}
    real_client = httpx.AsyncClient

    class Client(real_client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = httpx.MockTransport(lambda _: _ok(
                _sign(keypair, sub="alice", sid="fresh-session"), "new-refresh"))
            super().__init__(*args, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", Client)
    response = asyncio.run(owner_sessions.callback(req))
    assert response.status_code == 403
    assert b"Sign-in" in response.body
    with owner_sessions.store() as db:
        assert db.execute("SELECT COUNT(*) FROM owner_sessions").fetchone()[0] == 0
    assert "new-refresh" not in str(response.headers)


@pytest.mark.parametrize("grant", ["authorization_code", "refresh_token"])
def test_token_exchange_and_refresh_refused_without_persisting(
        deleted, monkeypatch, keypair, grant):
    status, body, cookies, _ = _drive(
        {"grant_type": grant, "code": "fresh", "code_verifier": "verifier",
         "redirect_uri": "https://tinyassets.io/app"},
        cookie="ta_rt=old-refresh", data_dir=str(deleted), monkeypatch=monkeypatch,
        provider=_provider(keypair),
        upstream=lambda _: _ok(_sign(keypair, sub="alice", sid="fresh-session"), "rotated"))
    assert status == 401 and body["error"] == "identity_unresolved"
    assert not cookies
    assert not list(deleted.glob(".runtime/app_refresh_sessions/*.json"))


def test_api_key_and_hook_token_refuse_after_credential_revocation(deleted):
    from tinyassets.api_keys import ApiKeyStore, resolve_identity
    from tinyassets.router_credentials import HookOwners

    keys = ApiKeyStore(deleted)
    key = keys.mint("alice", name="fixture", scopes=[], authority=None)["key"]
    hooks = HookOwners(deleted)
    hooks.publish(hashlib.sha256(b"hook").hexdigest(), "alice")
    revoke_account(deleted, "alice")
    assert keys.resolve(key) is None
    assert resolve_identity(key) is None
    assert hooks.resolve("hook") is None
    with keys.db() as db:
        assert db.execute("SELECT last_used_at FROM api_keys").fetchone()[0] is None


def test_stored_sessions_and_provider_bearers_are_revoked(tmp_path, monkeypatch, keypair):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(tmp_path, principal="alice", center="cc-alice")
    handle = session_store.mint("refresh", owner="alice")
    with owner_sessions.store() as db:
        db.execute("INSERT INTO owner_sessions VALUES (?,?,?)", (
            owner_sessions.hashed("old-cookie"), json.dumps({"user_id": "alice"}), time.time()+300))
    path = tmp_path / ".broker/state/owner-identities.db"
    drive(local_mutate(path, "alice", "erase", routing=projection(path)))
    revoke_account(tmp_path, "alice")
    assert owner_sessions.lookup("old-cookie") is None
    monkeypatch.setattr(middleware, "_get_provider", lambda: _provider(keypair))
    assert middleware.auth_middleware(_sign(keypair, sub="alice", sid="session_old")) is None
    assert session_store.read(handle) == ("", "")


def test_existing_refresh_handle_and_rotation_refuse_after_revocation(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(tmp_path, principal="alice", center="cc-alice")
    handle = session_store.mint("refresh", owner="alice")
    assert session_store.read(handle)[0] == "refresh"
    drive(local_mutate(tmp_path / ".broker/state/owner-identities.db", "alice", "erase",
        routing=projection(tmp_path / ".broker/state/owner-identities.db")))
    revoke_account(tmp_path, "alice")
    assert session_store.rotate(handle, "new-refresh") == ""
    assert session_store.read(handle) == ("", "")


def test_deleted_refresh_handle_is_explained_without_contacting_workos(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tests.support.broker_double import seed_owner_admission

    seed_owner_admission(tmp_path, principal="alice", center="cc-alice")
    handle = session_store.mint("refresh", owner="alice")
    drive(local_mutate(tmp_path / ".broker/state/owner-identities.db", "alice", "erase",
        routing=projection(tmp_path / ".broker/state/owner-identities.db")))
    revoke_account(tmp_path, "alice")
    status, body, cookies, calls = _drive(
        {"grant_type": "refresh_token", "session_ref": handle},
        data_dir=str(tmp_path), monkeypatch=monkeypatch,
        upstream=lambda _: pytest.fail("deleted refresh reached WorkOS"))
    assert status == 401 and body["error"] == "no_refresh_token"
    assert not cookies and not calls


@pytest.mark.parametrize("deleted_before_exchange", [False, True])
def test_local_oauth_authorize_exchange_and_bearer_are_fenced(
        tmp_path, monkeypatch, deleted_before_exchange):
    import base64

    from tinyassets.auth.provider import OAuthProvider

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("UNIVERSE_SERVER_OAUTH_SUBJECT", "alice")
    provider = OAuthProvider(tmp_path / ".auth.db")
    redirect = "https://client.example/callback"
    client = provider.register_client({"redirect_uris": [redirect]})["client_id"]
    verifier = "v" * 43
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    code = provider.create_authorization(client, redirect, "read", "", challenge, "S256")
    if deleted_before_exchange:
        drive(local_mutate(tmp_path / ".broker/state/owner-identities.db", "alice", "erase",
            routing=projection(tmp_path / ".broker/state/owner-identities.db")))
        revoke_account(tmp_path, "alice")
    tokens = provider.exchange_code(code, client, redirect, verifier)
    if deleted_before_exchange:
        assert tokens is None
    else:
        assert provider.resolve_token(tokens["access_token"]) is not None
        drive(local_mutate(tmp_path / ".broker/state/owner-identities.db", "alice", "erase",
            routing=projection(tmp_path / ".broker/state/owner-identities.db")))
        revoke_account(tmp_path, "alice")
        assert provider.resolve_token(tokens["access_token"]) is None
    with provider._connect() as db:
        for table in ("authorization_codes", "access_tokens", "refresh_tokens"):
            assert db.execute(
                f"SELECT COUNT(*) FROM {table} WHERE user_id='alice'").fetchone()[0] == 0
