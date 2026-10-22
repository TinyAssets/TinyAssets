"""Offline refusal coverage: no request can reach WorkOS or production."""

import json
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import yaml

from scripts.live_signin_probe import Probe, Refused, main

CFG = {
    "configured": True,
    "issuer": "https://auth.example.test",
    "authorization_endpoint": "https://auth.example.test/oauth2/authorize",
    "client_id": "client_test",
    "resource": "https://tinyassets.io/mcp",
    "scopes": "openid profile email offline_access",
}


@pytest.fixture
def probe():
    return Probe("probe@example.test", "synthetic-password", "user_probe")


def callback(probe, **query):
    return (
        probe.redirect + "?" + urlencode({"state": probe.state, "code": "synthetic-code", **query})
    )


def upstream(probe, *, token=None, me=None, landing=None):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/app":
            return (
                landing
                if landing is not None
                else httpx.Response(200, text="app", headers={"X-TinyAssets-Build": "test"})
            )
        if request.url.path == "/app/token":
            assert request.method == "POST"
            assert request.headers["origin"] == probe.base
            assert request.headers["content-type"] == "application/json"
            assert json.loads(request.content) == {
                "code": "synthetic-code",
                "code_verifier": probe.verifier,
                "redirect_uri": probe.redirect,
            }
            return (
                token
                if token is not None
                else httpx.Response(
                    200,
                    json={"access_token": "synthetic-bearer", "session_ref": "private-ref"},
                    headers={"set-cookie": "session=must-not-mask-bearer; Path=/"},
                )
            )
        assert request.url.path == "/app/me"
        assert request.headers["authorization"] == "Bearer synthetic-bearer"
        assert "cookie" not in request.headers
        return me if me is not None else httpx.Response(200, json={"principal_id": "user_probe"})

    return httpx.Client(transport=httpx.MockTransport(handle)), calls


def test_full_code_exchange_uses_only_public_app_routes(probe):
    with upstream(probe)[0] as client:
        probe.exchange(client, callback(probe))


@pytest.mark.parametrize(
    ("stage", "response", "message"),
    [
        ("landing", httpx.Response(404, text="Not Found"), "callback /app: HTTP 404: Not Found"),
        ("landing", httpx.Response(302, text="Moved"), "callback /app: HTTP 302"),
        ("landing", httpx.Response(200, text="marketing site"), "without X-TinyAssets-Build"),
        (
            "token",
            httpx.Response(403, json={"error": "cross_origin_rejected"}),
            "cross_origin_rejected",
        ),
        ("token", httpx.Response(400, json={"error": "exchange_failed"}), "exchange_failed"),
        (
            "token",
            httpx.Response(502, json={"error": "token_endpoint_unreachable"}),
            "token_endpoint_unreachable",
        ),
        ("token", httpx.Response(200, text="not JSON"), "expected a JSON object"),
        ("token", httpx.Response(200, json=[]), "expected a JSON object"),
        ("token", httpx.Response(200, json={}), "without access_token"),
        ("token", httpx.Response(200, json={"access_token": 7}), "without access_token"),
        (
            "me",
            httpx.Response(
                401, json={"error": "invalid_token", "error_description": "session_not_active"}
            ),
            "session_not_active",
        ),
        ("me", httpx.Response(403, text="owner_box_unavailable"), "owner_box_unavailable"),
        ("me", httpx.Response(503, text="Unavailable"), "HTTP 503: Unavailable"),
        ("me", httpx.Response(302, text="Login"), "HTTP 302"),
        ("me", httpx.Response(200, json={"principal_id": "user_other"}), "different principal_id"),
        ("me", httpx.Response(200, json={}), "missing or different principal_id"),
        ("me", httpx.Response(200, text="HTML"), "expected a JSON object"),
    ],
)
def test_each_public_refusal_stops_the_probe(probe, stage, response, message):
    client, calls = upstream(probe, **{stage: response})
    with client, pytest.raises(Refused, match=message):
        probe.exchange(client, callback(probe))
    assert len(calls) == {"landing": 1, "token": 2, "me": 3}[stage]


@pytest.mark.parametrize("path", ["/app", "/app/token", "/app/me"])
def test_transport_failure_is_stage_specific_without_exception_secrets(probe, path):
    client, _ = upstream(probe)
    original = client._transport.handler

    def fail(request):
        if request.url.path == path:
            raise httpx.ConnectError("secret transport context " + probe.password)
        return original(request)

    client._transport = httpx.MockTransport(fail)
    with client, pytest.raises(Refused, match="transport failure \\(ConnectError\\)") as caught:
        probe.exchange(client, callback(probe))
    assert probe.password not in str(caught.value)


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ({"state": "wrong"}, "mismatched state"),
        ({"state": ""}, "mismatched state"),
        ({"code": ""}, "missing or duplicate authorization code"),
        (
            {"error": "access_denied", "error_description": "Login refused"},
            "access_denied Login refused",
        ),
    ],
)
def test_bad_oauth_callback_never_exchanges(probe, query, message):
    with pytest.raises(Refused, match=message):
        probe.callback_code(callback(probe, **query))


