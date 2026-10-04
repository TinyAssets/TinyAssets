"""Real ledger, private worker/broker IPC, synthetic credential and HTTP boundaries."""
from __future__ import annotations

import asyncio
import json
import multiprocessing
import os
import threading
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from tinyassets.broker.client import BrokerClient
from tinyassets.broker.fence import Fence
from tinyassets.broker.ops import OpStore, new_op_id
from tinyassets.broker.server import OWNER, BrokerServer
from tinyassets.connection_oauth.tokens import TokenBundle, encode
from tinyassets.exceptions import ProviderAuthorityHeldError
from tinyassets.providers.definition import ProviderDefinition, _definition_id
from tinyassets.request_budget import RequestBudgetExceeded, TurnRequestBudget
from tinyassets.storage import outbound_connections as outbound
from tinyassets.storage.agent_request_usage import InferenceUsageStopped

OWNER_ID, UNIVERSE, GRANT, CONNECTION = "owner", "home", "grant-model", "connection-model"
WIRE = {"url": "https://models.example/v1/chat", "body": {"model": "model"}}
BUNDLE = TokenBundle(access_token="synthetic-access-v1", refresh_token="synthetic-refresh",
                     token_url="https://tokens.example/refresh", client_id="synthetic-client")


@pytest.fixture
def rig(tmp_path):
    (tmp_path / UNIVERSE).mkdir()
    ledger = outbound.ConnectionLedger(tmp_path / "outbound.db",
                                        verify_authenticated_principal=lambda: OWNER_ID)
    ledger.create_connection(
        connection_id=CONNECTION, owner_user_id=OWNER_ID, connection_class="http",
        connection_type="http", auth_scheme="oauth2", scopes=("POST",), provider="http",
        destination="compute:synthetic", credential_ref="vault://http/synthetic",
        allowed_endpoints=[dict(host="models.example", path_template="/v1/chat", methods=["POST"])],
    )
    ledger.grant_connection(grant_id=GRANT, connection_id=CONNECTION,
                            owner_user_id=OWNER_ID, universe_id=UNIVERSE)
    fields = dict(universe_id=UNIVERSE, owner_user_id=OWNER_ID, access_method="api_key_http",
                  protocol="openai_chat", model="model", ref=GRANT)
    definition = ProviderDefinition(**fields, id=_definition_id(**fields), visibility="private",
                                    created_at="2026-10-04T00:00:00+00:00")
    (tmp_path / UNIVERSE / "provider_definitions.json").write_text(
        json.dumps([definition.as_dict()]))
    state = SimpleNamespace(base=tmp_path, ledger=ledger, definition=definition)
    state.budgets = []
    yield state
    for budget in state.budgets:
        budget.close()


def reserve(rig, *, limit=6, budget=None, free=True, purpose="reply", wire=None):
    if budget is None:
        budget = TurnRequestBudget(OWNER_ID, UNIVERSE, free_pool_limit=limit)
        budget.persist(rig.base)
        rig.budgets.append(budget)
    ordinal = budget.reserve(owner=OWNER_ID, universe=UNIVERSE,
                             source_ref="api_key_http:" + rig.definition.id,
                             model="model", free=free, purpose=purpose)
    reference = budget.issue_reference(ordinal, grant_id=GRANT, connection_id=CONNECTION,
                                       verb="POST", request=wire or WIRE, operation_id=new_op_id())
    return budget, ordinal, reference


