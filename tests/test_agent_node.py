"""The agent node: a converse turn as a workflow step (change agent-node-and-tool-grants).

Real run path, admission, work receipt, agent loop, persona assembly and
ENGINE MCP HANDLERS: the in-memory client talks to ``engine_mcp_server.mcp``
itself, so ``write_brain`` / ``read_brain`` / ``write_graph`` run their own
pins, identity binding and owner checks. Only the model's HTTP wire is scripted.
"""

import json
import sqlite3
from dataclasses import replace

import pytest
from fastmcp import Client

from tests import test_workflow_http_agent as foreground
from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tests.inference_usage_helpers import accounting_resolver
from tinyassets import engine_mcp_server, engine_tool_client
from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.providers.api_key_http_provider import ApiKeyHttpProvider
from tinyassets.served_tools import SERVED_ENGINE_MCP_TOOLS
from tinyassets.shared_self import agent_node_key
from tinyassets.shared_self import prepare_shared_self_turn as _REAL_PREPARE
from tinyassets.storage.provider_work_authority import db_path

http_wire = foreground.http_wire
work_agent = foreground.work_agent

pytestmark = pytest.mark.usefixtures("cloud_runtime")

MARK = "I am the agent node, and I remember the orchard ledger."


def _call(name, **arguments):
    return {"name": name, "arguments": json.dumps(arguments)}


def _agent_branch(tools_allowed, *, author="acct_alice"):
    from tests.test_run_provider_session import _branch

    branch = _branch(node_count=1, author=author)
    branch.branch_def_id = "branch_repo_spec_loop"
    node = branch.node_defs[0]
    node.prompt_template = "Keep the orchard ledger in your brain."
    node.llm_policy = {"preferred": {"model": "synthetic-model"}, "fallback_chain": []}
    node.tools_allowed = list(tools_allowed)
    node.output_keys = ["ledger"]
    branch.state_schema = [{"name": "ledger", "type": "str", "default": ""}]
    # Another author's branch must be one the owner can run at all, or the test
    # proves only that it was never found.
    branch.visibility = "private" if author == "acct_alice" else "public"
    return branch


@pytest.fixture
def engine(tmp_path, monkeypatch, work_agent):
    """Real prepare + real engine handlers; a scripted model on the HTTP wire."""
    import tinyassets.api.visibility as visibility
    from tinyassets.daemon_server import ensure_universe_registered
    from tinyassets.universe_bundle import seed_okf_bundle

    udir = tmp_path / "universe_alice"
    udir.mkdir(exist_ok=True)
    seed_okf_bundle(udir, purpose="Tend the orchard ledger.", loop_branch_def_id="")
    ensure_universe_registered(tmp_path, universe_id="universe_alice", universe_path=udir)
    visibility.set_universe_visibility("universe_alice", "private", source="owner")
    monkeypatch.setattr("tinyassets.shared_self.prepare_shared_self_turn", _REAL_PREPARE)
    # The real persona and the whole served tool block do not fit the 32k
    # synthetic catalogue model; a large-context model is what an agent would pick.
    from tests import test_work_model_selection as selection

    small = selection._model
    monkeypatch.setattr(selection, "_model",
                        lambda *a, **k: dict(small(*a, **k), context_length=1_000_000))
    monkeypatch.setattr(engine_mcp_server, "_ACTOR_ID", "acct_alice")
    monkeypatch.setattr(engine_mcp_server, "_GRAPH_ID", "universe_alice")
    state = work_agent
    state.routes, state.offered, state.results, state.script = [], [], [], []
    state.plain = []

    def client(route, timeout):
        state.routes.append((route.actor_id, route.graph_id))
        return Client(engine_mcp_server.mcp)

    class Proxy:
        def close(self):
            pass

        def request(self, verb, document):
            body = document["body"]
            body = json.loads(body) if isinstance(body, str) else body
            state.wires.append(document)
            state.offered.append(sorted(t["function"]["name"] for t in body.get("tools") or []))
            state.results.extend(m.get("content") for m in body.get("messages") or []
                                 if m.get("role") == "tool")
            if not body.get("tools"):
                # An ordinary prompt node: one plain model call, no tools offered.
                state.plain.append(body)
                return {"status": 200, "body": json.dumps({
                    "model": "actual-work-model", "choices": [{
                        "message": {"role": "assistant", "content": "plain answer"},
                        "finish_reason": "stop",
                    }], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": 0},
                })}
            calls = state.script.pop(0) if state.script else []
            message = {"role": "assistant", "content": None if calls else "steward done"}
            if calls:
                message["tool_calls"] = [
                    {"id": f"tool-{index}", "type": "function", "function": call}
                    for index, call in enumerate(calls)
                ]
            return {"status": 200, "body": json.dumps({
                "model": "actual-work-model", "choices": [{
                    "message": message, "finish_reason": "tool_calls" if calls else "stop",
                }], "usage": {"prompt_tokens": 3, "completion_tokens": 4, "cost": 0},
            })}

    monkeypatch.setattr(engine_tool_client, "_make_client", client)
    monkeypatch.setattr(ApiKeyHttpProvider, "_resolve_proxy",
                        accounting_resolver(lambda *a, **k: Proxy()))
    return state


