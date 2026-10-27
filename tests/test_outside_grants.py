"""P0-3: outside-client grants are owner policy in the broker, admitted at the router.

Decision A1: an outside agent has the owner's normal authority by default (``full``),
or exact scopes (``scoped``), or none (``revoked``). Only the owner's protected session
changes a grant, on the router's owner channel; the router admits every outside call
against it and stamps it on the identity; the box refuses an unstamped one.
"""
# ruff: noqa: F811 -- imported pytest fixtures
from __future__ import annotations

import asyncio
import json

import pytest
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route
from starlette.testclient import TestClient

from tests.support.outside_grants import LocalBroker, RouterAdmission
from tests.test_n10_rules_at_open import COOKIE, _check, _rule, rig  # noqa: F401
from tinyassets.auth.provider import Identity
from tinyassets.broker.policy import PolicyRefused
from tinyassets.outside_authority import OutsideRefused, check_identity, check_mcp_request

pytestmark = pytest.mark.verifies(
    "owner-daemon-in-box:Outside-client grants are owner policy in the broker")
CHATGPT = {"client": "1" * 64, "family": "2" * 64, "authenticated_at": 100}
CLAUDE = {"client": "3" * 64, "family": "4" * 64, "authenticated_at": 100}
HOME = {"universe": "home", "agent": "main", "capability": "read_graph"}


@pytest.fixture
def grants(tmp_path, monkeypatch):
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TINYASSETS_OUTSIDE_DENY", raising=False)
    return LocalBroker(tmp_path, monkeypatch)


def outside(origin, owner="alice"):
    return Identity(owner, owner, metadata={"outside_origin": dict(origin)})


def admit(identity):
    from tinyassets.outside_authority import admit as router_admit
    from tinyassets.storage import data_dir

    router_admit(data_dir(), identity)
    return identity.metadata["outside_origin"]


def test_two_connectors_are_two_clients_each_full_by_default(grants):
    for origin in (CHATGPT, CLAUDE):
        stamped = admit(outside(origin))
        assert (stamped["mode"], stamped["scopes"], stamped["generation"]) == ("full", [], 1)
    rows = grants.store.outside_list("alice")
    assert [(r["client"], r["source"], r["mode"]) for r in rows] == [
        (CHATGPT["client"], "mcp", "full"), (CLAUDE["client"], "mcp", "full")]
    assert grants.store.outside_list("bob") == []  # per owner, never shared
    # Full is the owner's normal authority: any capability passes the grant check.
    full = outside({**CHATGPT, **rows[0]})
    check_identity(full, universe="elsewhere", agent="main", capability="run_graph")
    check_mcp_request(full, {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "read_graph", "arguments": {"run_id": "r1"}}})  # no universe to resolve
    with pytest.raises(OutsideRefused, match="protected owner surface"):
        check_mcp_request(full, {"jsonrpc": "2.0", "id": 1, "method": "tools/call",
                                 "params": {"name": "write_graph",
                                            "arguments": {"webhook_op": "mint"}}})


def test_a_scoped_client_keeps_exactly_its_triples(grants):
    admit(outside(CHATGPT))
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="scoped", scopes=[HOME])
    identity = outside(CHATGPT)
    admit(identity)  # takes effect on the agent's next call (A1)
    check_identity(identity, universe="home", agent="main", capability="read_graph")
    for refused in ({**HOME, "capability": "run_graph"}, {**HOME, "universe": "work"},
                    {**HOME, "agent": "helper"}):
        with pytest.raises(OutsideRefused, match="not granted"):
            check_identity(identity, **refused)
    call = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
        "name": "read_graph", "arguments": {"universe_id": "home"}}}
    check_mcp_request(identity, call)
    assert identity.metadata["outside_resource"] == HOME
    call["params"]["name"] = "write_graph"
    with pytest.raises(OutsideRefused, match="not granted"):
        check_mcp_request(identity, call)


def test_check_identity_admits_only_a_router_stamp(grants):
    check_identity(Identity("alice", "alice"))  # the owner never consults a grant
    for bound in (CHATGPT, {**CHATGPT, "mode": "revoked", "scopes": [], "generation": 2},
                  {**CHATGPT, "mode": "full", "scopes": [], "generation": "1"}):
        with pytest.raises(OutsideRefused, match="authority unavailable"):
            check_identity(outside(bound))
    check_identity(outside(admit(outside(CHATGPT))))


