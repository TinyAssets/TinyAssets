"""Outside clients cannot author themselves more authority or revive revoked families."""
import json

import pytest

from tests.test_inline_owner_sessions import setup as owner_setup  # noqa: F401
from tinyassets.outside_authority import OutsideClientAuthority, OutsideRefused, origin


@pytest.mark.parametrize("selector", ["agent_id", "agent_binding_id", "conversation_agent"])
def test_every_named_agent_selector_requires_exact_grant(tmp_path, monkeypatch, selector):
    from tinyassets.auth.provider import Identity
    from tinyassets.outside_authority import check_mcp_request

    store = OutsideClientAuthority(tmp_path)
    monkeypatch.setattr("tinyassets.outside_authority.current_store", lambda: store)
    source = {"client": "a", "family": "s", "authenticated_at": 100}
    store.observe("owner", source)
    store.set_enabled(True)
    store.change("owner", "a", expected_generation=0, family="s", scopes=[
        {"universe": "home", "agent": "main", "capability": "get_status"}])
    identity = Identity("owner", "owner", metadata={"outside_origin": store.admit("owner", source)})
    def request(agent):
        check_mcp_request(identity, {"method": "tools/call", "params": {
            "name": "get_status", "arguments": {"universe_id": "home",
                "include_conversation": True, selector: agent}}})
    request("main")
    with pytest.raises(OutsideRefused):
        request("private-agent")


def test_independent_clients_revoke_reconnect_and_kill_switch(tmp_path, monkeypatch):
    monkeypatch.setattr("tinyassets.outside_authority.time.time", lambda: 200)
    first, worker = OutsideClientAuthority(tmp_path), OutsideClientAuthority(tmp_path)
    scope = {"universe": "home", "agent": "main", "capability": "read_graph"}
    a = {"client": "a", "family": "a1", "authenticated_at": 100}
    b = {"client": "b", "family": "b1", "authenticated_at": 100}
    for bound in (a, b):
        first.observe("owner", bound)
        first.change("owner", bound["client"], expected_generation=0,
                     family=bound["family"], scopes=[scope])
    with pytest.raises(OutsideRefused):
        worker.admit("owner", a)
    first.set_enabled(True)
    admitted = worker.admit("owner", a, **scope)
    assert admitted["generation"] == 1
    for change in ({"capability": "write_graph"}, {"agent": "other"}, {"universe": "other"}):
        with pytest.raises(OutsideRefused):
            worker.admit("owner", a, **{**scope, **change})
    with pytest.raises(OutsideRefused):
        worker.admit("foreign-owner", a, **scope)
    first.change("owner", "a", expected_generation=1, family="a1", scopes=[], revoke=True)
    with pytest.raises(OutsideRefused):
        worker.admit("owner", admitted, **scope)
    assert worker.admit("owner", b, **scope)
    with pytest.raises(OutsideRefused, match="fresh"):
        first.change("owner", "a", expected_generation=2, family="a1", scopes=[scope])
    fresh = {"client": "a", "family": "a2", "authenticated_at": 300}
    first.observe("owner", fresh)
    first.change("owner", "a", expected_generation=2, family="a2", scopes=[scope])
    assert worker.admit("owner", fresh, **scope)["generation"] == 3
    with pytest.raises(OutsideRefused):
        worker.admit("owner", admitted)
    first.set_enabled(False)
    for bound in (fresh, b):
        with pytest.raises(OutsideRefused):
            worker.admit("owner", bound)


def test_scope_change_fences_existing_work_and_deny_override_never_enables(tmp_path, monkeypatch):
    store = OutsideClientAuthority(tmp_path)
    bound = {"client": "a", "family": "s", "authenticated_at": 100}
    store.observe("owner", bound)
    store.change("owner", "a", expected_generation=0, family="s", scopes=[])
    store.set_enabled(True)
    old = store.admit("owner", bound)
    store.change("owner", "a", expected_generation=1, family="s", scopes=[])
    with pytest.raises(OutsideRefused):
        store.admit("owner", old)
    monkeypatch.setenv("TINYASSETS_OUTSIDE_DENY", "1")
    with pytest.raises(OutsideRefused):
        store.admit("owner", bound)
    monkeypatch.delenv("TINYASSETS_OUTSIDE_DENY")
    store.set_enabled(False)
    with pytest.raises(OutsideRefused):
        store.admit("owner", bound)


def test_verified_claim_configuration_and_exact_firstparty(monkeypatch):
    monkeypatch.setenv("TINYASSETS_OUTSIDE_CLIENT_CLAIM", "client_id")
    monkeypatch.setenv("TINYASSETS_FIRST_PARTY_CLIENTS", json.dumps(["https://issuer|first"]))
    claims = {"iss": "https://issuer", "client_id": "external", "sid": "session", "auth_time": 100}
    assert origin(claims)["client"] != "external"
    assert origin({**claims, "client_id": "first"}) is None
    assert origin({**claims, "iss": "https://other", "client_id": "first"}) is not None
    with pytest.raises(OutsideRefused):
        origin({"iss": "https://issuer", "sid": "session", "client_name": "first"})


