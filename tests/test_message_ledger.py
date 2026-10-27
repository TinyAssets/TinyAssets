"""The S30 message ledger core: attribution, idempotency, rooms, reads and receipts."""
from __future__ import annotations

import hashlib
import json
import sqlite3
from types import SimpleNamespace

import pytest

from tests.test_owner_policy import _session, admissions
from tinyassets.broker.conversations import Conversations
from tinyassets.broker.message_ledger import LedgerRefused, MessageLedger, router_operation
from tinyassets.broker.policy import PolicyStore, account_path

COOKIE = "interactive-cookie-" + "q" * 32
PROOF = {"session_hash": hashlib.sha256(COOKIE.encode()).hexdigest()}
MUSE = {"client": "a" * 64, "family": "b" * 64}
DOT = {"client": "c" * 64, "family": "d" * 64}


@pytest.fixture
def rig(tmp_path):
    identities = admissions(tmp_path)
    _session(tmp_path, "alice", COOKIE)
    PolicyStore(tmp_path, identities).bootstrap()  # as admission does, before any ledger op
    ledger = MessageLedger(tmp_path, identities)

    def call(op, action, origin=None, **fields):
        return router_operation(ledger, Conversations(tmp_path, identities),
                                PolicyStore(tmp_path, identities),
                                {"op": op, "action": action, "principal": "alice",
                                 "origin": origin, **fields})

    def register(origin, name="Muse"):
        return call("OUTSIDE_AGENTS", "register", origin, session_proof=PROOF,
                    command_center_id="alpha", source="mcp", source_ref="conn-1",
                    display_name=name, delivery_method="pull", recipe_id=None)["agent_id"]

    def post(origin, room, body="hello", cmid="c1", parent=None):
        return call("ROOM_POST", "post", origin, room_id=room, client_message_id=cmid,
                    body=body, parent_id=parent)

    def read(origin=None, limit=50):
        return call("MAILBOX_RECEIPT", "read", origin, limit=limit)

    def db(name="message_ledger.db"):
        conn = sqlite3.connect(account_path(tmp_path, "alice", name))
        conn.row_factory = sqlite3.Row
        return conn

    return SimpleNamespace(root=tmp_path, identities=identities, call=call, register=register,
                           post=post, read=read, db=db)


@pytest.mark.verifies("trusted-message-ledger:The sender is the verified identity")
def test_attribution_reply_receipt_chain_depth_and_states(rig):
    muse = rig.register(MUSE)
    first = rig.post(None, f"dm:{muse}", body='{"sender": "owner"}')
    assert first == {"message_id": first["message_id"], "recipients": [muse], "held": False,
                     "paused_reason": ""}
    with pytest.raises(ValueError, match="invalid ledger request"):  # never a body field
        rig.call("ROOM_POST", "post", MUSE, room_id=f"dm:{muse}", client_message_id="x",
                 body="x", parent_id=None, sender="owner")
    [mail] = rig.read(MUSE)["messages"]
    assert (mail["sender_kind"], mail["sender_id"], mail["chain_depth"]) == ("owner", "owner", 0)
    assert mail["body"] == '{"sender": "owner"}' and mail["integrity"] == "ok"
    reply = rig.post(MUSE, f"dm:{muse}", body="hi", cmid="r1", parent=first["message_id"])
    assert reply["recipients"] == ["owner"]
    [back] = rig.read()["messages"]
    assert (back["sender_kind"], back["sender_id"], back["chain_depth"]) == ("outside", muse, 1)
    again = rig.post(None, f"dm:{muse}", cmid="c2", parent=reply["message_id"])
    with rig.db() as db:
        states = dict(db.execute("SELECT message_id, state FROM ledger_recipients"))
        depths = dict(db.execute("SELECT message_id, chain_depth FROM ledger_messages"))
        roots = {r[0] for r in db.execute("SELECT chain_root FROM ledger_messages")}
        receipts = [(r["agent_id"], r["kind"], r["ref"], json.loads(r["detail_json"]))
                    for r in db.execute("SELECT * FROM ledger_receipts ORDER BY seq")]
    assert states == {first["message_id"]: "replied", reply["message_id"]: "replied",
                      again["message_id"]: "queued"}
    assert depths[again["message_id"]] == 0 and roots == {first["message_id"]}
    kinds = [(agent, kind) for agent, kind, _, _ in receipts]
    assert kinds == [(muse, "read"), (muse, "reply"), ("owner", "read"), ("owner", "reply")]
    detail = receipts[1][3]
    assert detail["parent_id"] == first["message_id"] and receipts[1][2] == reply["message_id"]
    assert 0 <= detail["read_s"] <= detail["reply_s"]


