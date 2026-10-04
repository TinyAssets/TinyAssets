"""D6a: discovery, existing effect enforcement, and platform-minted context."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace

import pytest

from tests.test_authenticated_external_call_effector import (
    _install_inprocess_proxy,
    _install_loopback_driver,
    _Loopback,
    _setup,
)
from tinyassets import agent_review, agent_rules, ta_cli
from tinyassets.ta_capabilities import Capabilities, ExecutionContext


def backend(root, *, agent="worker", owner="user-1", platform=(), call=None,
            authority=lambda: None):
    return Capabilities(root, ExecutionContext(root.name, owner, agent),
                        list(platform), call, authority)


def invoke(service, **message):
    return asyncio.run(service.dispatch(message))


def call(service, **request):
    return invoke(service, op="call", name="connection:conn-http:POST",
                  arguments={"request": {"path": "/v1/messages", **request}})


def test_platform_search_describe_and_call_share_the_catalog(monkeypatch, tmp_path):
    calls = []

    async def handler(name, arguments):
        calls.append((name, arguments))
        return {"graphs": ["mine"]}

    schema = {"type": "object", "properties": {"target": {"type": "string"}}}
    service = backend(tmp_path, platform=[{"name": "read_graph", "description": "Read graphs",
                                          "arguments": schema}], call=handler)
    monkeypatch.setattr(ta_cli, "remote", lambda msg: invoke(service, **msg))
    monkeypatch.setattr(ta_cli, "extensions", lambda _roots: {})
    assert ta_cli.main(["search", "graphs"]) == [
        {"name": "read_graph", "description": "Read graphs"}]
    assert ta_cli.main(["describe", "read_graph"])["arguments"] == schema
    assert ta_cli.main(["read_graph", "--json", '{"target":"graph"}']) == {"graphs": ["mine"]}
    assert calls == [("read_graph", {"target": "graph"})]


@pytest.mark.parametrize("behaviour,expected", [
    (agent_rules.DO, None), (agent_rules.HAND_OFF, "rule_hand_off"),
    (agent_rules.ASK_FIRST, "rule_ask_first"),
    (agent_rules.DO_IF_PREAPPROVED, "rule_ask_first"),
])
def test_connection_uses_initiating_agents_rules_and_broker(
    tmp_path, monkeypatch, behaviour, expected,
):
    monkeypatch.setenv("TINYASSETS_OUTBOUND_HTTP_CONNECTIONS_ENABLED", "1")
    _, root, db = _setup(tmp_path, token="D6-SYNTHETIC-SECRET")
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(monkeypatch, db_path=db, universe_dir=root,
                             grant_id="grant-http", provider="http", destination="api.example.com",
                             runtime_root=tmp_path / "runtime")
    # The main agent's rule disagrees: a fallback to main would fail this test.
    agent_rules.set_rule(root, "app.write", agent_rules.HAND_OFF if expected is None
                         else agent_rules.DO, agent="main")
    agent_rules.set_rule(root, "app.write", behaviour, agent="worker")
    try:
        with agent_review.bound(
            lambda *_a, **_k: '{"verdict":"proceed","reason":"test"}', active=True,
        ):
            result = call(backend(root), body={"hello": "world"})["result"]
        if expected:
            assert result["error_kind"] == expected
            assert loop.recorded == []
        else:
            assert result.get("delivered") is True, result
            assert loop.recorded[0]["headers"]["Authorization"] == "Bearer D6-SYNTHETIC-SECRET"
        assert "D6-SYNTHETIC-SECRET" not in json.dumps(result)
        catalog = invoke(backend(root), op="catalog")
        assert "D6-SYNTHETIC-SECRET" not in json.dumps(catalog)
        assert "credential_ref" not in json.dumps(catalog)
        assert catalog["capabilities"][0]["name"] == "connection:conn-http:POST"
        assert catalog["extension_roots"]["agent"] == "/u/agents/worker/extensions"
    finally:
        loop.stop()


def test_connection_missing_consent_and_scope_still_refuse(tmp_path, monkeypatch):
    _, root, db = _setup(tmp_path, grant_consent_for=False)
    service = backend(root)
    agent_rules.set_rule(root, "app.write", agent_rules.DO, agent="worker")
    with agent_review.bound(lambda *_a, **_k: '{"verdict":"proceed","reason":"test"}', active=True):
        assert call(service)["result"]["error_kind"] == "missing_consent"
    # Scope checks run in the real broker, even if a crafted path reaches it.
    from tinyassets.storage.effector_consents import grant_consent

    grant_consent(root, sink="authenticated_external_call", destination="api.example.com",
                  granted_by="test")
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(monkeypatch, db_path=db, universe_dir=root,
                             grant_id="grant-http", provider="http", destination="api.example.com",
                             runtime_root=tmp_path / "runtime")
    try:
        with agent_review.bound(
            lambda *_a, **_k: '{"verdict":"proceed","reason":"test"}', active=True,
        ):
            assert call(service, path="/not-allowed")["result"]["error_kind"] == (
                "outbound_request_failed")
        assert loop.recorded == []
    finally:
        loop.stop()


def test_cross_user_and_context_spoofing_fail_closed(tmp_path):
    _, root, _ = _setup(tmp_path)
    foreign = backend(root, owner="user-2")
    assert invoke(foreign, op="catalog")["capabilities"] == []
    assert call(foreign) == {"error": "unknown capability"}
    service = backend(root)
    assert invoke(service, op="catalog", owner="user-1")["error"] == "invalid ta request"
    assert invoke(service, op="call", name="connection:conn-http:POST",
                  arguments={"request": {}, "execution_context": {"initiating_agent": "main"}})[
                      "error"]
    assert invoke(backend(root, authority=lambda: "revoked"), op="catalog")["error"]
    service.context = replace(service.context, research=True)
    assert invoke(service, op="catalog")["error"] == "research_is_read_only"
    other = tmp_path / "other-universe"
    other.mkdir()
    assert call(backend(other))["error"] == "unknown capability"


def test_context_is_rechecked_below_catalog_and_packet_cannot_override_it(tmp_path):
    from tinyassets.effectors.authenticated_external_call import (
        run_authenticated_external_call_effector,
    )

    _, root, _ = _setup(tmp_path)
    context = ExecutionContext(root.name, "user-2", "worker")
    packet = {"sink": "authenticated_external_call", "connection_id": "conn-http",
              "grant_id": "grant-http", "verb": "POST", "request": {"path": "/v1/messages"},
              "owner": "user-1", "initiating_agent": "main"}

    def run(ctx):
        return run_authenticated_external_call_effector(node_id="ta", output_keys=["call"],
            run_state={"call": packet}, base_path=root, execution_context=ctx)

    assert run(context)["error_kind"] == "connection_owner_mismatch"
    assert run(replace(context, universe="other"))["error_kind"] == "execution_context_mismatch"
    assert run(replace(context, research=True))["error_kind"] == "research_is_read_only"


def test_reflected_credential_never_crosses_broker_and_revocation_is_current(tmp_path, monkeypatch):
    from tinyassets.storage.outbound_connections import ConnectionLedger

    _, root, db = _setup(tmp_path, token="D6-REFLECTED-SECRET")
    service = backend(root)
    assert invoke(service, op="catalog")["capabilities"]
    agent_rules.set_rule(root, "app.write", agent_rules.DO, agent="worker")
    loop = _Loopback(lambda *_: b'{"echo":"D6-REFLECTED-SECRET"}')
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(monkeypatch, db_path=db, universe_dir=root,
                             grant_id="grant-http", provider="http", destination="api.example.com",
                             runtime_root=tmp_path / "runtime")
    try:
        with agent_review.bound(
            lambda *_a, **_k: '{"verdict":"proceed","reason":"test"}', active=True,
        ):
            result = call(service)["result"]
        assert loop.recorded[0]["headers"]["Authorization"] == "Bearer D6-REFLECTED-SECRET"
        assert result["error_kind"] == "outbound_request_failed"
        assert "D6-REFLECTED-SECRET" not in json.dumps(result)
        ConnectionLedger(db).revoke_grant("grant-http")
        assert call(service)["error"] == "unknown capability"
        assert len(loop.recorded) == 1
    finally:
        loop.stop()


# --- The launch's tool grant bounds ta (PR #4439 review finding 2) ---

GRANT_KEY = "k" * 43


def signed_launch(monkeypatch, tools=None, *, session="", turn="", key=GRANT_KEY, url=None):
    """Serve the next engine call on the route the platform would launch with."""
    from types import SimpleNamespace
    from urllib.parse import parse_qsl, urlsplit

    from fastmcp.server import dependencies

    from tinyassets.engine_steering import route_with_session
    from tinyassets.served_tools import LAUNCH_GRANT_KEY_ENV, SERVED_ENGINE_MCP_TOOLS

    monkeypatch.setenv(LAUNCH_GRANT_KEY_ENV, GRANT_KEY)
    if url is None:
        url = route_with_session(
            "http://127.0.0.1:8790/mcp", session, turn, grant_key=key,
            tools=SERVED_ENGINE_MCP_TOOLS if tools is None else tools)
    monkeypatch.setattr(dependencies, "get_http_request", lambda: SimpleNamespace(
        query_params=dict(parse_qsl(urlsplit(url).query))))
    return url


def engine(monkeypatch, root, calls):
    from types import SimpleNamespace

    from tinyassets.api import helpers
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS

    async def list_tools():
        return [SimpleNamespace(name=name, description=name, parameters={"type": "object"})
                for name in SERVED_ENGINE_MCP_TOOLS]

    async def call_tool(name, arguments):
        calls.append(name)
        return SimpleNamespace(content=[SimpleNamespace(
            model_dump=lambda **_: {"type": "text", "text": json.dumps({"called": name})})])

    monkeypatch.setattr(helpers, "_universe_dir", lambda _universe: root)
    return SimpleNamespace(
        _GRAPH_ID=root.name, _ACTOR_ID="user-1", _acting_agent=lambda: "worker",
        _binding_error=lambda: None,
        mcp=SimpleNamespace(list_tools=list_tools, call_tool=call_tool))


def through_ta(server, *messages):
    """What each message returns through the launch's ta; None when ta is withheld."""
    from tinyassets.ta_capabilities import engine_dispatch

    async def run():
        dispatch = await engine_dispatch(server)
        if dispatch is None:
            return None
        return [await asyncio.to_thread(dispatch, message) for message in messages]

    return asyncio.run(run())


