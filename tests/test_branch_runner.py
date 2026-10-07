"""Branch graph runner tests.

Covers the compiler, the runs persistence layer, the 6 MCP actions on the
``extensions`` tool, and the acceptance criteria from the spec.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest

from tests.cloud_runtime_fixture import cloud_runtime  # noqa: F401
from tinyassets.branches import (
    BranchDefinition,
    EdgeDefinition,
    GraphNodeRef,
    NodeDefinition,
)

#: The universe every run in this file is attributed to. Runs are owned by a
#: universe, not by a bare actor, so this is registered + ACL-granted in the
#: fixture and threaded through every `run_branch` call below.
RUNNER_UNIVERSE = "runner-universe"


pytestmark = pytest.mark.usefixtures("cloud_runtime")


@pytest.fixture
def runner_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    # Branch mutation requires a credential-derived subject. Without one the
    # extensions surface returns
    # `{"error": "Authenticated branch subject required."}` and these tests
    # die before reaching their own concern. The conftest default grants
    # extensions read/write/admin; this file additionally needs
    # `extensions.costly`, which gates `run_branch` — the action most of
    # these tests exist to exercise. Nothing here asserts a costly refusal,
    # so granting it costs no assertion strength.
    authenticate_request("tester", capabilities=[
        "tinyassets.extensions.read",
        "tinyassets.extensions.write",
        "tinyassets.extensions.admin",
        "tinyassets.extensions.costly",
    ])

    # Branches are run BY universes now, so a run needs a registered universe
    # the actor may write. Without a `universe_id` run_branch returns
    # `branch_run_requires_universe`; with an unregistered one it returns
    # `universe_access_denied` — the ACL grant is the part that is easy to
    # miss, since registration alone still fails.
    from tinyassets.daemon_server import (
        ensure_universe_registered,
        grant_universe_access,
        set_founder_home,
    )

    udir = base / RUNNER_UNIVERSE
    udir.mkdir(parents=True, exist_ok=True)
    ensure_universe_registered(
        base, universe_id=RUNNER_UNIVERSE, universe_path=udir,
    )
    grant_universe_access(
        base,
        universe_id=RUNNER_UNIVERSE,
        actor_id="tester",
        permission="write",
        granted_by="runner_env",
    )
    set_founder_home(
        base,
        founder_sub="tester",
        universe_id=RUNNER_UNIVERSE,
        platform_generated=True,
    )

    from tinyassets import universe_server as us
    provider_calls = importlib.import_module("tinyassets.providers.call")
    from tests.test_run_provider_session import (
        _CountingProvider,
        _seed_serving_assignment,
    )
    from tinyassets.providers.router import ProviderRouter

    importlib.reload(us)
    _seed_serving_assignment(
        base,
        owner_user_id="tester",
        universe_id=RUNNER_UNIVERSE,
    )
    provider = _CountingProvider()
    provider_router = ProviderRouter({provider.name: provider})

    def governed_provider_call(
        prompt,
        system="",
        *,
        role="writer",
        config=None,
        universe_context=None,
        operation=None,
        **_kwargs,
    ):
        return provider_router.call_sync(
            role,
            prompt,
            system,
            config,
            universe_context=universe_context,
            operation=operation,
        ).text

    monkeypatch.setattr(provider_calls, "call_provider", governed_provider_call)
    yield us, base
    importlib.reload(us)



def _call(us, action, **kwargs):
    return json.loads(us._extensions_impl(action=action, **kwargs))


def _run_and_wait(us, *, timeout: float = 30.0, **kwargs):
    """Kick off `run_branch` and block on the worker.

    Returns the initial response dict with ``status`` and ``output``
    populated from the terminal state after the worker completes. Tests
    written against the sync-v1 contract use this as a drop-in.
    """
    kwargs.setdefault("universe_id", RUNNER_UNIVERSE)
    result = _call(us, "run_branch", **kwargs)
    if "run_id" not in result:
        return result
    from tinyassets.runs import wait_for

    wait_for(result["run_id"], timeout=timeout)
    snapshot = _call(us, "get_run", run_id=result["run_id"])
    final = dict(result)
    final["status"] = snapshot.get("status", result.get("status"))
    output_result = _call(us, "get_run_output", run_id=result["run_id"])
    final["output"] = output_result.get("output", {})
    return final


def _build_recipe_branch(us) -> str:
    """Build the recipe-tracker branch from the Phase 2 vignette."""
    steps = (
        ("capture", "Capture recipe", "Extract recipe: {raw_recipe}"),
        ("categorize", "Categorize", "Classify: {capture_output}"),
        ("archive", "Archive", "Archive: {categorize_output}"),
    )
    spec = {
        "name": "Recipe tracker",
        "entry_point": "capture",
        "node_defs": [
            {"node_id": nid, "display_name": display, "prompt_template": tmpl,
             "output_keys": [f"{nid}_output"]}
            for nid, display, tmpl in steps
        ],
        "edges": [
            {"from": "START", "to": "capture"},
            {"from": "capture", "to": "categorize"},
            {"from": "categorize", "to": "archive"},
            {"from": "archive", "to": "END"},
        ],
        "state_schema": [
            {"name": field, "type": "str"}
            for field in ("raw_recipe", "capture_output",
                          "categorize_output", "archive_output")
        ],
    }
    built = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert built["status"] == "built", built
    return built["branch_def_id"]


# ─────────────────────────────────────────────────────────────────────────────
# Compiler unit tests
# ─────────────────────────────────────────────────────────────────────────────


def test_compiler_rejects_invalid_branch(tmp_path):
    from tinyassets.graph_compiler import CompilerError, compile_branch

    b = BranchDefinition(name="")  # no name, no nodes
    with pytest.raises(CompilerError):
        compile_branch(b)


def test_compiler_runs_owner_code_unapproved_and_refuses_foreign_code():
    """Design D2 (change `sandboxed-code-node`): the OS sandbox bounds what
    code can touch; AUTHORSHIP decides whose code runs. An owner-authored
    node needs no approval; a public foreign branch run directly refuses."""
    from tinyassets.graph_compiler import (
        BranchExecutionContext,
        ForeignCodeError,
        compile_branch,
    )

    b = BranchDefinition(name="test", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only", display_name="Only",
        source_code="def run(state): return {}",
        approved=False,
    )]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    compile_branch(b)  # own (no context = the caller's own compile)
    compile_branch(b, execution_context=BranchExecutionContext(
        actor="me", universe_id="u", caller_provenance="own"))
    with pytest.raises(ForeignCodeError, match="remix"):
        compile_branch(b, execution_context=BranchExecutionContext(
            actor="victim", universe_id="u", caller_provenance="public-foreign"))


def test_compiler_accepts_approved_source_code():
    from langgraph.checkpoint.memory import InMemorySaver

    from tinyassets.graph_compiler import compile_branch

    b = BranchDefinition(name="test", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only", display_name="Only",
        source_code="def run(state): return {'out': state.get('x', 0) + 1}",
        input_keys=["x"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    b.state_schema = [
        {"name": "x", "type": "int"},
        {"name": "out", "type": "int"},
    ]
    compiled = compile_branch(b)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    result = app.invoke({"x": 5}, config={"configurable": {"thread_id": "t1"}})
    assert result["out"] == 6


def test_source_code_node_can_call_allowed_mcp_action(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    import tinyassets.api.market as market
    from tinyassets.graph_compiler import compile_branch

    calls: list[tuple[str, dict]] = []

    def fake_goals(*, action: str, **kwargs):
        calls.append((action, kwargs))
        return json.dumps({
            "status": "ok",
            "entries": [{"branch_def_id": "b1"}],
        })

    monkeypatch.setattr(market, "goals", fake_goals)

    b = BranchDefinition(name="test", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only",
        display_name="Only",
        source_code=(
            "def run(state):\n"
            "    board = invoke_mcp_action("
            "'goals.leaderboard', goal_id=state['goal_id'], metric='run_count')\n"
            "    return {'leaderboard_count': len(board['entries'])}\n"
        ),
        input_keys=["goal_id"],
        output_keys=["leaderboard_count"],
        tools_allowed=["goals.leaderboard"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    b.state_schema = [
        {"name": "goal_id", "type": "str"},
        {"name": "leaderboard_count", "type": "int"},
    ]
    compiled = compile_branch(b)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    result = app.invoke(
        {"goal_id": "g1"}, config={"configurable": {"thread_id": "mcp-ok"}},
    )

    assert result["leaderboard_count"] == 1
    assert calls == [("leaderboard", {"goal_id": "g1", "metric": "run_count"})]


def test_source_code_node_rejects_mcp_action_not_in_tools_allowed(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    import tinyassets.api.market as market
    from tinyassets.graph_compiler import CompilerError, compile_branch

    def fake_goals(*, action: str, **kwargs):
        raise AssertionError("disallowed MCP action should not dispatch")

    monkeypatch.setattr(market, "goals", fake_goals)

    b = BranchDefinition(name="test", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only",
        display_name="Only",
        source_code=(
            "def run(state):\n"
            "    invoke_mcp_action('goals.leaderboard', goal_id='g1')\n"
            "    return {'out': 'unreachable'}\n"
        ),
        output_keys=["out"],
        tools_allowed=[],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    b.state_schema = [{"name": "out", "type": "str"}]
    compiled = compile_branch(b)
    app = compiled.graph.compile(checkpointer=InMemorySaver())

    with pytest.raises(CompilerError) as exc_info:
        app.invoke({}, config={"configurable": {"thread_id": "mcp-denied"}})
    assert "not allowed" in str(exc_info.value)


def test_source_code_node_can_call_wiki_read(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    import tinyassets.api.wiki as wiki_mod
    from tinyassets.graph_compiler import compile_branch

    calls: list[tuple[str, dict]] = []

    def fake_wiki(*, action: str, **kwargs):
        calls.append((action, kwargs))
        return json.dumps({"status": "ok", "results": [{"page": "BUG-001"}]})

    monkeypatch.setattr(wiki_mod, "wiki", fake_wiki)

    b = BranchDefinition(name="test", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only",
        display_name="Only",
        source_code=(
            "def run(state):\n"
            "    hits = invoke_mcp_action('wiki.search', query=state['q'])\n"
            "    return {'n': len(hits['results'])}\n"
        ),
        input_keys=["q"],
        output_keys=["n"],
        tools_allowed=["wiki.search"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    b.state_schema = [
        {"name": "q", "type": "str"},
        {"name": "n", "type": "int"},
    ]
    compiled = compile_branch(b)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    result = app.invoke(
        {"q": "backlog"}, config={"configurable": {"thread_id": "wiki-ok"}},
    )

    assert result["n"] == 1
    assert calls == [("search", {"query": "backlog"})]


def test_source_code_node_cannot_call_wiki_write(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    import tinyassets.api.wiki as wiki_mod
    from tinyassets.graph_compiler import CompilerError, compile_branch

    def fake_wiki(*, action: str, **kwargs):
        raise AssertionError("wiki write must not dispatch from a node")

    monkeypatch.setattr(wiki_mod, "wiki", fake_wiki)

    b = BranchDefinition(name="test", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only",
        display_name="Only",
        source_code=(
            "def run(state):\n"
            "    invoke_mcp_action('wiki.write', page='X', content='Y')\n"
            "    return {'out': 'unreachable'}\n"
        ),
        output_keys=["out"],
        tools_allowed=["wiki.write"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    b.state_schema = [{"name": "out", "type": "str"}]
    compiled = compile_branch(b)
    app = compiled.graph.compile(checkpointer=InMemorySaver())

    # 'wiki.write' is not in the alias allow-list at all — unsupported action.
    with pytest.raises(CompilerError) as exc_info:
        app.invoke({}, config={"configurable": {"thread_id": "wiki-write"}})
    assert "unsupported MCP action" in str(exc_info.value)


def test_node_wiki_dispatch_blocks_write_even_if_aliased(monkeypatch):
    from langgraph.checkpoint.memory import InMemorySaver

    import tinyassets.api.wiki as wiki_mod
    import tinyassets.graph_compiler as gc
    from tinyassets.graph_compiler import CompilerError, compile_branch

    def fake_wiki(*, action: str, **kwargs):
        raise AssertionError("wiki write must not dispatch from a node")

    monkeypatch.setattr(wiki_mod, "wiki", fake_wiki)
    # Simulate a future mistake: a write action wired into the alias map.
    # The dispatch-time WIKI_WRITE_ACTIONS guard must still refuse it.
    monkeypatch.setitem(
        gc._NODE_MCP_ACTION_ALIASES, "wiki.write", ("wiki", "write"),
    )

    b = BranchDefinition(name="test", entry_point="only")
    b.node_defs = [NodeDefinition(
        node_id="only",
        display_name="Only",
        source_code=(
            "def run(state):\n"
            "    invoke_mcp_action('wiki.write', page='X', content='Y')\n"
            "    return {'out': 'unreachable'}\n"
        ),
        output_keys=["out"],
        tools_allowed=["wiki.write"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="only"),
        EdgeDefinition(from_node="only", to_node="END"),
    ]
    b.state_schema = [{"name": "out", "type": "str"}]
    compiled = compile_branch(b)
    app = compiled.graph.compile(checkpointer=InMemorySaver())

    with pytest.raises(CompilerError) as exc_info:
        app.invoke({}, config={"configurable": {"thread_id": "wiki-dd"}})
    assert "write" in str(exc_info.value)


def test_branch_spec_preserves_tools_allowed():
    from tinyassets.api.branches import _apply_node_spec

    branch = BranchDefinition(name="test")
    err = _apply_node_spec(branch, {
        "node_id": "only",
        "display_name": "Only",
        "source_code": "def run(state): return {}",
        "tools_allowed": ["goals.leaderboard"],
    })

    assert err == ""
    assert branch.node_defs[0].tools_allowed == ["goals.leaderboard"]


def test_compiler_synthesized_typeddict_reducer_append():
    """state_schema with reducer=append should accumulate across nodes."""
    from langgraph.checkpoint.memory import InMemorySaver

    from tinyassets.graph_compiler import compile_branch

    b = BranchDefinition(name="accumulator", entry_point="a")
    b.node_defs = [
        NodeDefinition(
            node_id="a", display_name="A",
            source_code="def run(state): return {'log': ['from-a']}",
        ).mark_approved(),
        NodeDefinition(
            node_id="b", display_name="B",
            source_code="def run(state): return {'log': ['from-b']}",
        ).mark_approved(),
    ]
    b.graph_nodes = [
        GraphNodeRef(id="a", node_def_id="a", position=0),
        GraphNodeRef(id="b", node_def_id="b", position=1),
    ]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="a"),
        EdgeDefinition(from_node="a", to_node="b"),
        EdgeDefinition(from_node="b", to_node="END"),
    ]
    b.state_schema = [
        {"name": "log", "type": "list", "reducer": "append"},
    ]
    compiled = compile_branch(b)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    result = app.invoke(
        {"log": ["start"]}, config={"configurable": {"thread_id": "acc1"}},
    )
    assert result["log"] == ["start", "from-a", "from-b"]


# ─────────────────────────────────────────────────────────────────────────────
# Prompt-template substitution (bug #44)
# ─────────────────────────────────────────────────────────────────────────────


def _single_node_branch(template: str, output_key: str = "out") -> BranchDefinition:
    b = BranchDefinition(name="T", entry_point="write")
    b.node_defs = [NodeDefinition(
        node_id="write", display_name="Write",
        prompt_template=template, output_keys=[output_key],
    )]
    b.graph_nodes = [GraphNodeRef(id="write", node_def_id="write")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="write"),
        EdgeDefinition(from_node="write", to_node="END"),
    ]
    b.state_schema = [
        {"name": "topic", "type": "str"},
        {"name": "style", "type": "str"},
        {"name": output_key, "type": "str"},
    ]
    return b


def _run_and_capture(branch, inputs):
    from langgraph.checkpoint.memory import InMemorySaver

    from tinyassets.graph_compiler import compile_branch

    captured: list[str] = []

    def fake_provider(prompt, system="", *, role="writer", fallback_response=None):
        captured.append(prompt)
        return "[mock]"

    compiled = compile_branch(branch, provider_call=fake_provider)
    app = compiled.graph.compile(checkpointer=InMemorySaver())
    app.invoke(inputs, config={"configurable": {"thread_id": "t"}})
    return captured


def test_prompt_template_single_brace_substitutes():
    """Python-style {var} placeholder should be filled from state."""
    branch = _single_node_branch("Write about {topic}")
    captured = _run_and_capture(branch, {"topic": "scaling laws"})
    assert captured == ["Write about scaling laws"]


def test_prompt_template_double_brace_substitutes():
    """Jinja-style {{var}} placeholder should also be filled (bug #44)."""
    branch = _single_node_branch("Write about {{topic}}")
    captured = _run_and_capture(branch, {"topic": "scaling laws"})
    assert captured == ["Write about scaling laws"]
    # Critical: the literal `{topic}` must NOT leak into the LLM prompt.
    assert "{topic}" not in captured[0]


def test_prompt_template_double_brace_with_whitespace():
    """{{ topic }} with spaces inside should still substitute."""
    branch = _single_node_branch("Write about {{ topic }}")
    captured = _run_and_capture(branch, {"topic": "scaling laws"})
    assert captured == ["Write about scaling laws"]


def test_prompt_template_mixed_single_and_double():
    """Mixed {var} and {{var}} in one template both substitute."""
    branch = _single_node_branch("Write {{topic}} in {style}")
    captured = _run_and_capture(
        branch, {"topic": "scaling laws", "style": "academic"},
    )
    assert captured == ["Write scaling laws in academic"]


def test_prompt_template_multiple_double_brace_occurrences():
    branch = _single_node_branch(
        "Topic: {{topic}}. Also: {{topic}}. Style: {{style}}."
    )
    captured = _run_and_capture(
        branch, {"topic": "X", "style": "Y"},
    )
    assert captured == ["Topic: X. Also: X. Style: Y."]


def test_prompt_template_missing_key_raises():
    """Referencing a state key that's not defined is caught at
    compile time by BranchDefinition.validate() — the build-time
    layer added in the literal-brace spec. Runtime raise remains as
    the second layer if the validator is bypassed."""
    from tinyassets.graph_compiler import CompilerError, compile_branch

    branch = _single_node_branch("Write about {missing}")
    branch.state_schema = [{"name": "out", "type": "str"}]  # no 'missing'
    with pytest.raises(CompilerError) as exc_info:
        compile_branch(branch, provider_call=lambda *a, **kw: "[mock]")
    assert "missing" in str(exc_info.value).lower()


def test_prompt_template_missing_key_detected_for_double_brace():
    """Bug #44 cousin: ``{{missing}}`` is normalized to ``{missing}``
    before the build-time validator checks declaration, so clients
    can't silently leak Jinja-form placeholders into the LLM."""
    from tinyassets.graph_compiler import CompilerError, compile_branch

    branch = _single_node_branch("Write about {{missing}}")
    branch.state_schema = [{"name": "out", "type": "str"}]
    with pytest.raises(CompilerError) as exc_info:
        compile_branch(branch, provider_call=lambda *a, **kw: "[mock]")
    assert "missing" in str(exc_info.value).lower()


def test_normalize_placeholders_helper():
    """Direct unit test on the helper."""
    from tinyassets.graph_compiler import _normalize_placeholders

    assert _normalize_placeholders("{x}") == "{x}"
    assert _normalize_placeholders("{{x}}") == "{x}"
    assert _normalize_placeholders("{{ x }}") == "{x}"
    assert _normalize_placeholders("a{{x}}b{{y}}c") == "a{x}b{y}c"
    assert _normalize_placeholders("") == ""


# ─────────────────────────────────────────────────────────────────────────────
# Runs persistence
# ─────────────────────────────────────────────────────────────────────────────


# Every run has a principal, and code runs in the universe that authored it,
# so a branch under test names the same actor twice: as author and as runner.
ACTOR = "universe:u-test"


def test_execute_branch_end_to_end(tmp_path):
    from tinyassets.runs import execute_branch, get_run, list_events

    b = BranchDefinition(name="test", entry_point="n1", author=ACTOR)
    b.node_defs = [NodeDefinition(
        node_id="n1", display_name="N1",
        source_code="def run(state): return {'out': state.get('x', 0) * 2}",
        input_keys=["x"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="n1", node_def_id="n1")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="n1"),
        EdgeDefinition(from_node="n1", to_node="END"),
    ]
    b.state_schema = [
        {"name": "x", "type": "int"}, {"name": "out", "type": "int"},
    ]

    outcome = execute_branch(tmp_path, branch=b, inputs={"x": 21}, actor="universe:u-test")
    assert outcome.status == "completed", getattr(outcome, "error", outcome)
    assert outcome.output["out"] == 42

    record = get_run(tmp_path, outcome.run_id)
    assert record["status"] == "completed"
    events = list_events(tmp_path, outcome.run_id)
    assert any(e["status"] == "ran" for e in events)


def test_execute_branch_records_executor_identity_without_changing_actor(tmp_path):
    from tinyassets.runs import execute_branch, get_run

    b = BranchDefinition(name="test", entry_point="n1")
    b.node_defs = [NodeDefinition(
        node_id="n1", display_name="N1", approved=True,
        source_code="def run(state): return {'out': state.get('x', 0) + 1}",
        input_keys=["x"],
    )]
    b.graph_nodes = [GraphNodeRef(id="n1", node_def_id="n1")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="n1"),
        EdgeDefinition(from_node="n1", to_node="END"),
    ]
    b.state_schema = [
        {"name": "x", "type": "int"}, {"name": "out", "type": "int"},
    ]

    outcome = execute_branch(
        tmp_path,
        branch=b,
        inputs={"x": 1},
        actor="requester-user",
        daemon_id="daemon::owner",
        runtime_instance_id="runtime-123",
        worker_id="codex-1",
    )

    record = get_run(tmp_path, outcome.run_id)
    assert record is not None
    assert record["actor"] == "requester-user"
    assert record["daemon_id"] == "daemon::owner"
    assert record["runtime_instance_id"] == "runtime-123"
    assert record["worker_id"] == "codex-1"


