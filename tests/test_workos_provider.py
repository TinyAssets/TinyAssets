"""Tests for the WorkOS AuthKit Resource Server provider (founder-identity slice 1).

Our MCP server validates a WorkOS AuthKit access-token JWT and resolves its
``sub`` to the founder Identity. These tests sign real RS256 tokens with a
generated keypair and inject a fake JWKS client, so nothing touches the network.
"""

from __future__ import annotations

import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa

import tinyassets.auth.workos_provider as workos_provider_module
from tinyassets.auth.provider import Identity
from tinyassets.auth.workos_provider import (
    WorkOSAuthProvider,
)

ISSUER = "https://inventive-van-62-staging.authkit.app"
RESOURCE = "https://tinyassets.io/mcp"


@pytest.fixture(scope="module")
def keypair() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


@pytest.fixture(scope="module")
def other_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class _FakeSigningKey:
    def __init__(self, public_key: object) -> None:
        self.key = public_key


class _FakeJWKSClient:
    """Stand-in for PyJWKClient: always returns the configured public key."""

    def __init__(self, public_key: object) -> None:
        self._key = _FakeSigningKey(public_key)

    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        return self._key


class _FailingJWKSClient:
    def get_signing_key_from_jwt(self, token: str) -> _FakeSigningKey:
        raise RuntimeError("sensitive-jwks-detail")


@pytest.fixture
def isolated_rejection_log(monkeypatch):
    monkeypatch.setattr(
        workos_provider_module,
        "_token_rejection_log_state",
        {},
        raising=False,
    )
    monkeypatch.setattr(
        workos_provider_module,
        "_rejection_log_now",
        lambda: 100.0,
        raising=False,
    )


def _provider(
    keypair: rsa.RSAPrivateKey,
    *,
    issuer: str = ISSUER,
    audience: str | None = None,
) -> WorkOSAuthProvider:
    return WorkOSAuthProvider(
        issuer=issuer,
        jwks_uri="https://example.invalid/oauth2/jwks",
        audience=audience,
        jwks_client=_FakeJWKSClient(keypair.public_key()),
    )


def _sign(keypair: rsa.RSAPrivateKey, **claims: object) -> str:
    now = int(time.time())
    payload = {
        "sub": "user_workos_123",
        "sid": "session_test",
        "iss": ISSUER,
        "iat": now,
        "exp": now + 3600,
        "email": "founder@example.com",
    }
    payload.update(claims)
    return jwt.encode(payload, keypair, algorithm="RS256", headers={"kid": "test"})


@pytest.mark.verifies('owner-daemon-in-box:C3')
@pytest.mark.parametrize("offset,accepted", [(-1, False), (0, True), (1, True)])
def test_revoked_original_session_refuses_even_a_fresh_access_token(
        tmp_path, monkeypatch, keypair, offset, accepted):
    from tinyassets.router_account_deletion import revoke_local_tokens

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    now = int(time.time())
    revoke_local_tokens(tmp_path, "user_123", now=now)
    # A second writer cannot move the fence backwards; another owner is unaffected.
    revoke_local_tokens(tmp_path, "user_123", now=now - 100)
    token = _sign(keypair, sub="user_123", iat=now + offset)
    assert (_provider(keypair).resolve_token(token) is not None) is accepted
    assert _provider(keypair).resolve_token(_sign(keypair, sub="user_other")) is not None


@pytest.mark.verifies('owner-daemon-in-box:C3')
@pytest.mark.parametrize("rotated", ["rotated-refresh", ""])
def test_pre_restore_raw_cookie_refresh_is_refused(tmp_path, monkeypatch, keypair, rotated):
    from tests.test_onboarding_session_refresh import _drive, _ok
    from tinyassets.router_account_deletion import revoke_local_tokens

    revoke_local_tokens(tmp_path, "user_123", now=int(time.time()) - 1)
    access = _sign(keypair, sub="user_123", sid="session_original")
    status, body, cookies, calls = _drive(
        {"grant_type": "refresh_token"}, cookie="ta_rt=pre-restore-refresh",
        data_dir=str(tmp_path), monkeypatch=monkeypatch,
        upstream=lambda form: _ok(access, rotated), provider=_provider(keypair))
    assert calls[0]["refresh_token"] == "pre-restore-refresh"
    assert status == 401
    assert body == {"error": "identity_unresolved"}
    assert not cookies