def _run(tmp_path, monkeypatch, authenticate_request, tools_allowed, *, author="acct_alice"):
    """One agent node run through the real run path in Alice's universe.

    These used to launch through the background carrier, which claimed fleet-era
    cloud-automation slices; that path is gone (dark-code deletion plan C2). The
    properties they pin are about the node and its owner, not the launcher.
    """
    from tests.test_run_provider_session import _run_branch
    from tinyassets.auth.middleware import _get_provider
    from tinyassets.provider_assignment_manifest import ModelAccess

    def as_production(subject):
        # The test provider is legacy full-auth (exact named scopes); production
        # is WorkOS resolve-always, where the engine's effect grants authorize.
        authenticate_request(subject)
        provider = _get_provider()
        monkeypatch.setattr(provider, "is_auth_required", lambda: False)
        monkeypatch.setattr(provider, "resolve_always_writes", lambda: True)

    return _run_branch(tmp_path, monkeypatch, as_production,
                       _agent_branch(tools_allowed, author=author),
                       open_provider=True, model_access=ModelAccess("discovered"))[0]


def _branches_authored_in_alice(tmp_path):
    from tinyassets.daemon_server import list_branch_definitions

    return [b for b in list_branch_definitions(tmp_path, author="acct_alice", include_private=True,
                                               viewer="acct_alice")
            if b.get("name") == "Orchard follow-up"]


