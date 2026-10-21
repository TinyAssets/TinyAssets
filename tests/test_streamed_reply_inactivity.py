"""A streamed model reply is judged by whether it keeps arriving, not by its length.

Founder, 2026-10-02: "if the model response is just slow your skipping it and
then that call is used up for the user". On a capped free tier every abandoned
reply is one of the day's requests. The broker now reads a streamed inference
reply with a per-read INACTIVITY window: a reply that keeps arriving runs past
the old whole-reply ceiling; one that goes silent is returned as far as it got,
marked ``stalled``; a slow drip still ends at the outer total.

Real sockets against the real driver; the times are scaled down.
"""

import socket
import threading

import pytest

from tests.test_inference_reply_budget import (  # noqa: F401 - broker is a fixture
    INFERENCE_URL,
    _read_request,
    broker,
)
from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tinyassets.storage.outbound_connections import (
    ConnectionSecretBundle,
    _SsrfHardenedHttpDriver,
)


def _event(n):
    return f'data: {{"choices":[{{"index":0,"delta":{{"content":"w{n} "}}}}]}}\n\n'.encode()


def _stream_server(*, events, gap_s, then_silent=False, drip=False):
    """A chunked SSE stream: ``events`` events ``gap_s`` apart, then the end,
    or silence (held open until teardown), or an endless drip."""
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def chunk(data):
        return f"{len(data):x}\r\n".encode() + data + b"\r\n"

    def serve():
        try:
            conn, _ = listener.accept()
            try:
                _read_request(conn)
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Type: text/event-stream\r\n"
                             b"Transfer-Encoding: chunked\r\n\r\n")
                n = 0
                while drip and not stop.wait(gap_s):
                    conn.sendall(chunk(b":\n"))
                for n in range(events):
                    if stop.wait(gap_s):
                        return
                    conn.sendall(chunk(_event(n)))
                if then_silent:
                    stop.wait(30)
                    return
                conn.sendall(chunk(b"data: [DONE]\n\n") + b"0\r\n\r\n")
            finally:
                conn.close()
        except OSError:
            pass
        finally:
            listener.close()

    threading.Thread(target=serve, daemon=True).start()

    def open_socket(_address, timeout, _source_address):
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    driver = _SsrfHardenedHttpDriver(
        resolver=lambda _h, _p: ["127.0.0.1"], validator=lambda addr: addr,
        open_socket=open_socket, ssl_context=_PassThroughTLS(),
        allowed_ports=frozenset({port}), timeout=0.3, max_total_seconds=0.3,
    )
    return driver, port, stop


def _call(driver, port, **extra):
    return driver(
        bundle=ConnectionSecretBundle(token="stream-token-not-in-response"),
        auth_scheme="bearer", method="POST",
        url=f"https://public.example:{port}/v1/chat/completions", body={}, **extra,
    )


# --------------------------------------------------------------------------
# The broker grants the window only to a model source that got the budget.
# --------------------------------------------------------------------------

def _post(broker, grant, budget, idle, verb="POST"):  # noqa: F811
    instance, calls, _ = broker
    instance.dispatch(grant, verb, {"url": INFERENCE_URL, "body": {},
                                    "reply_budget_s": budget, "reply_idle_s": idle})
    return calls[-1]


# --------------------------------------------------------------------------
# Codex review of the streaming commit (2026-10-02).
# --------------------------------------------------------------------------


def _header_drip_server():
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(1)
    port = listener.getsockname()[1]
    stop = threading.Event()

    def serve():
        try:
            conn, _ = listener.accept()
            try:
                _read_request(conn)
                conn.sendall(b"HTTP/1.1 200 OK\r\n")
                while not stop.wait(0.1):
                    conn.sendall(b"X-Drip: a\r\n")
            finally:
                conn.close()
        except OSError:
            pass
        finally:
            listener.close()

    threading.Thread(target=serve, daemon=True).start()

    def open_socket(_address, timeout, _source_address):
        return socket.create_connection(("127.0.0.1", port), timeout=timeout)

    driver = _SsrfHardenedHttpDriver(
        resolver=lambda _h, _p: ["127.0.0.1"], validator=lambda addr: addr,
        open_socket=open_socket, ssl_context=_PassThroughTLS(),
        allowed_ports=frozenset({port}), timeout=0.3, max_total_seconds=0.3,
    )
    return driver, port, stop


@pytest.mark.verifies('owner-daemon-in-box:T6')
def test_a_credential_split_across_streamed_deltas_is_refused(broker):  # noqa: F811
    """Each delta passes a substring scan; the rejoined reply would not."""
    instance, _calls, outcome = broker
    from tinyassets.storage.outbound_connections import ProxyRequestError

    split = "\n".join([
        'data: {"choices":[{"delta":{"content":"key: synthetic-"}}]}', "",
        'data: {"choices":[{"delta":{"content":"nonsecret"}}]}', "",
        "data: [DONE]", "",
    ])
    outcome["response"] = {"status": 200, "body": split}
    with pytest.raises(ProxyRequestError, match="unsafe destination response"):
        instance.dispatch("grant-model", "POST", {"url": INFERENCE_URL, "body": {}})


from tests.support.egress import admitted_egress  # noqa: E402, F401
