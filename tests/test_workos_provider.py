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
from tinyassets.auth.provider import Identity, create_provider
from tinyassets.auth.workos_provider import (
    WorkOSAuthProvider,
    derive_endpoints,
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
        "iss": ISSUER,
        "iat": now,
        "exp": now + 3600,
        "email": "founder@example.com",
    }
    payload.update(claims)
    return jwt.encode(payload, keypair, algorithm="RS256", headers={"kid": "test"})


# --- derive_endpoints ------------------------------------------------------


def test_derive_endpoints_bare_domain() -> None:
    issuer, jwks = derive_endpoints("inventive-van-62-staging.authkit.app")
    assert issuer == ISSUER
    assert jwks == f"{ISSUER}/oauth2/jwks"


def test_derive_endpoints_full_origin_and_trailing_slash() -> None:
    issuer, jwks = derive_endpoints("https://foo.authkit.app/")
    assert issuer == "https://foo.authkit.app"
    assert jwks == "https://foo.authkit.app/oauth2/jwks"


def test_derive_endpoints_rejects_empty() -> None:
    with pytest.raises(ValueError):
        derive_endpoints("   ")


# --- resolve_token: happy path --------------------------------------------


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


def test_only_verified_tokens_supply_opaque_connection_metadata(keypair):
    token = _sign(keypair, sid="fixture-session")
    ident = _provider(keypair).resolve_token(token)
    connection = ident.metadata["oauth_connection"]
    assert connection["identity_source"] == "verified_session"
    assert len(connection["id"]) == 64
    assert "fixture-session" not in str(connection) and token not in str(ident.metadata)
    assert _provider(keypair).resolve_token(token + "invalid") is None


def test_token_permissions_become_capabilities(keypair) -> None:
    # RBAC permissions in the token add capabilities beyond the base — e.g.
    # platform `admin`, which is never implicit.
    ident = _provider(keypair).resolve_token(
        _sign(keypair, permissions=["admin"])
    )
    assert ident is not None
    assert "admin" in ident.capabilities
    assert "read" in ident.capabilities  # base still present
    assert ident.metadata["permissions"] == ["admin"]


def test_username_falls_back_to_sub_without_email(keypair) -> None:
    ident = _provider(keypair).resolve_token(_sign(keypair, email=""))
    assert ident is not None
    assert ident.username == "user_workos_123"


def test_org_role_permissions_surface_in_metadata(keypair) -> None:
    token = _sign(
        keypair,
        org_id="org_1",
        role="admin",
        permissions=["a", "b"],
    )
    ident = _provider(keypair).resolve_token(token)
    assert ident is not None
    assert ident.metadata["org_id"] == "org_1"
    assert ident.metadata["role"] == "admin"
    assert ident.metadata["permissions"] == ["a", "b"]


# --- resolve_token: rejections --------------------------------------------


def test_expired_token_rejected(keypair) -> None:
    now = int(time.time())
    token = _sign(keypair, iat=now - 7200, exp=now - 3600)
    assert _provider(keypair).resolve_token(token) is None


def test_missing_sub_rejected(keypair) -> None:
    # require=["exp","sub"] -> PyJWT raises MissingRequiredClaim.
    assert _provider(keypair).resolve_token(_sign(keypair, sub="")) is None


def test_anonymous_sub_rejected(keypair) -> None:
    assert _provider(keypair).resolve_token(_sign(keypair, sub="anonymous")) is None


def test_wrong_issuer_rejected(keypair) -> None:
    token = _sign(keypair, iss="https://evil.example.com")
    assert _provider(keypair).resolve_token(token) is None


def test_bad_signature_rejected(keypair, other_key) -> None:
    # Token signed by a different key than the provider's JWKS exposes.
    token = jwt.encode(
        {"sub": "x", "iss": ISSUER, "exp": int(time.time()) + 3600},
        other_key,
        algorithm="RS256",
        headers={"kid": "test"},
    )
    assert _provider(keypair).resolve_token(token) is None


