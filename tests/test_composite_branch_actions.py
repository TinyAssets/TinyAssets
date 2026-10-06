"""Composite branch actions — `build_branch` and `patch_branch`.

Covers acceptance criteria from
`docs/specs/composite_branch_actions.md`:

1. Recipe-tracker builds in a single `build_branch` call.
2. Validation failure returns `suggestions`; applying them succeeds.
3. Invalid op in `patch_branch` batch rejects everything atomically.
4. One ledger entry per composite call, not per internal op.
5. Fine-grained actions still work unchanged.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path

import pytest
from langgraph.checkpoint.memory import InMemorySaver


@pytest.fixture
def comp_env(tmp_path, monkeypatch, authenticate_request):
    base = tmp_path / "output"
    base.mkdir()
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(base))
    monkeypatch.setenv("UNIVERSE_SERVER_USER", "tester")
    authenticate_request("tester")
    from tinyassets import universe_server as us

    importlib.reload(us)
    yield us, base
    importlib.reload(us)


def _call(us, action, **kwargs):
    return json.loads(us._extensions_impl(action=action, **kwargs))


RECIPE_SPEC = {
    "name": "Recipe tracker",
    "description": "Capture, categorize, archive recipes",
    "entry_point": "capture",
    "node_defs": [
        {"node_id": "capture", "display_name": "Capture raw recipe",
         "prompt_template": "Extract: {raw_recipe}"},
        {"node_id": "categorize", "display_name": "Categorize",
         "prompt_template": "Classify: {capture_output}"},
        {"node_id": "archive", "display_name": "Archive",
         "prompt_template": "File: {categorize_output}"},
    ],
    "edges": [
        {"from": "START", "to": "capture"},
        {"from": "capture", "to": "categorize"},
        {"from": "categorize", "to": "archive"},
        {"from": "archive", "to": "END"},
    ],
    "state_schema": [
        {"name": "raw_recipe", "type": "str"},
        {"name": "capture_output", "type": "str"},
        {"name": "categorize_output", "type": "str"},
        {"name": "archive_output", "type": "str"},
    ],
}


# ─────────────────────────────────────────────────────────────────────────────
# AC #1 — one-shot recipe-tracker build
# ─────────────────────────────────────────────────────────────────────────────


def test_recipe_tracker_builds_in_one_call(comp_env):
    us, _ = comp_env
    result = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    assert result["status"] == "built", result
    assert result["node_count"] == 3
    assert result["edge_count"] == 4
    assert result["branch_def_id"]
    assert "text" in result
    assert "Recipe tracker" in result["text"]
    assert "```mermaid" in result["text"]


def test_build_branch_returns_batch_receipt(comp_env):
    us, _ = comp_env
    result = _call(
        us,
        "build_branch",
        spec_json=json.dumps(RECIPE_SPEC),
        # `request_id` is an idempotency key and must be 16-128 characters.
        # "chat-plan-123" is 14, so the call was rejected before a receipt was
        # minted and this test failed on a missing key rather than on receipt
        # content. Keep every assertion below; only the key length was wrong.
        request_id="chat-plan-123-valid-idempotency-key",
    )

    receipt = result["batch_receipt"]
    assert receipt["receipt_type"] == "branch_authoring_batch"
    assert receipt["action"] == "build_branch"
    assert receipt["actor"] == "tester"
    assert receipt["branch_def_id"] == result["branch_def_id"]
    assert receipt["branch_name"] == "Recipe tracker"
    assert receipt["operation_count"] == 1
    assert receipt["node_count"] == 3
    assert receipt["edge_count"] == 4
    assert receipt["skill_count"] == 0
    assert receipt["validation"] == {
        "status": "ok",
        "valid": True,
        "error_count": 0,
    }
    assert receipt["source_code_approval"]["runnable"] is True
    assert receipt["source_code_approval"]["unapproved_count"] == 0
    assert (
        receipt["plan_context"]["request_id"]
        == "chat-plan-123-valid-idempotency-key"
    )
    assert receipt["plan_context"]["authoritative"] is False
    assert receipt["authorization_effect"] == {
        "grants_authorization": False,
        "grants_scoped_trust_session": False,
        "bypasses_client_approval_prompts": False,
        "approved_action_scope": [],
        "revocation_handle": None,
        "note": (
            "Evidence-only receipt: clients may display or audit it, but must "
            "not treat it as permission to execute future writes."
        ),
    }
    assert "not an authorization grant" in receipt["caveats"][0]


def test_build_branch_receipt_reports_unapproved_source_code(comp_env):
    us, _ = comp_env
    spec = {
        "name": "Code-backed check",
        "entry_point": "calculate",
        "node_defs": [{
            "node_id": "calculate",
            "display_name": "Calculate",
            "source_code": "def run(state): return {'answer': 42}",
            "output_keys": ["answer"],
        }],
        "edges": [
            {"from": "START", "to": "calculate"},
            {"from": "calculate", "to": "END"},
        ],
        "state_schema": [{"name": "answer", "type": "int"}],
    }

    result = _call(us, "build_branch", spec_json=json.dumps(spec))

    assert result["status"] == "built", result
    approval = result["batch_receipt"]["source_code_approval"]
    assert approval["source_code_node_count"] == 1
    assert approval["approved_count"] == 0
    assert approval["unapproved_count"] == 1
    # Provenance only: the node is unapproved AND the branch runs. Approval
    # never gates a code node (the OS sandbox does); the receipt says so.
    assert approval["gates_execution"] is False
    assert approval["problems"] == []
    assert approval["runnable"] is True
    assert approval["unapproved_nodes"] == [{
        "node_id": "calculate",
        "display_name": "Calculate",
    }]


def test_build_branch_returns_full_branch_in_structured(comp_env):
    us, _ = comp_env
    result = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    assert result["status"] == "built"
    assert result["name"] == "Recipe tracker"
    assert result["node_count"] == 3


def test_build_branch_persists(comp_env):
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]
    # Atomic get_branch returns the same branch.
    got = _call(us, "get_branch", branch_def_id=bid)
    assert got["name"] == "Recipe tracker"


def test_build_branch_preserves_node_timeout_seconds(comp_env):
    us, _ = comp_env
    spec = {
        **RECIPE_SPEC,
        "node_defs": [{
            **RECIPE_SPEC["node_defs"][0],
            "timeout_seconds": 45,
        }],
        "edges": [
            {"from": "START", "to": "capture"},
            {"from": "capture", "to": "END"},
        ],
        "state_schema": [
            {"name": "raw_recipe", "type": "str"},
            {"name": "capture_output", "type": "str"},
        ],
    }

    built = _call(us, "build_branch", spec_json=json.dumps(spec))

    assert built["status"] == "built", built
    got = _call(us, "get_branch", branch_def_id=built["branch_def_id"])
    capture = next(n for n in got["node_defs"] if n["node_id"] == "capture")
    assert capture["timeout_seconds"] == 45.0


def test_build_branch_preserves_explicit_non_strict_input_isolation(comp_env):
    us, _ = comp_env
    spec = {
        **RECIPE_SPEC,
        "node_defs": [{
            "node_id": "capture",
            "display_name": "Capture raw recipe",
            "input_keys": ["raw_recipe"],
            "output_keys": ["capture_output"],
            "prompt_template": "Extract {raw_recipe} using {style_guide}",
            "strict_input_isolation": False,
        }],
        "edges": [
            {"from": "START", "to": "capture"},
            {"from": "capture", "to": "END"},
        ],
        "state_schema": [
            {"name": "raw_recipe", "type": "str"},
            {"name": "style_guide", "type": "str"},
            {"name": "capture_output", "type": "str"},
        ],
    }

    built = _call(us, "build_branch", spec_json=json.dumps(spec))

    assert built["status"] == "built", built
    got = _call(us, "get_branch", branch_def_id=built["branch_def_id"])
    capture = next(n for n in got["node_defs"] if n["node_id"] == "capture")
    assert capture["strict_input_isolation"] is False

    from tinyassets.branches import BranchDefinition
    from tinyassets.graph_compiler import compile_branch

    prompts: list[str] = []

    def provider(prompt, system="", *, role="writer", fallback_response=None):
        prompts.append(prompt)
        return "captured"

    branch = BranchDefinition.from_dict(got)
    app = compile_branch(branch, provider_call=provider).graph.compile(
        checkpointer=InMemorySaver(),
    )
    result = app.invoke(
        {"raw_recipe": "pasta", "style_guide": "terse"},
        config={"configurable": {"thread_id": "non-strict-build-branch"}},
    )
    assert prompts == ["Extract pasta using terse"]
    assert result["capture_output"] == "captured"


def test_build_branch_fork_from_inherits_parent_topology(comp_env):
    us, base = comp_env
    from tinyassets.branch_versions import publish_branch_version

    parent = _call(us, "build_branch", spec_json=json.dumps({
        **RECIPE_SPEC,
        "skills": [{
            "skill_id": "recipe-parser",
            "name": "Recipe Parser",
            "version": "1.0.0",
            "body": "Parse a recipe.",
        }],
    }))
    parent_branch = _call(us, "get_branch", branch_def_id=parent["branch_def_id"])
    parent_version = publish_branch_version(
        base,
        parent_branch,
        notes="publish parent",
        publisher="tester",
    )

    child = _call(us, "build_branch", spec_json=json.dumps({
        "name": "Recipe tracker remix",
        "description": "Forked without repeating the graph",
        "fork_from": parent_version.branch_version_id,
    }))

    assert child["status"] == "built", child
    assert child["node_count"] == 3
    assert child["edge_count"] == 4
    assert child["skill_count"] == 1

    got = _call(us, "get_branch", branch_def_id=child["branch_def_id"])
    assert got["fork_from"] == parent_version.branch_version_id
    assert got["parent_def_id"] == parent["branch_def_id"]
    assert got["entry_point"] == "capture"
    assert [n["node_id"] for n in got["node_defs"]] == [
        n["node_id"] for n in parent_branch["node_defs"]
    ]
    assert got["graph"]["edges"] == parent_branch["graph"]["edges"]
    assert got["state_schema"] == parent_branch["state_schema"]
    assert got["skills"] == parent_branch["skills"]


# ─────────────────────────────────────────────────────────────────────────────
# AC #2 — strict-with-suggestions
# ─────────────────────────────────────────────────────────────────────────────


def test_build_branch_infers_a_missing_entry_point_instead_of_rejecting(comp_env):
    """CONTRACT CHANGED 2026-09-30: an absent entry_point is DERIVED, not refused.

    This test asserted the rejection until then. The rejection was a round the
    spec already determined the answer to — the head of the graph is the node
    nothing points at — and live (turn ``c7d6279d4af74d798375d3f13780140e``,
    round 18) it cost a free account one of its twenty-one rounds. The
    suggestion machinery it used to exercise is still asserted, below, on a spec
    that genuinely cannot be resolved.
    """
    us, _ = comp_env
    spec = {**RECIPE_SPEC, "entry_point": ""}
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "built", result
    got = _call(us, "get_branch", branch_def_id=result["branch_def_id"])
    assert got["entry_point"] == "capture"


def test_a_wrong_entry_point_is_still_rejected_with_a_concrete_suggestion(comp_env):
    """The default fills an ABSENCE; a typo is still an error that names the fix."""
    us, _ = comp_env
    spec = {**RECIPE_SPEC, "entry_point": "captrue"}
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "rejected"
    assert result["errors"]
    assert result["suggestions"]
    # The suggestion names a concrete field to change, never "reshape the spec".
    fixes = " ".join(s["proposed_fix"] for s in result["suggestions"])
    assert "entry_point" in fixes or "capture" in fixes
    assert "reshape the spec" not in fixes.lower()
    # attempted_spec echoed for the client
    assert result["attempted_spec"]["name"] == "Recipe tracker"


def test_applying_suggestion_succeeds(comp_env):
    us, _ = comp_env
    # First call: a misspelled entry_point → rejected with a suggestion.
    bad = {**RECIPE_SPEC, "entry_point": "captrue"}
    first = _call(us, "build_branch", spec_json=json.dumps(bad))
    assert first["status"] == "rejected"
    # Spec suggests capture (first node with no incoming non-START edge).
    # Apply it and retry.
    fixed = {**RECIPE_SPEC, "entry_point": "capture"}
    second = _call(us, "build_branch", spec_json=json.dumps(fixed))
    assert second["status"] == "built"


def test_build_branch_rejects_missing_name_with_suggestion(comp_env):
    us, _ = comp_env
    spec = {**RECIPE_SPEC, "name": ""}
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "rejected"
    assert any(
        "name" in s["proposed_fix"].lower() for s in result["suggestions"]
    )


def test_build_branch_rejects_malformed_json(comp_env):
    us, _ = comp_env
    result = _call(us, "build_branch", spec_json="not json at all")
    assert result["status"] == "rejected"
    assert "suggestions" in result


def test_build_branch_rejects_empty_spec(comp_env):
    us, _ = comp_env
    result = _call(us, "build_branch", spec_json="")
    assert result["status"] == "rejected"
    assert result["suggestions"]


def test_build_branch_rejects_duplicate_node_ids(comp_env):
    us, _ = comp_env
    spec = {
        **RECIPE_SPEC,
        "node_defs": [
            {"node_id": "dup", "display_name": "A", "prompt_template": "a"},
            {"node_id": "dup", "display_name": "B", "prompt_template": "b"},
        ],
        "entry_point": "dup",
        "edges": [
            {"from": "START", "to": "dup"},
            {"from": "dup", "to": "END"},
        ],
    }
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "rejected"


def test_build_branch_coerces_unknown_state_type_and_reports(comp_env):
    """An unknown state type is coerced, REPORTED, and the build still lands.

    CONTRACT CHANGED 2026-09-30: this asserted `status == "rejected"` until
    then. Refusing a whole build over a type name the code had already resolved
    is the defect class that cost a live free-account turn its first create
    attempt (`"string"` → `str`, turn f3617ca3a91d4acab30eea8dbbeb2663 round 3).
    The coercion is still surfaced — as `notices`, which do not reject — so
    nothing is hidden from the author.
    """
    us, _ = comp_env
    spec = {
        **RECIPE_SPEC,
        "state_schema": [
            {"name": "raw_recipe", "type": "strang"},  # typo
            {"name": "capture_output", "type": "str"},
            {"name": "categorize_output", "type": "str"},
            {"name": "archive_output", "type": "str"},
        ],
    }
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "built", result
    assert any("strang" in n for n in result["notices"]), result
    assert not result.get("errors"), result


# ─────────────────────────────────────────────────────────────────────────────
# AC #3 — transactional patch_branch
# ─────────────────────────────────────────────────────────────────────────────


def test_patch_branch_batch_succeeds(comp_env):
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]

    changes = [
        {"op": "add_node", "node_id": "novelty_check",
         "display_name": "Novelty assessor",
         "prompt_template": "Rate: {capture_output}"},
        {"op": "add_state_field", "name": "novelty_score",
         "type": "float"},
        {"op": "remove_edge", "from": "capture", "to": "categorize"},
        {"op": "add_edge", "from": "capture", "to": "novelty_check"},
        {"op": "add_edge", "from": "novelty_check", "to": "categorize"},
    ]
    result = _call(us, "patch_branch", branch_def_id=bid,
                   changes_json=json.dumps(changes))
    assert result["status"] == "patched", result
    assert result["ops_applied"] == 5
    assert result["node_count"] == 4
    receipt = result["batch_receipt"]
    assert receipt["action"] == "patch_branch"
    assert receipt["operation_count"] == 5
    assert receipt["branch_def_id"] == bid
    assert receipt["node_count"] == 4
    assert receipt["source_code_approval"]["runnable"] is True

    got = _call(us, "get_branch", branch_def_id=bid)
    assert any(n["node_id"] == "novelty_check" for n in got["node_defs"])


def test_patch_branch_publishes_versioned_snapshot(comp_env):
    us, base = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]

    result = _call(
        us,
        "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([
            {
                "op": "add_state_field",
                "name": "review_output",
                "type": "str",
            },
        ]),
    )

    assert result["status"] == "patched", result
    assert result["branch_version_id"]
    assert result["parent_version_id"]

    from tinyassets.branch_versions import get_branch_version

    parent = get_branch_version(base, result["parent_version_id"])
    version = get_branch_version(base, result["branch_version_id"])
    assert parent is not None
    assert version is not None
    assert parent.branch_version_id != version.branch_version_id
    assert version.parent_version_id == parent.branch_version_id
    assert parent.snapshot["branch_def_id"] == bid
    assert version.snapshot["branch_def_id"] == bid
    assert all(f["name"] != "review_output" for f in parent.snapshot["state_schema"])
    assert any(f["name"] == "review_output" for f in version.snapshot["state_schema"])


def test_patch_branch_rollback_on_any_op_failure(comp_env):
    """AC #3 — if op 3 is invalid, zero rows mutated. All errors reported."""
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]

    before = _call(us, "get_branch", branch_def_id=bid)
    before_node_count = len(before["node_defs"])

    changes = [
        {"op": "add_node", "node_id": "step_a",
         "display_name": "A", "prompt_template": "a"},
        {"op": "add_node", "node_id": "step_b",
         "display_name": "B", "prompt_template": "b"},
        {"op": "remove_node", "node_id": "does_not_exist"},  # op 2 fails
        {"op": "add_node", "node_id": "step_c",
         "display_name": "C", "prompt_template": "c"},
        {"op": "add_node", "node_id": "step_d",
         "display_name": "D", "prompt_template": "d"},
    ]
    result = _call(us, "patch_branch", branch_def_id=bid,
                   changes_json=json.dumps(changes))
    assert result["status"] == "rejected"
    assert "batch_receipt" not in result
    # Per-op errors include the op_index so clients can target the fix.
    err_indices = [e["op_index"] for e in result["errors"]]
    assert 2 in err_indices

    # Branch is unchanged on disk.
    after = _call(us, "get_branch", branch_def_id=bid)
    assert len(after["node_defs"]) == before_node_count


