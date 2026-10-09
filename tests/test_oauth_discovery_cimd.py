"""Unknown-service OAuth regressions; loopback only, no provider registration."""

import json
from urllib.parse import parse_qsl, urlsplit

import httpx
import pytest

from tests import test_generic_oauth_connections as fixtures
from tests.test_generic_oauth_connections import (
    API,
    AUTH,
    OWNER,
    TASKS_ASK,
    TOKEN,
    UID,
    _as,
    _ask,
    _broker,
    _get,
    _sign_in,
    _vault_bundle,
)
from tinyassets.connection_oauth import discovery, tokens
from tinyassets.connection_oauth.transport import OAuthError

app = fixtures.app
provider = fixtures.provider
universes = fixtures.universes


def metadata(issuer="https://auth.example/tenant"):
    return {
        "issuer": issuer,
        "authorization_endpoint": issuer + "/authorize",
        "token_endpoint": issuer + "/token",
        "response_types_supported": ["code"],
        "code_challenge_methods_supported": ["S256"],
        "client_id_metadata_document_supported": True,
        "scopes_supported": ["openid"],
    }


def test_exact_issuer_including_trailing_slash(monkeypatch):
    monkeypatch.setattr(discovery, "request_json", lambda *a, **k: (200, metadata()))
    with pytest.raises(OAuthError, match="issuer_mismatch"):
        discovery.fetch_server_metadata("https://auth.example/tenant/")


def test_path_oidc_discovery_order():
    assert discovery._metadata_urls("https://auth.example/tenant") == [
        "https://auth.example/.well-known/oauth-authorization-server/tenant",
        "https://auth.example/.well-known/openid-configuration/tenant",
        "https://auth.example/tenant/.well-known/openid-configuration",
    ]


def test_challenge_parser_handles_multiple_schemes_and_quoted_comma():
    assert discovery.bearer_challenge(
        'Basic realm="other", Bearer realm="a,b", '
        'resource_metadata="https://api.example/meta", scope="tools.read tools.write"'
    ) == {
        "realm": "a,b",
        "resource_metadata": "https://api.example/meta",
        "scope": "tools.read tools.write",
    }


def test_resource_and_scope_survive_exchange_encoding_and_refresh(monkeypatch):
    forms = []

    def request(method, url, **kw):
        forms.append(kw["form"])
        return 200, {"access_token": "access", "refresh_token": "refresh"}

    monkeypatch.setattr(tokens, "request_json", request)
    bundle = tokens.exchange_code(
        token_url="https://auth.example/token",
        client_id="c",
        code="code",
        verifier="v" * 43,
        redirect_uri="https://tinyassets.io/callback",
        resource="https://api.example/mcp",
        issuer="https://auth.example",
        scope="tools.read",
    )
    bundle = tokens.refresh(tokens.decode(tokens.encode(bundle)))
    assert bundle.resource == "https://api.example/mcp"
    assert bundle.issuer == "https://auth.example"
    assert bundle.scope == "tools.read"
    assert [f["resource"] for f in forms] == [bundle.resource, bundle.resource]


@pytest.mark.parametrize(
    ("error", "code"),
    [
        ("invalid_client", "registration_required"),
        ("invalid_grant", "reconnect_required"),
        ("invalid_scope", "insufficient_scope"),
    ],
)
def test_recoverable_token_errors(monkeypatch, error, code):
    monkeypatch.setattr(tokens, "request_json", lambda *a, **k: (400, {"error": error}))
    with pytest.raises(OAuthError, match=code):
        tokens.refresh(tokens.TokenBundle("a", "https://auth.example/token", "c", "r"))


@pytest.mark.parametrize("named", ["https://api.example/other", "https://evil.example/mcp"])
def test_resource_identity_rejected_before_authorization(monkeypatch, named):
    monkeypatch.setattr(
        discovery,
        "request_json",
        lambda *a, **k: (
            200,
            {"resource": named, "authorization_servers": ["https://auth.example"]},
        ),
    )
    with pytest.raises(OAuthError, match="protected_resource_mismatch"):
        discovery.protected_resource_issuers("api.example")


