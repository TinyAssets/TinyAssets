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
    UID,
    VERIFIER,
    _broker,
    _call,
    _connected,
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