def test_patch_branch_validation_failure_reverts(comp_env):
    """Op-level success but validate() failure must still revert."""
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]

    # Add a node but don't wire it — validation will flag it as
    # unreachable. Expect rollback.
    changes = [
        {"op": "add_node", "node_id": "orphan",
         "display_name": "Orphan", "prompt_template": "?"},
    ]
    result = _call(us, "patch_branch", branch_def_id=bid,
                   changes_json=json.dumps(changes))
    assert result["status"] == "rejected"
    assert result["validation_errors"]

    # The orphan must NOT be persisted.
    after = _call(us, "get_branch", branch_def_id=bid)
    assert not any(n["node_id"] == "orphan" for n in after["node_defs"])


def test_patch_branch_rejects_unknown_op(comp_env):
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]
    result = _call(us, "patch_branch", branch_def_id=bid,
                   changes_json=json.dumps([{"op": "nonsense"}]))
    assert result["status"] == "rejected"


def test_patch_branch_requires_branch_id(comp_env):
    us, _ = comp_env
    result = _call(us, "patch_branch", changes_json="[]")
    assert result["status"] == "rejected"


def test_patch_branch_requires_changes_json(comp_env):
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    result = _call(us, "patch_branch", branch_def_id=built["branch_def_id"])
    assert result["status"] == "rejected"


