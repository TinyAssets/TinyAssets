"""Adversarial tests for the general SSRF-hardened outbound HTTP driver.

Scope: channel-agnostic-outbound design.md D3/D4/D5, SLICE 1 (DARK). These
tests exercise the *mechanism* (`_SsrfHardenedHttpDriver` + its strict transport
helpers) directly. Each case would FAIL if the corresponding guard were removed:
they assert refusal on a hostile input or scrubbing of a leaked secret, not just
that the happy path works.

The driver exposes injectable seams (resolver / address validator / socket
opener / TLS context) whose DEFAULTS are the secure production implementations.
The security-critical default classifier (`_classify_global_address`) is tested
on its own; the live-socket integration cases inject a permissive validator and
a loopback socket so the real request/response/pinning/read/scrub path runs
against a local stub server (a loopback address can never pass `is_global`, so
there is no other way to exercise the transport end-to-end).
"""

from __future__ import annotations

import http.server
import os
import re
import socket
import ssl
import threading
import time

import pytest

from tinyassets.storage.outbound_connections import (
    ConnectionSecretBundle,
    OutboundEndpoint,
    ProxyRequestError,
    SsrfValidationError,
    _classify_global_address,
    _default_ssl_context,
    _sanitize_child_environment,
    _SsrfHardenedHttpDriver,
    _TrustedNetworkDriver,
)


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #
class _PassThroughTLS:
    """A TLS context that returns the plain socket unwrapped.

    Lets the integration cases speak plain HTTP to a local stub through the real
    `_PinnedHTTPSConnection` without generating a certificate, while still
    proving the SNI/hostname value the driver *would* verify against.
    """

    def __init__(self) -> None:
        self.verify_mode = ssl.CERT_NONE
        self.check_hostname = False
        self.captured_server_hostname: str | None = None

    def wrap_socket(self, sock, server_hostname=None):  # noqa: ANN001
        self.captured_server_hostname = server_hostname
        return sock


class _FakeSock:
    """A socket whose peer address is controllable (peer-validation tests)."""

    def __init__(self, peer: str) -> None:
        self._peer = peer
        self.closed = False

    def getpeername(self):
        return (self._peer, 443)

    def close(self):
        self.closed = True


class _StopDial(Exception):
    """Raised by an injected opener to halt before any real network I/O."""


def _never_dial(*_args, **_kwargs):
    raise AssertionError("the transport must not open a socket for this input")


class _StubHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):  # silence the test server
        return

    def _serve(self):
        stub = self.server.stub  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b""
        stub["received"] = {
            "path": self.path,
            "headers": {k.lower(): v for k, v in self.headers.items()},
            "body": raw,
        }
        payload = stub["body"]
        self.send_response(stub["status"])
        for name, value in stub.get("extra_headers", ()):  # type: ignore[union-attr]
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    do_GET = _serve
    do_POST = _serve
    do_DELETE = _serve


@pytest.fixture
def stub_server():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _StubHandler)
    server.stub = {  # type: ignore[attr-defined]
        "status": 200,
        "body": b"ok",
        "extra_headers": (),
        "received": None,
    }
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _local_driver(server, **overrides):
    """A driver that dials `server` over loopback while pretending it pinned a
    public address; records resolver + open_socket calls for assertions."""
    port = server.server_address[1]
    calls = {"resolver": 0, "open_socket": []}

    def resolver(_host, _port):
        calls["resolver"] += 1
        return ["127.0.0.1"]

    def open_socket(address, timeout, _source_address):
        calls["open_socket"].append(address)
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    context = _PassThroughTLS()
    kwargs = dict(
        resolver=resolver,
        validator=lambda addr: addr,  # permissive: allow the loopback pin
        open_socket=open_socket,
        ssl_context=context,
        allowed_ports=frozenset({port}),
    )
    kwargs.update(overrides)
    return _SsrfHardenedHttpDriver(**kwargs), calls, context, port