def test_execute_branch_reports_node_status_callback(tmp_path):
    from tinyassets.runs import (
        NODE_STATUS_RAN,
        NODE_STATUS_RUNNING,
        execute_branch,
    )

    b = BranchDefinition(name="test", entry_point="n1", author=ACTOR)
    b.node_defs = [NodeDefinition(
        node_id="n1", display_name="N1",
        source_code="def run(state): return {'out': state.get('x', 0) * 2}",
        input_keys=["x"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="n1", node_def_id="n1")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="n1"),
        EdgeDefinition(from_node="n1", to_node="END"),
    ]
    b.state_schema = [
        {"name": "x", "type": "int"}, {"name": "out", "type": "int"},
    ]
    seen: list[tuple[str, str]] = []

    outcome = execute_branch(
        tmp_path,
        branch=b,
        inputs={"x": 21},
        on_node_status=lambda node_id, status: seen.append((node_id, status)),
        actor="universe:u-test",
    )

    assert outcome.status == "completed"
    assert seen == [
        ("n1", NODE_STATUS_RUNNING),
        ("n1", NODE_STATUS_RAN),
    ]


def test_execute_branch_fails_on_compiler_error(tmp_path):
    from tinyassets.runs import execute_branch

    # Missing entry point → validate() error → compiler error
    b = BranchDefinition(name="broken")
    b.node_defs = [NodeDefinition(
        node_id="only", display_name="Only", prompt_template="{x}",
    )]
    b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
    b.state_schema = [{"name": "x", "type": "str"}]

    outcome = execute_branch(tmp_path, branch=b, inputs={"x": "hi"}, actor="universe:u-test")
    assert outcome.status == "failed"
    assert outcome.error