# ─────────────────────────────────────────────────────────────────────────────
# AC #4 — one ledger entry per composite call
# ─────────────────────────────────────────────────────────────────────────────


def test_build_branch_writes_one_ledger_entry(comp_env):
    us, base = comp_env
    _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    ledger = json.loads((Path(base) / "ledger.json").read_text("utf-8"))
    actions = [e["action"] for e in ledger]
    # Exactly one build_branch entry — not three add_nodes + four
    # connect_nodes + four add_state_fields + one set_entry_point.
    assert actions.count("build_branch") == 1
    # No atomic actions should be in the ledger from this build.
    for atomic in ("add_node", "connect_nodes", "set_entry_point",
                   "add_state_field"):
        assert atomic not in actions


def test_patch_branch_writes_one_ledger_entry(comp_env):
    us, base = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]
    _call(us, "patch_branch", branch_def_id=bid,
          changes_json=json.dumps([
              {"op": "add_node", "node_id": "x",
               "display_name": "X", "prompt_template": "x"},
              {"op": "add_edge", "from": "capture", "to": "x"},
              {"op": "add_edge", "from": "x", "to": "categorize"},
              {"op": "remove_edge", "from": "capture", "to": "categorize"},
              {"op": "add_state_field", "name": "x_output", "type": "str"},
          ]))
    ledger = json.loads((Path(base) / "ledger.json").read_text("utf-8"))
    patch_entries = [e for e in ledger if e["action"] == "patch_branch"]
    assert len(patch_entries) == 1


