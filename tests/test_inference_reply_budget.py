"""A model writing a whole app gets its answer; a slow answer says so.

Live 2026-09-29, free-only universe ``u-01ky3zh1arr8qth8jee7zx63pq``, turn
``b804819f1da04025a024734680e24fbc``: three rounds on a free model answered, and
the fourth -- the one writing the app -- died 35.8s in. The credential broker's
network driver ends EVERY request at a 30s total (``_SSRF_MAX_TOTAL_SECONDS``);
a non-streaming model sends nothing until it has finished. The deadline error
was then flattened to "outbound request failed", recorded as class ``unknown``,
and the owner read "we could not identify why".

The longer budget is decided by the broker from the CONNECTION (it carries a
model capability) and never from what the request claims; everything else the
driver bounds is unchanged.
"""

import math
import socket
import threading
import time

import pytest

from tests import test_interactive_http_agent as integration
from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tinyassets.exceptions import AllProvidersExhaustedError
from tinyassets.storage.outbound_connections import (
    INFERENCE_MAX_SECONDS,
    ConnectionLedger,
    ConnectionSecretBundle,
    CredentialBlindBroker,
    OutboundDeadlineExceeded,
    ProxyRequestError,
    _adapter_safe_proxy_error,
    _ProxyChannel,
    _SsrfHardenedHttpDriver,
)

rig = integration.rig
reader = integration.reader
served = integration.served
agent = integration.agent

INFERENCE_URL = "https://models.example.com/v1/chat/completions"
MODEL_USE = {
    "wire": "openai_chat",
    "models": [{"id": "lab/model:free", "tools": True, "context": 100000}],
    "billing": "free",
}


# --------------------------------------------------------------------------
# The broker decides the budget from the connection, never from the request.
# --------------------------------------------------------------------------


@pytest.fixture
def broker(tmp_path):
    ledger = ConnectionLedger(
        tmp_path / "outbound.db", verify_authenticated_principal=lambda: "owner"
    )
    for connection_id, grant_id in (("conn-model", "grant-model"), ("conn-plain", "grant-plain")):
        ledger.create_connection(
            connection_id=connection_id, owner_user_id="owner",
            connection_class="http", connection_type="http", auth_scheme="bearer",
            scopes=("GET", "POST"), provider="http", destination=f"compute:{connection_id}",
            credential_ref="vault://http/synthetic",
            allowed_endpoints=[{
                "host": "models.example.com", "path_template": "/v1/chat/completions",
                "methods": ["GET", "POST"],
            }],
        )
        ledger.grant_connection(
            grant_id=grant_id, connection_id=connection_id,
            owner_user_id="owner", universe_id="universe",
        )
    ledger.configure_capability(
        connection_id="conn-model", capability_kind="model_use",
        descriptor=MODEL_USE, enabled=True,
    )
    calls = []
    outcome = {"raise": None}

    def network(**kwargs):
        calls.append(kwargs)
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return {"status": 200, "body": "{}"}

    return CredentialBlindBroker(
        ledger, resolve_credential=lambda *_: "synthetic-nonsecret", network_request=network,
    ), calls, outcome


def _post(broker, grant, budget, verb="POST"):
    instance, calls, _ = broker
    instance.dispatch(grant, verb, {"url": INFERENCE_URL, "body": {}, "reply_budget_s": budget})
    return calls[-1]


def test_a_model_source_gets_the_budget_it_asks_for(broker):
    call = _post(broker, "grant-model", 300)
    assert call["reply_budget_s"] == 300.0
    # The field never reaches the network driver as part of the request.
    assert "reply_budget_s" not in call["request"]


@pytest.mark.parametrize("asked", [10**9, 10**1000, 1e300])
def test_the_budget_never_exceeds_the_one_ceiling(broker, asked):
    assert _post(broker, "grant-model", asked)["reply_budget_s"] == INFERENCE_MAX_SECONDS


