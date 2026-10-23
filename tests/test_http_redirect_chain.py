"""Real pinned sockets through a controlled local server; no external requests."""

import http.server
import re
import socket
import threading
import time
import urllib.parse

import pytest

from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    ConnectionSecretBundle,
    CredentialBlindBroker,
    GrantResolutionError,
    ProxyRequestError,
    SsrfValidationError,
    _classify_global_address,
    _parse_allowed_endpoints,
    _SsrfHardenedHttpDriver,
    _TrustedNetworkDriver,
)

SOURCE = {
    "host": "api.example.com",
    "path_template": "/download",
    "methods": ["GET"],
    "redirect_mode": "public_https_get",
}
KEY = "synthetic-connection-secret"
SIGNATURE = "synthetic-signed-download-capability"


class _ChainHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *args):
        pass

    def do_GET(self):
        state = self.server.state
        index = len(state["requests"])
        # Consume the legacy GET/POST body's declared bytes before replying;
        # otherwise BaseHTTPRequestHandler parses them as another request and
        # can reset the connection while the client reads the first response.
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
        state["requests"].append({"path": self.path, "headers": dict(self.headers), "body": body})
        response = state["responses"][index] if index < len(state["responses"]) else {"status": 500}
        callback = response.get("callback")
        if callback:
            callback()
        time.sleep(response.get("delay", 0))
        body = response.get("body", b"")
        try:
            self.send_response(response.get("status", 200), response.get("reason"))
            for key, value in response.get("headers", []):
                self.send_header(key, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        except OSError:
            pass  # a bounded/refused caller may already have closed its socket

    do_POST = do_GET


@pytest.fixture
def chain():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _ChainHandler)
    server.state = {"responses": [], "requests": [], "dns": [], "sockets": []}
    # Shutdown waits for this poll; keep real HTTP coverage without 0.5s per case.
    threading.Thread(
        target=server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True
    ).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _driver(chain, **overrides):
    def resolver(host, port):
        chain.state["dns"].append(host)
        return ["127.0.0.1"]

    def open_socket(address, timeout, source):
        chain.state["sockets"].append(address)
        return socket.create_connection(chain.server_address, timeout)

    return _SsrfHardenedHttpDriver(
        **{
            "resolver": resolver,
            "open_socket": open_socket,
            "ssl_context": _PassThroughTLS(),
            # Only this fixture's loopback pin is exempt; other addresses still use
            # the production classifier. This is not production TLS/DNS evidence.
            "validator": lambda ip: ip if ip == "127.0.0.1" else _classify_global_address(ip),
            **overrides,
        }
    )


def _request(driver, **overrides):
    return driver(
        **{
            "bundle": ConnectionSecretBundle(token=KEY),
            "auth_scheme": "bearer",
            "method": "GET",
            "url": "https://api.example.com/download",
            "headers": {"X-Custom": "only-on-first-hop"},
            "allowed_endpoints": _parse_allowed_endpoints([SOURCE]),
            "revalidate_authority": lambda deadline: None,
            **overrides,
        }
    )


def _redirect(url, status=302, **overrides):
    return {"status": status, "headers": [("Location", url)], **overrides}


@pytest.mark.verifies(
    'owner-network-egress-policy:Owner policy participates in the single egress decision')