def test_rejected_build_does_not_ledger(comp_env):
    us, base = comp_env
    # A misspelled entry_point, since an ABSENT one now builds (2026-09-30).
    bad = {**RECIPE_SPEC, "entry_point": "captrue"}
    _call(us, "build_branch", spec_json=json.dumps(bad))
    ledger_path = Path(base) / "ledger.json"
    if ledger_path.exists():
        ledger = json.loads(ledger_path.read_text("utf-8"))
    else:
        ledger = []
    assert not any(e["action"] == "build_branch" for e in ledger)


def test_rejected_patch_does_not_ledger(comp_env):
    us, base = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]
    _call(us, "patch_branch", branch_def_id=bid,
          changes_json=json.dumps([{"op": "nonsense"}]))
    ledger = json.loads((Path(base) / "ledger.json").read_text("utf-8"))
    assert not any(e["action"] == "patch_branch" for e in ledger)


# ─────────────────────────────────────────────────────────────────────────────
# AC #5 — fine-grained actions still work unchanged (regression gate)
# ─────────────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────────────
# Return shape compliance — tool_return_shapes.md
# ─────────────────────────────────────────────────────────────────────────────


def test_build_branch_text_channel_includes_ack_and_mermaid(comp_env):
    us, _ = comp_env
    result = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    assert "text" in result
    assert "Built branch" in result["text"]
    assert "```mermaid" in result["text"]


