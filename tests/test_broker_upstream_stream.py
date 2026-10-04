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
    SsrfValidationError,
    UpstreamStream,
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
        body={"stream": True}, **stream_kwargs,
    )


def _drain(stream):
    out = bytearray()
    while chunk := stream.read(65536):
        out += chunk
    return bytes(out)


def test_the_first_bytes_arrive_before_the_upstream_finishes(dribble):
    dribble.stub.update(pieces=[b"data: first\n\n", None, None, b"data: last\n\n"], pause=0.4)
    started = time.monotonic()
    stream = _open(dribble)
    first = stream.read(65536)
    first_at = time.monotonic() - started
    rest = _drain(stream)
    assert first == b"data: first\n\n" and rest == b"data: last\n\n"
    assert first_at < 0.6 < time.monotonic() - started
    assert stream.status == 200 and stream.read(10) == b""


def test_silence_longer_than_the_idle_bound_is_a_deadline(dribble):
    dribble.stub.update(pieces=[b"x", None], pause=3, hang=True)
    stream = _open(dribble, idle_s=0.5)
    assert stream.read(10) == b"x"
    with pytest.raises(OutboundDeadlineExceeded):
        stream.read(10)


def test_the_body_bound_is_cumulative(dribble):
    dribble.stub.update(pieces=[b"a" * 600, b"b" * 600])
    stream = _open(dribble, max_body_bytes=1000)
    with pytest.raises(SsrfValidationError):
        _drain(stream)


def test_a_header_echoing_the_credential_is_refused_before_a_stream_exists(dribble):
    dribble.stub.update(headers=[("X-Echo", f"Bearer {SECRET}")])
    with pytest.raises(ProxyRequestError):
        _open(dribble)


def test_a_reason_phrase_echoing_the_credential_is_refused(dribble):
    dribble.stub.update(reason=SECRET)
    with pytest.raises(ProxyRequestError):
        _open(dribble)


def test_close_ends_a_read_waiting_in_another_thread_within_its_idle_bound(dribble):
    dribble.stub.update(pieces=[b"x", None], pause=5, hang=True)
    stream = _open(dribble, idle_s=0.5)
    assert stream.read(10) == b"x"
    errors = []

    def reader():
        try:
            stream.read(10)
        except Exception as exc:  # noqa: BLE001 - the test inspects it
            errors.append(exc)

    thread = threading.Thread(target=reader)
    thread.start()
    time.sleep(0.3)
    stream.close()
    thread.join(3)
    assert not thread.is_alive()


# ── the broker's stream ─────────────────────────────────────────────────────


@pytest.fixture
def ledger(tmp_path):
    ledger = ConnectionLedger(tmp_path / "outbound.db",
                              verify_authenticated_principal=lambda: "owner")
    ledger.create_connection(
        connection_id="conn-model", owner_user_id="owner", connection_class="http",
        connection_type="http", auth_scheme="bearer", scopes=("POST",), provider="http",
        destination="compute:conn-model", credential_ref="vault://http/synthetic",
        allowed_endpoints=[{"host": "models.example.com", "path_template": "/v1/chat",
                            "methods": ["POST"]}],
    )
    ledger.grant_connection(grant_id="grant-model", connection_id="conn-model",
                            owner_user_id="owner", universe_id="universe")
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


def test_the_broker_streams_a_clean_body_whole(ledger):
    broker, calls = _broker(ledger, [b"data: hello\n\n", b"data: [DONE]\n\n"])
    stream = broker.dispatch("grant-model", "POST",
                             {"url": "https://models.example.com/v1/chat", "body": {}},
                             stream=True)
    assert isinstance(stream, BrokerStream) and stream.status == 200
    assert _read_all(stream) == b"data: hello\n\ndata: [DONE]\n\n"
    assert len(calls) == 1


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


def test_the_broker_refuses_a_header_echoing_its_held_value(ledger):
    broker, _ = _broker(ledger, [b"ok"], headers={"x-echo": "held-credential-xyz"})
    with pytest.raises(ProxyRequestError):
        broker.dispatch("grant-model", "POST",
                        {"url": "https://models.example.com/v1/chat", "body": {}}, stream=True)


def test_request_close_dispatch_is_unchanged(ledger):
    calls = []

    def network(**kwargs):
        calls.append(kwargs)
        return {"status": 200, "body": "ok"}

    broker = CredentialBlindBroker(ledger, resolve_credential=lambda *_: "c",
                                   network_request=network)
    result = broker.dispatch("grant-model", "POST",
                             {"url": "https://models.example.com/v1/chat", "body": {}})
    assert result == {"status": 200, "body": "ok"} and "stream" not in calls[0]


def test_an_oauth1_signature_alone_is_held_encoded_and_decoded():
    import re
    import urllib.parse

    driver = _SsrfHardenedHttpDriver(open_socket=lambda *a: None)
    bundle = ConnectionSecretBundle(api_key="ck", api_secret="cs", access_token="at",
                                    access_token_secret="ats")
    prepared = driver._prepare(
        bundle=bundle, auth_scheme="oauth1a", method="POST",
        url="https://api.example.com/2/tweets", headers=None, body={"text": "hi"},
        header_name="", allowed_endpoints=None, access_mode="exact",
    )
    header = prepared.auth_headers["Authorization"]
    signature = re.search(r'oauth_signature="([^"]+)"', header).group(1)
    assert signature in prepared.sensitive
    assert urllib.parse.unquote(signature) in prepared.sensitive


def test_close_from_another_thread_never_blocks_behind_a_read(dribble):
    dribble.stub.update(pieces=[b"x", None], pause=5, hang=True)
    stream = _open(dribble, idle_s=0.5)
    assert stream.read(10) == b"x"
    reading = threading.Thread(target=lambda: _swallow(stream.read, 10))
    reading.start()
    time.sleep(0.3)
    started = time.monotonic()
    stream.close()
    assert time.monotonic() - started < 0.5
    reading.join(3)
    assert not reading.is_alive()