@pytest.mark.parametrize("mode", ["open", "allowlist", "ask"])
def test_redirect_rechecks_owner_policy_without_inherited_permission(
        chain, admitted_egress, mode):  # noqa: F811 -- shared fixture
    from tests.test_owner_policy import _session
    from tinyassets.broker.egress import EgressPolicyRefused

    _, owner, store, _ = admitted_egress
    cookie = "redirect-policy-owner-" + "x" * 32
    _session(store._root, owner, cookie)
    policy = store.read(owner)
    policy.document["egress"] = {"mode": mode, "destinations": [
        {"host": "api.example.com", "port": 443}]}
    store.write(owner, cookie, policy.generation, policy.document)
    chain.state["responses"] = [_redirect("https://cdn.example.com/blob"), {"body": b"ok"}]
    if mode == "open":
        assert _request(_driver(chain))["body"] == "ok"
        assert "Authorization" not in chain.state["requests"][1]["headers"]
    else:
        with pytest.raises(EgressPolicyRefused):
            _request(_driver(chain))
        assert len(chain.state["requests"]) == 1
        assert len(chain.state["sockets"]) == 1
        receipts = store.receipts(owner)
        assert receipts[0]["decision"] == ("ask" if mode == "ask" else "deny")


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("status", [301, 302, 303, 307, 308])
def test_approved_redirect_download_returns_final_text_and_strips_foreign_auth(chain, status):
    chain.state["responses"] = [
        _redirect(f"https://cdn.example.com/blob?sig={SIGNATURE}", status),
        {"body": b"useful final content", "headers": [("Set-Cookie", "not-for-the-agent")]},
    ]
    chain.state["responses"][0]["headers"].append(("Set-Cookie", "origin-session=private"))
    result = _request(_driver(chain))
    assert result["body"] == "useful final content"
    assert result["redirect_count"] == 1
    first, second = chain.state["requests"]
    assert first["headers"]["Authorization"] == f"Bearer {KEY}"
    assert first["headers"]["X-Custom"] == "only-on-first-hop"
    assert "Authorization" not in second["headers"]
    assert "X-Custom" not in second["headers"]
    assert "Referer" not in second["headers"]
    assert "Cookie" not in second["headers"]
    assert SIGNATURE not in str(result)
    assert "cdn.example.com" not in str(result)
    assert "set-cookie" not in result["headers"]


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("authorized", [False, True])
def test_same_origin_auth_requires_independent_destination_permission(chain, authorized):
    chain.state["responses"] = [_redirect("next"), {"body": b"ok"}]
    endpoints = [SOURCE]
    if authorized:
        endpoints.append({**SOURCE, "path_template": "/next", "redirect_mode": "none"})
    _request(_driver(chain), allowed_endpoints=_parse_allowed_endpoints(endpoints))
    assert chain.state["requests"][1]["path"] == "/next"
    assert ("Authorization" in chain.state["requests"][1]["headers"]) == authorized


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_cross_origin_then_return_never_restores_credentials(chain):
    chain.state["responses"] = [
        _redirect("https://cdn.example.com/blob"),
        _redirect("https://api.example.com/final"),
        {"body": b"ok"},
    ]
    _request(_driver(chain), access_mode="full")
    assert len(chain.state["requests"]) == 3
    assert all("Authorization" not in r["headers"] for r in chain.state["requests"][1:])


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize(
    "location",
    [
        "http://other.example.com/file",
        "../file",
        "/a/../b",
        "/%2e%2e/file",
        "/a%2fb",
        "https://user:password@other.example.com/file",
        "/file#fragment",
        "/file#",
        "https://other.example.com:444/file",
        "/file\\hidden",
        "/%252f",
        "https://10.0.0.2/file",
    ],
)
def test_unsafe_redirect_opens_no_followup_socket(chain, location):
    chain.state["responses"] = [_redirect(location)]
    with pytest.raises((SsrfValidationError, ProxyRequestError)):
        _request(_driver(chain))
    assert len(chain.state["sockets"]) == 1


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("value", [KEY, "synthetic%2dconnection%2dsecret"])
def test_secret_in_redirect_target_is_refused_before_followup(chain, value):
    chain.state["responses"] = [_redirect(f"https://cdn.example.com/blob?key={value}")]
    with pytest.raises(ProxyRequestError, match="unsafe destination response") as caught:
        _request(_driver(chain))
    assert KEY not in str(caught.value)
    assert len(chain.state["requests"]) == 1


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("where", ["body", "reason"])
def test_reflected_download_capability_cannot_escape(chain, where):
    response = {"body": SIGNATURE.encode()} if where == "body" else {"reason": SIGNATURE}
    chain.state["responses"] = [
        _redirect(f"https://cdn.example.com/blob?sig={SIGNATURE}"),
        response,
    ]
    with pytest.raises(ProxyRequestError, match="unsafe destination response") as caught:
        _request(_driver(chain))
    assert SIGNATURE not in str(caught.value)


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_revocation_after_followup_dns_stops_before_its_socket(chain):
    chain.state["responses"] = [_redirect("https://cdn.example.com/blob")]
    revoked = False

    def resolver(host, port):
        nonlocal revoked
        chain.state["dns"].append(host)
        if host == "cdn.example.com":
            revoked = True
        return ["127.0.0.1"]

    def recheck(deadline):
        if revoked:
            raise GrantResolutionError("revoked")

    with pytest.raises(GrantResolutionError):
        _request(_driver(chain, resolver=resolver), revalidate_authority=recheck)
    assert chain.state["dns"] == ["api.example.com", "cdn.example.com"]
    assert len(chain.state["sockets"]) == 1


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("checkpoint", [3, 6])
def test_revocation_at_actual_dial_preserves_authority_error(chain, checkpoint):
    chain.state["responses"] = [_redirect("https://cdn.example.com/blob")]
    checks = 0

    def recheck(deadline):
        nonlocal checks
        checks += 1
        # Before DNS, after DNS, and at the dial, once per hop.
        if checks == checkpoint:
            raise GrantResolutionError("outbound connection authority changed")

    with pytest.raises(GrantResolutionError, match="authority changed"):
        _request(_driver(chain), revalidate_authority=recheck)
    assert checks == checkpoint
    assert len(chain.state["sockets"]) == (0 if checkpoint == 3 else 1)


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("encoded", [False, True])
def test_reflected_path_segment_capability_is_refused(chain, encoded):
    token = "opaque-path-capability-123456789"
    path_token = token.replace("-", "%2D") if encoded else token
    chain.state["responses"] = [
        _redirect(f"https://cdn.example.com/downloads/{path_token}/file"),
        {"body": token.encode()},
    ]
    with pytest.raises(ProxyRequestError):
        _request(_driver(chain))
    assert len(chain.state["requests"]) == 2


