"""describe/validate/get_branch: `runnable` reports what the compiler enforces.

`unapproved_source_code_nodes` is provenance (who approved which code) and is
still listed; it never decides `runnable`. What does: structural validation
errors and `source_code_problems`, the compiler's own pattern/size/syntax
check, through the one definition in graph_compiler.source_code_problems."""
import json
from unittest.mock import patch  # noqa: E402

import pytest


@pytest.fixture(autouse=True)
def isolated_root(tmp_path, monkeypatch):
    # Module imports may retain a patched helper; use its supported environment input.
    monkeypatch.setenv("TINYASSETS_DATA_DIR", str(tmp_path))

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_node(node_id="n1", display_name="My Node", source_code="", approved=False):
    import hashlib

    # PR #1349 fail-closed: a genuine approval records the source hash. The
    # describe/validate/get_branch runnability surfaces now provenance-check
    # approval (matching the runtime gate), so an approved fixture node must
    # carry the matching hash to be reported runnable.
    approved_source_hash = ""
    if approved and source_code:
        approved_source_hash = hashlib.sha256(
            source_code.encode("utf-8")
        ).hexdigest()
    return {
        "node_id": node_id,
        "display_name": display_name,
        "source_code": source_code,
        "approved": approved,
        "approved_source_hash": approved_source_hash,
        "phase": "custom",
        "input_keys": [],
        "output_keys": [],
        "description": "",
        "dependencies": [],
        "author": "anon",
        "registered_at": "",
        "enabled": True,
    }


def _make_branch_dict(node_defs=None, branch_def_id="b1", name="TestBranch"):
    nodes = node_defs or []
    entry_point = nodes[0]["node_id"] if nodes else ""
    return {
        "branch_def_id": branch_def_id,
        "name": name,
        "author": "tester",
        "domain_id": "fantasy",
        "entry_point": entry_point,
        "node_defs": nodes,
        "edges": [],
        "state_schema": [],
        "visibility": "public",
        "fork_from": None,
    }


def _call_validate_real(branch_dict):
    from tinyassets.api.branches import _ext_branch_validate

    with (
        patch("tinyassets.daemon_server.get_branch_definition", return_value=branch_dict),
        patch(
            "tinyassets.providers.base.get_sandbox_status",
            return_value={"bwrap_available": True},
        ),
    ):
        result = _ext_branch_validate({"branch_def_id": branch_dict["branch_def_id"]})
    return json.loads(result)


def _call_describe(branch_dict, validate_errors=None):
    from tinyassets.api.branches import _ext_branch_describe

    if validate_errors is None:
        validate_errors = []

    with (
        patch("tinyassets.daemon_server.get_branch_definition", return_value=branch_dict),
        patch(
            "tinyassets.api.branches._related_wiki_pages",
            return_value={"items": [], "truncated_count": 0},
        ),
        patch("tinyassets.branch_versions.list_branch_versions", return_value=[]),
        patch("tinyassets.daemon_server.list_branch_definitions", return_value=[]),
        patch("tinyassets.branches.BranchDefinition.validate", return_value=validate_errors),
    ):
        result = _ext_branch_describe({"branch_def_id": branch_dict["branch_def_id"]})
    return json.loads(result)


def _call_get(branch_dict):
    from tinyassets.api.branches import _ext_branch_get

    with (
        patch("tinyassets.daemon_server.get_branch_definition", return_value=branch_dict),
        patch("tinyassets.api.engine_helpers._current_actor", return_value="tester"),
        patch("tinyassets.api.engine_helpers._current_actor", return_value="tester"),
        patch("tinyassets.api.market._gates_enabled", return_value=False),
        patch(
            "tinyassets.api.branches._related_wiki_pages",
            return_value={"items": [], "truncated_count": 0},
        ),
        patch("tinyassets.daemon_server.list_gate_claims", return_value=[]),
    ):
        result = _ext_branch_get({"branch_def_id": branch_dict["branch_def_id"]})
    return json.loads(result)


def _call_validate(branch_dict, validate_errors=None):
    from tinyassets.api.branches import _ext_branch_validate

    if validate_errors is None:
        validate_errors = []

    with (
        patch("tinyassets.daemon_server.get_branch_definition", return_value=branch_dict),
        patch("tinyassets.branches.BranchDefinition.validate", return_value=validate_errors),
        patch(
            "tinyassets.providers.base.get_sandbox_status",
            return_value={"bwrap_available": True},
        ),
    ):
        result = _ext_branch_validate({"branch_def_id": branch_dict["branch_def_id"]})
    return json.loads(result)


# ---------------------------------------------------------------------------
# describe_branch tests
# ---------------------------------------------------------------------------