def test_signed_launch_origin_cannot_be_stripped(monkeypatch):
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.served_tools import launch_grant, verified_launch_grant

    identity = Identity("owner", "owner", metadata={"outside_origin": {
        "client": "a", "family": "s", "generation": 4}})
    with identity_context(identity):
        grant = launch_grant("key", "thread", "turn", ["bash"])
        with pytest.raises(PermissionError, match="signed"):
            launch_grant("", "thread", "turn", ["bash"])
        from tinyassets.engine_steering import route_with_session

        with pytest.raises(PermissionError, match="signed"):
            route_with_session("http://localhost/mcp", "thread")
    assert verified_launch_grant("key", "thread", "turn", grant) == ("bash",)
    stripped = "bash." + grant.rpartition(".")[2]
    assert verified_launch_grant("key", "thread", "turn", stripped) is None


def test_corrupt_authority_store_fails_closed(tmp_path):
    store = OutsideClientAuthority(tmp_path)
    store.path.write_bytes(b"not a database")
    with pytest.raises(OutsideRefused):
        store.admit("owner", {"client": "a", "family": "s", "authenticated_at": 100})


def test_queued_run_rehydrates_origin_and_revocation_survives_restart(tmp_path, monkeypatch):
    from tinyassets.api import helpers
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.outside_authority import run_identity
    from tinyassets.runs import create_run

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    store = OutsideClientAuthority(tmp_path)
    source = {"client": "a", "family": "s", "authenticated_at": 100}
    store.observe("owner", source)
    store.set_enabled(True)
    store.change("owner", "a", expected_generation=0, family="s", scopes=[])
    bound = store.admit("owner", source)
    with identity_context(Identity("owner", "owner", metadata={"outside_origin": bound})):
        run = create_run(tmp_path, branch_def_id="b", thread_id="", inputs={},
                         actor="owner", owner_user_id="owner")
    # Recovery has a new unrestricted owner identity; durable origin wins.
    with identity_context(Identity("owner", "owner")), run_identity(tmp_path, run):
        assert current_identity().metadata["outside_origin"] == bound
        child = create_run(tmp_path, branch_def_id="b", thread_id="", inputs={},
                           actor="owner", owner_user_id="owner")
    store.change("owner", "a", expected_generation=1, family="s", scopes=[], revoke=True)
    for queued in (run, child):
        with identity_context(Identity("owner", "owner")), pytest.raises(OutsideRefused):
            with run_identity(tmp_path, queued):
                pytest.fail("revoked queued work started")


def test_inflight_effect_does_not_block_revoke_or_admit_future_effects(tmp_path, monkeypatch):
    import threading

    from tinyassets.api import helpers
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.outside_authority import effect_admission

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    store = OutsideClientAuthority(tmp_path)
    source = {"client": "a", "family": "s", "authenticated_at": 100}
    store.observe("owner", source)
    store.set_enabled(True)
    store.change("owner", "a", expected_generation=0, family="s", scopes=[])
    bound = store.admit("owner", source)
    started, done = threading.Event(), threading.Event()
    def revoke():
        started.set()
        store.change("owner", "a", expected_generation=1, family="s", scopes=[], revoke=True)
        done.set()
    with identity_context(Identity("owner", "owner", metadata={"outside_origin": bound})):
        with effect_admission():
            worker = threading.Thread(target=revoke)
            worker.start()
            assert started.wait(2)
            assert done.wait(2), "in-flight network work must not block revoke"
            with pytest.raises(OutsideRefused):
                with effect_admission():
                    pytest.fail("new effect after acknowledged revoke")
        worker.join(3)
        assert done.is_set()
        with pytest.raises(OutsideRefused):
            with effect_admission():
                pytest.fail("effect admitted after revoke acknowledgment")


