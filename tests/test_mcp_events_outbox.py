"""A box reports completed runs; the broker queues and delivers (plan P0-1, P0-4, P0-5).

The real terminal outbox drives ``capture_terminal`` in the box. The box's broker
channel is the real ``box_operation`` on a real broker store, bound to the box's
owner as the relay binds it. The last tests run the real broker server ops.
"""

import asyncio
import json
import os
import socket
import time
from types import SimpleNamespace

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.test_automations import OWNER, UNIVERSE, _seed_branch, _seed_owner
from tests.test_broker_server import broker  # noqa: F401
from tests.test_mcp_events import SECRET, params, rows
from tinyassets import rpc_frames as rf
from tinyassets.auth.middleware import identity_context
from tinyassets.auth.provider import Identity
from tinyassets.broker.mcp_events import EventStore, box_operation
from tinyassets.runs import _connect, create_run, deliver_terminal_events, update_run_status

pytestmark = pytest.mark.usefixtures("cloud_runtime")
FOLLOWED = "storage-workflow"


def answering(owner, url, body, headers):
    data = json.loads(body)
    return 200, json.dumps({"challenge": data.get("challenge")}).encode()


@pytest.fixture(name="home")
def event_home(tmp_path, monkeypatch):
    # Events need durable runs/ACLs, not a model credential or an execution assignment.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    _seed_owner(tmp_path)
    from tinyassets.daemon_server import grant_universe_ownership

    grant_universe_ownership(tmp_path, universe_id=UNIVERSE, owner_id=OWNER)
    _seed_branch(tmp_path, branch_def_id=FOLLOWED, visibility="private")
    return tmp_path


@pytest.fixture(name="channel")
def box_channel(home, tmp_path, monkeypatch):
    """This box's broker channel, bound to its owner; every report is recorded."""
    sent, reports = [], []

    def sender(owner, url, body, headers):
        sent.append(json.loads(body))
        return answering(owner, url, body, headers)

    store = EventStore(tmp_path / "broker-state" / "mcp-events.db", sender=sender)
    document = params()
    document["arguments"].update(command_center_id=UNIVERSE, branch_def_id=FOLLOWED)

    def request(center, doc):
        reports.append(doc)
        try:
            return box_operation(store, OWNER, doc)
        except Exception as exc:  # The broker answers BOX_REFUSED.
            raise PermissionError("owner broker operation refused") from exc

    monkeypatch.setattr("tinyassets.owner_channel.request", request)
    monkeypatch.setattr("tinyassets.mcp_events._box_owner", lambda: OWNER)
    return SimpleNamespace(store=store, sent=sent, reports=reports, document=document)


def _outside_grant(home, monkeypatch):
    """A scoped outside client's broker grant, and its identity as the router stamps it."""
    from tests.support.outside_grants import LocalBroker

    grants = LocalBroker(home, monkeypatch, owners=(OWNER,))
    client = "c" * 64
    grants.grant(OWNER, client, source="mcp", mode="scoped", scopes=[
        {"universe": UNIVERSE, "agent": "main", "capability": "read_graph"}])
    bound = {"client": client, "family": "f" * 64, "authenticated_at": int(time.time()),
             **grants.store.outside_check(OWNER, client, None)}
    return grants, bound


def run(home, *, inputs, status="completed", output=None):
    with identity_context(Identity(OWNER, OWNER)):
        run_id = create_run(home, branch_def_id=FOLLOWED, thread_id="t", actor=OWNER,
                            queue_universe_id=UNIVERSE, inputs=inputs)
    update_run_status(home, run_id, status="running")
    update_run_status(home, run_id, status=status, output=output or {"saved": True})
    return run_id


def owed(home, run_id):
    with _connect(home) as conn:
        return conn.execute(
            "SELECT delivered_at FROM run_terminal_outbox WHERE run_id=?", (run_id,)
        ).fetchone()[0] is None