# --------------------------------------------------------------------------- #
# Positive path — a normal public https call succeeds through the pinned path
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_ambient_env_proxy_is_ignored(stub_server, monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    stub_server.stub.update(status=200, body=b"ok")
    driver, calls, _context, port = _local_driver(stub_server)

    result = driver(
        bundle=ConnectionSecretBundle(token="bearer-tok-not-in-any-response"),
        auth_scheme="bearer",
        method="GET",
        url=f"https://public.example:{port}/x",
    )

    assert result["status"] == 200
    # Straight to the pinned socket — the env proxy was never consulted.
    assert calls["open_socket"] == [("127.0.0.1", port)]


# --------------------------------------------------------------------------- #
# URL canonicalization refusals
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize(
    "url",
    [
        "http://public.example/x",  # non-https scheme (metadata via http too)
        "ftp://public.example/x",  # other scheme
        "https://user:pass@public.example/x",  # userinfo
        "https://public.example/x#frag",  # fragment
        "https://public.example/a/../b",  # dot-segment
        "https://public.example/./b",  # single-dot segment
        "https://public.example/%252e%252e/x",  # double-encoding
        "https://public.example\\@evil.example/x",  # backslash
        "https://public.example /x",  # embedded whitespace
        "https://public.example:8443/x",  # unexpected port (default allows 443)
        "https://2130706433/x",  # decimal IP literal
        "https://0x7f000001/x",  # hex IP literal
        "https://0177.0.0.1/x",  # octal-ish dotted literal
        "https://localhost/x",  # single-label host (no real TLD)
        "https://public.example/public/%2e%2e/admin",  # encoded dot-seg (Codex)
        "https://public.example/public/.%2e/admin",  # mixed encoded dot-seg
        "https://public.example/public/%2e%2e%2fadmin",  # encoded dot + slash
        "https://public.example/%2E%2E/admin",  # uppercase encoded dot-seg
    ],
)
def test_noncanonical_or_unsafe_urls_are_refused(url):
    driver = _SsrfHardenedHttpDriver(open_socket=_never_dial)
    with pytest.raises(SsrfValidationError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="bearer",
            method="GET",
            url=url,
        )


# --------------------------------------------------------------------------- #
# Address classification refusals (literal hosts that pass parse, fail classify)
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize(
    "url",
    [
        "https://169.254.169.254/latest/meta-data/",  # link-local metadata
        "https://[::ffff:169.254.169.254]/x",  # IPv4-mapped IPv6 metadata
        "https://127.0.0.1/x",  # loopback
        "https://[::1]/x",  # IPv6 loopback
        "https://10.0.0.5/x",  # private
        "https://192.168.1.1/x",  # private
        "https://172.16.9.9/x",  # private
        "https://0.0.0.0/x",  # unspecified
        "https://100.64.0.1/x",  # shared / CGNAT
        "https://[fec0::1]/x",  # deprecated IPv6 site-local (Codex-found)
        "https://[64:ff9b::c0a8:1]/x",  # NAT64 embedding 192.168.0.1 (Codex-found)
    ],
)
def test_nonglobal_ip_literals_are_refused(url):
    driver = _SsrfHardenedHttpDriver(open_socket=_never_dial)
    with pytest.raises(SsrfValidationError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="bearer",
            method="GET",
            url=url,
        )


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_classifier_unwraps_ipv4_mapped_ipv6():
    with pytest.raises(SsrfValidationError):
        _classify_global_address("::ffff:169.254.169.254")
    with pytest.raises(SsrfValidationError):
        _classify_global_address("::ffff:127.0.0.1")
    # A genuine public address passes.
    assert _classify_global_address("93.184.216.34") == "93.184.216.34"


# --------------------------------------------------------------------------- #
# DNS resolution / rebinding / pinning
# --------------------------------------------------------------------------- #


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_all_resolved_addresses_are_validated_not_just_first():
    driver = _SsrfHardenedHttpDriver(
        resolver=lambda _h, _p: ["93.184.216.34", "10.0.0.5"],
        open_socket=_never_dial,
        ssl_context=_PassThroughTLS(),
    )
    with pytest.raises(SsrfValidationError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="bearer",
            method="GET",
            url="https://public.example/x",
        )


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_pins_first_validated_address_and_never_reresolves():
    resolver_calls: list[str] = []
    dialed: list[tuple[str, int]] = []

    def resolver(host, _port):
        resolver_calls.append(host)
        # A rebinding resolver would hand back a private IP on a *second* call.
        # Pinning means we never call it twice, so the private answer is unused.
        return ["93.184.216.34"] if len(resolver_calls) == 1 else ["10.0.0.5"]

    def open_socket(address, _timeout, _source_address):
        dialed.append(address)
        raise _StopDial

    driver = _SsrfHardenedHttpDriver(
        resolver=resolver,
        open_socket=open_socket,
        ssl_context=_PassThroughTLS(),
    )
    with pytest.raises(ProxyRequestError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="bearer",
            method="GET",
            url="https://public.example/x",
        )

    assert resolver_calls == ["public.example"]  # resolved exactly once
    assert dialed == [("93.184.216.34", 443)]  # dialed the FIRST validated addr


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_connected_peer_mismatch_is_rejected():
    fake = _FakeSock("10.0.0.5")  # rerouted to a private address after the pin

    driver = _SsrfHardenedHttpDriver(
        resolver=lambda _h, _p: ["93.184.216.34"],
        validator=lambda addr: addr,  # permit the public pin
        open_socket=lambda *_a: fake,
        ssl_context=_PassThroughTLS(),
    )
    with pytest.raises(ProxyRequestError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="bearer",
            method="GET",
            url="https://public.example/x",
        )
    assert fake.closed


# --------------------------------------------------------------------------- #
# Caller-supplied sensitive headers are refused (auth is applied in-driver only)
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize(
    "bad_header",
    [
        {"Authorization": "Bearer attacker"},
        {"authorization": "Bearer attacker"},
        {"Host": "evil.example"},
        {"Cookie": "sid=1"},
        {"Proxy-Authorization": "Basic x"},
        {"Proxy-Connection": "keep-alive"},
        {"X-Evil": "line1\r\nInjected: 1"},  # header CR/LF injection
        {"Content-Length": "0"},  # request-smuggling framing header (Codex-found)
        {"Transfer-Encoding": "chunked"},  # request-smuggling framing header
        {"Connection": "close"},  # hop-by-hop header
    ],
)
def test_caller_supplied_sensitive_or_malformed_headers_are_refused(bad_header):
    driver = _SsrfHardenedHttpDriver(open_socket=_never_dial)
    with pytest.raises(SsrfValidationError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="bearer",
            method="GET",
            url="https://public.example/x",
            headers=bad_header,
        )


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("scheme", ["bearer", "header"])
def test_auth_value_with_obs_fold_crlf_is_refused(scheme):
    # A bundle secret carrying CR/LF (obs-fold) would emit a folded header line
    # http.client accepts verbatim, re-opening request smuggling (Codex-found).
    driver = _SsrfHardenedHttpDriver(open_socket=_never_dial)
    with pytest.raises(SsrfValidationError):
        driver(
            bundle=ConnectionSecretBundle(token="secret\r\n Content-Length: 0"),
            auth_scheme=scheme,
            header_name="X-Api-Key",
            method="POST",
            url="https://public.example/x",
            body=b"GET /admin HTTP/1.1\r\nHost: internal\r\n\r\n",
        )


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize(
    "header_name",
    [
        "Content-Length",  # framing → request smuggling (Codex-found)
        "Transfer-Encoding",
        "Connection",
        "Host",
        "Cookie",
        "Proxy-Authorization",
    ],
)
def test_custom_auth_header_name_cannot_bypass_the_header_policy(header_name):
    # `auth_scheme="header"` must apply the SAME framing/sensitive/proxy policy
    # to its header name, or it is a back door around `_validated_request_headers`.
    driver = _SsrfHardenedHttpDriver(open_socket=_never_dial)
    with pytest.raises(SsrfValidationError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="header",
            header_name=header_name,
            method="POST",
            url="https://public.example/x",
            body=b"GET /admin HTTP/1.1\r\nHost: internal\r\n\r\n",
        )


# --------------------------------------------------------------------------- #
# Response bounds
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# Credential-blindness: the whole typed bundle is scrubbed from the response
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_response_echoing_the_injected_secret_is_scrubbed(stub_server):
    secret = "xoxb-super-secret-value"
    stub_server.stub.update(status=200, body=f"echo {secret}".encode())
    driver, _calls, _context, port = _local_driver(stub_server)

    with pytest.raises(ProxyRequestError) as raised:
        driver(
            bundle=ConnectionSecretBundle(token=secret),
            auth_scheme="bearer",
            method="GET",
            allowed_endpoints=(_endpoint(path_template="/x", methods=("GET",)),),
            url=f"https://public.example:{port}/x",
        )
    assert secret not in str(raised.value)
    assert "unsafe destination response" in str(raised.value)


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_basic_auth_base64_echo_is_scrubbed(stub_server):
    # A Basic credential goes on the wire as base64(user:pass), which matches no
    # raw bundle member; an adversarial destination could echo that blob and a
    # caller could reverse it. The wire-value scrub must catch it (Codex-found).
    import base64

    username, password = "admin", "s3cr3t-password-value"
    blob = base64.b64encode(f"{username}:{password}".encode()).decode()
    stub_server.stub.update(status=200, body=f"you sent {blob}".encode())
    driver, _calls, _context, port = _local_driver(stub_server)

    with pytest.raises(ProxyRequestError, match="unsafe destination response") as raised:
        driver(
            bundle=ConnectionSecretBundle(username=username, password=password),
            auth_scheme="basic",
            allowed_endpoints=(_endpoint(path_template="/x", methods=("GET",)),),
            method="GET",
            url=f"https://public.example:{port}/x",
        )
    assert blob not in str(raised.value)


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_exception_context_does_not_leak_the_secret():
    # A destination that replies with a non-HTTP line containing the token makes
    # http.client raise BadStatusLine(<that line>). The sanitized ProxyRequestError
    # must not carry it on __context__/__cause__ (the slack_transport.py:89 class).
    token = "xoxb-CONTEXT-LEAK-must-not-appear"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]

    def serve():
        try:
            conn, _ = listener.accept()
            try:
                conn.recv(65535)
                conn.sendall(f"{token} NOT-HTTP\r\n\r\n".encode())
            finally:
                conn.close()
        finally:
            listener.close()

    threading.Thread(target=serve, daemon=True).start()

    def open_socket(_address, timeout, _source_address):
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    driver = _SsrfHardenedHttpDriver(
        resolver=lambda _h, _p: ["127.0.0.1"],
        validator=lambda addr: addr,
        open_socket=open_socket,
        ssl_context=_PassThroughTLS(),
        allowed_ports=frozenset({port}),
    )
    with pytest.raises(ProxyRequestError) as raised:
        driver(
            bundle=ConnectionSecretBundle(token=token),
            auth_scheme="bearer",
            method="GET",
            url=f"https://public.example:{port}/x",
        )

    exc: BaseException | None = raised.value
    seen: set[int] = set()
    pieces: list[str] = []
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        pieces.append(str(exc))
        pieces.append(repr(exc))
        exc = exc.__context__ or exc.__cause__
    assert all(token not in piece for piece in pieces)
    assert raised.value.__context__ is None


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_declassify_scans_every_bundle_member_not_just_one(stub_server):
    bot_token = "xoxb-bot-token"
    app_token = "xapp-app-level-token"
    # The response echoes the APP token, which was NOT the header sent — the
    # whole-bundle scan still catches it (design.md D4).
    stub_server.stub.update(status=200, body=f"leak {app_token}".encode())
    driver, _calls, _context, port = _local_driver(stub_server)

    with pytest.raises(ProxyRequestError, match="unsafe destination response"):
        driver(
            bundle=ConnectionSecretBundle(bot_token=bot_token, app_token=app_token),
            auth_scheme="none",
            method="GET",
            allowed_endpoints=(_endpoint(path_template="/x", methods=("GET",)),),
            url=f"https://public.example:{port}/x",
        )


