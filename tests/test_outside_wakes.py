"""S3b wakes: the ledger opens one wake per ringing recipient; AGENT_WAKE sends it once."""
from __future__ import annotations

import asyncio
import hashlib
import json
import secrets
import time
from functools import partial
from types import SimpleNamespace

import pytest

from tests.test_mcp_events import SECRET
from tests.test_message_ledger import DOT, MUSE, PROOF, rig  # noqa: F401
from tinyassets.broker.conversations import Conversations
from tinyassets.broker.mcp_events import EventDeliveries, EventStore, error_answer
from tinyassets.broker.message_ledger import LedgerRefused, MessageLedger, agent_wake
from tinyassets.broker.ops import OpStore, new_op_id
from tinyassets.broker.policy import PolicyStore, account_path
from tinyassets.control_plane.outside_wakes import run_due
from tinyassets.mcp_event_webhooks import canonical
from tinyassets.mcp_events import EVENTS, MAILBOX


def mailbox(agent_id, url="https://receiver.example/wake"):
    return {"name": MAILBOX, "arguments": {"command_center_id": "alpha", "agent_id": agent_id},
            "delivery": {"mode": "webhook", "url": url, "secret": SECRET}}


@pytest.fixture
def wk(rig, tmp_path):  # noqa: F811
    sent, answers = [], []

    def sender(owner, url, body, headers, retry_after=False):
        if "challenge" in json.loads(body):
            return 200, json.dumps({"challenge": json.loads(body)["challenge"]}).encode()
        sent.append(body)
        status, retry = answers.pop(0) if answers else (202, None)
        if status is None:
            raise ConnectionResetError("reset after the request left")
        return status, b"", retry

    store = EventStore(tmp_path / "mcp-events.db", sender=sender)
    ops = OpStore(tmp_path / "ops.db")
    ledger = MessageLedger(rig.root, rig.identities)

    def register(origin, method="mcp_event"):
        return rig.call("OUTSIDE_AGENTS", "register", origin, session_proof=PROOF,
                        command_center_id="alpha", source="mcp", source_ref="conn-1",
                        display_name="Muse", delivery_method=method, recipe_id=None)["agent_id"]

    def subscribe(agent, origin=MUSE, **kwargs):
        return store.subscribe("alice", mailbox(agent, **kwargs),
                               authority={**origin, "authenticated_at": 1.0},
                               agents=ledger.mailbox_subscriber)

    def tick(now=None):
        return run_due(ledger, now=time.time() if now is None else now,
                       send=partial(agent_wake, ledger, store, ops))

    def table(name):
        with rig.db() as db:
            return [dict(r) for r in db.execute(f"SELECT * FROM {name} ORDER BY rowid")]

    return SimpleNamespace(rig=rig, sent=sent, answers=answers, store=store, ops=ops,
                           ledger=ledger, register=register, subscribe=subscribe, tick=tick,
                           table=table, receipts=lambda: [r["kind"] for r in table(
                               "ledger_receipts") if r["kind"] not in ("read", "reply")])


@pytest.mark.verifies("trusted-message-ledger:One open wake per ringing recipient")
def test_a_message_opens_one_wake_more_mail_extends_it_and_a_resend_adds_none(wk):
    muse = wk.register(MUSE)
    wk.subscribe(muse)
    first = wk.rig.post(None, f"dm:{muse}", cmid="c1")
    wk.rig.post(None, f"dm:{muse}", body="again", cmid="c2")
    wk.rig.post(None, f"dm:{muse}", cmid="c1")  # a resend is the same message
    [wake] = wk.table("ledger_wakes")
    assert (wake["state"], wake["agent_id"], wake["attempts"]) == ("pending", muse, 0)
    assert wk.tick() == 1 and wk.tick() == 0  # sent once, never twice
    [body] = wk.sent
    event = json.loads(body)
    assert event["eventId"] == "evt_" + hashlib.sha256(
        canonical(["alice", muse, wake["wake_id"]]).encode()).hexdigest()
    assert (event["name"], event["data"]) == (
        MAILBOX, {"command_center_id": "alpha", "agent_id": muse, "cursor": 0})
    assert [w["state"] for w in wk.table("ledger_wakes")] == ["sent"]
    assert {r["state"] for r in wk.table("ledger_recipients")} == {"woken"}
    assert wk.receipts() == ["wake_accepted"]
    [mail, _] = wk.rig.read(MUSE)["messages"]
    assert mail["message_id"] == first["message_id"]
    wk.rig.post(None, f"dm:{muse}", body="later", cmid="c3")  # the next message, the next wake
    assert [w["state"] for w in wk.table("ledger_wakes")] == ["sent", "pending"]