def build_dispatch(base, *, statuses=(200,), events=None, refresh_failure=False,
                   refresh_same=False, on_send=None, before_connect=None, stream_factory=None):
    """Production factory; only credentials/tokens and network are synthetic."""
    events = events if events is not None else []
    remaining = list(statuses)

    def credential(*args):
        events.append("credential")
        return encode(BUNDLE)

    def tokens(*args, rejected="", **kwargs):
        events.append("refresh" if rejected else "token_check")
        if rejected and refresh_failure:
            raise outbound.ConnectionAuthorizationError("synthetic refresh refused")
        if rejected and not refresh_same:
            return replace(BUNDLE, access_token="synthetic-access-v2")
        return BUNDLE

    def network(**kwargs):
        if before_connect is not None:
            before_connect(kwargs)
        if kwargs.get("checkpoint") is not None:
            kwargs["checkpoint"]()
        if kwargs.get("on_connect") is not None:
            kwargs["on_connect"](None)
        events.append("send")
        assert "inference_usage" not in kwargs and "operation_id" not in kwargs
        assert not {"request_budget", "budget", "inference_usage"} & kwargs["request"].keys()
        if on_send is not None:
            on_send(kwargs)
        status = remaining.pop(0) if remaining else 200
        if isinstance(status, BaseException):
            raise status
        body = json.dumps({"choices": [{"message": {"content": "synthetic answer"}}]})
        if kwargs.get("stream"):
            return (stream_factory or SyntheticStream)(status, body.encode())
        return {"status": status, "body": body}

    ledger = outbound.ConnectionLedger(base / "outbound.db",
                                        verify_authenticated_principal=lambda: OWNER_ID)
    config = ledger.broker_dispatch_config(grant_id=GRANT, universe_id=UNIVERSE,
                                           provider="http", destination="compute:synthetic",
                                           owner_user_id=OWNER_ID, connection_type="http")
    # The factory captures the resolver/driver objects. Token keeper is a fake
    # instance; no vault, real token endpoint or provider can be reached.
    with patch.object(outbound, "_TrustedCredentialResolver", lambda cfg: credential), \
            patch.object(outbound, "_TrustedNetworkDriver", lambda cfg, root: network), \
            patch("tinyassets.connection_oauth.tokens.ConnectionTokens",
                  lambda **kwargs: SimpleNamespace(current=tokens)):
        return outbound._build_credential_broker_dispatch(config)


class SyntheticStream:
    def __init__(self, status, body):
        self.status, self.reason, self.headers, self.redirect_count = status, "synthetic", {}, 0
        self.sensitive, self.body = (), body
        self.closed = False

    def read(self, size):
        if self.closed:
            raise RuntimeError("synthetic stream closed")
        if not self.body:
            return None
        result, self.body = self.body[:size], self.body[size:]
        return result

    def close(self):
        self.closed = True


def invoke(dispatch, reference=None, **changes):
    kwargs = dict(grant_id=GRANT, verb="POST", request=WIRE)
    if reference is not None:
        kwargs.update(inference_usage=reference.document(), operation_id=reference.operation_id)
    kwargs.update(changes)
    return dispatch(**kwargs)


def test_oauth_retry_is_reserved_and_separately_counted(rig):
    budget, ordinal, reference = reserve(rig)
    events = []
    dispatch = build_dispatch(rig.base, statuses=(401, 200), events=events)
    assert invoke(dispatch, reference)["status"] == 200
    budget.settle_invocation(ordinal, "succeeded")
    assert events == ["credential", "token_check", "send", "refresh", "send"]
    assert [(a["purpose"], a["state"]) for a in budget.receipt()["attempts"]] == [
        ("reply", "failed"), ("reply", "succeeded"),
    ]
    assert budget.receipt()["dispatched"] == 2


def test_exhausted_retry_does_not_refresh_or_send_again(rig):
    budget, ordinal, reference = reserve(rig, limit=1)
    events = []
    dispatch = build_dispatch(rig.base, statuses=(401, 200), events=events)
    with pytest.raises(RequestBudgetExceeded) as stopped:
        invoke(dispatch, reference)
    assert stopped.value.reason == "free_pool_attempt_limit"
    assert stopped.value.request_receipt["usage_id"] == budget.usage_id
    budget.settle_invocation(ordinal, "failed")
    assert events == ["credential", "token_check", "send"]
    assert budget.receipt()["reserved"] == budget.receipt()["dispatched"] == 1


@pytest.mark.parametrize("change", [
    {}, {"inference_usage": {"limit": 1000}},
    {"request": {**WIRE, "request_budget": 1000}},
    {"request": {**WIRE, "headers": {"X-Request-Budget": "1000"}}},
])
def test_missing_or_spoofed_envelope_cannot_bypass_before_credentials(rig, change):
    events = []
    dispatch = build_dispatch(rig.base, events=events)
    with pytest.raises(ProviderAuthorityHeldError):
        invoke(dispatch, **change)
    assert events == []


@pytest.mark.parametrize("failure,same", [(True, False), (False, True)])
def test_unused_retry_does_not_count_as_a_send(rig, failure, same):
    budget, ordinal, reference = reserve(rig)
    events = []
    dispatch = build_dispatch(rig.base, statuses=(401, 200), events=events,
                              refresh_failure=failure, refresh_same=same)
    if failure:
        with pytest.raises(outbound.ConnectionAuthorizationError):
            invoke(dispatch, reference)
    else:
        assert invoke(dispatch, reference)["status"] == 401
    budget.settle_invocation(ordinal, "failed")
    assert events.count("send") == 1 and events.count("refresh") == 1
    assert [a["state"] for a in budget.receipt()["attempts"]] == ["failed", "not_sent"]