def test_execute_branch_refuses_code_the_run_did_not_author(tmp_path):
    """Design D2 (`sandboxed-code-node`): the runner derives caller_provenance
    from the run's actor and the branch's author. A branch someone else
    authored, run directly, fails before any node runs - classified
    `node_not_accepted` with the remix instruction. The same branch authored
    by the actor runs unapproved."""
    from tinyassets.runs import _classify_failure, execute_branch, get_run

    def _branch(author):
        b = BranchDefinition(name="test", entry_point="only", author=author)
        b.node_defs = [NodeDefinition(
            node_id="only", display_name="Only",
            source_code="def run(state): return {}",
            approved=False,
        )]
        b.graph_nodes = [GraphNodeRef(id="only", node_def_id="only")]
        b.edges = [
            EdgeDefinition(from_node="START", to_node="only"),
            EdgeDefinition(from_node="only", to_node="END"),
        ]
        return b

    outcome = execute_branch(
        tmp_path, branch=_branch("someone_else"), inputs={}, actor=ACTOR,
    )
    assert outcome.status == "failed"
    assert "did not author" in outcome.error and "remix" in outcome.error
    assert _classify_failure(get_run(tmp_path, outcome.run_id)) == "node_not_accepted"

    # The same branch, authored by the actor. This used to read
    # `_branch("anonymous")` against an actor that also defaulted to
    # "anonymous" -- two placeholders comparing equal, which is not what
    # "the actor authored it" means.
    outcome = execute_branch(tmp_path, branch=_branch(ACTOR), inputs={}, actor=ACTOR)
    assert outcome.status == "completed", outcome.error


