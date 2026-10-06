"""BUG-085 — strict_input_isolation diagnostic + state_schema defaults.

Two distinct fixes covered here:

**B1 — Contradictory error message.** Before the fix, a placeholder
referencing a DECLARED input_key that simply wasn't present in state
(upstream node hadn't produced it yet, or a state_schema default wasn't
seeded) raised a CompilerError saying the key was "outside declared
input_keys" — while listing the key inside the declared list. The
operator saw a self-contradicting message.

After the fix, the error path partitions missing keys into:

- ``truly_outside``: keys NOT in declared_inputs → real isolation
  violation; the existing "outside declared input_keys" message stands.
- ``declared_but_unavailable``: keys IN declared_inputs but absent from
  state at execution time → a different error explaining the actual
  failure mode.

**B2 — state_schema defaults not seeded.** State_schema fields with a
declared ``default_value`` should be available to strict-isolation
prompt placeholders even when the caller didn't pass them in ``inputs``.
The fix exposes ``seed_initial_state`` which merges defaults UNDER
caller inputs.
"""

from __future__ import annotations

import pytest

from tinyassets.branches import BranchDefinition, EdgeDefinition, GraphNodeRef, NodeDefinition
from tinyassets.graph_compiler import (
    CompilerError,
    _build_prompt_template_node,
    _state_schema_defaults,
    compile_branch,
    seed_initial_state,
)


def _make_prompt_fn(
    node: NodeDefinition,
    *,
    provider_call=lambda prompt, system, role="writer": f"RENDERED::{prompt}",
    event_sink=None,
):
    return _build_prompt_template_node(
        node, provider_call=provider_call, event_sink=event_sink,
    )


# ─── B1: declared-but-unavailable diagnostic ──────────────────────────────


def test_strict_isolation_declared_but_missing_in_state_emits_clear_message():
    """A declared input_key that isn't present in state at execution
    time must NOT be reported as 'outside declared input_keys'. The
    error must clearly state the key IS declared but not present."""
    node = NodeDefinition(
        node_id="n1",
        display_name="n1",
        # goal_ladder + surface_status are declared, but state will
        # not contain them — mirrors the BUG-085 reproduction shape.
        input_keys=["goal_ladder", "surface_status"],
        output_keys=["draft"],
        prompt_template="Ladder: {goal_ladder}\nStatus: {surface_status}",
        strict_input_isolation=True,
    )
    fn = _make_prompt_fn(node)
    with pytest.raises(CompilerError) as exc:
        fn({})  # state has neither key
    msg = str(exc.value)
    # The error should NOT claim these keys are "outside declared input_keys".
    assert "outside declared input_keys" not in msg, msg
    # It should say they are not present in state.
    assert "not present in state" in msg
    # It should name the declared keys.
    assert "goal_ladder" in msg
    assert "surface_status" in msg


def test_strict_isolation_undeclared_key_still_reports_outside():
    """Pure isolation violation (placeholder NOT in declared inputs)
    must still emit the original 'outside declared input_keys' error."""
    node = NodeDefinition(
        node_id="n1",
        display_name="n1",
        input_keys=["topic"],
        output_keys=["draft"],
        prompt_template="Write {topic} with {leaked_key}.",
        strict_input_isolation=True,
    )
    fn = _make_prompt_fn(node)
    with pytest.raises(CompilerError) as exc:
        fn({"topic": "whales", "leaked_key": "leaked"})
    msg = str(exc.value)
    assert "outside declared input_keys" in msg
    assert "leaked_key" in msg
    # The original strict_input_isolation marker still appears so
    # existing assertions in test_input_keys_isolation.py keep passing.
    assert "strict_input_isolation=true" in msg