def test_patch_branch_text_channel_on_success(comp_env):
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    result = _call(us, "patch_branch",
                   branch_def_id=built["branch_def_id"],
                   changes_json=json.dumps([
                       {"op": "add_state_field",
                        "name": "extra", "type": "str"},
                   ]))
    assert "text" in result
    assert "Patched" in result["text"]


def test_patch_branch_update_node_persists_node_config(comp_env):
    us, _ = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]
    policy = {"preferred": {"provider": "codex", "model": "gpt-5"}}
    retry = {"max_retries": 2, "backoff_seconds": 4.0}

    result = _call(
        us,
        "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "update_node",
            "node_id": "capture",
            "model_hint": "reviewer",
            "llm_policy": policy,
            "retry_policy": retry,
            "timeout_seconds": 450,
        }]),
    )

    assert result["status"] == "patched", result
    assert result["ops_applied"] == 1
    got = _call(us, "get_branch", branch_def_id=bid)
    capture = next(n for n in got["node_defs"] if n["node_id"] == "capture")
    assert capture["model_hint"] == "reviewer"
    assert capture["llm_policy"] == policy
    assert capture["retry_policy"] == retry
    assert capture["timeout_seconds"] == 450.0


def test_patch_branch_update_node_rejects_unknown_field(comp_env):
    us, base = comp_env
    built = _call(us, "build_branch", spec_json=json.dumps(RECIPE_SPEC))
    bid = built["branch_def_id"]

    from tinyassets.branch_versions import list_branch_versions

    before_versions = list_branch_versions(base, bid, limit=10)
    result = _call(
        us,
        "patch_branch",
        branch_def_id=bid,
        changes_json=json.dumps([{
            "op": "update_node",
            "node_id": "capture",
            "approved": True,
        }]),
    )

    assert result["status"] == "rejected"
    assert "unsupported field" in result["errors"][0]["error"]
    after_versions = list_branch_versions(base, bid, limit=10)
    assert [v.branch_version_id for v in after_versions] == [
        v.branch_version_id for v in before_versions
    ]
    ledger = json.loads((Path(base) / "ledger.json").read_text("utf-8"))
    assert not any(e["action"] == "patch_branch" for e in ledger)


def test_build_branch_truncates_mermaid_above_12_nodes(comp_env):
    us, _ = comp_env
    # Build a 13-node linear chain.
    node_defs = [
        {"node_id": f"n{i}", "display_name": f"N{i}",
         "prompt_template": f"step {i}"}
        for i in range(13)
    ]
    edges = [{"from": "START", "to": "n0"}]
    edges += [
        {"from": f"n{i}", "to": f"n{i+1}"} for i in range(12)
    ]
    edges.append({"from": "n12", "to": "END"})
    spec = {
        "name": "Linear 13",
        "entry_point": "n0",
        "node_defs": node_defs,
        "edges": edges,
        "state_schema": [],
    }
    result = _call(us, "build_branch", spec_json=json.dumps(spec))
    assert result["status"] == "built"
    # Text notes the phone-legibility truncation explicitly.
    assert "12-node" in result["text"] or "structuredContent" in result["text"]


# ─────────────────────────────────────────────────────────────────────────────
# update_node (#45) — stable-id edits, version bump, ledger inherited
# ─────────────────────────────────────────────────────────────────────────────