# --------------------------------------------------------------------------- #
# The secure defaults
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_default_ssl_context_verifies_certificate_and_hostname():
    context = _default_ssl_context()
    assert context.check_hostname is True
    assert context.verify_mode == ssl.CERT_REQUIRED


# --------------------------------------------------------------------------- #
# FIX 1 — TLS key-logging credential leak (SSLKEYLOGFILE)
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_ssl_context_disables_keylog_even_with_env(monkeypatch, tmp_path):
    monkeypatch.setenv("SSLKEYLOGFILE", str(tmp_path / "tls.keys"))
    context = _default_ssl_context()
    assert context.keylog_filename is None


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_child_environment_sanitization_drops_keylog_and_proxies(monkeypatch):
    monkeypatch.setenv("SSLKEYLOGFILE", "C:/temp/tls.keys")
    monkeypatch.setenv("HTTPS_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")
    monkeypatch.setenv("ALL_PROXY", "http://127.0.0.1:9")
    _sanitize_child_environment()
    assert "SSLKEYLOGFILE" not in os.environ
    assert "HTTPS_PROXY" not in os.environ
    assert "HTTP_PROXY" not in os.environ
    assert "ALL_PROXY" not in os.environ


# --------------------------------------------------------------------------- #
# FIX 2 — total wall-clock deadline (slowloris / drip-feed)
# --------------------------------------------------------------------------- #


