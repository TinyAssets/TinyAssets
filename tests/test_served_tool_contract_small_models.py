"""One user message must not cost a small model twenty requests.

LIVE EVIDENCE (prod, 2026-09-30, free account ``u-01ky3zh1arr8qth8jee7zx63pq``
on OpenRouter ``:free`` models, whose whole account shares about fifty requests
a day). Turn 01:07Z, "make a morning note workflow", took 21 rounds:

* rounds 5-9 and 12 -- ``write_graph target=branch operation=create`` with a
  ``payload_json`` STRING the model had hand-escaped wrong, answered
  ``{"error": "payload_json must be valid JSON."}`` with ``isError: false``.
  No position, and a non-error flag that read as "that worked".
* round 11 -- ``{"nodes": []}``: "Branch must have at least one node."
* round 13 -- ``{"nodes": [{"node_id": "n1", "type": "prompt", ...}]}``: told
  "node spec missing node_id or display_name" with node_id present.

Other turns: 05:16Z polled ``read_graph target=run`` three times back to back.

``tests/test_served_branch_create_errors.py`` (#4108/#4123) already pins the
positioned parse error and the round-13 display_name/entry-point defaults on the
handler function. This module pins what that could not see, because it lives
past the handler: the argument TYPE a model may send, and the ``isError`` flag
on the MCP result -- so the refusal tests here dispatch through a real MCP
client session, not the Python function.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from tests import test_served_branch_create_errors as _branch_create
from tests.test_served_branch_create_errors import _create, _landed

#: The REAL served create path, end to end into storage (see that module).
served = _branch_create.served

# ---------------------------------------------------------------------------
# The evidence payloads
# ---------------------------------------------------------------------------

#: Rounds 5-9, 12: the spec as a JSON STRING carrying a literal newline inside
#: ``prompt_template`` -- the escaping a small model gets wrong.
ROUND_5_STRING = (
    '{"name": "Morning Note", "nodes": [{"node_id": "generate_note", '
    '"type": "prompt", "prompt_template": "Write a short note\non what to '
    'focus on today"}]}'
)
#: The same spec, as the object the model meant.
ROUND_5_OBJECT = {
    "name": "Morning Note",
    "nodes": [{
        "node_id": "generate_note",
        "type": "prompt",
        "prompt_template": "Write a short note\non what to focus on today",
    }],
}
#: Round 11.
ROUND_11_EMPTY = {"name": "Morning Note", "nodes": []}
#: Round 13: node_id present, no display_name, no edges, no entry_point.
ROUND_13_NODES = {
    "name": "Morning Note",
    "nodes": [{"node_id": "n1", "type": "prompt", "prompt_template": "Hello"}],
}


def _call(server, tool: str, arguments: dict):
    """Dispatch through a real MCP client session: middleware, schema and all."""
    from fastmcp import Client

    async def go():
        async with Client(server.mcp) as client:
            return await client.call_tool_mcp(tool, arguments)

    return asyncio.run(go())


def _text(result) -> str:
    assert len(result.content) == 1, result.content
    return result.content[0].text


# ---------------------------------------------------------------------------
# 1. The payload may be the object itself
# ---------------------------------------------------------------------------


def test_round_5_as_an_object_lands_through_the_real_dispatch(served):
    """The whole fix for six rounds: nothing to escape, so nothing to get wrong."""
    result = _call(served, "write_graph", {
        "target": "branch", "operation": "create", "payload_json": ROUND_5_OBJECT,
    })
    assert result.isError is False, _text(result)
    out = json.loads(_text(result))
    assert _landed(out), out


def test_the_object_keeps_its_newline_verbatim(served):
    out = json.loads(served.write_graph(
        target="branch", operation="create", payload_json=ROUND_5_OBJECT,
    ))
    assert _landed(out), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert stored["node_defs"][0]["prompt_template"] == (
        "Write a short note\non what to focus on today"
    )


def test_the_string_form_still_works(served):
    """Back-compat: every caller that sends text keeps working unchanged."""
    out = _create(served, json.dumps(ROUND_5_OBJECT))
    assert _landed(out), out


def test_a_double_encoded_string_is_unwrapped_once(served):
    """The other thing a small model sends after "pass a JSON string"."""
    out = _create(served, json.dumps(json.dumps(ROUND_5_OBJECT)))
    assert _landed(out), out


def test_the_schema_advertises_object_or_string(served):
    """What the model is TOLD it may send. A str-only schema invited the escaping."""
    from fastmcp import Client

    async def go():
        async with Client(served.mcp) as client:
            return {tool.name: tool for tool in await client.list_tools()}

    tools = asyncio.run(go())
    for name, param in (("write_graph", "payload_json"), ("run_graph", "inputs_json")):
        schema = tools[name].inputSchema["properties"][param]
        kinds = {branch.get("type") for branch in schema.get("anyOf", [schema])}
        assert {"string", "object", "array"} <= kinds, (name, schema)


def test_run_graph_inputs_json_accepts_the_object(monkeypatch):
    """The run_graph docstring itself shows ``inputs_json={"files": [...]}``."""
    from tinyassets import engine_mcp_server as s

    assert s._json_text({"topic": "a\nb"}) == '{"topic":"a\\nb"}'
    assert s._json_text('{"topic": 1}') == '{"topic": 1}'
    assert s._json_text("") == ""
    assert s._json_text(None) == ""
    assert s._json_text([{"op": "rename"}]) == '[{"op":"rename"}]'
    # A string that merely IS a JSON string (not an encoded object) is left alone.
    assert s._json_text('"hello"') == '"hello"'
    # Malformed text passes through untouched, to reach the positioned error.
    assert s._json_text(ROUND_5_STRING) == ROUND_5_STRING


# ---------------------------------------------------------------------------
# 2. A refusal is an error, with its position
# ---------------------------------------------------------------------------


def test_round_5_string_is_an_error_with_line_column_and_excerpt(served):
    """Before: isError false, six words. After: isError true, and WHERE."""
    result = _call(served, "write_graph", {
        "target": "branch", "operation": "create", "payload_json": ROUND_5_STRING,
    })
    assert result.isError is True
    detail = json.loads(_text(result))["error"]
    assert "line 1 column" in detail, detail
    assert "near:" in detail and "\\n" in detail, detail
    # And the way out that needs no escaping at all.
    assert "JSON object itself" in detail, detail


def test_round_11_is_an_accurate_error_flagged_as_one(served):
    result = _call(served, "write_graph", {
        "target": "branch", "operation": "create", "payload_json": ROUND_11_EMPTY,
    })
    assert result.isError is True
    out = json.loads(_text(result))
    assert out["status"] == "rejected"
    assert "at least one node" in " ".join(out["errors"]).lower()
    # The fix names the container the caller used and shows an entry.
    assert "node_id" in json.dumps(out["suggestions"])


def test_round_13_lands_and_never_says_node_id_is_missing(served):
    result = _call(served, "write_graph", {
        "target": "branch", "operation": "create", "payload_json": ROUND_13_NODES,
    })
    text = _text(result)
    assert "missing node_id" not in text
    assert result.isError is False, text
    assert _landed(json.loads(text))


def test_a_success_is_not_flagged(served):
    result = _call(served, "read_graph", {"target": "handbook"})
    assert result.isError is False


@pytest.mark.parametrize("text,refused", [
    ('{"error": "payload_json must be valid JSON."}', True),
    ('{"status": "rejected", "errors": ["Branch name is required."]}', True),
    ('{"errors": ["x"]}', True),
    ('{"error": "run failed", "status": "failed", "run_id": "r1"}', False),
    ('{"status": "built", "branch_def_id": "b1", "errors": []}', False),
    ('{"error": ""}', False),
    ('{"untrusted": true, "content": {"error": "theirs"}}', False),
    ("plain text", False),
    ("[1, 2]", False),
    ("{not json", False),
])
def test_the_refusal_predicate(text, refused):
    """A READ of a failed run succeeded; its ``error`` is data, not a refusal."""
    from tinyassets.engine_mcp_server import refusal_text

    assert refusal_text(text) is refused


def test_every_served_handle_is_covered_by_one_middleware():
    """Mutation guard: the flag lives in dispatch, so no handler can forget it."""
    from tinyassets import engine_mcp_server as s

    kinds = [type(m).__name__ for m in s.mcp.middleware]
    assert "RefusalsAreErrors" in kinds
    # INSIDE the ceiling: it judges the handler's whole text, not an envelope.
    assert kinds.index("RefusalsAreErrors") > kinds.index("BoundedResults")


def test_an_oversized_refusal_is_still_an_error_and_still_bounded(monkeypatch, served):
    """gpt-6-astra repro: outside the ceiling, a truncation envelope hid the refusal."""
    from tinyassets import engine_result_bounds as bounds

    monkeypatch.delenv(bounds.CEILING_ENV, raising=False)
    monkeypatch.delenv(bounds.CONTEXT_TOKENS_ENV, raising=False)
    result = _call(served, "read_graph", {"target": "x" * 200_000})
    assert result.isError is True
    assert len(_text(result).encode()) <= bounds.DEFAULT_CEILING_BYTES


def test_a_file_that_contains_an_error_field_is_read_successfully(monkeypatch, served,
                                                                  _owner_box_double):
    """gpt-6-astra repro: a successful read of `{"errors": [...]}` was flagged.

    A file's bytes are the owner's content, not this server's refusal shape.
    """
    from tinyassets import universe_tools
    from tinyassets.api.helpers import _universe_dir
    from tinyassets.boxes.provider import ExecLimits
    from tinyassets.control_plane.binding import bind_session, use_binding

    assert _owner_box_double is not None, "the owner box double needs Linux"
    content = '{"errors": ["historical sample"], "ok": true}'
    monkeypatch.setattr(universe_tools, "read_file", lambda udir, **kw: content)
    # The engine runs a file tool only for the owner session admitted on the box.
    udir = _universe_dir(served._GRAPH_ID)
    udir.mkdir(parents=True, exist_ok=True)
    binding = bind_session(provider=_owner_box_double, limits=ExecLimits(), universe_dir=udir,
                           owner=served._ACTOR_ID, execution_id="small-model-read")
    with use_binding(binding):
        result = _call(served, "read", {"path": "notes/sample.json"})
    assert result.isError is False
    assert _text(result) == content


def test_every_raw_content_handle_is_exempt():
    """A new file/shell handle must join the exemption, or its reads misreport."""
    import inspect

    from tinyassets import engine_mcp_server as s

    async def tools():
        return await s.mcp.list_tools()

    raw = {
        tool.name for tool in asyncio.run(tools())
        if "_universe_tool(" in inspect.getsource(tool.fn)
    }
    assert raw, "the universe file/shell handles were not found"
    assert raw == set(s._RAW_CONTENT_TOOLS)


def test_a_failed_run_read_is_not_an_error(monkeypatch, served):
    """Reading a failed run is a successful read; flagging it would misreport."""
    import tinyassets.universe_server as us

    record = {"run_id": "r1", "status": "failed", "error": "node raised"}
    monkeypatch.setattr(us, "read_graph", lambda **kw: json.dumps(record))
    result = _call(served, "read_graph", {"target": "run", "run_id": "r1"})
    assert result.isError is False
    assert json.loads(_text(result))["status"] == "failed"


# ---------------------------------------------------------------------------
# 3. A list of nodes with no edges runs in order
# ---------------------------------------------------------------------------


def test_a_chain_of_nodes_with_no_edges_runs_in_the_order_listed(served):
    out = _create(served, {
        "name": "Two step",
        "nodes": [
            {"node_id": "gather", "type": "prompt", "prompt_template": "List today"},
            {"node_id": "write_up", "type": "prompt", "prompt_template": "Summarize"},
        ],
    })
    assert _landed(out), out
    assert any("gather -> write_up" in n for n in out.get("notices", [])), out
    from tinyassets.api.helpers import _base_path
    from tinyassets.daemon_server import get_branch_definition

    stored = get_branch_definition(_base_path(), branch_def_id=out["branch_def_id"])
    assert stored["entry_point"] == "gather"
    from tinyassets.branches import BranchDefinition

    edges = BranchDefinition.from_dict(stored).edges
    assert [(e.from_node, e.to_node) for e in edges] == [("gather", "write_up")]


def test_any_wiring_the_author_gave_is_validated_as_written(served):
    """Only an ABSENCE is filled: one edge means the author is wiring it."""
    out = _create(served, {
        "name": "Three step",
        "nodes": [
            {"node_id": "a", "prompt_template": "A"},
            {"node_id": "b", "prompt_template": "B"},
            {"node_id": "c", "prompt_template": "C"},
        ],
        "edges": [{"from": "a", "to": "b"}],
    })
    assert not _landed(out)
    assert "'c' is not reachable" in " ".join(out["errors"]), out


def test_an_entry_point_other_than_the_first_node_is_not_chained(served):
    out = _create(served, {
        "name": "Odd entry",
        "entry_point": "b",
        "nodes": [
            {"node_id": "a", "prompt_template": "A"},
            {"node_id": "b", "prompt_template": "B"},
        ],
    })
    assert not _landed(out)
    assert not out.get("notices"), out


# ---------------------------------------------------------------------------
# 4. Reading a running run waits a bounded time instead of forcing polls
# ---------------------------------------------------------------------------


class _Clock:
    def __init__(self):
        self.now = 0.0
        self.sleeps: list[float] = []

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.now += seconds


def test_a_run_that_settles_inside_the_window_is_answered_once():
    from tinyassets.engine_mcp_server import _read_run_settled

    states = iter(["queued", "running", "running", "completed"])
    reads = []

    def read():
        status = next(states)
        reads.append(status)
        return json.dumps({"run_id": "r1", "status": status})

    clock = _Clock()
    out = _read_run_settled(read, wait_s=10, poll_s=1, clock=clock, sleep=clock.sleep)
    assert json.loads(out)["status"] == "completed"
    assert reads == ["queued", "running", "running", "completed"]
    assert clock.sleeps == [1, 1, 1]


def test_a_long_run_still_answers_running_at_the_bound():
    from tinyassets.engine_mcp_server import _read_run_settled

    clock = _Clock()
    out = _read_run_settled(
        lambda: json.dumps({"status": "running"}),
        wait_s=10, poll_s=1, clock=clock, sleep=clock.sleep,
    )
    assert json.loads(out)["status"] == "running"
    assert sum(clock.sleeps) == pytest.approx(10)


@pytest.mark.parametrize("payload", [
    json.dumps({"error": "Run 'r1' not found."}),
    json.dumps({"status": "failed", "error": "x"}),
    json.dumps({"status": "completed"}),
    "not json",
])
def test_anything_but_a_moving_run_returns_on_the_first_read(payload):
    from tinyassets.engine_mcp_server import _read_run_settled

    clock = _Clock()
    assert _read_run_settled(
        lambda: payload, wait_s=10, poll_s=1, clock=clock, sleep=clock.sleep,
    ) == payload
    assert clock.sleeps == []


def test_the_served_run_read_uses_the_bounded_wait(monkeypatch, served):
    """Wired, not just written: the handle itself waits for the outcome."""
    import tinyassets.universe_server as us
    from tinyassets import engine_mcp_server as s

    states = iter(["running", "completed"])
    monkeypatch.setattr(us, "read_graph", lambda **kw: json.dumps(
        {"run_id": "r1", "status": next(states)},
    ))
    monkeypatch.setattr(s, "_RUN_READ_POLL_S", 0.0)
    out = json.loads(s.read_graph(target="run", run_id="r1"))
    body = out.get("content", out)
    assert body["status"] == "completed", out
