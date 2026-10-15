import contextlib
import io
import json
from types import SimpleNamespace

import pytest

from tests.native_room_fixture import ROOM_SOURCE, Dispatch, Room, builder
from tinyassets.engine_mcp_server import _untrusted

CONFIG = dict(
    author="fixture-owner",
    command_center_id="fixture-center",
    storage_workflow_id="fixture-storage",
    room_id="fixture-room",
    room_source=ROOM_SOURCE,
)


@pytest.fixture
def wired(tmp_path):
    room = Room(tmp_path)
    room.init()
    receipt = room.post(
        {
            "client_message_id": "fixture_owner_01",
            "text": "Fixture question",
            "requested_responders": ["village_elder"],
        }
    )
    dispatcher = Dispatch(tmp_path)
    dispatcher.configure(enabled=True)
    reads, commands = [], []
    actual = {"run_id": "fixture-run", "branch_def_id": "fixture-storage", "status": "completed"}
    output = {
        "run_id": "fixture-run",
        "status": "completed",
        "truncated": False,
        "value": json.dumps(receipt),
    }

    def invoke(action, **args):
        if action == "read_graph":
            reads.append(args)
            assert args["graph_id"] == "fixture-center" and args["run_id"] == "fixture-run"
            data = actual if args["target"] == "run" else output
            text = _untrusted("run:fixture-run", json.dumps(data))
            return {"is_error": False, "text": text, "data": json.loads(text)}
        assert action == "bash"
        commands.append(args)
        command = args["command"]
        assert command.startswith("python3 - <<'NATIVE_DISPATCH_PY'\n")
        code = command.split("\n", 1)[1].rsplit("\nNATIVE_DISPATCH_PY", 1)[0]
        code = code.replace("execute_stage('/u',", "execute_stage(" + repr(str(tmp_path)) + ",")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            exec(compile(code, "<fixture-owner-shell>", "exec"), {})
        from tinyassets.universe_tools import DEFAULT_LIMITS

        if len(stdout.getvalue().encode()) > DEFAULT_LIMITS.output_bytes:
            return {"text": f"[killed: output passed {DEFAULT_LIMITS.output_bytes} bytes]"}
        return {"text": stdout.getvalue() + "\n[exit code 0]"}

    branch = builder.build(**CONFIG)
    functions = {}
    for node in branch.node_defs:
        if node.source_code:
            namespace = {"invoke_mcp_action": invoke}
            exec(compile(node.source_code, "<fixture-code-node>", "exec"), namespace)
            functions[node.node_id] = namespace["run"]
    event = {
        "type": "run_completed",
        "branch_def_id": "fixture-storage",
        "outcome": "completed",
        "run_id": "fixture-run",
    }
    return branch, functions, room, dispatcher, actual, output, event, reads, commands


def test_real_generated_stages_commit_actual_result_once(wired):
    branch, functions, room, _, _, _, event, reads, _ = wired
    assert branch.validate() == []
    state = functions["claim"]({"event": event})
    assert state["route"] == "generate" and len(reads) == 2
    answer = functions["commit"]({**state, "native_reply": "Fixture provider output"})
    assert json.loads(answer["result"])["status"] == "done"
    assert room.load()["messages"][-1]["text"] == "Fixture provider output"
    assert functions["claim"]({"event": event})["route"] == "stop"


@pytest.mark.parametrize("separator", ["\u2028", "\u2029", "\u0085"])
def test_unicode_line_separators_preserve_claim_and_reply(wired, separator):
    _, functions, room, _, _, output, event, _, _ = wired
    peer_text = "Earlier participant text" + separator + "continues here"
    room.post({"client_message_id": "fixture_peer_01", "text": peer_text},
              participant="fixture_peer")
    receipt = room.post({
        "client_message_id": "fixture_owner_02", "text": "Addressed question",
        "requested_responders": ["village_elder"],
    })
    output["value"] = json.dumps(receipt)
    state = functions["claim"]({"event": event})
    assert state["route"] == "generate" and peer_text in state["prompt"]
    reply = "Actual native reply" + separator + "continues here"
    answer = functions["commit"]({**state, "native_reply": reply})
    assert json.loads(answer["result"])["status"] == "done"
    assert room.load()["messages"][-1]["text"] == reply
    assert functions["claim"]({"event": event})["route"] == "stop"