@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize("change", ["connection", "grant", "incarnation", "policy", "mode"])
def test_real_broker_rechecks_the_original_ledger_authority_between_hops(chain, tmp_path, change):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path)
    ledger.create_connection(
        connection_id="download",
        owner_user_id="alice",
        connection_class="http",
        connection_type="http",
        auth_scheme="bearer",
        scopes=("GET",),
        provider="http",
        destination="download",
        credential_ref="vault://http/download",
        allowed_endpoints=[SOURCE],
    )
    ledger.grant_connection(
        grant_id="original",
        connection_id="download",
        owner_user_id="alice",

    )
    audit = []
    network = _TrustedNetworkDriver(
        {"allow_test_fixtures": False, "allow_http_connections": True},
        tmp_path / "runtime",
    )
    network._http = _driver(chain)
    broker = CredentialBlindBroker(
        ledger,
        resolve_credential=lambda ref, kind: KEY,
        network_request=network,
        audit=audit.append,
    )

    def change_authority():
        if change == "connection":
            ledger.revoke_connection("download")
        elif change == "grant":
            ledger.revoke_grant("original")
        else:
            with ledger._connect() as conn:
                sql = {
                    "incarnation": "UPDATE outbound_connections SET incarnation = 'replacement'",
                    "policy": 'UPDATE outbound_connections SET scopes_json = \'["GET", "POST"]\'',
                    "mode": "UPDATE outbound_connections SET access_mode = 'full'",
                }[change]
                conn.execute(sql)

    chain.state["responses"] = [
        _redirect(f"https://cdn.example.com/blob?sig={SIGNATURE}", callback=change_authority),
    ]
    with pytest.raises(ProxyRequestError) as caught:
        broker.dispatch(
            "original",
            "GET",
            {
                "url": "https://api.example.com/download",
                "revalidate_authority": "caller cannot replace the trusted hook",
            },
        )
    assert len(chain.state["requests"]) == 1
    assert len(chain.state["sockets"]) == 1
    assert len(audit) == 1
    assert SIGNATURE not in str(audit) + str(caught.value)
    assert KEY not in str(audit) + str(caught.value)


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_same_host_dns_rebinding_is_rechecked_for_the_redirect(chain):
    calls = 0

    def resolver(host, port):
        nonlocal calls
        calls += 1
        return ["127.0.0.1"] if calls == 1 else ["10.0.0.2"]

    chain.state["responses"] = [_redirect("/next")]
    with pytest.raises(SsrfValidationError):
        _request(_driver(chain, resolver=resolver))
    assert calls == 2
    assert len(chain.state["sockets"]) == 1


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("echo", ["none", "first", "second", "signature"])
def test_oauth_is_resigned_for_each_authorized_url_and_all_signatures_are_protected(
    chain,
    monkeypatch,
    echo,
):
    import tinyassets.storage.outbound_connections as outbound

    original = outbound._ssrf_auth_headers
    signed_urls = []

    def sign(*args, **kwargs):
        signed_urls.append(kwargs["url"])
        return original(*args, **kwargs)

    monkeypatch.setattr(outbound, "_ssrf_auth_headers", sign)
    final = {"body": b"ok"}

    def choose_body():
        if echo == "none":
            return
        index = 0 if echo == "first" else 1
        value = chain.state["requests"][index]["headers"]["Authorization"]
        if echo == "signature":
            value = urllib.parse.unquote(re.search(r'oauth_signature="([^"]+)"', value).group(1))
        final["body"] = value.encode()

    final["callback"] = choose_body
    chain.state["responses"] = [_redirect("/next"), final]
    args = {
        "auth_scheme": "oauth1a",
        "bundle": ConnectionSecretBundle(
            api_key="synthetic-consumer-key",
            api_secret="synthetic-consumer-secret",
            access_token="synthetic-access-token",
            access_token_secret="synthetic-token-secret",
        ),
        "allowed_endpoints": _parse_allowed_endpoints(
            [
                SOURCE,
                {**SOURCE, "path_template": "/next", "redirect_mode": "none"},
            ]
        ),
    }
    if echo == "none":
        assert _request(_driver(chain), **args)["body"] == "ok"
    else:
        with pytest.raises(ProxyRequestError, match="unsafe destination response"):
            _request(_driver(chain), **args)
    assert signed_urls == ["https://api.example.com/download", "https://api.example.com/next"]
    assert (
        chain.state["requests"][0]["headers"]["Authorization"]
        != (chain.state["requests"][1]["headers"]["Authorization"])
    )


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_form_encoded_secret_is_refused_before_redirect_socket(chain):
    secret = "synthetic secret with spaces"
    chain.state["responses"] = [
        _redirect("https://cdn.example.com/blob?key=" + urllib.parse.quote_plus(secret)),
    ]
    with pytest.raises(ProxyRequestError, match="unsafe destination response"):
        _request(_driver(chain), bundle=ConnectionSecretBundle(token=secret))
    assert len(chain.state["sockets"]) == 1


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_followup_still_enforces_the_pinned_peer(chain):
    count = 0

    def resolver(host, port):
        nonlocal count
        count += 1
        return ["127.0.0.1"] if count == 1 else ["8.8.8.8"]

    chain.state["responses"] = [_redirect("https://cdn.example.com/blob")]
    with pytest.raises(SsrfValidationError, match="peer"):
        _request(_driver(chain, resolver=resolver))
    assert len(chain.state["sockets"]) == 2
    assert len(chain.state["requests"]) == 1


from tests.support.egress import admitted_egress  # noqa: E402, F401