@pytest.mark.verifies("native-mcp-events:A box reports runs as IDs and rechecks authority")
def test_real_terminal_outbox_survives_failed_fanout_then_deduplicates(home, channel,
                                                                       monkeypatch):
    from tinyassets import runs

    channel.store.subscribe(OWNER, channel.document)
    local_events = []
    local_emit = runs._emit_run_completed

    def observe_local(*args, **kwargs):
        local_events.append(args[1])
        return local_emit(*args, **kwargs)

    monkeypatch.setattr(runs, "_emit_run_completed", observe_local)
    real = channel.store.capture

    def unavailable(*args, **kwargs):
        raise OSError("broker store unavailable")

    monkeypatch.setattr(channel.store, "capture", unavailable)
    run_id = run(home, inputs={"operation": "save", "text": "private context"})
    assert local_events == [run_id]
    assert owed(home, run_id)
    monkeypatch.setattr(channel.store, "capture", real)
    assert deliver_terminal_events(home, older_than=0) == 1
    assert len(rows(channel.store, "deliveries")) == 1
    # Crash after fanout but before source acknowledgment: repeat the same source row.
    with _connect(home) as conn:
        conn.execute("UPDATE run_terminal_outbox SET delivered_at=NULL WHERE run_id=?", (run_id,))
    assert deliver_terminal_events(home, older_than=0) == 1
    assert len(rows(channel.store, "deliveries")) == 1
    channel.store.pump()
    assert channel.sent[-1]["data"]["run_id"] == run_id
    # Nothing but identifiers crosses the box channel or reaches the callback.
    assert "private context" not in json.dumps(channel.sent)
    assert "private context" not in json.dumps(channel.reports)
    assert all(set(doc["event"]) == {"command_center_id", "branch_def_id", "run_id",
                                     "event_id", "occurred"} for doc in channel.reports)


@pytest.mark.verifies("native-mcp-events:Only an owner follows an exact workflow; only IDs leave")
@pytest.mark.parametrize("status", ["failed", "cancelled", "interrupted"])
def test_terminal_failure_does_not_emit(home, channel, status):
    channel.store.subscribe(OWNER, channel.document)
    run(home, inputs={"operation": "save"}, status=status)
    assert channel.reports == []
    assert rows(channel.store, "deliveries") == []


@pytest.mark.verifies("native-mcp-events:Only an owner follows an exact workflow; only IDs leave")
@pytest.mark.parametrize(
    "inputs,output",
    [
        ({"operation": "reply"}, {"saved": True}),
        ({"operation": "save", "data": {"operation": "reply"}}, {"saved": True}),
        ({"operation": "save"}, {"saved": False}),
        ({"operation": "save"}, {"saved": 1}),
        ({}, {"saved": True}),
    ],
)
def test_exact_filters_and_reply_suppression_are_decided_in_the_box(home, channel, inputs,
                                                                     output):
    channel.store.subscribe(OWNER, channel.document)
    run_id = run(home, inputs=inputs, output=output)
    assert rows(channel.store, "deliveries") == []
    assert not owed(home, run_id)
    assert rows(channel.store, "subscriptions")[0]["active"] == 1


@pytest.mark.verifies("native-mcp-events:Only an owner follows an exact workflow; only IDs leave")
@pytest.mark.parametrize("value", [float("nan"), float("inf"), {}, [True]])
def test_noncanonical_output_is_nonmatch_not_outbox_poison(home, channel, value):
    channel.store.subscribe(OWNER, channel.document)
    run_id = run(home, inputs={"operation": "save"}, output={"saved": value})
    assert rows(channel.store, "deliveries") == []
    assert not owed(home, run_id)
    assert rows(channel.store, "subscriptions")[0]["active"] == 1


@pytest.mark.verifies("native-mcp-events:A box reports runs as IDs and rechecks authority")
def test_another_principals_run_is_not_reported(home, channel, monkeypatch):
    channel.store.subscribe(OWNER, channel.document)
    monkeypatch.setattr("tinyassets.mcp_events._box_owner", lambda: "acct_other")
    run(home, inputs={"operation": "save"})
    assert channel.reports == []


@pytest.mark.verifies("native-mcp-events:A box reports runs as IDs and rechecks authority")
def test_broker_unavailable_keeps_the_source_owed(home, channel, monkeypatch):
    channel.store.subscribe(OWNER, channel.document)

    def refused(center, doc):
        raise PermissionError("owner broker operation refused")

    monkeypatch.setattr("tinyassets.owner_channel.request", refused)
    run_id = run(home, inputs={"operation": "save"})
    assert owed(home, run_id)