@pytest.mark.verifies("trusted-message-ledger:AGENT_WAKE sends once, through the op journal")
def test_a_429_keeps_the_mail_queued_and_the_next_wake_waits_retry_after(wk):
    muse = wk.register(MUSE)
    wk.subscribe(muse)
    wk.answers.append((429, "120"))
    wk.rig.post(None, f"dm:{muse}", cmid="c1")
    now = time.time()
    assert wk.tick(now) == 0 and len(wk.sent) == 1  # only an accepted wake counts
    [wake] = wk.table("ledger_wakes")
    assert wake["state"] == "rate_limited" and wake["due"] == pytest.approx(now + 120)
    wk.rig.post(None, f"dm:{muse}", body="more", cmid="c2")  # extends; still waits
    assert wk.table("ledger_wakes") == [wake]
    assert {r["state"] for r in wk.table("ledger_recipients")} == {"queued"}
    assert wk.tick(now + 60) == 0 and len(wk.sent) == 1
    assert wk.tick(now + 121) == 1 and len(wk.sent) == 2
    assert [w["state"] for w in wk.table("ledger_wakes")] == ["sent"]
    assert wk.receipts() == ["rate_limited", "wake_accepted"]


@pytest.mark.verifies("trusted-message-ledger:AGENT_WAKE sends once, through the op journal")
def test_an_ambiguous_send_is_unknown_and_never_replayed(wk):
    muse = wk.register(MUSE)
    wk.subscribe(muse)
    wk.answers.append((None, None))  # the connection drops after the request left
    wk.rig.post(None, f"dm:{muse}", cmid="c1")
    now = time.time()
    assert wk.tick(now) == 0 and len(wk.sent) == 1
    [wake] = wk.table("ledger_wakes")
    assert wake["state"] == "unknown" and wk.receipts() == ["wake_sent"]
    record = wk.ops.status("agent_wake:alice", wake["op_id"])
    assert record.sent and record.state == "failed"
    assert wk.tick(now + 3600) == 0 and len(wk.sent) == 1
    assert {r["state"] for r in wk.table("ledger_recipients")} == {"queued"}  # still pullable

    def crash_mid_send(mark):
        """A claim, and perhaps the durable may-have-sent mark, then the broker dies."""
        wk.rig.post(None, f"dm:{muse}", body=secrets.token_hex(4), cmid=secrets.token_hex(4))
        [open_wake] = [w for w in wk.table("ledger_wakes") if w["state"] == "pending"]
        op_id, at = new_op_id(), time.time()
        assert wk.ops.admit("agent_wake:alice", op_id, "digest").kind == "new"
        assert wk.ledger.claim_wake("alice", open_wake["wake_id"], op_id=op_id, now=at)
        if mark:
            wk.ops.mark_may_have_sent("agent_wake:alice", op_id)
        wk.ops.recover()  # the restart

        def state():
            return {w["wake_id"]: w["state"] for w in wk.table("ledger_wakes")}[
                open_wake["wake_id"]]

        wk.tick(at + 30)
        assert state() == "sending"  # inside the lease nothing moves
        wk.tick(at + 61)  # past it, the journal decides
        assert state() == ("unknown" if mark else "pending")
        return open_wake["wake_id"], at

    unsent, at = crash_mid_send(mark=False)  # provably never sent: due again, and sent
    assert wk.tick(at + 62) == 1 and len(wk.sent) == 2
    maybe, at = crash_mid_send(mark=True)  # a byte may have left: unknown, never replayed
    assert wk.tick(at + 3600) == 0 and len(wk.sent) == 2
    states = {w["wake_id"]: w["state"] for w in wk.table("ledger_wakes")}
    assert (states[unsent], states[maybe]) == ("sent", "unknown")