@pytest.mark.parametrize("grant,budget,verb", [
    # A connection that is not a model source cannot ask its way to 600s.
    ("grant-plain", 600, "POST"),
    # Inference is a POST; a GET on a model source keeps the ordinary bound.
    ("grant-model", 600, "GET"),
    # Not a number the broker will read as one.
    ("grant-model", True, "POST"),
    ("grant-model", "600", "POST"),
    ("grant-model", math.inf, "POST"),
    ("grant-model", math.nan, "POST"),
    # Asking for less than the ordinary bound changes nothing.
    ("grant-model", 10, "POST"),
])
def test_everything_else_keeps_the_ordinary_bound(broker, grant, budget, verb):
    call = _post(broker, grant, budget, verb)
    assert "reply_budget_s" not in call
    assert "reply_budget_s" not in call["request"]


def test_a_deadline_crosses_the_broker_typed_and_fixed(broker):
    instance, _, outcome = broker
    outcome["raise"] = OutboundDeadlineExceeded("upstream said private-detail-xyz")
    with pytest.raises(OutboundDeadlineExceeded) as error:
        instance.dispatch("grant-model", "POST", {"url": INFERENCE_URL, "body": {}})
    assert "private-detail-xyz" not in str(error.value)
    # Any other failure still flattens, as before.
    outcome["raise"] = RuntimeError("private-detail-xyz")
    with pytest.raises(ProxyRequestError) as error:
        instance.dispatch("grant-model", "POST", {"url": INFERENCE_URL, "body": {}})
    assert type(error.value) is ProxyRequestError


def test_the_child_boundary_keeps_the_deadline_typed():
    assert _adapter_safe_proxy_error(OutboundDeadlineExceeded("x")) == (
        "outbound request exceeded its time budget"
    )

    class Channel:
        def send_bytes(self, _payload):
            pass

        def recv_bytes(self, *_):
            import json

            return json.dumps({
                "ok": False, "error_type": "OutboundDeadlineExceeded",
                "message": "outbound request exceeded its time budget",
            }).encode()

    channel = _ProxyChannel(Channel(), process=None)
    with pytest.raises(OutboundDeadlineExceeded):
        channel.request("POST", {"url": INFERENCE_URL})


# --------------------------------------------------------------------------
# The driver: the budget replaces BOTH timeouts for that one request, and the
# slow-drip guard still ends a drip at the budget.
# --------------------------------------------------------------------------


def _read_request(conn):
    """Read the whole request -- headers AND body -- before answering.

    Closing with unread request bytes makes Windows send RST instead of the
    response, which reads as a destination failure rather than a slow answer.
    """
    data = b""
    while b"\r\n\r\n" not in data:
        data += conn.recv(65535)
    head, _, body = data.partition(b"\r\n\r\n")
    length = 0
    for line in head.split(b"\r\n"):
        if line.lower().startswith(b"content-length:"):
            length = int(line.split(b":", 1)[1])
    while len(body) < length:
        body += conn.recv(65535)


def _slow_server(delay_s, *, drip=False, timeout=0.3, max_total_seconds=0.3):
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
                if drip:
                    conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n")
                    while not stop.wait(0.05):
                        conn.sendall(b"x")
                    return
                # A model that says nothing until it has finished. An event
                # wait, not a sleep: `stop` ends it at teardown instead of
                # leaving the thread asleep into later tests.
                if stop.wait(delay_s):
                    return
                conn.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")
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
        allowed_ports=frozenset({port}),
        # The ordinary bounds, scaled down: 0.3s stands for the 30s.
        timeout=timeout, max_total_seconds=max_total_seconds,
    )
    return driver, port, stop


def _call(driver, port, **extra):
    return driver(
        bundle=ConnectionSecretBundle(token="budget-token-not-in-response"),
        auth_scheme="bearer", method="POST",
        url=f"https://public.example:{port}/x", body={}, **extra,
    )


def test_a_slow_whole_answer_ends_at_the_ordinary_bound_typed():
    driver, port, stop = _slow_server(1.0)
    try:
        with pytest.raises(OutboundDeadlineExceeded):
            _call(driver, port)
    finally:
        stop.set()