def test_an_agent_node_uses_its_own_brain_and_graph_on_a_private_universe(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    engine.script = [[
        _call("write_brain", identity=MARK),
        _call("read_brain"),
        _call("write_graph", target="branch", operation="create", payload_json=json.dumps({
            "name": "Orchard follow-up", "entry_point": "note",
            "node_defs": [{"node_id": "note", "display_name": "Note",
                           "prompt_template": "Summarise the orchard ledger."}],
            "edges": [{"from_node": "note", "to_node": "END"}],
        })),
    ]]
    result = _run(tmp_path, monkeypatch, authenticate_request, ["agent"])
    assert result["terminal_status"] == "completed", (result, engine.errors)

    # Every tool reached the route pinned to the run's own owner and universe.
    assert set(engine.routes) == {("acct_alice", "universe_alice")}
    # 1. brain write landed in the universe's own file ...
    assert MARK in (tmp_path / "universe_alice" / "identity.md").read_text(encoding="utf-8")
    # 2. ... and the agent read it back in the same turn (the tool result the model saw).
    assert any(MARK in (content or "") for content in engine.results), engine.results
    # 3. write_graph built a branch owned by this universe's owner.
    assert len(_branches_authored_in_alice(tmp_path)) == 1, engine.results
    # The node's answer is the run's output, and both rounds are metered on the one receipt.
    from tinyassets.runs import get_run

    output = get_run(tmp_path, result["run_id"])["output"]
    assert output.get("ledger") == "steward done", output
    with sqlite3.connect(db_path(tmp_path)) as conn:
        rows = [json.loads(r[0]) for r in conn.execute(
            "SELECT record_json FROM provider_invocation_reservations ORDER BY ordinal")]
    assert len(rows) == 2 and all(row["state"] == "succeeded" for row in rows)
    assert sum(row["actual_total_tokens"] for row in rows) == 14
    # The marker alone grants what the owner's chat has.
    assert engine.offered[0] == sorted(SERVED_ENGINE_MCP_TOOLS)


def test_a_narrowed_grant_offers_and_allows_only_the_granted_tools(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    engine.script = [[_call("read_brain"), _call("write_brain", identity=MARK)]]
    result = _run(tmp_path, monkeypatch, authenticate_request, ["agent", "read_brain"])

    assert engine.offered[0] == ["read_brain"]
    # The ungranted write never reached the handler: the brain is unchanged, and the
    # turn stops rather than completing with an effect it was not given.
    assert MARK not in (tmp_path / "universe_alice" / "identity.md").read_text(encoding="utf-8")
    assert result["terminal_status"] != "completed", result


def test_an_unknown_grant_refuses_before_any_model_round(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    result = _run(tmp_path, monkeypatch, authenticate_request, ["agent", "write_brian"])
    assert result["terminal_status"] != "completed", result
    assert engine.wires == [] and engine.routes == []


def test_another_authors_branch_never_drives_the_owners_tools(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    """A prompt written by another user must not steer the owner's agent tools, even
    in a run the owner's universe admitted."""
    engine.script = [[_call("write_brain", identity=MARK)]]
    result = _run(tmp_path, monkeypatch, authenticate_request, ["agent"], author="acct_bob")
    assert result["terminal_status"] != "completed", result
    assert engine.wires == [] and engine.routes == []
    assert MARK not in (tmp_path / "universe_alice" / "identity.md").read_text(encoding="utf-8")


def test_the_tools_cannot_be_pointed_at_another_users_universe(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    """No served tool takes a universe; an injected id is ignored and the pinned one
    is used. Bob's private brain stays untouched and unread."""
    from tinyassets.universe_bundle import seed_okf_bundle

    bob = tmp_path / "universe_bob"
    seed_okf_bundle(bob, purpose="Bob's private work.", loop_branch_def_id="")
    before = (bob / "identity.md").read_text(encoding="utf-8")
    engine.script = [[
        _call("write_brain", identity=MARK, universe_id="universe_bob", graph_id="universe_bob"),
        _call("read_graph", target="status", graph_id="universe_bob"),
    ]]
    _run(tmp_path, monkeypatch, authenticate_request, ["agent"])
    assert set(engine.routes) == {("acct_alice", "universe_alice")}
    assert (bob / "identity.md").read_text(encoding="utf-8") == before
    # Unknown parameters are refused by the tool schema; nothing of Bob's comes back.
    assert all("Bob's private work" not in (content or "") for content in engine.results)


def test_a_workflow_mixes_plain_steps_and_differently_granted_agents(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    """Foreground: any number of agent nodes beside an ordinary prompt node, each
    with its own grant. The ordinary node stays one plain call."""
    from tests.test_run_provider_session import _branch, _run_branch
    from tinyassets.provider_assignment_manifest import ModelAccess

    branch = _branch(node_count=3)
    policy = {"preferred": {"model": "synthetic-model"}, "fallback_chain": []}
    for node in branch.node_defs:
        node.llm_policy = policy
    branch.node_defs[1].tools_allowed = ["agent", "read_brain"]
    branch.node_defs[2].tools_allowed = ["agent"]
    engine.script = [[_call("read_brain")], [], [_call("write_brain", identity=MARK)], []]
    result = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                         open_provider=True, model_access=ModelAccess("discovered"))[0]

    assert result["terminal_status"] == "completed", (result, engine.errors)
    assert len(engine.plain) == 1
    assert [o for o in engine.offered if o] == [["read_brain"], ["read_brain"],
                              sorted(SERVED_ENGINE_MCP_TOOLS), sorted(SERVED_ENGINE_MCP_TOOLS)]
    assert MARK in (tmp_path / "universe_alice" / "identity.md").read_text(encoding="utf-8")


def test_an_invoked_child_never_borrows_the_parents_agent_grant(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    """A blocking invoke_branch child runs on its parent's run session. Bob's
    public child names its agent node after Alice's fully granted one; it must
    not get Alice's grant, brain or tools."""
    from tests.test_run_provider_session import _branch, _run_branch
    from tinyassets.daemon_server import save_branch_definition
    from tinyassets.provider_assignment_manifest import ModelAccess

    policy = {"preferred": {"model": "synthetic-model"}, "fallback_chain": []}
    child = BranchDefinition(
        branch_def_id="branch_bob_public", name="Bob's public step", author="acct_bob",
        visibility="public",
        graph_nodes=[GraphNodeRef(id="n1", node_def_id="n1")],
        edges=[EdgeDefinition(from_node="n1", to_node="END")], entry_point="n1",
        node_defs=[NodeDefinition(node_id="n1", display_name="Writer 1",
                                  prompt_template="Write my mark into your brain.",
                                  output_keys=["child_out"], tools_allowed=["agent"])],
        state_schema=[{"name": "child_out", "type": "str"}],
    )
    save_branch_definition(tmp_path, branch_def=child.to_dict())
    branch = _branch(node_count=2)
    for node in branch.node_defs:
        node.llm_policy = dict(policy)
    branch.node_defs[0].tools_allowed = ["agent"]
    invoker = branch.node_defs[1]
    invoker.prompt_template, invoker.llm_policy = "", None
    invoker.invoke_branch_spec = {"branch_def_id": "branch_bob_public", "inputs_mapping": {},
                                  "output_mapping": {"answer_2": "child_out"},
                                  "wait_mode": "blocking"}
    engine.script = [[], [_call("write_brain", identity=MARK)], []]
    result = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                         open_provider=True, model_access=ModelAccess("discovered"))[0]
    assert MARK not in (tmp_path / "universe_alice" / "identity.md").read_text(encoding="utf-8")
    # Alice's own agent node ran once; the child never got a tool-bearing round.
    assert [o for o in engine.offered if o] == [sorted(SERVED_ENGINE_MCP_TOOLS)], result
    assert result["terminal_status"] != "completed", result
    # Refused for the binding, not because the child never compiled or ran.
    from tinyassets.runs import get_run

    child_run_id = result["terminal_error"].split("child run_id=")[1].rstrip(")")
    assert "agent_node_not_in_admitted_branch" in str(get_run(tmp_path, child_run_id)["error"])


@pytest.mark.parametrize("tools_allowed,agent", [([], False), (["agent"], True),
                                                 (["universe_self", "read_brain"], True)])
def test_the_compiler_names_only_agent_nodes_and_gives_them_the_turn_backstop(
    tools_allowed, agent,
):
    from tinyassets.graph_compiler import _build_prompt_template_node
    from tinyassets.universe_intelligence import (
        UNBOUNDED_TURN_SECONDS,
        served_absolute_cap_s,
    )

    seen = []

    def provider(prompt, system, *, role, config=None, **kwargs):
        seen.append(config)
        return "ok"

    node = NodeDefinition(node_id="step", display_name="Step", prompt_template="Go.",
                          tools_allowed=tools_allowed, timeout_seconds=300.0)
    _build_prompt_template_node(node, provider_call=provider, event_sink=None,
                                branch_def_id="b")({})
    (config,) = seen
    assert config.agent_node_id == ("step" if agent else "")
    # The key binds the call to this branch's node, which the session re-derives.
    assert config.agent_node_key == (agent_node_key("b", node.to_dict()) if agent else "")
    # An agent node IS the converse turn, so it gets the turn's backstop: the
    # universe's own cap if it set one, else the unreachable number that stands
    # for "no wall clock" (founder, 2026-09-30: a turn runs until it is
    # finished). A non-agent node keeps its declared timeout.
    assert served_absolute_cap_s(None) is None, "no platform turn cap"
    assert config.absolute_cap_s == pytest.approx(
        UNBOUNDED_TURN_SECONDS if agent else 300.0, rel=0.01,
    )


def test_codex_native_turn_enables_only_the_grant(tmp_path, monkeypatch):
    from tests.engine_authority_helpers import seed_engine_authority
    from tinyassets.providers.base import ModelConfig
    from tinyassets.providers.codex_provider import _codex_engine_mcp_args

    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_engine_authority(tmp_path)
    (tmp_path / ".engine_mcp_http_routes.json").write_text(json.dumps({"u-a": {
        "version": 1, "actor_id": "actor-a", "url": "http://127.0.0.1:8790/mcp",
        "port": 8790, "secret": "s" * 43,
    }}), encoding="utf-8")
    config = ModelConfig(engine_mcp_enabled=True, engine_mcp_actor_id="actor-a",
                         engine_mcp_graph_id="u-a")

    def enabled(cfg):
        (server,) = [a for a in _codex_engine_mcp_args(cfg, {}) if "mcp_servers." in a]
        return server.split("enabled_tools=[", 1)[1].split("]", 1)[0]

    assert enabled(config) == ",".join(f'"{t}"' for t in SERVED_ENGINE_MCP_TOOLS)
    narrowed = replace(config, engine_tool_grant=("read_brain", "write_graph"))
    assert enabled(narrowed) == '"write_graph","read_brain"'


def test_claude_native_turn_denies_what_the_grant_withholds():
    from tinyassets.providers.base import ModelConfig
    from tinyassets.shared_self import _granted_config
    from tinyassets.universe_intelligence import _ENGINE_MCP_ALLOWED

    base = ModelConfig(allowed_tools=("WebFetch",) + _ENGINE_MCP_ALLOWED,
                       disallowed_tools=("Bash",))
    assert _granted_config(base, {"tools_allowed": ["agent"]}) is base
    narrowed = _granted_config(base, {"tools_allowed": ["agent", "read_brain"]})
    assert narrowed.engine_tool_grant == ("read_brain",)
    assert narrowed.allowed_tools == ("WebFetch", "mcp__tinyassets__read_brain")
    assert "mcp__tinyassets__write_brain" in narrowed.disallowed_tools
    assert "Bash" in narrowed.disallowed_tools
    assert "mcp__tinyassets__read_brain" not in narrowed.disallowed_tools


# --------------------------------------------------------------------------- #
# Slice 2: a code node reaches its granted served tools, pinned the same way
# --------------------------------------------------------------------------- #
@pytest.fixture
def code_node_engine(tmp_path, monkeypatch):
    """Real route authority and real engine handlers for universe u-a / actor-a."""
    from tests.engine_authority_helpers import seed_engine_authority
    from tinyassets.universe_bundle import seed_okf_bundle

    monkeypatch.setenv("TINYASSETS_ENGINE_MCP_TOOLS", "1")
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))
    seed_okf_bundle(tmp_path / "u-a", purpose="Actor A's universe.", loop_branch_def_id="")
    seed_engine_authority(tmp_path)
    (tmp_path / ".engine_mcp_http_routes.json").write_text(json.dumps({"u-a": {
        "version": 1, "actor_id": "actor-a", "url": "http://127.0.0.1:8790/mcp",
        "port": 8790, "secret": "s" * 43,
    }}), encoding="utf-8")
    monkeypatch.setattr(engine_mcp_server, "_ACTOR_ID", "actor-a")
    monkeypatch.setattr(engine_mcp_server, "_GRAPH_ID", "u-a")
    routes = []

    def client(route, timeout):
        routes.append((route.actor_id, route.graph_id))
        return Client(engine_mcp_server.mcp)

    monkeypatch.setattr(engine_tool_client, "_make_client", client)
    return routes


def _invoker(tools_allowed, **context):
    from tinyassets.graph_compiler import BranchExecutionContext, _build_node_mcp_invoker

    ctx = BranchExecutionContext(**{
        "actor": "universe:u-a", "universe_id": "u-a", "caller_provenance": "own",
        "owner_user_id": "actor-a", "definition_author": "actor-a", **context,
    })
    node = NodeDefinition(node_id="code", display_name="Code",
                          source_code="def run(state): return {}",
                          tools_allowed=list(tools_allowed))
    return _build_node_mcp_invoker(node, event_sink=None, execution_context=ctx)


def test_a_code_node_calls_every_served_tool_by_default(tmp_path, code_node_engine):
    invoke = _invoker([])
    wrote = invoke("write_brain", identity=MARK)
    assert wrote["is_error"] is False and wrote["data"]["ok"] is True, wrote
    read = invoke("read_brain")
    assert MARK in read["data"]["brain"]["identity"]
    assert set(code_node_engine) == {("actor-a", "u-a")}


def test_a_code_node_grant_narrows_its_served_tools(tmp_path, code_node_engine):
    from tinyassets.graph_compiler import CompilerError

    invoke = _invoker(["read_run_file", "read_brain"])
    assert "identity" in invoke("read_brain")["data"]["brain"]
    with pytest.raises(CompilerError, match="not granted the served tool 'write_brain'"):
        invoke("write_brain", identity=MARK)
    assert MARK not in (tmp_path / "u-a" / "identity.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("context", [
    {"caller_provenance": "public-foreign"},
    {"definition_author": "actor-b"},
    {"owner_user_id": ""},
    # Another user's universe: the engine route/authority is actor-a's, never theirs.
    {"owner_user_id": "actor-b", "definition_author": "actor-b"},
    {"universe_id": "u-b"},
])
def test_a_code_node_never_reaches_served_tools_outside_its_owners_own_run(
    tmp_path, code_node_engine, context,
):
    from tinyassets.graph_compiler import CompilerError

    with pytest.raises(CompilerError):
        _invoker([], **context)("write_brain", identity=MARK)
    assert code_node_engine == []
    assert MARK not in (tmp_path / "u-a" / "identity.md").read_text(encoding="utf-8")


# --------------------------------------------------------------------------- #
# What an agent node persists stays bound to its universe (#4060 review)
# --------------------------------------------------------------------------- #
def _reads_as(authenticate_request, who, run_id):
    from tinyassets.api import runs as api_runs

    authenticate_request(who)
    return "\n".join([
        api_runs._action_get_run({"run_id": run_id}),
        api_runs._action_get_run_output({"run_id": run_id}),
        api_runs._action_list_runs({}),
        api_runs._action_query_runs({"branch_def_id": "branch_repo_spec_loop",
                                     "select": ["ledger"]}),
    ])


def _second_user_is_refused(tmp_path, monkeypatch, authenticate_request, run_id, secret):
    from tinyassets.api import runs as api_runs
    from tinyassets.daemon_server import set_founder_home

    monkeypatch.setattr(api_runs, "_base_path", lambda: tmp_path)
    monkeypatch.setattr(api_runs, "_ensure_runs_recovery", lambda: None)
    (tmp_path / "universe_bob").mkdir(exist_ok=True)
    set_founder_home(tmp_path, founder_sub="acct_bob", universe_id="universe_bob",
                     platform_generated=True)
    seen = _reads_as(authenticate_request, "acct_bob", run_id)
    assert secret not in seen
    assert run_id not in seen
    # The owner still reads it, so the refusal is about who asks, not a broken read.
    assert secret in _reads_as(authenticate_request, "acct_alice", run_id)


def test_a_foreground_agent_nodes_saved_output_is_refused_to_another_user(
    tmp_path, monkeypatch, authenticate_request, engine,
):
    from tests.test_run_provider_session import _branch, _run_branch
    from tinyassets.provider_assignment_manifest import ModelAccess

    branch = _branch(node_count=1)
    branch.branch_def_id = "branch_repo_spec_loop"
    branch.node_defs[0].llm_policy = {"preferred": {"model": "synthetic-model"},
                                      "fallback_chain": []}
    branch.node_defs[0].tools_allowed = ["agent"]
    branch.node_defs[0].output_keys = ["ledger"]
    branch.state_schema = [{"name": "ledger", "type": "str", "default": ""}]
    engine.script = [[_call("read_brain")]]
    result = _run_branch(tmp_path, monkeypatch, authenticate_request, branch,
                         open_provider=True, model_access=ModelAccess("discovered"))[0]
    assert result["terminal_status"] == "completed", (result, engine.errors)
    _second_user_is_refused(tmp_path, monkeypatch, authenticate_request,
                            result["run_id"], "steward done")