@pytest.mark.verifies("trusted-message-ledger:One open wake per ringing recipient")
def test_no_wake_for_the_sender_and_none_ever_for_a_pull_agent(wk):
    muse, dot = wk.register(MUSE), wk.register(DOT, "pull")
    wk.subscribe(muse)
    first = wk.rig.post(None, f"dm:{muse}", cmid="c1")
    assert wk.tick() == 1
    wk.rig.read(MUSE)
    wk.rig.post(MUSE, f"dm:{muse}", body="reply", cmid="r1", parent=first["message_id"])
    wk.rig.post(None, f"dm:{dot}", cmid="d1")
    assert [(w["agent_id"], w["state"]) for w in wk.table("ledger_wakes")] == [(muse, "sent")]
    assert wk.tick() == 0 and len(wk.sent) == 1
    with pytest.raises(PermissionError, match="mcp_event"):
        wk.subscribe(dot, origin=DOT)  # a pull agent has no doorbell


@pytest.mark.verifies("native-mcp-events:mailbox.message rings only the agent's own client")
def test_subscribe_is_refused_for_anyone_but_the_agents_own_client(wk):
    muse = wk.register(MUSE)
    for agent, origin in ((muse, DOT), ("agent_" + "0" * 16, MUSE)):
        with pytest.raises(LedgerRefused) as refused:
            wk.subscribe(agent, origin=origin)
        assert error_answer(refused.value)["code"] == -32024
    with pytest.raises(PermissionError):
        wk.store.subscribe("alice", mailbox(muse), authority=None,
                           agents=wk.ledger.mailbox_subscriber)
    first = wk.subscribe(muse)["id"]
    second = wk.subscribe(muse, url="https://receiver.example/other")["id"]
    with wk.store.db() as conn:
        active = {r["id"] for r in conn.execute("SELECT id FROM subscriptions WHERE active=1")}
    assert active == {second} and first != second  # one callback per agent: the newest
    for routine in ("routine_fire", "webhook"):
        with pytest.raises(LedgerRefused, match="only pull and mcp_event"):
            wk.register(MUSE, routine)


@pytest.mark.verifies("native-mcp-events:mailbox.message rings only the agent's own client")
@pytest.mark.parametrize("revoke_fails", [False, True])
def test_a_disabled_agent_stops_its_wakes_and_its_callback_is_erased(wk, caplog,
                                                                     revoke_fails):
    from tinyassets import rpc_frames as rf
    from tinyassets.broker.server import OWNER, _Connection

    muse = wk.register(MUSE)
    wk.subscribe(muse)
    wk.rig.post(None, f"dm:{muse}", cmid="c1")
    server = SimpleNamespace(
        _fence=SimpleNamespace(admits=lambda generation, token: True),
        ledger=(wk.ledger, Conversations(wk.rig.root, wk.rig.identities)),
        _policy_store=PolicyStore(wk.rig.root, wk.rig.identities),
        mcp_events=EventDeliveries(wk.store))

    async def disable():
        connection = _Connection(server, None, OWNER)
        await connection._connection_op("OUTSIDE_AGENTS", {
            "op": "OUTSIDE_AGENTS", "action": "disable", "principal": "alice", "origin": None,
            "session_proof": PROOF, "agent_id": muse, "reason": "revoked"})
        reader = asyncio.StreamReader()
        reader.feed_data(connection._queue.get_nowait())
        return (await rf.read_frame(reader)).control()["op"]

    if revoke_fails:  # the disable has committed: it is not reported as refused
        def broken(owner, agent_id):
            raise OSError("event store unavailable")

        wk.store.revoke_agent = broken
        assert asyncio.run(disable()) == "OUTSIDE_AGENTS_IS"
        assert "ALARM: outside agent callbacks not revoked" in caplog.text
        return
    assert asyncio.run(disable()) == "OUTSIDE_AGENTS_IS"
    assert [w["state"] for w in wk.table("ledger_wakes")] == ["suppressed"]
    with wk.store.db() as conn:
        [sub] = conn.execute("SELECT active, config FROM subscriptions").fetchall()
    assert (sub["active"], sub["config"]) == (0, "")
    assert wk.tick() == 0 and wk.sent == []