def test_oversized_peer_context_stops_before_consuming_allowance(wired):
    _, functions, room, dispatcher, _, output, event, _, _ = wired
    room.post({"client_message_id": "fixture_large_peer", "text": "🌍" * 10000},
              participant="fixture_peer")
    receipt = room.post({
        "client_message_id": "fixture_owner_oversize", "text": "Addressed question",
        "requested_responders": ["village_elder"],
    })
    output["value"] = json.dumps(receipt)
    assert json.loads(functions["claim"]({"event": event})["result"]) == {
        "route": "stop", "reason": "context_too_large",
    }
    assert dispatcher._load()["records"] == {}
    # A later bounded excerpt still has the default one-turn allowance.
    for index in range(10):
        receipt = room.post({
            "client_message_id": f"fixture_small_{index}", "text": "Small question",
            "requested_responders": ["village_elder"],
        })
    output["value"] = json.dumps(receipt)
    state = functions["claim"]({"event": event})
    assert state["route"] == "generate"
    answer = functions["commit"]({**state, "native_reply": "Actual native reply"})
    assert json.loads(answer["result"])["status"] == "done"


@pytest.mark.parametrize(
    "field,value",
    [
        ("branch_def_id", "foreign"),
        ("status", "failed"),
        ("cancel_requested", True),
        ("run_id", "foreign"),
    ],
)
def test_actual_source_run_must_match_before_storage(wired, field, value):
    _, functions, _, _, actual, _, event, _, commands = wired
    actual[field] = value
    with pytest.raises(PermissionError):
        functions["claim"]({"event": event})
    assert not commands


@pytest.mark.parametrize(
    "field,value", [("status", "running"), ("truncated", True), ("run_id", "foreign")]
)
def test_incomplete_receipt_refused_before_storage(wired, field, value):
    _, functions, _, _, _, output, event, _, commands = wired
    output[field] = value
    with pytest.raises(ValueError):
        functions["claim"]({"event": event})
    assert not commands


def test_foreign_room_and_owner_read_denial_refused(wired):
    _, functions, _, _, actual, output, event, _, commands = wired
    value = json.loads(output["value"])
    value["room_id"] = "foreign-room"
    output["value"] = json.dumps(value)
    with pytest.raises(PermissionError):
        functions["claim"]({"event": event})
    actual["error"] = "access revoked"
    with pytest.raises(RuntimeError):
        functions["claim"]({"event": event})
    assert not commands


@pytest.mark.parametrize("owner_cap", [None, 43, 1e10, 315360000])
def test_graph_routes_one_native_node_then_commit_and_stops_replay(
    wired, monkeypatch, tmp_path, owner_cap,
):
    from tinyassets import graph_compiler
    from tinyassets.config import load_universe_config
    from tinyassets.served_tools import node_tool_grant

    branch, functions, room, _, _, _, event, _, _ = wired
    assert node_tool_grant(branch.node_defs[1].tools_allowed) == ("read",)
    calls = []

    def source_node(node, **kwargs):
        return functions[node.node_id]

    def provider(prompt, system, **kwargs):
        calls.append(prompt)
        from tinyassets.universe_intelligence import UNBOUNDED_TURN_SECONDS

        assert kwargs["config"].absolute_cap_s == min(
            owner_cap or UNBOUNDED_TURN_SECONDS, UNBOUNDED_TURN_SECONDS,
        )
        return "Actual fixture native output"

    # Only replace the process sandbox with the same generated functions and a
    # fixture owner RPC. The real compiler handles routing and the prompt node.
    monkeypatch.setattr(graph_compiler, "_build_source_code_node", source_node)
    if owner_cap is not None:
        (tmp_path / "config.yaml").write_text(f"absolute_cap_s: {owner_cap}\n")
    compiled = graph_compiler.compile_branch(
        branch, provider_call=provider,
        universe_context=SimpleNamespace(config=load_universe_config(tmp_path)),
    )
    graph = compiled.graph.compile()
    graph.invoke({"event": event})
    graph.invoke({"event": event})
    assert len(calls) == 1 and len(room.load()["messages"]) == 2
    assert branch.node_defs[1].retry_policy["max_retries"] == 0