def _serve_drip(first_chunk: bytes):
    """Listener that sends ``first_chunk`` then drips 1 byte / 50 ms forever.

    Reproduces a slow-drip in whichever http.client parse phase ``first_chunk``
    leaves unterminated. Returns ``(port, stop_event)``.
    """
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def serve():
        try:
            conn, _ = listener.accept()
            try:
                conn.recv(65535)
                conn.sendall(first_chunk)
                while not stop.is_set():
                    try:
                        conn.sendall(b"x")
                    except OSError:
                        break
                    time.sleep(0.05)
            finally:
                conn.close()
        except OSError:
            pass
        finally:
            listener.close()

    threading.Thread(target=serve, daemon=True).start()
    return port, stop


def _drip_driver(port):
    def open_socket(_address, timeout, _source_address):
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    return _SsrfHardenedHttpDriver(
        resolver=lambda _h, _p: ["127.0.0.1"],
        validator=lambda addr: addr,
        open_socket=open_socket,
        ssl_context=_PassThroughTLS(),
        allowed_ports=frozenset({port}),
        timeout=0.2,
        max_total_seconds=0.4,
    )


# --------------------------------------------------------------------------- #
# Per-connection endpoint allowlist — the real egress boundary (residual #1)
# --------------------------------------------------------------------------- #
def _endpoint(
    host="public.example",
    path_template="/api/send",
    methods=("GET", "POST"),
    param_patterns=None,
    allowed_query=(),
    query_patterns=(),
):
    # Every {param} in the template needs a DECLARED value pattern (FIX 3); auto-
    # fill a permissive-but-declared one unless the test overrides it.
    if param_patterns is None:
        names = re.findall(r"\{([a-z0-9_]+)\}", path_template)
        param_patterns = tuple((name, r"[A-Za-z0-9._-]+") for name in names)
    elif isinstance(param_patterns, dict):
        param_patterns = tuple(sorted(param_patterns.items()))
    if isinstance(query_patterns, dict):
        query_patterns = tuple(sorted(query_patterns.items()))
    return OutboundEndpoint(
        host=host,
        path_template=path_template,
        methods=tuple(methods),
        param_patterns=tuple(param_patterns),
        allowed_query=tuple(allowed_query),
        query_patterns=tuple(query_patterns),
    )