# ─────────────────────────────────────────────────────────────────────────────
# MCP action tests (through the extensions tool dispatcher)
# ─────────────────────────────────────────────────────────────────────────────


def test_run_branch_recipe_vignette_end_to_end(runner_env):
    """Acceptance criterion #1 — recipe-tracker runs via run_branch.

    Phase 3.5: run_branch now returns ``status=queued`` and the worker
    finishes in the background. Use ``_run_and_wait`` to resolve the
    terminal state for assertion.
    """
    us, _ = runner_env
    bid = _build_recipe_branch(us)

    result = _run_and_wait(
        us,
        branch_def_id=bid,
        inputs_json=json.dumps({"raw_recipe": "pasta carbonara"}),
    )
    assert result["status"] == "completed", result
    assert "capture_output" in result["output"]
    assert "archive_output" in result["output"]


def test_run_branch_rejects_invalid_branch(runner_env):
    us, base = runner_env
    # No nodes → validate() fails. build_branch refuses such a spec up front,
    # so the stored row is written directly, as a legacy or partial row would be.
    from tinyassets.daemon_server import save_branch_definition

    bid = "empty-branch"
    save_branch_definition(base, branch_def=BranchDefinition(
        branch_def_id=bid, name="Empty", author="tester",
    ).to_dict())
    result = _call(us, "run_branch", branch_def_id=bid, inputs_json="{}",
                   universe_id=RUNNER_UNIVERSE)
    assert "error" in result
    assert "validation_errors" in result