def test_strict_isolation_mixed_missing_keys_prioritizes_outside_violation():
    """When BOTH categories are present, the message must clearly call
    out the truly-outside keys (the real isolation violation) and ALSO
    surface the declared-but-unavailable keys so the operator sees the
    full picture, not a contradictory single-message dump."""
    node = NodeDefinition(
        node_id="n1",
        display_name="n1",
        # 'topic' is declared but missing; 'leaked' is the real
        # isolation violation.
        input_keys=["topic"],
        output_keys=["draft"],
        prompt_template="Topic: {topic}; Extra: {leaked}",
        strict_input_isolation=True,
    )
    fn = _make_prompt_fn(node)
    with pytest.raises(CompilerError) as exc:
        fn({"leaked": "value"})  # 'topic' missing, 'leaked' is undeclared
    msg = str(exc.value)
    assert "outside declared input_keys" in msg
    assert "leaked" in msg
    # The declared-but-unavailable key should also be surfaced.
    assert "topic" in msg
    assert "not present in state" in msg


def test_strict_isolation_declared_keys_present_still_renders():
    """Sanity: declared input_keys that ARE present in state render
    normally — no error path triggered."""
    node = NodeDefinition(
        node_id="n1",
        display_name="n1",
        input_keys=["goal_ladder"],
        output_keys=["draft"],
        prompt_template="Ladder: {goal_ladder}",
        strict_input_isolation=True,
    )
    fn = _make_prompt_fn(node)
    out = fn({"goal_ladder": "step1"})
    assert out == {"draft": "RENDERED::Ladder: step1"}


# ─── B2: state_schema defaults seeded into initial state ──────────────────


def test_state_schema_defaults_extracts_default_values():
    """``_state_schema_defaults`` returns a dict of {name: default_value}
    for every state_schema entry carrying a non-None default."""
    schema = [
        {"name": "topic", "type": "string", "default_value": "whales"},
        {"name": "count", "type": "number", "default_value": 5},
        {"name": "no_default", "type": "string"},
        {"name": "explicit_none", "type": "string", "default_value": None},
    ]
    result = _state_schema_defaults(schema)
    assert result == {"topic": "whales", "count": 5}


def test_state_schema_defaults_handles_empty_or_none():
    assert _state_schema_defaults(None) == {}
    assert _state_schema_defaults([]) == {}


def test_seed_initial_state_merges_defaults_under_inputs():
    """Caller inputs always win; defaults only fill keys the caller did
    not provide."""
    schema = [
        {"name": "topic", "type": "string", "default_value": "default-topic"},
        {"name": "mood", "type": "string", "default_value": "cheerful"},
    ]
    inputs = {"topic": "explicit-topic"}
    seeded = seed_initial_state(inputs, schema)
    assert seeded["topic"] == "explicit-topic"  # caller wins
    assert seeded["mood"] == "cheerful"  # default fills gap


def test_seed_initial_state_returns_independent_dict():
    """The returned dict must not be the same object as inputs (no
    surprise mutation of the caller's dict)."""
    schema = [{"name": "x", "type": "string", "default_value": "d"}]
    inputs = {"y": 1}
    seeded = seed_initial_state(inputs, schema)
    assert seeded is not inputs
    assert "x" not in inputs  # caller's dict untouched


def test_seed_initial_state_no_schema_returns_copy_of_inputs():
    inputs = {"a": 1}
    seeded = seed_initial_state(inputs, None)
    assert seeded == {"a": 1}
    assert seeded is not inputs


def test_strict_isolation_state_schema_default_available_to_placeholder():
    """End-to-end: a state_schema field with a default, declared as an
    input_key, is available to a strict-isolation placeholder even when
    the caller did not pass it in ``inputs`` — provided the caller used
    ``seed_initial_state`` to build the initial state."""
    schema = [
        {"name": "style", "type": "string", "default_value": "formal"},
    ]
    node = NodeDefinition(
        node_id="n1",
        display_name="n1",
        input_keys=["style"],
        output_keys=["draft"],
        prompt_template="Style: {style}",
        strict_input_isolation=True,
    )
    fn = _make_prompt_fn(node)
    initial_state = seed_initial_state({}, schema)
    out = fn(initial_state)
    assert out == {"draft": "RENDERED::Style: formal"}