# --------------------------------------------------------------------------- #
# Threaded DNS resolver bounded by its own timeout (residual #3)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# _TrustedNetworkDriver wiring: http path behind a default-OFF flag
# --------------------------------------------------------------------------- #
def _trusted(tmp_path, *, allow_http):
    return _TrustedNetworkDriver(
        {"allow_test_fixtures": True, "allow_http_connections": allow_http},
        tmp_path,
    )


# --------------------------------------------------------------------------- #
# FIX 1 — fail-OPEN on unknown connection_type (Codex REJECT)
# --------------------------------------------------------------------------- #


# --------------------------------------------------------------------------- #
# FIX 2 — encoded backslash + overlong-UTF-8 separator traversal (Codex REJECT)
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:C7')
@pytest.mark.parametrize(
    "url",
    [
        "https://public.example/public/%5c../admin",  # encoded backslash
        "https://public.example/public/%5C../admin",  # uppercase encoded backslash
        "https://public.example/public/%c0%af/admin",  # overlong-UTF-8 slash
        "https://public.example/public/%c0%ae%c0%ae/admin",  # overlong dots
        "https://public.example/public/%255c../admin",  # double-encoded backslash
    ],
)
def test_encoded_backslash_or_overlong_traversal_is_refused(url):
    # A Windows/IIS origin decodes %5c as a path separator; overlong UTF-8 spells
    # a separator with high bytes. Both must be refused at canonical parse before
    # the allowlist template ({doc}) can bind the segment.
    driver = _SsrfHardenedHttpDriver(open_socket=_never_dial)
    with pytest.raises(SsrfValidationError):
        driver(
            bundle=ConnectionSecretBundle(token="t"),
            auth_scheme="bearer",
            method="GET",
            url=url,
            allowed_endpoints=(
                _endpoint(path_template="/public/{doc}/admin", methods=("GET",)),
            ),
        )