@pytest.mark.parametrize("suffix", ["&state=duplicate", "&code=duplicate", "#fragment"])
def test_ambiguous_callback_is_refused(probe, suffix):
    with pytest.raises(Refused):
        probe.callback_code(callback(probe) + suffix)


def test_callback_origin_is_exact(probe):
    with pytest.raises(Refused, match="unexpected redirect URI"):
        probe.callback_code(callback(probe).replace("tinyassets.io", "evil.test"))


@pytest.mark.parametrize(
    "cfg, message",
    [
        (None, "missing server-injected"),
        ({"configured": False}, "not configured"),
        ({**CFG, "client_id": ""}, "missing client_id"),
        ({**CFG, "issuer": "http://auth.example.test"}, "invalid AuthKit issuer"),
        (
            {**CFG, "authorization_endpoint": "https://evil.test"},
            "unexpected authorization endpoint",
        ),
    ],
)
def test_config_refusals(probe, cfg, message):
    body = "wrong site" if cfg is None else "const cfg=" + json.dumps(cfg) + ", key='x';"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        with pytest.raises(Refused, match=message):
            probe.config(client)


def test_deployed_config_and_pkce(probe):
    body = "const cfg=" + json.dumps(CFG) + ", key='x';"
    with httpx.Client(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, text=body))
    ) as client:
        cfg = probe.config(client)
    query = parse_qs(urlsplit(probe.authorize_url(cfg)).query)
    assert query["response_type"] == ["code"]
    assert query["redirect_uri"] == [probe.redirect]
    assert query["resource"] == [CFG["resource"]]
    assert query["scope"] == [CFG["scopes"]]
    assert query["code_challenge_method"] == ["S256"]
    import base64
    import hashlib

    expected = (
        base64.urlsafe_b64encode(hashlib.sha256(probe.verifier.encode()).digest())
        .rstrip(b"=")
        .decode()
    )
    assert query["code_challenge"] == [expected]
    assert "code_verifier" not in query and "client_secret" not in query


def test_refusals_keep_exact_reason_but_never_print_credentials(probe):
    probe.hidden.append("synthetic-code")
    response = httpx.Response(
        401,
        json={
            "access_token": "short-bearer",
            "refresh_token": "short-refresh",
            "error": "invalid_token",
            "error_description": f"revoked {probe.password} synthetic-code "
            f"short-bearer short-refresh {probe.verifier}",
        },
    )
    with pytest.raises(Refused, match="invalid_token") as caught:
        probe.response(response, "identity /app/me")
    for secret in (
        probe.password,
        "synthetic-code",
        "short-bearer",
        "short-refresh",
        probe.verifier,
    ):
        assert secret not in str(caught.value)


class Locator:
    def __init__(self, page, selector):
        self.page, self.selector = page, selector
        self.first = self

    def count(self):
        return int(self.is_visible())

    def is_visible(self):
        return self.selector in {
            "email": ['input[type="email"]', 'button[type="submit"]'],
            "password": ['input[type="password"]', 'button[type="submit"]'],
            "refused": ['[role="alert"]'],
        }.get(self.page.stage, [])

    def inner_text(self):
        return self.page.reason

    def fill(self, value):
        self.page.filled.append(value)

    def click(self):
        if self.page.stage == "email":
            self.page.stage = "password"
        elif self.page.reason:
            self.page.stage = "refused"
        else:
            self.page.callback(self.page.route_object)


class HostedPage:
    """Fake hosted upstream: email -> password -> refusal or callback."""

    def __init__(self, probe, reason="", status=200):
        from types import SimpleNamespace

        self.url = CFG["issuer"]
        self.stage, self.reason, self.status = "email", reason, status
        self.filled = []
        self.route_object = SimpleNamespace(
            request=SimpleNamespace(url=callback(probe)), fulfill=lambda **kw: None
        )

    def route(self, pattern, handler):
        self.callback = handler

    def goto(self, url, **kwargs):
        from types import SimpleNamespace

        return SimpleNamespace(status=self.status)

    def locator(self, selector):
        return Locator(self, selector)

    def get_by_role(self, *args, **kwargs):
        return Locator(self, "consent")

    def wait_for_timeout(self, _):
        pass