@pytest.mark.verifies("trusted-message-ledger:The sender is the verified identity")
def test_resend_returns_the_original_and_another_hash_conflicts(rig):
    muse = rig.register(MUSE)
    sent = rig.post(None, f"dm:{muse}", body="one")
    assert rig.post(None, f"dm:{muse}", body="one") == sent
    for change in ({"body": "two"}, {"room": f"dm:{rig.register(DOT, 'Dot')}"}):
        with pytest.raises(LedgerRefused, match="conflict"):
            rig.post(None, **{"room": f"dm:{muse}", "body": "one", **change})
    with rig.db() as db:
        assert db.execute("SELECT COUNT(*) FROM ledger_messages").fetchone()[0] == 1
    assert [m["body"] for m in rig.read(MUSE)["messages"]] == ["one"]
    # The same client id from another sender is that sender's own message.
    assert rig.post(MUSE, f"dm:{muse}", body="two")["message_id"] != sent["message_id"]


@pytest.mark.verifies("trusted-message-ledger:A reader sees only its own mail")
def test_a_tampered_body_is_withheld_and_flagged(rig):
    muse = rig.register(MUSE)
    rig.post(None, f"dm:{muse}", body="pay the invoice")
    with rig.db("conversations.db") as db:
        db.execute("UPDATE message_bodies SET body='pay the attacker'")
    [mail] = rig.read(MUSE)["messages"]
    assert (mail["body"], mail["integrity"]) == (None, "tampered")


@pytest.mark.verifies("trusted-message-ledger:A reader sees only its own mail")
def test_reader_isolation_cursor_ack_and_a_duplicate_session(rig):
    muse, dot = rig.register(MUSE), rig.register(DOT, "Dot")
    rig.post(None, f"dm:{muse}", body="for muse")
    rig.post(None, f"dm:{dot}", body="for dot", cmid="c2")
    first = rig.read(MUSE)
    assert [m["body"] for m in first["messages"]] == ["for muse"]
    assert [m["body"] for m in rig.read(DOT)["messages"]] == ["for dot"]
    assert rig.read()["messages"] == []  # the owner was sent nothing
    # Before an ack a reread returns the same message_ids, the dedupe key.
    assert [m["message_id"] for m in rig.read(MUSE)["messages"]] == [
        m["message_id"] for m in first["messages"]]
    through = first["messages"][-1]["seq"]
    assert rig.call("MAILBOX_RECEIPT", "ack", MUSE, through=through)["through"] == through
    assert rig.read(MUSE)["messages"] == []  # W3-R5: the retried session finds nothing new
    assert rig.call("MAILBOX_RECEIPT", "ack", MUSE, through=0)["through"] == through
    with pytest.raises(LedgerRefused, match="past the ledger"):
        rig.call("MAILBOX_RECEIPT", "ack", MUSE, through=through + 10)
    assert len(rig.read(DOT)["messages"]) == 1  # Muse's cursor is Muse's alone