def test_a_silent_destination_hitting_the_per_operation_timeout_is_typed_too():
    """The per-operation timeout firing first means the same thing as the total."""
    driver, port, stop = _slow_server(1.0, timeout=0.2, max_total_seconds=5.0)
    try:
        with pytest.raises(OutboundDeadlineExceeded):
            _call(driver, port)
    finally:
        stop.set()


def test_a_slow_whole_answer_arrives_inside_the_budget():
    driver, port, stop = _slow_server(1.0)
    try:
        assert _call(driver, port, reply_budget_s=3.0)["status"] == 200
    finally:
        stop.set()


def test_a_drip_still_ends_at_the_budget():
    driver, port, stop = _slow_server(0, drip=True)
    started = time.monotonic()
    try:
        with pytest.raises(OutboundDeadlineExceeded):
            _call(driver, port, reply_budget_s=0.8)
        assert time.monotonic() - started < 3.0
    finally:
        stop.set()


# --------------------------------------------------------------------------
# The real caller: three models, the live sequence.
# --------------------------------------------------------------------------


def _three(agent, monkeypatch):
    from tests.test_provider_model_refusal import _order

    _order(agent, monkeypatch, ["lab/refusing:free", "lab/slow-writer:free"])
    agent.requested_rounds = 0
    agent.capacity_failures.update({1: 429, 2: 403})


def test_the_live_sequence_asks_for_the_turns_budget_on_every_model(agent, monkeypatch):
    """429, 403, then the third answers -- every request carried the turn's budget.

    That the driver then honours it past the ordinary bound is proven above,
    against a real socket (``test_a_slow_whole_answer_arrives_inside_the_budget``).
    """
    _three(agent, monkeypatch)
    assert integration.run(agent) == "finished exact answer"
    # Every inference asks for the turn's own remaining cap as its budget.
    budgets = [wire[1].get("reply_budget_s") for wire in agent.wires]
    assert all(0 < budget <= agent.config.absolute_cap_s for budget in budgets)
    assert budgets == sorted(budgets, reverse=True)
    assert agent.wires[-1][1]["body"]["model"] == "lab/slow-writer:free"


def test_the_live_sequence_says_the_model_took_too_long(agent, monkeypatch):
    from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
    from tinyassets.universe_server import _served_failure_notice, _served_failure_record

    _three(agent, monkeypatch)

    real_resolve = ApiKeyHttpProvider._resolve_proxy

    def resolve(self, **kwargs):
        proxy = real_resolve(self, **kwargs)
        inner = proxy.request

        def request(verb, document):
            if len(agent.wires) == 2:  # the third model's request
                agent.wires.append((verb, document))
                raise OutboundDeadlineExceeded("outbound request exceeded its time budget")
            return inner(verb, document)

        proxy.request = request
        return proxy

    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy", resolve)
    with pytest.raises(AllProvidersExhaustedError) as error:
        integration.run(agent)
    attempt = error.value.attempts[-1]
    assert attempt.failure_class == "provider_reply_timeout"
    record = _served_failure_record(error.value)
    notice = _served_failure_notice(error.value, record)
    assert record.code == "provider_reply_timeout"
    assert record.stage == "model_request"
    assert "took longer to answer" in notice
    assert "continue" in notice
    assert "could not identify" not in notice
    # A slow answer says nothing about the source: it is not cooled.
    provider = agent.served.context.model_selection.connection_id
    assert agent.served.router._quota.cooldown_remaining(provider) == 0


def test_a_later_round_asks_only_for_what_is_left_of_the_turn(agent):
    """A late round must not get a fresh cap: the broker cannot be cancelled mid-request,
    so a full cap from round N would hold the connection long after the turn ended."""
    agent.requested_rounds = 1
    agent.before_reply = lambda: time.sleep(0.3)
    assert integration.run(agent) == "finished exact answer"
    first, second = (wire[1]["reply_budget_s"] for wire in agent.wires)
    assert first <= agent.config.absolute_cap_s
    assert second <= first - 0.3