def test_registration_unavailable_is_actionable(provider, monkeypatch):
    original = provider.route

    def route(*args):
        status, doc = original(*args)
        if isinstance(doc, dict):
            doc.pop("registration_endpoint", None)
        return status, doc

    monkeypatch.setattr(provider, "route", route)
    assert discovery.resolve_offer({}, [API]) == (None, "registration_required")


def test_rejected_dcr_is_registration_required(monkeypatch):
    monkeypatch.setattr(
        discovery, "request_json", lambda *a, **k: (403, {"error": "access_denied"})
    )
    with pytest.raises(OAuthError, match="registration_required"):
        discovery.register_public_client(
            "https://auth.example/register",
            redirect_uri="https://tinyassets.io/app/model-callback/connect",
        )


def test_registration_persistence_is_issuer_and_callback_keyed(universes):
    from tinyassets.connection_oauth.pkce import (
        cached_client,
        forget_client,
        remember_client,
    )

    remember_client("https://auth.example/a", "https://tinyassets.io/cb", "a-client", "dcr")
    remember_client("https://auth.example/b", "https://tinyassets.io/cb", "b-client", "dcr")
    assert cached_client("https://auth.example/a", "https://tinyassets.io/cb") == "a-client"
    assert cached_client("https://auth.example/b", "https://tinyassets.io/cb") == "b-client"
    assert cached_client("https://auth.example/a/", "https://tinyassets.io/cb") == ""
    assert cached_client("https://auth.example/a", "https://tinyassets.io/changed") == ""
    forget_client("https://auth.example/a", "a-client")
    assert cached_client("https://auth.example/a", "https://tinyassets.io/cb") == ""
    assert cached_client("https://auth.example/b", "https://tinyassets.io/cb") == "b-client"


def test_isolated_child_reads_public_registration_via_daemon(monkeypatch):
    from tinyassets.connection_oauth import pkce, service

    monkeypatch.setattr(service, "inherited_config", lambda: {"port": 123, "token": "cap"})
    seen = []

    def call(config, doc):
        seen.append(doc)
        return {"client_id": "public-client"}

    monkeypatch.setattr(service, "call", call)
    monkeypatch.setattr(pkce, "flows_db", lambda *a: pytest.fail("child opened daemon DB"))
    assert pkce.cached_client("https://auth.example", "https://tinyassets.io/cb") == "public-client"
    assert seen == [
        {
            "op": "public_client",
            "issuer": "https://auth.example",
            "redirect_uri": "https://tinyassets.io/cb",
        }
    ]


def test_daemon_returns_only_public_identity(app):
    from tinyassets.connection_oauth import pkce, service

    pkce.remember_client("https://auth.example", "https://tinyassets.io/cb", "public-client", "dcr")
    assert service._dispatch(
        (app / UID, OWNER),
        {
            "op": "public_client",
            "issuer": "https://auth.example",
            "redirect_uri": "https://tinyassets.io/cb",
        },
    ) == {"client_id": "public-client"}


def test_public_cache_expires_even_without_an_authorization_callback(universes, monkeypatch):
    from tinyassets.connection_oauth import pkce

    now = pkce.time.time()
    pkce.remember_client("https://auth.example", "https://tinyassets.io/cb", "old", "dcr")
    assert pkce.cached_client("https://auth.example", "https://tinyassets.io/cb") == "old"
    monkeypatch.setattr(pkce.time, "time", lambda: now + 3601)
    assert pkce.cached_client("https://auth.example", "https://tinyassets.io/cb") == ""


def test_root_challenge_names_parent_resource(monkeypatch):
    from tinyassets.connection_oauth import transport

    monkeypatch.setattr(
        transport,
        "request_json_with_headers",
        lambda *a, **k: (
            401,
            {},
            {
                "www-authenticate": 'Bearer resource_metadata="https://api.example/.well-known/oauth-protected-resource"'
            },
        ),
    )
    monkeypatch.setattr(
        discovery,
        "request_json",
        lambda *a, **k: (
            200,
            {"resource": "https://api.example/", "authorization_servers": ["https://auth.example"]},
        ),
    )
    assert discovery._resource_metadata("https://api.example/mcp", probe=True) == (
        ["https://auth.example"],
        [],
        "https://api.example/",
    )


