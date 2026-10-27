"""The S30 agent registry: who may register an outside agent, and from what identity."""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import hashlib
import socket
import sys

import pytest

from tests.test_broker_server import _connect, broker  # noqa: F401
from tests.test_message_ledger import COOKIE, MUSE, PROOF, rig  # noqa: F401
from tests.test_owner_policy import _session
from tinyassets import rpc_frames as rf
from tinyassets.api_keys import ApiKeyStore, _client
from tinyassets.broker.message_ledger import LedgerRefused
from tinyassets.broker.policy import PolicyRefused, PolicyStore

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


def test_an_api_key_agent_names_the_owners_own_live_key(rig):
    keys = ApiKeyStore(rig.root)
    theirs = keys.mint("bob", name="bob key", scopes=[], authority=None)["key_id"]
    mine = keys.mint("alice", name="alice key", scopes=[], authority=None)["key_id"]
    fields = {**AGENT, "source": "api_key", "session_proof": PROOF}
    for ref in (theirs, "0" * 16):
        with pytest.raises(LedgerRefused, match="not this owner's live key"):
            rig.call("OUTSIDE_AGENTS", "register", **{**fields, "source_ref": ref})
    with pytest.raises(LedgerRefused, match="not this API key"):
        rig.call("OUTSIDE_AGENTS", "register", MUSE, **{**fields, "source_ref": mine})
    agent = rig.call("OUTSIDE_AGENTS", "register", **{**fields, "source_ref": mine})
    assert agent["outside_client"] == _client(mine)
    assert [a["source_ref"] for a in rig.call("OUTSIDE_AGENTS", "list")["agents"]] == [mine]


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

    def over_the_socket(root, request):
        with _connect(broker) as sock:
            sock.sendall(rf.control(rf.CONNECTION, {**request, **broker.state}))
            return rf.read_frame_blocking(sock).control()

    monkeypatch.setattr(owner_identities, "_owner_request", over_the_socket)
    outside = Identity("alice", "alice", metadata={"outside_origin": {**MUSE, "key_id": "k"}})
    agent = ledger_request(tmp_path, outside, "OUTSIDE_AGENTS", "register",
                           session_proof=PROOF, **AGENT)
    owner = Identity("alice", "alice", metadata={})
    sent = ledger_request(tmp_path, owner, "ROOM_POST", "post", room_id=f"dm:{agent['agent_id']}",
                          client_message_id="c1", body="hi", parent_id=None, principal="bob")
    assert sent["recipients"] == [agent["agent_id"]]
    [mail] = ledger_request(tmp_path, outside, "MAILBOX_RECEIPT", "read", limit=5)["messages"]
    assert (mail["body"], mail["sender_kind"]) == ("hi", "owner")
    broker.state["token"] = "stale"
    with pytest.raises(BrokerRefused, match="fenced"):
        ledger_request(tmp_path, owner, "OUTSIDE_AGENTS", "list")