def test_http_scopes_aliases_and_non_mcp_routes(tmp_path, monkeypatch):
    import asyncio

    from tests.test_onboarding_auth_boundary import _ok_app, _RequireAuthProvider
    from tinyassets.api import helpers
    from tinyassets.auth import middleware as mw
    from tinyassets.auth.provider import Identity

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr("tinyassets.daemon_server.get_founder_home", lambda *a: "home")
    store = OutsideClientAuthority(tmp_path)
    source = {"client": "a", "family": "s", "authenticated_at": 100}
    store.observe("owner", source)
    store.set_enabled(True)
    store.change("owner", "a", expected_generation=0, family="s", scopes=[
        {"universe": "home", "agent": "main", "capability": name}
        for name in ("read_graph", "read_page")])
    class Provider(_RequireAuthProvider):
        def resolve_token(self, token):
            return Identity("owner", "owner", metadata={"outside_origin":
                                                        store.admit("owner", source)})
    monkeypatch.setattr(mw, "_provider", Provider())
    def request(path="/mcp", name="read_graph", arguments=None):
        document = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": name, "arguments": arguments or {}}}
        async def receive():
            return {"type": "http.request", "body": json.dumps(document).encode(),
                    "more_body": False}
        sent = []
        async def send(message):
            sent.append(message)
        asyncio.run(mw.AuthContextMiddleware(_ok_app)(
            {"type": "http", "method": "POST", "path": path,
             "headers": [(b"authorization", b"Bearer verified")]}, receive, send))
        return next(m["status"] for m in sent if m["type"] == "http.response.start")
    assert request() == 200
    assert request(name="write_graph") == 403
    assert request(arguments={"agent_binding_id": "other"}) == 403
    assert request(name="read_page", arguments={"command_center_id": "other"}) == 403
    assert request(arguments={"run_id": "indirect"}) == 403
    assert request(path="/app/outside-clients") == 403


def test_scheduled_work_inherits_origin_and_cannot_outlive_revoke(tmp_path, monkeypatch):
    from dataclasses import replace
    from datetime import datetime, timezone

    from tinyassets.api import helpers
    from tinyassets.auth.middleware import current_identity, identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.automations import Automation, AutomationStore
    from tinyassets.outside_authority import automation_identity

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    store = OutsideClientAuthority(tmp_path)
    source = {"client": "a", "family": "s", "authenticated_at": 100}
    store.observe("owner", source)
    store.set_enabled(True)
    store.change("owner", "a", expected_generation=0, family="s", scopes=[])
    bound = store.admit("owner", source)
    stamp = datetime.now(timezone.utc).isoformat()
    automation = Automation("scheduled", "home", "owner", "Schedule", "branch", "interval",
                            60, "", {}, "active", "", 1, stamp, stamp, "", "", "", "", "")
    with identity_context(Identity("owner", "owner", metadata={"outside_origin": bound})):
        AutomationStore(tmp_path).insert(automation)
    with identity_context(Identity("owner", "owner")), automation_identity(tmp_path, automation):
        assert current_identity().metadata["outside_origin"] == bound
        wake = replace(automation, automation_id="wake", trigger_kind="once")
        AutomationStore(tmp_path).insert(wake)
    store.change("owner", "a", expected_generation=1, family="s", scopes=[], revoke=True)
    for work in (automation, wake):
        with pytest.raises(OutsideRefused), automation_identity(tmp_path, work):
            pytest.fail("saved outside work survived revoke")


@pytest.mark.usefixtures("owner_setup")
def test_grants_require_protected_owner_session(tmp_path, monkeypatch):
    import asyncio

    from tests.test_inline_owner_sessions import request
    from tinyassets import onboarding
    from tinyassets.api import helpers
    from tinyassets.onboarding.outside_clients import handle

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr("tinyassets.daemon_server.get_founder_home", lambda *a: "home")
    store = OutsideClientAuthority(tmp_path)
    store.observe("alice", {"client": "a", "family": "s", "authenticated_at": 100})
    document = {"operation": "grant", "client": "a", "family": "s", "expected_generation": 0,
                "scopes": [{"universe": "home", "agent": "main", "capability": "read_graph"}]}
    async def read(*args, **kwargs):
        return document
    monkeypatch.setattr(onboarding, "_read_small_json", read)
    assert asyncio.run(handle(request(cookie=""))).status_code == 403
    assert asyncio.run(handle(request(origin="https://other.example"))).status_code == 403
    assert asyncio.run(handle(request())).status_code == 200
    assert asyncio.run(handle(request())).status_code == 403  # stale generation
    assert store.inspect("other-owner")["clients"] == []


def test_resolved_indirect_object_cannot_escape_admitted_universe(tmp_path, monkeypatch):
    from tinyassets.api import helpers, permissions
    from tinyassets.auth.middleware import identity_context
    from tinyassets.auth.provider import Identity
    from tinyassets.outside_authority import check_mcp_request

    monkeypatch.setattr(helpers, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(permissions, "_universe_is_owned", lambda *args: True)
    monkeypatch.setattr(permissions, "universe_public_read_allowed", lambda *args: True)
    store = OutsideClientAuthority(tmp_path)
    source = {"client": "a", "family": "s", "authenticated_at": 100}
    store.observe("owner", source)
    store.set_enabled(True)
    store.change("owner", "a", expected_generation=0, family="s", scopes=[
        {"universe": "home", "agent": "main", "capability": "read_graph"}])
    identity = Identity("owner", "owner", metadata={"outside_origin": store.admit("owner", source)})
    with identity_context(identity):
        check_mcp_request(identity, {"method": "tools/call", "params": {
            "name": "read_graph", "arguments": {"graph_id": "home", "run_id": "indirect"}}})
        assert permissions.universe_access_allows("home")
        assert not permissions.universe_access_allows("other")