class TestDescribeBranchApproval:
    def test_no_source_code_nodes_runnable(self):
        """Branch with no source_code nodes is runnable with no approval warnings."""
        node = _make_node(source_code="", approved=False)
        branch = _make_branch_dict(node_defs=[node])
        result = _call_describe(branch)

        assert result["runnable"] is True
        assert result["unapproved_source_code_nodes"] == []
        assert "APPROVAL REQUIRED" not in result["summary"]

    def test_approved_source_code_node_runnable(self):
        """Branch with an approved source_code node is runnable."""
        node = _make_node(source_code="def run(state): return state", approved=True)
        branch = _make_branch_dict(node_defs=[node])
        result = _call_describe(branch)

        assert result["runnable"] is True
        assert result["unapproved_source_code_nodes"] == []

    def test_unapproved_source_code_node_is_still_runnable(self):
        """An unapproved code node is listed as provenance and RUNS: approval
        never gates execution (the OS sandbox is the boundary)."""
        node = _make_node(
            node_id="sc1",
            display_name="Custom Runner",
            source_code="def run(state): return state",
            approved=False,
        )
        branch = _make_branch_dict(node_defs=[node])
        result = _call_describe(branch)

        assert result["runnable"] is True
        assert result["source_code_problems"] == []
        assert len(result["unapproved_source_code_nodes"]) == 1
        unapp = result["unapproved_source_code_nodes"][0]
        assert unapp["node_id"] == "sc1"
        assert unapp["display_name"] == "Custom Runner"
        assert "APPROVAL REQUIRED" not in result["summary"]
        assert "approve" not in result["summary"].lower()

    def test_code_node_the_compiler_refuses_is_not_runnable(self):
        """What DOES stop a run: a code node with a syntax error, named."""
        node = _make_node(
            node_id="sc1",
            display_name="Broken Runner",
            source_code="def run(state: return state",
            approved=True,
        )
        branch = _make_branch_dict(node_defs=[node])
        result = _call_describe(branch)

        assert result["valid"] is True
        assert result["runnable"] is False
        [problem] = result["source_code_problems"]
        assert problem["node_id"] == "sc1"
        assert problem["display_name"] == "Broken Runner"
        assert any("does not parse" in p for p in problem["problems"])
        assert "CODE NODE 'sc1' (Broken Runner) will not compile" in result["summary"]

    def test_no_served_text_tells_the_agent_to_stop_on_approval(self):
        """Codex round 1: two prompts still said "if a source-code node isn't
        approved, stop". The agent plans from these; they must match the
        receipt."""
        import pathlib

        import tinyassets.api.branches as branches
        import tinyassets.api.prompts as prompts

        for mod in (branches, prompts):
            text = pathlib.Path(mod.__file__).read_text(encoding="utf-8")
            assert "isn't approved" not in text, mod.__name__
            assert "must be approved before it can run" not in text, mod.__name__
        text = pathlib.Path(prompts.__file__).read_text(encoding="utf-8")
        assert "approval never gates a run" in text

    def test_a_syntax_refusal_keeps_its_cause(self):
        from tinyassets.graph_compiler import CompilerError, _validate_source_code

        class _Node:
            node_id = "n"
            source_code = "def run(state) return state"

        try:
            _validate_source_code(_Node())
        except CompilerError as exc:
            assert isinstance(exc.__cause__, SyntaxError)
        else:
            raise AssertionError("did not refuse")

    def test_disallowed_pattern_is_not_runnable(self):
        from tinyassets.graph_compiler import _DANGEROUS_PATTERNS

        node = _make_node(
            node_id="sc1",
            source_code=f"def run(state):\n    {_DANGEROUS_PATTERNS[0]}\n    return state",
        )
        result = _call_describe(_make_branch_dict(node_defs=[node]))
        assert result["runnable"] is False
        assert "disallowed pattern" in result["source_code_problems"][0]["problems"][0]

    def test_unapproved_node_still_valid_structurally(self):
        """valid reflects structural errors only; runnable reflects the compiler."""
        node = _make_node(source_code="code", approved=False)
        branch = _make_branch_dict(node_defs=[node])
        result = _call_describe(branch)

        assert result["valid"] is True
        assert result["runnable"] is True

    def test_multiple_unapproved_nodes_all_listed(self):
        """All unapproved source_code nodes appear in the provenance list."""
        nodes = [
            _make_node(node_id="n1", display_name="Node A", source_code="code", approved=False),
            _make_node(node_id="n2", display_name="Node B", source_code="code", approved=True),
            _make_node(node_id="n3", display_name="Node C", source_code="code", approved=False),
        ]
        branch = _make_branch_dict(node_defs=nodes)
        result = _call_describe(branch)

        assert result["runnable"] is True
        ids = {n["node_id"] for n in result["unapproved_source_code_nodes"]}
        assert ids == {"n1", "n3"}
        assert "n2" not in str(result["unapproved_source_code_nodes"])


# ---------------------------------------------------------------------------
# get_branch tests
# ---------------------------------------------------------------------------

