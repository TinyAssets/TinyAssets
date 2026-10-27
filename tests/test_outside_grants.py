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
from tests.test_broker_server import broker  # noqa: F401
from tests.test_n10_rules_at_open import (  # noqa: F401
    COOKIE,
    PEERCRED,
    _box_channel,
    _box_open,
    _box_rig,
    _check,
    _rule,
    rig,
)
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


# -- Revocation reaches durable work (security review of #5343) -------------------


def _keyed(grants, monkeypatch, *scopes):
    """A key and its grant, as the router mints them, and its identity as stamped."""
    from tinyassets.api_keys import ApiKeyStore, _client, _family

    keys = ApiKeyStore(grants.root)
    monkeypatch.setattr("tinyassets.api_keys.current_store", lambda base=None: keys)
    key = keys.mint("alice", name="durable")
    grants.grant("alice", _client(key["key_id"]), mode="scoped" if scopes else "full",
                 scopes=scopes)
    identity = Identity("alice", "alice", metadata={
        "auth_provider": "api_key",
        "outside_origin": {"client": _client(key["key_id"]), "family": _family(key["key_id"])}})
    admit(identity)
    return key, identity


def test_a_key_revoke_stops_its_stored_run_automation_and_subscription(grants, monkeypatch):
    from functools import partial

    from tinyassets.broker.server import _outside_admits
    from tinyassets.outside_authority import run_identity, stored_identity
    from tinyassets.router_credentials import _dispatch
    from tinyassets.runs import _connect, create_run

    key, identity = _keyed(grants, monkeypatch)
    bound = identity.metadata["outside_origin"]
    saved = json.dumps({"owner": "alice", "bound": bound, "capabilities": []})
    run_id = create_run(grants.root, branch_def_id="b", thread_id="", inputs={},
                        actor="alice", owner_user_id="alice", queue_universe_id="home")
    with _connect(grants.root) as conn:
        conn.execute("UPDATE runs SET outside_origin_json=? WHERE run_id=?", (saved, run_id))
    admits = partial(_outside_admits, grants.store, "alice")
    with run_identity(grants.root, run_id), stored_identity(saved, "alice"):
        pass  # both admitted while the key is live
    assert admits(bound)
    _dispatch(grants.root, "alice", {"operation": "key_revoke", "key_id": key["key_id"]})
    with pytest.raises(OutsideRefused):
        with run_identity(grants.root, run_id):
            pass
    with pytest.raises(OutsideRefused):  # an automation's saved identity
        with stored_identity(saved, "alice"):
            pass
    assert not admits(bound)  # the broker revokes its event subscription at capture
    assert grants.store.outside_list("alice")[0]["mode"] == "revoked"
    assert ("router", "outside_revoke") in grants.calls


def test_a_key_revoke_fails_loudly_when_the_broker_cannot_answer(grants, monkeypatch):
    from tinyassets.api_keys import ApiKeyStore
    from tinyassets.broker import owner_identities
    from tinyassets.router_credentials import _dispatch

    key, _ = _keyed(grants, monkeypatch)

    def down(root, doc):
        raise RuntimeError("owner identity broker is unavailable")

    monkeypatch.setattr(owner_identities, "_owner_request", down)
    with pytest.raises(RuntimeError):
        _dispatch(grants.root, "alice", {"operation": "key_revoke", "key_id": key["key_id"]})
    # Not half done: the key stays live until its grant is revoked, and is shown so.
    assert ApiKeyStore(grants.root).list("alice")["keys"][0]["revoked_at"] is None


def test_a_withdrawn_owner_refuses_every_outside_grant(grants):
    from tests.support.admission import drive, projection
    from tinyassets.broker.admission import local_mutate
    from tinyassets.broker.server import _outside_admits

    stamped = admit(outside(CHATGPT))
    identities = grants.store._admissions
    drive(local_mutate(identities.path, "alice", "suspend", reason="billing", suspended=True,
                       routing=projection(identities.path)))
    # Account revocation and deletion run withdrawn: no grant admits, none needs a revoke.
    with pytest.raises(PolicyRefused):
        grants.store.outside_check("alice", CHATGPT["client"], CHATGPT["authenticated_at"])
    assert not _outside_admits(grants.store, "alice", stamped)


def test_a_launch_rereads_the_grant_on_each_ta_and_extension_call(grants):
    from types import SimpleNamespace

    from tinyassets.extension_capabilities import ExtensionCapabilities, ExtensionError
    from tinyassets.ta_capabilities import Capabilities

    stamped = outside(CHATGPT)
    admit(stamped)
    context = SimpleNamespace(universe="home", initiating_agent="main", research=False,
                              delegated_authority="serving-owner", approval_id=None)
    ta = object.__new__(Capabilities)
    ta.outside_identity, ta.context = stamped, context
    ta.check_authority = lambda: "stop here"  # past the outside check
    message = {"op": "call", "name": "read_graph"}
    assert asyncio.run(ta._dispatch(message)) == {
        "error": "serving owner authority unavailable"}
    extensions = object.__new__(ExtensionCapabilities)
    extensions.backend = SimpleNamespace(outside_identity=stamped, context=context,
                                         check_authority=lambda: None)
    extensions._authority()
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="scoped", scopes=[HOME])
    # The launch's stamp is unchanged; its next call reads the rescope and refuses.
    assert asyncio.run(ta._dispatch(message)) == {
        "error": "outside client capability not granted"}
    with pytest.raises(ExtensionError, match="outside client authority unavailable"):
        extensions._authority()