def test_unknown_network_outcome_never_refunds_or_turns_successful(rig):
    budget, ordinal, reference = reserve(rig)
    events = []
    dispatch = build_dispatch(rig.base, statuses=(TimeoutError("synthetic"),), events=events)
    with pytest.raises(outbound.ProxyRequestError):
        invoke(dispatch, reference)
    budget.settle_invocation(ordinal, "failed")
    assert budget.receipt()["attempts"][0]["state"] == "unknown"
    assert budget.receipt()["dispatched"] == 1


def test_close_between_401_and_retry_has_no_refresh_effect(rig):
    budget, ordinal, reference = reserve(rig)
    events = []
    dispatch = build_dispatch(rig.base, statuses=(401,), events=events,
                              on_send=lambda _: budget.close())
    with pytest.raises(RequestBudgetExceeded):
        invoke(dispatch, reference)
    budget.settle_invocation(ordinal, "unknown")
    assert events == ["credential", "token_check", "send"]
    assert budget.receipt()["dispatched"] == 1


def test_paid_and_local_policy_does_not_acquire_a_low_free_cap(rig):
    budget, ordinal, reference = reserve(rig, limit=1, free=False)
    for iteration in range(5):
        if iteration:
            budget, ordinal, reference = reserve(rig, budget=budget, free=False)
        assert invoke(build_dispatch(rig.base, statuses=(401, 200)), reference)["status"] == 200
        budget.settle_invocation(ordinal, "succeeded")
    assert budget.receipt()["dispatched"] == 10


def worker_main(channel, base, statuses):
    # A spawned fixture process uses the production worker loop and the same
    # real dispatcher factory, with only local synthetic IO injected above.
    factories = {"credential_broker_v1": lambda config: build_dispatch(base, statuses=statuses)}
    with patch.dict(outbound._TRUSTED_DISPATCH_FACTORIES, factories):
        outbound._run_proxy_worker(channel, "credential_broker_v1", {}, GRANT, ("POST",))


@pytest.mark.parametrize("limit,expected", [(1, "stopped"), (2, "success")])
def test_spawned_worker_consumes_reference_and_preserves_typed_stop(rig, limit, expected):
    budget, ordinal, reference = reserve(rig, limit=limit)
    context = multiprocessing.get_context("spawn")
    client, server = context.Pipe()
    process = context.Process(target=worker_main, args=(server, rig.base, (401, 200)))
    process.start()
    server.close()
    assert client.poll(10)
    assert outbound._receive_message(client) == {"op": "ready"}
    proxy = outbound._ProxyChannel(client, process)
    try:
        if expected == "stopped":
            with pytest.raises(InferenceUsageStopped) as stopped:
                proxy.request("POST", WIRE, inference_usage=reference)
            assert stopped.value.usage_id == budget.usage_id
        else:
            assert proxy.request("POST", WIRE, inference_usage=reference)["status"] == 200
        budget.settle_invocation(ordinal, "succeeded" if expected == "success" else "failed")
        assert budget.receipt()["dispatched"] == limit
    finally:
        proxy.close()
    assert not process.is_alive()


def test_direct_broker_without_factory_still_requires_usage_before_credentials(rig):
    events = []
    broker = outbound.CredentialBlindBroker(
        rig.ledger, resolve_credential=lambda *_: events.append("credential"),
        network_request=lambda **_: events.append("send"),
    )
    with pytest.raises(ProviderAuthorityHeldError):
        broker.dispatch(GRANT, "POST", WIRE)
    assert events == []


@pytest.mark.parametrize("boundary", ["checkpoint", "on_connect"])
def test_parent_closure_during_connect_fences_send_and_does_not_count(rig, boundary):
    budget, ordinal, reference = reserve(rig)
    events = []

    def before_connect(kwargs):
        budget.close()
        kwargs[boundary](None) if boundary == "on_connect" else kwargs[boundary]()

    dispatch = build_dispatch(rig.base, events=events, before_connect=before_connect)
    with pytest.raises(RequestBudgetExceeded) as stopped:
        invoke(dispatch, reference)
    assert stopped.value.reason == "dispatch_closed"
    assert "send" not in events
    assert budget.receipt()["dispatched"] == 0
    assert budget.receipt()["attempts"][ordinal - 1]["state"] == "not_sent"


