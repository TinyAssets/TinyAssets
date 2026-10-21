"""Streamed upstream responses (S6, I14): the driver's open_stream and the broker's stream.

The driver tests run the real pinned transport against a local stub server
that dribbles a chunked body, through the same seams the request/close tests
use (a loopback socket, a pass-through TLS context). The broker tests use the
real ledger and an injected network driver.
"""

from __future__ import annotations

import http.server
import socket
import threading
import time

import pytest

from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tinyassets.storage.outbound_connections import (
    BrokerStream,
    ConnectionLedger,
    ConnectionSecretBundle,
    CredentialBlindBroker,
    OutboundDeadlineExceeded,
    ProxyRequestError,
    UpstreamStream,
    _parse_allowed_endpoints,
    _SsrfHardenedHttpDriver,
)

SECRET = "s3cr3t-bot-token"


class _Dribble(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):
        return

    def do_POST(self):  # noqa: N802 - http.server API
        stub = self.server.stub  # type: ignore[attr-defined]
        length = int(self.headers.get("Content-Length") or 0)
        self.rfile.read(length)
        self.send_response(stub.get("status", 200), stub.get("reason"))
        for name, value in stub.get("headers", ()):
            self.send_header(name, value)
        self.send_header("Transfer-Encoding", "chunked")
        self.end_headers()
        for piece in stub["pieces"]:
            if piece is None:
                time.sleep(stub.get("pause", 0.3))
                continue
            self.wfile.write(f"{len(piece):x}\r\n".encode() + piece + b"\r\n")
            self.wfile.flush()
        if not stub.get("hang"):
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        else:
            time.sleep(5)


@pytest.fixture
def dribble():
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Dribble)
    server.stub = {"pieces": [b"data: one\n\n"]}  # type: ignore[attr-defined]
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()


def _driver(server, **overrides):
    port = server.server_address[1]

    def open_socket(address, timeout, _source):
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    kwargs = dict(resolver=lambda *_: ["127.0.0.1"], validator=lambda addr: addr,
                  open_socket=open_socket, ssl_context=_PassThroughTLS(),
                  allowed_ports=frozenset({port}))
    kwargs.update(overrides)
    return _SsrfHardenedHttpDriver(**kwargs), port


def _open(server, **overrides):
    stream_kwargs = {k: overrides.pop(k) for k in ("idle_s", "reply_budget_s") if k in overrides}
    driver, port = _driver(server, **overrides)
    return driver.open_stream(
        bundle=ConnectionSecretBundle(token=SECRET), auth_scheme="bearer", method="POST",
        url=f"https://models.example:{port}/v1/chat", headers={"Content-Type": "application/json"},
        allowed_endpoints=_parse_allowed_endpoints([{
            "host": "models.example", "path_template": "/v1/chat", "methods": ["POST"]}]),
        body={"stream": True}, **stream_kwargs,
    )


def _drain(stream):
    out = bytearray()
    while chunk := stream.read(65536):
        out += chunk
    return bytes(out)


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_header_echoing_the_credential_is_refused_before_a_stream_exists(dribble):
    dribble.stub.update(headers=[("X-Echo", f"Bearer {SECRET}")])
    with pytest.raises(ProxyRequestError):
        _open(dribble)


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_reason_phrase_echoing_the_credential_is_refused(dribble):
    dribble.stub.update(reason=SECRET)
    with pytest.raises(ProxyRequestError):
        _open(dribble)


# ── the broker's stream ─────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path):
    ledger = ConnectionLedger(tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
                              verify_authenticated_principal=lambda: "owner")
    ledger.create_connection(
        connection_id="conn-model", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
        # These transport/secret-scanning cases send ordinary HTTP, with no
        # model invocation. Model grants require the real accounting envelope.
        destination="stream-service", credential_ref="vault://http/synthetic",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                            "methods": ["POST"]}],
    )
    ledger.grant_connection(grant_id="grant-model", connection_id="conn-model",
                            owner_user_id="owner")
    return ledger