def test_hosted_password_not_api_grant(probe):
    page = HostedPage(probe)
    assert probe.hosted_login(page, CFG) == callback(probe)
    assert page.filled == [probe.email, probe.password]


@pytest.mark.parametrize(
    "reason",
    [
        "Invalid email or password",
        "Verify you are human",
        "Enter your authentication code",
        "Verify your email",
        "Too many attempts",
    ],
)
def test_hosted_refusal_is_exact(probe, reason):
    with pytest.raises(Refused, match=reason):
        probe.hosted_login(HostedPage(probe, reason), CFG)


def test_hosted_http_refusal(probe):
    with pytest.raises(Refused, match="authorize: HTTP 429: Too many attempts"):
        probe.hosted_login(HostedPage(probe, "Too many attempts", status=429), CFG)


def test_hosted_challenge_without_alert(probe):
    with pytest.raises(Refused, match="no OAuth callback; hosted page says: Verify you are human"):
        probe.hosted_login(HostedPage(probe, "Verify you are human"), CFG, timeout=0)


def test_unexpected_host_never_receives_password(probe):
    page = HostedPage(probe)
    page.url = "https://evil.test/login"
    with pytest.raises(Refused, match="unexpected hosted-login origin"):
        probe.hosted_login(page, CFG)
    assert page.filled == []


def test_browser_exception_does_not_leak_fill_values(probe):
    page = HostedPage(probe)

    def broken(*args, **kwargs):
        raise RuntimeError("fill value " + probe.password)

    page.goto = broken
    with pytest.raises(Refused, match="browser failure") as caught:
        probe.hosted_login(page, CFG)
    assert probe.password not in str(caught.value)


@pytest.mark.parametrize(
    "status, location, message",
    [
        (302, "", "redirect without Location"),
        (302, "https://evil.test/login", "unexpected hosted redirect origin"),
        (307, "/login", "unsupported hosted redirect HTTP 307"),
    ],
)
def test_hosted_redirect_refusals(probe, status, location, message):
    from types import SimpleNamespace

    page = HostedPage(probe)
    response = SimpleNamespace(status=status, headers={"location": location})
    route = SimpleNamespace(
        request=SimpleNamespace(
            url=CFG["authorization_endpoint"], is_navigation_request=lambda: True
        ),
        fetch=lambda **kw: response,
        fulfill=lambda **kw: None,
    )

    def navigate(*args, **kwargs):
        page.callback(route)
        return SimpleNamespace(status=200)

    page.goto = navigate
    with pytest.raises(Refused, match=message):
        probe.hosted_login(page, CFG)


def test_hosted_navigation_transport_refusal(probe):
    from types import SimpleNamespace

    page = HostedPage(probe)

    def broken(**kwargs):
        raise RuntimeError(probe.password)

    route = SimpleNamespace(
        request=SimpleNamespace(
            url=CFG["authorization_endpoint"], is_navigation_request=lambda: True
        ),
        fetch=broken,
        fulfill=lambda **kw: None,
    )

    def navigate(*args, **kwargs):
        page.callback(route)
        return SimpleNamespace(status=200)

    page.goto = navigate
    with pytest.raises(Refused, match="navigation transport failure") as caught:
        probe.hosted_login(page, CFG)
    assert probe.password not in str(caught.value)


def test_missing_credentials_fail_before_network(monkeypatch, capsys):
    for name in ("EMAIL", "PASSWORD", "USER_ID"):
        monkeypatch.delenv("TINYASSETS_CANARY_" + name, raising=False)
    assert main() == 1
    assert "missing TINYASSETS_CANARY_EMAIL" in capsys.readouterr().err


def test_required_workflow_step_joins_existing_recovery():
    root = Path(__file__).resolve().parents[1]
    steps = yaml.safe_load((root / ".github/workflows/deploy-prod.yml").read_text())["jobs"][
        "deploy"
    ]["steps"]
    index = next(i for i, step in enumerate(steps) if step.get("id") == "canary")
    signin = steps[index + 1]
    assert signin["id"] == "signin_probe"
    assert "if" not in signin and not signin.get("continue-on-error")
    assert "python scripts/live_signin_probe.py" in signin["run"]
    recovery = next(step for step in steps if step.get("id") == "post_commit_failure")
    assert "steps.signin_probe.outcome == 'failure'" in recovery["env"]["IMAGE_ROLLBACK_ALLOWED"]
    assert "signin=${{ steps.signin_probe.outcome }}" in recovery["env"]["POST_COMMIT_REASON"]
    assert "--post-deploy-failure" in recovery["run"]