def _swallow(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - the test only needs it to return
        pass


def test_a_body_hit_is_audited(ledger):
    audits = []

    def network(**kwargs):
        return UpstreamStream.complete(
            {"status": 200, "reason": "OK", "headers": {},
             "body": b"data: held-credential-xyz\n\n"}, ())

    broker = CredentialBlindBroker(ledger, resolve_credential=lambda *_: "held-credential-xyz",
                                   network_request=network, audit=audits.append)
    stream = broker.dispatch("grant-model", "POST",
                             {"url": "https://models.example.com/v1/chat", "body": {}},
                             stream=True)
    with pytest.raises(ProxyRequestError):
        _read_all(stream)
    assert any("credential material" in str(record) for record in audits)


def test_the_guard_is_held_across_every_send_including_the_oauth_resend(ledger):
    entered = []

    class Guard:
        def __enter__(self):
            entered.append("in")

        def __exit__(self, *exc):
            entered.append("out")
            return False

    def network(**kwargs):
        assert entered and entered[-1] == "in"  # the send happens inside the guard
        return UpstreamStream.complete({"status": 200, "reason": "OK", "headers": {},
                                        "body": b"ok"}, ())

    broker = CredentialBlindBroker(ledger, resolve_credential=lambda *_: "c",
                                   network_request=network)
    stream = broker.dispatch("grant-model", "POST",
                             {"url": "https://models.example.com/v1/chat", "body": {}},
                             stream=True, guard=Guard)
    assert _read_all(stream) == b"ok" and entered == ["in", "out"]

@pytest.mark.parametrize("operation,args", [("recv", (1,)), ("recv_into", (bytearray(1),)),
                                           ("send", (b"x",)), ("sendall", (b"x",))])
def test_cancel_checkpoint_precedes_every_socket_operation(operation, args):
    from tinyassets.storage.outbound_connections import BrokerStreamStop, _DeadlineSocket

    class Cancelled(BrokerStreamStop):
        pass

    def checkpoint():
        raise Cancelled

    sock = _DeadlineSocket(object(), deadline=time.monotonic() + 30,
                           per_op_timeout=30, checkpoint=checkpoint)
    with pytest.raises(Cancelled):
        getattr(sock, operation)(*args)


@pytest.mark.parametrize("phase", ["write", "read"])
def test_transport_preserves_cancel_type(dribble, phase):
    from tinyassets.storage.outbound_connections import BrokerStreamStop

    class Cancelled(BrokerStreamStop):
        pass

    stopped = False

    def checkpoint():
        if stopped:
            raise Cancelled

    def connected(_sock):
        nonlocal stopped
        stopped = phase == "write"

    dribble.stub.update(pieces=[None, b"x"], pause=0.3)
    driver, port = _driver(dribble)
    if phase == "write":
        with pytest.raises(Cancelled):
            driver.open_stream(bundle=ConnectionSecretBundle(token=SECRET), auth_scheme="bearer",
                               method="POST", url=f"https://models.example:{port}/v1/chat",
                               checkpoint=checkpoint, on_connect=connected)
    else:
        stream = driver.open_stream(bundle=ConnectionSecretBundle(token=SECRET),
                                    auth_scheme="bearer", method="POST",
                                    url=f"https://models.example:{port}/v1/chat",
                                    checkpoint=checkpoint)
        stopped = True
        with pytest.raises(Cancelled):
            _drain(stream)


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
            broker.dispatch("grant-model", "POST", {}, stream=True, guard=Guard,
                            deadline_at=time.monotonic() - 1)
        assert not refreshes and not calls
    else:
        def network(**kwargs):
            assert kwargs["checkpoint"] is checkpoint
            return UpstreamStream.complete({"status": 401, "body": b""}, ())

        def checkpoint():
            pass
        broker._network_request = network
        broker.dispatch("grant-model", "POST", {}, stream=True, guard=Guard,
                        checkpoint=checkpoint, deadline_at=time.monotonic() + 30)
        assert len(refreshes) == 2


@pytest.mark.parametrize("echo", ["body", "header"])
def test_proactive_refresh_keeps_original_tokens_held(oauth_broker, monkeypatch, echo):
    broker, _, bundle = oauth_broker
    monkeypatch.setattr(CredentialBlindBroker, "_oauth_bundle", lambda *args, **kwargs: bundle)
    broker._network_request = lambda **kwargs: UpstreamStream.complete({
        "status": 200, "body": b"old-access-token" if echo == "body" else b"ok",
        "headers": {"x-echo": "old-refresh-token"} if echo == "header" else {},
    }, ())
    with pytest.raises(ProxyRequestError):
        _read_all(broker.dispatch("grant-model", "POST", {}, stream=True))


def test_redirect_stream_reports_each_connection():
    from tests.test_http_redirect_chain import SOURCE, _driver, _redirect, chain
    from tinyassets.storage.outbound_connections import _parse_allowed_endpoints

    fixture = chain.__wrapped__()
    server = next(fixture)
    try:
        server.state["responses"] = [_redirect("https://cdn.example.com/file"), {"body": b"ok"}]
        connected = []
        stream = _driver(server).open_stream(
            bundle=ConnectionSecretBundle(token=SECRET), auth_scheme="bearer", method="GET",
            url="https://api.example.com/download",
            allowed_endpoints=_parse_allowed_endpoints([SOURCE]),
            revalidate_authority=lambda deadline: None, on_connect=connected.append,
        )
        assert _drain(stream) == b"ok" and len(connected) == 2
    finally:
        fixture.close()
