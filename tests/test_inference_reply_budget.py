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

import json
import socket
import threading

import pytest

from tests import test_interactive_http_agent as integration
from tests.test_outbound_ssrf_driver import _PassThroughTLS
from tinyassets.broker.ops import new_op_id
from tinyassets.providers.definition import ProviderDefinition, _definition_id
from tinyassets.request_budget import TurnRequestBudget
from tinyassets.storage.outbound_connections import (
    ConnectionLedger,
    ConnectionSecretBundle,
    CredentialBlindBroker,
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
    from tinyassets.broker.owner_sources import publish

    ledger = ConnectionLedger(
        tmp_path / ".broker" / "outbound.db", data_root=tmp_path,
        verify_authenticated_principal=lambda: "owner"
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
            owner_user_id="owner",
        )
    ledger.configure_capability(
        connection_id="conn-model", capability_kind="model_use",
        descriptor=MODEL_USE, enabled=True,
    )
    root = tmp_path / "universe"
    root.mkdir()
    fields = dict(universe_id="universe", owner_user_id="owner", access_method="api_key_http",
                  protocol="openai_chat", model="lab/model:free", ref="grant-model")
    definition = ProviderDefinition(**fields, id=_definition_id(**fields), visibility="private",
                                    created_at="2026-10-04T00:00:00+00:00")
    (root / "provider_definitions.json").write_text(json.dumps([definition.as_dict()]))
    publish(ledger, "owner", "universe", definition.as_dict())
    calls = []
    outcome = {"raise": None}

    def network(**kwargs):
        if kwargs.get("on_connect") is not None:
            kwargs["on_connect"](None)
        calls.append(kwargs)
        if outcome["raise"] is not None:
            raise outcome["raise"]
        return outcome.get("response", {"status": 200, "body": "{}"})

    actual = CredentialBlindBroker(
        ledger, resolve_credential=lambda *_: "synthetic-nonsecret", network_request=network,
    )

    class AccountedBroker:
        def dispatch(self, grant, verb, request):
            if grant != "grant-model" or verb != "POST":
                return actual.dispatch(grant, verb, request)
            request = {**request, "body": {"model": "lab/model:free"}}
            budget = TurnRequestBudget("owner", "universe")
            budget.persist(tmp_path)
            ordinal = budget.reserve(owner="owner", universe="universe",
                                     source_ref="api_key_http:" + definition.id,
                                     model="lab/model:free", free=True, purpose="reply")
            reference = budget.issue_reference(
                ordinal, grant_id=grant, connection_id="conn-model", verb=verb,
                request=request, operation_id=new_op_id(),
            )
            try:
                result = actual.dispatch(grant, verb, request, inference_usage=reference.document(),
                                         operation_id=reference.operation_id)
                budget.settle_invocation(ordinal, "succeeded")
                return result
            finally:
                budget.close()

    return AccountedBroker(), calls, outcome


def _post(broker, grant, budget, verb="POST"):
    instance, calls, _ = broker
    instance.dispatch(grant, verb, {"url": INFERENCE_URL, "body": {}, "reply_budget_s": budget})
    return calls[-1]


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


# --------------------------------------------------------------------------
# The real caller: three models, the live sequence.
# --------------------------------------------------------------------------


def _three(agent, monkeypatch):
    from tests.test_provider_model_refusal import _order

    _order(agent, monkeypatch, ["lab/refusing:free", "lab/slow-writer:free"])
    agent.requested_rounds = 0
    agent.capacity_failures.update({1: 429, 2: 403})


from tests.support.egress import admitted_egress  # noqa: E402, F401