# --------------------------------------------------------------------------- #
# FIX 3 — over-broad placeholders + query smuggling (Codex REJECT)
# --------------------------------------------------------------------------- #
@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_placeholder_without_declared_pattern_fails_closed(stub_server):
    # A raw endpoint whose {account_id} has NO declared pattern must not match
    # "any non-empty segment" — that was the FIX 3 bypass (victim != self).
    endpoint = OutboundEndpoint(
        host="public.example",
        path_template="/v1/accounts/{account_id}/secrets",
        methods=("GET",),
    )
    driver, _, _, _ = _local_driver(stub_server, allowed_ports=frozenset({443}))
    result = driver(
        bundle=ConnectionSecretBundle(token="t"),
        auth_scheme="bearer",
        method="GET",
        url="https://public.example/v1/accounts/victim/secrets",
        allowed_endpoints=(endpoint,),
    )
    assert result["status"] == 200
    assert "authorization" not in stub_server.stub["received"]["headers"]


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_declared_pattern_binds_the_tenant_and_refuses_others(stub_server):
    endpoint = _endpoint(
        host="public.example",
        path_template="/v1/accounts/{account_id}/secrets",
        methods=("GET",),
        param_patterns={"account_id": "self"},  # ONLY "self"
    )
    stub_server.stub.update(status=200, body=b"ok")
    driver, _calls, _ctx, port = _local_driver(stub_server)
    ok = driver(
        bundle=ConnectionSecretBundle(token="tok-not-in-response"),
        auth_scheme="bearer",
        method="GET",
        url=f"https://public.example:{port}/v1/accounts/self/secrets",
        allowed_endpoints=(endpoint,),
    )
    assert ok["status"] == 200

    refuser, _, _, _ = _local_driver(stub_server, allowed_ports=frozenset({443}))
    result = refuser(
        bundle=ConnectionSecretBundle(token="t"),
        auth_scheme="bearer",
        method="GET",
        url="https://public.example/v1/accounts/victim/secrets",
        allowed_endpoints=(endpoint,),
    )
    assert result["status"] == 200
    assert "authorization" not in stub_server.stub["received"]["headers"]


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_undeclared_query_param_refused_even_beside_a_declared_one(stub_server):
    endpoint = _endpoint(path_template="/v1/export", methods=("GET",), allowed_query=("mode",))
    driver, _, _, _ = _local_driver(stub_server, allowed_ports=frozenset({443}))
    result = driver(
        bundle=ConnectionSecretBundle(token="t"),
        auth_scheme="bearer",
        method="GET",
        url="https://public.example/v1/export?account=victim&mode=safe",
        allowed_endpoints=(endpoint,),
    )
    assert result["status"] == 200
    assert "authorization" not in stub_server.stub["received"]["headers"]


@pytest.mark.verifies('owner-daemon-in-box:C7')
def test_declared_query_value_pattern_is_enforced(stub_server):
    endpoint = _endpoint(
        path_template="/v1/export",
        methods=("GET",),
        allowed_query=("mode",),
        query_patterns={"mode": "safe"},
        host="public.example",
    )
    stub_server.stub.update(status=200, body=b"ok")
    driver, _calls, _ctx, port = _local_driver(stub_server)
    ok = driver(
        bundle=ConnectionSecretBundle(token="tok-not-in-response"),
        auth_scheme="bearer",
        method="GET",
        url=f"https://public.example:{port}/v1/export?mode=safe",
        allowed_endpoints=(endpoint,),
    )
    assert ok["status"] == 200

    refuser, _, _, _ = _local_driver(stub_server, allowed_ports=frozenset({443}))
    result = refuser(
        bundle=ConnectionSecretBundle(token="t"),
        auth_scheme="bearer",
        method="GET",
        url="https://public.example/v1/export?mode=admin",
        allowed_endpoints=(endpoint,),
    )
    assert result["status"] == 200
    assert "authorization" not in stub_server.stub["received"]["headers"]


from tests.support.egress import admitted_egress  # noqa: E402, F401