CATALOG = {"op": "catalog"}
CONNECTION = {"op": "call", "name": "connection:conn-http:POST",
              "arguments": {"request": {"path": "/v1/messages"}}}
FILE_TOOLS = ("read", "write", "edit", "bash")


def platform_call(name):
    return {"op": "call", "name": name, "arguments": {}}


def test_unrestricted_launch_keeps_every_served_capability_and_connections(
    tmp_path, monkeypatch,
):
    from tinyassets.providers.base import ModelConfig
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS, granted_tools

    _, root, _ = _setup(tmp_path)
    calls = []
    # The owner's own chat and an agent node naming no tool: the default grant.
    signed_launch(monkeypatch, granted_tools(ModelConfig()))
    catalog, wrote, ran = through_ta(engine(monkeypatch, root, calls), CATALOG,
                                     platform_call("write_graph"), platform_call("run_graph"))
    assert [item["name"] for item in catalog["capabilities"]] == [
        t for t in SERVED_ENGINE_MCP_TOOLS if t not in FILE_TOOLS
    ] + ["connection:conn-http:POST"]
    assert wrote == {"result": {"called": "write_graph"}}
    assert ran == {"result": {"called": "run_graph"}} and calls == ["write_graph", "run_graph"]


@pytest.mark.parametrize("tools_allowed,reachable", [
    (["agent", "read", "bash"], []),
    (["agent", "read_graph", "read_brain", "bash"], ["read_graph", "read_brain"]),
    # write_graph alone cannot run what it builds, so a connection stays out of reach.
    (["agent", "write_graph", "bash"], ["write_graph"]),
])
def test_node_grant_is_the_whole_reach_of_ta(tmp_path, monkeypatch, tools_allowed, reachable):
    from tinyassets.providers.base import ModelConfig
    from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS, granted_tools
    from tinyassets.shared_self import _granted_config

    _, root, _ = _setup(tmp_path)
    calls = []
    config = _granted_config(ModelConfig(), {"tools_allowed": tools_allowed})
    signed_launch(monkeypatch, granted_tools(config), session="node:branch-1:worker")
    withheld = [t for t in SERVED_ENGINE_MCP_TOOLS if t not in reachable and t not in FILE_TOOLS]
    server = engine(monkeypatch, root, calls)
    catalog, *answers = through_ta(
        server, CATALOG, CONNECTION, *map(platform_call, reachable + withheld))
    assert [item["name"] for item in catalog["capabilities"]] == reachable
    assert answers[0] == {"error": "unknown capability"}
    assert answers[1:1 + len(reachable)] == [{"result": {"called": t}} for t in reachable]
    assert answers[1 + len(reachable):] == [{"error": "unknown capability"}] * len(withheld)
    assert calls == reachable
    # The four file tools are never ta capabilities, granted or not.
    assert through_ta(server, platform_call("bash")) == [{"error": "unknown capability"}]