class TestGetBranchApproval:
    def test_no_source_code_nodes_runnable(self):
        """get_branch: no source_code nodes → runnable True, list empty."""
        node = _make_node(source_code="", approved=False)
        branch = _make_branch_dict(node_defs=[node])
        result = _call_get(branch)

        assert result["runnable"] is True
        assert result["unapproved_source_code_nodes"] == []

    def test_unapproved_source_code_node_is_still_runnable(self):
        """get_branch: unapproved code node → listed as provenance, runnable True."""
        node = _make_node(
            node_id="sc1",
            display_name="Script Node",
            source_code="def run(state): ...",
            approved=False,
        )
        branch = _make_branch_dict(node_defs=[node])
        result = _call_get(branch)

        assert result["runnable"] is True
        assert result["source_code_problems"] == []
        assert len(result["unapproved_source_code_nodes"]) == 1
        assert result["unapproved_source_code_nodes"][0]["node_id"] == "sc1"

    def test_code_node_the_compiler_refuses_is_not_runnable(self):
        """get_branch: the read the served agent plans from names the real problem."""
        node = _make_node(
            node_id="sc1",
            display_name="Script Node",
            source_code="def run(state) return state",
            approved=False,
        )
        result = _call_get(_make_branch_dict(node_defs=[node]))

        assert result["runnable"] is False
        [problem] = result["source_code_problems"]
        assert problem["node_id"] == "sc1"
        assert "does not parse" in problem["problems"][0]


# ---------------------------------------------------------------------------
# validate_branch tests (BUG-031: was missing runnable + unapproved fields)
# ---------------------------------------------------------------------------

class TestValidateBranchApproval:
    def test_no_source_code_nodes_runnable(self):
        """validate_branch: no source_code nodes → runnable True, list empty."""
        node = _make_node(source_code="", approved=False)
        branch = _make_branch_dict(node_defs=[node])
        result = _call_validate(branch)

        assert result["valid"] is True
        assert result["runnable"] is True
        assert result["unapproved_source_code_nodes"] == []

    def test_unapproved_source_code_node_is_still_runnable(self):
        """validate_branch: unapproved code node → provenance listed, runnable True."""
        node = _make_node(
            node_id="sc1",
            display_name="Custom Script",
            source_code="def run(state): return state",
            approved=False,
        )
        branch = _make_branch_dict(node_defs=[node])
        result = _call_validate(branch)

        assert result["valid"] is True
        assert result["runnable"] is True
        assert result["source_code_problems"] == []
        assert len(result["unapproved_source_code_nodes"]) == 1
        assert result["unapproved_source_code_nodes"][0]["node_id"] == "sc1"
        assert result["unapproved_source_code_nodes"][0]["display_name"] == "Custom Script"

    def test_code_node_the_compiler_refuses_is_not_runnable(self):
        node = _make_node(
            node_id="sc1",
            display_name="Custom Script",
            source_code="def run(state) return state",
        )
        result = _call_validate(_make_branch_dict(node_defs=[node]))

        assert result["valid"] is True
        assert result["runnable"] is False
        assert result["source_code_problems"][0]["node_id"] == "sc1"

    def test_approved_source_code_node_runnable(self):
        """validate_branch: approved source_code node → runnable True."""
        node = _make_node(source_code="def run(state): ...", approved=True)
        branch = _make_branch_dict(node_defs=[node])
        result = _call_validate(branch)

        assert result["valid"] is True
        assert result["runnable"] is True
        assert result["unapproved_source_code_nodes"] == []

    def test_structural_errors_make_not_runnable(self):
        """validate_branch: structural validation errors → valid False, runnable False."""
        node = _make_node(source_code="", approved=False)
        branch = _make_branch_dict(node_defs=[node])
        result = _call_validate(branch, validate_errors=["entry_point is required"])

        assert result["valid"] is False
        assert result["runnable"] is False

    def test_unapproved_and_structural_errors(self):
        """validate_branch: both structural errors and unapproved node → both flagged."""
        node = _make_node(
            node_id="sc1", source_code="code", approved=False,
        )
        branch = _make_branch_dict(node_defs=[node])
        result = _call_validate(branch, validate_errors=["entry_point is required"])

        assert result["valid"] is False
        assert result["runnable"] is False
        assert len(result["unapproved_source_code_nodes"]) == 1

    def test_multiple_unapproved_nodes_all_listed(self):
        """validate_branch: multiple unapproved nodes all appear in the list."""
        nodes = [
            _make_node(node_id="n1", source_code="code", approved=False),
            _make_node(node_id="n2", source_code="code", approved=True),
            _make_node(node_id="n3", source_code="code", approved=False),
        ]
        branch = _make_branch_dict(node_defs=nodes)
        result = _call_validate(branch)

        ids = {n["node_id"] for n in result["unapproved_source_code_nodes"]}
        assert ids == {"n1", "n3"}
        assert result["runnable"] is True

    def test_state_field_node_id_collision_not_valid_or_runnable(self):
        node = _make_node(node_id="status", display_name="Status")
        branch = _make_branch_dict(node_defs=[node])
        branch["graph_nodes"] = [{
            "id": "status",
            "node_def_id": "status",
            "position": 0,
        }]
        branch["edges"] = [
            {"from": "START", "to": "status"},
            {"from": "status", "to": "END"},
        ]
        branch["state_schema"] = [{"name": "status", "type": "str"}]

        result = _call_validate_real(branch)

        assert result["valid"] is False
        assert result["runnable"] is False
        assert any(
            "State field name 'status' collides with a graph node ID" in error
            for error in result["errors"]
        )