def test_legacy_endpoint_uses_origin_authorization_metadata(monkeypatch):
    monkeypatch.setattr(discovery, "_resource_metadata", lambda *a, **k: ([], [], ""))
    seen = []

    def fetch(issuer):
        seen.append(issuer)
        return "metadata"

    monkeypatch.setattr(discovery, "fetch_server_metadata", fetch)
    assert discovery._metadata_for(["https://api.example/mcp"])[0] == "metadata"
    assert seen == ["https://api.example"]


@pytest.mark.parametrize("probe_error", ["timeout", "malformed_challenge"])
@pytest.mark.parametrize("root_metadata", [False, True])
def test_full_url_probe_error_preserves_root_fallback(monkeypatch, probe_error, root_metadata):
    from tinyassets.connection_oauth import transport

    origin = "https://api.example"
    issuer = "https://auth.example" if root_metadata else origin
    fetched = []

    def probe(*args, **kwargs):
        if probe_error == "timeout":
            raise OAuthError("oauth_server_unreachable")
        return 401, {}, {
            "www-authenticate": "Bearer error=invalid_request, error_description=Missing token"
        }

    def request(method, url, **kwargs):
        fetched.append(url)
        if url == origin + "/.well-known/oauth-protected-resource" and root_metadata:
            return 200, {"resource": origin, "authorization_servers": [issuer]}
        if url == issuer + "/.well-known/oauth-authorization-server":
            return 200, metadata(issuer)
        return 404, {}

    monkeypatch.setattr(transport, "request_json_with_headers", probe)
    monkeypatch.setattr(discovery, "request_json", request)
    bare = discovery._metadata_for(["api.example"])[0]
    fetched.clear()
    full, resource, _ = discovery._metadata_for([origin + "/v1/items"])
    assert full == bare
    assert full.issuer == issuer
    assert origin + "/.well-known/oauth-protected-resource" in fetched
    assert resource == (origin if root_metadata else origin + "/v1/items")


def test_shared_registration_does_not_limit_second_owner_scopes(provider, app, monkeypatch):
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    original_route, original_authorize = provider.route, provider.authorize
    registered_scopes, authorized = {}, []

    def route(method, host, path, headers, body):
        status, doc = original_route(method, host, path, headers, body)
        if host == AUTH and path == "/register" and method == "POST":
            registration = json.loads(body)
            registered_scopes[doc["client_id"]] = (
                set(registration["scope"].split()) if "scope" in registration else None
            )
        return status, doc

    def authorize(url):
        query = dict(parse_qsl(urlsplit(url).query))
        scopes = set(query["scope"].split())
        allowed = registered_scopes[query["client_id"]]
        assert allowed is None or scopes <= allowed, "registration rejects invalid_scope"
        authorized.append((query["client_id"], scopes))
        return original_authorize(url)

    monkeypatch.setattr(provider, "route", route)
    monkeypatch.setattr(provider, "authorize", authorize)
    for owner, uid, scopes in (
        (OWNER, UID, ["read"]),
        (fixtures.OTHER, fixtures.OTHER_UID, ["read", "write"]),
    ):
        with _as(owner):
            asked = _ask(uid=uid, action={**TASKS_ASK, "oauth": {"scopes": scopes}})
        assert _sign_in(provider, asked["request_id"], owner=owner)[2].status_code == 200
    assert len(registered_scopes) == 1
    assert authorized == [
        (authorized[0][0], {"read"}),
        (authorized[0][0], {"read", "write"}),
    ]


def test_bad_first_resource_does_not_mask_second_host(monkeypatch):
    def resource(url, **kw):
        if "first" in url:
            raise OAuthError("protected_resource_mismatch")
        return ["https://auth.example"], [], url

    monkeypatch.setattr(discovery, "_resource_metadata", resource)
    monkeypatch.setattr(discovery, "fetch_server_metadata", lambda issuer: "metadata")
    assert (
        discovery._metadata_for(["https://first.example/mcp", "https://second.example/mcp"])[1]
        == "https://second.example/mcp"
    )