@pytest.mark.verifies("native-mcp-events:A box reports runs as IDs and rechecks authority")
def test_saved_outside_authority_is_rechecked_at_capture(home, channel, monkeypatch):
    grants, bound = _outside_grant(home, monkeypatch)
    channel.store.subscribe(OWNER, channel.document, authority=bound)
    run(home, inputs={"operation": "save"})
    assert len(rows(channel.store, "deliveries")) == 1
    grants.grant(OWNER, bound["client"], source="mcp", mode="revoked")
    run(home, inputs={"operation": "save"})
    record = rows(channel.store, "subscriptions")[0]
    assert record["active"] == 0 and record["config"] == ""
    assert rows(channel.store, "deliveries")[0]["state"] == "stopped"


@pytest.mark.verifies("native-mcp-events:A box reports runs as IDs and rechecks authority")
def test_the_broker_revokes_a_subscription_its_grant_no_longer_admits(home, channel,
                                                                     monkeypatch):
    from functools import partial

    from tinyassets.broker.policy import outside_admits as _outside_admits

    grants, bound = _outside_grant(home, monkeypatch)
    channel.store.subscribe(OWNER, channel.document, authority=bound)
    admits = partial(_outside_admits, grants.store, OWNER)
    assert admits(bound)
    grants.grant(OWNER, bound["client"], source="mcp", mode="scoped", scopes=bound["scopes"])
    assert not admits(bound) and not partial(_outside_admits, None, OWNER)(bound)
    # A box that still allows it (a stale box) cannot keep it: the broker revokes it.
    [subscription] = rows(channel.store, "subscriptions")
    event = {"command_center_id": UNIVERSE, "branch_def_id": FOLLOWED, "run_id": "r1",
             "event_id": "r1#1", "occurred": time.time()}
    assert channel.store.capture(OWNER, event, allow=[subscription["id"]], revoke=[],
                                 admits=admits) == {"queued": 0}
    assert rows(channel.store, "subscriptions")[0]["active"] == 0


@pytest.mark.verifies("native-mcp-events:Subscriptions live in the broker and expire")
def test_poisoned_subscription_has_bounded_outbox_lifetime(home, channel):
    channel.store.subscribe(OWNER, channel.document)
    with channel.store.db() as conn:
        conn.execute("UPDATE subscriptions SET config=?", ("invalid",))
    run_id = run(home, inputs={"operation": "save"})
    assert not owed(home, run_id)
    assert rows(channel.store, "deliveries") == []
    assert rows(channel.store, "subscriptions")[0]["active"] == 0


# -- The real broker server ops -------------------------------------------------


def box_call(broker, doc, owner="alice"):  # noqa: F811
    """One report on a BOX channel: the relay bound ``owner``; the report names none."""
    from tinyassets.broker.server import BOX, _Connection

    async def call():
        connection = _Connection(broker.server, None, BOX)
        connection._box_owner = owner
        await connection._connection_op(doc["op"], doc)
        return rf.Decoder().feed(connection._queue.get_nowait())[0].control()

    return asyncio.run_coroutine_threadsafe(call(), broker.loop).result(15)


@pytest.fixture
def router_root(broker, tmp_path, monkeypatch):  # noqa: F811
    """The router reaches the broker socket as the owner role, as in production."""
    from tinyassets.boxes import layout

    monkeypatch.setattr(layout, "BROKER_UID", os.getuid())
    root = tmp_path / "router"
    (root / "broker").mkdir(parents=True)
    (root / "broker" / "broker.sock").symlink_to(broker.path)
    return root


