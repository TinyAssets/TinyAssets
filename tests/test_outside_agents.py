"""The S30 agent registry: who may register an outside agent, and from what identity."""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import hashlib
import socket
import sqlite3
import sys

import pytest

from tests.test_broker_server import _connect, broker  # noqa: F401
from tests.test_message_ledger import COOKIE, MUSE, PROOF, rig  # noqa: F401
from tests.test_owner_policy import _session
from tinyassets import rpc_frames as rf
from tinyassets.api_keys import ApiKeyRefused, ApiKeyStore, _client, _family
from tinyassets.broker.message_ledger import LedgerRefused
from tinyassets.broker.policy import PolicyRefused, PolicyStore, account_path

pytestmark = pytest.mark.verifies(
    "trusted-message-ledger:Outside agents register from a verified identity")
AGENT = dict(command_center_id="alpha", source="mcp", source_ref="conn-1",
             display_name="Muse", delivery_method="pull", recipe_id=None)


def test_register_and_disable_need_the_owners_protected_session(rig):
    other = "interactive-cookie-" + "o" * 32
    _session(rig.root, "bob", other)
    for proof in (None, {"session_hash": "f" * 64},
                  {"session_hash": hashlib.sha256(other.encode()).hexdigest()}, COOKIE):
        fields = AGENT if proof is None else {**AGENT, "session_proof": proof}
        with pytest.raises((LedgerRefused, PolicyRefused), match="interactive owner session"):
            rig.call("OUTSIDE_AGENTS", "register", MUSE, **fields)
    assert rig.call("OUTSIDE_AGENTS", "list") == {"agents": []}
    agent = rig.call("OUTSIDE_AGENTS", "register", MUSE, session_proof=PROOF, **AGENT)
    with pytest.raises(LedgerRefused, match="interactive owner session"):
        rig.call("OUTSIDE_AGENTS", "disable", agent_id=agent["agent_id"], reason="x")
    [listed] = rig.call("OUTSIDE_AGENTS", "list")["agents"]
    assert listed["disabled_reason"] is None
    assert (listed["outside_client"], listed["outside_family"]) == (MUSE["client"],
                                                                   MUSE["family"])
    with sqlite3.connect(account_path(rig.root, "alice", "policy.db")) as db:
        refused = db.execute("SELECT COUNT(*) FROM receipts WHERE kind='refusal'").fetchone()
    assert refused == (5,)  # four refused registers and one refused disable


def test_the_router_checks_a_key_is_the_owners_and_fails_loudly(tmp_path):
    keys = ApiKeyStore(tmp_path)
    theirs = keys.mint("bob", name="bob key", scopes=[], authority=None)["key_id"]
    mine = keys.mint("alice", name="alice key", scopes=[], authority=None)["key_id"]
    assert keys.agent_origin("alice", mine) == {"client": _client(mine), "family": _family(mine)}
    for ref in (theirs, "0" * 16, None):
        with pytest.raises(ApiKeyRefused, match="not this owner's live key"):
            keys.agent_origin("alice", ref)
    keys.revoke("alice", mine, authority=None)
    with pytest.raises(ApiKeyRefused):
        keys.agent_origin("alice", mine)
    keys.path.write_bytes(b"not a database")  # a store failure is an error, never a refusal
    with pytest.raises(sqlite3.DatabaseError):
        keys.agent_origin("alice", mine)


def test_the_broker_takes_a_keys_origin_from_the_router_and_never_reads_the_store(rig):
    key = "0123456789abcdef"
    origin = {"client": _client(key), "family": _family(key)}
    fields = {**AGENT, "source": "api_key", "source_ref": key, "session_proof": PROOF}
    for wrong in (None, MUSE):
        with pytest.raises(LedgerRefused, match="verified outside identity|this API key's"):
            rig.call("OUTSIDE_AGENTS", "register", wrong, **fields)
    assert rig.call("OUTSIDE_AGENTS", "register", origin, **fields)["source_ref"] == key
    assert not (rig.root / ".api-keys.sqlite3").exists()


def test_only_pull_delivery_is_admitted(rig):
    for method, error in (("webhook", LedgerRefused), ("mcp_event", LedgerRefused),
                          ("carrier", ValueError)):
        with pytest.raises(error):
            rig.call("OUTSIDE_AGENTS", "register", MUSE, session_proof=PROOF,
                     **{**AGENT, "delivery_method": method})
    assert rig.call("OUTSIDE_AGENTS", "list") == {"agents": []}


def test_an_mcp_agent_is_refused_without_a_verified_outside_origin(rig):
    # origin() returns None until the outside-client claim is configured (P0-3).
    with pytest.raises(LedgerRefused, match="verified outside identity"):
        rig.call("OUTSIDE_AGENTS", "register", None, session_proof=PROOF, **AGENT)
    assert rig.call("OUTSIDE_AGENTS", "list") == {"agents": []}


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(socket, "SO_PEERCRED"),
                    reason="broker owner channel uses Unix peer credentials")
def test_the_router_helper_crosses_the_fenced_owner_channel(broker, tmp_path, monkeypatch):
    from tinyassets.auth.provider import Identity
    from tinyassets.broker import owner_identities
    from tinyassets.broker.client import BrokerRefused, ledger_request

    _session(tmp_path, "alice", COOKIE)
    broker.server._policy_store = PolicyStore(tmp_path, broker.server._owner_identities)
    broker.server._policy_store.bootstrap_owner("alice")
    sent = []

    def over_the_socket(root, request):
        sent.append(request)
        with _connect(broker) as sock:
            sock.sendall(rf.control(rf.CONNECTION, {**request, **broker.state}))
            return rf.read_frame_blocking(sock).control()

    monkeypatch.setattr(owner_identities, "_owner_request", over_the_socket)
    outside = Identity("alice", "alice", metadata={"outside_origin": {**MUSE, "key_id": "k"}})
    agent = ledger_request(tmp_path, outside, "OUTSIDE_AGENTS", "register",
                           session_proof=PROOF, **AGENT)
    owner = Identity("alice", "alice", metadata={})
    posted = ledger_request(tmp_path, owner, "ROOM_POST", "post",
                            room_id=f"dm:{agent['agent_id']}", client_message_id="c1",
                            body="hi", parent_id=None, principal="bob")
    assert posted["recipients"] == [agent["agent_id"]]
    # The owner registers its own key: the router checks it and sends the key's origin.
    keys = ApiKeyStore(tmp_path)
    mine = keys.mint("alice", name="alice key", scopes=[], authority=None)["key_id"]
    theirs = keys.mint("bob", name="bob key", scopes=[], authority=None)["key_id"]
    keyed = {**AGENT, "source": "api_key", "session_proof": PROOF}
    assert ledger_request(tmp_path, owner, "OUTSIDE_AGENTS", "register",
                          **{**keyed, "source_ref": mine})["outside_client"] == _client(mine)
    calls = len(sent)
    for caller, ref in ((owner, theirs), (outside, mine)):
        with pytest.raises((ApiKeyRefused, BrokerRefused)):
            ledger_request(tmp_path, caller, "OUTSIDE_AGENTS", "register",
                           **{**keyed, "source_ref": ref})
    assert len(sent) == calls  # refused in the router, before any broker call
    [mail] = ledger_request(tmp_path, outside, "MAILBOX_RECEIPT", "read", limit=5)["messages"]
    assert (mail["body"], mail["sender_kind"]) == ("hi", "owner")
    broker.state["token"] = "stale"
    with pytest.raises(BrokerRefused, match="fenced"):
        ledger_request(tmp_path, owner, "OUTSIDE_AGENTS", "list")