def test_run_branch_rejects_malformed_inputs_json(runner_env):
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    result = _call(
        us, "run_branch",
        branch_def_id=bid, inputs_json="this is not json",
        universe_id=RUNNER_UNIVERSE,
    )
    assert "error" in result


def test_get_run_returns_snapshot_with_mermaid(runner_env):
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    run = _run_and_wait(
        us, branch_def_id=bid,
        inputs_json=json.dumps({"raw_recipe": "risotto"}),
    )
    rid = run["run_id"]

    snapshot = _call(us, "get_run", run_id=rid)
    assert snapshot["status"] == "completed"
    assert snapshot["mermaid"].startswith("```mermaid")
    assert "flowchart" in snapshot["mermaid"]
    # Status-colored classes are declared in the diagram
    assert "classDef ran" in snapshot["mermaid"]
    # Per-node statuses are ordered, each a short record
    assert len(snapshot["node_statuses"]) >= 3
    assert all(
        isinstance(s.get("node_id"), str) and isinstance(s.get("status"), str)
        for s in snapshot["node_statuses"]
    )


def test_list_runs_filters_by_branch(runner_env):
    us, _ = runner_env
    bid1 = _build_recipe_branch(us)
    bid2 = _build_recipe_branch(us)
    _run_and_wait(us, branch_def_id=bid1,
                  inputs_json=json.dumps({"raw_recipe": "a"}))
    _run_and_wait(us, branch_def_id=bid2,
                  inputs_json=json.dumps({"raw_recipe": "b"}))

    listing = _call(us, "list_runs", branch_def_id=bid1)
    assert listing["count"] == 1
    assert listing["runs"][0]["branch_def_id"] == bid1