def test_a_revoke_refuses_the_next_call_and_needs_a_fresh_sign_in(grants):
    admit(outside(CHATGPT))
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="revoked")
    with pytest.raises(OutsideRefused, match="revoked"):
        admit(outside(CHATGPT))
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="full")
    with pytest.raises(OutsideRefused, match="fresh sign-in"):
        admit(outside(CHATGPT))  # a credential from before the revoke stays dead
    fresh = {**CHATGPT, "authenticated_at": 2 ** 40}
    assert admit(outside(fresh))["generation"] == 3


def test_work_that_outlives_its_call_rereads_the_grant(grants):
    from tinyassets.outside_authority import refresh

    stamped = admit(outside(CHATGPT))
    assert refresh("alice", stamped) == stamped
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="scoped", scopes=[HOME])
    with pytest.raises(OutsideRefused, match="authority changed"):
        refresh("alice", stamped)
    assert ("box", "outside_check") in grants.calls  # the box reads; it never admits


def test_an_api_key_without_a_broker_row_is_refused_never_widened(grants, monkeypatch):
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.rest_api import rest_api_routes

    monkeypatch.setattr("tinyassets.api_keys.current_store",
                        lambda base=None: ApiKeyStore(grants.root))
    monkeypatch.setattr("tinyassets.universe_server.read_graph",
                        lambda **arguments: json.dumps({"ran": True}))
    from tinyassets.auth.middleware import AuthContextMiddleware

    http = TestClient(AuthContextMiddleware(RouterAdmission(
        Starlette(routes=rest_api_routes()))))
    key = ApiKeyStore(grants.root).mint("alice", name="pre-cutover key")  # no grant row
    response = http.get("/api/command-centers/home/status",
                        headers={"authorization": "Bearer " + key["key"]})
    assert response.status_code == 403 and response.json()["reason"] == (
        "outside client has no grant")
    assert grants.store.outside_list("alice") == []  # admission recorded nothing


def test_the_router_refuses_before_any_forward_or_trusted_answer(grants, monkeypatch):
    from tinyassets import owner_router, routing_snapshot
    from tinyassets.auth import middleware

    class Snapshot:
        def require_current(self):
            pass

    reached = []

    async def match(scope, receive):
        reached.append(scope["path"])
        return None, receive

    async def owner(self, scope, receive, send, identity, *, forward_only):
        reached.append("forward")
        await PlainTextResponse("box")(scope, receive, send)

    monkeypatch.setattr(routing_snapshot, "snapshot", lambda root: Snapshot())
    monkeypatch.setattr(owner_router.TRUSTED, "match", match)
    monkeypatch.setattr(owner_router.OwnerRouter, "_owner", owner)
    router = owner_router.OwnerRouter(None)

    def send_as(identity, path):
        async def app(scope, receive, send):
            token = middleware._current_identity.set(identity)
            try:
                await router(scope, receive, send)
            finally:
                middleware._current_identity.reset(token)
        return TestClient(app).post(path, content=b"{}")

    assert send_as(outside(CHATGPT), "/mcp").text == "box"
    assert reached == ["/mcp", "forward"]
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="revoked")
    reached.clear()
    for path in ("/mcp", "/api/mailbox", "/app/api/outside-agents"):
        refused = send_as(outside(CHATGPT), path)
        assert refused.status_code == 403
        assert refused.json()["detail"] == owner_router.OUTSIDE_REFUSED
    assert reached == []  # no registry lookup, no forward, no ledger call
    monkeypatch.setenv("TINYASSETS_OUTSIDE_DENY", "1")
    assert send_as(outside(CLAUDE), "/mcp").status_code == 403
    assert send_as(Identity("alice", "alice"), "/mcp").text == "box"  # the owner


def test_the_box_refuses_an_outside_identity_the_router_did_not_stamp(grants):
    from tinyassets.auth.middleware import AuthContextMiddleware, current_identity_or_none

    ran = []

    def handler(request):
        ran.append(current_identity_or_none().metadata["outside_origin"])
        return PlainTextResponse("handled")

    box = AuthContextMiddleware(Starlette(routes=[Route("/api/x", handler)]))

    async def forwarded(identity):
        async def app(scope, receive, send):
            await box({**scope, "tinyassets_router_identity": identity}, receive, send)
        return await asyncio.to_thread(lambda: TestClient(app).get("/api/x"))

    unstamped = asyncio.run(forwarded(outside(CHATGPT)))
    assert unstamped.status_code == 403 and ran == []
    stamped = outside(CHATGPT)
    admit(stamped)
    assert asyncio.run(forwarded(stamped)).text == "handled"
    assert ran[0]["mode"] == "full"