@pytest.mark.verifies("trusted-message-ledger:AGENT_WAKE sends once, through the op journal")
def test_wakes_ride_the_brokers_event_delivery_pass(wk):
    from tinyassets.broker.server import BrokerServer

    muse = wk.register(MUSE)
    wk.subscribe(muse)
    wk.rig.post(None, f"dm:{muse}", cmid="c1")
    server = SimpleNamespace(ledger=(wk.ledger, None), _ops=wk.ops)
    server.mcp_events = EventDeliveries(wk.store, wakes=partial(
        BrokerServer._outside_wakes, server))

    async def one_kick():  # what a waker tick or a ROOM_POST does
        server.mcp_events.kick()
        await server.mcp_events._task

    asyncio.run(one_kick())
    assert len(wk.sent) == 1 and [w["state"] for w in wk.table("ledger_wakes")] == ["sent"]


@pytest.mark.verifies("trusted-message-ledger:Wakes are doorbells")
def test_no_wake_or_event_payload_carries_a_message_body(wk):
    """S31 ``wake-data``: whatever a wake's outcome, its body never leaves the ledger."""
    for definition, _ in EVENTS.values():
        properties = definition["payloadSchema"]["properties"]
        assert all(p["type"] in ("string", "integer") for p in properties.values())
        assert definition["payloadSchema"]["additionalProperties"] is False
    muse = wk.register(MUSE)
    wk.subscribe(muse)
    bodies, start = [], time.time()
    for step, answer in enumerate(((429, "1"), (None, None), (500, None), (202, None)), 1):
        wk.answers.append(answer)
        bodies.append("BODY-" + secrets.token_hex(8))
        wk.rig.post(None, f"dm:{muse}", body=bodies[-1], cmid=f"c{step}")
        wk.tick(start + 10 * step)
        assert len(wk.sent) == step
    assert len(wk.sent) == 4
    ledger = account_path(wk.rig.root, "alice", "message_ledger.db")
    stored = b"".join(path.read_bytes() for path in (
        *wk.store.path.parent.glob("mcp-events.db*"), *ledger.parent.glob(ledger.name + "*")))
    for body in bodies:
        assert all(body.encode() not in sent for sent in wk.sent)
        assert body.encode() not in stored
    for sent in wk.sent:
        assert set(json.loads(sent)["data"]) == set(
            EVENTS[MAILBOX][0]["payloadSchema"]["properties"])


@pytest.mark.verifies("trusted-message-ledger:AGENT_WAKE sends once, through the op journal")
def test_one_owners_many_agents_never_crowd_out_another_owner(wk):
    """At most one wake per owner per pass, and each pass starts after the last owner."""
    conversations = Conversations(wk.rig.root, wk.rig.identities)
    for owner, count in (("alice", 4), ("bob", 1)):
        for n in range(count):
            agent = wk.ledger.register(
                owner, client=f"{owner[0]}{n}" * 32, family="f" * 64, command_center_id="alpha",
                source="mcp", source_ref="conn-1", display_name="Muse",
                delivery_method="mcp_event", recipe_id=None)["agent_id"]
            wk.ledger.post(owner, origin=None, room_id=f"dm:{agent}", client_message_id=f"c{n}",
                           body="hi", parent_id=None, put_body=partial(conversations.put, owner))
    now = time.time() + 1
    assert sorted(w.owner for w in wk.ledger.due_wakes(now, limit=4)) == ["alice", "bob"]
    first, second = (wk.ledger.due_wakes(now, limit=1) for _ in range(2))
    assert {first[0].owner, second[0].owner} == {"alice", "bob"}  # the start rotates


@pytest.mark.verifies("native-mcp-events:mailbox.message rings only the agent's own client")
def test_nothing_is_claimed_while_the_outside_switch_is_off(wk, monkeypatch):
    muse = wk.register(MUSE)
    wk.subscribe(muse)
    wk.rig.post(None, f"dm:{muse}", cmid="c1")
    monkeypatch.setenv("TINYASSETS_OUTSIDE_DENY", "1")
    assert wk.tick() == 0 and wk.sent == []
    assert [w["state"] for w in wk.table("ledger_wakes")] == ["pending"]  # held, not lost
    monkeypatch.delenv("TINYASSETS_OUTSIDE_DENY")
    assert wk.tick() == 1 and len(wk.sent) == 1