@pytest.mark.verifies('owner-daemon-in-box:C3')
@pytest.mark.parametrize("sid", [None, "", "unknown", "session_absent"])
def test_missing_current_provider_session_denies(keypair, sid, monkeypatch):
    # Production regression: AuthKit OAuth tokens are absent from this API.
    calls = []
    monkeypatch.setattr("tinyassets.router_account_deletion.workos_sessions",
                        lambda owner: calls.append(owner) or [])
    assert _provider(keypair).resolve_token(_sign(keypair, sid=sid)) is not None
    assert calls == []


@pytest.mark.verifies('owner-daemon-in-box:C3')
@pytest.mark.parametrize("issued", [None, True, "123", float("inf")])
def test_unusable_issue_time_is_refused(keypair, issued):
    assert _provider(keypair).resolve_token(_sign(keypair, iat=issued)) is None


@pytest.mark.verifies('owner-daemon-in-box:C3')
def test_corrupt_local_revocation_store_fails_closed(tmp_path, monkeypatch, keypair):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    (tmp_path / ".workos-revocations.sqlite3").write_bytes(b"corrupt")
    assert _provider(keypair).resolve_token(_sign(keypair)) is None


@pytest.mark.verifies('owner-daemon-in-box:C3')
def test_fresh_login_and_cookie_refresh_after_restore(tmp_path, monkeypatch, keypair):
    from http.cookies import SimpleCookie

    from tests.support.broker_double import seed_owner_admission
    from tests.test_onboarding_session_refresh import _drive, _ok
    from tinyassets.router_account_deletion import revoke_local_tokens

    seed_owner_admission(tmp_path, principal="user_123", center="cc-user")
    revoke_local_tokens(tmp_path, "user_123", now=int(time.time()) - 1)
    access = _sign(keypair, sub="user_123")
    args = dict(data_dir=str(tmp_path), monkeypatch=monkeypatch,
                upstream=lambda form: _ok(access, "refresh"), provider=_provider(keypair))
    status, body, cookies, calls = _drive(
        {"grant_type": "authorization_code", "code": "fresh", "code_verifier": "v",
         "redirect_uri": "https://tinyassets.io/app"}, **args)
    assert status == 200
    cookie = SimpleCookie(cookies[0])["ta_rt"].value
    assert cookie == "local:" + body["session_ref"]
    status, body, cookies, calls = _drive(
        {"grant_type": "refresh_token"}, cookie="ta_rt=" + cookie, **args)
    assert status == 200
    assert calls[0]["refresh_token"] == "refresh"

    # Restore removes sealed handles; an old cookie must not reach upstream.
    from tinyassets.onboarding import session_store

    session_store.revoke_owner("user_123")
    status, body, cookies, calls = _drive(
        {"grant_type": "refresh_token"}, cookie="ta_rt=local:" + body["session_ref"],
        **args)
    assert status == 401 and body == {"error": "no_refresh_token"}
    assert not calls


# --- derive_endpoints ------------------------------------------------------








# --- resolve_token: happy path --------------------------------------------


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_valid_token_resolves_to_founder_identity(keypair) -> None:
    ident = _provider(keypair).resolve_token(_sign(keypair))
    assert isinstance(ident, Identity)
    assert ident.user_id == "user_workos_123"  # sub == founder key
    assert ident.username == "founder@example.com"
    assert ident.metadata["auth_provider"] == "workos"
    assert ident.metadata["email"] == "founder@example.com"
    # OAuth best practice: the token authenticates; fine-grained authz is the
    # resource server's per-universe ownership ACL. So an authenticated founder
    # holds coarse read/write/costly and can create + own their universe (the
    # ACL confines them to it). `admin` (platform) is NOT implicit — RBAC only.
    assert "read" in ident.capabilities
    assert "write" in ident.capabilities
    assert "costly" in ident.capabilities
    assert "admin" not in ident.capabilities


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_only_verified_tokens_supply_opaque_connection_metadata(keypair):
    token = _sign(keypair, sid="session_fixture")
    ident = _provider(keypair).resolve_token(token)
    connection = ident.metadata["oauth_connection"]
    assert connection["identity_source"] == "verified_session"
    assert len(connection["id"]) == 64
    assert "session_fixture" not in str(connection) and token not in str(ident.metadata)
    assert _provider(keypair).resolve_token(token + "invalid") is None