def test_the_box_channel_cannot_change_or_admit_a_grant(grants):
    from tinyassets.broker.client import BrokerClient

    admit(outside(CHATGPT))
    proof = grants.proofs["alice"]
    change = {"op": "OWNER_POLICY", "principal": "alice", "client": CHATGPT["client"],
              "source": "mcp", "mode": "revoked", "scopes": [], "expected_generation": 1,
              "name": ""}
    box = grants.peers["box"]
    for doc in ({**change, "action": "outside_set", "session_proof": proof},
                {"op": "OWNER_POLICY", "action": "outside_admit", "principal": "alice",
                 "client": CLAUDE["client"], "source": "mcp", "authenticated_at": 100},
                {"op": "OWNER_POLICY", "action": "held", "principal": "alice",
                 "request_id": "api_key:x"}):
        answer = box._owner_policy(doc)
        assert answer == {"op": "OWNER_POLICY_REFUSED",
                          "reason": "owner policy widens only through the router"}
    client = BrokerClient(grants.root / "absent.sock", principal="alice",
                          command_center="alice", fence=lambda: (0, ""))
    for action in ("outside_set", "outside_admit", "held"):
        with pytest.raises(ValueError, match="unsupported owner policy operation"):
            client.owner_policy(action, session_proof=proof)
    # The router's channel still needs the owner's live protected session.
    for bad in ({"session_hash": "f" * 64}, grants.proofs["bob"], "agent-token-" + "x" * 32):
        answer = grants.peers["owner"]._owner_policy(
            {**change, "action": "outside_set", "session_proof": bad})
        assert answer == {"op": "OWNER_POLICY_REFUSED",
                          "reason": "interactive owner session required"}
    [row] = grants.store.outside_list("alice")
    assert (row["mode"], row["generation"]) == ("full", 1)
    with pytest.raises(PolicyRefused, match="outside client source changed"):
        grants.store.outside_admit("alice", CHATGPT["client"], "api_key", None)


def test_the_roster_is_the_broker_grants_one_row_per_client(grants, monkeypatch):
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.connected_agents import roster

    monkeypatch.setattr("tinyassets.api_keys.current_store",
                        lambda base=None: ApiKeyStore(grants.root))
    for origin in (CHATGPT, CLAUDE):
        admit(outside(origin))
    grants.grant("alice", CLAUDE["client"], source="mcp", mode="scoped",
                 scopes=[{**HOME, "universe": "work"}])
    assert [row["connection_id"] for row in roster(
        grants.root, universe_id="home", owner="alice")] == ["mcp:" + CHATGPT["client"]]
    assert len(roster(grants.root, universe_id="work", owner="alice")) == 2
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="revoked")
    assert roster(grants.root, universe_id="home", owner="alice") == []


def test_an_open_for_an_outside_client_binds_its_grant(rig):
    client = "5" * 64
    rig.store.outside_admit("alice", client, "mcp", 100)  # full by default (A1)
    full = {"agent": "main", "outside": client + ":1:100"}
    assert _check(rig, authority=full) is None
    rig.store.outside_set("alice", COOKIE, client=client, source="mcp", mode="scoped",
                          scopes=[HOME], expected_generation=1)
    assert _check(rig, authority=full) == "refused"  # the grant changed since the call
    scoped = {"agent": "main", "outside": client + ":2:100"}
    assert _check(rig, authority=scoped) is None
    _rule(rig, "app.write", "hand_off", agent="outside:" + client)
    assert _check(rig, authority=scoped) == "policy_hand_off"  # its own rules join
    assert _check(rig, authority={"agent": "main"}) is None  # the owner's own call
    rig.store.outside_set("alice", COOKIE, client=client, source="mcp", mode="revoked",
                          scopes=[], expected_generation=2)
    assert _check(rig, authority={"agent": "main", "outside": client + ":3:100"}) == "refused"


def test_a_grant_change_ends_that_clients_open_streams(grants):
    from types import SimpleNamespace

    admit(outside(CHATGPT))
    mine = SimpleNamespace(namespace="alice", outside=CHATGPT["client"])
    others = [SimpleNamespace(namespace="alice", outside=CLAUDE["client"]),
              SimpleNamespace(namespace="alice", outside=""),
              SimpleNamespace(namespace="bob", outside=CHATGPT["client"])]
    grants.streams.update(enumerate([mine, *others]))
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="revoked")
    assert grants.cancelled == [mine]