def test_cancel_after_completion_reports_actual_status_without_cancel_record(runner_env):
    from tinyassets.runs import is_cancel_requested

    us, base = runner_env
    bid = _build_recipe_branch(us)
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw_recipe": "a"}))

    result = _call(us, "cancel_run", run_id=run["run_id"])
    assert result["status"] == "completed"
    assert result["terminal"] is True
    assert result["cancel_requested"] is False
    assert not is_cancel_requested(base, run["run_id"])


def test_get_run_output_full_and_single_field(runner_env):
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw_recipe": "a"}))

    full = _call(us, "get_run_output", run_id=run["run_id"])
    assert full["status"] == "completed"
    assert full["output"]

    single = _call(us, "get_run_output",
                   run_id=run["run_id"], field_name="capture_output")
    assert single["field_name"] == "capture_output"
    assert isinstance(single["value"], str)

    missing = _call(us, "get_run_output",
                    run_id=run["run_id"], field_name="nope")
    assert "error" in missing
    assert "available_fields" in missing


def test_run_ledger_entries_land(runner_env):
    us, base = runner_env
    bid = _build_recipe_branch(us)
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw_recipe": "a"}))
    _call(us, "cancel_run", run_id=run["run_id"])

    ledger = json.loads((Path(base) / "ledger.json").read_text("utf-8"))
    actions = [e["action"] for e in ledger]
    assert "run_branch" in actions
    assert "cancel_run" in actions


def test_thread_isolation_between_runs(runner_env):
    """AC #5 — two runs of different branches don't bleed state."""
    us, base = runner_env
    bid1 = _build_recipe_branch(us)
    bid2 = _build_recipe_branch(us)
    run1 = _run_and_wait(us, branch_def_id=bid1,
                         inputs_json=json.dumps({"raw_recipe": "first"}))
    run2 = _run_and_wait(us, branch_def_id=bid2,
                         inputs_json=json.dumps({"raw_recipe": "second"}))

    rec1 = _call(us, "get_run", run_id=run1["run_id"])
    rec2 = _call(us, "get_run", run_id=run2["run_id"])
    assert rec1["run_id"] != rec2["run_id"]
    # thread_id == run_id (isolation guarantee)
    from tinyassets.runs import get_run as raw_get_run

    raw1 = raw_get_run(base, run1["run_id"])
    raw2 = raw_get_run(base, run2["run_id"])
    assert raw1["thread_id"] == run1["run_id"]
    assert raw2["thread_id"] == run2["run_id"]
    assert raw1["thread_id"] != raw2["thread_id"]


def test_no_fantasy_domain_import_required(runner_env, monkeypatch):
    """AC #6 — a non-fantasy branch runs with no fantasy_author imports."""
    import sys

    # Drop any previously-imported fantasy_author modules to prove the
    # runner doesn't require them.
    fa_mods = [
        k for k in list(sys.modules)
        if k.startswith("fantasy_author")
        or k.startswith("domains.fantasy_daemon")
    ]
    for mod in fa_mods:
        monkeypatch.setitem(sys.modules, mod, None)

    us, _ = runner_env
    built = _call(us, "build_branch", spec_json=json.dumps({
        "name": "Research",
        "entry_point": "analyze",
        "node_defs": [{
            "node_id": "analyze", "display_name": "Analyze",
            "prompt_template": "Summarize: {topic}", "output_keys": ["summary"],
        }],
        "edges": [
            {"from": "START", "to": "analyze"},
            {"from": "analyze", "to": "END"},
        ],
        "state_schema": [
            {"name": "topic", "type": "str"},
            {"name": "summary", "type": "str"},
        ],
    }))
    assert built["status"] == "built", built
    bid = built["branch_def_id"]

    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"topic": "small language models"}))
    assert run["status"] == "completed"
    assert "summary" in run["output"]


# ─────────────────────────────────────────────────────────────────────────────
# tool_return_shapes.md compliance (two-channel returns)
# ─────────────────────────────────────────────────────────────────────────────