def test_grant_holding_build_and_run_reaches_connections_as_before(tmp_path, monkeypatch):
    _, root, _ = _setup(tmp_path)
    signed_launch(monkeypatch, ["write_graph", "run_graph", "bash"])
    (catalog,) = through_ta(engine(monkeypatch, root, []), CATALOG)
    assert [item["name"] for item in catalog["capabilities"]] == [
        "run_graph", "write_graph", "connection:conn-http:POST"]


def test_unsigned_forged_or_foreign_grant_never_widens(tmp_path, monkeypatch):
    from tinyassets.engine_mcp_http import _EngineServer
    from tinyassets.served_tools import LAUNCH_GRANT_KEY_ENV, SERVED_ENGINE_MCP_TOOLS

    _, root, _ = _setup(tmp_path)
    calls = []
    server = engine(monkeypatch, root, calls)
    probe = (platform_call("write_graph"), CONNECTION)
    narrow = signed_launch(monkeypatch, ["read", "bash"], session="node:b:n", turn="t1")
    assert GRANT_KEY not in narrow and "grant=read%2Cbash." in narrow
    assert through_ta(server, *probe) == [{"error": "unknown capability"}] * 2
    full = "%2C".join(SERVED_ENGINE_MCP_TOOLS)
    wide = signed_launch(monkeypatch, None, session="node:b:other", turn="t1")
    forgeries = [
        "http://h/mcp?session=node%3Ab%3An&turn=t1",                # no grant
        narrow.replace("grant=read%2Cbash", f"grant={full}"),       # edited names, old mac
        narrow.replace("grant=read%2Cbash", "grant=%2A"),           # invented wildcard
        narrow.split("&grant=")[0] + f"&grant={full}",              # names with no mac
        narrow.split("&grant=")[0] + f"&grant={full}." + "0" * 64,  # names with a made-up mac
        wide.replace("node%3Ab%3Aother", "node%3Ab%3An"),           # another launch's grant
        narrow.replace("turn=t1", "turn=t2"),                       # another turn
    ]
    for url in forgeries:
        signed_launch(monkeypatch, url=url)
        assert through_ta(server, *probe) is None, url
    # Another universe's (or a retired) engine server signs with a different key.
    other = _EngineServer("other-universe", "user-2", 8791, str(tmp_path))
    assert other.grant_key not in ("", other.secret, GRANT_KEY)
    signed_launch(monkeypatch, None, key=other.grant_key)
    assert through_ta(server, *probe) is None
    # A server with no key grants nothing.
    signed_launch(monkeypatch, None)
    monkeypatch.delenv(LAUNCH_GRANT_KEY_ENV)
    assert through_ta(server, *probe) is None
    # A launch never granted bash holds no ta, whatever else it was granted.
    signed_launch(monkeypatch, ["read_graph", "write_graph", "run_graph"])
    assert through_ta(server, *probe) is None
    # The request body is never a grant.
    signed_launch(monkeypatch, ["read", "bash"])
    assert through_ta(server, {"op": "catalog", "grant": "*"},
                      dict(platform_call("write_graph"), grant=list(SERVED_ENGINE_MCP_TOOLS))) == [
        {"error": "invalid ta request"}] * 2
    assert calls == []


def test_grant_key_travels_only_on_the_private_route(tmp_path, monkeypatch):
    from tinyassets import engine_mcp_http as routes

    server = routes._EngineServer("u-a", "actor-a", 8790, str(tmp_path))
    routes._write_routes(tmp_path, [server])
    monkeypatch.setattr(routes, "engine_tools_authorized", lambda **_: True)
    route = routes.read_engine_mcp_route(actor_id="actor-a", graph_id="u-a", root=tmp_path)
    assert route.grant_key == server.grant_key and server.grant_key not in repr(route)
    # A record written before the key existed still reads; its launches hold no ta.
    record = json.loads((tmp_path / routes.ROUTES_FILENAME).read_text())
    del record["u-a"]["grant_key"]
    (tmp_path / routes.ROUTES_FILENAME).write_text(json.dumps(record))
    old = routes.read_engine_mcp_route(actor_id="actor-a", graph_id="u-a", root=tmp_path)
    assert old.grant_key == ""
    signed_launch(monkeypatch, None, key=old.grant_key)
    assert through_ta(engine(monkeypatch, tmp_path, []), CATALOG) is None