def test_cache_failure_is_a_recoverable_offer_reason(provider, monkeypatch):
    from tinyassets.connection_oauth import pkce

    def unavailable(*args):
        raise OAuthError("platform_client_unavailable")

    monkeypatch.setattr(pkce, "cached_client", unavailable)
    assert discovery.resolve_offer({}, [API]) == (None, "platform_client_unavailable")


def test_null_scope_retains_grant_scope(monkeypatch):
    monkeypatch.setattr(
        tokens, "request_json", lambda *a, **k: (200, {"access_token": "fresh", "scope": None})
    )
    fresh = tokens.refresh(
        tokens.TokenBundle("a", "https://auth.example/token", "c", "r", scope="tools.read")
    )
    assert fresh.scope == "tools.read"


def test_expired_registration_on_pending_card_registers_again(provider, app, monkeypatch):
    from tinyassets.connection_oauth import pkce

    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    with _as(OWNER):
        first = _ask()
    assert _sign_in(provider, first["request_id"])[2].status_code == 200
    with _as(OWNER):
        second = _ask(action={**TASKS_ASK, "destination": "second"})
    assert second["action"]["oauth"]["registration_method"] == "cached"
    now = pkce.time.time()
    monkeypatch.setattr(pkce.time, "time", lambda: now + 3601)
    assert _sign_in(provider, second["request_id"])[2].status_code == 200
    assert len(provider.clients) == 2