def test_an_open_forwarded_stream_ends_when_its_grant_changes(grants, monkeypatch):
    import anyio

    from tinyassets import owner_router

    stamped = outside(CHATGPT)
    admit(stamped)
    monkeypatch.setattr(owner_router, "OUTSIDE_RECHECK_S", 0.01)

    async def watched(change=None):
        """Whether the watcher ended the stream before it ran its course."""
        ran = []
        async with anyio.create_task_group() as tasks:
            tasks.start_soon(owner_router._watch_grant, stamped, tasks.cancel_scope)
            if change is not None:
                await anyio.to_thread.run_sync(change)
            await anyio.sleep(1)
            ran.append(True)  # reached only when the watcher let the stream run
            tasks.cancel_scope.cancel()
        return not ran

    assert anyio.run(watched) is False  # an unchanged grant keeps the stream open
    assert anyio.run(watched, lambda: grants.grant(
        "alice", CHATGPT["client"], source="mcp", mode="revoked")) is True


def test_the_deny_switch_reaches_the_broker_too(grants, monkeypatch):
    from tinyassets.broker.server import _outside_admits

    stamped = admit(outside(CHATGPT))
    monkeypatch.setenv("TINYASSETS_OUTSIDE_DENY", "1")
    for call in (lambda: grants.store.outside_admit("alice", CLAUDE["client"], "mcp", 100),
                 lambda: grants.store.outside_check("alice", CHATGPT["client"], 100)):
        with pytest.raises(PolicyRefused, match="switched off"):
            call()
    assert not _outside_admits(grants.store, "alice", stamped)  # events capture
    from tinyassets.broker.authority_gate import outside_grant  # the OPEN gate's read

    with pytest.raises(PolicyRefused, match="switched off"):
        outside_grant(grants.store, "alice", CHATGPT["client"] + ":1:100")


def test_a_lost_grant_answer_keeps_the_key_its_committed_grant_belongs_to(grants,
                                                                         monkeypatch):
    from tinyassets import router_credentials
    from tinyassets.api_keys import ApiKeyStore, _client
    from tinyassets.broker import owner_identities
    from tinyassets.onboarding.api_keys import _mint

    monkeypatch.setattr(router_credentials, "mint_key", lambda root, owner, **kw: (
        ApiKeyStore(root).mint(owner, **kw)))
    answering = owner_identities._owner_request

    def lost(root, doc):
        answer = answering(root, doc)  # the broker commits
        if doc.get("action") == "outside_set":
            raise RuntimeError("the answer was lost")
        return answer

    monkeypatch.setattr(owner_identities, "_owner_request", lost)
    minted = _mint("alice", grants.proofs["alice"], {"name": "bot", "scopes": []})
    assert minted["key"].startswith("ta_key_")
    assert grants.store.outside_list("alice")[0]["client"] == _client(minted["key_id"])
    # With no grant committed, the key goes: never a key without its grant.
    def refused(root, doc):
        if doc.get("action") == "outside_set":
            raise RuntimeError("the broker never answered")
        return answering(root, doc)

    monkeypatch.setattr(owner_identities, "_owner_request", refused)
    with pytest.raises(RuntimeError):
        _mint("alice", grants.proofs["alice"], {"name": "bot", "scopes": []})
    assert [k["key_id"] for k in ApiKeyStore(grants.root).list("alice")["keys"]] == [
        minted["key_id"]]


def test_a_sign_in_just_before_a_revoke_stays_dead_after_a_re_grant(grants):
    import time

    admit(outside(CHATGPT))
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="revoked")
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="full")
    skewed = {**CHATGPT, "authenticated_at": int(time.time()) + 2}  # a clock a little ahead
    with pytest.raises(OutsideRefused, match="fresh sign-in"):
        admit(outside(skewed))


def test_a_scoped_client_reaches_its_mailbox_only_with_the_tool_granted(grants, monkeypatch):
    from tinyassets import mailbox_door

    calls = []

    def ledger(identity, op, action, **fields):
        calls.append((op, action))
        if (op, action) == ("OUTSIDE_AGENTS", "list"):
            return {"agents": [{"agent_id": "agent_1", "command_center_id": "home",
                                "outside_client": CHATGPT["client"],
                                "outside_family": CHATGPT["family"], "disabled_reason": None}]}
        return {"reader": "agent_1", "cursor": 0, "messages": [], "more": False}

    monkeypatch.setattr(mailbox_door, "_ledger", ledger)
    admit(outside(CHATGPT))
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="scoped",
                 scopes=[{**HOME, "capability": "write_graph"}])
    identity = outside(CHATGPT)
    admit(identity)
    with pytest.raises(mailbox_door.Refused, match="not granted"):
        mailbox_door.read(identity)
    assert ("MAILBOX_RECEIPT", "read") not in calls
    mailbox_door.ack(identity, through=0)  # write_graph in its own command center
    grants.grant("alice", CHATGPT["client"], source="mcp", mode="scoped", scopes=[HOME])
    admit(identity)
    assert mailbox_door.read(identity)["messages"] == []


@PEERCRED
def test_an_open_registered_after_a_revoke_rechecks_its_grant(broker, monkeypatch, tmp_path):
    from tinyassets.broker import authority_gate

    store, _ledger = _box_rig(broker, tmp_path)
    client = "6" * 64
    store.outside_admit("alice", client, "mcp", 100)
    gate = authority_gate.check

    def racing(policy, *args, **kwargs):
        refusal = gate(policy, *args, **kwargs)
        policy.outside_revoke("alice", client)  # lands between the gate and registration
        return refusal

    monkeypatch.setattr(authority_gate, "check", racing)
    with _box_channel(broker, monkeypatch, store) as sock:
        end = _box_open(sock, authority={"agent": "main", "outside": client + ":1:100"})
    assert (end["op"], end["error_class"], end["stream_sent"]) == ("END", "refused", False)
    assert broker.sent == []