# --- resolve_token: rejections --------------------------------------------


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_expired_token_rejected(keypair) -> None:
    now = int(time.time())
    token = _sign(keypair, iat=now - 7200, exp=now - 3600)
    assert _provider(keypair).resolve_token(token) is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_missing_sub_rejected(keypair) -> None:
    # require=["exp","sub"] -> PyJWT raises MissingRequiredClaim.
    assert _provider(keypair).resolve_token(_sign(keypair, sub="")) is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_anonymous_sub_rejected(keypair) -> None:
    assert _provider(keypair).resolve_token(_sign(keypair, sub="anonymous")) is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_wrong_issuer_rejected(keypair) -> None:
    token = _sign(keypair, iss="https://evil.example.com")
    assert _provider(keypair).resolve_token(token) is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_bad_signature_rejected(keypair, other_key) -> None:
    # Token signed by a different key than the provider's JWKS exposes.
    token = jwt.encode(
        {"sub": "x", "iss": ISSUER, "exp": int(time.time()) + 3600},
        other_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    assert _provider(keypair).resolve_token(token) is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_empty_token_returns_none(keypair) -> None:
    p = _provider(keypair)
    assert p.resolve_token("") is None
    assert p.resolve_token("   ") is None












# --- audience binding ------------------------------------------------------


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_audience_enforced_when_configured(keypair) -> None:
    p = _provider(keypair, audience=RESOURCE)
    assert p.resolve_token(_sign(keypair, aud=RESOURCE)) is not None
    assert p.resolve_token(_sign(keypair, aud="https://other")) is None
    assert p.resolve_token(_sign(keypair)) is None  # no aud at all


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_canonical_resource_audience_is_accepted(keypair) -> None:
    # AuthKit sets aud to the resource the client requested. Clients and
    # dashboards differ by a trailing slash, a capitalised host, or an
    # explicit default port. Those are the same connector. The environment
    # client id WorkOS uses when `resource` is omitted is not.
    p = _provider(keypair, audience=RESOURCE)
    for aud in (
        "https://tinyassets.io/mcp/",
        "https://TinyAssets.io/mcp",
        "https://tinyassets.io:443/mcp",
        ["client_01M15YZXW7G7X6X1YQ4TG87Q00", RESOURCE],
    ):
        assert p.resolve_token(_sign(keypair, aud=aud)) is not None, aud
    assert p.resolve_token(_sign(
        keypair, aud="client_01M15YZXW7G7X6X1YQ4TG87Q00",
    )) is None
    assert p.resolve_token(_sign(
        keypair, aud="https://tinyassets.io/mcp?extra=1",
    )) is None
    assert p.resolve_token(_sign(
        keypair, aud="https://user@tinyassets.io/mcp",
    )) is None
    # Password-only userinfo still has to stay distinct. urlsplit reports an
    # empty username for ``https://:secret@host``, and an out-of-range port
    # raises; neither may authenticate or raise out of resolve_token.
    assert p.resolve_token(_sign(
        keypair, aud="https://:secret@tinyassets.io/mcp",
    )) is None
    assert p.resolve_token(_sign(
        keypair, aud="https://tinyassets.io:99999/mcp",
    )) is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_client_id_audience_is_a_named_401(
    keypair, caplog, isolated_rejection_log,
) -> None:
    """WorkOS shape when the client omits `resource` and no default indicator
    is set: aud is the environment client id, scope is a string, email may be
    absent. Login at AuthKit can still succeed. The connector must say the
    audience does not match, not a generic invalid token.
    """
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from tinyassets.auth.middleware import AuthContextMiddleware, set_provider

    token = _sign(
        keypair,
        aud="client_01M15YZXW7G7X6X1YQ4TG87Q00",
        scope="openid profile email offline_access",
        email="",
    )
    provider = _provider(keypair, audience=RESOURCE)
    set_provider(provider)
    caplog.set_level(logging.WARNING, logger="universe_server.auth.workos")

    async def ok(request):  # noqa: ANN001, ANN202
        return PlainTextResponse("ok")

    app = AuthContextMiddleware(Starlette(routes=[Route("/mcp", ok, methods=["POST"])]))
    client = TestClient(app)
    try:
        response = client.post(
            "/mcp",
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/json, text/event-stream",
            },
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "probe", "version": "0"},
                },
            },
        )
    finally:
        set_provider(None)

    assert response.status_code == 401
    assert response.json()["error"] == "invalid_token"
    assert response.json()["error_description"] == (
        "The access token was not issued for this connector."
    )
    challenge = response.headers["www-authenticate"]
    assert 'error="invalid_token"' in challenge
    assert 'error_description="The access token was not issued for this connector."' in challenge
    assert "client_01" not in response.text
    assert token not in response.text
    assert "category=audience" in caplog.text
    assert token not in caplog.text