def test_run_branch_returns_markdown_text_channel(runner_env):
    """Phase 3.5 — run_branch returns status=queued immediately with a
    text channel that points callers at the polling surface."""
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    result = _call(us, "run_branch", branch_def_id=bid,
                   inputs_json=json.dumps({"raw_recipe": "a"}),
                   universe_id=RUNNER_UNIVERSE)
    assert "text" in result
    assert "queued" in result["text"].lower()
    # Phone-legibility: raw run_id must live in structuredContent, not
    # the text channel (#58). run_id is still present in the dict.
    assert result["run_id"] not in result["text"]
    assert "run_id" in result  # structured content still carries it
    assert 'read_graph target="run"' in result["text"]
    assert "wait_for_run" not in result["text"]
    assert "get_run" not in result["text"]
    assert "cancel_run" not in result["text"]


def test_get_run_text_channel_matches_summary(runner_env):
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw_recipe": "a"}))
    snap = _call(us, "get_run", run_id=run["run_id"])
    assert "text" in snap
    assert "```mermaid" in snap["text"]


def test_list_runs_catalog_text_is_compact(runner_env):
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    _run_and_wait(us, branch_def_id=bid,
                  inputs_json=json.dumps({"raw_recipe": "a"}))
    _run_and_wait(us, branch_def_id=bid,
                  inputs_json=json.dumps({"raw_recipe": "b"}))
    result = _call(us, "list_runs")
    assert "text" in result
    assert "run(s):" in result["text"]
    assert "- `" in result["text"]


def test_cancel_run_text_channel(runner_env):
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw_recipe": "a"}))
    result = _call(us, "cancel_run", run_id=run["run_id"])
    assert "text" in result
    assert "Run finished" in result["text"]
    assert "Cancel requested" not in result["text"]


def test_get_run_output_text_channel(runner_env):
    us, _ = runner_env
    bid = _build_recipe_branch(us)
    run = _run_and_wait(us, branch_def_id=bid,
                       inputs_json=json.dumps({"raw_recipe": "a"}))
    full = _call(us, "get_run_output", run_id=run["run_id"])
    assert "text" in full
    # #58: raw run_id does not leak into the phone-legible text channel.
    assert run["run_id"] not in full["text"]
    # The workflow name should surface instead — it's in the text.
    assert "recipe tracker" in full["text"].lower()
    single = _call(us, "get_run_output", run_id=run["run_id"],
                   field_name="capture_output")
    assert "text" in single
    assert "capture_output" in single["text"]
    assert run["run_id"] not in single["text"]


# ─────────────────────────────────────────────────────────────────────────────
# Phase 3.5: async executor (task #39)
# ─────────────────────────────────────────────────────────────────────────────
#
# `run_branch` returns ``status=queued`` in <1s even when the graph takes
# minutes. `cancel_run` actually stops an in-flight run at the next node
# boundary. `recover_in_flight_runs` cleans up interrupted runs on restart.


def test_run_branch_returns_quickly_with_queued_status(runner_env):
    """AC: MCP returns in <1s wall even when the graph would take much
    longer. Phase 3.5 makes this real."""
    import time

    us, _ = runner_env
    bid = _build_recipe_branch(us)

    start = time.monotonic()
    result = _call(us, "run_branch", branch_def_id=bid,
                   inputs_json=json.dumps({"raw_recipe": "a"}),
                   universe_id=RUNNER_UNIVERSE)
    elapsed = time.monotonic() - start

    assert result["status"] == "queued"
    assert result["run_id"]
    # 1s budget — the synchronous prep path writes the run row + a
    # handful of pending events. Mock provider never runs in the
    # foreground.
    assert elapsed < 2.0, f"run_branch took {elapsed:.2f}s, expected <2s"
    # Wait for the background worker to finish before leaving the test
    # so we don't leak threads into the next one.
    from tinyassets.runs import wait_for

    wait_for(result["run_id"], timeout=30.0)


def test_cancel_run_interrupts_mid_flight(tmp_path):
    """Cancel requested between nodes unwinds the graph cleanly.

    Works directly against ``tinyassets.runs`` rather than through the
    Universe Server so the test can control node timing precisely.
    """
    import threading

    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.runs import (
        execute_branch_async,
        get_run,
        request_cancel,
        wait_for,
    )

    # Build a 3-node linear chain; the middle node waits on a latch so
    # the test has a reliable window to request cancel AFTER node1
    # completes but BEFORE node2.
    gate = threading.Event()

    def fake_provider(prompt, system="", *, role="writer", fallback_response=None):
        if "wait_for_cancel" in prompt:
            gate.wait(timeout=10.0)
        return "[ok]"

    b = BranchDefinition(name="Cancel", entry_point="n1")
    b.node_defs = [
        NodeDefinition(node_id=n, display_name=n.upper(),
                       prompt_template=t, output_keys=[f"{n}_out"])
        for n, t in (
            ("n1", "first"),
            ("n2", "wait_for_cancel"),
            ("n3", "third"),
        )
    ]
    b.graph_nodes = [
        GraphNodeRef(id="n1", node_def_id="n1", position=0),
        GraphNodeRef(id="n2", node_def_id="n2", position=1),
        GraphNodeRef(id="n3", node_def_id="n3", position=2),
    ]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="n1"),
        EdgeDefinition(from_node="n1", to_node="n2"),
        EdgeDefinition(from_node="n2", to_node="n3"),
        EdgeDefinition(from_node="n3", to_node="END"),
    ]
    b.state_schema = [{"name": k, "type": "str"} for k in (
        "n1_out", "n2_out", "n3_out",
    )]

    outcome = execute_branch_async(
        tmp_path, branch=b, inputs={},
        provider_call=fake_provider,
        actor="universe:u-test",
    )
    assert outcome.status == "queued"
    # Request cancel while n2 is blocked on the gate.
    import time

    time.sleep(0.5)
    request_cancel(tmp_path, outcome.run_id)
    gate.set()  # let n2 finish; cancel check fires in the event_sink
    wait_for(outcome.run_id, timeout=10.0)

    record = get_run(tmp_path, outcome.run_id)
    assert record["status"] == "cancelled"
    # n3 must NOT have emitted a 'ran' event.
    from tinyassets.runs import list_events

    events = list_events(tmp_path, outcome.run_id)
    ran_nodes = {e["node_id"] for e in events if e["status"] == "ran"}
    assert "n3" not in ran_nodes