def test_concurrent_oauth_invocations_cannot_exceed_parent_free_pool(rig):
    from concurrent.futures import ThreadPoolExecutor

    budget, first, ref1 = reserve(rig, limit=3)
    _, second, ref2 = reserve(rig, budget=budget)
    barrier = threading.Barrier(2)
    events = []

    def make_dispatch():
        first_send = True

        def sync(_):
            nonlocal first_send
            if first_send:
                first_send = False
                barrier.wait(timeout=5)

        return build_dispatch(rig.base, statuses=(401, 200), events=events, on_send=sync)

    dispatches = [make_dispatch(), make_dispatch()]

    def runner(dispatch, reference, ordinal):
        try:
            invoke(dispatch, reference)
            outcome = "succeeded"
        except RequestBudgetExceeded:
            outcome = "failed"
        budget.settle_invocation(ordinal, outcome)
        return outcome

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(runner, dispatches[0], ref1, first),
                   pool.submit(runner, dispatches[1], ref2, second)]
        assert sorted(f.result(timeout=10) for f in futures) == ["failed", "succeeded"]
    assert events.count("send") == budget.receipt()["dispatched"] == 3
    assert events.count("refresh") == 1


@pytest.fixture
def uds_broker(rig):
    fence = Fence(rig.base / "fence.json", verify_lease_proof=lambda g, p: (g, p) == (1, "proof"))
    ops = OpStore(rig.base / "ops.db")
    state = {}
    server = BrokerServer(
        ledger_for=lambda principal: outbound.ConnectionLedger(
            rig.base / "outbound.db", verify_authenticated_principal=lambda: principal),
        dispatch_for=lambda *args: state["dispatch"], ops=ops, fence=fence,
        roles={os.getuid(): OWNER},
    )
    # pytest's long directory names exceed AF_UNIX's 108-byte path bound.
    import tempfile
    from pathlib import Path

    with tempfile.TemporaryDirectory(prefix="budget-uds-") as directory:
        path = Path(directory) / "broker.sock"
        loop = asyncio.new_event_loop()
        ready = threading.Event()
        listeners = []

        def run():
            asyncio.set_event_loop(loop)
            listeners.append(loop.run_until_complete(server.serve(path)))
            ready.set()
            loop.run_forever()
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.close()

        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        assert ready.wait(5)
        generation, token = fence.barrier(1, "proof")
        client = BrokerClient(path, principal=OWNER_ID, command_center=UNIVERSE,
                              fence=lambda: (generation, token), timeout=5)
        yield SimpleNamespace(client=client, state=state, server=server, ops=ops)
        loop.call_soon_threadsafe(listeners[0].close)
        loop.call_soon_threadsafe(loop.stop)
        thread.join(timeout=5)
        assert not thread.is_alive()


def uds_invoke(uds, reference, **changes):
    kwargs = dict(grant_id=GRANT, connection_id=CONNECTION, verb="POST", request=WIRE,
                  op_id=reference.operation_id, inference_usage=reference.document())
    kwargs.update(changes)
    return uds.client.request(**kwargs)


@pytest.mark.parametrize("limit", [1, 2])
def test_real_unix_broker_preserves_retry_usage_and_stop(uds_broker, rig, limit):
    budget, ordinal, reference = reserve(rig, limit=limit)
    events = []
    uds_broker.state["dispatch"] = build_dispatch(rig.base, statuses=(401, 200), events=events)
    if limit == 1:
        with pytest.raises(InferenceUsageStopped) as stopped:
            uds_invoke(uds_broker, reference)
        assert stopped.value.usage_id == budget.usage_id
        assert stopped.value.reason == "free_pool_attempt_limit"
    else:
        assert uds_invoke(uds_broker, reference)["status"] == 200
    budget.settle_invocation(ordinal, "succeeded" if limit == 2 else "failed")
    assert budget.receipt()["dispatched"] == events.count("send") == limit
    assert not uds_broker.server._streams
    assert uds_broker.ops.status(f"{OWNER_ID}|{UNIVERSE}", reference.operation_id).state in {
        "completed", "failed"}


@pytest.mark.parametrize("changes", [
    {"inference_usage": None}, {"inference_usage": {"free_pool_limit": 1000}},
    {"request": {"url": WIRE["url"], "body": {"model": "different"}}},
    {"connection_id": "foreign-connection"},
])
def test_real_unix_broker_tamper_has_zero_credential_or_http_effect(uds_broker, rig, changes):
    budget, ordinal, reference = reserve(rig)
    events = []
    uds_broker.state["dispatch"] = build_dispatch(rig.base, events=events)
    with pytest.raises((ProviderAuthorityHeldError, outbound.GrantResolutionError)):
        uds_invoke(uds_broker, reference, **changes)
    assert events == [] and budget.receipt()["dispatched"] == 0