def test_builder_literals_do_not_reexpand_placeholder_like_identifiers():
    source = builder.source(
        "claim",
        **{k: v for k, v in CONFIG.items() if k != "author"},
    )
    compile(source, "<generated>", "exec")
    weird = dict(CONFIG, room_id="ROOM_PROGRAM_CENTER")
    branch = builder.build(**weird)
    assert "'ROOM_PROGRAM_CENTER'" in branch.node_defs[0].source_code


def test_production_node_invoker_envelope_and_owner_fence(monkeypatch):
    from tinyassets import engine_tool_client, graph_compiler

    branch = builder.build(**CONFIG)
    result = {"run_id": "fixture-run", "status": "completed"}
    opened = []

    class Client:
        async def call(self, name, arguments):
            assert name == "read_graph" and arguments["target"] == "run"
            return SimpleNamespace(
                isError=False, content=[SimpleNamespace(
                    text=_untrusted("run:fixture-run", json.dumps(result)))],
            )

    @contextlib.asynccontextmanager
    async def open_tools(**kwargs):
        opened.append(kwargs)
        yield Client()

    monkeypatch.setattr(engine_tool_client, "open_engine_tools", open_tools)
    context = SimpleNamespace(
        owner_user_id="fixture-owner",
        universe_id="fixture-center",
        caller_provenance="own",
        definition_author="fixture-owner",
    )
    invoke = graph_compiler._build_node_mcp_invoker(
        branch.node_defs[0],
        event_sink=None,
        execution_context=context,
    )
    envelope = invoke("read_graph", target="run", run_id="fixture-run")
    namespace = {}
    exec(compile(branch.node_defs[0].source_code, "<generated>", "exec"), namespace)
    assert namespace["object_result"](envelope, source="run:fixture-run") == result
    assert opened == [
        {
            "actor_id": "fixture-owner",
            "graph_id": "fixture-center",
            "enabled_tools": ("read_graph",),
        }
    ]
    context.definition_author = "other-owner"
    with pytest.raises(graph_compiler.CompilerError, match="owner"):
        invoke("read_graph", target="run", run_id="fixture-run")
    assert len(opened) == 1
    with pytest.raises(RuntimeError):
        namespace["object_result"]({"is_error": True, "text": "refused", "data": result})
    with pytest.raises(RuntimeError):
        namespace["object_result"]({"is_error": False, "text": "invalid", "data": None})


@pytest.mark.parametrize("content", [None, [], "not an object", {"error": "revoked"},
                                     {"is_error": True}])
def test_invalid_inner_engine_content_refused(content):
    namespace = {}
    exec(compile(builder.build(**CONFIG).node_defs[0].source_code, "<generated>", "exec"),
         namespace)
    inner = {"untrusted": True, "source": "run:fixture-run", "content": content}
    with pytest.raises(RuntimeError, match="invalid content"):
        namespace["object_result"](
            {"is_error": False, "text": json.dumps(inner), "data": inner},
            source="run:fixture-run",
        )


@pytest.mark.parametrize("changes", [{"source": "run:foreign"}, {"untrusted": False}])
def test_foreign_inner_engine_envelope_refused(changes):
    namespace = {}
    exec(compile(builder.build(**CONFIG).node_defs[0].source_code, "<generated>", "exec"),
         namespace)
    inner = {"untrusted": True, "source": "run:fixture-run", "content": {}, **changes}
    with pytest.raises(RuntimeError, match="foreign envelope"):
        namespace["object_result"](
            {"is_error": False, "text": json.dumps(inner), "data": inner},
            source="run:fixture-run",
        )