@pytest.mark.verifies("native-mcp-events:The router subscribes; a box never sees the callback")
def test_router_subscribes_and_box_reports_through_the_broker(broker, router_root,  # noqa: F811
                                                              tmp_path):
    from tinyassets.broker.mcp_events import request
    from tinyassets.broker.policy import PolicyStore

    sent = []

    def sender(owner, url, body, headers):
        sent.append((owner, json.loads(body)))
        return answering(owner, url, body, headers)

    broker.server.mcp_events.store.sender = sender
    # The outside subscriber's grant, which the broker rechecks at capture (P0-3).
    broker.server._policy_store = PolicyStore(tmp_path, broker.server._owner_identities)
    broker.server._policy_store.bootstrap_owner("alice")
    grant = broker.server._policy_store.outside_admit("alice", "c" * 64, "mcp", 1)
    authority = {"client": "c" * 64, "family": "f", "authenticated_at": 1,
                 "generation": grant["generation"]}
    sub = request(router_root, {"action": "subscribe", "principal": "alice",
                                "authority": authority, "params": params()})
    assert sub["id"].startswith("sub_") and sent[0][0] == "alice"
    event = {"command_center_id": "center", "branch_def_id": "save", "run_id": "run1",
             "event_id": "run1#1", "occurred": time.time()}
    matched = box_call(broker, {"op": "MCP_EVENTS", "action": "match", "event": event})
    assert matched == {"op": "BOX_RESULT", "result": [
        {"id": sub["id"], "arguments": params()["arguments"], "authority": authority}]}
    # Another owner's box sees nothing and can queue nothing for Alice.
    assert box_call(broker, {"op": "MCP_EVENTS", "action": "match", "event": event},
                    owner="bob")["op"] == "BOX_REFUSED"  # bob is not admitted
    queued = box_call(broker, {"op": "MCP_EVENTS", "action": "capture", "event": event,
                               "allow": [sub["id"]], "revoke": []})
    assert queued == {"op": "BOX_RESULT", "result": {"queued": 1}}
    deadline = time.monotonic() + 10
    while len(sent) < 2:  # The capture kicked a delivery pass at once.
        assert time.monotonic() < deadline
        time.sleep(0.05)
    assert sent[1] == ("alice", json.loads(rows(broker.server.mcp_events.store,
                                                "deliveries")[0]["body"]))
    assert request(router_root, {"action": "unsubscribe", "principal": "alice",
                                 "authority": None, "params": params()}) == {}


@pytest.mark.verifies("native-mcp-events:Callbacks leave only from the broker via owner egress")
def test_router_refusals_are_bounded_and_carry_no_callback(broker, router_root):  # noqa: F811
    from tinyassets.broker.mcp_events import request
    from tinyassets.mcp_event_webhooks import EventError

    def failing(owner, url, body, headers):
        return 500, b"https://receiver.example " + SECRET.encode()

    broker.server.mcp_events.store.sender = failing
    with pytest.raises(EventError) as error:
        request(router_root, {"action": "subscribe", "principal": "alice",
                              "authority": None, "params": params()})
    assert (error.value.code, error.value.data) == (-32015, {"reason": "challenge_failed"})
    assert "receiver.example" not in str(error.value)
    with pytest.raises(EventError) as error:
        request(router_root, {"action": "subscribe", "principal": "mallory",
                              "authority": None, "params": params()})
    assert error.value.code == -32024  # not an admitted owner
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as channel:
        channel.connect(str(broker.path))
        channel.sendall(rf.control(rf.CONNECTION, {"op": "MCP_EVENTS", "action": "match",
                                                   "event": {}}))
        answer = rf.read_frame_blocking(channel).control()
    assert answer["op"] == "MCP_EVENTS_ERROR"  # the box's report is not a router op


@pytest.mark.verifies("native-mcp-events:A box reports runs as IDs and rechecks authority")
def test_a_run_without_a_workflow_is_acknowledged_without_a_report(home, channel):
    """An activity session's run has no branch_def_id; no subscription can name it."""
    from tinyassets.mcp_events import capture_terminal

    channel.store.subscribe(OWNER, channel.document)
    capture_terminal(home, {
        "status": "completed", "queue_universe_id": UNIVERSE, "actor": f"universe:{UNIVERSE}",
        "cause_principal": OWNER, "inputs_json": "{}", "output_json": "{}",
        "branch_def_id": "", "run_id": "r1", "seq": 1, "created_at": time.time()})
    assert channel.reports == []


@pytest.mark.verifies("native-mcp-events:A box reports runs as IDs and rechecks authority")
def test_a_changed_grant_generation_ends_the_subscription(home, channel, monkeypatch):
    grants, admitted = _outside_grant(home, monkeypatch)
    assert admitted["generation"] == 1
    channel.store.subscribe(OWNER, channel.document, authority=admitted)
    # Narrowed and widened again: same scopes, a new generation.
    grants.grant(OWNER, admitted["client"], source="mcp", mode="scoped",
                 scopes=admitted["scopes"])
    run(home, inputs={"operation": "save"})
    assert rows(channel.store, "subscriptions")[0]["active"] == 0
    assert rows(channel.store, "deliveries") == []