def test_recover_in_flight_runs_marks_running_as_interrupted(tmp_path):
    """Simulated restart: queued/running rows become 'interrupted'."""
    from tinyassets.runs import (
        RUN_STATUS_RUNNING,
        create_run,
        get_run,
        initialize_runs_db,
        recover_in_flight_runs,
        update_run_status,
    )

    initialize_runs_db(tmp_path)
    rid1 = create_run(tmp_path, branch_def_id="b1", thread_id="",
                      inputs={}, actor="a")
    rid2 = create_run(tmp_path, branch_def_id="b2", thread_id="",
                      inputs={}, actor="a")
    update_run_status(tmp_path, rid1, status=RUN_STATUS_RUNNING)
    # rid2 stays queued; the process that owned both has died.
    from tests.run_owner_helpers import mark_owner_dead

    mark_owner_dead(tmp_path, rid1, rid2)
    count = recover_in_flight_runs(tmp_path)
    assert count == 2

    r1 = get_run(tmp_path, rid1)
    r2 = get_run(tmp_path, rid2)
    assert r1["status"] == "interrupted"
    assert r2["status"] == "interrupted"
    assert r1["finished_at"] is not None


def test_recover_in_flight_runs_leaves_terminal_rows_alone(tmp_path):
    """Completed/failed/cancelled rows must NOT be re-marked."""
    from tinyassets.runs import (
        RUN_STATUS_CANCELLED,
        RUN_STATUS_COMPLETED,
        RUN_STATUS_FAILED,
        create_run,
        get_run,
        recover_in_flight_runs,
        update_run_status,
    )

    for status in (RUN_STATUS_COMPLETED, RUN_STATUS_FAILED,
                   RUN_STATUS_CANCELLED):
        rid = create_run(tmp_path, branch_def_id="b", thread_id="",
                         inputs={}, actor="a")
        update_run_status(tmp_path, rid, status=status)
        recover_in_flight_runs(tmp_path)
        assert get_run(tmp_path, rid)["status"] == status


def test_latest_run_by_name_returns_newest_matching_branch_run(tmp_path):
    from tinyassets.runs import (
        RUN_STATUS_COMPLETED,
        create_run,
        latest_run_by_name,
        update_run_status,
    )

    older = create_run(
        tmp_path, branch_def_id="b1", thread_id="", inputs={},
        run_name="branch-task-bt-1", actor="a",
    )
    newer = create_run(
        tmp_path, branch_def_id="b1", thread_id="", inputs={},
        run_name="branch-task-bt-1", actor="a",
    )
    other_branch = create_run(
        tmp_path, branch_def_id="b2", thread_id="", inputs={},
        run_name="branch-task-bt-1", actor="a",
    )
    for rid in (older, newer, other_branch):
        update_run_status(tmp_path, rid, status=RUN_STATUS_COMPLETED)

    match = latest_run_by_name(
        tmp_path,
        run_name="branch-task-bt-1",
        branch_def_id="b1",
    )

    assert match is not None
    assert match["run_id"] == newer


def test_concurrent_cap_respected(tmp_path, monkeypatch):
    """Custom TINYASSETS_RUN_MAX_CONCURRENT is honored."""
    monkeypatch.setenv("TINYASSETS_RUN_MAX_CONCURRENT", "2")
    from tinyassets import runs as runs_mod

    # Force executor re-init with the new env var.
    runs_mod.shutdown_executor()
    executor = runs_mod._get_executor()
    assert executor._max_workers == 2
    runs_mod.shutdown_executor()


def test_async_run_completes_successfully(tmp_path):
    """End-to-end: execute_branch_async produces the same final output
    as the sync path, just on a worker thread."""
    from tinyassets.branches import (
        BranchDefinition,
        EdgeDefinition,
        GraphNodeRef,
        NodeDefinition,
    )
    from tinyassets.runs import (
        execute_branch_async,
        get_run,
        wait_for,
    )

    b = BranchDefinition(name="Async", entry_point="n", author=ACTOR)
    b.node_defs = [NodeDefinition(
        node_id="n", display_name="N",
        source_code="def run(state): return {'out': state.get('x', 0) * 3}",
        input_keys=["x"],
    ).mark_approved()]
    b.graph_nodes = [GraphNodeRef(id="n", node_def_id="n")]
    b.edges = [
        EdgeDefinition(from_node="START", to_node="n"),
        EdgeDefinition(from_node="n", to_node="END"),
    ]
    b.state_schema = [
        {"name": "x", "type": "int"}, {"name": "out", "type": "int"},
    ]

    outcome = execute_branch_async(tmp_path, branch=b, inputs={"x": 7}, actor="universe:u-test")
    assert outcome.status == "queued"
    wait_for(outcome.run_id, timeout=10.0)

    record = get_run(tmp_path, outcome.run_id)
    assert record["status"] == "completed"
    assert record["output"] == {"x": 7, "out": 21}