# ─── Codex checker finding 1: defaulted-but-not-in-input_keys is allowed ──


def test_strict_isolation_schema_default_not_in_input_keys_renders():
    """Codex repro (finding 1): ``state_schema=[{"name":"style",
    "default_value":"formal"}]``, node ``input_keys=["topic"]``, prompt
    ``{topic} {style}`` — must compile, seed ``style``, and render with
    ``style`` populated from defaults. Previously failed at runtime
    with ``style outside declared input_keys ['topic']``.

    The fix treats schema-defaulted keys as effectively-declared input
    for both the render allow-list and the truly_outside partition.
    """
    schema = [
        {"name": "style", "default_value": "formal"},
    ]
    node = NodeDefinition(
        node_id="n1",
        display_name="n1",
        input_keys=["topic"],  # 'style' NOT duplicated into input_keys
        output_keys=["draft"],
        prompt_template="{topic} {style}",
        strict_input_isolation=True,
    )
    fn = _build_prompt_template_node(
        node,
        provider_call=lambda prompt, system, role="writer": (
            f"RENDERED::{prompt}"
        ),
        event_sink=None,
        state_schema=schema,
    )
    initial_state = seed_initial_state({"topic": "whales"}, schema)
    out = fn(initial_state)
    assert out == {"draft": "RENDERED::whales formal"}


def test_strict_isolation_schema_default_referenced_but_missing_state_is_unavailable_not_outside():
    """A schema-defaulted key referenced in the prompt but absent from
    state (caller bypassed ``seed_initial_state``) must report as
    "not present in state", NOT as "outside declared input_keys".
    Schema defaults count as effectively-declared, so this is an
    unavailability problem, not an isolation violation.
    """
    schema = [
        {"name": "style", "default_value": "formal"},
    ]
    node = NodeDefinition(
        node_id="n1",
        display_name="n1",
        input_keys=["topic"],
        output_keys=["draft"],
        prompt_template="{topic} {style}",
        strict_input_isolation=True,
    )
    fn = _build_prompt_template_node(
        node,
        provider_call=lambda prompt, system, role="writer": (
            f"RENDERED::{prompt}"
        ),
        event_sink=None,
        state_schema=schema,
    )
    with pytest.raises(CompilerError) as exc:
        fn({"topic": "whales"})  # state lacks 'style'; no seed call
    msg = str(exc.value)
    # 'style' is schema-defaulted ⇒ effectively-declared ⇒ NOT outside.
    assert "outside declared input_keys" not in msg, msg
    assert "not present in state" in msg
    assert "style" in msg


# ─── Codex checker finding 2: mutable defaults don't leak across runs ──


def test_seed_initial_state_mutable_list_default_does_not_leak():
    """Codex repro (finding 2): ``schema=[{"name":"items",
    "default_value":[]}]``; call ``seed_initial_state``, append to
    ``items``, call again — the second initial state must have an
    EMPTY ``items`` list. Previously, the shallow copy of the defaults
    dict reused the same list object, so mutation leaked across runs.
    """
    schema = [{"name": "items", "default_value": []}]
    first = seed_initial_state({}, schema)
    first["items"].append("leaked")
    second = seed_initial_state({}, schema)
    assert second["items"] == []
    # And the two list objects must be distinct.
    assert first["items"] is not second["items"]


def test_seed_initial_state_mutable_dict_default_does_not_leak():
    """Mirror of the list-default repro but for dict defaults."""
    schema = [{"name": "tally", "default_value": {}}]
    first = seed_initial_state({}, schema)
    first["tally"]["count"] = 1
    second = seed_initial_state({}, schema)
    assert second["tally"] == {}
    assert first["tally"] is not second["tally"]