# --- provider contract -----------------------------------------------------






# --- from_env / factory ----------------------------------------------------












# --- integration: the whole point of slice 1 ------------------------------


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_middleware_chain_yields_real_subject(keypair, monkeypatch) -> None:
    """auth_middleware -> current_identity -> _current_actor returns the real sub,
    not 'anonymous'. This is the slice-1 acceptance behavior."""
    from tinyassets.api import engine_helpers
    from tinyassets.auth import middleware as mw

    monkeypatch.delenv("UNIVERSE_SERVER_USER", raising=False)
    mw.set_provider(_provider(keypair))
    try:
        mw.auth_middleware(_sign(keypair))
        assert mw.current_identity().user_id == "user_workos_123"
        assert engine_helpers._current_actor() == "user_workos_123"
    finally:
        mw.auth_middleware(None)  # reset request-local identity to ANONYMOUS
        mw.set_provider(None)




# --- resolve-always write enforcement (P1: anon cannot create/write) -------
# WorkOS mode is resolve-always: anonymous may read public surfaces, but every
# write/create/costly/admin action requires an authenticated founder + grant.
# (The per-universe ACL layer confines a founder to their OWN universe.)


@pytest.fixture
def workos_active(keypair, monkeypatch):
    from tinyassets.auth import middleware as mw

    monkeypatch.delenv("UNIVERSE_SERVER_USER", raising=False)
    mw.set_provider(_provider(keypair))
    try:
        yield keypair
    finally:
        mw.auth_middleware(None)
        mw.set_provider(None)






























@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_invalid_bearer_token_is_rejected_not_anonymous(workos_active) -> None:
    # A present-but-invalid token must set the None (401) signal, not anon.
    from tinyassets.auth import middleware as mw

    mw.auth_middleware("not-a-real-token")
    assert mw._current_identity.get() is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_missing_token_binds_nobody(workos_active) -> None:
    from tinyassets.auth import middleware as mw

    mw.auth_middleware(None)
    assert mw.current_identity_or_none() is None


@pytest.mark.verifies(
    'identity-auth-and-access-control:'
    'Bearer JWT validation is fail-closed, RS256-pinned, and audience-bound',
)
def test_invalid_bearer_token_gets_401_challenge(workos_active) -> None:
    from starlette.applications import Starlette
    from starlette.responses import PlainTextResponse
    from starlette.routing import Route
    from starlette.testclient import TestClient

    from tinyassets.auth.middleware import AuthContextMiddleware

    async def ok(request):  # noqa: ANN001, ANN202
        return PlainTextResponse("ok")

    app = AuthContextMiddleware(Starlette(routes=[Route("/x", ok)]))
    client = TestClient(app)

    # invalid token -> 401 with a resource_metadata challenge
    r = client.get("/x", headers={"Authorization": "Bearer bad-token"})
    assert r.status_code == 401
    assert "resource_metadata" in r.headers.get("www-authenticate", "")

    # no token -> anonymous public read still works
    assert client.get("/x").status_code == 200

    # valid founder token -> forwarded
    r3 = client.get("/x", headers={"Authorization": f"Bearer {_sign(workos_active)}"})
    assert r3.status_code == 200
