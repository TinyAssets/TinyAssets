"""D6a: discovery, existing effect enforcement, and platform-minted context."""
from __future__ import annotations

import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from tests.test_authenticated_external_call_effector import (
    _install_inprocess_proxy,
    _install_loopback_driver,
    _Loopback,
    _setup,
)
from tinyassets import agent_review, agent_rules, ta_cli
from tinyassets.ta_capabilities import Capabilities, ExecutionContext


def engine(root, monkeypatch):
    from fastmcp import FastMCP

    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(root.parent))
    return SimpleNamespace(mcp=FastMCP("ta regression"), _GRAPH_ID=root.name,
                           _ACTOR_ID="user-1", _acting_agent=lambda: "worker",
                           _binding_error=lambda: None)


def test_response_header_values_never_cross_into_jail(tmp_path, monkeypatch):
    from tinyassets.effectors import authenticated_external_call as aec

    _, root, _ = _setup(tmp_path)
    headers = {"Set-Cookie": "session=ROTATED-SECRET", "X-Echo-Credential": "encoded-secret",
               "Content-Type": "application/json", "ETag": "also-untrusted"}
    response = {"headers": headers, "body": "full body " * 3000}
    monkeypatch.setattr(aec, "run_authenticated_external_call_effector",
                        lambda **_: {"delivered": True, "response": response})
    result = call(backend(root))["result"]
    assert result["response"] == {
        "header_names": sorted(headers), "body": response["body"]}
    assert response["headers"] == headers  # leave the worker's own result intact
    for value in headers.values():
        assert value not in json.dumps(result)


@pytest.mark.parametrize("enabled,sampling,verdict,expected", [
    (False, False, "proceed", None),
    (False, True, "proceed", None),
    (True, False, "proceed", "auto_review_unavailable"),
    (True, True, "proceed", None),
    (True, True, "needs_approval", "auto_review_needs_approval"),
])
def test_engine_binds_owners_opt_in_review_to_callers_sampling(
    tmp_path, monkeypatch, enabled, sampling, verdict, expected,
):
    from fastmcp import Client

    from tinyassets.ta_capabilities import engine_dispatch

    _, root, db = _setup(tmp_path)
    server = engine(root, monkeypatch)
    agent_rules.set_rule(root, "app.write", agent_rules.DO, agent="worker")
    if enabled:
        agent_review.set_review(root, "app.write", True, confirm=True, agent="worker")
    loop = _Loopback()
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(monkeypatch, db_path=db, universe_dir=root,
                             grant_id="grant-http", provider="http", destination="api.example.com",
                             runtime_root=tmp_path / "runtime")
    reviews = []

    async def sample(messages, params, context):
        reviews.append((messages, params))
        return json.dumps({"verdict": verdict, "reason": "owner's connected model"})

    @server.mcp.tool(name="bash")
    async def turn():
        dispatch = await engine_dispatch(server)
        return await asyncio.to_thread(dispatch, {
            "op": "call", "name": "connection:conn-http:POST",
            "arguments": {"request": {"path": "/v1/messages"}},
        })

    async def run():
        async with Client(server.mcp, sampling_handler=sample if sampling else None) as client:
            return await client.call_tool("bash", {})

    try:
        answer = asyncio.run(run())
        result = json.loads(answer.content[0].text)["result"]
        if expected:
            assert result["error_kind"] == expected
            assert loop.recorded == []
        else:
            assert result["delivered"] is True, result
            assert len(loop.recorded) == 1
        assert len(reviews) == int(enabled and sampling)
        if reviews:
            messages, params = reviews[0]
            assert "conn-http" in messages[0].content.text
            assert params.systemPrompt == agent_review.SAFETY_REQUIREMENTS
            assert params.includeContext == "none"
    finally:
        loop.stop()