def test_real_unix_broker_operation_replay_has_no_extra_send(uds_broker, rig):
    from tinyassets.broker.client import BrokerRefused

    budget, ordinal, reference = reserve(rig)
    events = []
    uds_broker.state["dispatch"] = build_dispatch(rig.base, events=events)
    assert uds_invoke(uds_broker, reference)["status"] == 200
    with pytest.raises(BrokerRefused):
        uds_invoke(uds_broker, reference)
    budget.settle_invocation(ordinal, "succeeded")
    assert events.count("send") == budget.receipt()["dispatched"] == 1


def test_usage_settlement_failure_cannot_strand_broker_end(uds_broker, rig, monkeypatch, caplog):
    from tinyassets.storage.agent_request_usage import UsageDispatch

    budget, ordinal, reference = reserve(rig)
    streams = []

    class FailingStream(SyntheticStream):
        def read(self, size):
            raise TimeoutError("synthetic read timeout")

    def make_stream(status, body):
        stream = FailingStream(status, body)
        streams.append(stream)
        return stream

    def cannot_settle(self, outcome):
        raise OSError("synthetic accounting disk unavailable")

    monkeypatch.setattr(UsageDispatch, "settle", cannot_settle)
    uds_broker.state["dispatch"] = build_dispatch(rig.base, stream_factory=make_stream)
    with pytest.raises(outbound.OutboundDeadlineExceeded):
        uds_invoke(uds_broker, reference)
    assert streams[0].closed
    assert not uds_broker.server._streams
    assert uds_broker.ops.status(f"{OWNER_ID}|{UNIVERSE}", reference.operation_id).state == "failed"
    assert "could not finalize inference usage" in caplog.text
    assert budget.receipt()["dispatched"] == 1


@pytest.mark.parametrize("expired_before_send", [True, False])
def test_synthetic_clock_stops_new_send_but_retains_inflight_reply(
    rig, monkeypatch, expired_before_send,
):
    from tinyassets.storage.agent_request_usage import UsageStore

    clock = [1.0]
    load = UsageStore._load

    def timed_load(self, conn, scope, **kwargs):
        return load(self, conn, scope, **{**kwargs, "clock": lambda: clock[0]})

    monkeypatch.setattr(UsageStore, "_load", timed_load)
    budget = TurnRequestBudget(OWNER_ID, UNIVERSE, deadline=10, clock=lambda: clock[0])
    budget.persist(rig.base)
    rig.budgets.append(budget)
    _, ordinal, reference = reserve(rig, budget=budget)
    events = []

    def advance(_):
        clock[0] = 11.0

    dispatch = build_dispatch(
        rig.base, events=events,
        before_connect=advance if expired_before_send else None,
        on_send=None if expired_before_send else advance,
    )
    if expired_before_send:
        with pytest.raises(RequestBudgetExceeded) as stopped:
            invoke(dispatch, reference)
        assert stopped.value.reason == "dispatch_deadline"
        assert "send" not in events and budget.receipt()["dispatched"] == 0
    else:
        assert invoke(dispatch, reference)["status"] == 200
        budget.settle_invocation(ordinal, "succeeded")
        assert events.count("send") == budget.receipt()["dispatched"] == 1
        assert budget.receipt()["attempts"][0]["state"] == "succeeded"
    with pytest.raises(RequestBudgetExceeded):
        reserve(rig, budget=budget)


def test_real_unix_cancel_during_connect_has_no_send_or_refresh(uds_broker, rig):
    budget, ordinal, reference = reserve(rig)
    events = []

    def cancel(_):
        with uds_broker.server._streams_lock:
            streams = list(uds_broker.server._streams.values())
        assert len(streams) == 1
        uds_broker.server.cancel(streams[0])

    uds_broker.state["dispatch"] = build_dispatch(rig.base, before_connect=cancel, events=events)
    with pytest.raises(outbound.ProxyRequestError):
        uds_invoke(uds_broker, reference)
    assert not {"send", "refresh"} & set(events)
    assert budget.receipt()["dispatched"] == 0
    assert budget.receipt()["attempts"][ordinal - 1]["state"] == "not_sent"
    assert not uds_broker.server._streams
    operation = uds_broker.ops.status(f"{OWNER_ID}|{UNIVERSE}", reference.operation_id)
    assert operation.state == "cancelled"