@pytest.mark.verifies("trusted-message-ledger:The sender is the verified identity")
def test_dm_room_and_parent_rules(rig):
    muse, dot = rig.register(MUSE), rig.register(DOT, "Dot")
    own = rig.post(MUSE, f"dm:{muse}", cmid="m1")
    to_dot = rig.post(None, f"dm:{dot}", cmid="d1")
    for origin, room, parent, reason in (
            (MUSE, f"dm:{dot}", None, "its own dm room"),
            (None, "general", None, "enabled agent's dm"),
            (None, "dm:agent_0000000000000000", None, "enabled agent's dm"),
            (None, f"dm:{muse}", to_dot["message_id"], "parent is not"),
            (MUSE, f"dm:{muse}", own["message_id"], "parent is not"),
            (MUSE, f"dm:{muse}", "msg_missing", "parent is not")):
        with pytest.raises(LedgerRefused, match=reason):
            rig.post(origin, room, cmid="z", parent=parent)


@pytest.mark.verifies("trusted-message-ledger:The ledger lives in the broker")
def test_account_erasure_removes_the_ledger_and_bodies(rig):
    from tinyassets.broker.account_erasure import ACCOUNT_SCOPE, local_erase
    from tinyassets.storage.outbound_connections import ConnectionLedger

    rig.post(None, f"dm:{rig.register(MUSE)}")
    local_erase(ConnectionLedger(rig.root / ".broker" / "outbound.db", data_root=rig.root),
                principal="alice", command_center=ACCOUNT_SCOPE)
    assert not [name for name in ("message_ledger.db", "conversations.db")
                if account_path(rig.root, "alice", name).exists()]


@pytest.mark.verifies("trusted-message-ledger:The sender is the verified identity")
def test_a_disabled_agent_cannot_be_written_to_post_or_read(rig):
    muse = rig.register(MUSE)
    rig.post(None, f"dm:{muse}")
    disabled = rig.call("OUTSIDE_AGENTS", "disable", session_proof=PROOF, agent_id=muse,
                        reason="owner removed it")
    assert (disabled["disabled_reason"], disabled["revision"]) == ("owner removed it", 2)
    for attempt in (lambda: rig.post(None, f"dm:{muse}", cmid="c2"),
                    lambda: rig.post(MUSE, f"dm:{muse}", cmid="m1"),
                    lambda: rig.read(MUSE)):
        with pytest.raises(LedgerRefused):
            attempt()
    assert rig.register(MUSE) == muse  # the same identity re-registers as the same agent
    assert len(rig.read(MUSE)["messages"]) == 1


@pytest.mark.verifies("trusted-message-ledger:The ledger lives in the broker")
def test_a_ledger_op_before_policy_bootstrap_leaves_bootstrap_working(tmp_path):
    identities = admissions(tmp_path)  # admitted, as admission marks before bootstrap_owner
    ledger, bodies = MessageLedger(tmp_path, identities), Conversations(tmp_path, identities)
    doc = {"op": "OUTSIDE_AGENTS", "action": "list", "principal": "alice", "origin": None}
    with pytest.raises(PermissionError, match="not bootstrapped"):
        router_operation(ledger, bodies, None, doc)
    assert not account_path(tmp_path, "alice", "policy.db").parent.exists()
    policy = PolicyStore(tmp_path, identities)
    policy.bootstrap_owner("alice")
    assert policy.read("alice").generation == 1
    assert router_operation(ledger, bodies, None, doc) == {"agents": []}


@pytest.mark.verifies("trusted-message-ledger:The ledger lives in the broker")
def test_two_racing_first_opens_both_succeed(rig, monkeypatch):
    import os

    from tinyassets.broker import policy

    path = account_path(rig.root, "alice", "conversations.db")
    real = os.open

    def raced(target, flags, mode=0o777):
        if flags & os.O_EXCL:  # the other opener created it between exists() and open
            os.close(real(target, os.O_CREAT | os.O_WRONLY, 0o600))
            raise FileExistsError(target)
        return real(target, flags, mode)

    monkeypatch.setattr(policy.os, "open", raced)
    policy.open_account_db(path, rig.identities, "alice").close()
    assert path.stat().st_mode & 0o777 == 0o600