def test_empty_token_returns_none(keypair) -> None:
    p = _provider(keypair)
    assert p.resolve_token("") is None
    assert p.resolve_token("   ") is None


@pytest.mark.parametrize(
    ("error", "category"),
    [
        (jwt.ExpiredSignatureError("sensitive-expiry-detail"), "expired"),
        (jwt.InvalidAudienceError("sensitive-audience-detail"), "audience"),
        (jwt.InvalidIssuerError("sensitive-issuer-detail"), "issuer"),
        (jwt.MissingRequiredClaimError("sensitive-claim-name"), "required_claim"),
        (jwt.InvalidSignatureError("sensitive-signature-detail"), "signature"),
        (jwt.InvalidAlgorithmError("sensitive-algorithm-detail"), "algorithm"),
        (jwt.DecodeError("sensitive-decode-detail"), "malformed"),
        (jwt.InvalidTokenError("sensitive-token-detail"), "invalid_token"),
    ],
)
def test_validation_failure_logs_only_allowlisted_category(
    keypair,
    monkeypatch,
    caplog,
    isolated_rejection_log,
    error: jwt.PyJWTError,
    category: str,
) -> None:
    def _reject(*args, **kwargs):
        raise error

    monkeypatch.setattr(jwt, "decode", _reject)
    caplog.set_level(logging.WARNING, logger="universe_server.auth.workos")
    token = _sign(keypair)

    assert _provider(keypair).resolve_token(token) is None
    assert f"category={category}" in caplog.text
    assert "sensitive-" not in caplog.text
    assert token not in caplog.text


def test_signing_key_failure_logs_no_exception_or_token(
    keypair,
    caplog,
    isolated_rejection_log,
) -> None:
    token = _sign(keypair, email="sensitive-user@example.com")
    provider = WorkOSAuthProvider(
        issuer=ISSUER,
        jwks_uri="https://example.invalid/oauth2/jwks",
        jwks_client=_FailingJWKSClient(),
    )
    caplog.set_level(logging.WARNING, logger="universe_server.auth.workos")

    assert provider.resolve_token(token) is None
    assert "category=signing_key" in caplog.text
    assert "sensitive-" not in caplog.text
    assert token not in caplog.text


def test_invalid_subject_logs_sanitized_category(
    keypair,
    caplog,
    isolated_rejection_log,
) -> None:
    token = _sign(keypair, sub="anonymous")
    caplog.set_level(logging.WARNING, logger="universe_server.auth.workos")

    assert _provider(keypair).resolve_token(token) is None
    assert "category=invalid_subject" in caplog.text
    assert token not in caplog.text


def test_rejection_logs_are_bounded_per_category(
    keypair,
    monkeypatch,
    caplog,
    isolated_rejection_log,
) -> None:
    times = iter((100.0, 100.0, 161.0))
    monkeypatch.setattr(
        workos_provider_module,
        "_rejection_log_now",
        lambda: next(times),
        raising=False,
    )
    provider = WorkOSAuthProvider(
        issuer=ISSUER,
        jwks_uri="https://example.invalid/oauth2/jwks",
        jwks_client=_FailingJWKSClient(),
    )
    caplog.set_level(logging.WARNING, logger="universe_server.auth.workos")

    for _ in range(3):
        assert provider.resolve_token(_sign(keypair)) is None

    records = [
        record
        for record in caplog.records
        if "category=signing_key" in record.getMessage()
    ]
    assert len(records) == 2
    assert "suppressed=0" in records[0].getMessage()
    assert "suppressed=1" in records[1].getMessage()