@pytest.mark.verifies("native-mcp-events:mailbox.message rings only the agent's own client")
def test_a_subscribe_racing_a_disable_and_a_suppressing_claim_leave_no_live_secret(wk):
    muse = wk.register(MUSE)
    checks = []

    def disabled_meanwhile(owner, **fields):
        checks.append(fields)
        if len(checks) > 1:
            raise LedgerRefused("not this caller's mcp_event agent")

    with pytest.raises(LedgerRefused):
        wk.store.subscribe("alice", mailbox(muse), authority=MUSE, agents=disabled_meanwhile)
    with wk.store.db() as conn:
        assert [tuple(r) for r in conn.execute("SELECT active, config FROM subscriptions")] == [
            (0, "")]
    wk.subscribe(muse)
    wk.rig.post(None, f"dm:{muse}", cmid="c1")
    wk.ledger.disable("alice", agent_id=muse, reason="revoked")  # no server-side revoke here
    wk.ledger.register("alice", client=MUSE["client"], family=MUSE["family"],
                       command_center_id="alpha", source="mcp", source_ref="conn-1",
                       display_name="Muse", delivery_method="mcp_event", recipe_id=None)
    wk.rig.post(None, f"dm:{muse}", cmid="c2")
    wk.ledger.disable("alice", agent_id=muse, reason="revoked")
    with wk.rig.db() as db:  # a wake left open by a direct store change, as a crash would
        db.execute("UPDATE ledger_wakes SET state='pending' WHERE rowid=(SELECT MAX(rowid) "
                   "FROM ledger_wakes)")
    with wk.store.db() as conn:
        assert conn.execute("SELECT COUNT(*) FROM subscriptions WHERE active=1").fetchone()[0]
    assert wk.tick() == 0 and wk.sent == []  # the claim suppresses, and revokes
    with wk.store.db() as conn:
        assert {tuple(r) for r in conn.execute("SELECT active, config FROM subscriptions")} == {
            (0, "")}


@pytest.mark.verifies("native-mcp-events:mailbox.message rings only the agent's own client")
def test_only_the_subscribing_client_can_unsubscribe_a_mailbox(wk):
    muse = wk.register(MUSE)
    wk.subscribe(muse)
    for stranger in (None, DOT):
        with pytest.raises(PermissionError):
            wk.store.unsubscribe("alice", mailbox(muse), authority=stranger)
    assert wk.store.unsubscribe("alice", mailbox(muse), authority=MUSE) == {}
    with wk.store.db() as conn:
        assert [tuple(r) for r in conn.execute("SELECT active, config FROM subscriptions")] == [
            (0, "")]


@pytest.mark.verifies("trusted-message-ledger:Outside agents register from a verified identity")
def test_an_api_key_agent_cannot_take_the_mcp_event_doorbell(wk):
    from tinyassets.api_keys import _client, _family

    key = "0123456789abcdef"
    with pytest.raises(LedgerRefused, match="API-key agent reads by pull"):
        wk.rig.call("OUTSIDE_AGENTS", "register", {"client": _client(key), "family": _family(key)},
                    session_proof=PROOF, command_center_id="alpha", source="api_key",
                    source_ref=key, display_name="Key", delivery_method="mcp_event",
                    recipe_id=None)


@pytest.mark.verifies("trusted-message-ledger:AGENT_WAKE sends once, through the op journal")
@pytest.mark.parametrize("refusal", ["ssrf", "dns"])
def test_a_callback_refused_before_any_connection_fails_not_unknown(wk, monkeypatch, refusal):
    import socket

    from tinyassets.mcp_event_webhooks import post
    from tinyassets.storage import outbound_connections as outbound

    def resolve(host, port):
        if refusal == "ssrf":
            raise outbound.SsrfValidationError("nonpublic address")
        raise socket.gaierror("no such host")

    monkeypatch.setattr(outbound, "_PinnedHTTPSConnection",
                        lambda *a, **k: pytest.fail("a refused callback reached the network"))
    muse = wk.register(MUSE)
    wk.subscribe(muse)
    wk.store.sender = lambda owner, url, body, headers, **options: post(
        url, body, headers, resolve=resolve, **options)
    wk.rig.post(None, f"dm:{muse}", cmid="c1")
    assert wk.tick() == 0
    [wake] = wk.table("ledger_wakes")
    assert (wake["state"], json.loads(wake["receipt_json"])["reason"]) == ("failed", "not_sent")
    assert wk.receipts() == ["failed"]