def test_seed_initial_state_nested_mutable_default_does_not_leak():
    """Nested-structure default (list-of-dicts) — deepcopy must isolate
    every level, not just the top container."""
    schema = [
        {"name": "rows", "default_value": [{"v": 1}, {"v": 2}]},
    ]
    first = seed_initial_state({}, schema)
    first["rows"][0]["v"] = 999
    second = seed_initial_state({}, schema)
    assert second["rows"] == [{"v": 1}, {"v": 2}]
    assert first["rows"][0] is not second["rows"][0]


def test_state_schema_defaults_returns_independent_copies_per_call():
    """Direct test of ``_state_schema_defaults`` — the canonical
    "fresh defaults" entry point. Two calls must return independent
    objects for every mutable default."""
    schema = [
        {"name": "items", "default_value": [1, 2, 3]},
        {"name": "tally", "default_value": {"a": 1}},
    ]
    a = _state_schema_defaults(schema)
    b = _state_schema_defaults(schema)
    assert a["items"] is not b["items"]
    assert a["tally"] is not b["tally"]
    a["items"].append(99)
    a["tally"]["b"] = 2
    assert b["items"] == [1, 2, 3]
    assert b["tally"] == {"a": 1}


# ─── BUG-094: default_value key alignment (spec ↔ storage ↔ runtime) ───
#
# Triple-key mismatch before this fix:
#   - StateFieldDecl canonical key:        ``default_value``
#   - ``_apply_state_field_spec`` reads:   ``default`` / ``field_default``
#   - ``_apply_state_field_spec`` writes:  ``default``
#   - PR #932's ``_state_schema_defaults`` reads: ``default_value``
#
# Net effect: a build_branch spec with ``default_value`` was silently
# dropped on the way to storage, so PR #932's seeding path found nothing
# at runtime and strict-isolation prompts that legitimately relied on a
# schema default failed with the partition error.


def test_state_schema_defaults_reads_legacy_default_storage_key():
    """Back-compat: branches stored before the key alignment carry the
    field default under the legacy ``default`` storage key. PR #932's
    seeding must still find those values without a data migration."""
    schema = [{"name": "lab_policy", "default": "PROBE"}]
    assert _state_schema_defaults(schema) == {"lab_policy": "PROBE"}


def test_state_schema_defaults_prefers_default_value_over_legacy_default():
    """When both keys are present (dual-write path) ``default_value`` is
    canonical and wins."""
    schema = [
        {"name": "lab_policy", "default_value": "NEW", "default": "OLD"},
    ]
    assert _state_schema_defaults(schema) == {"lab_policy": "NEW"}


def test_state_schema_defaults_still_reads_canonical_default_value():
    """The canonical path remains intact: ``default_value`` alone works."""
    schema = [{"name": "lab_policy", "default_value": "PROBE"}]
    assert _state_schema_defaults(schema) == {"lab_policy": "PROBE"}


def test_apply_state_field_spec_canonical_default_value_preserved():
    """A build_branch spec entry using the canonical ``default_value``
    key must round-trip through ``_apply_state_field_spec`` into storage
    in a shape that ``_state_schema_defaults`` can read."""
    from tinyassets.api.branches import _apply_state_field_spec

    class _StubBranch:
        def __init__(self) -> None:
            self.state_schema: list[dict] = []

    branch = _StubBranch()
    err = _apply_state_field_spec(
        branch, {"name": "lab_policy", "type": "str",
                 "default_value": "PROBE"},
    )
    assert err == ""
    # Storage must carry the canonical key so the runtime seed path finds it.
    assert branch.state_schema[0]["default_value"] == "PROBE"
    # And ``_state_schema_defaults`` agrees end-to-end.
    assert _state_schema_defaults(branch.state_schema) == {
        "lab_policy": "PROBE",
    }