def test_real_key_client_classifies_malformed_token_before_jwks_lookup(
    caplog,
    isolated_rejection_log,
) -> None:
    provider = WorkOSAuthProvider(
        issuer=ISSUER,
        jwks_uri="https://example.invalid/oauth2/jwks",
    )
    caplog.set_level(logging.WARNING, logger="universe_server.auth.workos")

    assert provider.resolve_token("not-a-jwt") is None
    assert "category=malformed" in caplog.text
    assert "category=signing_key" not in caplog.text
    assert "not-a-jwt" not in caplog.text


# --- audience binding ------------------------------------------------------


def test_audience_enforced_when_configured(keypair) -> None:
    p = _provider(keypair, audience=RESOURCE)
    assert p.resolve_token(_sign(keypair, aud=RESOURCE)) is not None
    assert p.resolve_token(_sign(keypair, aud="https://other")) is None
    assert p.resolve_token(_sign(keypair)) is None  # no aud at all


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


def test_outside_store_failure_is_logged_not_swallowed(
    keypair, monkeypatch, caplog, isolated_rejection_log,
) -> None:
    import sqlite3

    monkeypatch.setenv("TINYASSETS_OUTSIDE_CLIENT_CLAIM", "client_id")
    monkeypatch.setenv("TINYASSETS_FIRST_PARTY_CLIENTS", "[]")

    class _Boom:
        def observe(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(
        "tinyassets.outside_authority.current_store",
        lambda: _Boom(),
    )
    token = _sign(
        keypair,
        aud=RESOURCE,
        client_id="external-client",
        sid="session-1",
        auth_time=100,
    )
    caplog.set_level(logging.WARNING, logger="universe_server.auth.workos")

    assert _provider(keypair, audience=RESOURCE).resolve_token(token) is None
    assert "category=outside_store" in caplog.text
    assert "unable to open database file" not in caplog.text
    assert token not in caplog.text


def test_first_party_sign_in_does_not_consult_the_outside_store(
    keypair, monkeypatch,
) -> None:
    import json
    import sqlite3

    monkeypatch.setenv("TINYASSETS_OUTSIDE_CLIENT_CLAIM", "client_id")
    monkeypatch.setenv(
        "TINYASSETS_FIRST_PARTY_CLIENTS",
        json.dumps([f"{ISSUER}|claude"]),
    )

    class _Boom:
        def observe(self, *_args, **_kwargs):
            raise sqlite3.OperationalError("unable to open database file")

    monkeypatch.setattr(
        "tinyassets.outside_authority.current_store",
        lambda: _Boom(),
    )
    token = _sign(keypair, aud=RESOURCE, client_id="claude", sid="session-1", auth_time=100)
    ident = _provider(keypair, audience=RESOURCE).resolve_token(token)
    assert ident is not None
    assert ident.user_id == "user_workos_123"
    assert "outside_origin" not in ident.metadata


def test_audience_skipped_when_constructed_without_audience(keypair) -> None:
    # Direct construction with audience=None (dev/test) -> tokens without aud
    # still validate. The PRODUCTION entry point (from_env) fails closed instead;
    # see test_from_env_requires_audience_by_default.
    assert _provider(keypair).resolve_token(_sign(keypair)) is not None


# --- provider contract -----------------------------------------------------


def test_is_auth_required_false(keypair) -> None:
    # Slice 1 never rejects anonymous; gating is slice 2.
    assert _provider(keypair).is_auth_required() is False


def test_flow_methods_are_not_ours(keypair) -> None:
    p = _provider(keypair)
    with pytest.raises(NotImplementedError):
        p.register_client({})
    with pytest.raises(NotImplementedError):
        p.create_authorization("c", "r", "s", "st", "cc", "S256")
    with pytest.raises(NotImplementedError):
        p.exchange_code("code", "c", "r", "v")


# --- from_env / factory ----------------------------------------------------


def test_from_env_requires_audience_by_default(monkeypatch) -> None:
    # F1: without WORKOS_MCP_RESOURCE (and no explicit dev override), from_env
    # fails closed rather than accepting any same-issuer token.
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", "foo.authkit.app")
    monkeypatch.delenv("WORKOS_MCP_RESOURCE", raising=False)
    monkeypatch.delenv("WORKOS_ALLOW_NO_AUDIENCE", raising=False)
    with pytest.raises(RuntimeError, match="WORKOS_MCP_RESOURCE"):
        WorkOSAuthProvider.from_env()


def test_from_env_allow_no_audience_override_derives_endpoints(monkeypatch) -> None:
    # The dev-only opt-out keeps the no-audience derivation path usable locally.
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", "inventive-van-62-staging.authkit.app")
    monkeypatch.delenv("WORKOS_MCP_RESOURCE", raising=False)
    monkeypatch.setenv("WORKOS_ALLOW_NO_AUDIENCE", "1")
    p = WorkOSAuthProvider.from_env()
    assert p._issuer == ISSUER
    assert p._jwks_uri == f"{ISSUER}/oauth2/jwks"
    assert p._audience is None


def test_from_env_reads_resource_audience(monkeypatch) -> None:
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", "foo.authkit.app")
    monkeypatch.setenv("WORKOS_MCP_RESOURCE", RESOURCE)
    monkeypatch.delenv("WORKOS_ALLOW_NO_AUDIENCE", raising=False)
    assert WorkOSAuthProvider.from_env()._audience == RESOURCE


def test_from_env_requires_domain(monkeypatch) -> None:
    monkeypatch.delenv("WORKOS_AUTHKIT_DOMAIN", raising=False)
    with pytest.raises(RuntimeError):
        WorkOSAuthProvider.from_env()


def test_create_provider_selects_workos(monkeypatch) -> None:
    monkeypatch.setenv("UNIVERSE_SERVER_AUTH", "workos")
    monkeypatch.setenv("WORKOS_AUTHKIT_DOMAIN", "foo.authkit.app")
    # Audience binding required by default; supply the dev override so the
    # factory selection itself is what's under test (not the F1 guard).
    monkeypatch.setenv("WORKOS_ALLOW_NO_AUDIENCE", "1")
    assert isinstance(create_provider(), WorkOSAuthProvider)


# --- integration: the whole point of slice 1 ------------------------------


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


def test_middleware_anonymous_without_token(keypair, monkeypatch) -> None:
    from tinyassets.auth import middleware as mw

    monkeypatch.delenv("UNIVERSE_SERVER_USER", raising=False)
    mw.set_provider(_provider(keypair))
    try:
        assert mw.auth_middleware(None) is None
        assert mw.current_identity_or_none() is None
        with pytest.raises(PermissionError):
            mw.current_identity()
    finally:
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


def test_workos_provider_resolve_always_writes(keypair) -> None:
    assert _provider(keypair).resolve_always_writes() is True
    # is_auth_required stays False so anonymous reads are never rejected.
    assert _provider(keypair).is_auth_required() is False


def test_workos_anonymous_cannot_create_universe(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    auth_middleware(None)  # anonymous
    with pytest.raises(PermissionError):
        require_action_scope("universe", "create_universe")


def test_workos_anonymous_cannot_write_wiki(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    auth_middleware(None)
    with pytest.raises(PermissionError):
        require_action_scope("wiki", "write")


def test_workos_nobody_cannot_read(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    auth_middleware(None)
    # A read used to be open to nobody. There is no anonymous reader now.
    with pytest.raises(PermissionError, match="Authentication required"):
        require_action_scope("wiki", "read")


def test_workos_authenticated_founder_can_create(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    keypair = workos_active
    # Founder token granting write/costly RBAC scopes.
    auth_middleware(_sign(keypair, permissions=["read", "write", "costly"]))
    ident = require_action_scope("universe", "create_universe")
    assert ident.user_id == "user_workos_123"


def test_workos_authenticated_founder_can_write_wiki(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    keypair = workos_active
    auth_middleware(_sign(keypair, permissions=["read", "write", "costly"]))
    ident = require_action_scope("wiki", "write")
    assert ident.user_id == "user_workos_123"


def test_workos_authenticated_founder_passes_write_scope_gate(workos_active) -> None:
    # OAuth best practice + multi-tenant ownership: the token authenticates; the
    # scope gate authorizes an authenticated founder for the coarse delegated
    # write/costly capability, and the REAL boundary is the per-universe ownership
    # ACL (see test_universe_write_boundary / test_universe_server_isolation) that
    # confines a founder to universes they own. So being authenticated is enough
    # to pass the scope gate for write + create_universe.
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    keypair = workos_active
    auth_middleware(_sign(keypair, permissions=["tinyassets.wiki.read"]))
    assert require_action_scope("wiki", "write").user_id == "user_workos_123"
    assert require_action_scope("universe", "create_universe").user_id == "user_workos_123"


def test_workos_founder_with_read_scope_can_read(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    keypair = workos_active
    auth_middleware(_sign(keypair, permissions=["tinyassets.wiki.read"]))
    ident = require_action_scope("wiki", "read")
    assert ident.user_id == "user_workos_123"


def test_goals_actions_are_in_scope_registry() -> None:
    from tinyassets.auth.provider import action_scope_for

    assert action_scope_for("goals", "propose").effect == "write"
    assert action_scope_for("goals", "set_canonical").effect == "write"
    assert action_scope_for("goals", "list").effect == "read"
    assert action_scope_for("goals", "run_canonical").effect == "costly"


def test_workos_anonymous_cannot_write_goals(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    auth_middleware(None)  # anonymous
    for action in (
        "propose", "update", "bind", "set_canonical",
        "define_protocol", "set_selector",
    ):
        with pytest.raises(PermissionError):
            require_action_scope("goals", action)


def test_workos_nobody_cannot_list_goals(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    auth_middleware(None)
    with pytest.raises(PermissionError, match="Authentication required"):
        require_action_scope("goals", "list")


def test_workos_founder_with_write_can_propose_goal(workos_active) -> None:
    from tinyassets.auth.middleware import auth_middleware, require_action_scope

    keypair = workos_active
    auth_middleware(_sign(keypair, permissions=["read", "write", "costly"]))
    ident = require_action_scope("goals", "propose")
    assert ident.user_id == "user_workos_123"


def test_gates_record_conformance_pack_is_write_scoped(workos_active) -> None:
    # record_conformance_pack is a durable write; it must be write-classified so
    # anonymous resolve-always callers are denied.
    from tinyassets.auth.middleware import auth_middleware, require_action_scope
    from tinyassets.auth.provider import action_scope_for

    assert action_scope_for("gates", "record_conformance_pack").effect == "write"
    auth_middleware(None)  # anonymous
    with pytest.raises(PermissionError):
        require_action_scope("gates", "record_conformance_pack")


def test_goals_impl_denies_anonymous_propose(
    workos_active, tmp_path, monkeypatch,
) -> None:
    import json

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    from tinyassets.api.market import goals as _goals_impl
    from tinyassets.auth.middleware import auth_middleware

    auth_middleware(None)  # anonymous WorkOS-mode caller
    out = json.loads(_goals_impl(action="propose", name="anon bypass goal"))
    assert out.get("auth_scope_required") is True
    assert out.get("tool") == "goals"


def test_invalid_bearer_token_is_rejected_not_anonymous(workos_active) -> None:
    # A present-but-invalid token must set the None (401) signal, not anon.
    from tinyassets.auth import middleware as mw

    mw.auth_middleware("not-a-real-token")
    assert mw._current_identity.get() is None


def test_missing_token_binds_nobody(workos_active) -> None:
    from tinyassets.auth import middleware as mw

    mw.auth_middleware(None)
    assert mw.current_identity_or_none() is None


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