@pytest.mark.parametrize("registration", ["cimd", "dcr", "existing"])
@pytest.mark.parametrize("discovery_route", ["challenge", "path"])
def test_unlisted_mcp_through_connect_card(
    provider, app, monkeypatch, tmp_path, registration, discovery_route
):
    """Real loopback HTTP, real card/PKCE/vault/broker, unlisted MCP tools."""
    from tinyassets.api.http_connection import _ids
    from tinyassets.connection_oauth.pkce import cached_client

    endpoint = f"https://{API}/tenants/a/mcp"
    issuer = f"https://{AUTH}/tenant"
    callback = "https://tinyassets.io/app/model-callback/connect"
    cimd = "https://tinyassets.io/app/oauth/client-metadata.json"
    monkeypatch.setenv("UNIVERSE_SERVER_URL", "https://tinyassets.io")
    original = provider.route
    original_authorize = provider.authorize
    forms, registrations, calls, fetched = [], [], [], []
    provider.clients["approved-client"] = [callback]

    def route(method, host, path, headers, body):
        provider.response_headers = {}
        fetched.append((host, path))
        if host == AUTH and method == "GET" and urlsplit(path).path == "/authorize":
            provider.response_headers = {"Location": original_authorize(f"https://{AUTH}{path}")}
            return 302, None
        if host == API and path == "/tenants/a/mcp":
            token = headers.get("Authorization", "").removeprefix("Bearer ")
            if provider.access.get(token, 0) == 0:
                if discovery_route == "challenge":
                    provider.response_headers = {
                        "WWW-Authenticate": (
                            f'Bearer resource_metadata="https://{API}/metadata/a", '
                            'scope="tools.read"'
                        )
                    }
                return 401, {"error": "invalid_token"}
            packet = json.loads(body)
            calls.append(packet["method"])
            result = {
                "initialize": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": "unlisted", "version": "1"},
                },
                "tools/list": {"tools": [{"name": "hello", "inputSchema": {"type": "object"}}]},
                "tools/call": {"content": [{"type": "text", "text": "hello from unlisted"}]},
            }.get(packet["method"])
            if packet["method"] == "notifications/initialized":
                return 202, None
            return 200, {"jsonrpc": "2.0", "id": packet["id"], "result": result}
        meta_path = (
            "/metadata/a"
            if discovery_route == "challenge"
            else "/.well-known/oauth-protected-resource/tenants/a/mcp"
        )
        if host == API and path == meta_path:
            return 200, {
                "resource": endpoint,
                "authorization_servers": [issuer],
                "scopes_supported": ["tools.read"],
            }
        if host == AUTH and path == "/.well-known/oauth-authorization-server/tenant":
            return 200, {
                **metadata(issuer),
                "authorization_endpoint": f"https://{AUTH}/authorize",
                "token_endpoint": f"https://{TOKEN}/token",
                "registration_endpoint": f"https://{AUTH}/register",
                "client_id_metadata_document_supported": registration != "dcr",
            }
        if host == AUTH and path == "/register":
            registrations.append(json.loads(body))
            assert registrations[-1]["application_type"] == "web"
        if host == TOKEN:
            form = dict(parse_qsl(body.decode()))
            forms.append(form)
            assert form["resource"] == endpoint
        status, doc = original(method, host, path, headers, body)
        if host == TOKEN and status == 200:
            doc.pop("scope", None)  # Omitted scope means the authorized requested scope.
        return status, doc

    def authorize(url):
        query = dict(parse_qsl(urlsplit(url).query))
        assert query["resource"] == endpoint and query["scope"] == "tools.read"
        if registration == "cimd":
            assert query["client_id"] == cimd
            document = _get(urlsplit(cimd).path).json()
            assert document["client_id"] == cimd
            assert document["token_endpoint_auth_method"] == "none"
            provider.clients[cimd] = document["redirect_uris"]
        response = httpx.get(  # hermetic-ok: fixture-owned loopback with logical AS Host
            f"http://127.0.0.1:{provider.port}/authorize?{urlsplit(url).query}",
            headers={"Host": AUTH},
            follow_redirects=False,
        )
        assert response.status_code == 302
        return response.headers["location"]

    monkeypatch.setattr(provider, "route", route)
    monkeypatch.setattr(provider, "authorize", authorize)
    oauth = {"client_id": "approved-client"} if registration == "existing" else {}
    with _as(OWNER):
        asked = _ask(
            action={**TASKS_ASK, "path_template": "/tenants/a/mcp", "oauth": oauth}, fields=[]
        )
    assert asked.get("primary") == "sign_in", asked
    assert asked["action"]["oauth"]["source"] == "discovered"
    assert asked["action"]["oauth"]["scopes"] == ["tools.read"]
    _, _, done = _sign_in(provider, asked["request_id"])
    assert done.status_code == 200, done.text
    bundle = _vault_bundle(app)
    assert bundle.resource == endpoint and bundle.issuer == issuer and bundle.scope == "tools.read"
    assert cached_client(issuer, callback) == (bundle.client_id if registration == "dcr" else "")
    assert cached_client(issuer + "/", callback) == ""
    refreshed = tokens.refresh(bundle)
    assert refreshed.resource == endpoint and len(forms) == 2
    assert len(registrations) == (1 if registration == "dcr" else 0)
    _, grant_id = _ids(universe_id=UID, destination="tasklark")
    dispatch = _broker(app, UID, OWNER, grant_id, tmp_path / "runtime")
    for number, method in enumerate(
        ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    ):
        packet = {"jsonrpc": "2.0", "method": method, "params": {}}
        if method != "notifications/initialized":
            packet["id"] = number
        if method == "initialize":
            packet["params"] = {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "TinyAssets", "version": "1"},
            }
        if method == "tools/call":
            packet["params"] = {"name": "hello", "arguments": {}}
        response = dispatch(
            grant_id,
            "POST",
            {
                "url": endpoint,
                "body": packet,
                "headers": {
                    "Accept": "application/json, text/event-stream",
                    "MCP-Protocol-Version": "2025-06-18",
                },
            },
        )
        assert response["status"] in (200, 202)
    assert "hello from unlisted" in response["body"]
    assert calls == ["initialize", "notifications/initialized", "tools/list", "tools/call"]
    if discovery_route == "challenge":
        assert (API, "/.well-known/oauth-protected-resource/tenants/a/mcp") not in fetched