def test_apply_state_field_spec_legacy_default_spec_still_works():
    """The legacy ``default`` spec shape continues to work — older
    callers (and earlier tests) that pass ``default`` should still
    produce a runtime-seedable schema entry."""
    from tinyassets.api.branches import _apply_state_field_spec

    class _StubBranch:
        def __init__(self) -> None:
            self.state_schema: list[dict] = []

    branch = _StubBranch()
    err = _apply_state_field_spec(
        branch, {"name": "lab_policy", "type": "str", "default": "PROBE"},
    )
    assert err == ""
    assert _state_schema_defaults(branch.state_schema) == {
        "lab_policy": "PROBE",
    }


def test_apply_state_field_spec_field_default_legacy_spec_still_works():
    """The other legacy spec shape ``field_default`` (used by
    fine-grained ``add_state_field``) also keeps working."""
    from tinyassets.api.branches import _apply_state_field_spec

    class _StubBranch:
        def __init__(self) -> None:
            self.state_schema: list[dict] = []

    branch = _StubBranch()
    err = _apply_state_field_spec(
        branch,
        {"name": "lab_policy", "type": "str", "field_default": "PROBE"},
    )
    assert err == ""
    assert _state_schema_defaults(branch.state_schema) == {
        "lab_policy": "PROBE",
    }


def test_build_branch_default_value_preserved_through_get_branch(
    tmp_path, monkeypatch, authenticate_request,
):
    """Round-trip: build_branch with state_schema ``default_value`` →
    get_branch returns the value preserved. This is the canonical
    BUG-094 reproduction — before the fix, get_branch returned the
    state_schema entry stripped of any default."""
    import importlib
    import json as _json

    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    # Branch WRITE actions resolve the caller via `_request_branch_actor()`,
    # which is credential-derived and ignores `UNIVERSE_SERVER_USER`.
    authenticate_request("tester")
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    try:
        spec = {
            "name": "BUG-094 round-trip",
            "entry_point": "n1",
            "node_defs": [
                {"node_id": "n1", "display_name": "n1",
                 "input_keys": ["topic"],
                 "prompt_template": "{topic} {lab_policy}"},
            ],
            "edges": [
                {"from": "START", "to": "n1"},
                {"from": "n1", "to": "END"},
            ],
            "state_schema": [
                {"name": "topic", "type": "str"},
                {"name": "lab_policy", "type": "str",
                 "default_value": "PROBE"},
            ],
        }

        built = _json.loads(us._extensions_impl(
            action="build_branch", spec_json=_json.dumps(spec),
        ))
        assert built["status"] == "built", built
        bid = built["branch_def_id"]

        got = _json.loads(us._extensions_impl(
            action="get_branch", branch_def_id=bid,
        ))
        lab_field = next(
            f for f in got["state_schema"] if f["name"] == "lab_policy"
        )
        # Canonical key must be set so the runtime seed path finds it.
        assert lab_field.get("default_value") == "PROBE", lab_field
    finally:
        importlib.reload(us)