@pytest.mark.parametrize("kind", ["refusal", "validation", "large_refusal"])
def test_engine_platform_refusal_and_validation_text_is_preserved(tmp_path, monkeypatch, kind):
    from tinyassets.engine_mcp_server import RefusalsAreErrors
    from tinyassets.engine_result_bounds import resolve_ceiling
    from tinyassets.ta_capabilities import engine_dispatch

    server = engine(tmp_path, monkeypatch)
    server.mcp.add_middleware(RefusalsAreErrors())

    @server.mcp.tool(name="read_graph")
    async def read_graph(target: int):
        return json.dumps({"error": "Owner refused this action" + (
            " x" * resolve_ceiling() if kind == "large_refusal" else "")})

    async def run():
        dispatch = await engine_dispatch(server)
        return await asyncio.to_thread(dispatch, {
            "op": "call", "name": "read_graph",
            "arguments": {"target": "invalid" if kind == "validation" else 1},
        })

    text = asyncio.run(run())["result"]["error"]
    assert "ta request failed" not in text
    assert ("target" if kind == "validation" else "Owner refused this action") in text
    assert len(text.encode()) <= resolve_ceiling()
    if kind == "large_refusal":
        assert json.loads(text)["truncated"] is True


@pytest.mark.parametrize("bad", ["{", "[]", "x" * (256 * 1024 + 1),
                                '{"tools": [], "tools": []}', "duplicate_tools"],
                         ids=["malformed", "wrong_shape", "oversized", "duplicate_keys",
                              "duplicate_tools"])
def test_bad_extension_is_reported_without_breaking_other_capabilities(
    tmp_path, monkeypatch, capsys, bad,
):
    tool = {"name": "hello", "description": "Hello", "arguments": {"type": "object"}}
    spec = {"executable": "run", "tools": [tool]}
    for name, raw in (("good", json.dumps(spec)), ("bad", bad)):
        package = tmp_path / name
        package.mkdir()
        if raw == "duplicate_tools":
            raw = json.dumps({**spec, "tools": [tool, tool]})
        (package / "extension.json").write_text(raw)

    def remote(message):
        if message["op"] == "catalog":
            return {"extension_roots": {"shared": str(tmp_path)}, "capabilities": [
                {"name": "read_graph", "description": "Read", "arguments": {}}]}
        return {"result": {"ok": True}}

    monkeypatch.setattr(ta_cli, "remote", remote)
    found = ta_cli.main(["search"])
    assert {item["name"] for item in found} == {"read_graph", "ext:shared:good:hello"}
    assert ta_cli.main(["read_graph", "--json", "{}"]) == {"ok": True}
    assert ta_cli.main(["describe", "ext:shared:good:hello"])["arguments"] == tool["arguments"]
    captured = capsys.readouterr()
    assert "skipped extension" in captured.err and "bad" in captured.err
    assert captured.out == ""


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
    agent_review.set_review(root, "app.write", False, confirm=True, agent="worker")
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
    agent_review.set_review(root, "app.write", False, confirm=True, agent="worker")
    service = backend(root)
    agent_rules.set_rule(root, "app.write", agent_rules.DO, agent="worker")
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
    agent_review.set_review(root, "app.write", False, confirm=True, agent="worker")
    service = backend(root)
    assert invoke(service, op="catalog")["capabilities"]
    agent_rules.set_rule(root, "app.write", agent_rules.DO, agent="worker")
    loop = _Loopback(lambda *_: b'{"echo":"D6-REFLECTED-SECRET"}')
    _install_loopback_driver(monkeypatch, loop.port)
    _install_inprocess_proxy(monkeypatch, db_path=db, universe_dir=root,
                             grant_id="grant-http", provider="http", destination="api.example.com",
                             runtime_root=tmp_path / "runtime")
    try:
        result = call(service)["result"]
        assert loop.recorded[0]["headers"]["Authorization"] == "Bearer D6-REFLECTED-SECRET"
        assert result["error_kind"] == "outbound_request_failed"
        assert "D6-REFLECTED-SECRET" not in json.dumps(result)
        ConnectionLedger(db).revoke_grant("grant-http")
        assert call(service)["error"] == "unknown capability"
        assert len(loop.recorded) == 1
    finally:
        loop.stop()