def _broker(ledger, pieces, *, headers=None, credential="held-credential-xyz"):
    calls = []

    def network(**kwargs):
        calls.append(kwargs)
        assert kwargs["stream"] is True
        return UpstreamStream.complete(
            {"status": 200, "reason": "OK", "headers": headers or {},
             "body": b"".join(pieces)}, (),
        )

    return CredentialBlindBroker(ledger, resolve_credential=lambda *_: credential,
                                 network_request=network), calls


def _read_all(stream: BrokerStream) -> bytes:
    out = bytearray()
    while (chunk := stream.read(7)) is not None:
        out += chunk
    return bytes(out)


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_the_broker_withholds_its_own_held_value_from_the_body(ledger):
    broker, _ = _broker(ledger, [b"data: leaked held-credential-xyz\n\n"])
    stream = broker.dispatch("grant-model", "POST",
                             {"url": "https://models.example.com/v1/chat", "body": {}},
                             stream=True)
    forwarded = bytearray()
    with pytest.raises(ProxyRequestError):
        while (chunk := stream.read(5)) is not None:
            forwarded += chunk
    assert b"held-credential-xyz" not in bytes(forwarded)
    assert len(forwarded) <= len(b"data: leaked ")


def _swallow(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - the test only needs it to return
        pass


@pytest.fixture
def oauth_broker(ledger, monkeypatch):
    from dataclasses import replace

    from tinyassets.connection_oauth.tokens import TokenBundle, encode

    resource = ledger._active_resource_for_grant("grant-model")
    monkeypatch.setattr(ledger, "_active_resource_for_grant",
                        lambda _: replace(resource, auth_scheme="oauth2"))
    original = TokenBundle("old-access-token", "https://auth.example/token", "client",
                           refresh_token="old-refresh-token", expires_at=0)
    broker, calls = _broker(ledger, [b"ok"], credential=encode(original))
    return broker, calls, replace(original, access_token="new-access-token",
                                  refresh_token="new-refresh-token")


@pytest.mark.verifies('owner-daemon-in-box:T5')
@pytest.mark.parametrize("expired", [False, True])
def test_oauth_refresh_is_guarded_and_checks_deadline(oauth_broker, monkeypatch, expired):
    broker, calls, bundle = oauth_broker
    entered, refreshes = [], []

    class Guard:
        def __enter__(self):
            entered.append(True)

        def __exit__(self, *args):
            entered.pop()

    def refresh(*args, **kwargs):
        assert entered
        refreshes.append(kwargs)
        return bundle

    monkeypatch.setattr(CredentialBlindBroker, "_oauth_bundle", refresh)
    if expired:
        with pytest.raises(OutboundDeadlineExceeded):
            broker.dispatch("grant-model", "POST", {"url": "https://models.example.com/v1/chat"},
                            stream=True, guard=Guard,
                            deadline_at=time.monotonic() - 1)
        assert not refreshes and not calls
    else:
        def network(**kwargs):
            assert kwargs["checkpoint"] is checkpoint
            return UpstreamStream.complete({"status": 401, "body": b""}, ())

        def checkpoint():
            pass
        broker._network_request = network
        broker.dispatch("grant-model", "POST", {"url": "https://models.example.com/v1/chat"},
                        stream=True, guard=Guard,
                        checkpoint=checkpoint, deadline_at=time.monotonic() + 30)
        assert len(refreshes) == 2


@pytest.mark.verifies('owner-daemon-in-box:T6')
@pytest.mark.parametrize("echo", ["body", "header"])
def test_proactive_refresh_keeps_original_tokens_held(oauth_broker, monkeypatch, echo):
    broker, _, bundle = oauth_broker
    events = []
    monkeypatch.setattr(CredentialBlindBroker, "_oauth_bundle",
                        lambda *args, **kwargs: events.append("refresh") or bundle)

    def network(**kwargs):
        events.append("send")
        return UpstreamStream.complete({
            "status": 200, "body": b"old-access-token" if echo == "body" else b"ok",
            "headers": {"x-echo": "old-refresh-token"} if echo == "header" else {},
        }, ())

    broker._network_request = network
    with pytest.raises(ProxyRequestError):
        _read_all(broker.dispatch("grant-model", "POST", {
            "url": "https://models.example.com/v1/chat"}, stream=True))
    assert events == ["refresh", "send"]


from tests.support.egress import admitted_egress  # noqa: E402, F401