def test_build_branch_state_schema_default_seeded_to_strict_prompt(
    tmp_path, monkeypatch, authenticate_request,
):
    """End-to-end M3 proof: build_branch with state_schema default →
    run_branch with only ``topic`` in inputs → strict-isolation prompt
    captures BOTH ``topic`` AND the seeded ``lab_policy`` default.

    This is the BUG-094 + BUG-085-M3 full chain. Before the fix the
    default was stripped at build time, the seeding path found nothing,
    and the prompt invocation failed with the partition error citing
    ``{lab_policy} outside declared input_keys``.
    """
    import importlib
    import json as _json

    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    # Branch WRITE actions resolve the caller via `_request_branch_actor()`,
    # which is credential-derived and ignores `UNIVERSE_SERVER_USER`.
    authenticate_request("tester")
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    try:
        spec = {
            "name": "BUG-094 M3 round-trip",
            "entry_point": "n1",
            "node_defs": [
                {"node_id": "n1", "display_name": "n1",
                 "input_keys": ["topic"],
                 "output_keys": ["draft"],
                 "prompt_template": "{topic} :: {lab_policy}",
                 "strict_input_isolation": True},
            ],
            "edges": [
                {"from": "START", "to": "n1"},
                {"from": "n1", "to": "END"},
            ],
            "state_schema": [
                {"name": "topic", "type": "str"},
                {"name": "draft", "type": "str"},
                {"name": "lab_policy", "type": "str",
                 "default_value": "PROBE"},
            ],
        }
        built = _json.loads(us._extensions_impl(
            action="build_branch", spec_json=_json.dumps(spec),
        ))
        assert built["status"] == "built", built
        bid = built["branch_def_id"]

        got = _json.loads(us._extensions_impl(
            action="get_branch", branch_def_id=bid,
        ))

        from langgraph.checkpoint.memory import InMemorySaver

        from tinyassets.branches import BranchDefinition
        from tinyassets.graph_compiler import (
            compile_branch,
            seed_initial_state,
        )

        prompts: list[str] = []

        def provider(prompt, system="", *, role="writer",
                     fallback_response=None):
            prompts.append(prompt)
            return "drafted"

        branch = BranchDefinition.from_dict(got)
        app = compile_branch(branch, provider_call=provider).graph.compile(
            checkpointer=InMemorySaver(),
        )
        # ``seed_initial_state`` is the canonical entry point — defaults
        # merge UNDER caller inputs, mirroring the runtime path.
        initial = seed_initial_state(
            {"topic": "hello"}, branch.state_schema,
        )
        result = app.invoke(
            initial,
            config={"configurable": {"thread_id": "bug-094-m3"}},
        )
        # The strict-isolation prompt must have seen BOTH keys.
        assert prompts == ["hello :: PROBE"], prompts
        assert result["draft"] == "drafted"
    finally:
        importlib.reload(us)


# ─── BUG-091: partial state_schema must fail before runtime drops keys ───


def test_compile_rejects_output_key_missing_from_partial_state_schema():
    """A partial state_schema used to validate successfully, then LangGraph
    dropped the node's undeclared output at invoke time. Validation must fail
    before compile/run_branch can reach that silent-drop path.
    """
    branch = BranchDefinition(name="BUG-091 output drop", entry_point="n1")
    branch.node_defs = [
        NodeDefinition(
            node_id="n1",
            display_name="N1",
            output_keys=["draft"],
            prompt_template="write",
        )
    ]
    branch.graph_nodes = [GraphNodeRef(id="n1", node_def_id="n1")]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="n1"),
        EdgeDefinition(from_node="n1", to_node="END"),
    ]
    branch.state_schema = [{"name": "other", "type": "str"}]

    with pytest.raises(CompilerError) as exc:
        compile_branch(
            branch,
            provider_call=lambda prompt, system="", *, role="writer": "draft",
        )

    msg = str(exc.value)
    assert "output_key 'draft' is not declared in state_schema" in msg


def test_compile_rejects_input_key_missing_from_partial_state_schema():
    """Initial inputs outside a non-empty state_schema are dropped by the
    synthesized runtime state. Validation must catch the undeclared key.
    """
    branch = BranchDefinition(name="BUG-091 input drop", entry_point="n1")
    branch.node_defs = [
        NodeDefinition(
            node_id="n1",
            display_name="N1",
            input_keys=["topic"],
            output_keys=["draft"],
            prompt_template="{topic}",
        )
    ]
    branch.graph_nodes = [GraphNodeRef(id="n1", node_def_id="n1")]
    branch.edges = [
        EdgeDefinition(from_node="START", to_node="n1"),
        EdgeDefinition(from_node="n1", to_node="END"),
    ]
    branch.state_schema = [{"name": "draft", "type": "str"}]

    with pytest.raises(CompilerError) as exc:
        compile_branch(
            branch,
            provider_call=lambda prompt, system="", *, role="writer": "draft",
        )

    msg = str(exc.value)
    assert "input_key 'topic' is not declared in state_schema" in msg
